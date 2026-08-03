"""
HumynCapture entry point.

Flow:
    1. Configure logging to %LOCALAPPDATA%\\HumynCapture\\logs\\humyncapture.log
    2. Apply stylesheet, single-instance check.
    3. If first run -> show SetupWizard.
    4. Show MainWindow.

Logging is configured BEFORE any other app imports execute their own
log.info() calls — that's why this file is small and the heavy imports
happen inside main() rather than at module top.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path


def _install_thread_excepthook() -> None:
    """Real gap found: a genuine crash (RawMouseCapture's Win32 message
    pump, `raw_mouse.py`) took down the whole app with total log silence
    right after — no further activity at all, not even the health monitor's
    own ~2s-later "subsystem failed mid-session" log line that should have
    fired. There was no `threading.excepthook` at all: an exception in any
    background thread that ISN'T already wrapped in its own try/except
    prints to stderr only and never reaches humyncapture.log — on a
    packaged exe, stderr usually isn't visible to the user or preserved
    anywhere, so this is effectively silent. This won't necessarily catch
    every failure mode (a hard native-level crash may not go through Python
    exception handling at all), but it closes a real, confirmed blind spot
    for any other thread's unhandled exception.
    """
    log = logging.getLogger("app.thread_excepthook")

    def _hook(args: threading.ExceptHookArgs) -> None:
        log.error("unhandled exception in thread %r", args.thread.name,
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = _hook


def _setup_logging() -> Path:
    """
    Set up logging to a file under %LOCALAPPDATA%\\HumynCapture\\logs\\.
    Returns the log file path so main() can echo it to the user on errors.

    We use a rotating handler so logs don't grow without bound; one session
    of input capture can produce ~50K events that get debug-logged.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HumynCapture"
    else:
        base = Path.home() / ".local" / "share" / "HumynCapture"
    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "humyncapture.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    _install_thread_excepthook()

    # app._version is deliberately tiny and dependency-free (no PySide6/
    # pynput/etc.) so it's safe to import here, before any heavy app module
    # — see that file's docstring for why this line exists: several rounds
    # of debugging were spent unable to tell whether a given exe/log
    # actually contained the latest fixes. Now it's the first thing in the
    # log, every time.
    from app._version import HUMYN_VERSION

    root.info("=" * 60)
    root.info("HumynCapture v%s starting (log: %s)", HUMYN_VERSION, log_path)
    root.info("=" * 60)
    return log_path


def main() -> int:
    _setup_logging()
    log = logging.getLogger("app.main")
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication, QDialog

        from app.core.state import is_setup_complete
        from app.ui.main_window import MainWindow
        from app.ui.setup_window import SetupWizard
        from app.ui.style import STYLESHEET

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication(sys.argv)
        app.setApplicationName("HumynCapture")
        app.setOrganizationName("Humyn Labs")
        app.setStyle("Fusion")
        app.setStyleSheet(STYLESHEET)

        if not is_setup_complete():
            log.info("First run -> showing SetupWizard")
            wizard = SetupWizard()
            if wizard.exec() != QDialog.DialogCode.Accepted:
                log.info("User cancelled setup")
                return 0

        log.info("Showing MainWindow")
        window = MainWindow()
        window.show()
        return app.exec()
    except Exception:
        log.exception("Failed to start HumynCapture")
        raise


if __name__ == "__main__":
    sys.exit(main())
