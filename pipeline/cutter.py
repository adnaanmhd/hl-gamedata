"""FIX_CUT_SEGMENTS — split one session into spec-valid segments (plan §11).

Per keep-window: lossless video cut (start snaps FORWARD to the next
keyframe — a stream copy can only start there; end snaps back to a frame
boundary via `-to`), slice frames.csv to the cut's actual frame range,
re-zero frame_id, rebase timestamp_ms to the segment's own real PTS,
recompute session.json (`session_id` + `-pN`, created/ended = original +
window offset, durations, frame_count, fps = frames/duration). Segments
under the 70 s bar are dropped (§5); each survivor re-enters Phase II
independently (R15/F2 — splitting one recording into several delivered
sessions is allowed).
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path

from translator import rrd as rrdmod
from translator import video as V
from translator.v2 import V2_FRAME_COLS

from . import config as C


class CutError(Exception):
    pass


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def complement_windows(cut: list[tuple[float, float]],
                       duration_s: float) -> list[tuple[float, float]]:
    """Keep-windows = clip minus the union of cut windows."""
    merged: list[list[float]] = []
    for a, b in sorted((max(a, 0.0), min(b, duration_s)) for a, b in cut):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    keep: list[tuple[float, float]] = []
    prev = 0.0
    for a, b in merged:
        if a > prev:
            keep.append((prev, a))
        prev = max(prev, b)
    if prev < duration_s:
        keep.append((prev, duration_s))
    return keep


def cut_segments(session_dir: Path, keep: list[tuple[float, float]],
                 out_root: Path) -> dict:
    """Cut `session_dir` into segments for the given keep-windows.

    Returns {"segments": [{id, dir, t0, t1, duration_s, frames}],
             "dropped": [{t0, t1, why}]} — segments land in
    out_root/<sid>-pN/ as 5-file v2 sessions (stub rrd, real script)."""
    session_dir = Path(session_dir)
    out_root = Path(out_root)
    src_video = session_dir / "video.mp4"
    s = json.loads((session_dir / "session.json").read_text())
    sid = s["session_id"]
    created = datetime.fromisoformat(
        s["created_at_utc"].replace("Z", "+00:00"))
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)   # naive == UTC

    src_pts = V.frame_pts(src_video)             # µs, first frame == 0
    if not src_pts:
        raise CutError("source PTS unreadable — cannot cut losslessly")
    keyframes = V.keyframe_times(src_video)      # seconds
    if not keyframes:
        raise CutError("no keyframes readable")
    # frame_pts normalizes to first frame; keyframe times are absolute —
    # rebase them the same way so both live on the same clock
    first_abs = _first_pts_abs(src_video)
    kf_rel = [k - first_abs for k in keyframes]

    with (session_dir / "frames.csv").open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    assert header == V2_FRAME_COLS, "cutter needs a v2 frames.csv"

    segments = []
    dropped = []
    n = 0
    for (t0, t1) in keep:
        n += 1
        seg_id = f"{sid}-p{n}"
        i = bisect_left(kf_rel, t0 - 1e-6)
        if i >= len(kf_rel):
            dropped.append({"t0": t0, "t1": t1,
                            "why": "no keyframe at/after window start"})
            continue
        start = kf_rel[i]
        if t1 - start < C.MIN_CLIP_S:
            dropped.append(
                {"t0": t0, "t1": t1,
                 "why": f"segment {t1 - start:.1f}s after keyframe snap "
                        f"(< {C.MIN_CLIP_S:.0f}s minimum)"})
            continue
        out_dir = out_root / seg_id
        out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-ss", f"{start + first_abs:.6f}", "-to",
             f"{t1 + first_abs:.6f}",
             "-i", str(src_video), "-map", "0", "-c", "copy",
             "-avoid_negative_ts", "make_zero",
             str(out_dir / "video.mp4")], check=True)
        info = V.probe(out_dir / "video.mp4")
        seg_pts = V.frame_pts(out_dir / "video.mp4")
        m = info.frame_count
        if not seg_pts or len(seg_pts) != m:
            raise CutError(f"{seg_id}: segment PTS unreadable")

        # source frame index of the segment's first frame
        i0 = bisect_left(src_pts, round(start * 1e6) - 500)
        if i0 >= len(src_pts) or \
                abs(src_pts[i0] - start * 1e6) > 0.6 * 1e6 / max(info.fps, 1):
            raise CutError(f"{seg_id}: cut start {start:.3f}s does not land "
                           f"on a source frame")
        if i0 + m > len(rows):
            raise CutError(f"{seg_id}: segment frames {m} overrun CSV rows")
        kept = [list(r) for r in rows[i0:i0 + m]]
        offset_ms = int(kept[0][1])
        for j, r in enumerate(kept):
            r[0] = str(j)
            r[1] = str(int(round(seg_pts[j] / 1000.0)))  # segment's OWN PTS

        seg_created = created + timedelta(milliseconds=offset_ms)
        seg_ended = seg_created + timedelta(seconds=info.duration_s)
        seg_s = dict(s)
        seg_s.update(
            session_id=seg_id,
            created_at_utc=_iso(seg_created), ended_at_utc=_iso(seg_ended),
            duration_ms=round(info.duration_s * 1000.0),
            duration_seconds=round(info.duration_s, 3),
            fps=info.fps, frame_count=m)
        with (out_dir / "frames.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(V2_FRAME_COLS)
            w.writerows(kept)
        (out_dir / "session.json").write_text(json.dumps(seg_s, indent=2))
        rrdmod.write_script(out_dir)
        (out_dir / "session.rrd").touch()        # stub — packaging regenerates
        # children inherit the raw sidecars (R3): a segment can still take
        # the RETRANSLATE path — its created_at encodes the source offset
        if (session_dir / "raw").is_dir():
            shutil.copytree(session_dir / "raw", out_dir / "raw",
                            dirs_exist_ok=True)

        segments.append({"id": seg_id, "dir": str(out_dir),
                         "t0": round(start, 3), "t1": round(t1, 3),
                         "duration_s": round(info.duration_s, 3),
                         "frames": m})
    return {"segments": segments, "dropped": dropped}


def _first_pts_abs(video: Path) -> float:
    """Absolute pts_time of the first video packet (frame_pts subtracts it)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time", "-of", "csv=p=0",
         "-read_intervals", "%+#1", str(video)],
        capture_output=True, text=True, timeout=120)
    for line in out.stdout.splitlines():
        line = line.strip().rstrip(",")
        if line:
            try:
                return float(line)
            except ValueError:
                pass
    return 0.0
