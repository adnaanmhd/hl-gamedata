"""Regression coverage for the [WinError 2] bug.

translator/{trim,video,rrd}.py invoke bare "ffmpeg"/"ffprobe", correct for
translator's own CLI/dev usage but not for the packaged app on an end-user
Windows machine with no system-wide ffmpeg — only the bundled copy under
%LOCALAPPDATA%\\HumynCapture\\ffmpeg\\. ensure_ffmpeg_on_path() fixes this by
prepending the bundled binary's directory to PATH before any translator call.
"""
import os

from app.core import paths


def test_ensure_ffmpeg_on_path_prepends_bundled_dir(tmp_path, monkeypatch):
    fake_ffmpeg = tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe"
    fake_ffmpeg.parent.mkdir(parents=True)
    fake_ffmpeg.write_text("")
    monkeypatch.setattr(paths, "ffmpeg_exe", lambda: fake_ffmpeg)
    monkeypatch.setenv("PATH", "/some/other/dir")

    paths.ensure_ffmpeg_on_path()

    entries = os.environ["PATH"].split(os.pathsep)
    assert str(fake_ffmpeg.parent) == entries[0]
    assert "/some/other/dir" in entries


def test_ensure_ffmpeg_on_path_does_not_duplicate(tmp_path, monkeypatch):
    fake_ffmpeg = tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe"
    fake_ffmpeg.parent.mkdir(parents=True)
    fake_ffmpeg.write_text("")
    monkeypatch.setattr(paths, "ffmpeg_exe", lambda: fake_ffmpeg)
    monkeypatch.setenv("PATH", str(fake_ffmpeg.parent))

    paths.ensure_ffmpeg_on_path()

    assert os.environ["PATH"].split(os.pathsep).count(str(fake_ffmpeg.parent)) == 1
