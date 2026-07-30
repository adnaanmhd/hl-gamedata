"""Generate session.rrd + rrd_creation.py (spec §2.3 — 15% QA visualization).

We write the exact script used to produce the .rrd into the session folder
(as the spec requires) and then run it, so the two always agree.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RRD_SCRIPT = '''"""Generates session.rrd alongside the session bundle for QA visualization
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
'''


def write_script(session_dir: Path) -> Path:
    p = session_dir / "rrd_creation.py"
    # encoding="utf-8" is required, not cosmetic: RRD_SCRIPT contains "§"
    # and "—". Path.write_text() with no encoding uses the platform's
    # default (e.g. cp1252 on Windows), which CAN represent those two
    # characters as single bytes — but as bytes that are invalid UTF-8, so
    # anything reading the file back as UTF-8 (any editor/tool not also
    # defaulting to cp1252) sees "�" mojibake instead. Confirmed on a
    # real Windows delivery.
    p.write_text(RRD_SCRIPT, encoding="utf-8")
    return p


def generate(session_dir: Path, *, python: str | None = None,
             in_process: bool = False) -> Path:
    """Write rrd_creation.py and run it to produce session.rrd.

    `in_process=True` imports and calls the written script's `log_session()`
    directly instead of spawning `[python, script, ...]` as a subprocess.
    Required inside a frozen PyInstaller executable: `sys.executable` there
    points at the frozen exe itself, not a Python interpreter, so shelling
    out to "run" the script actually tries to relaunch the whole packaged
    app — session.rrd never gets produced (confirmed on a real delivery:
    rrd_creation.py was written, session.rrd was not). `python=` is ignored
    when `in_process=True`.
    """
    session_dir = Path(session_dir)
    script = write_script(session_dir)
    if in_process:
        import importlib.util
        spec = importlib.util.spec_from_file_location("rrd_creation", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.log_session(session_dir)
    else:
        subprocess.run(
            [python or sys.executable, str(script), "--session-dir", str(session_dir)],
            check=True,
        )
    return session_dir / "session.rrd"
