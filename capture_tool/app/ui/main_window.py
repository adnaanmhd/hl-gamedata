"""
Main window.

Two states:
    - IDLE: shows the metadata form + 'Start session' button + recent sessions
    - RECORDING: shows a compact recording widget; collapses everything else

We keep the GUI simple by toggling the central widget between these two
modes rather than building a real navigation stack. Good enough for v0.

--- D1 fix visible here ---
The game field is a QComboBox populated from `app.core.games.dropdown_titles()`
— never a QLineEdit. Free text is no longer possible; see app/core/games.py.

--- B2/E1/E2 fix visible here ---
After a session finishes, the result panel shows `qa_status` and every
self-check failure/warning by name (not just "done") — a session that isn't
`ready_for_upload` is visually distinct (red) from one that is, and the
contributor sees exactly which check failed while they can still re-record.
"""
from __future__ import annotations

import asyncio
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from app.core.games import dropdown_titles, resolve_game
from app.core.paths import SESSIONS_DIR
from app.core.process_watcher import find_pid_by_exe, list_likely_games
from app.core.session_engine import SessionEngine, SessionMetadata
from app.ui.async_runner import AsyncRunner

SKILL_LEVELS = ["novice", "intermediate", "expert"]
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HumynCapture")
        self.resize(480, 560)

        self._runner = AsyncRunner()
        self._runner.start()
        self._engine: SessionEngine | None = None

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)
        self._idle_widget = self._build_idle_widget()
        self._recording_widget = self._build_recording_widget()
        self._stack.addWidget(self._idle_widget)
        self._stack.addWidget(self._recording_widget)
        self._stack.setCurrentWidget(self._idle_widget)

        self._runner.progress.connect(self._on_progress)
        self._runner.finished.connect(self._on_session_finished)
        self._runner.failed.connect(self._on_session_failed)

        self._refresh_recent_sessions()

    # ------------------------------------------------------------------ IDLE
    def _build_idle_widget(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        form = QFormLayout()
        self._email_field = QLineEdit()
        self._email_field.setPlaceholderText("you@example.com")
        form.addRow("Contributor email", self._email_field)

        self._skill_field = QComboBox()
        self._skill_field.addItems(SKILL_LEVELS)
        form.addRow("Skill level", self._skill_field)

        self._role_field = QLineEdit()
        form.addRow("Role", self._role_field)

        self._objective_field = QLineEdit()
        form.addRow("Objective / task", self._objective_field)

        # D1: dropdown only — never a free-text game field.
        self._game_field = QComboBox()
        self._game_field.addItems(dropdown_titles())
        form.addRow("Game", self._game_field)

        self._process_field = QComboBox()
        form.addRow("Running process", self._process_field)
        refresh_btn = QPushButton("Refresh process list")
        refresh_btn.clicked.connect(self._refresh_processes)

        layout.addLayout(form)
        layout.addWidget(refresh_btn)

        start_btn = QPushButton("Start session")
        start_btn.clicked.connect(self._on_start_clicked)
        layout.addWidget(start_btn)

        layout.addWidget(QLabel("Recent sessions"))
        self._recent_list = QListWidget()
        layout.addWidget(self._recent_list)

        self._refresh_processes()
        return w

    def _refresh_processes(self) -> None:
        self._process_field.clear()
        self._process_field.addItems(list_likely_games())

    def _refresh_recent_sessions(self) -> None:
        self._recent_list.clear()
        if not SESSIONS_DIR.exists():
            return
        for entry in sorted(SESSIONS_DIR.iterdir(), reverse=True)[:20]:
            if entry.is_dir() and not entry.name.endswith("_raw"):
                self._recent_list.addItem(QListWidgetItem(entry.name))

    # ------------------------------------------------------------- RECORDING
    def _build_recording_widget(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self._status_label = QLabel("Recording...")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self._status_label.font()
        font.setPointSize(16)
        self._status_label.setFont(font)

        self._detail_label = QLabel("")
        self._detail_label.setWordWrap(True)

        stop_btn = QPushButton("Stop session")
        stop_btn.clicked.connect(self._on_stop_clicked)

        self._result_panel = QTextEdit()
        self._result_panel.setReadOnly(True)
        self._result_panel.hide()

        layout.addStretch(1)
        layout.addWidget(self._status_label)
        layout.addWidget(self._detail_label)
        layout.addWidget(stop_btn)
        layout.addWidget(self._result_panel)
        layout.addStretch(1)
        return w

    # ---------------------------------------------------------------- start
    def _on_start_clicked(self) -> None:
        email = self._email_field.text().strip()
        if not EMAIL_RE.match(email):
            QMessageBox.warning(self, "Invalid email", "Enter a valid contributor email.")
            return
        exe_display = self._process_field.currentText().strip()
        if not exe_display:
            QMessageBox.warning(self, "No process selected",
                                 "Refresh and pick the game's running process.")
            return
        pid = find_pid_by_exe(exe_display)
        if pid is None:
            QMessageBox.warning(self, "Process not found",
                                 f"{exe_display} is no longer running.")
            return

        meta = SessionMetadata(
            contributor_email=email,
            skill_level=self._skill_field.currentText(),
            role=self._role_field.text().strip(),
            objective_task=self._objective_field.text().strip(),
            game_pid=pid,
            game_exe_name=exe_display,
            game_display_pick=self._game_field.currentText(),
        )
        self._engine = SessionEngine(
            status_fn=self._runner.status_callback(token="session"))
        self._result_panel.hide()
        self._status_label.setText("Starting...")
        self._detail_label.setText("")
        self._stack.setCurrentWidget(self._recording_widget)
        self._runner.run("session", self._engine.run(meta))

    def _on_stop_clicked(self) -> None:
        if self._engine is not None:
            self._engine.cancel()
            self._status_label.setText("Stopping...")

    def _on_progress(self, token: object, stage: str, detail: str, pct: object) -> None:
        if token != "session":
            return
        self._status_label.setText(stage.replace("_", " ").title())
        self._detail_label.setText(detail)

    def _on_session_finished(self, token: object, result: object) -> None:
        if token != "session":
            return
        self._engine = None
        ok = bool(getattr(result, "ready_for_upload", False))
        lines = [f"QA status: {getattr(result, 'qa_status', '?')}"]
        for f in getattr(result, "self_check_failures", []):
            lines.append(f"FAIL: {f}")
        for wmsg in getattr(result, "self_check_warnings", []):
            lines.append(f"WARN: {wmsg}")
        self._status_label.setText("Ready for upload" if ok else "Needs attention")
        self._status_label.setProperty("status", "ok" if ok else "error")
        self._result_panel.setPlainText("\n".join(lines))
        self._result_panel.show()
        self._refresh_recent_sessions()

    def _on_session_failed(self, token: object, error: Exception) -> None:
        if token != "session":
            return
        self._engine = None
        self._status_label.setText("Session failed")
        self._status_label.setProperty("status", "error")
        self._result_panel.setPlainText(str(error))
        self._result_panel.show()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        self._runner.stop()
        super().closeEvent(event)
