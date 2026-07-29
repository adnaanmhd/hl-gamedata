"""
Centralized path resolution. Everything lives under %LOCALAPPDATA%\\HumynCapture
so we never touch any system-wide config or %APPDATA%.

Layout:
    %LOCALAPPDATA%\\HumynCapture\\
        ffmpeg\\
            ffmpeg.exe              bundled ffmpeg binary
        sessions\\                  recorded sessions go here
            <session_id>\\
                video.mp4
                inputs.jsonl
                metadata.json
                session.json        # v2 delivery (native finalize output)
                frames.csv           # v2 delivery
                session.rrd          # v2 delivery
                rrd_creation.py      # v2 delivery
        logs\\                      app logs
        state.json                  first-run flag, version
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _localappdata() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base)
        return Path.home() / "AppData" / "Local"
    # Non-Windows dev/test environments (this repo is authored/tested on
    # macOS — see capture_tool/README.md): fall back to a POSIX-friendly
    # equivalent so the module tree still imports and unit tests can run.
    return Path.home() / ".local" / "share"


ROOT = _localappdata() / "HumynCapture"
FFMPEG_DIR = ROOT / "ffmpeg"
SESSIONS_DIR = ROOT / "sessions"
LOGS_DIR = ROOT / "logs"
STATE_FILE = ROOT / "state.json"


def ensure_dirs() -> None:
    for d in (ROOT, FFMPEG_DIR, SESSIONS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def ffmpeg_exe() -> Path:
    """Locate ffmpeg.exe inside our ffmpeg bundle.

    The gyan.dev essentials layout is FFMPEG_DIR/<release>/bin/ffmpeg.exe;
    we also check FFMPEG_DIR/ffmpeg.exe for a flat layout.
    """
    direct = FFMPEG_DIR / "ffmpeg.exe"
    if direct.exists():
        return direct
    if FFMPEG_DIR.exists():
        for sub in FFMPEG_DIR.iterdir():
            candidate = sub / "bin" / "ffmpeg.exe"
            if candidate.exists():
                return candidate
    return direct
