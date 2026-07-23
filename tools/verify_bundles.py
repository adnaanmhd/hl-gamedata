"""Self-check the trimmed bundles against the spec + Slack feedback.

Checks per bundle:
- frames.csv row count == ffprobe nb_frames of video.mp4
- frame_id is 0..N-1, monotonic, no gaps
- timestamp_ms is monotonically non-decreasing and starts at 0
- new video duration is within ~50ms of (original - 10s)  (smart-cut boundary tolerance)
- every literal input_keys / input_mouse_buttons that appears in frames.csv exists in key_binding.json
- no semantic input bleed: no row has two literal keys mapping to the same semantic action
- session.json's canonical.frame_count == CSV row count
- session.json's canonical.video_duration_s matches ffprobe duration
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def ffprobe_nb_frames_and_duration(video: Path) -> tuple[int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    return int(v["nb_frames"]), float(data["format"]["duration"])


def check_bundle(bundle: Path, original_duration_s: float) -> list[str]:
    errs: list[str] = []
    csv_path = bundle / "frames.csv"
    video = bundle / "video.mp4"
    session_json = bundle / "session.json"
    key_binding_path = bundle / "key_binding.json"

    for p in [csv_path, video, session_json, key_binding_path,
              bundle / "inputs.jsonl", bundle / "rrd_creation.py"]:
        if not p.exists():
            errs.append(f"missing: {p.name}")

    nb, dur = ffprobe_nb_frames_and_duration(video)
    rows = list(csv.DictReader(csv_path.open(newline="")))
    n_rows = len(rows)

    if n_rows != nb:
        errs.append(f"row count {n_rows} != video nb_frames {nb}")

    expected_dur = original_duration_s - 10.0
    if abs(dur - expected_dur) > 0.05:
        errs.append(f"video duration {dur:.3f}s deviates from expected {expected_dur:.3f}s")

    # frame_id monotonicity
    prev_id = -1
    prev_ts = -1
    for r in rows:
        fid = int(r["frame_id"])
        if fid != prev_id + 1:
            errs.append(f"frame_id gap at row {fid}")
            break
        prev_id = fid
        ts = int(r["timestamp_ms"])
        if ts < prev_ts:
            errs.append(f"timestamp regress at frame_id={fid}: {ts} < {prev_ts}")
            break
        prev_ts = ts
    if rows:
        if int(rows[0]["timestamp_ms"]) != 0:
            errs.append(f"first timestamp_ms != 0 (got {rows[0]['timestamp_ms']})")

    # key coverage + bleed
    with key_binding_path.open() as f:
        kb = json.load(f)
    for r in rows:
        for k in r["input_keys"].split("|") if r["input_keys"] else []:
            if k not in kb:
                errs.append(f"key '{k}' missing from key_binding.json (frame_id={r['frame_id']})")
                break
        for b in r["input_mouse_buttons"].split("|") if r["input_mouse_buttons"] else []:
            if b not in kb:
                errs.append(f"mouse button '{b}' missing from key_binding.json (frame_id={r['frame_id']})")
                break

    # semantic bleed: any frame where two literal keys produce the same action
    for r in rows:
        keys = r["input_keys"].split("|") if r["input_keys"] else []
        seen: dict[str, str] = {}
        for k in keys:
            for a in kb.get(k, []):
                if a in seen and seen[a] != k:
                    errs.append(
                        f"input bleed: keys '{seen[a]}' and '{k}' both map to '{a}' at frame_id={r['frame_id']}"
                    )
                    break
                seen[a] = k

    # session.json consistency
    with session_json.open() as f:
        sj = json.load(f)
    canon = sj.get("canonical", {})
    if canon.get("frame_count") != n_rows:
        errs.append(f"session.json frame_count {canon.get('frame_count')} != CSV rows {n_rows}")
    if abs(float(canon.get("video_duration_s", 0)) - dur) > 0.01:
        errs.append(
            f"session.json video_duration_s {canon.get('video_duration_s')} != ffprobe {dur}"
        )

    return errs


# Map output bundle -> original source bundle so we can verify duration delta
SESSIONS = [
    ("kamla", "2026-05-27T13-12-20Z_kamla_c_c944bee0e87b2625"),
    ("kamla", "2026-05-27T13-18-20Z_kamla_c_c944bee0e87b2625"),
    ("kamla", "2026-05-27T13-44-46Z_kamla_c_c944bee0e87b2625"),
    ("outer_wilds", "2026-05-27T12-55-40Z_outer_wilds_c_e7c7aa4d6e4b6618"),
    ("outer_wilds", "2026-05-27T13-08-32Z_outer_wilds_c_e7c7aa4d6e4b6618"),
    ("outer_wilds", "2026-05-27T13-21-33Z_outer_wilds_c_e7c7aa4d6e4b6618"),
]


def main() -> int:
    total_errs = 0
    for game, sid in SESSIONS:
        bundle = ROOT / "out" / game / "05-28-26" / sid
        src = ROOT / "humynlabs" / game / "05-28-26" / sid
        src_video = src / "video.mp4"
        _, orig_dur = ffprobe_nb_frames_and_duration(src_video)
        errs = check_bundle(bundle, orig_dur)
        if errs:
            print(f"FAIL {game}/{sid}")
            for e in errs:
                print(f"  - {e}")
            total_errs += len(errs)
        else:
            print(f"OK   {game}/{sid}")
    print(f"\nTotal errors: {total_errs}")
    return 0 if total_errs == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
