"""Application-wide Qt stylesheet. Dark, minimal — a recording tool should
stay visually quiet while a game has focus."""
from __future__ import annotations

STYLESHEET = """
QWidget {
    background-color: #1b1d21;
    color: #e6e6e6;
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
}
QMainWindow, QDialog {
    background-color: #1b1d21;
}
QPushButton {
    background-color: #2a2d33;
    border: 1px solid #3a3d44;
    border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover {
    background-color: #34383f;
}
QPushButton:disabled {
    color: #7a7d84;
}
QLineEdit, QComboBox {
    background-color: #24262b;
    border: 1px solid #3a3d44;
    border-radius: 4px;
    padding: 4px 6px;
}
QProgressBar {
    border: 1px solid #3a3d44;
    border-radius: 4px;
    text-align: center;
}
QProgressBar::chunk {
    background-color: #4c8bf5;
}
QLabel[status="ok"] { color: #6fcf7f; }
QLabel[status="warn"] { color: #f2c94c; }
QLabel[status="error"] { color: #eb5757; }
"""
