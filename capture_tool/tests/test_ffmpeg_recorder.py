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


class TestDdagrabDetectionAndInvocation:
    """Regression coverage for two real bugs found together: ddagrab is an
    avfilter SOURCE (`-f lavfi -i "ddagrab=..."`), not an avdevice.

    1. `_probe_ddagrab_support` checked `-devices` (where gdigrab lives) —
       ddagrab is never listed there even on ffmpeg builds that fully
       support it; it's listed under `-filters`. This meant ddagrab was
       NEVER detected/used on any machine, silently forcing the `gdigrab`
       fallback (and its C2 exclusive-fullscreen black-capture limitation)
       even where the bundled ffmpeg build genuinely supported ddagrab.
    2. `_build_command` invoked it as `-f ddagrab -i 0` (device syntax) —
       not how it works at all even if detection were fixed alone.
    """

    def test_probe_checks_filters_not_devices(self):
        with patch.object(fr, "_run_ffmpeg_query") as query, \
             patch.object(fr, "_ddagrab_opens", return_value=True):
            query.return_value = "ddagrab  V..... DXGI Desktop Duplication"
            assert fr._probe_ddagrab_support() is True
        query.assert_called_once_with(["-hide_banner", "-filters"])

    def test_probe_false_when_filter_not_listed(self):
        with patch.object(fr, "_run_ffmpeg_query", return_value="gdigrab  V....."):
            assert fr._probe_ddagrab_support() is False

    def test_probe_false_when_listed_but_fails_to_actually_open(self):
        """The exact regression this preflight guards against: ddagrab is
        listed in -filters AND correctly invoked, but still fails at
        runtime with "Selected output not supported" on some GPU/monitor
        configurations — listing alone must not be trusted."""
        with patch.object(fr, "_run_ffmpeg_query",
                          return_value="ddagrab  V..... DXGI Desktop Duplication"), \
             patch.object(fr, "_ddagrab_opens", return_value=False):
            assert fr._probe_ddagrab_support() is False

    def test_ddagrab_opens_false_on_nonzero_exit(self):
        fake = type("R", (), {"returncode": 1, "stderr": b"Selected output not supported"})()
        with patch.object(fr.subprocess, "run", return_value=fake):
            assert fr._ddagrab_opens() is False

    def test_ddagrab_opens_true_on_success(self):
        fake = type("R", (), {"returncode": 0, "stderr": b""})()
        with patch.object(fr.subprocess, "run", return_value=fake):
            assert fr._ddagrab_opens() is True

    def test_build_command_uses_lavfi_filter_syntax_when_ddagrab_available(self):
        recorder = fr.FFmpegRecorder()
        with patch.object(fr, "_probe_ddagrab_support", return_value=True), \
             patch.object(fr, "_detect_hw_encoder", return_value="libx264"):
            cmd = recorder._build_command((10, 20, 800, 600), "out.mp4")
        # Must NOT use the old, wrong device-style invocation.
        assert "ddagrab" not in cmd or "-f" not in cmd or \
            not (cmd[cmd.index("-f") + 1] == "ddagrab")
        assert "lavfi" in cmd
        i = cmd.index("lavfi")
        assert cmd[i - 1] == "-f"
        input_arg = cmd[cmd.index("-i") + 1]
        assert input_arg.startswith("ddagrab=")
        assert "video_size=800x600" in input_arg
        assert "offset_x=10" in input_arg
        assert "offset_y=20" in input_arg
        # No separate crop filter — ddagrab crops natively via the params above.
        assert "crop=" not in cmd[cmd.index("-vf") + 1]

    def test_build_command_falls_back_to_gdigrab_when_ddagrab_unavailable(self):
        recorder = fr.FFmpegRecorder()
        with patch.object(fr, "_probe_ddagrab_support", return_value=False), \
             patch.object(fr, "_detect_hw_encoder", return_value="libx264"):
            cmd = recorder._build_command((10, 20, 800, 600), "out.mp4")
        assert cmd[cmd.index("-f") + 1] == "gdigrab"


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
