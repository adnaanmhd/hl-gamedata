"""ffprobe wrapper. ffprobe.exe ships inside our ffmpeg bundle.

--- Fix applied here (issue A3) ---
The original implementation reported the container's nominal `r_frame_rate`
(30/1 on every session, regardless of what was actually achieved — see the
evidence table in HumynCapture_Capture_Tool_Issues.md: measured averages were
24.1-26.4 fps against a reported 30.0). `probe_video()` now returns BOTH
`fps_nominal` (the advertised rate, for reference) and `fps_avg` computed as
`nb_frames / duration_s` — the true achieved rate — plus `nb_frames`,
`duration_s`, and `has_audio` (needed for D2's metadata fields and C1's
audio-track reporting). Callers must not treat a bare `fps` as CFR-30.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.core.paths import FFMPEG_DIR


def _ffprobe_path() -> str:
    """
    Locate ffprobe.exe inside our ffmpeg bundle. The gyan.dev essentials
    layout is FFMPEG_DIR/<release>/bin/ffprobe.exe; we also check
    FFMPEG_DIR/ffprobe.exe for a flat layout, then fall back to PATH.
    """
    direct = FFMPEG_DIR / "ffprobe.exe"
    if direct.exists():
        return str(direct)
    if FFMPEG_DIR.exists():
        for sub in FFMPEG_DIR.iterdir():
            candidate = sub / "bin" / "ffprobe.exe"
            if candidate.exists():
                return str(candidate)
    return "ffprobe"


def _parse_rational(s: str | None) -> float | None:
    if not s:
        return None
    if "/" in s:
        num, _, den = s.partition("/")
        try:
            num_f, den_f = float(num), float(den)
            return num_f / den_f if den_f else None
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def probe_video(path: Path) -> dict[str, Any] | None:
    """Best-effort ffprobe of `path`. Returns None on any failure (caller
    treats that as 'video metadata unavailable', not fatal)."""
    cmd = [
        _ffprobe_path(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              creationflags=creationflags, check=True)
        data = json.loads(out.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None

    streams = data.get("streams") or []
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if vstream is None:
        return None

    fmt = data.get("format") or {}
    duration_s = None
    for src in (vstream.get("duration"), fmt.get("duration")):
        try:
            duration_s = float(src)
            break
        except (TypeError, ValueError):
            continue

    nb_frames = None
    for src in (vstream.get("nb_frames"),):
        try:
            nb_frames = int(src)
            break
        except (TypeError, ValueError):
            continue

    fps_nominal = _parse_rational(vstream.get("r_frame_rate"))
    fps_avg = None
    if nb_frames and duration_s:
        fps_avg = nb_frames / duration_s
    if fps_avg is None:
        fps_avg = _parse_rational(vstream.get("avg_frame_rate")) or fps_nominal

    return {
        "codec": vstream.get("codec_name"),
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "fps_nominal": fps_nominal,
        "fps_avg": fps_avg,
        "nb_frames": nb_frames,
        "duration_s": duration_s,
        "has_audio": has_audio,
    }
