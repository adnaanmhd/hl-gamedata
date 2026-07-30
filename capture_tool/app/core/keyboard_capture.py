"""
Keyboard + mouse-button + mouse-wheel capture via pynput.

pynput installs a Windows low-level global hook for keyboard/mouse, so we see
events even when the game has focus. We run the listeners on background
threads (pynput's design); they push events into an asyncio.Queue that the
session engine drains.

Why not Windows SetWindowsHookEx directly? pynput is well-maintained,
MIT-licensed, ~600KB, and abstracts the gnarly LL_KEYBOARD_HOOK lifetime
management. For raw mouse movement we DO use Windows Raw Input directly
(raw_mouse.py) because pynput doesn't expose dx/dy from Raw Input — it gives
cursor positions, which the game isn't reading.

Events emitted to the queue use the schema:
    {"t": <us_since_anchor>, "type": "key", "key": "<name>", "action": "down"|"up"}
    {"t": <us>, "type": "mouse_button", "button": "left"|"right"|"middle"|"x1"|"x2", "action": "down"|"up"}
    {"t": <us>, "type": "mouse_wheel", "dy": <int>}

--- Fixes applied here (see HumynCapture_Capture_Tool_Issues.md, Group B) ---

B3 (inconsistent modifier side: bare `shift` vs `shift_l`): pynput's
high-level `Key` enum is unreliable for Shift/Ctrl/Alt side on Windows — which
side Windows reports depends on the delivery path. We resolve the side
ourselves from the raw `KBDLLHOOKSTRUCT` (`scanCode` + the extended-key
flag) via pynput's `win32_event_filter` hook, which is deterministic:
    Shift : scanCode 0x2A = left,  0x36 = right
    Ctrl  : scanCode 0x1D, extended bit clear = left,  set = right
    Alt   : scanCode 0x38, extended bit clear = left,  set = right
This is the same scancode table Windows itself uses to distinguish L/R
modifiers (there is no vendor-specific magic here — it's the AT set-1
scancode layout every Windows keyboard driver reports).

B4 (OS/system key & raw vk_### pollution): a vk->name table canonicalizes
numpad/extended keys instead of emitting `vk_97`; a fixed set of OS/system
virtual-key codes (Win, media keys, PrintScreen, lock keys) is dropped at
capture instead of leaking into inputs.jsonl for downstream to filter.

B5 (Ctrl+letter emits a control byte): pynput's `KeyCode.char` for Ctrl+<letter>
combos on Windows is the ASCII control character (U+0001..U+001A), not the
letter. We map it back to the base letter (`ctrl+w` records as `ctrl` (already
down) + `w`, not a stray U+0017).

B7 (simultaneous L+R modifier "bleed"): Windows can momentarily deliver a
duplicate transition for the *other* side of a modifier within a few ms of
the real one. `_ModifierDebounce` drops a same-pair opposite-side "down"
that arrives within `BLEED_WINDOW_S` of an already-down side, keeping only
the side with the real (first) scancode transition.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# B4: vk -> canonical name for keys pynput can't map to a nice string, and the
# set of OS/system keys we refuse to record as game input.
# --------------------------------------------------------------------------- #
_NUMPAD_VK_NAMES = {
    0x60: "numpad0", 0x61: "numpad1", 0x62: "numpad2", 0x63: "numpad3",
    0x64: "numpad4", 0x65: "numpad5", 0x66: "numpad6", 0x67: "numpad7",
    0x68: "numpad8", 0x69: "numpad9",
    0x6A: "numpad_multiply", 0x6B: "numpad_add", 0x6C: "numpad_separator",
    0x6D: "numpad_subtract", 0x6E: "numpad_decimal", 0x6F: "numpad_divide",
}

# Dropped outright — never recorded as game input (B4). Win keys, media keys,
# lock keys, PrintScreen, and browser/launcher keys are not gameplay signal
# and their pynput names are inconsistent junk (`cmd`, `vk_###`) anyway.
_OS_SYSTEM_VKS = {
    0x5B, 0x5C, 0x5D,          # LWin, RWin, Apps
    0x2C,                       # PrintScreen
    0x14, 0x90, 0x91,            # CapsLock, NumLock, ScrollLock
    0xAD, 0xAE, 0xAF,            # Volume mute/down/up
    0xB0, 0xB1, 0xB2, 0xB3,      # Media next/prev/stop/play-pause
    0xB4, 0xB5, 0xB6, 0xB7,      # Launch mail/media/app1/app2
    0xA6, 0xA7,                  # Browser back/forward
}

# Scancodes shared by both sides of a modifier; side is resolved from these
# plus the extended-key flag (B3).
_SHIFT_LEFT_SCAN, _SHIFT_RIGHT_SCAN = 0x2A, 0x36
_CTRL_SCAN, _ALT_SCAN = 0x1D, 0x38
LLKHF_EXTENDED = 0x01

# B7: modifier pairs that must never both transition "down" within this
# window without the second being treated as a bleed artifact.
BLEED_WINDOW_S = 0.03
_MODIFIER_PAIRS = (("shift_l", "shift_r"), ("ctrl_l", "ctrl_r"), ("alt_l", "alt_r"))


def _resolve_modifier_side(vk: int, scan_code: int, flags: int) -> str | None:
    """B3: deterministic L/R side from the raw scancode + extended-key bit."""
    extended = bool(flags & LLKHF_EXTENDED)
    if scan_code == _SHIFT_LEFT_SCAN:
        return "shift_l"
    if scan_code == _SHIFT_RIGHT_SCAN:
        return "shift_r"
    if scan_code == _CTRL_SCAN:
        return "ctrl_r" if extended else "ctrl_l"
    if scan_code == _ALT_SCAN:
        return "alt_r" if extended else "alt_l"
    return None


def _base_letter_from_control_byte(ch: str) -> str | None:
    """B5: Ctrl+<letter> control byte (U+0001..U+001A) -> base letter."""
    o = ord(ch)
    if 1 <= o <= 26:
        return chr(ord("a") + o - 1)
    return None


class _ModifierDebounce:
    """B7: suppress a spurious opposite-side "down" for a modifier pair that
    arrives within BLEED_WINDOW_S of the same pair's other side already
    being down."""

    def __init__(self) -> None:
        self._down_at: dict[str, float] = {}

    def should_suppress_down(self, name: str, now: float) -> bool:
        for left, right in _MODIFIER_PAIRS:
            other = right if name == left else left if name == right else None
            if other is None:
                continue
            other_since = self._down_at.get(other)
            if other_since is not None and (now - other_since) < BLEED_WINDOW_S:
                return True
        return False

    def note_down(self, name: str, now: float) -> None:
        self._down_at[name] = now

    def note_up(self, name: str) -> None:
        self._down_at.pop(name, None)


def _key_to_str(key: Any, side_hint: str | None) -> str | None:
    """
    pynput keys come in two flavors:
      - KeyCode(char='w')  for printable keys
      - Key.shift, Key.ctrl, Key.f1, Key.space, etc. for special keys

    `side_hint` is the B3-resolved left/right modifier name for this exact
    event (from the raw win32 hook data), or None for non-modifier keys.
    Returns None when the key should be dropped entirely (B4 OS/system keys).
    """
    import pynput.keyboard as keyboard

    if side_hint is not None:
        return side_hint

    vk = getattr(key, "vk", None)
    if vk is not None and vk in _OS_SYSTEM_VKS:
        return None
    if vk is not None and vk in _NUMPAD_VK_NAMES:
        return _NUMPAD_VK_NAMES[vk]

    if isinstance(key, keyboard.KeyCode):
        char = key.char
        if char is not None and len(char) == 1 and ord(char) < 32:
            base = _base_letter_from_control_byte(char)
            if base is not None:
                return base  # B5: ctrl is already tracked as held separately
            return None  # unmappable control byte — drop rather than leak it
        if char:
            return char.lower() if char.isalpha() else char
        if vk is not None:
            return f"vk_{vk}"
        return None

    # Key.<name> special keys (space, esc, f1, ...). Generic shift/ctrl/alt
    # without a side_hint means the raw hook didn't give us a resolvable
    # scancode (shouldn't happen on Windows for these three, but fall back
    # to pynput's own name rather than crash).
    name = getattr(key, "name", None)
    return name


def _button_to_str(button: Any) -> str:
    import pynput.mouse as mouse

    mapping = {
        mouse.Button.left: "left",
        mouse.Button.right: "right",
        mouse.Button.middle: "middle",
    }
    if button in mapping:
        return mapping[button]
    name = getattr(button, "name", None)
    if name:
        return name
    return str(button)


class InputCapture:
    """
    High-level keyboard + mouse-button + mouse-wheel capture.

    Usage:
        capture = InputCapture(anchor_monotonic=time.perf_counter())
        await capture.start()
        ...
        events = []
        while True:
            ev = await capture.queue.get()
            if ev is None:
                break
            events.append(ev)
        await capture.stop()
    """

    def __init__(self, anchor_monotonic: float) -> None:
        """
        anchor_monotonic: time.perf_counter() value to be treated as t=0.
        Typically set to the moment ffmpeg starts capturing. We use
        perf_counter (QPC-backed, sub-µs) instead of monotonic because on
        Windows the latter has ~15.6 ms resolution, which would quantize
        per-event timestamps and produce duplicate-looking rows.
        """
        self.anchor = anchor_monotonic
        self.queue: asyncio.Queue = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._kb_listener = None
        self._mouse_listener = None
        self._enabled = True
        self._keys_down: set[str] = set()
        self._debounce = _ModifierDebounce()
        self._last_raw_kb: tuple[int, int, int] | None = None  # (vk, scanCode, flags)
        self.last_error: str | None = None

    def is_alive(self) -> bool:
        kb_alive = self._kb_listener is not None and self._kb_listener.is_alive()
        mouse_alive = self._mouse_listener is not None and self._mouse_listener.is_alive()
        return kb_alive and mouse_alive

    def set_enabled(self, enabled: bool) -> None:
        """
        When False, events are dropped instead of pushed to the queue.
        Used by FocusTracker to pause capture when the game loses focus.
        """
        if self._enabled != enabled:
            log.info("input capture %s", "resumed" if enabled else "paused")
        self._enabled = enabled

    def release_all_held_keys(self) -> None:
        """B6 fix: called by FocusTracker right before `set_enabled(False)`.

        The old behavior silently cleared `_keys_down` on focus loss, so a
        key held across a focus change never got its matching "up" event —
        held-state could desync at focus boundaries. This emits a synthetic
        "up" for every key currently tracked as down (still timestamped and
        enabled, so it reaches the queue) before capture pauses, keeping
        down/up pairs consistent even across alt-tabs.
        """
        for name in list(self._keys_down):
            self._emit({"t": self._now_us(), "type": "key", "key": name, "action": "up"})
        self._keys_down.clear()
        for name in ("shift_l", "shift_r", "ctrl_l", "ctrl_r", "alt_l", "alt_r"):
            self._debounce.note_up(name)

    def _now_us(self) -> int:
        """Microseconds since anchor (sub-µs precision via perf_counter)."""
        return int((time.perf_counter() - self.anchor) * 1_000_000)

    def _emit(self, event: dict) -> None:
        """Thread-safe push from pynput's listener thread to the asyncio queue."""
        if not self._enabled:
            return
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self.queue.put_nowait, event)

    def _win32_event_filter(self, msg: int, data: Any) -> None:
        """pynput Windows-backend hook: fires with the raw KBDLLHOOKSTRUCT
        before on_press/on_release for the same event. We stash vk/scanCode/
        flags here so `_key_to_str` can resolve modifier side (B3). No-op on
        non-Windows backends (pynput simply never calls this there)."""
        try:
            self._last_raw_kb = (data.vkCode, data.scanCode, data.flags)
        except AttributeError:
            self._last_raw_kb = None

    def _consume_side_hint(self, key: Any) -> str | None:
        import pynput.keyboard as keyboard

        if key not in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r,
                       keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
                       keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
                       keyboard.Key.alt_gr):
            return None
        raw = self._last_raw_kb
        if raw is None:
            return None
        vk, scan_code, flags = raw
        return _resolve_modifier_side(vk, scan_code, flags)

    def _on_press(self, key: Any) -> None:
        try:
            side_hint = self._consume_side_hint(key)
            name = _key_to_str(key, side_hint)
            if name is None:
                return
            now = time.monotonic()
            if name in ("shift_l", "shift_r", "ctrl_l", "ctrl_r", "alt_l", "alt_r"):
                if self._debounce.should_suppress_down(name, now):
                    return
                self._debounce.note_down(name, now)
            if name in self._keys_down:
                return
            self._keys_down.add(name)
            self._emit({"t": self._now_us(), "type": "key", "key": name, "action": "down"})
        except Exception:
            log.exception("on_press failed")

    def _on_release(self, key: Any) -> None:
        try:
            side_hint = self._consume_side_hint(key)
            name = _key_to_str(key, side_hint)
            if name is None:
                return
            if name in ("shift_l", "shift_r", "ctrl_l", "ctrl_r", "alt_l", "alt_r"):
                self._debounce.note_up(name)
            self._keys_down.discard(name)
            self._emit({"t": self._now_us(), "type": "key", "key": name, "action": "up"})
        except Exception:
            log.exception("on_release failed")

    def _on_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        try:
            self._emit({
                "t": self._now_us(),
                "type": "mouse_button",
                "button": _button_to_str(button),
                "action": "down" if pressed else "up",
            })
        except Exception:
            log.exception("on_click failed")

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        try:
            self._emit({"t": self._now_us(), "type": "mouse_wheel", "dy": dy})
        except Exception:
            log.exception("on_scroll failed")

    async def start(self) -> None:
        """Install global hooks on background threads."""
        import pynput.keyboard as keyboard
        import pynput.mouse as mouse

        self._loop = asyncio.get_running_loop()
        try:
            self._kb_listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
                win32_event_filter=self._win32_event_filter,
            )
            self._kb_listener.start()
            self._mouse_listener = mouse.Listener(
                on_click=self._on_click,
                on_scroll=self._on_scroll,
            )
            self._mouse_listener.start()
            # Both waits are blocking calls (pynput's Listener.wait isn't
            # asyncio-aware) — run them off the event loop thread, and
            # concurrently rather than sequentially, so a slow/stuck hook
            # install doesn't stall the whole loop for up to 6s and delay
            # every other subsystem's readiness signal from being delivered.
            kb_ready, mouse_ready = await asyncio.gather(
                asyncio.to_thread(self._kb_listener.wait, 3),
                asyncio.to_thread(self._mouse_listener.wait, 3),
            )
            if not kb_ready or not mouse_ready:
                self.last_error = "pynput listener failed to start within 3s"
        except Exception as e:
            log.exception("InputCapture.start failed")
            self.last_error = f"input capture failed to start: {e}"

    async def stop(self) -> None:
        """Remove hooks. Safe to call multiple times."""
        if self._kb_listener is not None:
            self._kb_listener.stop()
            self._kb_listener = None
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None
