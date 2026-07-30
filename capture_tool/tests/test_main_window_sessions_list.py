"""Regression coverage for MainWindow._refresh_sessions_list finding the
REAL finalized-session layout.

Real bug: translator.v2.translate_bundle_v2 (called from
finalize/pipeline.py) writes finalized sessions nested under
`<SESSIONS_DIR>/<vendor>/<mm-dd-yyyy>/<game_slug>/<session_id>/`, not as a
flat `<SESSIONS_DIR>/<session_id>/` sibling of the `_raw` folder written
during recording. _refresh_sessions_list used to glob only the top level of
SESSIONS_DIR, so it found nothing but the vendor folder itself (e.g.
"humynlabs") and listed THAT as if it were a session, while the real
finalized sessions nested inside it were invisible.
"""
import os

from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    return QApplication.instance() or QApplication([])


def test_refresh_sessions_list_finds_nested_finalized_sessions(tmp_path, monkeypatch):
    _app()
    from app.ui import main_window
    # SESSIONS_DIR is imported by name into main_window's module namespace
    # (`from app.core.paths import SESSIONS_DIR`), so it must be patched
    # there, not on app.core.paths, to actually affect _refresh_sessions_list.
    monkeypatch.setattr(main_window, "SESSIONS_DIR", tmp_path)

    # A raw folder (written during recording) — must NOT show up as a session.
    raw = tmp_path / "2026-01-01T00-00-00Z_kamla_c_abc_raw"
    raw.mkdir()
    (raw / "video.mp4").write_bytes(b"x")

    # The real finalized layout: vendor/date/slug/session_id/, several
    # directories deep — must be found regardless of nesting.
    finalized = tmp_path / "humynlabs" / "01-01-2026" / "kamla" / "2026-01-01T00-00-00Z_kamla_c_abc"
    finalized.mkdir(parents=True)
    (finalized / "session.json").write_text("{}")
    (finalized / "video.mp4").write_bytes(b"x" * (2 * 1024 * 1024))

    from app.ui.main_window import MainWindow
    w = MainWindow()
    try:
        w._refresh_sessions_list()
        items = [w.sessions_list.item(i) for i in range(w.sessions_list.count())]
        paths_shown = [item.data(0x0100) for item in items]  # Qt.ItemDataRole.UserRole == 256
        assert str(finalized) in paths_shown
        assert str(raw) not in paths_shown
        assert str(tmp_path / "humynlabs") not in paths_shown
    finally:
        w.close()
