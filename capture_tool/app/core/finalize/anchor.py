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

The fix
--------
We anchor to the first video frame's PTS, using a wallclock<->monotonic
pairing rather than trying to parse ffmpeg's progress output live (the
handoff doc's option 1) or re-run ffprobe mid-recording (not possible):

  1. `FFmpegRecorder` is told to run with `-use_wallclock_as_timestamps 1` on
     its video input (see ffmpeg_recorder.py), so every encoded frame's PTS
     is wallclock-derived (seconds since the Unix epoch), not
     capture-pipeline-relative.
  2. At the exact instant we launch ffmpeg, we record a
     `(wallclock, monotonic)` pair via `time.time()` / `time.perf_counter()`
     back-to-back (`capture_launch_pairing()`) — close enough in time that
     the two clocks can be treated as offset by a fixed constant for the
     duration of one recording session (session lengths are minutes, not
     hours, so wallclock drift/adjustment risk is negligible).
  3. After the recording stops, we read the first frame's real PTS
     (wallclock seconds, thanks to step 1) via ffprobe and convert it to our
     monotonic timeline using the step-2 pairing. That gives
     `frame0_monotonic` — the *correct* input-clock anchor, in the same
     units `InputCapture`/`RawMouseCapture`/`FocusTracker` already use.
  4. Every input event was recorded relative to the OLD (buggy) anchor; we
     compute one constant `correction_us` and shift every event by it during
     finalize's re-anchor step (`app/core/finalize/pipeline.py`), rather than
     re-deriving anything per-event.

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
    method: str                       # "first_frame_pts_wallclock" | "unavailable"
    correction_us: int
    launch_pairing: ClockPairing | None
    frame0_wallclock_s: float | None
    frame0_monotonic_s: float | None

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
        }


def compute_anchor_correction(
    *, launch_pairing: ClockPairing, old_anchor_monotonic_s: float,
    video_path: Path,
) -> AnchorResult:
    """Returns the correction (in µs) to ADD to every recorded event's `t`
    so that t=0 lands on the first video frame's real presentation instant
    instead of the old process-launch-based anchor.

    correction_us > 0 means the old anchor was too early (events need to
    move later, i.e. the video actually started after the old t=0) — this
    matches the client's observed direction ("video behind inputs").
    """
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
    # instead of failing loudly. The handoff doc's own evidence says the
    # real startup gap is sub-second to a few frames at most (§3, "1-3
    # frames" on correctly-anchored sessions) — anything beyond a few
    # seconds is certainly this failure mode, not a real anchor correction.
    MAX_PLAUSIBLE_CORRECTION_S = 10.0
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
