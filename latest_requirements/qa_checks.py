"""qa_checks.py — Spec QA checks for the Game Data Capture Spec v1.

Accepts a list of canonical-format row dicts and runs all checks.
Returns a QAResult with PASS / WARN / FAIL status and a list of
human-readable issue strings.

Column aliases are expected to already be resolved to canonical names before
calling run_checks().  Pass a QAConfig to skip or downgrade checks for data
that is not provided (e.g. no camera matrices).
"""
from __future__ import annotations

import json
import math
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# QA check configuration
# ---------------------------------------------------------------------------

class QALevel(str, Enum):
    REQUIRED = "required"   # failure → FAIL status
    WARN     = "warn"       # failure → WARN status (session still usable)
    SKIP     = "skip"       # check not run


@dataclass
class QAConfig:
    """QA rule configuration.  All defaults match the full spec."""
    camera_matrix:     QALevel = QALevel.REQUIRED  # det(R), ortho, bottom row, upright, roll
    camera_intrinsics: QALevel = QALevel.REQUIRED  # fx/fy columns + square-pixel check
    camera_orientation: QALevel = QALevel.WARN     # upright + roll (subset of camera_matrix)
    timestamp_mono:    QALevel = QALevel.REQUIRED  # strictly-increasing timestamps
    timestamp_jitter:  QALevel = QALevel.WARN      # frames outside [0.1×, 3×] median interval
    mouse_capture:     QALevel = QALevel.WARN      # dx/dy all zero
    key_checks:        QALevel = QALevel.WARN      # lctrl+ctrl, mouse_right leak, hotkey leak
    frame_count:       QALevel = QALevel.WARN      # video frame count vs CSV row count
    frame_sync:        QALevel = QALevel.WARN      # video duration vs CSV timestamp span
    nan_inf:           QALevel = QALevel.REQUIRED  # NaN/Inf in any numeric column
    duplicate_frames:  QALevel = QALevel.REQUIRED  # duplicate frame_id values
    fps_range:         QALevel = QALevel.WARN      # FPS outside [5, 240]
    session_length:    QALevel = QALevel.WARN      # session under 30 seconds
    camera_stationary: QALevel = QALevel.WARN      # camera position never changes
    teleport:          QALevel = QALevel.WARN      # unrealistic single-frame position jump
    camera_spin:       QALevel = QALevel.WARN      # sustained extreme angular velocity
    fov_range:         QALevel = QALevel.WARN      # derived FOV outside [10°, 150°]
    video_present:     QALevel = QALevel.REQUIRED   # no MP4 delivered alongside CSV


# ---------------------------------------------------------------------------
# Thresholds (from spec + accumulated QA experience)
# ---------------------------------------------------------------------------

REQUIRED_BASE_COLUMNS = frozenset({
    "frame_id", "timestamp_ms",
    "input_keys", "input_mouse_buttons", "input_mouse_dx", "input_mouse_dy",
})
C2W_COLUMNS = frozenset(f"c2w_m{r}{c}" for r in range(4) for c in range(4))
INTRINSIC_COLUMNS = frozenset({"camera_model", "camera_fx", "camera_fy", "camera_cx", "camera_cy"})

ORTHO_FAIL_THRESHOLD    = 1e-4
ORTHO_FAIL_FRAME_FRAC   = 0.001  # FAIL only if > 0.1% of frames exceed threshold; isolated
                                  # floating-point noise frames (e.g. 4/33435) become WARN
ORTHO_WARN_THRESHOLD    = 1e-5
# Keys that are legitimately held for most of a session during normal gameplay; requires a
# much higher fraction before flagging (player just running forward the whole time).
MOVEMENT_KEYS           = frozenset({"w", "a", "s", "d", "space",
                                      "up", "down", "left", "right",
                                      "arrow_up", "arrow_down", "arrow_left", "arrow_right"})
TOOL_KEY_FRACTION       = 0.90   # modifier/uncommon keys: flag at 90%
MOVEMENT_KEY_FRACTION   = 0.99   # movement keys: only flag when held in ≥99% of all frames
ROLL_THRESHOLD          = 0.10
ROLL_FRAME_FRACTION     = 0.05
LCTRL_CTRL_THRESHOLD    = 10
FX_FY_PIXEL_TOLERANCE   = 1.0
FRAME_COUNT_TOLERANCE   = 0.01   # WARN if |csv - video| > max(5, 1% of csv frames)
FRAME_SYNC_TOLERANCE_S  = 2.0    # WARN if |csv_duration - video_duration| > 2 seconds
FPS_MIN                 = 5.0
FPS_MAX                 = 240.0
MIN_SESSION_SECONDS     = 30.0
FOV_MIN_DEG             = 10.0
FOV_MAX_DEG             = 150.0
TELEPORT_FACTOR         = 25.0   # jump > 25× 99th-percentile delta = teleport
TELEPORT_MIN_UNITS      = 5.0    # absolute minimum jump size; prevents false positives in
                                 # slow-moving sessions where p99 is tiny
INVERT_THRESHOLD        = 0.1    # m21 must be < -0.1 for a frame to count as inverted
                                 # (≤0 triggers on normal steep look-down; -0.1 ≈ 96° from vertical)
INVERT_SUSTAINED_FRAC   = 0.01   # also flag if ≥1% of frames are in [−0.1, 0] (sustained lean)
INVERT_VARIATION_MIN    = 0.02   # sustained-lean check requires std(m21) > this; suppresses
                                 # constant-orientation games (e.g. NMS defaults to m21≈−0.024)
ROLL_VARIATION_MIN      = 0.02   # roll check requires std(m20) > this; suppresses games whose
                                 # C2W convention has a constant non-zero m20 (e.g. NMS m20≈−0.83)
SPIN_DEG_PER_FRAME      = 30.0   # degrees/frame
SPIN_FRAME_FRACTION     = 0.05   # >5% of frames spinning


def _video_info(mp4_path: Path) -> tuple[int, float] | tuple[None, None]:
    """Return (frame_count, duration_seconds) from ffprobe, or (None, None) on failure."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-select_streams", "v:0", str(mp4_path)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return None, None
        stream = json.loads(r.stdout).get("streams", [{}])[0]
        # nb_frames is a string when present; fall back to duration * avg_frame_rate
        nb = stream.get("nb_frames")
        frame_count = int(nb) if nb and nb != "N/A" else None
        duration = float(stream.get("duration", 0)) or None
        if frame_count is None and duration:
            rate_str = stream.get("avg_frame_rate", "0/1")
            num, den = (int(x) for x in rate_str.split("/"))
            frame_count = int(duration * num / den) if den else None
        return frame_count, duration
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Result data structure
# ---------------------------------------------------------------------------

@dataclass
class QAResult:
    vendor: str
    task_id: str
    game: str
    status: str = "PASS"
    frame_count: int = 0
    fps: float = 0.0
    footage_hours: float = 0.0
    issues: list[str] = field(default_factory=list)

    def _apply(self, level: QALevel, msg: str) -> None:
        """Record an issue at the appropriate severity given the QAConfig."""
        if level == QALevel.SKIP:
            return
        if level == QALevel.REQUIRED:
            self.status = "FAIL"
            self.issues.append(f"FAIL: {msg}")
        else:  # WARN
            if self.status == "PASS":
                self.status = "WARN"
            self.issues.append(f"WARN: {msg}")

    def add_fail(self, msg: str) -> None:
        self.status = "FAIL"
        self.issues.append(f"FAIL: {msg}")

    def add_warn(self, msg: str) -> None:
        if self.status == "PASS":
            self.status = "WARN"
        self.issues.append(f"WARN: {msg}")

    def to_dict(self) -> dict:
        return {
            "vendor":        self.vendor,
            "task":          self.task_id,
            "game":          self.game,
            "status":        self.status,
            "frame_count":   self.frame_count,
            "fps":           round(self.fps, 1),
            "footage_hours": round(self.footage_hours, 4),
            "issues":        [i.replace("FAIL: ", "").replace("WARN: ", "") for i in self.issues],
        }


# ---------------------------------------------------------------------------
# QA entry point
# ---------------------------------------------------------------------------

def run_checks(
    rows: list[dict],
    vendor: str,
    task_id: str,
    game: str,
    qa_config: Optional[QAConfig] = None,
    video_path: Optional[Path] = None,
) -> QAResult:
    """Run all spec checks on canonical-format rows.  Never raises."""
    cfg = qa_config or QAConfig()
    result = QAResult(vendor=vendor, task_id=task_id, game=game)

    if not rows:
        result.add_fail("empty dataset")
        return result

    result.frame_count = len(rows)

    # --- 1. Required base columns (always checked) ---
    actual_cols = set(rows[0].keys())
    missing_base = REQUIRED_BASE_COLUMNS - actual_cols
    if missing_base:
        result.add_fail(f"missing columns: {', '.join(sorted(missing_base))}")
        return result

    # --- 1b. Duplicate frame IDs ---
    if cfg.duplicate_frames != QALevel.SKIP:
        frame_ids = [r["frame_id"] for r in rows]
        n_dupes = len(frame_ids) - len(set(frame_ids))
        if n_dupes:
            result._apply(cfg.duplicate_frames, f"duplicate frame_id values: {n_dupes} duplicates")

    # Camera matrix columns
    if cfg.camera_matrix != QALevel.SKIP:
        missing_c2w = C2W_COLUMNS - actual_cols
        if missing_c2w:
            result._apply(cfg.camera_matrix, f"missing C2W columns: {', '.join(sorted(missing_c2w))}")
            if cfg.camera_matrix == QALevel.REQUIRED:
                return result  # can't run matrix checks without the columns

    # Intrinsic columns
    if cfg.camera_intrinsics != QALevel.SKIP:
        missing_intr = INTRINSIC_COLUMNS - actual_cols
        if missing_intr:
            result._apply(cfg.camera_intrinsics, f"missing intrinsic columns: {', '.join(sorted(missing_intr))}")

    # --- 2. Timestamps ---
    try:
        ts_ms = np.array([float(r["timestamp_ms"]) for r in rows])
    except (ValueError, KeyError) as exc:
        result.add_fail(f"timestamp parse error: {exc}")
        return result

    if cfg.nan_inf != QALevel.SKIP and not np.all(np.isfinite(ts_ms)):
        result._apply(cfg.nan_inf, "NaN/Inf in timestamp_ms")

    dts = np.diff(ts_ms)
    non_mono = int(np.sum(dts <= 0))
    if non_mono:
        result._apply(cfg.timestamp_mono, f"timestamps non-monotonic: {non_mono} regressions")

    pos_dts = dts[dts > 0]
    if len(pos_dts) > 0:
        median_dt = float(np.median(pos_dts))
        result.fps = 1000.0 / median_dt if median_dt > 0 else 0.0
        result.footage_hours = (len(rows) / result.fps / 3600.0) if result.fps > 0 else 0.0

        if cfg.fps_range != QALevel.SKIP and result.fps > 0:
            if not (FPS_MIN <= result.fps <= FPS_MAX):
                result._apply(cfg.fps_range, f"FPS={result.fps:.1f} outside expected range [{FPS_MIN}, {FPS_MAX}]")

        if cfg.session_length != QALevel.SKIP and result.footage_hours > 0:
            session_s = result.footage_hours * 3600.0
            if session_s < MIN_SESSION_SECONDS:
                result._apply(cfg.session_length, f"session too short: {session_s:.1f}s (minimum {MIN_SESSION_SECONDS:.0f}s)")

        jitter = int(np.sum((dts > 3 * median_dt) | ((dts > 0) & (dts < 0.1 * median_dt))))
        if jitter > len(dts) * 0.05:
            result._apply(
                cfg.timestamp_jitter,
                f"timestamp jitter: {jitter} frames outside [0.1×, 3×] median interval",
            )

    # --- 3. C2W matrix checks ---
    if cfg.camera_matrix != QALevel.SKIP and C2W_COLUMNS <= actual_cols:
        try:
            c2w = np.array(
                [[float(rows[i][f"c2w_m{r}{c}"]) for c in range(4)]
                 for i in range(len(rows)) for r in range(4)],
                dtype=np.float64,
            ).reshape(len(rows), 4, 4)
        except (KeyError, ValueError) as exc:
            result._apply(cfg.camera_matrix, f"c2w parse error: {exc}")
        else:
            if cfg.nan_inf != QALevel.SKIP and not np.all(np.isfinite(c2w)):
                result._apply(cfg.nan_inf, "NaN/Inf in C2W matrix values")

            R = c2w[:, :3, :3]

            # det(R) = +1
            dets = np.linalg.det(R)
            bad_det = int(np.sum(dets < 0))
            if bad_det:
                result._apply(cfg.camera_matrix, f"det(R) < 0 in {bad_det}/{len(rows)} frames (left-handed)")

            # R×Rᵀ = I
            RRt = np.matmul(R, R.transpose(0, 2, 1))
            eye_diff = RRt - np.eye(3)[None]
            ortho_errs = np.linalg.norm(eye_diff, ord="fro", axis=(1, 2))
            max_ortho = float(np.max(ortho_errs))
            bad_ortho_frames = int(np.sum(ortho_errs > ORTHO_FAIL_THRESHOLD))
            if max_ortho > ORTHO_FAIL_THRESHOLD and bad_ortho_frames > len(rows) * ORTHO_FAIL_FRAME_FRAC:
                result._apply(
                    cfg.camera_matrix,
                    f"C2W ortho error > {ORTHO_FAIL_THRESHOLD:.0e} in {bad_ortho_frames}/{len(rows)} frames "
                    f"(max {max_ortho:.2e})",
                )
            elif max_ortho > ORTHO_FAIL_THRESHOLD:
                # Isolated bad frames — likely floating-point noise from the game engine
                result._apply(
                    QALevel.WARN,
                    f"C2W ortho error {max_ortho:.2e} in {bad_ortho_frames} frame(s) — "
                    f"below {ORTHO_FAIL_FRAME_FRAC*100:.1f}% frame threshold, likely floating-point noise",
                )
            elif max_ortho > ORTHO_WARN_THRESHOLD:
                result._apply(
                    QALevel.WARN,  # ortho warn is always WARN regardless of config
                    f"C2W ortho error {max_ortho:.2e} exceeds warn threshold {ORTHO_WARN_THRESHOLD:.0e}",
                )

            # Bottom row [0,0,0,1]
            bot = c2w[:, 3, :]
            bad_bot = int(np.sum(np.any(np.abs(bot - [0, 0, 0, 1]) > 1e-6, axis=1)))
            if bad_bot:
                result._apply(cfg.camera_matrix, f"bottom row not [0,0,0,1] in {bad_bot} frames")

            # Camera orientation checks (subset of camera_matrix)
            orient_level = cfg.camera_orientation if cfg.camera_matrix == QALevel.SKIP else cfg.camera_matrix
            orient_level = QALevel.WARN  # orientation is always at most WARN

            m21 = c2w[:, 2, 1]
            # Significant inversion: up vector clearly past horizontal (< −INVERT_THRESHOLD)
            inverted = int(np.sum(m21 < -INVERT_THRESHOLD))
            # Sustained lean: many frames hovering in the marginal [−threshold, 0] band
            marginal = int(np.sum((m21 < 0) & (m21 >= -INVERT_THRESHOLD)))
            if inverted:
                result._apply(orient_level, f"c2w_m21 < -{INVERT_THRESHOLD} in {inverted} frames (camera inverted/upside-down)")
            elif marginal > len(rows) * INVERT_SUSTAINED_FRAC and float(np.std(m21)) > INVERT_VARIATION_MIN:
                result._apply(orient_level, f"c2w_m21 in [−{INVERT_THRESHOLD}, 0) in {marginal}/{len(rows)} frames (camera sustained near-horizontal)")

            m20 = c2w[:, 2, 0]
            rolling = int(np.sum(np.abs(m20) > ROLL_THRESHOLD))
            if rolling > len(rows) * ROLL_FRAME_FRACTION and float(np.std(m20)) > ROLL_VARIATION_MIN:
                result._apply(
                    orient_level,
                    f"|c2w_m20| > {ROLL_THRESHOLD} in {rolling}/{len(rows)} frames "
                    f"({rolling / len(rows) * 100:.1f}%) — possible camera roll",
                )

            # Camera position checks (translation column)
            positions = c2w[:, :3, 3]
            if len(positions) > 1:
                deltas = np.linalg.norm(np.diff(positions, axis=0), axis=1)

                if cfg.camera_stationary != QALevel.SKIP:
                    if np.all(deltas == 0):
                        result._apply(cfg.camera_stationary, "camera position never changes (translation all zero)")

                if cfg.teleport != QALevel.SKIP:
                    nonzero = deltas[deltas > 0]
                    if len(nonzero) >= 2:
                        threshold = max(float(np.percentile(nonzero, 99)) * TELEPORT_FACTOR, TELEPORT_MIN_UNITS)
                        n_teleport = int(np.sum(deltas > threshold))
                        if n_teleport:
                            result._apply(
                                cfg.teleport,
                                f"teleportation detected: {n_teleport} frame(s) with position jump "
                                f"> {TELEPORT_FACTOR:.0f}× 99th-percentile delta (≥{TELEPORT_MIN_UNITS:.0f} units)",
                            )

                if cfg.camera_spin != QALevel.SKIP:
                    R_rel = np.matmul(R[1:], R[:-1].transpose(0, 2, 1))
                    cos_a = np.clip((np.trace(R_rel, axis1=1, axis2=2) - 1.0) / 2.0, -1.0, 1.0)
                    angles_deg = np.degrees(np.arccos(cos_a))
                    spinning = int(np.sum(angles_deg > SPIN_DEG_PER_FRAME))
                    if spinning > len(rows) * SPIN_FRAME_FRACTION:
                        result._apply(
                            cfg.camera_spin,
                            f"camera spin: {spinning}/{len(rows)} frames "
                            f"({spinning / len(rows) * 100:.1f}%) rotate >{SPIN_DEG_PER_FRAME:.0f}°/frame",
                        )

    # --- 4. Camera intrinsics: fx ≈ fy (square pixels) + FOV range ---
    if cfg.camera_intrinsics != QALevel.SKIP and INTRINSIC_COLUMNS <= actual_cols:
        try:
            fx = np.array([float(r["camera_fx"]) for r in rows])
            fy = np.array([float(r["camera_fy"]) for r in rows])
            cy = np.array([float(r["camera_cy"]) for r in rows])

            if cfg.nan_inf != QALevel.SKIP and not (np.all(np.isfinite(fx)) and np.all(np.isfinite(fy))):
                result._apply(cfg.nan_inf, "NaN/Inf in camera intrinsic values")

            mismatch = int(np.sum(np.abs(fx - fy) > FX_FY_PIXEL_TOLERANCE))
            if mismatch:
                result._apply(
                    QALevel.WARN,
                    f"camera_fx ≠ camera_fy in {mismatch} frames (non-square pixels)",
                )

            if cfg.fov_range != QALevel.SKIP:
                valid_fy = fy[fy > 0]
                valid_cy = cy[fy > 0]
                if len(valid_fy) > 0:
                    fovs = np.degrees(2.0 * np.arctan(valid_cy / valid_fy))
                    median_fov = float(np.median(fovs))
                    if not (FOV_MIN_DEG <= median_fov <= FOV_MAX_DEG):
                        result._apply(
                            cfg.fov_range,
                            f"derived vertical FOV={median_fov:.1f}° outside expected range "
                            f"[{FOV_MIN_DEG:.0f}°, {FOV_MAX_DEG:.0f}°]",
                        )
        except (ValueError, KeyError):
            pass

    # --- 5. Mouse dx/dy all zero ---
    if cfg.mouse_capture != QALevel.SKIP:
        try:
            mouse_dx = np.array([float(r.get("input_mouse_dx") or 0) for r in rows])
            mouse_dy = np.array([float(r.get("input_mouse_dy") or 0) for r in rows])
            if np.all(mouse_dx == 0) and np.all(mouse_dy == 0):
                result._apply(cfg.mouse_capture, "mouse dx/dy all zero for entire session (mouse capture may have failed)")
        except ValueError:
            pass

    # --- 6. Keyboard / input checks ---
    if cfg.key_checks != QALevel.SKIP:
        # lctrl + ctrl co-occurrence: only flag when the specific form (lctrl/rctrl) is absent,
        # meaning the worker used the generic alias without normalising.
        lctrl_ctrl_frames = sum(
            1 for r in rows
            if "ctrl" in {k.strip() for k in (r.get("input_keys") or "").lower().split("|")}
            and not (
                "lctrl" in {k.strip() for k in (r.get("input_keys") or "").lower().split("|")}
                or "rctrl" in {k.strip() for k in (r.get("input_keys") or "").lower().split("|")}
            )
        )
        if lctrl_ctrl_frames > LCTRL_CTRL_THRESHOLD:
            result._apply(
                cfg.key_checks,
                f"'ctrl' in input_keys without lctrl/rctrl in {lctrl_ctrl_frames} frames (worker key-normalization issue)",
            )

        # Mouse button names leaking into input_keys: only flag frames where the button
        # is NOT also present in the correct input_mouse_buttons column.
        # e.g. "mouse_right" in input_keys is fine when "right" is in input_mouse_buttons.
        mouse_key_leak_frames = 0
        for r in rows:
            keys = {k.strip().lower() for k in (r.get("input_keys") or "").split("|") if k.strip()}
            btns = {b.strip().lower() for b in (r.get("input_mouse_buttons") or "").split("|") if b.strip()}
            for key in keys:
                if key.startswith("mouse_"):
                    canonical = key[len("mouse_"):]   # "mouse_right" → "right"
                    if canonical not in btns:
                        mouse_key_leak_frames += 1
                        break
        if mouse_key_leak_frames:
            result._apply(
                cfg.key_checks,
                f"mouse button name(s) in input_keys without matching input_mouse_buttons in {mouse_key_leak_frames} frames",
            )

        # Sustained key presses — two distinct thresholds:
        #   Movement keys (w/a/s/d/space/arrows): players legitimately hold these; only flag at ≥99%
        #   Modifier/uncommon keys: flag at ≥90% as possible capture tool hotkey leak
        key_counts: Counter = Counter()
        for r in rows:
            keys_raw = (r.get("input_keys") or "").strip()
            if not keys_raw:
                continue
            for k in keys_raw.lower().split("|"):
                k = k.strip()
                if k and "mouse" not in k:
                    key_counts[k] += 1

        for key, count in key_counts.most_common(10):
            fraction = count / len(rows)
            if key in MOVEMENT_KEYS:
                if fraction >= MOVEMENT_KEY_FRACTION:
                    result._apply(
                        cfg.key_checks,
                        f"movement key '{key}' held in {count}/{len(rows)} frames ({fraction * 100:.1f}%) "
                        f"— monotonous input, player may be holding forward the entire session",
                    )
            elif fraction >= TOOL_KEY_FRACTION:
                result._apply(
                    cfg.key_checks,
                    f"key '{key}' held in {count}/{len(rows)} frames ({fraction * 100:.1f}%) "
                    f"— possible capture tool hotkey leak",
                )

    # --- 7. Video / CSV frame-count and duration sync ---
    if cfg.video_present != QALevel.SKIP and (video_path is None or not video_path.exists()):
        result._apply(cfg.video_present, "no MP4 video file found for this session")

    if video_path is not None and video_path.exists():
        video_frames, video_duration_s = _video_info(video_path)

        if cfg.frame_count != QALevel.SKIP and video_frames is not None:
            csv_frames = len(rows)
            diff = abs(csv_frames - video_frames)
            threshold = max(5, int(csv_frames * FRAME_COUNT_TOLERANCE))
            if diff > threshold:
                result._apply(
                    cfg.frame_count,
                    f"frame count mismatch: CSV={csv_frames}, video={video_frames} (diff={diff})",
                )

        if cfg.frame_sync != QALevel.SKIP and video_duration_s is not None and len(ts_ms) >= 2:
            csv_duration_s = (float(ts_ms[-1]) - float(ts_ms[0])) / 1000.0
            delta_s = abs(csv_duration_s - video_duration_s)
            if delta_s > FRAME_SYNC_TOLERANCE_S:
                result._apply(
                    cfg.frame_sync,
                    f"duration mismatch: CSV={csv_duration_s:.1f}s, video={video_duration_s:.1f}s (Δ={delta_s:.1f}s)",
                )

    return result
