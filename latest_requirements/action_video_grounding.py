#!/usr/bin/env python3
"""Protege's local implementation of the controls-to-video sync check.

Runs the controls-to-video temporal alignment check locally against a delivered
session folder *before* upload, so control-to-video sync issues can be flagged
and fixed without waiting for the downstream QA pipeline.

It uses the same algorithm and thresholds as that pipeline, so the numbers it
prints should line up with the QA report:

  1. Look-input track:  input_mouse_dx/dy from frames.csv (falls back to
     camera-pose yaw/pitch deltas derived from the C2W rotation columns when
     mouse input is inactive; skips if neither is active).
  2. Video motion track: Farneback dense optical flow on each frame, downscaled
     to 208px wide, median flow over the central crop (excludes HUD + viewmodel).
  3. Cross-correlation: both tracks box-smoothed (8) and z-normalized; scan
     lags in +/-120 frames; pick the lag maximizing |corr_x|+|corr_y|; report the
     stronger axis signed.
  4. Three gates, in order:
       - active_fraction  < min_active_fraction (0.02) -> SKIP
       - |correlation|    < min_abs_correlation (0.15) -> WARN
       - |lag_ms|         > max_abs_lag_ms      (150)  -> FAIL
       - otherwise                                     -> PASS

Sign convention: the mouse axis is flipped per the session's declared
`input_mouse_convention` (only for maps_to == "camera_look_velocity"). With a
standard declaration (dx_positive=right, dy_positive=down) no flip is applied, so
a *correct* first-person capture yields a *negative* correlation, because the
scene moves opposite the camera (look right -> world slides left). Treat
|correlation| (magnitude) as the health signal, not the sign; see --explain-sign.

Dependencies: numpy, opencv-python (or opencv-python-headless).
    pip install numpy opencv-python-headless

Usage:
    python qa/action_video_grounding.py <session_dir>
    python qa/action_video_grounding.py <parent_dir>      # scans for sessions
    python qa/action_video_grounding.py <dir> --fps 26.4  # pin frame rate
A session dir is any folder containing video.mp4 + frames.csv (session.json
optional, used for the mouse convention).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from dataclasses import dataclass

import numpy as np
import cv2

# Defaults (match the QA pipeline's published thresholds)
DEFAULT_MAX_ANALYSIS_FRAMES = 3600
DEFAULT_DOWNSCALE_WIDTH = 208
DEFAULT_MAX_LAG_FRAMES = 120
DEFAULT_SMOOTHING_WINDOW_FRAMES = 8
DEFAULT_MIN_ACTIVE_FRACTION = 0.02
DEFAULT_MIN_ABS_CORRELATION = 0.15
DEFAULT_MAX_ABS_LAG_MS = 150.0

_MOUSE_DX_COLUMN = "input_mouse_dx"
_MOUSE_DY_COLUMN = "input_mouse_dy"
_C2W_COLUMNS = tuple(f"c2w_m{row}{col}" for row in range(3) for col in range(3))


# Data structures
@dataclass(frozen=True)
class VideoGlobalMotion:
    dx: np.ndarray
    dy: np.ndarray
    frame_rate: float


@dataclass(frozen=True)
class LookDeltas:
    dx: np.ndarray
    dy: np.ndarray


@dataclass(frozen=True)
class LagEstimate:
    lag_frames: int
    correlation: float
    analyzed_frames: int
    active_fraction: float


@dataclass(frozen=True)
class MouseConventionSigns:
    dx: float
    dy: float


# Core algorithm
def _normalized(track: np.ndarray) -> np.ndarray:
    centered = track - track.mean()
    scale = centered.std()
    return centered / scale if scale > 0 else centered


def _smoothed(track: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(track) < window:
        return track
    return np.convolve(track, np.ones(window) / window, mode="same")


def _lagged_correlation(action_track: np.ndarray, motion_track: np.ndarray, lag: int) -> float:
    if lag > 0:
        pairs = (action_track[lag:], motion_track[: len(motion_track) - lag])
    elif lag < 0:
        pairs = (action_track[:lag], motion_track[-lag:])
    else:
        pairs = (action_track, motion_track)
    return float(np.mean(pairs[0] * pairs[1]))


def estimate_action_video_lag(
    motion_dx: np.ndarray,
    motion_dy: np.ndarray,
    mouse_dx: np.ndarray,
    mouse_dy: np.ndarray,
    *,
    max_lag_frames: int = DEFAULT_MAX_LAG_FRAMES,
    smoothing_window_frames: int = DEFAULT_SMOOTHING_WINDOW_FRAMES,
) -> LagEstimate:
    """Estimate lag; reported lag maximizes |corr_x|+|corr_y|, correlation is the stronger axis signed."""
    num_frames = min(len(motion_dx), len(mouse_dx))
    action_raw_x = np.asarray(mouse_dx[:num_frames], dtype=np.float64)
    action_raw_y = np.asarray(mouse_dy[:num_frames], dtype=np.float64)
    active_fraction = (
        float(np.mean((action_raw_x != 0) | (action_raw_y != 0))) if num_frames else 0.0
    )
    motion_x = _normalized(_smoothed(np.asarray(motion_dx[:num_frames], dtype=np.float64), smoothing_window_frames))
    motion_y = _normalized(_smoothed(np.asarray(motion_dy[:num_frames], dtype=np.float64), smoothing_window_frames))
    action_x = _normalized(_smoothed(action_raw_x, smoothing_window_frames))
    action_y = _normalized(_smoothed(action_raw_y, smoothing_window_frames))

    max_lag = min(max_lag_frames, max(num_frames - 2, 0))
    best_lag = 0
    best_combined = -np.inf
    best_correlation = 0.0
    for lag in range(-max_lag, max_lag + 1):
        corr_x = _lagged_correlation(action_x, motion_x, lag)
        corr_y = _lagged_correlation(action_y, motion_y, lag)
        combined = abs(corr_x) + abs(corr_y)
        if combined > best_combined:
            best_combined = combined
            best_lag = lag
            best_correlation = corr_x if abs(corr_x) >= abs(corr_y) else corr_y
    return LagEstimate(
        lag_frames=best_lag,
        correlation=best_correlation,
        analyzed_frames=num_frames,
        active_fraction=active_fraction,
    )


def compute_video_global_motion(
    video_path: str,
    *,
    max_frames: int = DEFAULT_MAX_ANALYSIS_FRAMES,
    downscale_width: int = DEFAULT_DOWNSCALE_WIDTH,
) -> VideoGlobalMotion:
    """Per-frame global motion + container frame rate via center-crop optical flow."""
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        raise ValueError(f"Could not open video at {video_path}")
    try:
        frame_rate = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        shifts_dx: list[float] = [0.0]
        shifts_dy: list[float] = [0.0]
        previous = None
        while len(shifts_dx) < max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            scale = downscale_width / gray.shape[1]
            current = cv2.resize(gray, (downscale_width, max(int(gray.shape[0] * scale), 8)))
            if previous is not None:
                flow = cv2.calcOpticalFlowFarneback(previous, current, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                height, width = flow.shape[:2]
                center = flow[height // 6 : height * 5 // 6, width // 7 : width * 6 // 7]
                shifts_dx.append(float(np.median(center[..., 0])))
                shifts_dy.append(float(np.median(center[..., 1])))
            previous = current
    finally:
        capture.release()
    return VideoGlobalMotion(dx=np.asarray(shifts_dx), dy=np.asarray(shifts_dy), frame_rate=frame_rate)


def camera_pose_look_deltas(columns: dict) -> "LookDeltas | None":
    """Per-frame camera yaw/pitch deltas (deg) from C2W rotation columns; None if absent."""
    if any(c not in columns for c in _C2W_COLUMNS):
        return None
    rotation = np.stack([columns[c].astype(np.float64) for c in _C2W_COLUMNS], axis=1).reshape(-1, 3, 3)
    finite_mask = np.isfinite(rotation).all(axis=(1, 2))
    if not finite_mask.any():
        return None
    fill_indices = np.maximum.accumulate(np.where(finite_mask, np.arange(len(rotation)), -1))
    fill_indices[fill_indices == -1] = np.flatnonzero(finite_mask)[0]
    rotation = rotation[fill_indices]
    forward = rotation @ np.array([0.0, 0.0, -1.0])
    yaw = np.unwrap(np.arctan2(forward[:, 0], -forward[:, 2]))
    pitch = np.arcsin(np.clip(forward[:, 1], -1.0, 1.0))
    pose_dx = np.zeros(len(yaw))
    pose_dy = np.zeros(len(pitch))
    pose_dx[1:] = np.degrees(np.diff(yaw))
    pose_dy[1:] = np.degrees(np.diff(pitch))
    return LookDeltas(dx=pose_dx, dy=pose_dy)


def _active_fraction(track_x: np.ndarray, track_y: np.ndarray, threshold: float = 1e-4) -> float:
    if not len(track_x):
        return 0.0
    return float(np.mean((np.abs(track_x) > threshold) | (np.abs(track_y) > threshold)))


def _mouse_convention_signs(session_metadata: "dict | None") -> MouseConventionSigns:
    """Flip mouse axes per declared input_mouse_convention (camera_look_velocity only)."""
    if not session_metadata:
        return MouseConventionSigns(dx=1.0, dy=1.0)
    convention = session_metadata.get("input_mouse_convention") or {}
    if not isinstance(convention, dict) or convention.get("maps_to") != "camera_look_velocity":
        return MouseConventionSigns(dx=1.0, dy=1.0)
    return MouseConventionSigns(
        dx=-1.0 if convention.get("dx_positive") == "left" else 1.0,
        dy=-1.0 if convention.get("dy_positive") == "up" else 1.0,
    )


# Local I/O (reads the delivered session files directly)
def read_frames_csv(path: str) -> dict:
    """Read the columns the check needs from frames.csv into float arrays (empty cells -> NaN)."""
    want = {_MOUSE_DX_COLUMN, _MOUSE_DY_COLUMN, *_C2W_COLUMNS}
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        idx = {h.strip().lower(): i for i, h in enumerate(header)}
        cols = {name: idx[name] for name in want if name in idx}
        acc = {name: [] for name in cols}
        for row in reader:
            for name, i in cols.items():
                v = row[i].strip() if i < len(row) else ""
                acc[name].append(float(v) if v not in ("", "NaN", "nan", "null", "None") else np.nan)
    return {name: np.asarray(vals, dtype=np.float64) for name, vals in acc.items()}


def find_sessions(root: str) -> list:
    root = os.path.abspath(root)
    if os.path.isfile(os.path.join(root, "video.mp4")) and os.path.isfile(os.path.join(root, "frames.csv")):
        return [root]
    out = []
    for f in glob.glob(os.path.join(root, "**", "video.mp4"), recursive=True):
        d = os.path.dirname(f)
        if os.path.isfile(os.path.join(d, "frames.csv")):
            out.append(d)
    return sorted(out)


def run_session(session_dir: str, cfg: argparse.Namespace) -> dict:
    frames = read_frames_csv(os.path.join(session_dir, "frames.csv"))
    session_meta = None
    sj = os.path.join(session_dir, "session.json")
    if os.path.isfile(sj):
        try:
            session_meta = json.load(open(sj))
        except Exception:
            session_meta = None

    # signal selection: mouse first, camera-pose fallback
    signal_source = ""
    action_dx = np.zeros(0)
    action_dy = np.zeros(0)
    if _MOUSE_DX_COLUMN in frames and _MOUSE_DY_COLUMN in frames:
        mouse_dx = np.nan_to_num(frames[_MOUSE_DX_COLUMN])
        mouse_dy = np.nan_to_num(frames[_MOUSE_DY_COLUMN])
        if _active_fraction(mouse_dx, mouse_dy) >= cfg.min_active_fraction:
            signal_source = "mouse"
            signs = _mouse_convention_signs(session_meta)
            action_dx = mouse_dx * signs.dx
            action_dy = mouse_dy * signs.dy
    if not signal_source:
        pose = camera_pose_look_deltas(frames)
        if pose is not None and _active_fraction(pose.dx, pose.dy) >= cfg.min_active_fraction:
            signal_source = "camera_pose"
            action_dx, action_dy = pose.dx, pose.dy
    if not signal_source:
        return {"status": "SKIP", "message": "no active look signal (mouse + camera pose both inactive)",
                "metrics": {"signal_source": "none"}}

    motion = compute_video_global_motion(
        os.path.join(session_dir, "video.mp4"),
        max_frames=cfg.max_analysis_frames,
        downscale_width=cfg.downscale_width,
    )
    estimate = estimate_action_video_lag(
        motion.dx, motion.dy, action_dx, action_dy,
        max_lag_frames=cfg.max_lag_frames,
        smoothing_window_frames=cfg.smoothing_window_frames,
    )
    frame_rate = cfg.fps or motion.frame_rate or 60.0
    lag_ms = estimate.lag_frames / frame_rate * 1000.0
    metrics = {
        "lag_frames": estimate.lag_frames,
        "lag_ms": round(lag_ms, 1),
        "frame_rate": round(frame_rate, 3),
        "correlation": round(estimate.correlation, 4),
        "analyzed_frames": estimate.analyzed_frames,
        "active_fraction": round(estimate.active_fraction, 4),
        "signal_source": signal_source,
    }

    # three gates, in order
    if estimate.active_fraction < cfg.min_active_fraction:
        return {"status": "SKIP", "metrics": metrics,
                "message": f"{signal_source} signal inactive within analyzed window "
                           f"(active_fraction={estimate.active_fraction:.4f} < {cfg.min_active_fraction})"}
    if abs(estimate.correlation) < cfg.min_abs_correlation:
        return {"status": "WARN", "metrics": metrics,
                "message": f"{signal_source}-to-motion correlation too weak to verify alignment "
                           f"(|corr|={abs(estimate.correlation):.3f} < {cfg.min_abs_correlation})"}
    direction = "behind" if estimate.lag_frames < 0 else "ahead of"
    if abs(lag_ms) > cfg.max_abs_lag_ms:
        return {"status": "FAIL", "metrics": metrics,
                "message": f"video {abs(lag_ms):.1f} ms {direction} inputs "
                           f"(|lag|>{cfg.max_abs_lag_ms:.0f}ms); correlation {estimate.correlation:+.2f}"}
    return {"status": "PASS", "metrics": metrics,
            "message": f"aligned within tolerance (video {abs(lag_ms):.1f} ms {direction} inputs, "
                       f"correlation {estimate.correlation:+.2f})"}


ICON = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL", "SKIP": "SKIP", "ERROR": "ERR "}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Local controls-to-video sync check.")
    p.add_argument("path", nargs="?", help="A session folder (video.mp4 + frames.csv) or a parent to scan.")
    p.add_argument("--fps", type=float, default=0.0, help="Pin frame rate (overrides container).")
    p.add_argument("--max-analysis-frames", type=int, default=DEFAULT_MAX_ANALYSIS_FRAMES, dest="max_analysis_frames")
    p.add_argument("--downscale-width", type=int, default=DEFAULT_DOWNSCALE_WIDTH, dest="downscale_width")
    p.add_argument("--max-lag-frames", type=int, default=DEFAULT_MAX_LAG_FRAMES, dest="max_lag_frames")
    p.add_argument("--smoothing-window-frames", type=int, default=DEFAULT_SMOOTHING_WINDOW_FRAMES, dest="smoothing_window_frames")
    p.add_argument("--min-active-fraction", type=float, default=DEFAULT_MIN_ACTIVE_FRACTION, dest="min_active_fraction")
    p.add_argument("--min-abs-correlation", type=float, default=DEFAULT_MIN_ABS_CORRELATION, dest="min_abs_correlation")
    p.add_argument("--max-abs-lag-ms", type=float, default=DEFAULT_MAX_ABS_LAG_MS, dest="max_abs_lag_ms")
    p.add_argument("--explain-sign", action="store_true", help="Print the sign-convention caveat and exit.")
    cfg = p.parse_args(argv)

    if cfg.explain_sign:
        print("The check only flips the mouse axis when the vendor declares dx_positive=left or")
        print("dy_positive=up. With a standard declaration (right/down) no flip is applied, so a")
        print("CORRECT first-person capture yields a NEGATIVE correlation (the scene moves opposite")
        print("the camera: look right -> world slides left). Treat |correlation| as the health")
        print("signal, not the sign. This matches the QA pipeline's behavior.")
        return 0

    if not cfg.path:
        p.error("path is required (a session folder or a parent to scan)")

    sessions = find_sessions(cfg.path)
    if not sessions:
        sys.exit(f"No sessions (video.mp4 + frames.csv) found under: {cfg.path}")

    print(f"action_video_grounding - {len(sessions)} session(s) | "
          f"gates: active>={cfg.min_active_fraction}, |corr|>={cfg.min_abs_correlation}, "
          f"|lag|<={cfg.max_abs_lag_ms:.0f}ms\n")
    counts = {}
    worst = 0
    for d in sessions:
        name = os.path.basename(d)
        try:
            r = run_session(d, cfg)
        except Exception as exc:  # noqa: BLE001
            r = {"status": "ERROR", "message": f"{type(exc).__name__}: {exc}", "metrics": {}}
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["status"] == "FAIL":
            worst = max(worst, 2)
        elif r["status"] in ("WARN", "ERROR"):
            worst = max(worst, 1)
        print(f"[{ICON.get(r['status'], r['status'])}] {name}")
        print(f"       {r['message']}")
        m = r.get("metrics") or {}
        if m:
            print("       " + " | ".join(
                f"{k}={m[k]}" for k in ("lag_ms", "correlation", "active_fraction",
                                        "analyzed_frames", "frame_rate", "signal_source") if k in m))
        print()

    print("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
