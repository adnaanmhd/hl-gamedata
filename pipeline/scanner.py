"""Frame-diff candidate finder (plan §10.3).

Exists because the §5 2-second keep-vs-cut rule is finer than any VLM
sampling cadence: 1-frame-accurate window boundaries come from here, the VLM
classifies what the scanner finds, and the <40% stillness gate decides.

Decodes the FULL video once at native frame rate through an ffmpeg rawvideo
pipe (downscaled gray), which is far cheaper than per-frame seeks. Produces:
  - a per-frame-interval motion value (mean abs luma diff),
  - per-frame mean luma (black-frame detection),
  - static windows relative to a live-gameplay baseline,
  - a helper to measure any window's motion ratio.

Sibling insights honored: probes stay inside a window's span; translucent
scoreboards over live play are NOT frozen (their ratio stays high); the
baseline is this session's own live gameplay, not an absolute constant.
"""
from __future__ import annotations

import subprocess
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path

_W, _H = 160, 90
_FRAME_BYTES = _W * _H


def available() -> bool:
    try:
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class MotionTimeline:
    n_frames: int
    fps: float
    duration_s: float
    times_s: list[float]        # per-frame time (real PTS when readable)
    diffs: list[float]          # len n-1: |frame[i+1] - frame[i]| mean
    luma: list[float]           # len n: mean luma per frame

    def frame_at(self, t_s: float) -> int:
        i = bisect_right(self.times_s, t_s) - 1
        return max(0, min(i, self.n_frames - 1))

    def window_motion(self, t0: float, t1: float) -> float | None:
        """Mean diff strictly inside [t0, t1] (never reaches past the span)."""
        a = self.frame_at(t0)
        b = self.frame_at(t1)
        if b <= a:
            return None
        seg = self.diffs[a:b]
        return sum(seg) / len(seg) if seg else None

    def baseline(self, gameplay_ts: list[float] | None = None) -> float:
        """Live-gameplay motion baseline. Prefers VLM-confirmed gameplay
        sample times; falls back to the 75th percentile of all diffs (a
        session dominated by stillness must not drag its own bar down)."""
        vals: list[float] = []
        for t in (gameplay_ts or [])[:12]:
            m = self.window_motion(t - 0.3, t + 0.3)
            if m is not None:
                vals.append(m)
        if vals:
            return sum(vals) / len(vals)
        if not self.diffs:
            return 0.0
        s = sorted(self.diffs)
        return s[int(len(s) * 0.75)]


def scan_video(video: Path, *, pts_us: list[int] | None = None,
               timeout_s: int = 3600) -> MotionTimeline:
    """One full-video decode -> motion timeline."""
    import numpy as np
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-vf", f"scale={_W}:{_H}", "-pix_fmt", "gray",
         "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    diffs: list[float] = []
    luma: list[float] = []
    prev = None
    try:
        assert p.stdout is not None
        while True:
            buf = p.stdout.read(_FRAME_BYTES)
            if len(buf) < _FRAME_BYTES:
                break
            fr = np.frombuffer(buf, dtype=np.uint8).astype(np.int16)
            luma.append(float(fr.mean()))
            if prev is not None:
                diffs.append(float(np.abs(fr - prev).mean()))
            prev = fr
    finally:
        p.stdout.close()
        p.wait(timeout=timeout_s)
    n = len(luma)
    if pts_us and len(pts_us) == n:
        times = [t / 1e6 for t in pts_us]
        duration = times[-1] + (times[-1] - times[-2] if n > 1 else 0.0)
    else:
        # fall back to the container's average rate
        duration = _probe_duration(video) or (n / 30.0)
        times = [i * duration / n for i in range(n)] if n else []
    fps = n / duration if duration else 0.0
    return MotionTimeline(n_frames=n, fps=fps, duration_s=duration,
                          times_s=times, diffs=diffs, luma=luma)


def _probe_duration(video: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=120)
        return float(out.stdout.strip()) if out.returncode == 0 else None
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def static_windows(tl: MotionTimeline, *, ratio: float, baseline: float,
                   min_s: float) -> list[tuple[float, float]]:
    """Contiguous spans whose per-interval motion sits under
    ratio*baseline, at least min_s long. 1-frame-accurate boundaries."""
    if baseline <= 0 or not tl.diffs:
        return []
    thr = ratio * baseline
    out: list[tuple[float, float]] = []
    i, n = 0, len(tl.diffs)
    while i < n:
        if tl.diffs[i] < thr:
            j = i
            while j < n and tl.diffs[j] < thr:
                j += 1
            t0, t1 = tl.times_s[i], tl.times_s[min(j, tl.n_frames - 1)]
            if t1 - t0 >= min_s:
                out.append((round(t0, 3), round(t1, 3)))
            i = j
        else:
            i += 1
    return out


def refine_window(tl: MotionTimeline, t0: float, t1: float, *,
                  ratio: float, baseline: float,
                  pad_s: float = 2.0) -> tuple[float, float] | None:
    """Tighten a VLM-found window to the actual static span near it.

    Searches [t0-pad, t1+pad]; returns the longest static run that overlaps
    the original window, or None if nothing under the threshold exists
    (VLM window over moving frames — e.g. translucent scoreboard)."""
    if baseline <= 0:
        return None
    a = tl.frame_at(max(t0 - pad_s, 0.0))
    b = tl.frame_at(min(t1 + pad_s, tl.duration_s))
    thr = ratio * baseline
    best: tuple[float, float] | None = None
    i = a
    while i < min(b, len(tl.diffs)):
        if tl.diffs[i] < thr:
            j = i
            while j < min(b, len(tl.diffs)) and tl.diffs[j] < thr:
                j += 1
            w0, w1 = tl.times_s[i], tl.times_s[min(j, tl.n_frames - 1)]
            if w1 > t0 and w0 < t1:      # overlaps the VLM window
                if best is None or (w1 - w0) > (best[1] - best[0]):
                    best = (round(w0, 3), round(w1, 3))
            i = j
        else:
            i += 1
    return best


def zero_input_runs(timestamps_ms: list[int], active_flags: list[bool],
                    min_s: float) -> list[tuple[float, float]]:
    """Spans (seconds) of >= min_s with no input activity at all —
    AFK candidates (the stillness + context checks come after)."""
    out: list[tuple[float, float]] = []
    i, n = 0, len(active_flags)
    while i < n:
        if not active_flags[i]:
            j = i
            while j < n and not active_flags[j]:
                j += 1
            t0 = timestamps_ms[i] / 1000.0
            t1 = timestamps_ms[min(j, n - 1)] / 1000.0
            if t1 - t0 >= min_s:
                out.append((round(t0, 3), round(t1, 3)))
            i = j
        else:
            i += 1
    return out
