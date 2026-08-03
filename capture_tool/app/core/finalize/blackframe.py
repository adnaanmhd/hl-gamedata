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
        # pix_th=0.05: 0.10 false-positived on legitimately dark scenes.
        "-vf", "blackdetect=d=0.5:pix_th=0.05", "-f", "null", "-",
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


# Real false positive confirmed on a real delivery (Outer Wilds): the first
# ~2 minutes were the game's own narrative opening (publisher splash screen,
# then the "wake up" beat, played out in near-total darkness by design) —
# pixel-wise indistinguishable from a genuinely broken exclusive-fullscreen
# capture. Sampling ONLY the intro can never tell these apart. A real C2
# failure stays black for the ENTIRE recording; legitimate dark content
# resolves into real footage, usually within the first minute or two.
NUM_CHECKPOINTS = 4


def _probe_duration_s(video_path: Path, ffmpeg_bin: Path) -> float:
    probe_name = ffmpeg_bin.name.replace("ffmpeg", "ffprobe", 1)
    ffprobe_bin = ffmpeg_bin.with_name(probe_name)
    cmd = [str(ffprobe_bin), "-v", "error", "-show_entries", "format=duration",
           "-of", "csv=p=0", str(video_path)]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                              creationflags=creationflags, check=True)
        return float(out.stdout.strip().split(",")[0])
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return 0.0


def _sample_black_fraction(video_path: Path, start_s: float, sample_s: float,
                            ffmpeg_bin: Path) -> float:
    cmd = [
        str(ffmpeg_bin), "-v", "info", "-nostats",
        "-ss", str(start_s), "-t", str(sample_s), "-i", str(video_path),
        "-vf", "blackdetect=d=0.5:pix_th=0.05", "-f", "null", "-",
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              creationflags=creationflags)
    except (subprocess.SubprocessError, OSError):
        return 0.0
    black_seconds = 0.0
    for line in out.stderr.splitlines():
        m_start = _BLACK_START_RE.search(line)
        m_dur = re.search(r"black_duration:\s*([\d.]+)", line)
        if m_start and m_dur:
            black_seconds += float(m_dur.group(1))
    return min(black_seconds / sample_s, 1.0) if sample_s else 0.0


def detect_persistent_black_capture(
    video_path: Path, sample_s: float = 5.0, num_checkpoints: int = NUM_CHECKPOINTS,
) -> tuple[bool, dict]:
    """Returns (looks_black, {"intro_fraction": float, "checkpoints": {offset_s: fraction}}).

    Samples `num_checkpoints` windows spread across the WHOLE video
    (including the original intro window at t=0) instead of only the
    intro. Only reports a real failure if EVERY checkpoint is
    majority-black — a genuine capture failure has no escape from that;
    a dark intro/narrative opening does, and shows real content at later
    checkpoints. Falls back to the plain intro-only check if duration
    can't be determined or the video is too short to spread checkpoints
    across.
    """
    ffmpeg_bin = ffmpeg_exe()
    duration_s = _probe_duration_s(video_path, ffmpeg_bin)

    if duration_s <= 0 or duration_s <= sample_s * 2 or num_checkpoints <= 1:
        looks_black, fraction = detect_black_intro(video_path, sample_s)
        return looks_black, {"intro_fraction": fraction, "checkpoints": {0.0: fraction}}

    usable = duration_s - sample_s
    offsets = [usable * i / (num_checkpoints - 1) for i in range(num_checkpoints)]

    fractions = {start: _sample_black_fraction(video_path, start, sample_s, ffmpeg_bin)
                 for start in offsets}
    looks_black = all(f >= BLACK_FRACTION_FAIL_THRESHOLD for f in fractions.values())
    return looks_black, {"intro_fraction": fractions[offsets[0]], "checkpoints": fractions}
