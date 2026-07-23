"""Per-game keybind fallbacks (semantic_action -> literal[s]).

For NEW samples the capture tool / contributor supplies `keybind.json` per
session, which is authoritative. These built-ins exist so the existing sample
sessions (kamla, outer_wilds) can be reprocessed without re-deriving binds.
Derived from the contributors' in-game control screens.
"""
from __future__ import annotations

import re

KEYBINDS: dict[str, dict] = {
    "kamla": {
        "interact": "e",
        "pause_menu": "esc",
        "move_up": "w",
        "move_left": "a",
        "move_down": "s",
        "move_right": "d",
        "look": "mouse",
    },
    "outer_wilds": {
        # General
        "general_pause": "esc",
        "general_cancel": "q",
        # Enter is the documented keyboard Confirm key (menu/dialogue advance);
        # contributors' keybind.json files list only "e".
        "general_confirm": ["e", "enter"],
        "general_primary_interact": "e",
        "general_secondary_interact": "c",
        "general_view_map": "tab",
        "general_flashlight": "f",
        # Movement
        "movement_move_x_axis": ["a", "d"],
        "movement_move_y_axis": ["s", "w"],
        "movement_look_x_axis": "mouse_x",
        "movement_look_y_axis": "mouse_y",
        "movement_jump": "space",
        "movement_jetpack_boost": "space",
        # Equipment
        "equipment_signalscope": "y",
        "equipment_retrieve_scout": "mouse_right",
        "equipment_primary_tool_action": "mouse_left",
        "equipment_secondary_tool_action": "r",
        "equipment_tool_x_axis": ["1", "4"],
        "equipment_tool_y_axis": ["2", "3"],
        # Flight
        "flight_up_thrust": "shift_l",
        "flight_down_thrust": "ctrl_l",
        "flight_roll_mode": "r",
        "flight_lock_on": "mouse_middle",
        "flight_match_velocity": "space",
        "flight_landing_camera": "c",
        "flight_autopilot": "x",
        "flight_cockpit_free_look": "alt_l",
    },
}


def _collapse(s: str) -> str:
    """Lowercase, strip every non-alphanumeric — for fuzzy game-name matching."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def game_key_from_name(name: str, exe_name: str | None = None) -> str | None:
    """Map a free-form game name ('Outer Wilds') to a KEYBINDS slug.

    The capture metadata frequently mistypes the display name ('Outerworld',
    'Outerwild', 'Outer wild'), so we (1) compare with separators collapsed and
    (2) fall back to the exe name ('OuterWilds.exe'), which is far more reliable.
    """
    norms = {k: _collapse(k) for k in KEYBINDS}
    candidates = [name or ""]
    if exe_name:
        candidates.append(re.sub(r"\.exe$", "", exe_name, flags=re.IGNORECASE))
    for cand in candidates:
        c = _collapse(cand)
        if not c:
            continue
        for k, nk in norms.items():
            if c == nk or nk in c or c in nk:
                return k
    return None
