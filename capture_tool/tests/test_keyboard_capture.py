import asyncio
import time

import pynput.keyboard as keyboard

from app.core.keyboard_capture import (
    InputCapture,
    _AltGrPhantomCtrlDetector,
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


class TestAltGrPhantomCtrlDetector:
    def test_logs_warning_when_ctrl_l_immediately_precedes_alt_r(self, caplog):
        import logging
        d = _AltGrPhantomCtrlDetector()
        with caplog.at_level(logging.WARNING, logger="app.core.keyboard_capture"):
            d.note_down("ctrl_l", 100.000)
            d.note_down("alt_r", 100.003)
        assert any("AltGr" in r.message for r in caplog.records)

    def test_no_warning_when_gap_exceeds_the_tight_window(self, caplog):
        import logging
        d = _AltGrPhantomCtrlDetector()
        with caplog.at_level(logging.WARNING, logger="app.core.keyboard_capture"):
            d.note_down("ctrl_l", 100.000)
            d.note_down("alt_r", 100.050)
        assert not any("AltGr" in r.message for r in caplog.records)

    def test_no_warning_when_other_key_intervenes(self, caplog):
        import logging
        d = _AltGrPhantomCtrlDetector()
        with caplog.at_level(logging.WARNING, logger="app.core.keyboard_capture"):
            d.note_down("ctrl_l", 100.000)
            d.note_other_key()
            d.note_down("alt_r", 100.003)
        assert not any("AltGr" in r.message for r in caplog.records)

    def test_no_warning_for_alt_r_alone(self, caplog):
        import logging
        d = _AltGrPhantomCtrlDetector()
        with caplog.at_level(logging.WARNING, logger="app.core.keyboard_capture"):
            d.note_down("alt_r", 100.000)
        assert not any("AltGr" in r.message for r in caplog.records)


class TestInputCaptureNoDoubleRecording:
    async def _drain_queue(self, capture):
        await asyncio.sleep(0)
        events = []
        while not capture.queue.empty():
            events.append(await capture.queue.get())
        return events

    def _run(self, coro):
        return asyncio.run(coro)

    def test_os_key_repeat_emits_only_one_down_event(self):
        async def scenario():
            capture = InputCapture(anchor_monotonic=time.perf_counter())
            capture._loop = asyncio.get_running_loop()
            key = keyboard.KeyCode.from_char("w")
            for _ in range(5):
                capture._on_press(key)
            return await self._drain_queue(capture)

        events = self._run(scenario())
        downs = [e for e in events if e["type"] == "key" and e["action"] == "down"]
        assert len(downs) == 1
        assert downs[0]["key"] == "w"

    def test_release_then_press_again_emits_a_new_down(self):
        async def scenario():
            capture = InputCapture(anchor_monotonic=time.perf_counter())
            capture._loop = asyncio.get_running_loop()
            key = keyboard.KeyCode.from_char("w")
            capture._on_press(key)
            capture._on_release(key)
            capture._on_press(key)
            return await self._drain_queue(capture)

        events = self._run(scenario())
        downs = [e for e in events if e["type"] == "key" and e["action"] == "down"]
        assert len(downs) == 2

    def test_disabled_capture_drops_events(self):
        async def scenario():
            capture = InputCapture(anchor_monotonic=time.perf_counter())
            capture._loop = asyncio.get_running_loop()
            capture.set_enabled(False)
            capture._on_press(keyboard.KeyCode.from_char("w"))
            return await self._drain_queue(capture)

        assert self._run(scenario()) == []
