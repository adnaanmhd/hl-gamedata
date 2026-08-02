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


def _concat_clips(tmp_path, out_name, *clip_paths):
    """Concatenate same-codec/resolution clips via ffmpeg's concat demuxer."""
    list_path = tmp_path / f"{out_name}_list.txt"
    list_path.write_text("".join(f"file '{p}'\n" for p in clip_paths))
    out_path = tmp_path / f"{out_name}.mp4"
    subprocess.run(
        [SYSTEM_FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(list_path),
         "-c", "copy", str(out_path)],
        check=True, timeout=30)
    return out_path


class TestPersistentBlackCapture:
    """Real false positive confirmed on a real delivery (Outer Wilds): a
    legitimately dark narrative opening (splash screen + a "wake up in the
    dark" beat) is pixel-indistinguishable from a genuinely broken
    exclusive-fullscreen capture if only the first few seconds are checked.
    These tests build real multi-segment clips (concatenated, not mocked)
    to prove the fix distinguishes "dark intro, real content later" from
    "black literally everywhere"."""

    def test_dark_intro_with_real_content_later_does_not_fail(self, tmp_path, ffmpeg_on_app_path):
        black_part = _make_clip(tmp_path, "black_part", "color=c=black:s=320x240", duration=12)
        real_part = _make_clip(tmp_path, "real_part", "testsrc=size=320x240:rate=30", duration=40)
        combined = _concat_clips(tmp_path, "dark_intro_then_real", black_part, real_part)

        looks_black, detail = blackframe.detect_persistent_black_capture(
            combined, sample_s=5.0, num_checkpoints=4)

        assert looks_black is False
        assert detail["intro_fraction"] > 0.9  # intro genuinely is black
        # at least one later checkpoint must show real (non-black) content
        assert any(f < 0.5 for start, f in detail["checkpoints"].items() if start > 0)

    def test_black_for_the_entire_video_still_fails(self, tmp_path, ffmpeg_on_app_path):
        clip = _make_clip(tmp_path, "all_black", "color=c=black:s=320x240", duration=50)
        looks_black, detail = blackframe.detect_persistent_black_capture(
            clip, sample_s=5.0, num_checkpoints=4)
        assert looks_black is True
        assert all(f > 0.9 for f in detail["checkpoints"].values())

    def test_normal_video_throughout_passes(self, tmp_path, ffmpeg_on_app_path):
        clip = _make_clip(tmp_path, "all_normal", "testsrc=size=320x240:rate=30", duration=50)
        looks_black, detail = blackframe.detect_persistent_black_capture(
            clip, sample_s=5.0, num_checkpoints=4)
        assert looks_black is False

    def test_short_video_falls_back_to_intro_only_check(self, tmp_path, ffmpeg_on_app_path):
        """Too short to spread 4 checkpoints across without overlap -- must
        fall back gracefully to the plain intro check, not crash."""
        clip = _make_clip(tmp_path, "short_black", "color=c=black:s=320x240", duration=6)
        looks_black, detail = blackframe.detect_persistent_black_capture(
            clip, sample_s=5.0, num_checkpoints=4)
        assert looks_black is True
        assert detail["checkpoints"] == {0.0: detail["intro_fraction"]}
