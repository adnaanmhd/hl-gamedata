"""
Single input<->video timebase anchor — fix for issue A2 (§3 of
HumynCapture_V2_Fix_Handoff.md).

The bug
--------
Before this fix, input events were timestamped as "µs since
`time.perf_counter()` at the moment ffmpeg was confirmed running"
(`SessionEngine.run`'s old anchor), but the video's t=0 is the *first
encoded frame's presentation time* — a different instant. The startup gap
between "ffmpeg process running" and "first frame on disk" became a fixed
per-session offset between the input stream and the video (measured by the
client: 151.5ms / 207.7ms / 2326.9ms lag on the flagged 07-17 delivery).

The fix — v2, after the v1 approach was confirmed broken on real hardware
--------------------------------------------------------------------------
v1 (kept below as a fallback) anchored to the first video frame's PTS by
reading it back out of the muxed file via ffprobe, assuming
`-use_wallclock_as_timestamps 1` makes that PTS a real wallclock (epoch)
value. **Confirmed broken on a real delivery**: the muxed file's frame0 PTS
came back as a tiny relative number (~0.067s), not epoch-scale — ffmpeg's
`-avoid_negative_ts` output normalization resets it back near zero
regardless of the input timestamping flag. The v1 code's own implausibility
guard (`MAX_PLAUSIBLE_CORRECTION_S`) is what caught this rather than
silently applying a multi-year-wrong correction — but it meant A2's actual
sync bug was never fixed in practice, only safely refused.

**The real fix** is the handoff doc's other suggested option: parse
ffmpeg's own live `-stats` progress output. `FFmpegRecorder`'s
`_StderrMonitor` (ffmpeg_recorder.py) already tails stderr for `frame=`/
`drop=`; it now also captures, on the FIRST progress line ffmpeg prints,
the pairing `(time.perf_counter() at the instant we read that line,
the `time=` value ffmpeg reported)`. Since `time=T` at real monotonic
instant `M` means "time/frame zero occurred at `M - T`" — algebraically,
for ANY progress line — this never depends on what the container's PTS
becomes after muxing. No wallclock assumption, nothing to normalize away,
nothing to read back from a file at all.

  1. `FFmpegRecorder` tails its own stderr (already required for A1's
     frame-drop counter) and records `(read_monotonic_s, encoded_s)` for
     the first `time=` line (`RecorderHealth.first_progress_monotonic_s` /
     `first_progress_encoded_s`).
  2. `frame0_monotonic = read_monotonic_s - encoded_s` — the real
     monotonic instant the video's own t=0 occurred, in the same units
     `InputCapture`/`RawMouseCapture`/`FocusTracker` already use.
  3. Every input event was recorded relative to the OLD (buggy) anchor; we
     compute one constant `correction_us` and shift every event by it
     during finalize's re-anchor step (`app/core/finalize/pipeline.py`),
     rather than re-deriving anything per-event.
  4. The v1 ffprobe/wallclock approach is kept ONLY as a fallback for the
     rare case `_StderrMonitor` never captured a progress line (e.g. a
     near-zero-length recording); its implausibility guard stays in place
     since the same failure mode is possible there.

The chosen anchor and the raw pairing are recorded verbatim in
`capture_health` (session metadata) so a residual issue is diagnosable
downstream without re-running this logic — per the handoff doc's explicit
requirement ("record the chosen anchor explicitly in metadata").
"""
from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.paths import ffmpeg_exe

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClockPairing:
    """A single (wallclock, monotonic) sample taken back-to-back."""
    wallclock_s: float
    monotonic_s: float


def capture_launch_pairing() -> ClockPairing:
    """Call this at the exact moment ffmpeg is launched (see
    FFmpegRecorder.start). The two calls are back-to-back in the same
    process/thread, so their skew is sub-millisecond."""
    import time
    return ClockPairing(wallclock_s=time.time(), monotonic_s=time.perf_counter())


def first_frame_pts_wallclock_s(video_path: Path) -> float | None:
    """Read the first video frame's PTS (wallclock seconds, because the
    recorder ran with -use_wallclock_as_timestamps 1) via ffprobe. Returns
    None if unreadable (caller must treat that as 'cannot verify anchor',
    not silently anchor at 0 — see finalize/pipeline.py)."""
    cmd = [
        str(ffmpeg_exe()).replace("ffmpeg.exe", "ffprobe.exe"),
        "-v", "error", "-select_streams", "v:0",
        "-show_entries", "frame=pts_time", "-read_intervals", "%+#1",
        "-of", "csv=p=0", str(video_path),
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                              creationflags=creationflags, check=True)
        first_line = out.stdout.strip().splitlines()[0]
        # Real bug found on Windows: `-of csv=p=0` with a single selected
        # field ("frame=pts_time") still emits a trailing comma on this
        # ffprobe build ("0.066667,", not "0.066667") — float() choked on
        # it outright, and the failure was being swallowed silently before
        # the logging above was added. Take the first comma-separated field
        # rather than assuming the line is a bare number.
        return float(first_line.split(",")[0].strip())
    except (subprocess.SubprocessError, OSError, ValueError, IndexError) as e:
        # Real gap fixed here: this used to swallow the exact failure
        # reason silently, so every "time_anchor: unavailable" in a real
        # delivery gave no way to tell WHY ffprobe couldn't read the first
        # frame's PTS without re-running the command by hand on the
        # original machine. Log the command, exit code, and stderr/stdout
        # so this is diagnosable from humyncapture.log alone next time.
        stdout = getattr(e, "stdout", None)
        stderr = getattr(e, "stderr", None)
        log.warning(
            "first_frame_pts_wallclock_s failed: %s\ncmd: %s\nreturncode: %s\n"
            "stdout: %r\nstderr: %r",
            e, " ".join(cmd), getattr(e, "returncode", None), stdout, stderr)
        return None


@dataclass
class AnchorResult:
    method: str                       # "first_frame_pts_wallclock" | "unavailable" | ...
    correction_us: int
    launch_pairing: ClockPairing | None
    frame0_wallclock_s: float | None
    frame0_monotonic_s: float | None
    # Partial-fix #3: populated only for method="ffmpeg_progress_time_drift_fit".
    # `e = drift_slope * m + drift_intercept` fit across every progress
    # sample seen during the WHOLE recording (m=monotonic_s, e=encoded_s) —
    # see fit_progress_drift(). A drift_slope != 1.0 is direct evidence that
    # the video's internal content-time clock and real wallclock time
    # diverge over the session (see ffmpeg_recorder.py's _StderrMonitor
    # docstring for why -fps_mode cfr can cause exactly this), which a
    # single fixed correction_us cannot track — apply_drift_correction below
    # applies the full affine map instead of a constant shift.
    drift_slope: float | None = None
    drift_intercept: float | None = None
    drift_sample_count: int = 0

    def to_capture_health_dict(self) -> dict:
        """Exact shape written into metadata.json's capture_health block —
        kept flat and explicit per the handoff doc's diagnosability ask."""
        return {
            "time_anchor": self.method,
            "correction_applied_us": self.correction_us,
            "launch_wallclock_s": self.launch_pairing.wallclock_s if self.launch_pairing else None,
            "launch_monotonic_s": self.launch_pairing.monotonic_s if self.launch_pairing else None,
            "frame0_wallclock_s": self.frame0_wallclock_s,
            "frame0_monotonic_s": self.frame0_monotonic_s,
            "drift_slope": self.drift_slope,
            "drift_sample_count": self.drift_sample_count,
        }


# Real fps mismatches this is meant to catch are small (e.g. a monitor
# actually running 29.97Hz-locked capture against a nominal 30fps encode
# target) — a slope this far from 1.0 means the samples are noise/garbage,
# not a real drift signal, so fall back to the single-sample method instead
# of trusting it.
MIN_PLAUSIBLE_DRIFT_SLOPE = 0.5
MAX_PLAUSIBLE_DRIFT_SLOPE = 1.5
MIN_DRIFT_FIT_SAMPLES = 8


def fit_progress_drift(samples: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Ordinary least-squares fit of `encoded_s = slope * monotonic_s +
    intercept` across every ffmpeg progress sample seen in one recording.

    Returns None if there aren't enough samples to fit meaningfully, or the
    monotonic values have no spread (can't estimate a slope from a single
    point in time — this is exactly the single-sample case the old method
    already handles).
    """
    n = len(samples)
    if n < MIN_DRIFT_FIT_SAMPLES:
        return None
    mean_m = sum(m for m, _ in samples) / n
    mean_e = sum(e for _, e in samples) / n
    num = sum((m - mean_m) * (e - mean_e) for m, e in samples)
    den = sum((m - mean_m) ** 2 for m, _ in samples)
    if den == 0:
        return None
    slope = num / den
    intercept = mean_e - slope * mean_m
    return slope, intercept


MAX_PLAUSIBLE_CORRECTION_S = 10.0
# The handoff doc's own evidence says the real startup gap is sub-second to
# a few frames at most (§3, "1-3 frames" on correctly-anchored sessions) —
# anything beyond a few seconds is a computation failure, not a real anchor
# correction. Shared by both strategies below.


def compute_anchor_correction(
    *, launch_pairing: ClockPairing, old_anchor_monotonic_s: float,
    video_path: Path,
    first_progress_monotonic_s: float | None = None,
    first_progress_encoded_s: float | None = None,
    progress_samples: list[tuple[float, float]] | None = None,
) -> AnchorResult:
    """Returns the correction (in µs) to ADD to every recorded event's `t`
    so that t=0 lands on the first video frame's real presentation instant
    instead of the old process-launch-based anchor.

    correction_us > 0 means the old anchor was too early (events need to
    move later, i.e. the video actually started after the old t=0) — this
    matches the client's observed direction ("video behind inputs").

    Prefers the real fix (ffmpeg's own live progress output, captured by
    `_StderrMonitor` — see module docstring "v2") when available; falls
    back to the v1 ffprobe/wallclock-PTS approach only if a progress line
    was never captured (e.g. a near-instant recording).

    Partial-fix #3: when enough progress samples were captured across the
    WHOLE recording (not just the first line), prefers a drift-aware affine
    fit over the single-sample constant offset — see `fit_progress_drift`.
    A single sample can only ever measure the startup gap; it cannot detect
    or correct drift that accumulates for the rest of the session (e.g. from
    `-fps_mode cfr` duplicating/dropping frames to hold a constant output
    rate the real capture can't quite sustain). Falls back to the
    single-sample method if the fit isn't available or isn't plausible.
    """
    if progress_samples:
        fit = fit_progress_drift(progress_samples)
        if fit is not None:
            slope, intercept = fit
            if MIN_PLAUSIBLE_DRIFT_SLOPE <= slope <= MAX_PLAUSIBLE_DRIFT_SLOPE:
                # correction_us is still reported for backward-compat/logging
                # (the startup-instant correction the old method would have
                # produced), but callers should use apply_drift_correction
                # with drift_slope/drift_intercept for the real per-event fix.
                frame0_monotonic = -intercept / slope if slope else None
                correction_us = (
                    round((frame0_monotonic - old_anchor_monotonic_s) * 1_000_000)
                    if frame0_monotonic is not None else 0)
                log.info(
                    "anchor drift-fit: slope=%.8f intercept=%.6f n=%d "
                    "equivalent_correction_us=%d (over full session, "
                    "drift-only contribution=%.1fms per elapsed second)",
                    slope, intercept, len(progress_samples), correction_us,
                    (slope - 1.0) * 1000.0)
                return AnchorResult(
                    method="ffmpeg_progress_time_drift_fit",
                    correction_us=correction_us, launch_pairing=launch_pairing,
                    frame0_wallclock_s=None, frame0_monotonic_s=frame0_monotonic,
                    drift_slope=slope, drift_intercept=intercept,
                    drift_sample_count=len(progress_samples))
            log.warning(
                "progress-drift fit implausible (slope=%.6f from %d samples) "
                "— falling back to the single-sample anchor method",
                slope, len(progress_samples))

    if first_progress_monotonic_s is not None and first_progress_encoded_s is not None:
        frame0_monotonic = first_progress_monotonic_s - first_progress_encoded_s
        correction_us = round((frame0_monotonic - old_anchor_monotonic_s) * 1_000_000)
        if abs(correction_us) > MAX_PLAUSIBLE_CORRECTION_S * 1_000_000:
            log.warning(
                "ffmpeg-progress anchor implausible (correction=%.3fs, "
                "first_progress_monotonic=%s, first_progress_encoded=%s) — "
                "falling back to the ffprobe/wallclock-PTS method",
                correction_us / 1_000_000, first_progress_monotonic_s,
                first_progress_encoded_s)
        else:
            return AnchorResult(
                method="ffmpeg_progress_time", correction_us=correction_us,
                launch_pairing=launch_pairing, frame0_wallclock_s=None,
                frame0_monotonic_s=frame0_monotonic)

    frame0_wall = first_frame_pts_wallclock_s(video_path)
    if frame0_wall is None:
        return AnchorResult(
            method="unavailable", correction_us=0, launch_pairing=launch_pairing,
            frame0_wallclock_s=None, frame0_monotonic_s=None)

    frame0_monotonic = launch_pairing.monotonic_s + (frame0_wall - launch_pairing.wallclock_s)
    correction_us = round((frame0_monotonic - old_anchor_monotonic_s) * 1_000_000)

    # Safety net for an assumption this whole approach depends on:
    # -use_wallclock_as_timestamps should make the muxed file's frame PTS a
    # real wallclock (epoch-scale) value, so frame0_wall - launch_pairing.
    # wallclock_s is a SMALL number (the true startup gap). If ffmpeg's
    # output muxing instead normalizes PTS to start near 0 (common default
    # behavior — avoid_negative_ts), frame0_wall comes back tiny/relative
    # instead of epoch-scale, and the subtraction above produces a
    # correction of several YEARS, not milliseconds. Applying that would
    # silently shift every event timestamp by that same absurd amount
    # instead of failing loudly (see MAX_PLAUSIBLE_CORRECTION_S above).
    if abs(correction_us) > MAX_PLAUSIBLE_CORRECTION_S * 1_000_000:
        log.warning(
            "first-frame PTS anchor implausible (correction=%.3fs, frame0_wall=%s, "
            "launch_wallclock=%s) — the video's PTS likely does not survive as "
            "wallclock time after muxing (e.g. avoid_negative_ts normalization); "
            "treating as unavailable rather than applying a bogus correction",
            correction_us / 1_000_000, frame0_wall, launch_pairing.wallclock_s)
        return AnchorResult(
            method="unavailable", correction_us=0, launch_pairing=launch_pairing,
            frame0_wallclock_s=frame0_wall, frame0_monotonic_s=None)

    return AnchorResult(
        method="first_frame_pts_wallclock", correction_us=correction_us,
        launch_pairing=launch_pairing, frame0_wallclock_s=frame0_wall,
        frame0_monotonic_s=frame0_monotonic)


def apply_correction(events: list[dict], correction_us: int) -> list[dict]:
    """Shift every event's `t` by `-correction_us` so t=0 becomes frame 0.

    Events are `t = perf_counter() - old_anchor` (µs). The new anchor is
    `old_anchor + correction_us` (in seconds: `correction_us / 1e6`), so
    `t_new = t_old - correction_us`.
    """
    if not correction_us:
        return events
    out = []
    for e in events:
        t = e.get("t")
        if isinstance(t, int):
            e = dict(e)
            e["t"] = t - correction_us
        out.append(e)
    return out


def apply_drift_correction(
    events: list[dict], *, old_anchor_monotonic_s: float,
    drift_slope: float, drift_intercept: float,
) -> list[dict]:
    """Partial-fix #3's real per-event correction: unlike `apply_correction`
    (a constant shift, i.e. implicitly assuming slope=1.0 — video content
    time advances at exactly the same rate as real wallclock time), this
    applies the full affine map fit by `fit_progress_drift` so a session
    where those two clocks measurably diverge gets a correction that grows
    over the recording instead of a single fixed number that's only exactly
    right at one instant (typically start-of-recording) and increasingly
    wrong everywhere else.

    Each event's `t` (µs since the OLD anchor) is converted back to a real
    monotonic instant, mapped through the fit (`video_s = slope*monotonic_s
    + intercept`), then re-expressed as µs since frame 0 (video_s=0).
    """
    out = []
    for e in events:
        t = e.get("t")
        if isinstance(t, int):
            monotonic_s = old_anchor_monotonic_s + t / 1_000_000
            video_s = drift_slope * monotonic_s + drift_intercept
            e = dict(e)
            e["t"] = round(video_s * 1_000_000)
        out.append(e)
    return out
