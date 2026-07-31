"""Real (not mocked) coverage for the C2 black-frame heuristic, using
ffmpeg-generated synthetic clips. Guards against pix_th being too loose and
false-positiving on legitimately dark scenes."""
import shutil
import subprocess

import pytest

from app.core.finalize import blackframe

SYSTEM_FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(
    SYSTEM_FFMPEG is None, reason="requires a system ffmpeg on PATH")


def _make_clip(tmp_path, name, lavfi_source, duration=6):
    path = tmp_path / f"{name}.mp4"
    subprocess.run(
        [SYSTEM_FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"{lavfi_source}:d={duration}:r=30",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=30)
    return path


@pytest.fixture
def ffmpeg_on_app_path(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(blackframe, "ffmpeg_exe", lambda: Path(SYSTEM_FFMPEG))


class TestRealBlackFrameDetection:
    def test_pure_black_clip_is_detected(self, tmp_path, ffmpeg_on_app_path):
        clip = _make_clip(tmp_path, "black", "color=c=black:s=320x240")
        looks_black, fraction = blackframe.detect_black_intro(clip)
        assert looks_black is True
        assert fraction > 0.9

    def test_near_black_clip_is_still_detected(self, tmp_path, ffmpeg_on_app_path):
        clip = _make_clip(tmp_path, "near_black", "color=c=0x050505:s=320x240")
        looks_black, fraction = blackframe.detect_black_intro(clip)
        assert looks_black is True

    def test_dark_gray_scene_is_not_a_false_positive(self, tmp_path, ffmpeg_on_app_path):
        clip = _make_clip(tmp_path, "dark_gray", "color=c=0x101010:s=320x240")
        looks_black, fraction = blackframe.detect_black_intro(clip)
        assert looks_black is False

    def test_dark_scene_with_hud_detail_is_not_a_false_positive(self, tmp_path, ffmpeg_on_app_path):
        clip_path = tmp_path / "dark_with_detail.mp4"
        subprocess.run(
            [SYSTEM_FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=0x101010:s=320x240:d=6:r=30,"
                   "drawbox=x=10:y=10:w=15:h=15:color=0x606060:t=fill",
             "-pix_fmt", "yuv420p", str(clip_path)],
            check=True, timeout=30)
        looks_black, fraction = blackframe.detect_black_intro(clip_path)
        assert looks_black is False

    def test_normal_moving_content_is_not_a_false_positive(self, tmp_path, ffmpeg_on_app_path):
        clip = _make_clip(tmp_path, "normal", "testsrc=size=320x240:rate=30")
        looks_black, fraction = blackframe.detect_black_intro(clip)
        assert looks_black is False
        assert fraction == 0.0
