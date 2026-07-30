"""Regression coverage for the A1 hardware-encoder fallback.

Real bug hit on a Windows box during manual testing: h264_nvenc was *listed*
in `ffmpeg -encoders` (compiled in) but failed to actually open because the
installed NVIDIA driver's NVENC API version (12.2) was older than what this
ffmpeg build requires (13.1) — `_detect_hw_encoder` used to trust the listing
alone, so the whole session died instead of falling back to a working
encoder. `_encoder_opens` now runs a real preflight encode; these tests cover
the selection logic around it without needing a real ffmpeg/GPU.
"""
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
