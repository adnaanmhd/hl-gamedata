"""Real (not mocked) coverage for C3's remux-repair path, using an actual
truncated fragmented MP4 (matches what a real `kill -9` mid-write produces)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core import ffmpeg_recorder as fr
from app.core.ffmpeg_recorder import FFmpegRecorder

SYSTEM_FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(
    SYSTEM_FFMPEG is None, reason="requires a system ffmpeg on PATH")


@pytest.fixture
def ffmpeg_on_app_path(monkeypatch):
    monkeypatch.setattr(fr, "ffmpeg_exe", lambda: Path(SYSTEM_FFMPEG))


@pytest.fixture
def complete_fragmented_mp4(tmp_path):
    path = tmp_path / "complete.mp4"
    subprocess.run(
        [SYSTEM_FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=640x480:rate=30", "-t", "3",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", "15",
         "-pix_fmt", "yuv420p",
         "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
         str(path)],
        check=True, timeout=30)
    return path.read_bytes()


class TestRealRemuxRepair:
    @pytest.mark.parametrize("pct", [0.15, 0.35, 0.5, 0.75, 0.9])
    def test_recovers_a_valid_playable_file_from_a_real_truncation(
            self, tmp_path, complete_fragmented_mp4, ffmpeg_on_app_path, pct):
        n = int(len(complete_fragmented_mp4) * pct)
        truncated = tmp_path / "truncated.mp4"
        truncated.write_bytes(complete_fragmented_mp4[:n])

        rec = FFmpegRecorder()
        assert rec._attempt_remux_repair(truncated) is True

        out = subprocess.run(
            [SYSTEM_FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error",
             "-show_entries", "format=duration", "-of",
             "default=noprint_wrappers=1", str(truncated)],
            capture_output=True, text=True, timeout=10)
        assert "duration=" in out.stdout
        assert float(out.stdout.strip().split("=")[1]) > 0

    def test_fails_gracefully_with_nothing_to_recover(
            self, tmp_path, complete_fragmented_mp4, ffmpeg_on_app_path):
        truncated = tmp_path / "truncated.mp4"
        truncated.write_bytes(complete_fragmented_mp4[:28])

        rec = FFmpegRecorder()
        assert rec._attempt_remux_repair(truncated) is False

    def test_completely_missing_file_does_not_raise(self, tmp_path, ffmpeg_on_app_path):
        rec = FFmpegRecorder()
        assert rec._attempt_remux_repair(tmp_path / "does_not_exist.mp4") is False
