"""
Raw HID mouse delta capture via Windows Raw Input API.

Why this exists
---------------
Most AAA games lock the OS cursor and read mouse input directly from the HID
device (via DirectInput or Raw Input). When that happens, conventional
keyboard/mouse hooks — including pynput's hooks — see almost no mouse motion,
because the game intercepts the input before it propagates to the OS cursor
or the windows message queue of other apps.

To capture the mouse motion the game actually sees, we have to register
ourselves as a Raw Input client too. Windows broadcasts a copy of every HID
event to every registered Raw Input window, regardless of which window has
focus (when RIDEV_INPUTSINK is set). We get the same raw dx/dy stream the
game does.

How it works
------------
- We create a hidden message-only window on a dedicated thread.
- We register it as a Raw Input sink for mouse events:
    usagePage = 0x01 (generic desktop), usage = 0x02 (mouse), flags = INPUTSINK.
- We pump messages on that thread; for every WM_INPUT we GetRawInputData,
  decode the RAWMOUSE struct, and write a JSONL record with our monotonic
  clock so it aligns with the rest of inputs.jsonl.
- On stop, we post WM_QUIT to the thread; it cleans up and exits.

Output schema (one JSON per line)
---------------------------------
{
  "ts_offset_ns": <int>,            # ns from ffmpeg recording start (see set_t_zero)
  "ts_monotonic_ns": <int>,         # raw monotonic_ns
  "dx": <int>,                      # relative motion (raw HID counts)
  "dy": <int>,                      # relative motion (raw HID counts)
  "buttons": ["left_down", ...],    # button transitions in this event
  "wheel": <int|null>               # vertical wheel delta in WHEEL_DELTA units
}

Notes
-----
- dx/dy are RAW HID COUNTS, not pixels. They depend on mouse DPI and the
  game's sensitivity setting. For world-model training this is the right
  signal — it's what the game sees.
- We log RELATIVE deltas only. RAWMOUSE can theoretically deliver absolute
  positions for pen/digitizer devices (MOUSE_MOVE_ABSOLUTE flag); we ignore
  those because no gaming mouse uses that mode.
- AV/EDR products sometimes flag raw-input hooks. The hook is documented
  Windows API and benign, but we surface load failures so the user sees a
  clear error instead of silent data loss.

--- Fix applied here (issue B1) ---
Before this fix, `RawMouseCapture._thread_main` swallowed init failures into
`last_error` but nothing downstream ever read it — `session_engine.run`
proceeded into RECORDING with a dead sink and produced a silent zero-motion
session (3 of 6 samples in the evidence batch). Fixes:
  1. `RegisterRawInputDevices` now retries (`_REGISTER_RETRIES` attempts with
     a short backoff) and logs the specific Win32 error code on each failure
     via `ctypes.get_last_error()`, instead of raising on the first transient
     failure.
  2. `is_alive()` lets `app.core.health.SubsystemMonitor` poll this subsystem
     like any other — `session_engine.run` now checks `last_error` right
     after `start()` returns and aborts/warns instead of silently recording.
  3. `AsyncRawMouseCapture` exposes `is_alive()`/`last_error` passthroughs so
     the health monitor doesn't need to know this subsystem is thread-backed.
(The second half of B1 — `_merge_inputs` falling back to `ts_monotonic_ns`
instead of dropping records with no `ts_offset_ns` — lives in
session_engine.py, since that's where the merge happens.)
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"
_REGISTER_RETRIES = 3
_REGISTER_RETRY_DELAY_S = 0.25

if _WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    HWND = wintypes.HWND
    UINT = wintypes.UINT
    WPARAM = wintypes.WPARAM
    LPARAM = wintypes.LPARAM
    LRESULT = ctypes.c_ssize_t
    HANDLE = wintypes.HANDLE
    DWORD = wintypes.DWORD
    USHORT = wintypes.USHORT
    LONG = wintypes.LONG
    HINSTANCE = wintypes.HINSTANCE
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)

    WM_INPUT = 0xFF
    WM_DESTROY = 0x0002
    WM_QUIT = 0x0012
    HWND_MESSAGE = HWND(-3)
    WS_OVERLAPPEDWINDOW = 0x00CF0000
    RIDEV_INPUTSINK = 0x00000100
    RIDEV_REMOVE = 0x00000001
    RID_INPUT = 0x10000003
    RIM_TYPEMOUSE = 0

    RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
    RI_MOUSE_LEFT_BUTTON_UP = 0x0002
    RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
    RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
    RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
    RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
    RI_MOUSE_BUTTON_4_DOWN = 0x0040
    RI_MOUSE_BUTTON_4_UP = 0x0080
    RI_MOUSE_BUTTON_5_DOWN = 0x0100
    RI_MOUSE_BUTTON_5_UP = 0x0200
    RI_MOUSE_WHEEL = 0x0400
    MOUSE_MOVE_ABSOLUTE = 0x01

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", UINT), ("style", UINT), ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", HINSTANCE), ("hIcon", HANDLE), ("hCursor", HANDLE),
            ("hbrBackground", HANDLE), ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", HANDLE),
        ]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", HWND), ("message", UINT), ("wParam", WPARAM),
            ("lParam", LPARAM), ("time", DWORD), ("pt_x", LONG), ("pt_y", LONG),
        ]

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", USHORT), ("usUsage", USHORT),
            ("dwFlags", DWORD), ("hwndTarget", HWND),
        ]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [
            ("dwType", DWORD), ("dwSize", DWORD),
            ("hDevice", HANDLE), ("wParam", WPARAM),
        ]

    class _RAWMOUSE_BUTTONS(ctypes.Structure):
        _fields_ = [("usButtonFlags", USHORT), ("usButtonData", USHORT)]

    class _RAWMOUSE_BUTTONS_UNION(ctypes.Union):
        _fields_ = [("ulButtons", ctypes.c_ulong), ("buttons", _RAWMOUSE_BUTTONS)]

    class RAWMOUSE(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [
            ("usFlags", USHORT), ("u", _RAWMOUSE_BUTTONS_UNION),
            ("ulRawButtons", ctypes.c_ulong), ("lLastX", ctypes.c_long),
            ("lLastY", ctypes.c_long), ("ulExtraInformation", ctypes.c_ulong),
        ]

    class _RAWINPUT_DATA(ctypes.Union):
        _fields_ = [("mouse", RAWMOUSE)]

    class RAWINPUT(ctypes.Structure):
        _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWINPUT_DATA)]

    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, DWORD, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, HWND, HANDLE, HINSTANCE,
        ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = HWND
    user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT, UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.RegisterRawInputDevices.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICE), UINT, UINT]
    user32.RegisterRawInputDevices.restype = wintypes.BOOL
    user32.GetRawInputData.argtypes = [
        HANDLE, UINT, ctypes.c_void_p, ctypes.POINTER(UINT), UINT]
    user32.GetRawInputData.restype = UINT
    user32.PostThreadMessageW.argtypes = [DWORD, UINT, WPARAM, LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = DWORD
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = HINSTANCE

_BUTTON_TRANSITIONS = [
    (0x0001, "left_down"), (0x0002, "left_up"),
    (0x0004, "right_down"), (0x0008, "right_up"),
    (0x0010, "middle_down"), (0x0020, "middle_up"),
    (0x0040, "button4_down"), (0x0080, "button4_up"),
    (0x0100, "button5_down"), (0x0200, "button5_up"),
]


class RawMouseCapture:
    """Captures raw HID mouse deltas to a JSONL file on a dedicated thread.

    Use:
        cap = RawMouseCapture(jsonl_path)
        cap.start()
        cap.set_t_zero(record_started_ns)
        ...
        cap.stop()
    """

    def __init__(self, jsonl_path: Path) -> None:
        self.jsonl_path = jsonl_path
        self._t_zero_ns: int | None = None
        self._t_zero_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._fp = None
        self._fp_lock = threading.Lock()
        self.event_count = 0
        self.last_error: str | None = None
        self._wndproc_ref = None
        self._hwnd = None

    def is_supported(self) -> bool:
        return _WINDOWS

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_t_zero(self, monotonic_ns: int) -> None:
        with self._t_zero_lock:
            self._t_zero_ns = monotonic_ns

    def start(self) -> None:
        if not _WINDOWS:
            self.last_error = "Raw input capture only available on Windows"
            return
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.jsonl_path.open("w", buffering=1, encoding="utf-8")
        self._thread = threading.Thread(
            target=self._thread_main, name="RawMouseCapture", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=3):
            if not self.last_error:
                self.last_error = "raw mouse capture failed to initialize within 3s"

    def stop(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._close_file()
            return
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(timeout=3)
        self._stopped.set()
        self._close_file()

    def _close_file(self) -> None:
        with self._fp_lock:
            if self._fp is not None:
                try:
                    self._fp.close()
                finally:
                    self._fp = None

    def _thread_main(self) -> None:
        """Runs on the capture thread. Creates the message-only window,
        registers Raw Input, pumps messages until WM_QUIT."""
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            self._create_window_and_register()
            self._ready.set()
            self._pump_messages()
        except Exception as e:
            self.last_error = f"raw mouse thread failed: {e}"
            log.exception("RawMouseCapture thread failed")
            self._ready.set()
        finally:
            try:
                self._cleanup_window()
            except Exception:
                log.exception("RawMouseCapture cleanup failed")

    def _create_window_and_register(self) -> None:
        hinstance = kernel32.GetModuleHandleW(None)
        self._wndproc_ref = WNDPROC(self._wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinstance
        wc.lpszClassName = "HumynCaptureRawInput"
        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            err = ctypes.get_last_error()
            if err not in (0, 1410):  # 1410 = ERROR_CLASS_ALREADY_EXISTS
                raise OSError(f"RegisterClassExW failed: {err}")
        hwnd = user32.CreateWindowExW(
            0, "HumynCaptureRawInput", "HumynCaptureRawInput",
            WS_OVERLAPPEDWINDOW, 0, 0, 0, 0, HWND_MESSAGE, None, hinstance, None)
        if not hwnd:
            raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")
        self._hwnd = hwnd

        # B1: retry registration instead of failing on the first transient
        # error, and log the specific Win32 error code every attempt so a
        # persistent failure is diagnosable from humyncapture.log.
        rid = RAWINPUTDEVICE(usUsagePage=1, usUsage=2, dwFlags=RIDEV_INPUTSINK,
                              hwndTarget=hwnd)
        last_err = 0
        for attempt in range(1, _REGISTER_RETRIES + 1):
            ok = user32.RegisterRawInputDevices(
                ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE))
            if ok:
                return
            last_err = ctypes.get_last_error()
            log.warning("RegisterRawInputDevices attempt %d/%d failed: "
                        "Win32 error %d", attempt, _REGISTER_RETRIES, last_err)
            if attempt < _REGISTER_RETRIES:
                time.sleep(_REGISTER_RETRY_DELAY_S)
        raise OSError(f"RegisterRawInputDevices failed after "
                      f"{_REGISTER_RETRIES} attempts: Win32 error {last_err}")

    def _cleanup_window(self) -> None:
        if self._hwnd:
            try:
                rid = RAWINPUTDEVICE(usUsagePage=1, usUsage=2,
                                      dwFlags=RIDEV_REMOVE, hwndTarget=None)
                user32.RegisterRawInputDevices(
                    ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE))
            except Exception:
                pass
            try:
                user32.DestroyWindow(self._hwnd)
            except Exception:
                pass
            self._hwnd = None

    def _pump_messages(self) -> None:
        msg = MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                return
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wparam, lparam) -> int:
        if msg == WM_INPUT:
            try:
                self._handle_raw_input(lparam)
            except Exception as e:
                self.last_error = f"raw input decode error: {e}"
                log.exception("raw input decode failed")
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle_raw_input(self, hRawInput) -> None:
        size = UINT(0)
        header_size = UINT(ctypes.sizeof(RAWINPUTHEADER))
        rc = user32.GetRawInputData(hRawInput, RID_INPUT, None,
                                     ctypes.byref(size), header_size)
        if rc != 0 or size.value == 0:
            return
        buf = (ctypes.c_byte * size.value)()
        rc = user32.GetRawInputData(hRawInput, RID_INPUT, buf,
                                     ctypes.byref(size), header_size)
        if rc != size.value:
            return
        ri = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
        if ri.header.dwType != RIM_TYPEMOUSE:
            return
        m = ri.data.mouse
        if m.usFlags & MOUSE_MOVE_ABSOLUTE:
            return  # no gaming mouse uses absolute mode; not our signal

        button_flags = m.buttons.usButtonFlags
        wheel_delta = None
        if button_flags & RI_MOUSE_WHEEL:
            raw = m.buttons.usButtonData
            wheel_delta = raw - 65536 if raw & 0x8000 else raw
        buttons = [name for bit, name in _BUTTON_TRANSITIONS if button_flags & bit]

        now_ns = time.monotonic_ns()
        with self._t_zero_lock:
            t_zero = self._t_zero_ns
        ts_offset_ns = (now_ns - t_zero) if t_zero is not None else None

        record = {
            "ts_offset_ns": ts_offset_ns,
            "ts_monotonic_ns": now_ns,
            "dx": int(m.lLastX),
            "dy": int(m.lLastY),
            "buttons": buttons,
            "wheel": wheel_delta,
        }
        with self._fp_lock:
            if self._fp is not None:
                self._fp.write(json.dumps(record) + "\n")
        self.event_count += 1


class AsyncRawMouseCapture:
    """Thin async wrapper so the session engine can `await` start/stop.
    The actual capture is on a Win32 thread; we just shim the coroutine
    interface. `start()`/`stop()` run the (blocking, up-to-3s) calls off
    the event loop thread so the engine's asyncio loop never stalls."""

    def __init__(self, jsonl_path: Path) -> None:
        self._cap = RawMouseCapture(jsonl_path)

    @property
    def event_count(self) -> int:
        return self._cap.event_count

    @property
    def last_error(self) -> str | None:
        return self._cap.last_error

    def is_supported(self) -> bool:
        return self._cap.is_supported()

    def is_alive(self) -> bool:
        return self._cap.is_alive()

    def set_t_zero(self, monotonic_ns: int) -> None:
        self._cap.set_t_zero(monotonic_ns)

    async def start(self) -> None:
        await asyncio.to_thread(self._cap.start)

    async def stop(self) -> None:
        await asyncio.to_thread(self._cap.stop)
