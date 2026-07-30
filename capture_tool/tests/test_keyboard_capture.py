import pynput.keyboard as keyboard

from app.core.keyboard_capture import (
    _ModifierDebounce,
    _base_letter_from_control_byte,
    _key_to_str,
    _resolve_modifier_side,
)


class _FakeNamedKey:
    """A stand-in for a pynput `Key` enum member: has `.name`, no `.vk`, and
    is not a `KeyCode` instance. Used instead of e.g. `keyboard.Key.
    print_screen` directly because pynput's per-platform backend doesn't
    define every Windows-only member (print_screen, media_*, ...) on every
    OS — this repo's tests run on macOS — so referencing them by attribute
    would fail here even though they're exactly what a real Windows capture
    delivers, which is the actual bug being regression-tested."""

    def __init__(self, name: str) -> None:
        self.name = name


def test_key_to_str_drops_os_system_keys_delivered_as_named_key_enum():
    """Real bug: on Windows, Win/cmd, PrintScreen, lock keys, and media keys
    arrive from pynput as named `Key` enum members (Key.cmd, Key.print_screen,
    ...), which have no `.vk` at all — the vk-keyed `_OS_SYSTEM_VKS` drop set
    never applied to them. A real capture's inputs.jsonl had `cmd` and
    `print_screen` as its first two events, completely unfiltered."""
    assert _key_to_str(_FakeNamedKey("cmd"), side_hint=None) is None
    assert _key_to_str(_FakeNamedKey("print_screen"), side_hint=None) is None
    assert _key_to_str(_FakeNamedKey("caps_lock"), side_hint=None) is None
    assert _key_to_str(_FakeNamedKey("media_volume_mute"), side_hint=None) is None


def test_key_to_str_keeps_ordinary_special_keys():
    assert _key_to_str(keyboard.Key.space, side_hint=None) == "space"
    assert _key_to_str(keyboard.Key.esc, side_hint=None) == "esc"


def test_key_to_str_keeps_printable_chars():
    assert _key_to_str(keyboard.KeyCode.from_char("w"), side_hint=None) == "w"


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
