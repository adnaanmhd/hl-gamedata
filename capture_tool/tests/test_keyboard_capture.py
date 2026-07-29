from app.core.keyboard_capture import (
    _ModifierDebounce,
    _base_letter_from_control_byte,
    _resolve_modifier_side,
)


def test_shift_side_from_scancode():
    assert _resolve_modifier_side(vk=0x10, scan_code=0x2A, flags=0) == "shift_l"
    assert _resolve_modifier_side(vk=0x10, scan_code=0x36, flags=0) == "shift_r"


def test_ctrl_side_from_extended_bit():
    assert _resolve_modifier_side(vk=0x11, scan_code=0x1D, flags=0) == "ctrl_l"
    assert _resolve_modifier_side(vk=0x11, scan_code=0x1D, flags=1) == "ctrl_r"


def test_alt_side_from_extended_bit():
    assert _resolve_modifier_side(vk=0x12, scan_code=0x38, flags=0) == "alt_l"
    assert _resolve_modifier_side(vk=0x12, scan_code=0x38, flags=1) == "alt_r"


def test_unrelated_scancode_returns_none():
    assert _resolve_modifier_side(vk=0x57, scan_code=0x11, flags=0) is None


def test_control_byte_maps_to_base_letter():
    assert _base_letter_from_control_byte("\x17") == "w"  # Ctrl+W
    assert _base_letter_from_control_byte("\x01") == "a"  # Ctrl+A
    assert _base_letter_from_control_byte("\x1a") == "z"  # Ctrl+Z


def test_control_byte_out_of_range_is_none():
    assert _base_letter_from_control_byte("\x1c") is None


def test_modifier_debounce_suppresses_opposite_side_bleed():
    d = _ModifierDebounce()
    d.note_down("shift_l", 100.000)
    # Right shift "down" arriving 10ms later is a bleed artifact — suppress.
    assert d.should_suppress_down("shift_r", 100.010) is True


def test_modifier_debounce_allows_after_window():
    d = _ModifierDebounce()
    d.note_down("shift_l", 100.000)
    # 200ms later is a genuine, deliberate two-hand press — allow it.
    assert d.should_suppress_down("shift_r", 100.200) is False


def test_modifier_debounce_unrelated_pair_not_suppressed():
    d = _ModifierDebounce()
    d.note_down("shift_l", 100.000)
    assert d.should_suppress_down("ctrl_r", 100.001) is False


def test_modifier_debounce_note_up_clears_state():
    d = _ModifierDebounce()
    d.note_down("shift_l", 100.000)
    d.note_up("shift_l")
    assert d.should_suppress_down("shift_r", 100.001) is False
