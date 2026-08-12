"""Context-gated action resolution (customer feedback: no same-literal fan-out)."""
from translator.keybind import (build_resolver, collapse_ambiguous_runs,
                                resolve_actions)
from translator.keybinds import AMBIGUOUS_PAIRS, CONTEXT_ALLOWED, KEYBINDS

OW = KEYBINDS["outer_wilds"]
OW_ALLOWED = CONTEXT_ALLOWED["outer_wilds"]
RULES = build_resolver(OW)


def _acts(held, ctx, motion=(False, False)):
    acts, dead = resolve_actions(set(held), motion, RULES,
                                 context=ctx, allowed=OW_ALLOWED)
    return acts, dead


def test_space_resolves_per_context():
    acts, dead = _acts({"space"}, "on_foot")
    assert acts == ["movement_jump"] and not dead
    acts, dead = _acts({"space"}, "cockpit")
    assert acts == ["flight_match_velocity"] and not dead
    acts, dead = _acts({"space"}, "dialogue")     # Space does nothing in dialogue
    assert acts == [] and dead == {"space"}


def test_space_in_suit_is_ambiguous_pair_then_collapsed():
    acts, dead = _acts({"space"}, "suit")
    assert set(acts) == {"movement_jump", "movement_jetpack_boost"} and not dead
    pair = AMBIGUOUS_PAIRS["outer_wilds"]
    # 3-frame tap -> jump; 20-frame hold -> boost; override wins over heuristic
    per_frame = [list(acts) for _ in range(30)]
    for i in (3, 4, 5, 6):      # gap between runs
        per_frame[i] = []
    chosen = collapse_ambiguous_runs(per_frame, pair, fps=30.0)
    assert chosen[0] == "movement_jump" and per_frame[0] == ["movement_jump"]
    assert chosen[7] == "movement_jetpack_boost"
    per_frame2 = [list(a) for a in [["movement_jump", "movement_jetpack_boost"]] * 3]
    chosen2 = collapse_ambiguous_runs(per_frame2, pair, fps=30.0,
                                      overrides={0: "movement_jetpack_boost"})
    assert chosen2[0] == "movement_jetpack_boost"


def test_e_confirm_vs_interact_and_r_dead_in_dialogue():
    acts, dead = _acts({"e"}, "dialogue")
    assert acts == ["general_confirm"] and not dead
    acts, dead = _acts({"e"}, "on_foot")
    assert acts == ["general_primary_interact"]
    acts, dead = _acts({"r"}, "dialogue")         # customer's R-mash case
    assert acts == [] and dead == {"r"}
    acts, dead = _acts({"r"}, "cockpit")
    assert acts == ["flight_roll_mode"]
    acts, dead = _acts({"r"}, "on_foot")
    assert acts == ["equipment_secondary_tool_action"]


def test_shift_thrust_contexts_and_map_zoom_dead():
    for ctx in ("cockpit", "model_ship", "suit"):
        acts, dead = _acts({"shift_l"}, ctx)
        assert acts == ["flight_up_thrust"], ctx
    acts, dead = _acts({"shift_l"}, "map")        # map Shift = Zoom View: no semantic
    assert acts == [] and dead == {"shift_l"}
    acts, dead = _acts({"shift_l"}, "on_foot")    # campfire "Extend Stick": no semantic
    assert acts == [] and dead == {"shift_l"}


def test_ungated_semantics_fire_everywhere():
    for ctx in ("on_foot", "dialogue", "cockpit", "model_ship", "pause_menu"):
        acts, _ = _acts({"esc", "q"}, ctx)
        assert "general_pause" in acts and "general_cancel" in acts


def test_dialogue_and_pause_freeze_world_input():
    # movement axes / look axes / buttons are dead while the world is frozen
    for ctx in ("dialogue", "pause_menu"):
        acts, dead = _acts({"w", "mouse_left"}, ctx, motion=(True, True))
        assert "movement_move_y_axis" not in acts
        assert "equipment_primary_tool_action" not in acts
        assert not any(a.startswith("movement_look") for a in acts)
        assert dead == {"w", "mouse_left"}
    # but they stay live in the map view (Pan View / Rotate View prompts)
    acts, dead = _acts({"w"}, "map", motion=(True, False))
    assert "movement_move_y_axis" in acts
    assert "movement_look_x_axis" in acts and not dead


def test_no_context_means_legacy_behaviour():
    acts, dead = resolve_actions({"space"}, (False, False), RULES)
    assert set(acts) == {"movement_jump", "movement_jetpack_boost",
                         "flight_match_velocity"}
    assert not dead


def test_look_axes_fire_per_axis():
    acts, _ = resolve_actions(set(), (True, False), RULES,
                              context="on_foot", allowed=OW_ALLOWED)
    assert "movement_look_x_axis" in acts and "movement_look_y_axis" not in acts
    acts, _ = resolve_actions(set(), (False, True), RULES,
                              context="on_foot", allowed=OW_ALLOWED)
    assert "movement_look_y_axis" in acts and "movement_look_x_axis" not in acts
