"""r-loop 5: the keybind direction detector must be right in BOTH
directions. r-loop 4 fixed one failure mode and introduced its mirror
image."""
from translator.keybinds import KEYBINDS
from translator.translate import (_as_semantic_to_literal, _looks_semantic,
                                  invert_keybind)


def test_vocabulary_beats_the_underscore_shortcut():
    """'interact' and 'look' are real Kamla action names with no
    underscore. The old order returned False before the vocabulary test
    could ever see them."""
    assert "interact" in {s for kb in KEYBINDS.values() for s in kb}
    assert _looks_semantic("interact")
    assert _looks_semantic("look")


def test_inverted_kamla_keybind_is_flipped_back():
    """The regression r-loop 4 introduced: an inverted Kamla file (the
    literal->[semantic] shape our OWN v1 key_binding.json deliverable
    has) was left un-flipped, so no real key token was ever bound.
    input_keys/input_actions then shipped empty on every row and the
    session was rejected CNT_ACTIONS_FEW + INP_KEYS_MISSING -- both
    unfixable, no fix attempt, player coached to 'play actively' for our
    parser bug."""
    inverted = invert_keybind(KEYBINDS["kamla"])
    assert inverted != KEYBINDS["kamla"]
    assert _as_semantic_to_literal(inverted) == KEYBINDS["kamla"]


def test_correctly_oriented_kamla_keybind_is_left_alone():
    """The other direction: flipping a correct file is equally fatal."""
    correct = dict(KEYBINDS["kamla"])
    assert _as_semantic_to_literal(correct) == KEYBINDS["kamla"]


def test_r_loop4_multibind_file_is_still_not_flipped():
    """Do not regress the case r-loop 4 fixed: a correctly-oriented file
    whose entries are all multi-bind lists. Literals like 'a'/'d' are
    neither in the vocabulary nor underscored, so it must not flip."""
    r4 = {"movement_move_x_axis": ["a", "d"],
          "movement_move_y_axis": ["w", "s"]}
    assert _as_semantic_to_literal(r4) == r4


def test_outer_wilds_both_directions_unaffected():
    """OW was always immune (all 27 action names carry an underscore) --
    prove the fix did not disturb it."""
    assert _as_semantic_to_literal(
        invert_keybind(KEYBINDS["outer_wilds"])) == KEYBINDS["outer_wilds"]
    assert _as_semantic_to_literal(
        dict(KEYBINDS["outer_wilds"])) == KEYBINDS["outer_wilds"]
