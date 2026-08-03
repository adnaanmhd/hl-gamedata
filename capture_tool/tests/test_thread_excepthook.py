"""Real gap found: a background thread's unhandled exception (RawMouseCapture's
Win32 message pump crashing) went completely unlogged — no threading.excepthook
existed at all, so it printed to stderr only, invisible on a packaged exe."""
import logging
import threading

from app.main import _install_thread_excepthook


def test_installs_a_real_threading_excepthook():
    original = threading.excepthook
    try:
        _install_thread_excepthook()
        assert threading.excepthook is not original
    finally:
        threading.excepthook = original


def test_hook_logs_the_exception(caplog):
    original = threading.excepthook
    try:
        _install_thread_excepthook()
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            exc_info = __import__("sys").exc_info()
        args = threading.ExceptHookArgs(
            (exc_info[0], exc_info[1], exc_info[2], threading.current_thread()))
        with caplog.at_level(logging.ERROR, logger="app.thread_excepthook"):
            threading.excepthook(args)
        assert any("unhandled exception" in r.message for r in caplog.records)
        assert any("boom" in str(r.exc_info) for r in caplog.records if r.exc_info)
    finally:
        threading.excepthook = original
