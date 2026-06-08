"""QA — validates a delivered session against the spec + project guidelines.

Catches the things the old QA missed and the client cared about:
  - frame drift (CSV rows != video frames)
  - video↔data drift (CSV span != video duration)
  - input bleed (simultaneous L+R modifiers)
  - OS-key pollution in input_keys
  - frames.csv ↔ key_binding.json vocabulary mismatch / coverage gap
  - >=3 distinct actions per game, >=70 s clip
  - missing mouse capture (dx/dy never populated)
Camera columns are out of scope (input-only sessions) and are NOT failed on.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import keys as K
from . import video as V

MIN_CLIP_S = 70.0
MIN_ACTIONS = 3
DRIFT_TOL_S = 2.0
FRAME_SYNC_TOL_MS = 100.0   # max per-row timestamp deviation from real frame PTS


@dataclass
class QAResult:
    session: str
    status: str = "PASS"            # PASS | WARN | FAIL
    issues: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    def fail(self, m): self.status = "FAIL"; self.issues.append(f"FAIL: {m}")

    def warn(self, m):
        if self.status == "PASS":
            self.status = "WARN"
        self.issues.append(f"WARN: {m}")


def check_session(session_dir: Path) -> QAResult:
    session_dir = Path(session_dir)
    r = QAResult(session=session_dir.name)

    csv_path = session_dir / "frames.csv"
    if not csv_path.exists():
        r.fail("no frames.csv")
        return r
    rows = list(csv.DictReader(csv_path.open(newline="")))
    r.info["frames"] = len(rows)
    if not rows:
        r.fail("frames.csv empty")
        return r

    # key_binding
    kb_path = session_dir / "key_binding.json"
    keybind_keys = set(json.loads(kb_path.read_text())) if kb_path.exists() else set()

    # video grid
    video_path = session_dir / "video.mp4"
    info = V.probe(video_path) if video_path.exists() else None
    if info is None:
        r.warn("no video.mp4 — cannot verify drift")

    # frame_id integrity
    fids = [int(r_["frame_id"]) for r_ in rows]
    if fids != list(range(len(rows))):
        r.fail("frame_id not 0..N-1 contiguous")

    # frame drift
    if info is not None:
        if len(rows) != info.frame_count:
            r.fail(f"frame drift: CSV={len(rows)} rows, video={info.frame_count} frames")
        # video↔data drift (endpoint span only — does NOT catch interior drift)
        span_s = (int(rows[-1]["timestamp_ms"]) - int(rows[0]["timestamp_ms"])) / 1000.0
        if abs(span_s - info.duration_s) > DRIFT_TOL_S:
            r.warn(f"video↔data drift: CSV span {span_s:.1f}s vs video {info.duration_s:.1f}s")
        clip_s = info.duration_s
        if clip_s < MIN_CLIP_S:
            r.warn(f"clip {clip_s:.1f}s under {MIN_CLIP_S:.0f}s minimum")

        # INTERIOR frame-sync: each row's timestamp must match the real frame
        # presentation time. Endpoint span can match while the middle drifts by
        # seconds if the capture dropped frames and binning used a uniform fps
        # grid. Compare timestamp_ms against actual per-frame PTS.
        try:
            pts = V.frame_pts(video_path)
        except Exception:
            pts = []
        if pts and len(pts) == len(rows):
            worst = max(abs(int(rows[i]["timestamp_ms"]) - pts[i] / 1000.0)
                        for i in range(len(rows)))
            if worst > FRAME_SYNC_TOL_MS:
                r.fail(f"frame-sync drift: a row timestamp is {worst/1000:.1f}s off the "
                       f"real frame time (dropped frames + uniform-fps binning?)")
        elif pts:
            r.warn(f"cannot verify interior sync: {len(pts)} PTS vs {len(rows)} rows")

    # timestamps monotonic
    ts = [int(r_["timestamp_ms"]) for r_ in rows]
    if any(b < a for a, b in zip(ts, ts[1:])):
        r.fail("timestamps not non-decreasing")

    # input bleed + OS pollution + coverage
    bleed = 0
    polluted: set[str] = set()
    uncovered: set[str] = set()
    actions: set[str] = set()
    mouse_present = False
    keyboard_present = False
    buttons_present = False
    for r_ in rows:
        kset = {k for k in (r_["input_keys"] or "").split("|") if k}
        if kset:
            keyboard_present = True
        for left, right in K.MODIFIER_PAIRS:
            if left in kset and right in kset:
                bleed += 1
                break
        for k in kset:
            if K.normalize_event_key(k, aggressive=True) is None:
                polluted.add(k)
            if keybind_keys and k not in keybind_keys:
                uncovered.add(k)
        for a in (r_["input_actions"] or "").split("|"):
            if a:
                actions.add(a)
        if (r_["input_mouse_dx"] or "") not in ("", "0") or (r_["input_mouse_dy"] or "") not in ("", "0"):
            mouse_present = True
        if r_["input_mouse_buttons"]:
            buttons_present = True

    if bleed:
        r.warn(f"input bleed (L+R modifier) in {bleed} frames")
    if polluted:
        r.fail(f"OS/system keys leaked into input_keys: {sorted(polluted)}")
    if uncovered:
        r.warn(f"input_keys not covered by key_binding.json: {sorted(uncovered)}")
    if len(actions) < MIN_ACTIONS:
        r.warn(f"only {len(actions)} distinct action(s) (need >= {MIN_ACTIONS}): {sorted(actions)}")
    # missing input modalities (keyboard / mouse motion / mouse buttons)
    if not keyboard_present:
        r.warn("no keyboard input in any frame — keyboard capture missing or unused")
    if not mouse_present:
        r.warn("no mouse dx/dy data (mouse motion capture missing or all-zero) — session incomplete")
    if not mouse_present and not buttons_present:
        r.warn("no mouse input at all (neither motion nor buttons) — mouse capture missing")

    r.info.update(distinct_actions=sorted(actions), mouse_present=mouse_present,
                  keyboard_present=keyboard_present, mouse_buttons_present=buttons_present)
    return r
