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
    # Both times are on the SOURCE-RELATIVE clock (first frame == 0), the
    # same clock `V.frame_pts` and the raw event stream use. They are NOT
    # container timestamps — ffmpeg gets `+ first_pts_abs` added back at
    # the call site, exactly as cutter.py does.
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


def trim(src: Path, out_path: Path, *, info: V.VideoInfo | None = None,
         head_s: float = HEAD_S, tail_s: float = TAIL_S) -> TrimResult:
    """Stream-copy `src` minus a head/tail (default ~5 s each) into `out_path`."""
    info = info or V.probe(src)
    # keyframe_times is on the ABSOLUTE container clock; frame_pts, the raw
    # event stream and `head_s` are all source-RELATIVE. Rebase before
    # planning, exactly as cutter.py does — otherwise `head_cut_s` comes
    # back as a container timestamp and rebase_events shifts every event by
    # the container's start_time too much. Real captures carry ~0.035-0.048 s
    # there, so every delivered frames.csv was misplaced by 1-1.5 frames:
    # invisible to qa-v2 (build_session_json advances created_at_utc by the
    # SAME wrong number, so the two agree with each other) and invisible to
    # the sync checker (35 ms sits under the 50 ms target). Measured on a
    # real capture: reported 8.368034 s, actual content offset 8.333333 s,
    # delta +1.041 frames (r-loop 6).
    first_abs = V.first_pts_abs(src)
    kf = [k - first_abs for k in V.keyframe_times(src)]
    head_cut, end_cut = plan_cuts(kf, info.duration_s, head_s=head_s, tail_s=tail_s)
    warnings: list[str] = []
    new_dur = end_cut - head_cut
    if new_dur < MIN_CLIP_S:
        warnings.append(
            f"trimmed clip is {new_dur:.1f}s (< {MIN_CLIP_S:.0f}s minimum)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        # ffmpeg wants the ABSOLUTE container timestamp back (measured: an
        # `-ss` of 8.368034 on a file whose first packet is at 0.034701
        # lands on the frame at relative 8.333333)
        "-ss", f"{head_cut + first_abs:.6f}",
        "-to", f"{end_cut + first_abs:.6f}",
        "-i", str(src),
        "-map", "0",            # keep ALL streams (video/audio/data/metadata)
        "-c", "copy",           # no re-encode → lossless
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        str(out_path),
    ]
    # timeout like every other ffmpeg/ffprobe call in the package: this one
    # is reachable from the ARR_RAW_ONLY fix path, which runs in the
    # continuous driver's session-runner THREAD (not the bounded validation
    # subprocess), so a wedged ffmpeg would pin a runner slot forever
    # (r-loop 2 — the §12 sweep missed trim.py)
    subprocess.run(cmd, check=True, timeout=1800)
    # Re-probe the real trimmed duration (keyframe snap may differ slightly).
    out_info = V.probe(out_path)
    return TrimResult(out_path, head_cut, end_cut, out_info.duration_s, warnings)


def rebase_events(events: list[dict], head_cut_s: float, new_duration_s: float,
                  *, carried_out: list | None = None) -> list[dict]:
    """Shift event timestamps to the trimmed timeline; drop events outside it.

    Events are in microseconds from the original recording start (= original
    video frame 0). After trimming, new frame 0 = head_cut_s.

    `carried_out`, when given, also receives the synthetic t=0 re-presses so
    a caller can tell carried holds from in-band events: with bogus stamps
    (head beyond the whole recording) the carries are the ONLY survivors,
    and the retranslate guard must refuse rather than fabricate a full-clip
    hold (r-loop 9). Return value unchanged.
    """
    head_us = head_cut_s * 1_000_000.0
    end_us = (head_cut_s + new_duration_s) * 1_000_000.0
    # CARRY THE HELD STATE ACROSS THE CUT (r-loop 4). Dropping every event
    # before head_us loses the fact that a key/button was already DOWN when
    # the trim starts. bin_session seeds keys_down empty and only learns a
    # key from a 'down' it sees, while the surviving 'up' still does
    # keys_in[f].add(k) before discarding — so a W held from 4.5s to 14.0s
    # with the implicit 5s head trim shipped as ONE frame carrying 'w' (at
    # the release) instead of ~270 frames of hold. Nothing downstream can
    # see it: qa-v2 has no held-run check and the sync grounding correlates
    # mouse motion only. Worse, the resulting long empty stretch can read
    # as player inactivity to the AFK detector.
    held_keys: dict = {}
    held_btns: dict = {}
    out: list[dict] = []
    for e in events:
        t = e.get("t")
        if not isinstance(t, int):
            continue
        if t < head_us:
            et, act = e.get("type"), e.get("action")
            # str identities only: a container key/button (a list or dict
            # from a hand-edited/foreign-tool sidecar) is unhashable, so
            # using it as a dict key raised TypeError and crashed the WHOLE
            # translate/retranslate whenever one such event fell before the
            # head cut (r-loop 8). Nothing is lost — normalize_event_key /
            # the binner drop every non-str identity two steps later anyway.
            if et == "key":
                k = e.get("key")
                if isinstance(k, str):
                    if act == "down":
                        held_keys[k] = e
                    else:
                        held_keys.pop(k, None)
            elif et == "mouse_button":
                b = e.get("button")
                if isinstance(b, str):
                    if act == "down":
                        held_btns[b] = e
                    else:
                        held_btns.pop(b, None)
            continue
        if t < end_us:
            ne = dict(e)
            ne["t"] = int(t - head_us)
            out.append(ne)
    # re-press whatever was still held at the cut, at the new frame 0
    carried = []
    for e in list(held_keys.values()) + list(held_btns.values()):
        ne = dict(e)
        ne["t"] = 0
        ne["action"] = "down"
        carried.append(ne)
    if carried_out is not None:
        carried_out.extend(carried)
    return carried + out
