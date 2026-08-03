"""Regression coverage for InputCapture.start()'s readiness wait.

Real bug hit on Windows: pynput's Listener.wait() takes no arguments
(AbstractListener.wait(self)) — calling it as `.wait(timeout=3)` (what the
code used to do, both before and after an earlier attempted fix here) raises
`TypeError: wait() takes 1 positional argument but 2 were given`, which
surfaced in the running app as "input capture failed to start" and is very
likely what an earlier "keyboard/mouse timeout" report was actually hitting.
Fixed by polling the documented public `.running` attribute with our own
timeout loop (`_wait_listener_running`) instead of relying on `.wait()`.

These tests use a fake listener object with only a `.running` attribute
(no `.wait()` at all) precisely so a regression back to calling
`.wait(timeout=...)` fails loudly instead of silently working here just
because a real pynput listener happens to be importable.
"""
import asyncio

from app.core.keyboard_capture import InputCapture, _wait_listener_running


class _FakeListener:
    """Deliberately has no .wait() — only the public .running attribute
    InputCapture.start() must rely on."""

    def __init__(self, running: bool = False) -> None:
        self.running = running
        self.started = False

    def start(self) -> None:
        self.started = True
        self.running = True

    def stop(self) -> None:
        self.running = False


def test_wait_listener_running_returns_true_once_flag_flips():
    listener = _FakeListener(running=True)
    assert asyncio.run(_wait_listener_running(listener, timeout=1.0)) is True


def test_wait_listener_running_times_out_if_never_running():
    listener = _FakeListener(running=False)
    assert asyncio.run(_wait_listener_running(listener, timeout=0.1)) is False


def test_input_capture_start_uses_running_attr_not_wait_method(monkeypatch):
    """End-to-end through InputCapture.start(): must not call a nonexistent
    Listener.wait(timeout=...) and must not set last_error when both fake
    listeners report running=True immediately."""
    import pynput.keyboard
    import pynput.mouse

    monkeypatch.setattr(pynput.keyboard, "Listener",
                         lambda **kw: _FakeListener(running=True))
    monkeypatch.setattr(pynput.mouse, "Listener",
                         lambda **kw: _FakeListener(running=True))

    capture = InputCapture(anchor_monotonic=0.0)
    asyncio.run(capture.start())

    assert capture.last_error is None
    assert capture._kb_listener.started
    assert capture._mouse_listener.started
