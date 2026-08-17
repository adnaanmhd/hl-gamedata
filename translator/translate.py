"""Orchestrator — raw capture bundle  ->  spec delivery bundle.

Two entry points:
  - translate_bundle(): a fresh capture bundle from the tool
        (video.mp4 + inputs.jsonl + metadata.json + keybind.json).
        Applies the implicit 5 s head/tail lossless trim, then bins.
  - reprocess_session(): an already-delivered session folder, rebuilt in place
        (used to fix the existing samples; no trim).
"""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import keys as K
from . import rrd
from . import trim as trimmod
from . import video as V
from .binner import FRAME_COLS, bin_session
from .keybind import build_resolver, bound_literals, invert_keybind
from .keybinds import KEYBINDS, game_key_from_name

VENDOR = "humynlabs"
SPEC_VERSION = "v1"

# every semantic action name the built-in keybinds define — the authority
# for telling a semantic from a literal when sniffing keybind.json direction
_SEMANTIC_VOCAB = {sem for kb in KEYBINDS.values() for sem in kb}


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def load_events(inputs_path: Path) -> list[dict]:
    events: list[dict] = []
    # errors="replace" + a dict guard: this is untrusted player-written
    # JSONL, and a single cp1252 byte (an accented key name from a non-US
    # layout) or a bare `null` line used to raise out of the translator —
    # the same holes the checker had (r-loop 4). A key we cannot decode is
    # dropped by normalize_event_key anyway; crashing the whole translate
    # over one byte is never the right trade.
    with Path(inputs_path).open(errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict) and isinstance(ev.get("t"), int):
                events.append(ev)
    return events


def resolve_keybind(*, keybind_path: Path | None, game_name: str | None,
                    exe_name: str | None = None) -> dict:
    """Prefer an explicit keybind.json; else fall back to the built-in for the game."""
    if keybind_path and Path(keybind_path).exists():
        raw = json.loads(Path(keybind_path).read_text())
        # vendor file is semantic->literal; if it looks inverted (literal->[semantic]),
        # flip it back so we have a single canonical direction.
        return _as_semantic_to_literal(raw)
    slug = game_key_from_name(game_name or "", exe_name)
    return dict(KEYBINDS.get(slug, {})) if slug else {}


def _looks_semantic(x) -> bool:
    """A semantic ACTION name, not a literal key token.

    Requiring a '_' is not enough on its own — real literals like
    'left_shift' and 'mouse_left' have one. The authoritative test is our
    own semantic vocabulary (the keys of the built-in keybinds); anything
    outside it must be clearly namespaced to count.

    Biased deliberately toward NOT flipping: wrongly flipping a
    correctly-oriented file empties the whole keyboard column and gets the
    session rejected as CNT_ACTIONS_FEW (unfixable, unpaid player), whereas
    failing to flip a genuinely inverted one leaves it as it arrived."""
    if not isinstance(x, str) or "_" not in x:
        return False
    if x in _SEMANTIC_VOCAB:
        return True
    return x.count("_") >= 2


def _as_semantic_to_literal(raw: dict) -> dict:
    """Detect direction; return semantic->literal form the resolver expects."""
    # Heuristic: if values are lists of strings that look like our semantic
    # action names (contain '_' and aren't single keys), it's already inverted.
    vals = list(raw.values())
    # Implement the test the comment describes: the LIST ELEMENTS must look
    # like semantic action names. "all values are lists" alone was wrong,
    # because keybind.json is documented as user-authored {semantic:
    # literal} in which "multi-binding alternatives appear as lists" — so a
    # correctly-oriented file whose entries are all multi-bind (e.g.
    # {"movement_move_x_axis": ["a","d"], ...}) was flipped, making literals
    # the semantics. Every real key then failed to resolve, input_keys and
    # input_actions shipped EMPTY on every row, and — because qa-v2 cannot
    # see a keyboard that simply is not there — the session was rejected as
    # CNT_ACTIONS_FEW, which is unfixable, with the player coached to "play
    # actively" for a bug in our parser (r-loop 4).
    looks_inverted = bool(vals) and all(isinstance(v, list) for v in vals) \
        and all(_looks_semantic(x) for v in vals for x in v)
    if not looks_inverted:
        return raw
    sem2lit: dict[str, list[str]] = {}
    for literal, semantics in raw.items():
        for s in semantics:
            sem2lit.setdefault(s, []).append(literal)
    return {s: (lits[0] if len(lits) == 1 else lits) for s, lits in sem2lit.items()}


def write_frames_csv(rows: list[list], path: Path) -> None:
    with Path(path).open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FRAME_COLS)
        w.writerows(rows)


def write_key_binding(keybind: dict, keys_seen: set[str], path: Path) -> None:
    inv = invert_keybind(keybind, also_cover=keys_seen)
    Path(path).write_text(json.dumps(inv, indent=2))


def _data_quality(stats) -> dict:
    return {
        "keyboard_capture": "ok" if stats.has_keyboard else "missing",
        "mouse_capture": "ok" if stats.has_mouse_motion else "missing",   # motion (dx/dy)
        "mouse_buttons": "ok" if stats.has_mouse_buttons else "missing",
        "input_bleed_frames": len(stats.bleed_frames),
        "distinct_actions": sorted(stats.actions_seen),
        "frames_dropped_beyond_video": stats.dropped_beyond_video,
        "frame_timing": stats.frame_timing,
    }


# --------------------------------------------------------------------------- #
# reprocess an existing delivered session (fix in place, no trim)
# --------------------------------------------------------------------------- #
def reprocess_session(session_dir: Path, *, make_rrd: bool = True,
                      rrd_python: str | None = None) -> dict:
    session_dir = Path(session_dir)
    session_json = json.loads((session_dir / "session.json").read_text())
    canonical = session_json.get("canonical", {})
    game_name = canonical.get("game")

    info = V.probe(session_dir / "video.mp4")
    pts = V.frame_pts(session_dir / "video.mp4")
    events = load_events(session_dir / "inputs.jsonl")
    keybind = resolve_keybind(keybind_path=None, game_name=game_name)
    rules = build_resolver(keybind)
    bound = bound_literals(keybind)

    rows, stats = bin_session(events, info, keybind, rules, bound, frame_pts_us=pts)

    write_frames_csv(rows, session_dir / "frames.csv")
    write_key_binding(keybind, stats.keys_seen, session_dir / "key_binding.json")

    # refresh canonical grid + record data-quality
    canonical.update(
        spec_version=SPEC_VERSION, vendor=VENDOR,
        video_fps=info.fps, video_resolution=[info.width, info.height],
        video_duration_s=info.duration_s, frame_count=info.frame_count,
    )
    canonical["data_quality"] = _data_quality(stats)
    session_json["canonical"] = canonical
    (session_dir / "session.json").write_text(json.dumps(session_json, indent=2))

    if make_rrd:
        rrd.generate(session_dir, python=rrd_python)
    else:
        rrd.write_script(session_dir)

    return {"session": session_dir.name, "frames": stats.n_frames,
            "data_quality": canonical["data_quality"]}


# --------------------------------------------------------------------------- #
# translate a fresh capture bundle (implicit 5 s trim)
# --------------------------------------------------------------------------- #
def translate_bundle(bundle_dir: Path, out_root: Path, *, do_trim: bool = True,
                     make_rrd: bool = True, rrd_python: str | None = None) -> dict:
    bundle_dir = Path(bundle_dir)
    meta = json.loads((bundle_dir / "metadata.json").read_text())
    game_info = meta.get("game", {})
    game_name = game_info.get("name") or meta.get("game_name")
    exe_name = game_info.get("exe_name")
    session_id = meta.get("session_id") or bundle_dir.name
    game_slug = game_key_from_name(game_name or "", exe_name) or "unknown_game"

    date = datetime.now(timezone.utc).strftime("%m-%d-%y")
    out_dir = Path(out_root) / VENDOR / game_slug / date / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    src_video = bundle_dir / "video.mp4"
    events = load_events(bundle_dir / "inputs.jsonl")
    warnings: list[str] = []

    # implicit lossless 5 s head/tail trim for new samples
    if do_trim:
        tr = trimmod.trim(src_video, out_dir / "video.mp4")
        events = trimmod.rebase_events(events, tr.head_cut_s, tr.new_duration_s)
        warnings += tr.warnings
        trim_meta = {"head_cut_s": tr.head_cut_s, "end_cut_s": tr.end_cut_s,
                     "new_duration_s": tr.new_duration_s}
    else:
        shutil.copy2(src_video, out_dir / "video.mp4")
        trim_meta = None

    info = V.probe(out_dir / "video.mp4")
    pts = V.frame_pts(out_dir / "video.mp4")
    if pts and len(pts) != info.frame_count:
        warnings.append(
            f"frame PTS count {len(pts)} != frame_count {info.frame_count}; "
            "fell back to uniform-fps binning")
    keybind = resolve_keybind(keybind_path=bundle_dir / "keybind.json",
                              game_name=game_name, exe_name=exe_name)
    rules = build_resolver(keybind)
    bound = bound_literals(keybind)

    rows, stats = bin_session(events, info, keybind, rules, bound, frame_pts_us=pts)
    write_frames_csv(rows, out_dir / "frames.csv")
    write_key_binding(keybind, stats.keys_seen, out_dir / "key_binding.json")

    canonical = {
        "spec_version": SPEC_VERSION, "vendor": VENDOR, "game": game_name,
        "session_id": session_id, "video_fps": info.fps,
        "video_resolution": [info.width, info.height],
        "video_duration_s": info.duration_s, "frame_count": info.frame_count,
        "created_at_utc": meta.get("recording", {}).get("started_at_utc"),
        "has_audio": info.has_audio,
        "trim": trim_meta,
        "data_quality": _data_quality(stats),
    }
    (out_dir / "session.json").write_text(
        json.dumps({"canonical": canonical, "humyncapture_metadata": meta}, indent=2))

    if make_rrd:
        rrd.generate(out_dir, python=rrd_python)
    else:
        rrd.write_script(out_dir)

    return {"session": session_id, "out_dir": str(out_dir), "frames": stats.n_frames,
            "warnings": warnings, "data_quality": canonical["data_quality"]}
