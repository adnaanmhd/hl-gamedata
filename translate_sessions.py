"""
Translate HumynCapture session pairs (Inputs.jsonl + KeyBind.json) into the
Odyssey Game Data Capture Spec v1 format.

Decisions locked with user:
  - 30 fps binning (matches the tool's default ffmpeg config); frames are 33.333 ms wide.
  - Drop mouse_wheel events; keep ALL rows including unfocused frames.
  - Case-fold key names + apply a known-alias table when resolving semantic actions.
  - Flat output: <translated>/<session-slug>/{frames.csv, key_binding.json}.
  - Camera columns (c2w_m##, camera_*) written as empty (null).
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

SRC = Path("/Users/adnaan/Documents/hl-gamedata/game-data")
DST = SRC / "translated"
FPS = 30
FRAME_US = 1_000_000 / FPS  # 33_333.333...

# Camera columns kept null per user instruction.
C2W_COLS = [f"c2w_m{r}{c}" for r in range(4) for c in range(4)]
CAMERA_COLS = ["camera_model", "camera_fx", "camera_fy", "camera_cx", "camera_cy"]
FRAME_COLS = (
    ["frame_id", "timestamp_ms"]
    + C2W_COLS
    + CAMERA_COLS
    + ["input_keys", "input_actions", "input_mouse_buttons", "input_mouse_dx", "input_mouse_dy"]
)

# Map keybind literal names -> normalized event-key tokens used by HumynCapture/pynput.
# Multi-target values mean the keybind matches if ANY of those event keys is held
# (e.g., bare "Shift" matches shift, shift_l, or shift_r).
KEY_ALIASES: dict[str, tuple[str, ...]] = {
    # Modifiers
    "shift": ("shift", "shift_l", "shift_r"),
    "leftshift": ("shift_l",),
    "lshift": ("shift_l",),
    "left shift": ("shift_l",),
    "rightshift": ("shift_r",),
    "rshift": ("shift_r",),
    "right shift": ("shift_r",),
    "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
    "control": ("ctrl", "ctrl_l", "ctrl_r"),
    "leftctrl": ("ctrl_l",),
    "lctrl": ("ctrl_l",),
    "left ctrl": ("ctrl_l",),
    "rightctrl": ("ctrl_r",),
    "rctrl": ("ctrl_r",),
    "right ctrl": ("ctrl_r",),
    "alt": ("alt", "alt_l", "alt_r", "alt_gr"),
    "leftalt": ("alt_l",),
    "lalt": ("alt_l",),
    "left alt": ("alt_l",),
    "rightalt": ("alt_r",),
    "ralt": ("alt_r",),
    "right alt": ("alt_r",),
    # Named keys
    "escape": ("esc",),
    "esc": ("esc",),
    "space": ("space",),
    "spacebar": ("space",),
    "space bar": ("space",),
    "tab": ("tab",),
    "enter": ("enter",),
    "return": ("enter",),
    "backspace": ("backspace",),
    "capslock": ("caps_lock",),
    "caps_lock": ("caps_lock",),
    "delete": ("delete",),
    "del": ("delete",),
    "insert": ("insert",),
    "ins": ("insert",),
    "home": ("home",),
    "end": ("end",),
    "pageup": ("page_up",),
    "page up": ("page_up",),
    "pagedown": ("page_down",),
    "page down": ("page_down",),
    # Arrows
    "left": ("left",),
    "right": ("right",),
    "up": ("up",),
    "down": ("down",),
    # Punctuation by name
    "question": ("?",),
    "minus": ("-",),
    "plus": ("+",),
    "period": (".",),
    "comma": (",",),
    "slash": ("/",),
    "backslash": ("\\",),
    "semicolon": (";",),
    "quote": ("'",),
    "apostrophe": ("'",),
    "tilde": ("`",),
    "grave": ("`",),
    # Mouse — these map to mouse_button events, not key events
    "mouseleft": ("@mouse:left",),
    "mouseright": ("@mouse:right",),
    "mousemiddle": ("@mouse:middle",),
    "mouse4": ("@mouse:x1",),
    "mouse5": ("@mouse:x2",),
    # Things we cannot resolve to discrete events — bind to a sentinel that will never match
    "mousex": ("@unbindable",),
    "mousey": ("@unbindable",),
    "mousescrollup": ("@unbindable",),
    "mousescrolldown": ("@unbindable",),
    "mouse": ("@unbindable",),
}

# Generate F1..F24
for i in range(1, 25):
    KEY_ALIASES[f"f{i}"] = (f"f{i}",)


def normalize_key_token(raw: str) -> tuple[str, ...]:
    """Resolve a keybind literal (e.g. 'W', 'LeftShift', 'MouseRight', 'Spacebar')
    into one or more event tokens. Keyboard tokens come back as lowercase strings;
    mouse-button tokens come back prefixed with '@mouse:'.
    """
    s = raw.strip()
    low = s.lower()
    if low in KEY_ALIASES:
        return KEY_ALIASES[low]
    # Single printable char — lowercase it (W -> w, ? -> ?)
    if len(s) == 1:
        return (s.lower(),)
    # Strip a "left "/"right " or "l"/"r" prefix and retry
    return (low,)


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip())
    return cleaned.strip("_")


def session_pairs(src: Path) -> list[tuple[str, Path, Path | None]]:
    """Return (slug, inputs_path, keybind_path_or_none) for each pair.
    Inputs files are authoritative; the matching KeyBind is found by stripping
    the ' - Inputs[ (n)].jsonl' suffix from the filename and appending ' - KeyBind.json'.
    """
    pairs: list[tuple[str, Path, Path | None]] = []
    for inp in sorted(src.glob("* - Inputs*.jsonl")):
        name = inp.name
        # Strip " - Inputs.jsonl" or " - Inputs (1).jsonl"
        base = re.sub(r" - Inputs(?: \(\d+\))?\.jsonl$", "", name)
        # Suffix for duplicates: "(1)", "(2)"
        m = re.search(r" - Inputs \((\d+)\)\.jsonl$", name)
        dup = f"_v{m.group(1)}" if m else ""
        kb = src / f"{base} - KeyBind.json"
        slug = slugify(base) + dup
        pairs.append((slug, inp, kb if kb.exists() else None))
    return pairs


def load_keybind(path: Path | None) -> dict:
    if path is None:
        return {}
    with path.open() as f:
        return json.load(f)


def build_resolver(keybind: dict) -> list[tuple[tuple[str, ...], str]]:
    """Flatten keybind into a list of (required_event_tokens, semantic_action).
    A binding "fires" for a frame when ALL its required tokens are present in
    the held set for that frame. Combo bindings (modifier+key) thus need both.
    """
    rules: list[tuple[tuple[str, ...], str]] = []
    for semantic, value in keybind.items():
        groups = _collect_binding_targets(value)
        for group in groups:
            rules.append((group, semantic))
    return rules


def _collect_binding_targets(value) -> list[tuple[str, ...]]:
    """A binding value may be:
      - a literal string ("W")  -> [("w",)]
      - a list of literals ["W", "A"]  -> each becomes its own group
      - a combo dict {"modifier": "Control", "key": "Q"}  -> one group with both
    Each returned group is a tuple of event tokens that must ALL be held.
    Where an alias expands to multiple tokens (bare "Shift" -> shift|shift_l|shift_r),
    we emit one group per expansion (OR-ed).
    """
    out: list[tuple[str, ...]] = []

    def _expand_one(literal: str) -> list[tuple[str, ...]]:
        toks = normalize_key_token(literal)
        # Each token in `toks` is an alternative; emit one single-element group per.
        return [(t,) for t in toks]

    if isinstance(value, str):
        out.extend(_expand_one(value))
    elif isinstance(value, list):
        for item in value:
            out.extend(_collect_binding_targets(item))
    elif isinstance(value, dict):
        # Combo: cross-product of modifier alternatives × key alternatives
        mod_lits = value.get("modifier")
        key_lit = value.get("key")
        mod_groups = _collect_binding_targets(mod_lits) if mod_lits else [()]
        key_groups = _collect_binding_targets(key_lit) if key_lit else [()]
        for m in mod_groups:
            for k in key_groups:
                combined = tuple(sorted(set(m + k)))
                if combined:
                    out.append(combined)
    return out


def resolve_actions(held: set[str], rules) -> list[str]:
    """Return the unique list of semantic actions whose required token-groups
    are entirely contained in the held-set. Preserves keybind insertion order
    by deduping with a dict."""
    fired: dict[str, None] = {}
    for required, semantic in rules:
        if all(t in held for t in required):
            fired[semantic] = None
    return list(fired.keys())


def invert_keybind(keybind: dict) -> dict:
    """Spec example shows literal -> semantic. The vendor file is semantic -> literal(s).
    Produce a literal -> [semantic, ...] map (lists because multiple semantics can
    share a literal, e.g. PoE 'show_advanced_item_descriptions' and 'highlight_items_and_objects'
    both bind to 'Alt'). Combo bindings are encoded as 'Modifier+Key' string keys.
    """
    inv: dict[str, list[str]] = defaultdict(list)

    def _add(literal: str, semantic: str):
        if semantic not in inv[literal]:
            inv[literal].append(semantic)

    for semantic, value in keybind.items():
        if isinstance(value, str):
            _add(value, semantic)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    _add(item, semantic)
                elif isinstance(item, dict):
                    mod = item.get("modifier", "")
                    key = item.get("key", "")
                    _add(f"{mod}+{key}", semantic)
        elif isinstance(value, dict):
            mod = value.get("modifier", "")
            key = value.get("key", "")
            _add(f"{mod}+{key}", semantic)
    # Sort keys for stable output
    return {k: inv[k] for k in sorted(inv)}


def translate_session(slug: str, inputs_path: Path, keybind_path: Path | None) -> dict:
    """Bin events to 30 fps; emit frames.csv + key_binding.json into translated/<slug>/.
    Returns a small stats dict for the run summary."""
    keybind = load_keybind(keybind_path)
    rules = build_resolver(keybind)

    # First pass: load events, find max timestamp
    events: list[dict] = []
    max_t = 0
    n_wheel = 0
    n_focus = 0
    with inputs_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("t")
            if not isinstance(t, int):
                continue
            etype = ev.get("type")
            if etype == "mouse_wheel":
                n_wheel += 1
                continue  # user said drop wheel
            if etype == "focus":
                n_focus += 1
                # We keep all rows regardless of focus state, but we still need
                # to keep these events out of held-state tracking.
                continue
            if etype not in ("key", "mouse_button", "mouse_raw"):
                continue
            events.append(ev)
            if t > max_t:
                max_t = t

    events.sort(key=lambda e: e["t"])

    n_frames = int(max_t // FRAME_US) + 1 if events else 0

    # Per-frame accumulators
    keys_press_in_frame: list[set[str]] = [set() for _ in range(n_frames)]
    buttons_press_in_frame: list[set[str]] = [set() for _ in range(n_frames)]
    dx_sum = [0] * n_frames
    dy_sum = [0] * n_frames

    # Walk events with rolling held-state. A key/button is "held during frame f"
    # if it was down at any point during that frame's timespan.
    keys_down: set[str] = set()
    buttons_down: set[str] = set()
    # Tracks the frame at which we last updated held snapshots
    cur_frame = 0
    # Mark frames passed through (with no event) as still holding whatever was down
    def _flush_held_through(target_frame: int):
        nonlocal cur_frame
        while cur_frame < target_frame and cur_frame < n_frames:
            keys_press_in_frame[cur_frame] |= keys_down
            buttons_press_in_frame[cur_frame] |= buttons_down
            cur_frame += 1

    for ev in events:
        f_idx = int(ev["t"] // FRAME_US)
        if f_idx >= n_frames:
            f_idx = n_frames - 1
        _flush_held_through(f_idx)
        etype = ev["type"]
        if etype == "key":
            k = ev.get("key", "")
            keys_press_in_frame[f_idx].add(k)  # include even if down-then-up in same frame
            if ev.get("action") == "down":
                keys_down.add(k)
            else:
                keys_down.discard(k)
        elif etype == "mouse_button":
            b = ev.get("button", "")
            buttons_press_in_frame[f_idx].add(b)
            if ev.get("action") == "down":
                buttons_down.add(b)
            else:
                buttons_down.discard(b)
        elif etype == "mouse_raw":
            dx_sum[f_idx] += int(ev.get("dx", 0) or 0)
            dy_sum[f_idx] += int(ev.get("dy", 0) or 0)

    # Tail: extend held state through remaining frames
    _flush_held_through(n_frames)

    # Write outputs
    out_dir = DST / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    frames_path = out_dir / "frames.csv"
    with frames_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FRAME_COLS)
        empty_camera = [""] * (len(C2W_COLS) + len(CAMERA_COLS))
        for f_idx in range(n_frames):
            keys = sorted(keys_press_in_frame[f_idx])
            btns = sorted(buttons_press_in_frame[f_idx])
            # Build held-token set for resolver: keyboard literal + @mouse:<button>
            held_tokens = set(keys) | {f"@mouse:{b}" for b in btns}
            actions = resolve_actions(held_tokens, rules) if rules else []
            ts_ms = int(round(f_idx * (1000.0 / FPS)))
            w.writerow(
                [f_idx, ts_ms]
                + empty_camera
                + [
                    "|".join(keys),
                    "|".join(actions),
                    "|".join(btns),
                    dx_sum[f_idx],
                    dy_sum[f_idx],
                ]
            )

    # key_binding.json — spec-style literal -> semantic(s)
    kb_path = out_dir / "key_binding.json"
    with kb_path.open("w") as f:
        json.dump(invert_keybind(keybind), f, indent=2)

    return {
        "slug": slug,
        "frames": n_frames,
        "events": len(events),
        "dropped_wheel": n_wheel,
        "focus_events": n_focus,
        "rules": len(rules),
        "duration_s": round(max_t / 1_000_000, 1),
        "has_keybind": keybind_path is not None,
    }


def main():
    DST.mkdir(parents=True, exist_ok=True)
    pairs = session_pairs(SRC)
    print(f"Found {len(pairs)} sessions to translate.")
    missing_kb: list[str] = []
    total_frames = 0
    total_events = 0
    for slug, inp, kb in pairs:
        try:
            stats = translate_session(slug, inp, kb)
            total_frames += stats["frames"]
            total_events += stats["events"]
            kb_flag = "" if stats["has_keybind"] else "  [NO KEYBIND]"
            print(
                f"  {slug:40s}  frames={stats['frames']:6d}  "
                f"events={stats['events']:6d}  dur={stats['duration_s']:6.1f}s  "
                f"rules={stats['rules']:3d}{kb_flag}"
            )
            if not stats["has_keybind"]:
                missing_kb.append(slug)
        except Exception as e:
            print(f"  {slug}: FAILED -- {e!r}")
    print(
        f"\nDone. {len(pairs)} sessions  |  total frames={total_frames:,}  |  total events={total_events:,}"
    )
    if missing_kb:
        print(f"Sessions with no matching KeyBind.json ({len(missing_kb)}): " + ", ".join(missing_kb))


if __name__ == "__main__":
    main()
