"""Generates session.rrd alongside the session bundle for QA visualization
(spec §2.3). Loads frames.csv + video.mp4 from the same directory and logs
both timelines into a single rerun recording.

Usage:
    python rrd_creation.py --session-dir <path-to-session-dir>

The script is intentionally self-contained so it can run directly on the
delivery filesystem.
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
    fps = float(canonical.get("video_fps") or 30.0)

    rr.init(f"odyssey_{canonical.get('session_id', session_dir.name)}", spawn=False)
    rr.save(str(rrd_path))

    # Asset video — stored once, then we step the timeline frame-by-frame.
    rr.log("video", rr.AssetVideo(path=str(video_mp4)), static=True)

    with frames_csv.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            frame_id = int(row["frame_id"])
            ts_ms = int(row["timestamp_ms"])
            rr.set_time("frame", sequence=frame_id)
            rr.set_time("video_time", duration=1e-3 * ts_ms)

            keys = row.get("input_keys", "")
            actions = row.get("input_actions", "")
            buttons = row.get("input_mouse_buttons", "")
            dx = float(row.get("input_mouse_dx", 0) or 0)
            dy = float(row.get("input_mouse_dy", 0) or 0)

            # Log as text fields; rerun shows these on the right-hand pane,
            # frame-synced with the video.
            rr.log("inputs/keys", rr.TextLog(keys or "—"))
            rr.log("inputs/actions", rr.TextLog(actions or "—"))
            rr.log("inputs/mouse_buttons", rr.TextLog(buttons or "—"))
            rr.log("inputs/mouse_dx", rr.Scalars(dx))
            rr.log("inputs/mouse_dy", rr.Scalars(dy))

    return rrd_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-dir", type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()
    out = log_session(args.session_dir.resolve())
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
