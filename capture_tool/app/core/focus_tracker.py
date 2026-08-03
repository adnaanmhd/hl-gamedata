"""
Tracks the foreground window and pauses input capture when the game loses
focus.

--- Fix applied here (issue B6) ---
The original implementation polled `GetForegroundWindow()` at 5 Hz and, on
focus loss, called `InputCapture.set_enabled(False)` which cleared
`_keys_down` outright — a key held across a focus change never got a
matching "up", and up to ~200ms of input around the transition could be
mishandled (kamla/smp2 had 6 alt-tabs in one session).

Fix: an event-driven `SetWinEventHook(EVENT_SYSTEM_FOREGROUND, ...)` on a
dedicated message-pump thread replaces the poll loop — focus changes are
detected as soon as Windows delivers them, not up to 200ms later. On focus
loss we call `capture.release_all_held_keys()` (synthesizes "up" for every
currently-held key — see keyboard_capture.py) before disabling capture, so
held-state never desyncs at a focus boundary.

We don't poll because polling was never about correctness — it was a
stopgap because `SetWinEventHook` needs a running message pump on its own
thread, which is what we now provide (mirroring raw_mouse.py's
message-only-window thread).

Emits a 'focus' event into the same queue as InputCapture whenever focus
state changes, so inputs.jsonl includes focus markers inline.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes
from typing import Any

log = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"

if _WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    EVENT_SYSTEM_FOREGROUND = 0x0003
    WINEVENT_OUTOFCONTEXT = 0x0000
    WM_QUIT = 0x0012

    HWINEVENTHOOK = wintypes.HANDLE
    WINEVENTPROC = ctypes.WINFUNCTYPE(
        None, HWINEVENTHOOK, wintypes.DWORD, wintypes.HWND,
        ctypes.c_long, ctypes.c_long, wintypes.DWORD, wintypes.DWORD)

    user32.SetWinEventHook.argtypes = [
        wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WINEVENTPROC,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    user32.SetWinEventHook.restype = HWINEVENTHOOK
    user32.UnhookWinEvent.argtypes = [HWINEVENTHOOK]
    user32.UnhookWinEvent.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _get_foreground_pid() -> int | None:
    """Return the PID of the current foreground window, or None. Windows-only.
    Kept for the "is the game currently focused" snapshot check at start()."""
    if not _WINDOWS:
        return None
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        log.exception("GetForegroundWindow failed")
        return None


class FocusTracker:
    """
    Event-driven foreground-window tracker (B6). Detects focus changes via
    `SetWinEventHook` on a dedicated message-pump thread instead of 5Hz
    polling. When focus changes, emits a 'focus' event and toggles the
    InputCapture's enabled state — synthesizing held-key "up" events first
    on focus loss.

    Usage:
        tracker = FocusTracker(game_pid=14328, anchor_monotonic=anchor,
                               capture=input_capture)
        await tracker.start()
        ...
        await tracker.stop()
    """

    def __init__(self, game_pid: int, anchor_monotonic: float, capture: Any) -> None:
        self.game_pid = game_pid
        self.anchor = anchor_monotonic
        self.capture = capture
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._hook = None
        self._proc_ref = None
        self._ready = threading.Event()
        self.last_error: str | None = None
        self._currently_focused = True

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _now_us(self) -> int:
        return int((time.perf_counter() - self.anchor) * 1_000_000)

    def _emit_focus_event(self, focused: bool) -> None:
        """Push a focus event into the input queue inline with other inputs."""
        loop = self._loop
        if loop is None:
            return
        event = {"t": self._now_us(), "type": "focus",
                  "focused": focused}
        loop.call_soon_threadsafe(self.capture.queue.put_nowait, event)

    def _on_focus_changed(self, focused: bool) -> None:
        if focused == self._currently_focused:
            return
        self._currently_focused = focused
        self._emit_focus_event(focused)
        if focused:
            self.capture.set_enabled(True)
            log.info("focus regained — input capture resumed")
        else:
            self.capture.release_all_held_keys()
            self.capture.set_enabled(False)
            log.info("focus lost — held keys released, input capture paused")

    def _win_event_proc(self, hWinEventHook, event, hwnd, idObject, idChild,
                         idEventThread, dwmsEventTime) -> None:
        if event != EVENT_SYSTEM_FOREGROUND or not hwnd:
            return
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            self._on_focus_changed(int(pid.value) == self.game_pid)
        except Exception:
            log.exception("WinEventProc handling failed")

    def _thread_main(self) -> None:
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            self._currently_focused = _get_foreground_pid() == self.game_pid
            self._proc_ref = WINEVENTPROC(self._win_event_proc)
            self._hook = user32.SetWinEventHook(
                EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None,
                self._proc_ref, 0, 0, WINEVENT_OUTOFCONTEXT)
            if not self._hook:
                self.last_error = (f"SetWinEventHook failed: "
                                   f"Win32 error {ctypes.get_last_error()}")
            self._ready.set()
            if self._hook:
                self._pump_messages()
        except Exception as e:
            self.last_error = f"focus tracker thread failed: {e}"
            log.exception("FocusTracker thread failed")
            self._ready.set()
        finally:
            if self._hook:
                try:
                    user32.UnhookWinEvent(self._hook)
                except Exception:
                    pass
                self._hook = None

    def _pump_messages(self) -> None:
        # WINEVENT_OUTOFCONTEXT delivers the callback via this thread's
        # message queue; a plain GetMessage loop (mirroring raw_mouse.py) is
        # enough — we never PostThreadMessage anything but WM_QUIT to it.
        from app.core.raw_mouse import MSG  # reuse the ctypes MSG layout
        msg = MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                return
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    async def start(self) -> None:
        if not _WINDOWS:
            self.last_error = "focus tracking only available on Windows"
            return
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._thread_main, name="FocusTracker", daemon=True)
        self._thread.start()
        await asyncio.to_thread(self._ready.wait, 3)
        if self._thread_id is None:
            self.last_error = self.last_error or "focus tracker failed to start within 3s"

    async def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive() and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            await asyncio.to_thread(self._thread.join, 3)
