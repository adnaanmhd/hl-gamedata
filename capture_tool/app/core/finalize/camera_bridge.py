"""Camera-pose bridge — fix for issue #2 ("camera pose/intrinsics columns are
100% empty in every delivery").

The problem
-----------
HumynCapture only ever sees the game's composited screen output + OS-level
mouse/keyboard. It has no way to read the game engine's actual camera
transform, so `frames.csv`'s c2w_m00..m33 / camera_fx/fy/cx/cy / distortion
columns have been unconditionally blank in every real delivery checked so
far (see `translator/binner.py`'s `empty_camera` and `translator/v2.py`'s
`extra_null` — there is no wiring for this at all today, by design, because
nothing upstream has ever had this data to give it).

The fix
-------
A separate BepInEx plugin (`unity_plugin/CameraLogger/`, UNVERIFIED — no
Unity/BepInEx toolchain available here to build or test it) runs INSIDE the
Unity game process and is the only part of this whole pipeline that can
actually read `Camera.main`'s real transform. It writes one JSON line per
frame to `%LOCALAPPDATA%\\HumynCapture\\camera_bridge\\<pid>.jsonl`, keyed by
its own process id — which HumynCapture's own `metadata.json` already
records as `game.pid_at_capture` (session_engine.py), so no in-process
coordination is needed between the two: HumynCapture attaches to an
ALREADY-RUNNING game (process_watcher.py), it doesn't launch it, so it has
no chance to hand the plugin anything at startup time. The pid is the only
handshake that works with that constraint.

This module runs as a POST-PROCESSING step over `frames.csv` *after*
`translate_bundle_v2` has already written it (see `finalize/pipeline.py`),
rather than adding camera plumbing into `translator/binner.py`/`v2.py`
directly — same "don't duplicate/risk drifting from already-tested logic"
reasoning as the native-v2-emission fix: this only ever fills in columns
`translate_bundle_v2` already writes blank, it never changes how any other
column gets computed.

Coordinate convention: logged verbatim from Unity's own left-handed,
X-right/Y-up/Z-forward transform — this already matches the client's stated
spec (`Top down view game demands for data`, §4: "left-handed coordinate
system... Right-x, Up-y, Front-z"), so no axis conversion happens here.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from translator.v2 import C2W_COLS, CAMERA_COLS  # noqa: F401 (documents the contract)

# Real column order this module fills in, matching translator/v2.py's
# V2_FRAME_COLS exactly (C2W_COLS then CAMERA_COLS) — kept as a local literal
# rather than re-deriving it, so a change to translator's column order breaks
# this module's tests loudly instead of silently writing misaligned values.
_C2W_FIELDS = [f"c2w_m{r}{c}" for r in range(4) for c in range(4)]
_CAMERA_FIELDS = ["camera_model", "camera_fx", "camera_fy", "camera_cx", "camera_cy"]

# A camera sample more than this far (in ms) from a video frame's own
# timestamp is not a real match — better to leave that frame's camera
# columns blank (as they are today) than to silently attach the wrong
# camera pose to a frame. One frame at 30fps is ~33ms; a few frames of
# slack absorbs the same class of clock-skew this whole session has
# already been dealing with (A2), without accepting a clearly-wrong pairing.
MAX_MATCH_GAP_MS = 100


def load_camera_samples(path: Path) -> list[dict]:
    """Parse the BepInEx plugin's JSONL output. Skips (does not raise on)
    any line that fails to parse — a truncated last line from a crash/kill
    mid-write must not lose every earlier, valid sample."""
    samples = []
    if not path.exists():
        return samples
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            samples.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    samples.sort(key=lambda s: s["wallclock_ms"])
    return samples


def find_camera_log(pid: int, camera_bridge_dir: Path) -> Path | None:
    """Locate this session's camera log by the game's own pid — see module
    docstring for why pid is the only handshake available."""
    candidate = camera_bridge_dir / f"{pid}.jsonl"
    return candidate if candidate.exists() else None


def quaternion_to_c2w_matrix(
    position: tuple[float, float, float],
    quaternion: tuple[float, float, float, float],
) -> list[float]:
    """Camera-to-world 4x4 matrix (row-major, 16 values matching
    `_C2W_FIELDS`'s m00..m33 order) from Unity's own position + quaternion
    (x, y, z, w). No handedness conversion — Unity's quaternion math is
    already self-consistent within its own left-handed frame, so applying
    the standard quaternion->rotation-matrix formula to its raw components
    correctly reproduces Unity's own basis vectors; converting handedness
    would need axis negation, which is NOT done here since it would
    contradict the "log verbatim, no conversion" choice in the module
    docstring.
    """
    x, y, z, w = (float(v) for v in quaternion)
    r00 = 1 - 2 * (y * y + z * z)
    r01 = 2 * (x * y - w * z)
    r02 = 2 * (x * z + w * y)
    r10 = 2 * (x * y + w * z)
    r11 = 1 - 2 * (x * x + z * z)
    r12 = 2 * (y * z - w * x)
    r20 = 2 * (x * z - w * y)
    r21 = 2 * (y * z + w * x)
    r22 = 1 - 2 * (x * x + y * y)
    tx, ty, tz = (float(v) for v in position)
    return [
        r00, r01, r02, tx,
        r10, r11, r12, ty,
        r20, r21, r22, tz,
        0.0, 0.0, 0.0, 1.0,
    ]


def intrinsics_from_fov(fov_deg: float, width: int, height: int) -> tuple[float, float, float, float]:
    """fx/fy/cx/cy from Unity's vertical field-of-view + the recorded frame
    size. `fx == fy` here always, by construction — this is not a
    coincidence: it's only true when the projection's aspect ratio matches
    the pixel aspect ratio (width/height), which holds for an unmodified
    Camera.main render — and it's the client's own explicit acceptance
    criterion (`camera_intrinsics parameters, fx = fy`, spec §4.3#8).
    """
    fov_rad = math.radians(fov_deg)
    fy = (height / 2.0) / math.tan(fov_rad / 2.0)
    fx = fy
    return fx, fy, width / 2.0, height / 2.0


def _session_start_epoch_ms(session_json: dict) -> float:
    created_at = session_json["created_at_utc"]
    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).timestamp() * 1000.0


def _nearest_sample(target_wallclock_ms: float, samples: list[dict]) -> dict | None:
    """Binary-search-free nearest lookup — session-length sample counts
    (tens of thousands) make a linear scan with an early break plenty fast
    for a one-shot finalize step; not worth the complexity of bisect here."""
    best = None
    best_gap = None
    for s in samples:
        gap = abs(s["wallclock_ms"] - target_wallclock_ms)
        if best_gap is None or gap < best_gap:
            best, best_gap = s, gap
        elif s["wallclock_ms"] > target_wallclock_ms and best_gap is not None:
            # samples are sorted by wallclock_ms — once we've passed the
            # target and the gap is only growing, no later sample can beat
            # the best found so far.
            break
    if best is None or best_gap > MAX_MATCH_GAP_MS:
        return None
    return best


def patch_frames_csv(
    frames_csv_path: Path, session_json_path: Path, camera_log_path: Path,
) -> int:
    """Fills in the c2w_m*/camera_* columns of an already-written
    `frames.csv` in place, for every frame with a camera sample close enough
    in time to trust (see MAX_MATCH_GAP_MS). Frames with no close-enough
    sample are left exactly as they were (blank) — never guessed at.

    Returns the number of rows actually patched, for logging/diagnostics.
    """
    session = json.loads(session_json_path.read_text())
    samples = load_camera_samples(camera_log_path)
    if not samples:
        return 0
    start_epoch_ms = _session_start_epoch_ms(session)
    width = session["record_width_px"]
    height = session["record_height_px"]

    with frames_csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    patched = 0
    for row in rows:
        # timestamp_ms in frames.csv is VIDEO-relative (frame 0 = 0ms), per
        # translator/binner.py — convert to the same wallclock_ms scale the
        # plugin's samples use before matching.
        video_ms = float(row["timestamp_ms"])
        target_wallclock_ms = start_epoch_ms + video_ms
        sample = _nearest_sample(target_wallclock_ms, samples)
        if sample is None:
            continue
        c2w = quaternion_to_c2w_matrix(
            tuple(sample["position"]), tuple(sample["rotation_quaternion"]))
        for field, value in zip(_C2W_FIELDS, c2w):
            row[field] = repr(value)
        fx, fy, cx, cy = intrinsics_from_fov(sample["fov_deg"], width, height)
        row["camera_model"] = "pinhole"
        row["camera_fx"] = repr(fx)
        row["camera_fy"] = repr(fy)
        row["camera_cx"] = repr(cx)
        row["camera_cy"] = repr(cy)
        patched += 1

    with frames_csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return patched
