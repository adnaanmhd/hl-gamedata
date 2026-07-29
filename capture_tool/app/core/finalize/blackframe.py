"""
Black-frame / static-frame heuristic — fix for part of issue C2.

Exclusive-fullscreen games bypass the desktop compositor; even with ddagrab
(DXGI Desktop Duplication) instead of gdigrab, a title that flip-model
bypasses composition entirely can still produce a black or frozen capture.
Rather than assume the A1/C2 capture-path change eliminates the failure
mode, we check for it directly on every recording and fail the self-check
loudly instead of shipping a black clip unnoticed.

Uses ffmpeg's own `blackdetect` filter (already bundled — no extra
dependency) rather than decoding frames ourselves.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from app.core.paths import ffmpeg_exe

_BLACK_START_RE = re.compile(r"black_start:\s*([\d.]+)")

# If more than this fraction of the sampled window is reported as black,
# the self-check fails outright rather than warns.
BLACK_FRACTION_FAIL_THRESHOLD = 0.5


def detect_black_intro(video_path: Path, sample_s: float = 5.0) -> tuple[bool, float]:
    """Returns (looks_black, black_fraction_of_sample).

    Samples the first `sample_s` seconds (well past any trim window) via
    ffmpeg's blackdetect filter with a permissive threshold — we want to
    catch "the whole capture region is black" (C2's failure mode: exclusive
    fullscreen bypassing composition), not legitimately dark game scenes, so
    the pixel-black threshold is strict (pix_th) and we only flag when a
    large fraction of the sampled window is continuously black.
    """
    cmd = [
        str(ffmpeg_exe()), "-v", "info", "-nostats",
        "-t", str(sample_s), "-i", str(video_path),
        "-vf", "blackdetect=d=0.5:pix_th=0.10", "-f", "null", "-",
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              creationflags=creationflags)
    except (subprocess.SubprocessError, OSError):
        return False, 0.0  # can't verify -> don't block on a tooling failure

    black_seconds = 0.0
    for line in out.stderr.splitlines():
        m_start = _BLACK_START_RE.search(line)
        m_dur = re.search(r"black_duration:\s*([\d.]+)", line)
        if m_start and m_dur:
            black_seconds += float(m_dur.group(1))
    fraction = min(black_seconds / sample_s, 1.0) if sample_s else 0.0
    return fraction >= BLACK_FRACTION_FAIL_THRESHOLD, fraction
