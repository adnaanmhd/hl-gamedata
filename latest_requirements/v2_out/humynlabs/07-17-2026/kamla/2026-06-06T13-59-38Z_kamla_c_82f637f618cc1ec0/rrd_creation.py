"""Generates session.rrd alongside the session bundle for QA visualization
(spec §2.3). Loads frames.csv + video.mp4 from the same directory and logs both
timelines into a single rerun recording, frame-synced.

Usage:
    python rrd_creation.py --session-dir <path-to-session-dir>
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import rerun as rr


def log_session(session_dir: Path) -> Path:
    frames_csv = session_dir / "frames.csv"
    video_mp4 = session_dir / "video.mp4"
    session_json = session_dir / "session.json"
    rrd_path = session_dir / "session.rrd"

    with session_json.open() as f:
        session = json.load(f)
    canonical = session.get("canonical", {})

    rr.init(f"odyssey_{canonical.get('session_id', session_dir.name)}", spawn=False)
    rr.save(str(rrd_path))
    rr.log("video", rr.AssetVideo(path=str(video_mp4)), static=True)

    with frames_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            rr.set_time("frame", sequence=int(row["frame_id"]))
            rr.set_time("video_time", duration=1e-3 * int(row["timestamp_ms"]))
            rr.log("inputs/keys", rr.TextLog(row.get("input_keys") or "—"))
            rr.log("inputs/actions", rr.TextLog(row.get("input_actions") or "—"))
            rr.log("inputs/mouse_buttons", rr.TextLog(row.get("input_mouse_buttons") or "—"))
            rr.log("inputs/mouse_dx", rr.Scalars(float(row.get("input_mouse_dx") or 0)))
            rr.log("inputs/mouse_dy", rr.Scalars(float(row.get("input_mouse_dy") or 0)))
    return rrd_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-dir", type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()
    print(f"wrote {log_session(args.session_dir.resolve())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
