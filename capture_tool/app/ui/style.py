"""Centralized stylesheet. Dark, modern, minimal.

Restored verbatim from the shipped HumynCapture.exe (decompiled cleanly, no
gaps) — the version previously here was a from-scratch guess written when
this class's bytecode failed to decompile, and it visibly didn't match the
real app (different palette entirely: blue accent instead of the real
orange, no "primary"/"danger" button classes, no "section-title"/"muted"/
"warning" label classes, no card frames). main_window.py / setup_window.py
are being rewritten to actually use the `class` properties this stylesheet
defines, since the real app's widget tree failed to decompile the same way.
"""
from __future__ import annotations

STYLESHEET = """
* {
    color: #E8E8EA;
    font-family: "Segoe UI", "SF Pro Text", system-ui, sans-serif;
    font-size: 13px;
}

QMainWindow, QDialog, QWidget {
    background-color: #1A1A1C;
}

QLabel {
    background: transparent;
}

QLabel[class="section-title"] {
    font-size: 11px;
    font-weight: 600;
    color: #888;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}

QLabel[class="muted"] {
    color: #888;
}

QLabel[class="warning"] {
    color: #F0A968;
}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
    background-color: #232326;
    border: 1px solid #2E2E32;
    border-radius: 6px;
    padding: 6px 12px;
    min-height: 22px;
    selection-background-color: #FF6E42;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 1px solid #FF6E42;
}

QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #888;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #232326;
    border: 1px solid #2E2E32;
    selection-background-color: #FF6E42;
    outline: none;
}

QPushButton {
    background-color: #2A2A2E;
    border: 1px solid #2E2E32;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 500;
}
QPushButton:hover { background-color: #34343A; }
QPushButton:pressed { background-color: #232326; }
QPushButton:disabled { color: #555; background-color: #232326; }

QPushButton[class="primary"] {
    background-color: #FF6E42;
    color: #1A1A1C;
    border: none;
    font-weight: 600;
}
QPushButton[class="primary"]:hover { background-color: #FF855E; }
QPushButton[class="primary"]:pressed { background-color: #E55D33; }
QPushButton[class="primary"]:disabled { background-color: #4A2E22; color: #888; }

QPushButton[class="danger"] {
    background-color: transparent;
    color: #E06464;
    border: 1px solid #4A2A2A;
}
QPushButton[class="danger"]:hover { background-color: #2A1A1A; }

QProgressBar {
    background-color: #232326;
    border: 1px solid #2E2E32;
    border-radius: 4px;
    text-align: center;
    height: 8px;
}
QProgressBar::chunk {
    background-color: #FF6E42;
    border-radius: 3px;
}

QListWidget {
    background-color: #1F1F22;
    border: 1px solid #2E2E32;
    border-radius: 6px;
    padding: 4px;
    outline: none;
}
QListWidget::item {
    padding: 10px 12px;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: #2A2A2E;
}

QStatusBar {
    background-color: #1A1A1C;
    border-top: 1px solid #2E2E32;
    color: #888;
}

QFrame[class="card"] {
    background-color: #1F1F22;
    border: 1px solid #2E2E32;
    border-radius: 8px;
}
"""
