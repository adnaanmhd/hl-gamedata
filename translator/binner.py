"""Frame binner — the heart of the translator.

Turns the raw event stream (inputs.jsonl) into one row per *video* frame.

Correctness guarantees (these are the client's findings, fixed here):
  - NO off-by-one: an event at time t goes to the frame whose window CONTAINS
    it, i.e. floor(t / frame_us). Not the next frame.
  - NO frame drift: exactly `video.frame_count` rows are emitted.
  - NO video↔data drift: timestamps come from the same fps the video reports.
  - NO input bleed: simultaneous L+R modifiers are resolved (spurious side
    dropped, frame flagged).
  - Clean keys: OS/system keys + control bytes stripped (keys module).
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from . import keys as K
from .keybind import resolve_actions

C2W_COLS = [f"c2w_m{r}{c}" for r in range(4) for c in range(4)]
CAMERA_COLS = ["camera_model", "camera_fx", "camera_fy", "camera_cx", "camera_cy"]
FRAME_COLS = (
    ["frame_id", "timestamp_ms"]
    + C2W_COLS
    + CAMERA_COLS
    + ["input_keys", "input_actions", "input_mouse_buttons", "input_mouse_dx", "input_mouse_dy"]
)


@dataclass
class BinStats:
    n_frames: int = 0
    n_events: int = 0
    has_mouse_motion: bool = False
    has_mouse_buttons: bool = False
    has_keyboard: bool = False
    bleed_frames: list[int] = field(default_factory=list)
    keys_seen: set[str] = field(default_factory=set)
    actions_seen: set[str] = field(default_factory=set)
    dropped_beyond_video: int = 0
    frame_timing: str = "uniform_fps"   # "real_pts" once PTS-aware binning is used


def _resolve_bleed(keyset: set[str], bound: frozenset[str], f_idx: int,
                   stats: BinStats) -> set[str]:
    """Drop the spurious side of an L+R modifier artifact; keep the bound side."""
    out = set(keyset)
    bled = False
    for left, right in K.MODIFIER_PAIRS:
        if left in out and right in out:
            bled = True
            left_bound, right_bound = left in bound, right in bound
            if left_bound and not right_bound:
                out.discard(right)
            elif right_bound and not left_bound:
                out.discard(left)
            else:
                out.discard(right)  # default: drop the rarer right side
    if bled:
        stats.bleed_frames.append(f_idx)
    return out


def raw_int(v) -> int:
    """Coerce ONE untrusted inputs.jsonl numeric field.

    DEGRADE, never crash — the same contract the line-level parse and the
    CSV cells already honour. A bare `int(v or 0)` raised ValueError on a
    stringified number ("1.0"), a word, or a list, and TypeError on a
    container: out of check_session_v2 into the driver, which writes
    QUARANTINED "validation crashed" (TERMINAL, media held 48 h, manual
    queue) for a session that would otherwise have PASSed — and out of
    bin_session, so every retranslate fix failed too and the session was
    rejected under the bare fix-failed marker (r-loop 7). A malformed
    value contributes 0 motion, which the raw-vs-CSV comparison then sees
    as an ordinary mismatch and reports honestly. OverflowError too:
    json.loads accepts `Infinity`, `1e999` and 10**400, and int() on any
    of them raised straight past the two arms above — same crash, same
    wrongful QUARANTINED (r-loop 8; NaN already lands in ValueError)."""
    try:
        return int(float(v or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def bin_session(events: list[dict], video, keybind: dict, rules, bound: frozenset[str],
                *, aggressive: bool = True,
                frame_pts_us: list[int] | None = None) -> tuple[list[list], BinStats]:
    """Return (rows, stats). rows are lists aligned to FRAME_COLS.

    If `frame_pts_us` (real per-frame presentation timestamps, relative µs, len ==
    frame_count) is supplied, events are placed on the REAL frame timeline via
    binary search — correct even when the capture dropped frames. Otherwise we
    fall back to the legacy uniform `floor(t / frame_us)` grid.
    """
    n = video.frame_count
    frame_us = video.frame_us
    fps = video.fps
    use_pts = bool(frame_pts_us) and len(frame_pts_us) == n
    total_us = int((getattr(video, "duration_s", 0) or 0) * 1_000_000) \
        or (int(n * frame_us) if frame_us else 0)

    keys_in: list[set[str]] = [set() for _ in range(n)]
    btns_in: list[set[str]] = [set() for _ in range(n)]
    dx_sum = [0] * n
    dy_sum = [0] * n
    has_raw = False

    stats = BinStats(n_frames=n, frame_timing="real_pts" if use_pts else "uniform_fps")

    ev = [e for e in events if isinstance(e.get("t"), int)
          and e.get("type") in ("key", "mouse_button", "mouse_raw")]
    ev.sort(key=lambda e: e["t"])
    stats.n_events = len(ev)

    keys_down: set[str] = set()
    btns_down: set[str] = set()
    cur = 0

    def flush(target: int):
        nonlocal cur
        while cur < target and cur < n:
            keys_in[cur] |= keys_down
            btns_in[cur] |= btns_down
            cur += 1

    for e in ev:
        t = e["t"]
        if total_us and t >= total_us:       # event past the end of the video
            stats.dropped_beyond_video += 1
            continue
        if use_pts:
            # frame actually on screen at time t = last frame whose PTS <= t
            f = bisect_right(frame_pts_us, t) - 1
            if f < 0:
                f = 0
        else:
            f = int(t // frame_us)           # floor → window-containing frame
            if f >= n:
                stats.dropped_beyond_video += 1
                continue
            if f < 0:
                f = 0
        flush(f)
        et = e["type"]
        if et == "key":
            k = K.normalize_event_key(e.get("key"), bound=bound, aggressive=aggressive)
            if not k:
                continue
            keys_in[f].add(k)
            if e.get("action") == "down":
                keys_down.add(k)
            else:
                keys_down.discard(k)
        elif et == "mouse_button":
            btn = e.get("button")
            b = K.MOUSE_BUTTONS.get(btn.strip().lower()) \
                if isinstance(btn, str) else None
            if not b:
                continue
            btns_in[f].add(b)
            if e.get("action") == "down":
                btns_down.add(b)
            else:
                btns_down.discard(b)
        else:  # mouse_raw
            has_raw = True
            dx_sum[f] += raw_int(e.get("dx"))
            dy_sum[f] += raw_int(e.get("dy"))

    flush(n)
    stats.has_mouse_motion = has_raw and any(dx_sum[i] or dy_sum[i] for i in range(n))
    stats.has_mouse_buttons = any(btns_in[i] for i in range(n))

    empty_camera = [""] * (len(C2W_COLS) + len(CAMERA_COLS))
    rows: list[list] = []
    for f in range(n):
        kset = _resolve_bleed(keys_in[f], bound, f, stats)
        keys = sorted(kset)
        btns = sorted(btns_in[f])
        # per-axis: a dx-only frame must not fire the look-Y bind (and v.v.)
        motion = (bool(dx_sum[f]), bool(dy_sum[f]))
        actions = (resolve_actions(kset | set(btns), motion, rules)[0]
                   if rules else [])
        stats.keys_seen.update(keys)
        stats.actions_seen.update(actions)
        if use_pts:
            ts_ms = int(round(frame_pts_us[f] / 1000.0))   # real presentation time
        else:
            ts_ms = int(round(f * 1000.0 / fps)) if fps else 0
        if has_raw:
            dx_val, dy_val = dx_sum[f], dy_sum[f]
        else:
            dx_val, dy_val = "", ""   # no mouse capture in this session → blank, not 0
        rows.append(
            [f, ts_ms] + empty_camera
            + ["|".join(keys), "|".join(actions), "|".join(btns), dx_val, dy_val]
        )
    stats.has_keyboard = bool(stats.keys_seen)
    return rows, stats
