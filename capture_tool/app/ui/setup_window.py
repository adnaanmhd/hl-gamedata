"""
First-run setup window. Shows a progress bar while we download ffmpeg, then
closes itself when done.

Setup logic runs on a worker thread; we marshal progress strings + percent
back to the GUI via Qt signals.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

from app.core.state import mark_setup_complete
from app.setup.installer import run_full_setup


class SetupWizard(QDialog):
    _progress_signal = Signal(str, object)  # (message, pct|None)
    _done_signal = Signal(object)  # exception|None

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HumynCapture — First-Run Setup")
        self.setModal(True)
        self.setFixedSize(420, 160)

        self._label = QLabel("Preparing setup...")
        self._label.setWordWrap(True)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)
        layout.addLayout(row)

        self._progress_signal.connect(self._on_progress)
        self._done_signal.connect(self._on_done)
        self._thread = threading.Thread(target=self._run_setup, daemon=True)
        self._thread.start()

    def _run_setup(self) -> None:
        def progress(message: str, pct: int | None) -> None:
            self._progress_signal.emit(message, pct)

        try:
            run_full_setup(progress)
        except Exception as e:  # noqa: BLE001 — surfaced to the dialog, not swallowed
            self._done_signal.emit(e)
        else:
            self._done_signal.emit(None)

    def _on_progress(self, message: str, pct: object) -> None:
        self._label.setText(message)
        if isinstance(pct, int):
            self._bar.setRange(0, 100)
            self._bar.setValue(pct)
        else:
            self._bar.setRange(0, 0)  # indeterminate

    def _on_done(self, error: object) -> None:
        if error is not None:
            self._label.setText(f"Setup failed: {error}")
            self._cancel_btn.setText("Close")
            return
        mark_setup_complete()
        self.accept()
