"""Lossless head/tail trim.

Project rule (implicit for every NEW sample): drop the first and last 5 s of
the video — that's where app-toggling / loading / menus live — and re-sync all
the other files to the trimmed video.

"Lossless" means stream-copy (no re-encode): bit-identical video, all streams
and metadata intact. A stream copy can only start on a keyframe, so the cut
snaps to the nearest keyframe to 5 s. We return the ACTUAL cut points so the
binner can rebase events/timestamps to the trimmed video exactly — zero drift.
"""
from __future__ import annotations

import subprocess
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path

from . import video as V

HEAD_S = 5.0
TAIL_S = 5.0
MIN_CLIP_S = 70.0


@dataclass
class TrimResult:
    out_path: Path
    head_cut_s: float        # source time that becomes new frame 0
    end_cut_s: float         # source time of the (exclusive) end
    new_duration_s: float
    warnings: list[str]


def plan_cuts(keyframes: list[float], duration: float,
              head_s: float = HEAD_S, tail_s: float = TAIL_S) -> tuple[float, float]:
    """Pick lossless cut points for a ~5 s head/tail trim.

    The START of a stream copy must land on a keyframe (else the leading GOP is
    undecodable), so the head snaps to the first keyframe at/after 5 s. The END
    (`-to`) needs no keyframe — packets are copied up to it — so the tail is the
    plain `duration - 5 s`.
    """
    target_end = max(head_s, duration - tail_s)
    if not keyframes:
        return head_s, target_end
    i = bisect_left(keyframes, head_s)
    head_cut = keyframes[i] if i < len(keyframes) else keyframes[-1]
    if head_cut >= target_end:           # pathological: too short to trim cleanly
        head_cut = head_s
    return head_cut, target_end


def trim(src: Path, out_path: Path, *, info: V.VideoInfo | None = None) -> TrimResult:
    """Stream-copy `src` minus ~5 s head/tail into `out_path`."""
    info = info or V.probe(src)
    kf = V.keyframe_times(src)
    head_cut, end_cut = plan_cuts(kf, info.duration_s)
    warnings: list[str] = []
    new_dur = end_cut - head_cut
    if new_dur < MIN_CLIP_S:
        warnings.append(
            f"trimmed clip is {new_dur:.1f}s (< {MIN_CLIP_S:.0f}s minimum)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{head_cut:.6f}", "-to", f"{end_cut:.6f}",
        "-i", str(src),
        "-map", "0",            # keep ALL streams (video/audio/data/metadata)
        "-c", "copy",           # no re-encode → lossless
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    # Re-probe the real trimmed duration (keyframe snap may differ slightly).
    out_info = V.probe(out_path)
    return TrimResult(out_path, head_cut, end_cut, out_info.duration_s, warnings)


def rebase_events(events: list[dict], head_cut_s: float, new_duration_s: float) -> list[dict]:
    """Shift event timestamps to the trimmed timeline; drop events outside it.

    Events are in microseconds from the original recording start (= original
    video frame 0). After trimming, new frame 0 = head_cut_s.
    """
    head_us = head_cut_s * 1_000_000.0
    end_us = (head_cut_s + new_duration_s) * 1_000_000.0
    out: list[dict] = []
    for e in events:
        t = e.get("t")
        if not isinstance(t, int):
            continue
        if head_us <= t < end_us:
            ne = dict(e)
            ne["t"] = int(t - head_us)
            out.append(ne)
    return out
