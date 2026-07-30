"""
First-run setup window. Shows a progress bar while we download ffmpeg, then
closes itself when done.

Setup logic runs on a worker thread; we marshal progress strings + percent
back to the GUI via Qt signals.

Restored to match the shipped app (this class's bytecode decompiled cleanly
enough to read the real design directly) — the version previously here was
a from-scratch guess written when this class's body failed to decompile,
with a single combined `_on_done(error)` slot instead of the real design's
separate success/failure signals (`done_signal`/`error_signal` ->
`_on_done`/`_on_error`), different window sizing, and different wording.
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
    progress_signal = Signal(str, object)  # (message, pct|None)
    done_signal = Signal()
    error_signal = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("HumynCapture — First-run setup")
        self.setFixedSize(520, 220)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)

        title = QLabel("Setting up HumynCapture")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        explainer = QLabel(
            "We're downloading ffmpeg (~110 MB), the screen-recording engine "
            "we use. This is a one-time setup. Nothing else on your system "
            "is changed.")
        explainer.setWordWrap(True)
        explainer.setStyleSheet("color: #888;")
        layout.addWidget(explainer)

        self.status_label = QLabel("Preparing…")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        layout.addStretch(1)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_btn)
        layout.addLayout(button_row)

        self.progress_signal.connect(self._on_progress)
        self.done_signal.connect(self._on_done)
        self.error_signal.connect(self._on_error)

        self._thread = threading.Thread(target=self._run_setup, daemon=True)
        self._thread.start()

    def _run_setup(self) -> None:
        try:
            run_full_setup(lambda msg, pct: self.progress_signal.emit(msg, pct))
            mark_setup_complete()
            self.done_signal.emit()
        except Exception as e:  # noqa: BLE001 — surfaced to the dialog, not swallowed
            self.error_signal.emit(f"{type(e).__name__}: {e}")

    def _on_progress(self, msg: str, pct: "int | None") -> None:
        self.status_label.setText(msg)
        if pct is None:
            self.progress_bar.setRange(0, 0)  # indeterminate
            return
        if self.progress_bar.minimum() == 0 and self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(pct))

    def _on_done(self) -> None:
        self.status_label.setText("Setup complete.")
        self.progress_bar.setValue(100)
        self.cancel_btn.setText("Continue")
        try:
            self.cancel_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self.cancel_btn.clicked.connect(self.accept)

    def _on_error(self, err: str) -> None:
        self.status_label.setText(f"Setup failed: {err}")
        self.progress_bar.setValue(0)
