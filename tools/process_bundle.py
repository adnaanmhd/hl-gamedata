"""Process a HumynLabs session bundle: trim 5s head/tail losslessly, rebuild
frames.csv from real video PTS + inputs.jsonl, trim inputs.jsonl, update
session.json, and emit a flat spec-compliant bundle under out/.

Usage:
    python process_bundle.py \
        --session-dir <path to dir containing frames.csv/video.mp4/...> \
        --inputs-jsonl <path to inputs.jsonl> \
        --output-dir <path to write the new bundle>

Designed to be called per-session. A small wrapper enumerates the 6 sessions.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

TRIM_HEAD_S = 5.0
TRIM_TAIL_S = 5.0


# ------------------------- ffprobe / ffmpeg helpers ------------------------- #

def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if proc.returncode != 0:
        sys.stderr.write(
            f"\n[command failed] {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}\n"
        )
        proc.check_returncode()
    return proc


def probe_video_streams(video: Path) -> list[dict]:
    out = run([
        "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(video),
    ]).stdout
    return json.loads(out)["streams"]


def probe_format(video: Path) -> dict:
    out = run([
        "ffprobe", "-v", "error", "-show_format", "-of", "json", str(video),
    ]).stdout
    return json.loads(out)["format"]


def probe_video_frame_pts(video: Path) -> tuple[list[float], list[bool]]:
    """Return (pts_seconds, is_keyframe) for every video frame, sorted by PTS."""
    out = run([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "frame=pts_time,key_frame",
        "-of", "json",
        str(video),
    ]).stdout
    data = json.loads(out)
    pts: list[float] = []
    is_key: list[bool] = []
    for fr in data.get("frames", []):
        t_s = fr.get("pts_time")
        if t_s is None:
            continue
        try:
            t = float(t_s)
        except (TypeError, ValueError):
            continue
        kf = int(fr.get("key_frame", 0)) == 1
        pts.append(t)
        is_key.append(kf)
    paired = sorted(zip(pts, is_key), key=lambda x: x[0])
    return [p for p, _ in paired], [k for _, k in paired]


def has_audio_stream(streams: list[dict]) -> bool:
    return any(s.get("codec_type") == "audio" for s in streams)


# ------------------------- smart-cut video trim ----------------------------- #

def smart_cut(src: Path, dst: Path, start_s: float, end_s: float,
              pts: list[float], is_key: list[bool], audio: bool) -> None:
    """Trim [start_s, end_s) from src into dst. If a keyframe exists at start_s
    we stream-copy; otherwise we re-encode the small head segment losslessly
    (h264 -qp 0) and stream-copy the rest, then concat.

    Uses -frames:v with a precomputed frame count (instead of -to/-t) for
    deterministic frame-exact cuts. -to is unreliable with -c copy on videos
    with sparse keyframes."""
    # Find smallest index k with pts[k] >= start_s
    k = 0
    while k < len(pts) and pts[k] < start_s:
        k += 1
    if k >= len(pts):
        raise ValueError(f"start_s {start_s} past video end {pts[-1]}")

    # Find smallest index of a keyframe at or after start_s.
    kf_idx = k
    while kf_idx < len(pts) and not is_key[kf_idx]:
        kf_idx += 1
    if kf_idx >= len(pts):
        raise ValueError(f"no keyframe at or after {start_s}")

    keyframe_pts = pts[kf_idx]
    aligned = abs(keyframe_pts - start_s) < 1e-3 and k == kf_idx

    # Find index of last frame with pts < end_s
    end_idx = len(pts)
    while end_idx > 0 and pts[end_idx - 1] >= end_s:
        end_idx -= 1

    head_frames = kf_idx - k                 # frames in [start_s, keyframe_pts)
    tail_frames = end_idx - kf_idx            # frames in [keyframe_pts, end_s)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        if aligned:
            # Single stream copy with exact frame count
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-ss", f"{start_s:.6f}",
                "-i", str(src),
                "-frames:v", str(head_frames + tail_frames),
                "-c", "copy",
                "-map_metadata", "0",
                "-movflags", "+use_metadata_tags+faststart",
                "-avoid_negative_ts", "make_zero",
                str(dst),
            ]
            run(cmd)
            return

        # Two-segment smart cut
        head_mp4 = td_path / "head.mp4"
        tail_mp4 = td_path / "tail.mp4"
        list_txt = td_path / "list.txt"

        # Head: lossless re-encode covering exactly head_frames frames starting
        # at start_s.
        head_cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{start_s:.6f}",
            "-i", str(src),
            "-frames:v", str(head_frames),
            "-c:v", "libx264", "-preset", "veryslow", "-qp", "0",
            "-pix_fmt", "yuv420p",
        ]
        head_cmd += (["-c:a", "copy"] if audio else ["-an"])
        head_cmd += [
            "-avoid_negative_ts", "make_zero",
            "-map_metadata", "0",
            "-movflags", "+use_metadata_tags",
            str(head_mp4),
        ]
        run(head_cmd)

        # Tail: stream-copy exactly tail_frames frames starting at keyframe_pts.
        tail_cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{keyframe_pts:.6f}",
            "-i", str(src),
            "-frames:v", str(tail_frames),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-map_metadata", "0",
            "-movflags", "+use_metadata_tags",
            str(tail_mp4),
        ]
        run(tail_cmd)

        list_txt.write_text(
            f"file '{head_mp4.as_posix()}'\nfile '{tail_mp4.as_posix()}'\n"
        )

        concat_cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_txt),
            "-c", "copy",
            "-map_metadata", "0",
            "-movflags", "+use_metadata_tags+faststart",
            str(dst),
        ]
        run(concat_cmd)


# ------------------------- inputs.jsonl ------------------------------------- #

def load_inputs(path: Path) -> list[dict]:
    events: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    events.sort(key=lambda e: e["t"])
    return events


def normalize_key(k: str) -> str:
    # Current data already uses lowercase with side suffix; keep behaviour
    # deterministic without inventing translations.
    return k.lower()


def filter_and_shift_inputs(events: list[dict], start_us: int, end_us: int) -> list[dict]:
    out: list[dict] = []
    for e in events:
        t = e["t"]
        if start_us <= t < end_us:
            ne = dict(e)
            ne["t"] = t - start_us
            if ne.get("type") == "key" and "key" in ne:
                ne["key"] = normalize_key(ne["key"])
            elif ne.get("type") == "mouse_button" and "button" in ne:
                ne["button"] = normalize_key(ne["button"])
            out.append(ne)
    return out


def select_pre_window_events(events: list[dict], start_us: int) -> list[dict]:
    return [e for e in events if e["t"] < start_us]


# ------------------------- frames.csv rebuild ------------------------------- #

CAMERA_COLS = [
    "c2w_m00", "c2w_m01", "c2w_m02", "c2w_m03",
    "c2w_m10", "c2w_m11", "c2w_m12", "c2w_m13",
    "c2w_m20", "c2w_m21", "c2w_m22", "c2w_m23",
    "c2w_m30", "c2w_m31", "c2w_m32", "c2w_m33",
    "camera_model",
    "camera_fx", "camera_fy", "camera_cx", "camera_cy",
]

CSV_COLUMNS = [
    "frame_id", "timestamp_ms",
    *CAMERA_COLS,
    "input_keys", "input_actions", "input_mouse_buttons",
    "input_mouse_dx", "input_mouse_dy",
]


def resolve_bleed(keys_down: set[str], press_order: list[str],
                  key_binding: dict[str, list[str]]) -> set[str]:
    """Drop the older key in any pair that maps to the same semantic action."""
    if not keys_down:
        return set()
    # Map action -> most-recently-pressed key in keys_down for that action
    best_for_action: dict[str, str] = {}
    # Walk in press order so the LAST occurrence wins (overwrites)
    for k in press_order:
        if k not in keys_down:
            continue
        for action in key_binding.get(k, []):
            best_for_action[action] = k
    # Keys that need to stay are: keys with no binding (unmapped, kept as-is)
    # plus the winners
    keepers: set[str] = set()
    for k in keys_down:
        actions = key_binding.get(k, [])
        if not actions:
            keepers.add(k)
            continue
        if any(best_for_action.get(a) == k for a in actions):
            keepers.add(k)
    return keepers


def derive_actions(resolved_keys: Iterable[str], mouse_buttons: Iterable[str],
                   key_binding: dict[str, list[str]]) -> set[str]:
    actions: set[str] = set()
    for k in resolved_keys:
        actions.update(key_binding.get(k, []))
    for b in mouse_buttons:
        actions.update(key_binding.get(b, []))
    return actions


def rebuild_frames_csv(
    pts_after_trim: list[float],
    trim_start_s: float,
    events_shifted: list[dict],
    pre_window_events: list[dict],
    key_binding: dict[str, list[str]],
) -> tuple[list[dict], set[str]]:
    """Given the list of PTS times in the ORIGINAL clock that fall inside the
    kept window, and the events shifted to new clock, produce CSV rows.

    pre_window_events: events that occurred BEFORE trim_start_s, in original
    clock; used to compute the initial held-key state at frame 0.

    Returns (rows, unmapped_keys_seen)."""
    # Convert kept frame PTS into new-clock microseconds
    new_pts_us = [round((t - trim_start_s) * 1_000_000) for t in pts_after_trim]
    new_pts_us[0] = 0  # guarantee monotonic start at zero

    rows: list[dict] = []
    unmapped: set[str] = set()
    keys_down: set[str] = set()
    press_order: list[str] = []
    mouse_buttons_down: set[str] = set()
    mb_press_order: list[str] = []

    # Pre-roll: replay every event that fired before the window so any keys
    # held across the trim boundary land in the initial state.
    for e in pre_window_events:
        if e.get("type") == "mouse_raw":
            continue  # raw motion has no held state
        _apply_event(e, keys_down, press_order, mouse_buttons_down, mb_press_order)

    ev_idx = 0
    n_events = len(events_shifted)
    for frame_id, pts_us in enumerate(new_pts_us):
        dx_sum = 0
        dy_sum = 0
        while ev_idx < n_events and events_shifted[ev_idx]["t"] <= pts_us:
            e = events_shifted[ev_idx]
            if e.get("type") == "mouse_raw":
                dx_sum += int(e.get("dx", 0))
                dy_sum += int(e.get("dy", 0))
            else:
                _apply_event(e, keys_down, press_order, mouse_buttons_down, mb_press_order)
            ev_idx += 1

        resolved = resolve_bleed(keys_down, press_order, key_binding)
        actions = derive_actions(resolved, mouse_buttons_down, key_binding)

        # Track unmapped keys for binding completeness
        for k in keys_down:
            if k not in key_binding:
                unmapped.add(k)
        for b in mouse_buttons_down:
            if b not in key_binding:
                unmapped.add(b)

        row = {col: "" for col in CSV_COLUMNS}
        row["frame_id"] = frame_id
        row["timestamp_ms"] = round(pts_us / 1000)
        row["input_keys"] = "|".join(sorted(resolved))
        row["input_actions"] = "|".join(sorted(actions))
        row["input_mouse_buttons"] = "|".join(sorted(mouse_buttons_down))
        row["input_mouse_dx"] = dx_sum
        row["input_mouse_dy"] = dy_sum
        rows.append(row)
    return rows, unmapped


def _apply_event(e: dict, keys_down: set[str], press_order: list[str],
                 mouse_buttons: set[str], mb_press_order: list[str]) -> None:
    t = e.get("type")
    if t == "key":
        k = normalize_key(e["key"])
        a = e.get("action")
        if a == "down":
            if k not in keys_down:
                keys_down.add(k)
                press_order.append(k)
        elif a == "up":
            keys_down.discard(k)
            if k in press_order:
                press_order.remove(k)
    elif t == "mouse_button":
        b = normalize_key(e.get("button", ""))
        a = e.get("action")
        if a == "down":
            if b not in mouse_buttons:
                mouse_buttons.add(b)
                mb_press_order.append(b)
        elif a == "up":
            mouse_buttons.discard(b)
            if b in mb_press_order:
                mb_press_order.remove(b)
    elif t == "focus":
        if e.get("focused") is False:
            keys_down.clear()
            press_order.clear()
            mouse_buttons.clear()
            mb_press_order.clear()


def write_frames_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ------------------------- session.json update ------------------------------ #

def parse_iso_utc(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def format_iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = dt.astimezone(timezone.utc).isoformat()
    # Match original format: 2026-05-27T13:12:23.761548Z
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s


def update_session_json(orig: dict, new_video_path: Path,
                        new_frame_count: int, new_duration_s: float,
                        events_by_type: dict[str, int]) -> dict:
    out = json.loads(json.dumps(orig))  # deep copy

    canon = out.get("canonical", {})
    canon["video_duration_s"] = round(new_duration_s, 6)
    canon["frame_count"] = new_frame_count
    canon["video_fps"] = round(new_frame_count / new_duration_s, 6) if new_duration_s > 0 else 0
    out["canonical"] = canon

    hcm = out.get("humyncapture_metadata", {})
    rec = hcm.get("recording", {})
    if "started_at_utc" in rec:
        rec["started_at_utc"] = format_iso_utc(
            parse_iso_utc(rec["started_at_utc"]) + timedelta(seconds=TRIM_HEAD_S)
        )
    if "ended_at_utc" in rec:
        rec["ended_at_utc"] = format_iso_utc(
            parse_iso_utc(rec["ended_at_utc"]) - timedelta(seconds=TRIM_TAIL_S)
        )
    if "duration_seconds" in rec:
        rec["duration_seconds"] = round(
            rec["duration_seconds"] - TRIM_HEAD_S - TRIM_TAIL_S, 6
        )
    if "anchor_monotonic_us" in rec:
        rec["anchor_monotonic_us"] = int(rec["anchor_monotonic_us"]) + int(TRIM_HEAD_S * 1_000_000)
    hcm["recording"] = rec

    vid = hcm.get("video", {})
    vid["size_bytes"] = os.path.getsize(new_video_path)
    vid["fps"] = canon["video_fps"]
    hcm["video"] = vid

    ic = hcm.get("input_capture", {})
    ic["events_total"] = sum(events_by_type.values())
    ic["events_by_type"] = events_by_type
    hcm["input_capture"] = ic

    out["humyncapture_metadata"] = hcm
    return out


# ------------------------- key_binding.json --------------------------------- #

def patch_key_binding(orig: dict[str, list[str]], unmapped_keys: set[str]) -> dict[str, list[str]]:
    out = dict(orig)
    for k in sorted(unmapped_keys):
        if k not in out:
            out[k] = []  # stub: no semantic action, but satisfies coverage
    return out


# ------------------------- main per-session pipeline ------------------------ #

def process_session(session_dir: Path, inputs_jsonl: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    src_video = session_dir / "video.mp4"
    src_session_json = session_dir / "session.json"
    src_key_binding = session_dir / "key_binding.json"
    src_rrd_creation = session_dir / "rrd_creation.py"

    # Probe
    streams = probe_video_streams(src_video)
    fmt = probe_format(src_video)
    audio = has_audio_stream(streams)
    original_duration_s = float(fmt["duration"])
    pts, is_key = probe_video_frame_pts(src_video)

    if original_duration_s <= TRIM_HEAD_S + TRIM_TAIL_S + 70.0:
        # Spec requires >=70s after trim
        raise ValueError(
            f"Video too short after trim: original={original_duration_s}s"
        )

    start_s = TRIM_HEAD_S
    end_s = original_duration_s - TRIM_TAIL_S

    # Keep frames whose ORIGINAL pts is in [start_s, end_s)
    kept_pts = [t for t in pts if start_s <= t < end_s]
    if not kept_pts:
        raise ValueError("no frames fall in trimmed window")

    # 1) Trim the video
    new_video = output_dir / "video.mp4"
    smart_cut(src_video, new_video, start_s, end_s, pts, is_key, audio)

    # Probe the OUTPUT video — its real PTS list is what frames.csv must align
    # to (smart-cut concat can introduce a 1-2 frame boundary drift vs. the
    # original PTS slice).
    new_pts_s, _ = probe_video_frame_pts(new_video)
    new_fmt = probe_format(new_video)
    new_duration_s = float(new_fmt["duration"])

    # 2) Filter & shift inputs.jsonl
    raw_events = load_inputs(inputs_jsonl)
    start_us = int(start_s * 1_000_000)
    end_us = int(end_s * 1_000_000)
    shifted_events = filter_and_shift_inputs(raw_events, start_us, end_us)
    pre_window = select_pre_window_events(raw_events, start_us)
    # Normalize keys/buttons in pre-window events too
    for e in pre_window:
        if e.get("type") == "key" and "key" in e:
            e["key"] = normalize_key(e["key"])
        elif e.get("type") == "mouse_button" and "button" in e:
            e["button"] = normalize_key(e["button"])

    # 3) Rebuild frames.csv using NEW video PTS (already in new clock)
    with src_key_binding.open() as f:
        key_binding = json.load(f)
    rows, unmapped = rebuild_frames_csv(new_pts_s, 0.0, shifted_events, pre_window, key_binding)
    write_frames_csv(output_dir / "frames.csv", rows)

    # 4) Patch key_binding.json for coverage
    new_key_binding = patch_key_binding(key_binding, unmapped)
    (output_dir / "key_binding.json").write_text(
        json.dumps(new_key_binding, indent=2, sort_keys=True) + "\n"
    )

    # 5) Update session.json
    with src_session_json.open() as f:
        orig_session = json.load(f)
    events_by_type: dict[str, int] = {}
    for e in shifted_events:
        t = e.get("type", "unknown")
        events_by_type[t] = events_by_type.get(t, 0) + 1
    new_session = update_session_json(
        orig_session, new_video, new_frame_count=len(rows),
        new_duration_s=new_duration_s, events_by_type=events_by_type,
    )
    (output_dir / "session.json").write_text(
        json.dumps(new_session, indent=2) + "\n"
    )

    # 6) Write trimmed inputs.jsonl
    with (output_dir / "inputs.jsonl").open("w") as f:
        for e in shifted_events:
            f.write(json.dumps(e) + "\n")

    # 7) Copy rrd_creation.py unchanged (rrd regeneration runs separately)
    shutil.copy2(src_rrd_creation, output_dir / "rrd_creation.py")

    return {
        "session_id": session_dir.name,
        "original_duration_s": original_duration_s,
        "new_duration_s": new_duration_s,
        "new_frame_count": len(rows),
        "events_kept": len(shifted_events),
        "unmapped_keys_added_to_binding": sorted(unmapped),
        "audio": audio,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-dir", type=Path, required=True)
    ap.add_argument("--inputs-jsonl", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    report = process_session(args.session_dir.resolve(),
                             args.inputs_jsonl.resolve(),
                             args.output_dir.resolve())
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
