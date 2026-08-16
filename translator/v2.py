"""Game Data Capture Spec v2 — writer + QA.

v2 delta vs v1 (spec: latest_requirements/v2_Game_Data_Capture_Spec.pdf):
  - session.json: bare flat object with 16 required fields incl. localization,
    platform and input_mouse_convention. No nested canonical/humyncapture blocks.
  - key_binding.json: REMOVED from the delivery (semantics live in input_actions).
  - frames.csv: full 36-column schema (adds camera_radial_k1..k6 +
    camera_tangential_p1/p2, all null for input-only sessions); v2-style key
    names (W, Shift, LCtrl, Space; buttons Left/Right/Middle); mouse dx/dy as
    floats with a "0.0" no-movement sentinel; every key in input_keys must be
    bound so its frame has a non-null input_actions -> unbound keys are stripped.
  - upload layout: <vendor>/<mm-dd-yyyy of upload, UTC>/<game>/<session-id>/.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import context as ctxmod
from . import rrd
from . import sync
from . import trim as trimmod
from . import video as V
from .binner import C2W_COLS, CAMERA_COLS, bin_session
from .keybind import (bound_literals, build_resolver, collapse_ambiguous_runs,
                      invert_keybind, resolve_actions)
from .keybinds import (AMBIGUOUS_PAIRS, CONTEXT_ALLOWED, KEYBINDS,
                       game_key_from_name)
from .translate import VENDOR, load_events, resolve_keybind

# --------------------------------------------------------------------------- #
# v2 constants
# --------------------------------------------------------------------------- #
CAMERA_EXTRA_COLS = [f"camera_radial_k{i}" for i in range(1, 7)] + [
    "camera_tangential_p1", "camera_tangential_p2"]
V2_FRAME_COLS = (
    ["frame_id", "timestamp_ms"]
    + C2W_COLS + CAMERA_COLS + CAMERA_EXTRA_COLS
    + ["input_keys", "input_actions", "input_mouse_buttons",
       "input_mouse_dx", "input_mouse_dy"]
)

GAME_TITLES = {"kamla": "Kamla", "outer_wilds": "Outer Wilds"}
LOCALIZATIONS = {"kamla": "en-IN", "outer_wilds": "en-US"}

# Both games: FPS-style camera look, default (non-inverted) axes — confirmed
# with the recording team 2026-07-17.
MOUSE_CONVENTION = {
    "maps_to": "camera_look_velocity",
    "dx_positive": "right",
    "dx_negative": "left",
    "dy_positive": "down",
    "dy_negative": "up",
}

# Session-agreed keybind additions (2026-07-17): Outer Wilds' Enter is the
# documented menu/dialogue Confirm key; the raw keybind.json files predate this.
KEYBIND_PATCHES: dict[str, dict] = {
    "outer_wilds": {"general_confirm": ["e", "enter"]},
}

# canonical internal token -> v2 display name
_KEY_DISPLAY = {
    "shift": "Shift", "shift_l": "LShift", "shift_r": "RShift",
    "ctrl": "Ctrl", "ctrl_l": "LCtrl", "ctrl_r": "RCtrl",
    "alt": "Alt", "alt_l": "LAlt", "alt_r": "RAlt", "alt_gr": "AltGr",
    "esc": "Esc", "space": "Space", "tab": "Tab", "enter": "Enter",
    "backspace": "Backspace", "delete": "Delete", "home": "Home", "end": "End",
    "page_up": "PageUp", "page_down": "PageDown",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
}
_BTN_DISPLAY = {"mouse_left": "Left", "mouse_right": "Right",
                "mouse_middle": "Middle", "mouse_x1": "X1", "mouse_x2": "X2"}


def key_display(tok: str) -> str:
    if tok in _KEY_DISPLAY:
        return _KEY_DISPLAY[tok]
    if len(tok) == 1:
        return tok.upper()
    return "".join(p.capitalize() for p in tok.split("_"))


_KEY_DISPLAY_INV = {v: k for k, v in _KEY_DISPLAY.items()}
_BTN_DISPLAY_INV = {v: k for k, v in _BTN_DISPLAY.items()}


def key_canonical(disp: str) -> str:
    """v2 display name -> canonical token (inverse of key_display)."""
    if disp in _KEY_DISPLAY_INV:
        return _KEY_DISPLAY_INV[disp]
    if len(disp) == 1:
        return disp.lower()
    return re.sub(r"(?<!^)(?=[A-Z])", "_", disp).lower()


# --------------------------------------------------------------------------- #
# context-gated action resolution over binner rows
# --------------------------------------------------------------------------- #
def apply_context_to_rows(rows: list[list], ctx_track: list[str], slug: str,
                          rules, fps: float, *,
                          ambig_overrides: dict[int, str] | None = None) -> dict:
    """Re-resolve input_actions per row using the per-frame context track and
    strip context-dead literals from input_keys (in place, v1-canonical rows).

    Returns a summary: frames whose actions changed, dead-press strip counts
    per literal, and the ambiguous-pair choices made.
    """
    allowed = CONTEXT_ALLOWED[slug]
    per_frame: list[list[str]] = []
    dead_strips: dict[str, int] = {}
    changed = 0
    def _active(v) -> bool:
        """Mouse delta counts as motion: not blank and not (float) zero."""
        return v not in ("", None) and float(v) != 0.0

    for i, row in enumerate(rows):
        keys, _actions, btns, dx, dy = row[-5:]
        kset = set(keys.split("|")) - {""} if keys else set()
        bset = set(btns.split("|")) - {""} if btns else set()
        motion = (_active(dx), _active(dy))
        acts, dead = resolve_actions(kset | bset, motion, rules,
                                     context=ctx_track[i], allowed=allowed)
        if dead:
            for t in sorted(dead & (kset | bset)):
                dead_strips[t] = dead_strips.get(t, 0) + 1
            if dead & kset:
                kset -= dead
                row[-5] = "|".join(sorted(kset))
            if dead & bset:      # context-dead buttons strip too (07-31 rule)
                bset -= dead
                row[-3] = "|".join(sorted(bset))
        per_frame.append(acts)
    pair = AMBIGUOUS_PAIRS.get(slug)
    chosen = (collapse_ambiguous_runs(per_frame, pair, fps,
                                      overrides=ambig_overrides)
              if pair else {})
    for i, row in enumerate(rows):
        new = "|".join(per_frame[i])
        if row[-4] != new:
            changed += 1
            row[-4] = new
    return {"frames_changed": changed, "dead_press_strips": dead_strips,
            "ambiguous_choices": {start: action
                                  for action, starts in _group_runs(chosen).items()
                                  for start in starts}}


def _group_runs(chosen: dict[int, str]) -> dict[str, list[int]]:
    """{action: [run-start frames]} — for reporting collapse decisions."""
    out: dict[str, list[int]] = {}
    prev = None
    for i in sorted(chosen):
        if prev is None or i != prev + 1:
            out.setdefault(chosen[i], []).append(i)
        prev = i
    return out


# --------------------------------------------------------------------------- #
# translate a raw bundle to a v2 delivery
# --------------------------------------------------------------------------- #
def _iso_utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _v2_rows(rows: list[list], bound: frozenset[str], strip_stats: dict) -> list[list]:
    """v1 binner rows -> v2 rows: strip unbound keys, v2 names, float dx/dy."""
    extra_null = [""] * len(CAMERA_EXTRA_COLS)
    out = []
    for row in rows:
        head, (keys, actions, btns, dx, dy) = row[:-5], row[-5:]
        kept = []
        for k in (keys or "").split("|"):
            if not k:
                continue
            if k in bound:
                kept.append(key_display(k))
            else:
                strip_stats[k] = strip_stats.get(k, 0) + 1
        btn_disp = [_BTN_DISPLAY.get(b, key_display(b))
                    for b in (btns or "").split("|") if b]
        dx_out = "" if dx == "" else f"{float(dx):.1f}"
        dy_out = "" if dy == "" else f"{float(dy):.1f}"
        out.append(head + extra_null
                   + ["|".join(kept), actions, "|".join(btn_disp), dx_out, dy_out])
    return out


def build_session_json(*, slug: str, session_id: str, meta: dict,
                       info: V.VideoInfo, head_cut_s: float) -> dict:
    started_raw = meta.get("recording", {}).get("started_at_utc")
    started = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
    created = started + timedelta(seconds=head_cut_s)
    ended = created + timedelta(seconds=info.duration_s)
    system = meta.get("system", {})
    return {
        "vendor_name": VENDOR,
        "game_title": GAME_TITLES.get(slug, slug),
        "session_id": session_id,
        "created_at_utc": _iso_utc(created),
        "ended_at_utc": _iso_utc(ended),
        "duration_ms": round(info.duration_s * 1000.0),
        "duration_seconds": round(info.duration_s, 3),
        "fps": info.fps,
        "frame_count": info.frame_count,
        "record_width_px": info.width,
        "record_height_px": info.height,
        "screen_width_px": int(system.get("screen_width") or info.width),
        "screen_height_px": int(system.get("screen_height") or info.height),
        "localization": LOCALIZATIONS.get(slug, "en-US"),
        "platform": "PC",
        "input_mouse_convention": dict(MOUSE_CONVENTION),
    }


def translate_bundle_v2(bundle_dir: Path, out_root: Path, *,
                        head_s: float = trimmod.HEAD_S,
                        tail_s: float = trimmod.TAIL_S,
                        make_rrd: bool = True,
                        rrd_python: str | None = None,
                        lag_correct: bool = True,
                        action_overrides: dict[int, str] | None = None) -> dict:
    bundle_dir = Path(bundle_dir)
    meta = json.loads((bundle_dir / "metadata.json").read_text())
    game_info = meta.get("game", {})
    game_name = game_info.get("name") or meta.get("game_name")
    exe_name = game_info.get("exe_name")
    session_id = meta.get("session_id") or bundle_dir.name
    slug = game_key_from_name(game_name or "", exe_name) or "unknown_game"

    date = datetime.now(timezone.utc).strftime("%m-%d-%Y")   # v2: 4-digit year
    out_dir = Path(out_root) / VENDOR / date / slug / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_events = load_events(bundle_dir / "inputs.jsonl")
    warnings: list[str] = []

    tr = trimmod.trim(bundle_dir / "video.mp4", out_dir / "video.mp4",
                      head_s=head_s, tail_s=tail_s)
    events = trimmod.rebase_events(raw_events, tr.head_cut_s, tr.new_duration_s)
    warnings += tr.warnings

    info = V.probe(out_dir / "video.mp4")
    pts = V.frame_pts(out_dir / "video.mp4")
    if pts and len(pts) != info.frame_count:
        warnings.append(f"frame PTS count {len(pts)} != frame_count "
                        f"{info.frame_count}; fell back to uniform-fps binning")

    keybind = resolve_keybind(keybind_path=bundle_dir / "keybind.json",
                              game_name=game_name, exe_name=exe_name)
    keybind.update(KEYBIND_PATCHES.get(slug, {}))
    rules = build_resolver(keybind)
    bound = bound_literals(keybind)

    rows, stats = bin_session(events, info, keybind, rules, bound, frame_pts_us=pts)

    # Controls-to-video sync: the capture tool anchors the input clock at
    # "ffmpeg confirmed running", not at the first encoded frame, so inputs and
    # video carry a per-session CONSTANT offset (HumynCapture_Capture_Tool_Issues.md
    # §"input/video clock anchor"). Measure it with the client's own
    # action_video_grounding algorithm, shift the raw events by the measured
    # constant, and re-bin — then re-measure to confirm the residual.
    sync_report: dict = {"measured": False, "applied_shift_ms": 0.0}
    shift_us = 0
    if lag_correct and sync.available() and stats.has_mouse_motion:
        mdx, mdy = sync.motion_track(out_dir / "video.mp4")
        conv_meta = {"input_mouse_convention": MOUSE_CONVENTION}

        def _measure(rs):
            adx, ady = sync.input_track_from_rows(
                [row[-2] for row in rs], [row[-1] for row in rs], conv_meta)
            return sync.estimate_lag(mdx, mdy, adx, ady)

        est = _measure(rows)
        sync_report.update(
            measured=True,
            measured_lag_ms=round(est.lag_ms(info.fps), 1),
            measured_correlation=round(est.correlation, 4),
            active_fraction=round(est.active_fraction, 4))
        measurable = (est.active_fraction >= sync.MIN_ACTIVE_FRACTION
                      and abs(est.correlation) >= sync.MIN_ABS_CORRELATION)
        if not measurable:
            warnings.append(
                f"controls-to-video sync unverifiable (active "
                f"{est.active_fraction:.2%}, |corr| {abs(est.correlation):.3f}) "
                f"— no correction applied")
        for _ in range(3):
            if not (measurable and abs(est.lag_ms(info.fps)) > sync.TARGET_ABS_LAG_MS):
                break
            shift_us += round(-est.lag_frames / info.fps * 1_000_000)
            shifted = [dict(e, t=e["t"] + shift_us) for e in raw_events]
            events = trimmod.rebase_events(shifted, tr.head_cut_s,
                                           tr.new_duration_s)
            rows, stats = bin_session(events, info, keybind, rules, bound,
                                      frame_pts_us=pts)
            est = _measure(rows)
        sync_report.update(
            applied_shift_ms=round(shift_us / 1000.0, 1),
            residual_lag_ms=round(est.lag_ms(info.fps), 1),
            residual_correlation=round(est.correlation, 4))
        status, msg = sync.verdict(est, info.fps)
        sync_report["status"] = status
        if status == "FAIL":
            warnings.append(f"controls-to-video sync STILL FAILING after "
                            f"correction: {msg}")
    elif lag_correct and not sync.available():
        warnings.append("controls-to-video sync not measured — numpy/opencv "
                        "unavailable (add --with numpy --with "
                        "opencv-python-headless)")

    # Context-gated action resolution: place each multi-bound key's press onto
    # the ONE semantic the game mode supports (customer feedback: no fan-out).
    ctx_summary: dict = {}
    if slug in ctxmod.CONTEXT_GAMES:
        if ctxmod.available():
            ctx_track = ctxmod.classify_video(out_dir / "video.mp4", info.fps, slug)
            if len(ctx_track) == len(rows):
                ctx_summary = apply_context_to_rows(
                    rows, ctx_track, slug, rules, info.fps,
                    ambig_overrides=action_overrides)
            else:
                warnings.append(
                    f"context track {len(ctx_track)} frames != {len(rows)} rows "
                    f"— context gating SKIPPED; multi-bound keys fan out, DO NOT "
                    f"SHIP without review")
        else:
            warnings.append(
                "context gating SKIPPED (numpy/opencv unavailable) — multi-bound "
                "keys fan out, DO NOT SHIP without review (add --with numpy "
                "--with opencv-python-headless)")

    strip_stats: dict[str, int] = {}
    v2rows = _v2_rows(rows, bound, strip_stats)
    with (out_dir / "frames.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(V2_FRAME_COLS)
        w.writerows(v2rows)

    session = build_session_json(slug=slug, session_id=session_id, meta=meta,
                                 info=info, head_cut_s=tr.head_cut_s)
    (out_dir / "session.json").write_text(json.dumps(session, indent=2))

    if make_rrd:
        rrd.generate(out_dir, python=rrd_python)
    else:
        rrd.write_script(out_dir)

    # Correction record lives at the OUTPUT ROOT (outside the vendor upload
    # tree): qa-v2's raw off-by-one recheck reads the applied shift from here.
    report_path = Path(out_root) / "translation_report.json"
    try:
        report = json.loads(report_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        report = {}
    report[session_id] = {**sync_report, "shift_us": shift_us,
                          "out_dir": str(out_dir)}
    report_path.write_text(json.dumps(report, indent=2))

    if shift_us:
        sync_dq = (f"corrected {sync_report['applied_shift_ms']:+.1f}ms "
                   f"(was {sync_report['measured_lag_ms']:+.1f}ms, residual "
                   f"{sync_report['residual_lag_ms']:+.1f}ms)")
    elif sync_report.get("status") == "PASS":
        sync_dq = "ok"
    else:
        sync_dq = "unverified"

    return {
        "session": session_id, "out_dir": str(out_dir), "frames": stats.n_frames,
        "warnings": warnings, "stripped_keys": strip_stats, "sync": sync_report,
        "trim": {"head_cut_s": tr.head_cut_s, "end_cut_s": tr.end_cut_s,
                 "new_duration_s": tr.new_duration_s},
        "context": ctx_summary,
        "data_quality": {
            "controls_video_sync": sync_dq,
            "keyboard_capture": "ok" if stats.has_keyboard else "missing",
            "mouse_capture": "ok" if stats.has_mouse_motion else "missing",
            "mouse_buttons": "ok" if stats.has_mouse_buttons else "missing",
            "input_bleed_frames": len(stats.bleed_frames),
            "distinct_actions": sorted({a for row in rows
                                        for a in (row[-4] or "").split("|") if a}),
            "frame_timing": stats.frame_timing,
        },
    }


# --------------------------------------------------------------------------- #
# v2 QA — spec §1.5 vendor validation checks
# --------------------------------------------------------------------------- #
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_LOC_RE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2,3})?$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")
_PLATFORMS = {"Xbox", "PC", "Switch", "PlayStation", "Mobile-iOS",
              "Mobile-Android", "Steam Deck"}
_MAPS_TO = {"camera_look_velocity", "camera_pan_velocity", "camera_pan_position",
            "cursor_position", "vehicle_steering", "none", "other"}
_CAMERA_MAPS = {"camera_look_velocity", "camera_pan_velocity", "camera_pan_position"}
_REQUIRED = ["vendor_name", "game_title", "session_id", "created_at_utc",
             "ended_at_utc", "duration_ms", "duration_seconds", "fps",
             "frame_count", "record_width_px", "record_height_px",
             "screen_width_px", "screen_height_px", "localization", "platform",
             "input_mouse_convention"]


class V2Result:
    def __init__(self, session: str):
        self.session = session
        self.status = "PASS"
        self.issues: list[str] = []

    def fail(self, m):
        self.status = "FAIL"
        self.issues.append(f"FAIL: {m}")

    def warn(self, m):
        if self.status == "PASS":
            self.status = "WARN"
        self.issues.append(f"WARN: {m}")


def _check_session_json(s: dict, r: V2Result) -> None:
    missing = [k for k in _REQUIRED if k not in s]
    if missing:
        r.fail(f"session.json missing required fields: {missing}")
        return
    for k in ("created_at_utc", "ended_at_utc"):
        if not _TS_RE.match(s[k]):
            r.fail(f"session.json {k} not timezone-aware ISO 8601: {s[k]!r}")
    created = datetime.fromisoformat(s["created_at_utc"].replace("Z", "+00:00"))
    ended = datetime.fromisoformat(s["ended_at_utc"].replace("Z", "+00:00"))
    if ended <= created:
        r.fail("ended_at_utc <= created_at_utc")
    if abs(s["duration_ms"] / 1000.0 - s["duration_seconds"]) > 1.0:
        r.fail("duration_ms/1000 differs from duration_seconds by > 1s")
    if abs((ended - created).total_seconds() - s["duration_ms"] / 1000.0) > 1.0:
        r.fail("duration_ms inconsistent with ended-created (> 1s)")
    if abs(s["frame_count"] - s["fps"] * s["duration_seconds"]) > 2:
        r.fail("frame_count differs from fps*duration_seconds by > 2 frames")
    if not _LOC_RE.match(s["localization"]):
        r.fail(f"localization not BCP 47 per spec pattern: {s['localization']!r}")
    if s["platform"] not in _PLATFORMS:
        r.fail(f"platform not in spec enum: {s['platform']!r}")
    conv = s["input_mouse_convention"]
    need = ["maps_to", "dx_positive", "dx_negative", "dy_positive", "dy_negative"]
    miss = [k for k in need if k not in conv]
    if miss:
        r.fail(f"input_mouse_convention missing: {miss}")
        return
    if conv["maps_to"] not in _MAPS_TO:
        r.fail(f"maps_to not in enum: {conv['maps_to']!r}")
    if conv["maps_to"] == "other" and not conv.get("maps_to_other"):
        r.fail("maps_to='other' requires maps_to_other")
    if conv["maps_to"] in _CAMERA_MAPS:
        if conv["dx_positive"] not in {"right", "left"} or \
           conv["dx_negative"] not in {"right", "left"}:
            r.fail("camera mapping: dx_positive/dx_negative must be right|left")
        if conv["dy_positive"] not in {"down", "up"} or \
           conv["dy_negative"] not in {"down", "up"}:
            r.fail("camera mapping: dy_positive/dy_negative must be down|up")
    else:
        if any(conv[k] != "not_applicable" for k in need[1:]):
            r.fail("non-camera mapping: all dx/dy fields must be not_applicable")


def check_session_v2(session_dir: Path, raw_bundle: Path | None = None) -> V2Result:
    session_dir = Path(session_dir)
    r = V2Result(session_dir.name)

    for req in ("session.json", "frames.csv", "video.mp4", "session.rrd",
                "rrd_creation.py"):
        if not (session_dir / req).exists():
            r.fail(f"missing delivery file: {req}")
    if (session_dir / "key_binding.json").exists():
        r.fail("key_binding.json present — removed from the v2 delivery")
    if r.status == "FAIL":
        return r

    s = json.loads((session_dir / "session.json").read_text())
    _check_session_json(s, r)

    with (session_dir / "frames.csv").open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    if header != V2_FRAME_COLS:
        r.fail(f"frames.csv header != v2 schema (got {len(header)} cols, "
               f"want {len(V2_FRAME_COLS)})")
        return r
    col = {c: i for i, c in enumerate(V2_FRAME_COLS)}

    # structure
    if len(rows) != s.get("frame_count"):
        r.fail(f"row count {len(rows)} != session.json frame_count {s.get('frame_count')}")
    fids = [int(x[col["frame_id"]]) for x in rows]
    if fids != list(range(len(rows))):
        r.fail("frame_id not zero-based sequential")
    ts = [int(x[col["timestamp_ms"]]) for x in rows]
    if any(b <= a for a, b in zip(ts, ts[1:])):
        r.fail("timestamp_ms not strictly increasing")
    frame_iv = 1000.0 / s["fps"] if s.get("fps") else 0.0
    if ts and ts[0] > frame_iv:
        r.fail(f"timestamp_ms[0] = {ts[0]} not near zero")
    if ts and abs(ts[-1] - s["duration_ms"]) > 4 * frame_iv:
        r.fail(f"timestamp_ms[-1] = {ts[-1]} vs duration_ms {s['duration_ms']} "
               f"(> 4 frame intervals apart)")
    dts = [b - a for a, b in zip(ts, ts[1:])]
    if dts:
        med = sorted(dts)[len(dts) // 2]
        irregular = sum(1 for d in dts if abs(d - med) > 0.2 * med)
        if irregular:
            r.warn(f"frame spacing irregular in {irregular}/{len(dts)} intervals "
                   f"(median {med}ms) — capture tool drops frames; timestamps are "
                   f"REAL frame PTS (correct sync), spacing cannot be fixed in post")

    # camera columns all null (input-only delivery)
    cam_cols = [col[c] for c in C2W_COLS + CAMERA_COLS + CAMERA_EXTRA_COLS]
    dirty = sum(1 for x in rows if any(x[i] != "" for i in cam_cols))
    if dirty:
        r.fail(f"camera columns non-null in {dirty} rows (input-only session)")

    # input rules
    keys_no_action = bad_float = null_key_rows = 0
    bad_tokens: set[str] = set()
    bad_btns: set[str] = set()
    actions_seen: set[str] = set()
    any_motion = any_btn = False
    for x in rows:
        keys, acts = x[col["input_keys"]], x[col["input_actions"]]
        btns = x[col["input_mouse_buttons"]]
        dx, dy = x[col["input_mouse_dx"]], x[col["input_mouse_dy"]]
        if keys and not acts:
            keys_no_action += 1
        if not keys and not acts:
            null_key_rows += 1
        for t in keys.split("|") if keys else []:
            if not t or t != t.strip() or "," in t or " " in t \
                    or (t.lower() == t and not t.isdigit()):
                bad_tokens.add(t)
        for b in btns.split("|") if btns else []:
            if b not in {"Left", "Right", "Middle", "X1", "X2"}:
                bad_btns.add(b)
        for a in acts.split("|") if acts else []:
            actions_seen.add(a)
        if btns:
            any_btn = True
        for v in (dx, dy):
            if not _FLOAT_RE.match(v):
                bad_float += 1
        if dx not in ("", "0.0") or dy not in ("", "0.0"):
            any_motion = True
    if keys_no_action:
        r.fail(f"{keys_no_action} frames have input_keys but null input_actions")

    # same-literal fan-out (customer feedback): a frame must never list two
    # actions that are alternative meanings of ONE held key — only the action
    # the character is actually performing. Reconstructed from the built-in
    # keybind for the session's game.
    qa_slug = game_key_from_name(s.get("game_title", ""))
    if qa_slug in KEYBINDS:
        kb = dict(KEYBINDS[qa_slug])
        kb.update(KEYBIND_PATCHES.get(qa_slug, {}))
        lit2sem = invert_keybind(kb)
        fanout = 0
        example = ""
        for x in rows:
            acts = set((x[col["input_actions"]] or "").split("|")) - {""}
            if len(acts) < 2:
                continue
            toks = ([key_canonical(t) for t in x[col["input_keys"]].split("|") if t]
                    + [_BTN_DISPLAY_INV.get(b, b)
                       for b in x[col["input_mouse_buttons"]].split("|") if b])
            for t in toks:
                hit = acts & set(lit2sem.get(t, ()))
                if len(hit) >= 2:
                    fanout += 1
                    example = example or (f"frame {x[col['frame_id']]}: "
                                          f"{t} -> {sorted(hit)}")
                    break
        if fanout:
            r.fail(f"same-literal action fan-out in {fanout} frames — key(s) "
                   f"emit multiple conditional actions (e.g. {example}); "
                   f"context gating missing or failed")
    if bad_tokens:
        r.fail(f"non-v2 key tokens in input_keys: {sorted(bad_tokens)}")
    if bad_btns:
        r.fail(f"non-v2 mouse button tokens: {sorted(bad_btns)}")
    if bad_float:
        r.fail(f"input_mouse_dx/dy not float-formatted ('0.0' sentinel) in "
               f"{bad_float} cells")
    if len(actions_seen) < 3:
        r.warn(f"only {len(actions_seen)} distinct actions: {sorted(actions_seen)}")
    if not any_motion:
        r.fail("mouse motion missing (dx/dy never non-zero) — unrecoverable, re-record")
    if not any_btn:
        r.warn("no mouse button presses in any frame")

    # video ↔ metadata
    info = V.probe(session_dir / "video.mp4")
    if (info.width, info.height) != (s["record_width_px"], s["record_height_px"]):
        r.fail(f"video {info.width}x{info.height} != record_*_px "
               f"{s['record_width_px']}x{s['record_height_px']}")
    if info.frame_count != len(rows):
        r.fail(f"video frame count {info.frame_count} != csv rows {len(rows)}")
    if abs(info.duration_s - s["duration_seconds"]) > 1.0:
        r.fail(f"video duration {info.duration_s:.2f}s vs duration_seconds "
               f"{s['duration_seconds']:.2f}s (> 1s)")
    if info.duration_s < 70.0:
        r.warn(f"clip {info.duration_s:.1f}s under 70s minimum")

    # frame-sync: per-row timestamp vs real frame PTS
    pts = V.frame_pts(session_dir / "video.mp4")
    if pts and len(pts) == len(rows):
        worst = max(abs(ts[i] - pts[i] / 1000.0) for i in range(len(rows)))
        if worst > 100.0:
            r.fail(f"frame-sync drift: worst row timestamp {worst:.0f}ms off real PTS")
    else:
        r.warn("cannot verify frame sync (PTS unreadable)")

    # controls-to-video sync (client's action_video_grounding, same gates)
    shift_us = _applied_shift_us(session_dir)
    if sync.available():
        mdx, mdy = sync.motion_track(session_dir / "video.mp4")
        adx, ady = sync.input_track_from_rows(
            [x[col["input_mouse_dx"]] for x in rows],
            [x[col["input_mouse_dy"]] for x in rows], s)
        est = sync.estimate_lag(mdx, mdy, adx, ady)
        status, msg = sync.verdict(est, s["fps"])
        note = f" [capture clock offset corrected {shift_us / 1000:+.1f}ms " \
               f"at translate]" if shift_us else ""
        if status == "FAIL":
            r.fail(f"controls-to-video sync: {msg}{note}")
        elif status in ("WARN", "SKIP"):
            r.warn(f"controls-to-video sync: {msg}{note}")
        else:
            r.issues.append(f"OK: controls-to-video sync — {msg}{note}")
    else:
        r.warn("controls-to-video sync not measured (numpy/opencv unavailable)")

    # independent event-binning recomputation (anti-off-by-one proof)
    if raw_bundle is not None:
        _verify_against_raw(session_dir, Path(raw_bundle), s, rows, col, pts, r,
                            shift_us=shift_us)
    return r


def _applied_shift_us(session_dir: Path) -> int:
    """Read the sync shift applied at translate time, if any.

    translate-v2 records it in translation_report.json at the output root
    (…/<out>/translation_report.json, outside the vendor upload tree); the
    delivered files themselves stay bare per the v2 contract.
    """
    d = Path(session_dir).resolve()
    for parent in list(d.parents)[:4]:
        p = parent / "translation_report.json"
        if p.is_file():
            try:
                entry = json.loads(p.read_text()).get(d.name) or {}
                return int(entry.get("shift_us") or 0)
            except (json.JSONDecodeError, ValueError, TypeError):
                return 0
    return 0


# Isolated-flip tolerance (Adnaan 2026-08-16): a REAL desync drifts and
# shows as long runs of mismatched frames; single-frame flips are binning
# jitter (an event landing within float-noise of a frame boundary flips
# sides between translate and recheck) and blocked fixes forever (the
# 08-16 fix-failed loop, 5 of 10 rows). Block only on a run of >=
# RAW_DXDY_RUN_BLOCK consecutive mismatched frames OR a total above
# RAW_DXDY_FRAC_BLOCK of rows; anything smaller is a warn.
RAW_DXDY_RUN_BLOCK = 3
RAW_DXDY_FRAC_BLOCK = 0.005


def _verify_against_raw(session_dir: Path, raw_bundle: Path, s: dict,
                        rows: list[list], col: dict, pts: list[int],
                        r: V2Result, *, shift_us: int = 0) -> None:
    """Recompute per-frame dx/dy sums straight from inputs.jsonl with
    window-containment semantics (frame f owns [pts[f], pts[f+1])) and diff
    against the shipped CSV. Catches any off-by-one frame attribution.

    `shift_us` is the constant sync correction applied at translate time
    (events shifted later by shift_us before binning); the recomputation
    applies the same shift so an intentional correction isn't reported as
    misattribution."""
    from bisect import bisect_right
    meta = json.loads((raw_bundle / "metadata.json").read_text())
    started = datetime.fromisoformat(
        meta["recording"]["started_at_utc"].replace("Z", "+00:00"))
    created = datetime.fromisoformat(s["created_at_utc"].replace("Z", "+00:00"))
    head_us = (created - started).total_seconds() * 1e6 - shift_us
    end_us = head_us + s["duration_seconds"] * 1e6
    if not pts or len(pts) != len(rows):
        r.warn("raw verification skipped: PTS unavailable")
        return
    dx = [0.0] * len(rows)
    dy = [0.0] * len(rows)
    n_events = 0
    for line in (raw_bundle / "inputs.jsonl").open():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "mouse_raw" or not isinstance(e.get("t"), int):
            continue
        if not (head_us <= e["t"] < end_us):
            continue
        t = int(e["t"] - head_us)
        f = max(bisect_right(pts, t) - 1, 0)
        if f < len(rows):
            dx[f] += int(e.get("dx", 0) or 0)
            dy[f] += int(e.get("dy", 0) or 0)
            n_events += 1
    mismatch_idx = [
        i for i, x in enumerate(rows)
        if float(x[col["input_mouse_dx"]] or 0) != dx[i]
        or float(x[col["input_mouse_dy"]] or 0) != dy[i]]
    if mismatch_idx:
        run = best = 1
        for a, b in zip(mismatch_idx, mismatch_idx[1:]):
            run = run + 1 if b == a + 1 else 1
            best = max(best, run)
        frac = len(mismatch_idx) / len(rows)
        if best >= RAW_DXDY_RUN_BLOCK or frac > RAW_DXDY_FRAC_BLOCK:
            r.fail(f"raw recomputation: dx/dy differs from CSV in "
                   f"{len(mismatch_idx)} frames (max run {best}, "
                   f"{frac:.2%} of rows; possible off-by-one frame "
                   f"attribution)")
        else:
            r.warn(f"isolated dx/dy attribution flips: "
                   f"{len(mismatch_idx)} frame(s), max run {best}, "
                   f"{frac:.2%} of rows — within tolerance (run<"
                   f"{RAW_DXDY_RUN_BLOCK} and <={RAW_DXDY_FRAC_BLOCK:.1%});"
                   f" single-frame binning jitter, not desync")
    else:
        note = f" (incl. {shift_us / 1000:+.1f}ms sync shift)" if shift_us else ""
        r.issues.append(
            f"OK: off-by-one check — {n_events} raw mouse events independently "
            f"re-binned to containing frames{note}; CSV matches exactly on all "
            f"{len(rows)} rows")
