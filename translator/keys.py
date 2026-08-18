"""Canonical key vocabulary.

ONE vocabulary is used everywhere — frames.csv `input_keys`, key_binding.json
keys, and action resolution — so they always line up (fixes the
frames.csv `ctrl_l` vs key_binding `LCtrl` mismatch). Convention: lowercase
snake_case.

Two jobs:
  1. normalize a raw event-key token from inputs.jsonl  -> canonical or DROP
  2. normalize a keybind literal ("LCtrl", "Spacebar")  -> canonical
"""
from __future__ import annotations

import re

# --- Mouse button tokens (shared by input_mouse_buttons + key_binding) -------
MOUSE_BUTTONS = {"left": "mouse_left", "right": "mouse_right", "middle": "mouse_middle",
                 "x1": "mouse_x1", "x2": "mouse_x2"}

# --- OS / system keys that pollute input_keys (stripped unless explicitly bound)
#     Client feedback: "cmd, media volume keys, print_screen, vk_255" pollute the
#     input_keys column. We strip aggressively per project guideline.
_OS_KEYS = frozenset({
    # super / meta
    "cmd", "cmd_l", "cmd_r", "win", "win_l", "win_r", "super", "super_l", "super_r",
    "lwin", "rwin", "menu", "apps",
    # media
    "media_volume_up", "media_volume_down", "media_volume_mute",
    "volume_up", "volume_down", "volume_mute", "media_play_pause", "media_play",
    "media_pause", "media_next", "media_previous", "media_prev", "media_stop",
    "media_play_pause_button",
    # print / lock / misc system
    "print_screen", "prtsc", "snapshot", "sys_req",
    "caps_lock", "num_lock", "scroll_lock", "insert", "pause", "break",
})
_FKEYS = frozenset(f"f{i}" for i in range(1, 25))
_AGGRESSIVE_STRIP = _OS_KEYS | _FKEYS  # caps/num/scroll/insert + F-keys stripped unless bound

# --- Keybind-literal aliases: vendor naming -> canonical event token ----------
_LITERAL_ALIASES: dict[str, str] = {
    "leftshift": "shift_l", "lshift": "shift_l", "left shift": "shift_l",
    "rightshift": "shift_r", "rshift": "shift_r", "right shift": "shift_r",
    "leftctrl": "ctrl_l", "lctrl": "ctrl_l", "left ctrl": "ctrl_l", "control": "ctrl",
    "rightctrl": "ctrl_r", "rctrl": "ctrl_r", "right ctrl": "ctrl_r",
    "leftalt": "alt_l", "lalt": "alt_l", "left alt": "alt_l",
    "rightalt": "alt_r", "ralt": "alt_r", "right alt": "alt_r", "altgr": "alt_gr",
    "escape": "esc", "esc": "esc",
    "spacebar": "space", "space bar": "space", "space": "space",
    "return": "enter", "del": "delete", "ins": "insert",
    "pageup": "page_up", "page up": "page_up", "pagedown": "page_down", "page down": "page_down",
    "capslock": "caps_lock",
    "mouseleft": "mouse_left", "mouseright": "mouse_right", "mousemiddle": "mouse_middle",
    "mouse4": "mouse_x1", "mouse5": "mouse_x2",
    "mousex": "mouse_x", "mousey": "mouse_y", "mouse": "mouse",
    "mousescrollup": "mouse_scroll_up", "mousescrolldown": "mouse_scroll_down",
}

# Modifier pairs that must never both fire on one frame (capture artifact).
MODIFIER_PAIRS = (("shift_l", "shift_r"), ("ctrl_l", "ctrl_r"), ("alt_l", "alt_r"))

_VK_RE = re.compile(r"^(vk_?\d+|<\d+>|0x[0-9a-f]+)$")


def normalize_event_key(raw: str, *, bound: frozenset[str] = frozenset(),
                        aggressive: bool = True) -> str | None:
    """Raw inputs.jsonl key -> canonical token, or None to DROP.

    Drops: control bytes, unknown vk_### codes, and (unless bound in the
    session keybind) OS/system + lock + F keys.
    """
    if not isinstance(raw, str):
        # a numeric or container key code from a hand-edited/foreign-tool
        # sidecar raised AttributeError straight out of bin_session
        # (r-loop 7); None is the same answer an unrecognised key gets
        return None
    s = raw.strip().lower()
    if not s:
        return None
    # Control bytes (Ctrl+letter artifacts on Windows: U+0001..U+001F)
    if len(s) == 1 and ord(s) < 32:
        return None
    if _VK_RE.match(s):
        return None
    s = _LITERAL_ALIASES.get(s, s)
    strip_set = _AGGRESSIVE_STRIP if aggressive else _OS_KEYS
    if s in strip_set and s not in bound:
        return None
    return s


def normalize_literal(raw) -> str:
    """Keybind literal ("LCtrl", "Spacebar", "MouseRight") -> canonical token.

    Non-str input returns "" instead of raising: the `{modifier, key}`
    keybind form carries Windows VK numbers in the wild, and r-loop 7's
    resolve_keybind hardening covered only the flat form — `raw.strip()`
    on a number crashed the translate. Callers (_binding_groups) drop
    empty tokens so "" can never enter bound_literals (r-loop 8)."""
    if not isinstance(raw, str):
        return ""
    s = raw.strip().lower()
    return _LITERAL_ALIASES.get(s, s)


def is_motion_literal(token: str) -> bool:
    """True for mouse-motion binds that map to dx/dy, not a discrete key."""
    return token in {"mouse", "mouse_x", "mouse_y"}
