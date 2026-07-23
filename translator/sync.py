"""Controls-to-video sync measurement (client's action_video_grounding).

Vendored re-implementation of Odyssey's `action_video_grounding` check
(`latest_requirements/action_video_grounding.py`), kept algorithm-identical so
our numbers line up with their QA report:

  1. Video motion track: Farneback dense optical flow per frame, downscaled to
     208 px wide, median flow over the central crop (excludes HUD + viewmodel).
  2. Input track: per-frame input_mouse_dx/dy (signs flipped only when the
     declared input_mouse_convention is non-standard).
  3. Both tracks box-smoothed (8) + z-normalized; scan lags in ±120 frames;
     best lag maximizes |corr_x|+|corr_y|; correlation = stronger axis, signed.
  4. Gates, in order: active_fraction ≥ 0.02 (else SKIP) → |corr| ≥ 0.15
     (else WARN: unverifiable) → |lag_ms| ≤ 150 (else FAIL) → PASS.

Sign note (their --explain-sign): a CORRECT first-person capture yields a
NEGATIVE correlation — the scene moves opposite the camera. |corr| is the
health signal. Negative lag = video runs behind the recorded inputs.

Needs numpy + opencv (opencv-python-headless); `available()` reports whether
the check can run in this interpreter.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_ANALYSIS_FRAMES = 3600
DOWNSCALE_WIDTH = 208
MAX_LAG_FRAMES = 120
SMOOTHING_WINDOW_FRAMES = 8
MIN_ACTIVE_FRACTION = 0.02
MIN_ABS_CORRELATION = 0.15
MAX_ABS_LAG_MS = 150.0     # hard bound (FAIL above this)
TARGET_ABS_LAG_MS = 50.0   # client's stated target; we correct down to this


def available() -> bool:
    try:
        import cv2   # noqa: F401
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass(frozen=True)
class LagEstimate:
    lag_frames: int
    correlation: float
    analyzed_frames: int
    active_fraction: float

    def lag_ms(self, fps: float) -> float:
        return self.lag_frames / fps * 1000.0 if fps else 0.0


def convention_signs(session_meta: dict | None) -> tuple[float, float]:
    """Mouse-axis flips per declared input_mouse_convention (client parity:
    only maps_to == camera_look_velocity is ever flipped)."""
    conv = (session_meta or {}).get("input_mouse_convention") or {}
    if not isinstance(conv, dict) or conv.get("maps_to") != "camera_look_velocity":
        return 1.0, 1.0
    return (-1.0 if conv.get("dx_positive") == "left" else 1.0,
            -1.0 if conv.get("dy_positive") == "up" else 1.0)


def motion_track(video_path: Path, *, max_frames: int = MAX_ANALYSIS_FRAMES,
                 downscale_width: int = DOWNSCALE_WIDTH):
    """Per-frame global scene motion (dx, dy) via center-crop optical flow."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"could not open video: {video_path}")
    try:
        dxs, dys = [0.0], [0.0]
        prev = None
        while len(dxs) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            scale = downscale_width / gray.shape[1]
            cur = cv2.resize(gray, (downscale_width,
                                    max(int(gray.shape[0] * scale), 8)))
            if prev is not None:
                flow = cv2.calcOpticalFlowFarneback(
                    prev, cur, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                h, w = flow.shape[:2]
                center = flow[h // 6: h * 5 // 6, w // 7: w * 6 // 7]
                dxs.append(float(np.median(center[..., 0])))
                dys.append(float(np.median(center[..., 1])))
            prev = cur
    finally:
        cap.release()
    return np.asarray(dxs), np.asarray(dys)


def _normalized(track):
    centered = track - track.mean()
    scale = centered.std()
    return centered / scale if scale > 0 else centered


def _smoothed(track, window: int):
    import numpy as np
    if window <= 1 or len(track) < window:
        return track
    return np.convolve(track, np.ones(window) / window, mode="same")


def _lagged_correlation(action, motion, lag: int) -> float:
    import numpy as np
    if lag > 0:
        pairs = (action[lag:], motion[: len(motion) - lag])
    elif lag < 0:
        pairs = (action[:lag], motion[-lag:])
    else:
        pairs = (action, motion)
    return float(np.mean(pairs[0] * pairs[1]))


def estimate_lag(motion_dx, motion_dy, action_dx, action_dy, *,
                 max_lag_frames: int = MAX_LAG_FRAMES,
                 smoothing_window_frames: int = SMOOTHING_WINDOW_FRAMES) -> LagEstimate:
    import numpy as np
    n = min(len(motion_dx), len(action_dx))
    raw_x = np.asarray(action_dx[:n], dtype=np.float64)
    raw_y = np.asarray(action_dy[:n], dtype=np.float64)
    active = float(np.mean((raw_x != 0) | (raw_y != 0))) if n else 0.0
    mx = _normalized(_smoothed(np.asarray(motion_dx[:n], dtype=np.float64),
                               smoothing_window_frames))
    my = _normalized(_smoothed(np.asarray(motion_dy[:n], dtype=np.float64),
                               smoothing_window_frames))
    ax = _normalized(_smoothed(raw_x, smoothing_window_frames))
    ay = _normalized(_smoothed(raw_y, smoothing_window_frames))

    max_lag = min(max_lag_frames, max(n - 2, 0))
    best_lag, best_combined, best_corr = 0, -float("inf"), 0.0
    for lag in range(-max_lag, max_lag + 1):
        cx = _lagged_correlation(ax, mx, lag)
        cy = _lagged_correlation(ay, my, lag)
        combined = abs(cx) + abs(cy)
        if combined > best_combined:
            best_combined, best_lag = combined, lag
            best_corr = cx if abs(cx) >= abs(cy) else cy
    return LagEstimate(lag_frames=best_lag, correlation=best_corr,
                       analyzed_frames=n, active_fraction=active)


def input_track_from_rows(dx_cells: list, dy_cells: list, session_meta: dict | None):
    """Per-frame action track from frames.csv-style dx/dy cells ('' -> 0)."""
    import numpy as np
    sx, sy = convention_signs(session_meta)
    dx = np.asarray([float(v) if v not in ("", None) else 0.0 for v in dx_cells],
                    dtype=np.float64) * sx
    dy = np.asarray([float(v) if v not in ("", None) else 0.0 for v in dy_cells],
                    dtype=np.float64) * sy
    return dx, dy


def verdict(est: LagEstimate, fps: float) -> tuple[str, str]:
    """(status, message) with the client's three gates, in order."""
    lag_ms = est.lag_ms(fps)
    if est.active_fraction < MIN_ACTIVE_FRACTION:
        return "SKIP", (f"mouse signal inactive "
                        f"(active_fraction={est.active_fraction:.4f} < "
                        f"{MIN_ACTIVE_FRACTION})")
    if abs(est.correlation) < MIN_ABS_CORRELATION:
        return "WARN", (f"correlation too weak to verify alignment "
                        f"(|corr|={abs(est.correlation):.3f} < "
                        f"{MIN_ABS_CORRELATION})")
    direction = "behind" if est.lag_frames < 0 else "ahead of"
    if abs(lag_ms) > MAX_ABS_LAG_MS:
        return "FAIL", (f"video {abs(lag_ms):.1f}ms {direction} inputs "
                        f"(|lag| > {MAX_ABS_LAG_MS:.0f}ms); "
                        f"correlation {est.correlation:+.2f}")
    return "PASS", (f"video {abs(lag_ms):.1f}ms {direction} inputs "
                    f"(within {MAX_ABS_LAG_MS:.0f}ms; target "
                    f"{TARGET_ABS_LAG_MS:.0f}ms); correlation "
                    f"{est.correlation:+.2f}")
