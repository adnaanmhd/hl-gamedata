"""Regression coverage for two real bugs found on an actual delivery:

1. write_script() wrote rrd_creation.py without encoding="utf-8" — the
   script contains "§" and "—", which Path.write_text() with no encoding
   writes using the platform default (e.g. cp1252 on Windows), producing
   bytes that are valid cp1252 but NOT valid UTF-8. Anything reading the
   file back as UTF-8 saw "�" mojibake instead.
2. generate()'s default path shells out to `[sys.executable, script, ...]`
   to produce session.rrd. Inside a frozen PyInstaller exe, sys.executable
   IS the frozen exe, not a Python interpreter — session.rrd never got
   produced (rrd_creation.py was written, session.rrd was not, on a real
   Windows delivery). in_process=True must produce it without a subprocess.
"""
from pathlib import Path

from translator import rrd


def test_write_script_is_valid_utf8(tmp_path):
    path = rrd.write_script(tmp_path)
    # Must round-trip through UTF-8 without loss — this is exactly the
    # check that would have caught the cp1252-default mojibake bug.
    text = path.read_text(encoding="utf-8")
    assert "§" in text
    assert "—" in text


def test_generate_in_process_produces_rrd_without_subprocess(tmp_path, monkeypatch):
    """in_process=True must not touch subprocess at all — that's the whole
    point (a frozen exe has no python interpreter to shell out to)."""
    import subprocess as sp

    def _boom(*a, **kw):
        raise AssertionError("in_process=True must not call subprocess.run")

    monkeypatch.setattr(sp, "run", _boom)

    session_dir = tmp_path
    (session_dir / "session.json").write_text('{"canonical": {}}')
    (session_dir / "frames.csv").write_text(
        "frame_id,timestamp_ms,input_keys,input_actions,input_mouse_buttons,"
        "input_mouse_dx,input_mouse_dy\n0,0,,,,,\n")
    (session_dir / "video.mp4").write_bytes(b"")

    rrd_path = rrd.generate(session_dir, in_process=True)

    assert rrd_path == session_dir / "session.rrd"
    assert rrd_path.exists()
