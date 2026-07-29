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
from pathlib import Path


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

    root.info("=" * 60)
    root.info("HumynCapture starting (log: %s)", log_path)
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
