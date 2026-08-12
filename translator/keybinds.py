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


# --------------------------------------------------------------------------- #
# Per-game context gating (customer feedback 2026-07 via Jack Davis: a key with
# >1 conditional action must emit only the action the character is performing).
#
# Semantics listed here fire ONLY in the given contexts (translator/context.py
# classifies frames from the video); semantics NOT listed are ungated — they
# keep firing in every context, deliberately, so we never invent strips beyond
# the video evidence. Contexts: on_foot, suit, dialogue, model_ship, cockpit,
# map, pause_menu.
#
# Evidence (2026-06-06 sessions, frame-verified against in-game prompts):
#   cockpit "Unbuckle [E]" -> E stays interact; F toggles ship HEADLIGHTS ->
#   flashlight stays ungated; map "Zoom View [LShift/LCtl]" -> thrust gated
#   out of map; model console "Down/Up Thrust [LCtl/LShift]" -> thrust live in
#   model_ship; suit jetpack shares the flight thrust binds (approved 07-31).
#   model_ship Space = flight_match_velocity is UNVERIFIED (no press exists in
#   any sample; the model is flown with the ship control set).
_WORLD_LIVE = frozenset({"on_foot", "suit", "cockpit", "model_ship", "map"})

CONTEXT_ALLOWED: dict[str, dict[str, frozenset[str]]] = {
    "outer_wilds": {
        "general_confirm":               frozenset({"dialogue", "pause_menu"}),
        "general_primary_interact":      frozenset({"on_foot", "suit", "cockpit"}),
        "general_secondary_interact":    frozenset({"on_foot", "suit"}),
        "movement_jump":                 frozenset({"on_foot", "suit"}),
        "movement_jetpack_boost":        frozenset({"suit"}),
        "equipment_secondary_tool_action": frozenset({"on_foot", "suit"}),
        "flight_match_velocity":         frozenset({"cockpit", "model_ship"}),
        "flight_up_thrust":              frozenset({"cockpit", "model_ship", "suit"}),
        "flight_down_thrust":            frozenset({"cockpit", "model_ship", "suit"}),
        "flight_roll_mode":              frozenset({"cockpit"}),
        "flight_landing_camera":         frozenset({"cockpit"}),
        "flight_autopilot":              frozenset({"cockpit"}),
        "flight_cockpit_free_look":      frozenset({"cockpit"}),
        "flight_lock_on":                frozenset({"cockpit", "map"}),
        # dialogue/pause freeze world input (approved 07-31): movement/look axes
        # and tool/button actions go dead there. They stay live in map — the
        # map's own prompts bind WASD to Pan View and the mouse to Rotate View.
        "movement_move_x_axis":          _WORLD_LIVE,
        "movement_move_y_axis":          _WORLD_LIVE,
        "movement_look_x_axis":          _WORLD_LIVE,
        "movement_look_y_axis":          _WORLD_LIVE,
        "equipment_primary_tool_action": _WORLD_LIVE,
        "equipment_retrieve_scout":      _WORLD_LIVE,
        "equipment_tool_x_axis":         _WORLD_LIVE,
        "equipment_tool_y_axis":         _WORLD_LIVE,
        "equipment_signalscope":         _WORLD_LIVE,
    },
}

# Exclusive pair that can survive gating on ONE literal in ONE context:
# suit-context Space = jump (press on the ground) vs jetpack boost (airborne).
# Collapsed per held-run: tap -> jump, hold -> boost, overridable with
# video-verified per-run labels (keybind.collapse_ambiguous_runs).
AMBIGUOUS_PAIRS: dict[str, tuple[str, str]] = {
    "outer_wilds": ("movement_jump", "movement_jetpack_boost"),
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
