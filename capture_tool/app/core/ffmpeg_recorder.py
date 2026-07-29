"""
ffmpeg-based screen recorder.

--- Fixes applied here ---

A1 (12-20% dropped frames): the original pipeline was `gdigrab` (CPU-bound
GDI BitBlt of the desktop region) + software libx264 `preset=fast crf=20` at
1080p/30, running at normal process priority — under game load it couldn't
sustain 30 grabs+encodes/sec and dropped frames at the source, clustered
(2-12 consecutive frames) rather than uniformly. Fixed by:
  - `ddagrab` (DXGI Desktop Duplication, GPU-side) instead of `gdigrab`,
    falling back to `gdigrab` only if the bundled ffmpeg build lacks
    `ddagrab` support (`_probe_ddagrab_support`).
  - A detected hardware encoder (`h264_nvenc` / `h264_qsv` / `h264_amf`),
    falling back to libx264 `preset=veryfast` only if none is available
    (`_detect_hw_encoder`).
  - `-fps_mode cfr` for genuinely constant-rate output instead of relying on
    gdigrab's best-effort framerate.
  - `ABOVE_NORMAL_PRIORITY_CLASS` on the ffmpeg process so it isn't starved
    of CPU by the foregrounded game.
  - stderr is parsed continuously for ffmpeg's own `frame=`/`drop=` counters
    (`_StderrMonitor`) so `frames_dropped` is known and can gate the D2/E2
    self-check instead of only being inferable after the fact from PTS gaps.

C1 (no audio track): an optional WASAPI loopback input
(`-f wasapi -i <device>`) is added and muxed as AAC when
`RecorderConfig.audio_enabled` is set. Off by default — see
`RecorderConfig.audio_enabled` docstring: enabling it needs Odyssey
confirmation per the handoff doc, and it reintroduces independent-clock A/V
sync, which `has_audio` in metadata now makes visible either way (D2).

C2 (exclusive-fullscreen capture as black / desktop over-capture): `ddagrab`
captures the actual DXGI-composited output rather than a desktop GDI
surface, so exclusive-fullscreen games (which bypass the compositor for
`gdigrab`) are no longer guaranteed-black — though a title that still
flip-model bypasses composition can, in principle, still black out, so the
finalize-stage black-frame heuristic (see `app/core/finalize/blackframe.py`,
run from `SessionEngine.run`'s self-check) is the actual safety net, not this
module. This module also now crops to the game's client rect via a `crop`
filter (ddagrab always grabs a full output) instead of grabbing an arbitrary
desktop offset/size the way gdigrab could.

C3 (5s kill() truncates the MP4): `stop()`'s timeout now scales with
recording length (`_finalize_timeout_s`) instead of a flat 5s, output uses
fragmented MP4 (`+frag_keyframe+empty_moov+default_base_moof`) instead of
`+faststart` so a fragment boundary is always flushed and the file stays
structurally valid even if a later fragment is cut off, and a `kill()` now
triggers a best-effort remux repair (`_attempt_remux_repair`) that flags the
session as suspect rather than shipping a possibly-unreadable file silently.

Design notes carried over from the original module:
- We capture the actual composited screen output, not a window's GDI surface
  (`hwnd=` mode), because for DirectX/Vulkan/OpenGL games that surface is
  decoupled from the rendered content and yields blank/desktop pixels.
- 1080p / 30fps / CRF-equivalent quality is the v0 default. For training data
  we'd rather have quality over file size.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.paths import ffmpeg_exe

log = logging.getLogger(__name__)

ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
CREATE_NO_WINDOW = 0x08000000

MIN_FINALIZE_TIMEOUT_S = 30.0
MAX_FINALIZE_TIMEOUT_S = 120.0

_DROP_RE = re.compile(r"drop=\s*(\d+)")
_FRAME_RE = re.compile(r"frame=\s*(\d+)")


@dataclass
class RecorderConfig:
    fps: int = 30
    width: int = 1920
    height: int = 1080
    crf: int = 20
    preset: str = "veryfast"  # only used for the libx264 fallback path
    audio_enabled: bool = False  # C1 — off by default, see module docstring
    process_priority: int = ABOVE_NORMAL_PRIORITY_CLASS


@dataclass
class RecorderHealth:
    """Snapshot of what the recorder actually achieved — feeds D2's metadata
    fields and the A1/E2 self-check drop-threshold gate."""
    backend: str = "unknown"          # "ddagrab" | "gdigrab"
    encoder: str = "unknown"          # "h264_nvenc" | "h264_qsv" | "h264_amf" | "libx264"
    frames_dropped: int = 0
    frames_encoded: int = 0
    finalize_forced_kill: bool = False
    remux_repair_attempted: bool = False
    remux_repair_succeeded: bool = False


def _run_ffmpeg_query(args: list[str], timeout: float = 5.0) -> str:
    creationflags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run([str(ffmpeg_exe()), *args], capture_output=True,
                              text=True, timeout=timeout, creationflags=creationflags)
        return (out.stdout or "") + (out.stderr or "")
    except (subprocess.SubprocessError, OSError):
        return ""


def _detect_hw_encoder() -> str:
    """A1: prefer a hardware encoder; fall back to libx264 only if none of
    the bundled ffmpeg's encoders list a usable GPU path. Order reflects
    typical availability, not quality — any HW encoder beats software x264
    for our CPU-contention problem."""
    listing = _run_ffmpeg_query(["-hide_banner", "-encoders"])
    for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if enc in listing:
            return enc
    return "libx264"


def _probe_ddagrab_support() -> bool:
    listing = _run_ffmpeg_query(["-hide_banner", "-devices"])
    return "ddagrab" in listing


class _StderrMonitor:
    """Tails ffmpeg's stderr on a background thread, extracting the running
    `frame=`/`drop=` counters ffmpeg itself reports (A1/D2) — this is the
    authoritative drop count, not a PTS-gap inference after the fact."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self.frames_encoded = 0
        self.frames_dropped = 0
        self._lines: list[str] = []
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        for raw in iter(self._stream.readline, b""):
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                continue
            self._lines.append(line)
            m = _FRAME_RE.search(line)
            if m:
                self.frames_encoded = int(m.group(1))
            m = _DROP_RE.search(line)
            if m:
                self.frames_dropped = int(m.group(1))

    def tail(self, n: int = 20) -> list[str]:
        return self._lines[-n:]


class FFmpegRecorder:
    """
    Wraps an ffmpeg subprocess that captures the composited display output
    via ddagrab (DXGI Desktop Duplication), cropped to the game's rect.

    Lifecycle:
        rec = FFmpegRecorder(cfg)
        rec.start((x, y, w, h), output_path)  # synchronous
        ...
        rec.stop()  # synchronous, C3-aware finalize
    Use start_async/stop_async for asyncio.
    """

    def __init__(self, config: RecorderConfig | None = None) -> None:
        self.config = config or RecorderConfig()
        self.process: subprocess.Popen | None = None
        self._output_path: Path | None = None
        self._started_at_monotonic: float | None = None
        self._stderr_monitor: _StderrMonitor | None = None
        self.health = RecorderHealth()
        # A2: the (wallclock, monotonic) pairing taken at the instant ffmpeg
        # is launched. Combined with the encoded video's first-frame PTS
        # (wallclock, thanks to -use_wallclock_as_timestamps below), this is
        # what app.core.finalize.anchor uses to re-anchor input timestamps
        # to the real first frame instead of process-launch time.
        self.launch_pairing = None

    def is_recording(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _build_command(self, capture_rect: tuple[int, int, int, int],
                        output_path: Path) -> list[str]:
        """Build the ffmpeg invocation. `capture_rect` is (x, y, w, h) in
        screen coords of the game's client area; ddagrab always grabs a full
        display output, so we crop to the rect in the filter graph rather
        than at the grab stage the way gdigrab's -offset_x/-offset_y could."""
        cfg = self.config
        x, y, w, h = capture_rect
        w, h = w - (w % 2), h - (h % 2)  # even dims: yuv420p requirement

        use_ddagrab = _probe_ddagrab_support()
        self.health.backend = "ddagrab" if use_ddagrab else "gdigrab"
        encoder = _detect_hw_encoder()
        self.health.encoder = encoder

        cmd = [str(ffmpeg_exe()), "-y", "-loglevel", "warning", "-stats"]

        # A2: stamp frame PTS from the wallclock rather than a pipeline-
        # relative clock, so finalize's anchor step (app/core/finalize/
        # anchor.py) can read the first frame's *wallclock* instant back out
        # with ffprobe and re-anchor input timestamps to it.
        wallclock_ts = ["-use_wallclock_as_timestamps", "1"]
        if use_ddagrab:
            cmd += ["-f", "ddagrab", "-framerate", str(cfg.fps), *wallclock_ts, "-i", "0"]
            vf = f"crop={w}:{h}:{x}:{y},hwdownload,format=bgra"
        else:
            # Fallback path — same limitations as the original tool (issue
            # C2 applies): a desktop GDI surface, not the composited output.
            cmd += ["-f", "gdigrab", "-framerate", str(cfg.fps),
                    "-offset_x", str(x), "-offset_y", str(y),
                    "-video_size", f"{w}x{h}", *wallclock_ts, "-i", "desktop"]
            vf = None

        if cfg.audio_enabled:
            cmd += ["-f", "wasapi", "-i", "default"]

        scale_pad = (f"scale='min({cfg.width},iw)':'min({cfg.height},ih)':"
                     f"force_original_aspect_ratio=decrease,"
                     f"pad={cfg.width}:{cfg.height}:(ow-iw)/2:(oh-ih)/2:black")
        vf = f"{vf},{scale_pad}" if vf else scale_pad
        cmd += ["-vf", vf, "-fps_mode", "cfr"]

        if encoder == "h264_nvenc":
            cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                    "-cq", str(cfg.crf)]
        elif encoder == "h264_qsv":
            cmd += ["-c:v", "h264_qsv", "-global_quality", str(cfg.crf)]
        elif encoder == "h264_amf":
            cmd += ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp",
                    "-qp_i", str(cfg.crf), "-qp_p", str(cfg.crf)]
        else:
            cmd += ["-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf)]

        cmd += ["-pix_fmt", "yuv420p"]
        if cfg.audio_enabled:
            cmd += ["-c:a", "aac", "-b:a", "160k"]
        # C3: fragmented MP4 so a forced kill leaves a structurally valid
        # file (each flushed fragment has its own moof/mdat) instead of a
        # single trailing moov that a kill can lose entirely.
        cmd += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof"]
        cmd += [str(output_path)]
        return cmd

    def start(self, capture_rect: tuple[int, int, int, int], output_path: Path) -> float:
        """
        Start recording. Returns the monotonic timestamp (time.perf_counter())
        at which the ffmpeg process was launched.

        NOTE: per issue A2, this is NOT the anchor used for input timestamps
        any more — SessionEngine re-anchors to the first encoded frame's PTS
        during finalize (see app/core/finalize/anchor.py). This return value
        is kept only for logging/diagnostics.

        Raises FileNotFoundError if ffmpeg.exe doesn't exist.
        """
        ffmpeg_path = ffmpeg_exe()
        if not ffmpeg_path.exists():
            raise FileNotFoundError(
                f"ffmpeg not found at {ffmpeg_path}. Run setup again to download it.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_path = output_path
        cmd = self._build_command(capture_rect, output_path)
        log.info("Starting ffmpeg: %s", " ".join(cmd))

        creationflags = 0
        if sys.platform == "win32":
            creationflags = CREATE_NO_WINDOW | self.config.process_priority
        from app.core.finalize.anchor import capture_launch_pairing
        self.process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, creationflags=creationflags)
        # A2: taken immediately after Popen returns — as close to "ffmpeg is
        # now actually running" as we can get from this side of the pipe.
        self.launch_pairing = capture_launch_pairing()
        self._stderr_monitor = _StderrMonitor(self.process.stderr)
        self._stderr_monitor.start()
        self._started_at_monotonic = time.perf_counter()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                tail = "\n".join(self._stderr_monitor.tail())
                raise RuntimeError(f"ffmpeg exited immediately (code "
                                   f"{self.process.returncode}):\n{tail}")
            if output_path.exists() and output_path.stat().st_size > 0:
                break
            time.sleep(0.05)
        return self._started_at_monotonic

    def _finalize_timeout_s(self) -> float:
        """C3: scale with recording length instead of a flat 5s."""
        if self._started_at_monotonic is None:
            return MIN_FINALIZE_TIMEOUT_S
        elapsed = time.perf_counter() - self._started_at_monotonic
        return max(MIN_FINALIZE_TIMEOUT_S, min(MAX_FINALIZE_TIMEOUT_S, elapsed * 0.05))

    def _attempt_remux_repair(self, path: Path) -> bool:
        """C3: best-effort repair after a forced kill. Because the output is
        fragmented MP4 with an empty initial moov, ffmpeg can usually still
        read every fragment that was fully flushed before the kill even
        without a final moov — this remuxes into a normal, seekable MP4 and
        reports success/failure so the caller can flag the session."""
        repaired = path.with_name(path.stem + ".repaired.mp4")
        cmd = [str(ffmpeg_exe()), "-y", "-v", "error", "-i", str(path),
               "-c", "copy", "-movflags", "+faststart", str(repaired)]
        creationflags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            subprocess.run(cmd, timeout=60, check=True, creationflags=creationflags,
                            capture_output=True)
        except (subprocess.SubprocessError, OSError):
            return False
        if repaired.exists() and repaired.stat().st_size > 0:
            repaired.replace(path)
            return True
        return False

    def stop(self, timeout: float | None = None) -> Path:
        """
        Stop recording cleanly. ffmpeg writes the final fragment, exits.

        Returns the output path. If shutdown exceeds `timeout` (C3: scaled
        by recording length when not given explicitly), ffmpeg is killed and
        a remux repair is attempted; `self.health.finalize_forced_kill` /
        `remux_repair_succeeded` record what happened so the self-check
        (app/core/health.py) can flag a suspect session instead of shipping
        it silently.
        """
        if not self.process:
            raise RuntimeError("recorder was never started")
        if not self._output_path:
            raise RuntimeError("recorder has no output path")

        timeout = timeout if timeout is not None else self._finalize_timeout_s()
        try:
            if self.process.stdin:
                self.process.stdin.write(b"q")
                self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg did not exit within %.1fs — killing", timeout)
            self.process.kill()
            self.process.wait(timeout=10)
            self.health.finalize_forced_kill = True
            self.health.remux_repair_attempted = True
            self.health.remux_repair_succeeded = self._attempt_remux_repair(self._output_path)
            if not self.health.remux_repair_succeeded:
                log.error("remux repair failed for %s — session is suspect",
                          self._output_path)

        if self._stderr_monitor is not None:
            self.health.frames_encoded = self._stderr_monitor.frames_encoded
            self.health.frames_dropped = self._stderr_monitor.frames_dropped

        return self._output_path

    async def start_async(self, capture_rect: tuple[int, int, int, int],
                           output_path: Path) -> float:
        return await asyncio.to_thread(self.start, capture_rect, output_path)

    async def stop_async(self, timeout: float | None = None) -> Path:
        return await asyncio.to_thread(self.stop, timeout)
