"""Regression coverage for the A1 hardware-encoder fallback.

Real bug hit on a Windows box during manual testing: h264_nvenc was *listed*
in `ffmpeg -encoders` (compiled in) but failed to actually open because the
installed NVIDIA driver's NVENC API version (12.2) was older than what this
ffmpeg build requires (13.1) — `_detect_hw_encoder` used to trust the listing
alone, so the whole session died instead of falling back to a working
encoder. `_encoder_opens` now runs a real preflight encode; these tests cover
the selection logic around it without needing a real ffmpeg/GPU.
"""
import io
from unittest.mock import patch

from app.core import ffmpeg_recorder as fr


def test_detect_hw_encoder_picks_first_that_lists_and_opens():
    with patch.object(fr, "_run_ffmpeg_query", return_value="h264_nvenc h264_qsv"), \
         patch.object(fr, "_encoder_opens", return_value=True) as opens:
        assert fr._detect_hw_encoder() == "h264_nvenc"
    opens.assert_called_once_with("h264_nvenc")


def test_detect_hw_encoder_falls_back_when_listed_encoder_fails_to_open():
    """The exact regression: nvenc is listed but its preflight open fails
    (driver too old) -> must fall through to the next candidate, not die."""
    listing = "h264_nvenc h264_qsv h264_amf"
    with patch.object(fr, "_run_ffmpeg_query", return_value=listing), \
         patch.object(fr, "_encoder_opens", side_effect=lambda enc: enc == "h264_qsv"):
        assert fr._detect_hw_encoder() == "h264_qsv"


def test_detect_hw_encoder_falls_back_to_libx264_when_nothing_opens():
    with patch.object(fr, "_run_ffmpeg_query", return_value="h264_nvenc h264_qsv h264_amf"), \
         patch.object(fr, "_encoder_opens", return_value=False):
        assert fr._detect_hw_encoder() == "libx264"


def test_detect_hw_encoder_skips_encoders_not_even_listed():
    """Never preflight an encoder ffmpeg doesn't claim to have — that's a
    wasted subprocess call, not a fallback decision."""
    with patch.object(fr, "_run_ffmpeg_query", return_value="h264_amf"), \
         patch.object(fr, "_encoder_opens", return_value=True) as opens:
        assert fr._detect_hw_encoder() == "h264_amf"
    opens.assert_called_once_with("h264_amf")


class TestStderrMonitorProgressCapture:
    """Regression coverage for the real A2 fix: _StderrMonitor now captures
    the first `time=` progress line ffmpeg prints, paired with the
    monotonic instant we read it — this replaces reading the first frame's
    PTS back from the muxed file via ffprobe, which was confirmed broken on
    a real delivery (the PTS came back relative/near-zero, not wallclock,
    after ffmpeg's output muxing normalized it)."""

    def _run_monitor(self, lines: list[bytes]):
        stream = io.BytesIO(b"".join(lines))
        mon = fr._StderrMonitor(stream)
        mon._run()  # run synchronously in the test, not on a thread
        return mon

    def test_captures_first_time_progress_line(self):
        lines = [
            b"frame=   10 fps=0.0 q=20.0 size=     100kB time=00:00:00.33 bitrate= 100kbits/s\n",
            b"frame=   60 fps=30.0 q=20.0 size=     600kB time=00:00:02.00 bitrate= 100kbits/s\n",
        ]
        mon = self._run_monitor(lines)
        assert mon.first_progress_encoded_s == 0.33
        assert mon.first_progress_monotonic_s is not None

    def test_only_captures_the_first_time_line_not_later_ones(self):
        lines = [
            b"time=00:00:01.00\n",
            b"time=00:00:05.00\n",
        ]
        mon = self._run_monitor(lines)
        assert mon.first_progress_encoded_s == 1.0

    def test_parses_hours_minutes_correctly(self):
        lines = [b"time=01:02:03.50\n"]
        mon = self._run_monitor(lines)
        assert mon.first_progress_encoded_s == 3723.5

    def test_no_time_line_leaves_progress_fields_none(self):
        lines = [b"frame= 10 fps=0.0 q=20.0\n"]
        mon = self._run_monitor(lines)
        assert mon.first_progress_encoded_s is None
        assert mon.first_progress_monotonic_s is None

    def test_splits_on_carriage_return_not_just_newline(self):
        """Real precision bug avoided: ffmpeg's live -stats output is
        \\r-terminated (it overwrites one terminal line), not \\n-terminated.
        Splitting only on \\n would buffer several \\r-separated updates
        together and delay the captured timestamp until an unrelated later
        \\n arrives — silently hurting the exact precision the A2 fix
        depends on. Each \\r-terminated update must be handled as its own
        line, immediately."""
        # No \n anywhere — three \r-separated stats updates, the kind
        # ffmpeg actually emits to a terminal/pipe.
        stream_bytes = (
            b"frame=   5 fps=0.0 q=20.0 time=00:00:00.16 bitrate=100kbits/s\r"
            b"frame=  10 fps=0.0 q=20.0 time=00:00:00.33 bitrate=100kbits/s\r"
            b"frame=  60 fps=30.0 q=20.0 time=00:00:02.00 bitrate=100kbits/s\r"
        )
        mon = fr._StderrMonitor(io.BytesIO(stream_bytes))
        mon._run()
        assert mon.frames_encoded == 60
        assert mon.first_progress_encoded_s == 0.16  # from the FIRST \r-line
