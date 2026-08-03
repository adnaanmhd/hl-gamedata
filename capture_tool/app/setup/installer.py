"""
First-run installer.

Downloads ffmpeg essentials build from gyan.dev (stable URL that always
redirects to the latest release). ~110 MB download, ~330 MB extracted.

That's all we need — the gyan.dev essentials build includes ffmpeg.exe,
ffprobe.exe, all codecs we use, and is statically linked so there are no
external DLL dependencies.
"""
from __future__ import annotations

import logging
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from app.core.paths import FFMPEG_DIR, ROOT, ensure_dirs, ffmpeg_exe

log = logging.getLogger(__name__)

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
ProgressFn = Callable[[str, "int | None"], None]


def _noop(message: str, pct: "int | None" = None) -> None:
    pass


def is_ffmpeg_installed() -> bool:
    return ffmpeg_exe().exists()


def _download(url: str, dest: Path, progress: ProgressFn) -> None:
    """Download with progress reporting. Uses urllib (stdlib) — no extra deps."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "HumynCapture/0.2"})
    with urllib.request.urlopen(req) as resp:
        total = int(resp.headers.get("Content-Length") or 0) or None
        downloaded = 0
        chunk_size = 65536
        with dest.open("wb") as fp:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                fp.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = int(downloaded * 100 / total)
                    progress(f"Downloading ffmpeg ({downloaded // 1_000_000} / "
                              f"{total // 1_000_000} MB)", pct)
                else:
                    progress(f"Downloading ffmpeg ({downloaded // 1_000_000} MB)", None)


def install_ffmpeg(progress: ProgressFn = _noop, force: bool = False) -> None:
    """
    Download and extract ffmpeg into FFMPEG_DIR. Idempotent unless force=True.

    Raises on any failure — caller (UI) catches and displays.
    """
    if is_ffmpeg_installed() and not force:
        progress("ffmpeg already installed", 100)
        return
    ensure_dirs()
    if force and FFMPEG_DIR.exists():
        shutil.rmtree(FFMPEG_DIR)
    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)

    archive = ROOT / "_dl" / "ffmpeg.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    try:
        progress("Downloading ffmpeg...", 0)
        _download(FFMPEG_URL, archive, progress)
        progress("Extracting ffmpeg...", None)
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(FFMPEG_DIR)
    finally:
        try:
            archive.unlink()
            archive.parent.rmdir()
        except (FileNotFoundError, OSError):
            pass

    if not is_ffmpeg_installed():
        contents = []
        if FFMPEG_DIR.exists():
            contents = [str(p.relative_to(FFMPEG_DIR))
                        for p in sorted(FFMPEG_DIR.rglob("*"))[:20]]
        raise RuntimeError(f"ffmpeg.exe not found after extraction. Contents: {contents}")
    progress("ffmpeg installed", 100)


def run_full_setup(progress: ProgressFn = _noop) -> None:
    """Top-level setup. Just ffmpeg for now; add more steps here later."""
    install_ffmpeg(progress)
