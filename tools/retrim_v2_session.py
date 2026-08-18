"""Head-trim a DELIVERED v2 session in place (lossless, keyframe-snapped).

For cutting non-gameplay intros (menus/loading/cutscene) out of an
already-delivered session when the raw bundle no longer exists. Stream-copies
video from the first keyframe >= --head-s, drops the matching frames.csv rows,
rebases frame_id/timestamp_ms, updates session.json (created_at_utc,
duration_*, frame_count, fps) and regenerates session.rrd.

Usage:
  PYTHONPATH=. uv run --with rerun-sdk python tools/retrim_v2_session.py \
      <session_dir> --head-s <seconds> [--out <dir>]     (default: in place)
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translator import rrd                    # noqa: E402
from translator import video as V             # noqa: E402
from translator.trim import plan_cuts         # noqa: E402
from translator.v2 import V2_FRAME_COLS       # noqa: E402


def retrim(session_dir: Path, head_s: float, out_dir: Path) -> dict:
    session_dir = Path(session_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = session_dir / "video.mp4"
    info = V.probe(src)
    kfs = V.keyframe_times(src)
    head_cut, _ = plan_cuts(kfs, info.duration_s, head_s=head_s, tail_s=0.0)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False,
                                     dir=out_dir) as tf:
        tmp = Path(tf.name)
    # TIMEOUT-BOUNDED (r-loop 4). apply_fixes runs in the continuous
    # driver's session-runner THREAD, so a wedged ffmpeg pins a gate slot
    # and leaves the sid owned forever — pool capacity permanently down by
    # one, the row stuck FIXING, and on the batch rollback path it holds
    # run.lock so every later tick exits immediately with the unit in
    # 'activating' (TimeoutStartSec=infinity), meaning OnFailure never
    # fires and nothing alerts. Design §12 promises every ffmpeg/ffprobe
    # call in the fix path is bounded; this helper was the one the r-loop-2
    # sweep missed. TimeoutExpired surfaces through apply_fixes as an
    # ordinary fix failure (REVALIDATING).
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", f"{head_cut:.6f}", "-i",
         str(src), "-c", "copy", "-avoid_negative_ts", "make_zero", "-y",
         str(tmp)], check=True, timeout=1800)
    new_info = V.probe(tmp)

    with (session_dir / "frames.csv").open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    assert header == V2_FRAME_COLS
    i0 = len(rows) - new_info.frame_count
    kept = rows[i0:]
    base_ms = int(kept[0][1])
    for i, r in enumerate(kept):
        r[0] = str(i)
        r[1] = str(int(r[1]) - base_ms)

    # Anchor created_at on the EXACT source PTS of the first retained
    # frame, never the millisecond-rounded CSV cell (r-loop 5 blocker).
    # This is the same bug r-loop 4 fixed in pipeline/cutter.py:192 and
    # the identical math was left live here. translator/v2.py:846
    # recovers head_us = (created - started) from this field and re-bins
    # every raw mouse event against it, so a <=500us rounding error is a
    # broad systematic band, not isolated jitter: it flips every event
    # landing within half a millisecond of a frame boundary, _verify_-
    # against_raw FAILs, and validate.py maps that to SYN_TS_NOT_PTS --
    # spending a fix attempt and a paid Gemini sweep on a session with
    # nothing wrong with it. Zero error only when the retained frame's
    # PTS is a whole millisecond (1 frame in 3 on a nominal 30fps grid).
    # base_ms still drives the CSV rebase: its <=1ms error is far inside
    # the 100ms FRAME_SYNC_MS bar. If the PTS list is unreadable we fall
    # back to it here too -- no worse than the previous behaviour, and a
    # hard raise (cutter's choice) would instead burn a fix attempt on
    # the no-raw sessions where the rounding is harmless.
    src_pts = V.frame_pts(src)
    head_us = src_pts[i0] if 0 <= i0 < len(src_pts) else base_ms * 1000

    s = json.loads((session_dir / "session.json").read_text())
    created = datetime.fromisoformat(s["created_at_utc"].replace("Z", "+00:00"))
    created += timedelta(microseconds=head_us)
    ended = created + timedelta(seconds=new_info.duration_s)
    iso = lambda d: d.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f") + "Z"
    s.update(created_at_utc=iso(created), ended_at_utc=iso(ended),
             duration_ms=round(new_info.duration_s * 1000.0),
             duration_seconds=round(new_info.duration_s, 3),
             fps=new_info.fps, frame_count=new_info.frame_count)

    shutil.move(str(tmp), out_dir / "video.mp4")
    with (out_dir / "frames.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(V2_FRAME_COLS)
        w.writerows(kept)
    (out_dir / "session.json").write_text(json.dumps(s, indent=2))
    if session_dir != out_dir:
        shutil.copy2(session_dir / "rrd_creation.py", out_dir / "rrd_creation.py")
    rrd.generate(out_dir, timeout_s=1800)     # bounded, as deliver.py does
    return {"session": s["session_id"], "head_cut_s": round(head_cut, 3),
            "cut_rows": len(rows) - len(kept), "frames": len(kept),
            "duration_s": s["duration_seconds"], "out_dir": str(out_dir)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=Path)
    ap.add_argument("--head-s", type=float, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    res = retrim(args.session, args.head_s, args.out or args.session)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
