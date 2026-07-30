"""
Main window.

Two states:
    - IDLE: shows the metadata form + 'Start recording' button + recent sessions
    - RECORDING: shows a compact recording widget; collapses everything else

We keep the GUI simple by toggling the central widget between these two
modes rather than building a real navigation stack. Good enough for v0.

--- Restored to match the shipped app ---
This file's class body failed to decompile at all originally ("pass #
WARNING: Decompyle incomplete") — the version previously here was written
from scratch against only the module docstring, and it visibly didn't match
the real app: different window size, no status bar, no live 2s process-list
refresh, no recording timer, a bare result panel instead of the real
QMessageBox summary dialog, different attribute/method names throughout.
This version is reconstructed from `pycdas` bytecode disassembly of the
shipped exe (Names/Constants/opcode trace per method), which recovers the
real structure exactly: widget tree, labels, spacing, the `class` properties
style.py's stylesheet actually styles ("primary"/"muted"/"card"/
"section-title"), and the true attribute/method names
(`game_process_combo`/`_selected_exe`/`_on_status_update`/`async_runner.
submit`/etc.) — matched 1:1 below except where a documented fix requires a
real behavior change (see "Deviations from the original" below).

--- Deviations from the original (intentional, not decompilation gaps) ---
- D1 fix: the original's game field (`game_name_input`) was a free-text
  QLineEdit — literally issue D1 ("game.name is free-text and frequently
  mistyped"). It's a QComboBox here, populated from
  `app.core.games.dropdown_titles()`, and the exe name resolved via
  `app.core.games.resolve_game()` is authoritative over whatever the
  dropdown shows (see session_engine.py). Attribute kept as
  `game_display_pick` to match `SessionMetadata`'s existing field.
- B2/E1/E2 fix: the original had no end-of-session self-check at all — every
  recording was unconditionally "Session saved". `_on_session_finished`
  below keeps the original's duration/event-count/warnings summary dialog
  verbatim, but adds the QA status and any self-check failures, since that
  gate is the entire point of the E2 fix.
- `_refresh_sessions_list` skips directories ending in `_raw` — the original
  had no such suffix because it had no separate raw-vs-finalized concept;
  that split is this fix's own native-v2-finalize architecture
  (session_engine.py writes `<id>_raw/` during recording, then finalizes
  into `<id>/`).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from app.core.games import dropdown_titles
from app.core.paths import SESSIONS_DIR
from app.core.process_watcher import find_pid_by_exe, list_likely_games
from app.core.session_engine import SessionEngine, SessionMetadata, SessionResult
from app.ui.async_runner import AsyncRunner

SKILL_LEVELS = ["novice", "intermediate", "expert"]
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class MainWindow(QMainWindow):
    status_signal = Signal(str, str, object)  # (stage, detail, pct|None)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HumynCapture")
        self.setMinimumSize(720, 600)

        self.async_runner = AsyncRunner()
        self.async_runner.start()
        self.async_runner.finished.connect(self._on_session_finished)
        self.async_runner.failed.connect(self._on_session_failed)

        self.session_engine: SessionEngine | None = None
        self._recording_start: float | None = None
        self.status_signal.connect(self._on_status_update)

        self.stack = QStackedWidget()
        self.idle_widget = self._build_idle_view()
        self.recording_widget = self._build_recording_view()
        self.stack.addWidget(self.idle_widget)
        self.stack.addWidget(self.recording_widget)
        self.setCentralWidget(self.stack)

        self.statusBar().showMessage("Ready")
        self._refresh_sessions_list()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(2000)
        self.refresh_timer.timeout.connect(self._refresh_running_games)
        self.refresh_timer.start()
        self._refresh_running_games()

    # ------------------------------------------------------------------ IDLE
    def _build_idle_view(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(20)

        header = QLabel("New session")
        header.setStyleSheet("font-size: 22px; font-weight: 600;")
        root.addWidget(header)

        subtitle = QLabel("Launch your game first, fill out the details below, "
                           "then start.")
        subtitle.setProperty("class", "muted")
        root.addWidget(subtitle)

        form_card = QFrame()
        form_card.setProperty("class", "card")
        form_layout = QFormLayout(form_card)
        form_layout.setContentsMargins(24, 22, 24, 22)
        form_layout.setVerticalSpacing(18)
        form_layout.setHorizontalSpacing(20)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)

        FIELD_MIN_HEIGHT = 34

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("you@example.com")
        self.email_input.setMinimumHeight(FIELD_MIN_HEIGHT)
        form_layout.addRow("Email", self.email_input)

        # D1 fix: dropdown only, never free text — see module docstring
        # "Deviations from the original". The real app had a QLineEdit here
        # (`game_name_input`, placeholder "e.g. Life is Strange 2") — that
        # free-text field is literally issue D1.
        self.game_input = QComboBox()
        self.game_input.addItems(dropdown_titles())
        self.game_input.setMinimumHeight(FIELD_MIN_HEIGHT)
        form_layout.addRow("Game", self.game_input)

        self.game_process_combo = QComboBox()
        self.game_process_combo.setEditable(False)
        self.game_process_combo.addItem("— pick from running games —", "")
        self.game_process_combo.setMinimumHeight(FIELD_MIN_HEIGHT)
        form_layout.addRow("Game .exe", self.game_process_combo)

        self.role_input = QLineEdit()
        self.role_input.setPlaceholderText("e.g. explorer, scout, courier")
        self.role_input.setMinimumHeight(FIELD_MIN_HEIGHT)
        form_layout.addRow("Role in game", self.role_input)

        self.objective_task_input = QLineEdit()
        self.objective_task_input.setPlaceholderText(
            "What you're doing this session — e.g. complete chapter 1 intro")
        self.objective_task_input.setMinimumHeight(FIELD_MIN_HEIGHT)
        form_layout.addRow("Objective / Task", self.objective_task_input)

        self.skill_combo = QComboBox()
        self.skill_combo.addItems(SKILL_LEVELS)
        self.skill_combo.setMinimumHeight(FIELD_MIN_HEIGHT)
        form_layout.addRow("Skill level", self.skill_combo)

        root.addWidget(form_card)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.start_btn = QPushButton("Start recording")
        self.start_btn.setProperty("class", "primary")
        self.start_btn.setMinimumWidth(180)
        self.start_btn.setMinimumHeight(38)
        self.start_btn.clicked.connect(self._on_start_clicked)
        action_row.addWidget(self.start_btn)
        root.addLayout(action_row)

        sessions_label = QLabel("Recent sessions")
        sessions_label.setProperty("class", "section-title")
        root.addWidget(sessions_label)

        self.sessions_list = QListWidget()
        self.sessions_list.setMaximumHeight(140)
        self.sessions_list.itemDoubleClicked.connect(self._open_session_dir)
        root.addWidget(self.sessions_list)

        return w

    # ------------------------------------------------------------- RECORDING
    def _build_recording_view(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(16)

        title = QLabel("Recording…")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        root.addWidget(title)

        self.recording_status = QLabel("Setting up…")
        self.recording_status.setProperty("class", "muted")
        root.addWidget(self.recording_status)

        self.timer_label = QLabel("00:00")
        timer_font = QFont()
        timer_font.setPointSize(48)
        timer_font.setWeight(QFont.Weight.Light)
        self.timer_label.setFont(timer_font)
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.timer_label)

        info_card = QFrame()
        info_card.setProperty("class", "card")
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(20, 16, 20, 16)
        self.recording_info = QLabel(
            "Quit your game when you're done — we'll auto-save the session.")
        self.recording_info.setWordWrap(True)
        info_layout.addWidget(self.recording_info)
        root.addWidget(info_card)

        root.addStretch(1)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.stop_recording_btn = QPushButton("Stop recording")
        self.stop_recording_btn.setProperty("class", "primary")
        self.stop_recording_btn.setMinimumWidth(180)
        self.stop_recording_btn.setMinimumHeight(38)
        self.stop_recording_btn.clicked.connect(self._on_stop_recording)
        action_row.addWidget(self.stop_recording_btn)
        root.addLayout(action_row)

        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._update_timer_label)

        return w

    # ------------------------------------------------------------- refreshers
    def _refresh_running_games(self) -> None:
        prev_data = self.game_process_combo.currentData()
        likely = list_likely_games()
        self.game_process_combo.blockSignals(True)
        self.game_process_combo.clear()
        self.game_process_combo.addItem("— pick from running games —", "")
        for proc in likely[:50]:
            label = f"{proc['name']}  (PID {proc['pid']})"
            self.game_process_combo.addItem(label, proc["name"])
        if prev_data:
            idx = self.game_process_combo.findData(prev_data)
            if idx >= 0:
                self.game_process_combo.setCurrentIndex(idx)
        self.game_process_combo.blockSignals(False)

    def _refresh_sessions_list(self) -> None:
        """Show finalized sessions (the actual v2 delivery, has session.json)
        newest first.

        Real bug fixed here: this used to glob SESSIONS_DIR's TOP LEVEL only
        and skip `_raw`-suffixed names — but `translator.v2.translate_bundle_v2`
        (called from finalize/pipeline.py) writes the finalized output
        nested under `<SESSIONS_DIR>/<vendor>/<mm-dd-yyyy>/<game_slug>/
        <session_id>/`, not as a flat sibling of `<session_id>_raw/`. The
        old code found nothing there but the vendor folder itself (e.g.
        "humynlabs"), which it then listed as if it were a session. Finding
        every `session.json` anywhere under SESSIONS_DIR is correct
        regardless of how deep translator nests things.
        """
        self.sessions_list.clear()
        if not SESSIONS_DIR.exists():
            return
        finalized = sorted(SESSIONS_DIR.rglob("session.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        for session_json in finalized:
            s = session_json.parent
            video = s / "video.mp4"
            size_mb = (video.stat().st_size / 1024 / 1024) if video.exists() else 0
            label = (f"{s.name}    "
                      f"{'video.mp4 (' + f'{size_mb:.0f}' + ' MB)' if video.exists() else 'no video'}")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(s))
            self.sessions_list.addItem(item)

    def _open_session_dir(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        import os
        import subprocess
        import sys
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 — path is our own session dir, not user input
            return
        subprocess.Popen(["xdg-open", path])

    # ----------------------------------------------------------- start/stop
    def _validate_form(self) -> tuple[bool, str]:
        email = self.email_input.text().strip()
        if not email:
            return False, "Email is required."
        if not EMAIL_RE.match(email):
            return False, (f"'{email}' doesn't look like a valid email address. "
                            f"Please use the email you registered with us.")
        if not self._selected_exe():
            return False, "Pick the game's .exe from the dropdown."
        if not self.role_input.text().strip():
            return False, "Role is required."
        if not self.objective_task_input.text().strip():
            return False, "Objective / Task is required."
        return True, ""

    def _selected_exe(self) -> str:
        """Get the user's chosen game .exe from the dropdown."""
        combo_data = self.game_process_combo.currentData() or ""
        return combo_data.strip() if isinstance(combo_data, str) else ""

    def _on_start_clicked(self) -> None:
        ok, err = self._validate_form()
        if not ok:
            self._show_warning("Missing info", err)
            return
        exe = self._selected_exe()
        pid = find_pid_by_exe(exe)
        if pid is None:
            self._show_warning(
                "Game not running",
                f"No running process named '{exe}'.\n\n"
                f"Launch your game first, then click Start recording.")
            return

        meta = SessionMetadata(
            contributor_email=self.email_input.text().strip(),
            game_display_pick=self.game_input.currentText().strip(),
            role=self.role_input.text().strip(),
            objective_task=self.objective_task_input.text().strip(),
            skill_level=self.skill_combo.currentText(),
            game_exe_name=exe,
            game_pid=pid,
        )
        self.stack.setCurrentWidget(self.recording_widget)
        self.recording_status.setText("Setting up…")
        self.timer_label.setText("00:00")
        self.stop_recording_btn.setEnabled(True)

        self.session_engine = SessionEngine(
            status_fn=lambda stage, detail, pct: self.status_signal.emit(stage, detail, pct))
        self.async_runner.submit(self.session_engine.run(meta))
        self._recording_start = None

    def _on_status_update(self, stage: str, detail: str, pct: object) -> None:
        self.recording_status.setText(detail)
        if stage == "playing" and self._recording_start is None:
            self._recording_start = time.monotonic()
            self.elapsed_timer.start()

    def _update_timer_label(self) -> None:
        if self._recording_start is None:
            return
        elapsed = int(time.monotonic() - self._recording_start)
        mins, secs = divmod(elapsed, 60)
        hours, mins = divmod(mins, 60)
        if hours:
            self.timer_label.setText(f"{hours:02d}:{mins:02d}:{secs:02d}")
        else:
            self.timer_label.setText(f"{mins:02d}:{secs:02d}")

    def _on_stop_recording(self) -> None:
        if self.session_engine:
            self.session_engine.cancel()
        self.recording_status.setText("Stopping — finalizing files…")
        self.stop_recording_btn.setEnabled(False)

    def _on_session_finished(self, result: object) -> None:
        self.elapsed_timer.stop()
        self._recording_start = None
        if isinstance(result, SessionResult):
            mins = int(result.duration_sec or 0) // 60
            secs = int(result.duration_sec or 0) % 60
            warnings_text = ""
            # B2/E1/E2 fix: the original had no self-check at all, so every
            # recording just showed this summary unconditionally. QA status
            # and any self-check failures are appended here — that gate is
            # the entire point of the E2 fix; see module docstring.
            qa_lines = [f"QA status: {result.qa_status}"]
            qa_lines += [f"FAIL: {f}" for f in result.self_check_failures]
            qa_lines += [f"WARN: {w}" for w in result.self_check_warnings]
            warnings_text = "\n\n" + "\n".join(qa_lines)
            QMessageBox.information(
                self, "Session saved",
                f"Recorded {mins}m {secs}s\n"
                f"Total input events: {result.input_event_count}\n"
                f"Saved to:\n{result.out_dir}{warnings_text}")
        self.stack.setCurrentWidget(self.idle_widget)
        self._refresh_sessions_list()

    def _on_session_failed(self, err: object) -> None:
        self.elapsed_timer.stop()
        self._recording_start = None
        self.stack.setCurrentWidget(self.idle_widget)
        self._show_warning("Recording failed", str(err))

    def _show_warning(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        self.async_runner.stop()
        super().closeEvent(event)
