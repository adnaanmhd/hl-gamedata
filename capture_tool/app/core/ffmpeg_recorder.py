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
module. `ddagrab` crops to the game's client rect natively via its own
`video_size`/`offset_x`/`offset_y` params (real bug fixed here:
`_probe_ddagrab_support` used to check `-devices`, but `ddagrab` is a lavfi
FILTER, listed under `-filters` — this meant ddagrab was never actually
detected/used on ANY machine, always silently falling back to gdigrab even
when the bundled ffmpeg build genuinely supported it; the invocation itself
was also using device-style syntax, `-f ddagrab -i 0`, instead of the real
`-f lavfi -i "ddagrab=output_idx=0:framerate=...:video_size=...:offset_x=
...:offset_y=..."`).

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
# ffmpeg's own -stats progress line, e.g. "frame= 1234 fps= 30 q=20.0
# size=  2048kB time=00:00:41.13 bitrate= 407.0kbits/s speed=1.00x" — the
# real A2 fix (see _StderrMonitor docstring) reads `time=` here.
_TIME_RE = re.compile(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


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
    # Partial-fix #4 (frame drops) diagnosability: which path actually ran.
    hw_pipeline: str = "cpu_roundtrip"  # "qsv_zerocopy" | "cpu_roundtrip"
    frames_dropped: int = 0
    frames_encoded: int = 0
    finalize_forced_kill: bool = False
    remux_repair_attempted: bool = False
    remux_repair_succeeded: bool = False
    # The real A2 anchor data — see _StderrMonitor's docstring for why this
    # replaced the ffprobe/wallclock-PTS approach.
    first_progress_monotonic_s: float | None = None
    first_progress_encoded_s: float | None = None
    # Partial-fix #3 follow-up: every (monotonic_s, encoded_s) sample seen
    # during the recording, not just the first — lets anchor.py fit a drift
    # slope across the whole session instead of a single fixed offset.
    progress_samples: list[tuple[float, float]] = field(default_factory=list)


def _run_ffmpeg_query(args: list[str], timeout: float = 5.0) -> str:
    creationflags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run([str(ffmpeg_exe()), *args], capture_output=True,
                              text=True, timeout=timeout, creationflags=creationflags)
        return (out.stdout or "") + (out.stderr or "")
    except (subprocess.SubprocessError, OSError):
        return ""


def _encoder_opens(encoder: str) -> bool:
    """Actually try to open `encoder`, not just check that ffmpeg lists its
    name. A hardware encoder can be compiled into ffmpeg (and so appear in
    `-encoders`) while still failing to *open* — e.g. an NVENC build that
    requires a newer driver API than the installed GPU driver provides
    ("Driver does not support the required nvenc API version"). That failure
    only surfaces when the encoder is actually initialized, so we run a
    trivial synthetic encode (a handful of tiny lavfi-generated frames into
    `-f null -`, nothing touches disk or the real capture) and check the
    real exit code."""
    cmd = ["-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "color=size=64x64:rate=5:duration=0.2",
           "-pix_fmt", "yuv420p", "-c:v", encoder, "-f", "null", "-"]
    creationflags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run([str(ffmpeg_exe()), *cmd], capture_output=True,
                                 timeout=10, creationflags=creationflags)
    except (subprocess.SubprocessError, OSError):
        return False
    if result.returncode != 0:
        log.warning("encoder preflight failed for %s: %s", encoder,
                    (result.stderr or b"").decode("utf-8", errors="replace").strip()[-500:])
        return False
    return True


def _detect_hw_encoder() -> str:
    """A1: prefer a hardware encoder; fall back to libx264 if none of the
    bundled ffmpeg's *listed* encoders actually *open* on this machine.
    Order reflects typical availability, not quality — any HW encoder beats
    software x264 for our CPU-contention problem, but a HW encoder that
    fails to open is worse than not trying it: it kills the whole session
    (see `_encoder_opens` docstring), so we verify before committing."""
    listing = _run_ffmpeg_query(["-hide_banner", "-encoders"])
    for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if enc in listing and _encoder_opens(enc):
            return enc
    return "libx264"


def _ddagrab_opens() -> bool:
    """Actually try to open ddagrab's output_idx=0, not just check the
    filter is listed — same lesson as `_encoder_opens` (A1): "this ffmpeg
    build has the filter compiled in" and "this specific GPU/monitor
    configuration can actually open it" are different things. Confirmed on
    a real machine: the filter was correctly detected AND correctly
    invoked, and ffmpeg still exited with "Selected output not supported" /
    "Failed to configure output pad" — DXGI output indices don't always
    map cleanly to a usable output, especially on hybrid-GPU laptops or
    certain multi-monitor arrangements. Runs a trivial synthetic capture
    (tiny size, 3 frames, `-f null -`) so a real failure here falls back to
    gdigrab instead of crashing the whole recording session."""
    cmd = ["-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "ddagrab=output_idx=0:framerate=5:video_size=64x64",
           "-vf", "hwdownload,format=bgra",
           "-frames:v", "3", "-f", "null", "-"]
    creationflags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run([str(ffmpeg_exe()), *cmd], capture_output=True,
                                 timeout=10, creationflags=creationflags)
    except (subprocess.SubprocessError, OSError):
        return False
    if result.returncode != 0:
        log.warning("ddagrab preflight failed: %s",
                    (result.stderr or b"").decode("utf-8", errors="replace").strip()[-500:])
        return False
    return True


def _qsv_zerocopy_opens() -> bool:
    """Partial-fix #4 (frame drops, ~1.5% on a real ddagrab+h264_qsv
    session): real hypothesis, UNVERIFIED on real hardware (no Windows/Intel
    GPU available here — see capture_tool/README.md's disclaimer).

    The current pipeline for ddagrab+h264_qsv is: ddagrab produces D3D11
    hardware frames -> `hwdownload` pulls every one back to CPU memory ->
    CPU-bound `scale`/`pad` filters run on it -> `h264_qsv` (a HARDWARE
    encoder) re-uploads it to GPU memory internally to actually encode. That
    GPU->CPU->GPU round trip runs on every single frame and is a well-known
    cause of exactly this kind of encoder-backpressure frame drop under game
    load — the CPU-side hwdownload+scale/pad work is competing with the game
    for the same CPU the A1 fix was already trying to protect ffmpeg from.

    ffmpeg supports deriving a QSV hardware-frames context directly from
    ddagrab's D3D11 frames (`hwmap=derive_device=qsv,format=qsv`) so frames
    never leave GPU memory before h264_qsv encodes them. This preflights
    that exact chain with a tiny synthetic capture, same pattern as
    `_ddagrab_opens`/`_encoder_opens` — "the filter graph is theoretically
    valid" and "this exact ffmpeg build + Intel driver combination actually
    opens it" are different questions, and trusting the former without
    checking has already caused two real regressions in this file (A1, C2).
    Falls back to the existing hwdownload+CPU-scale path on ANY failure.
    """
    cmd = ["-hide_banner", "-loglevel", "error",
           "-init_hw_device", "qsv=hw", "-filter_hw_device", "hw",
           "-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=5:video_size=64x64",
           "-vf", "hwmap=derive_device=qsv,format=qsv",
           "-c:v", "h264_qsv", "-frames:v", "3", "-f", "null", "-"]
    creationflags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run([str(ffmpeg_exe()), *cmd], capture_output=True,
                                 timeout=10, creationflags=creationflags)
    except (subprocess.SubprocessError, OSError):
        return False
    if result.returncode != 0:
        log.warning("qsv zero-copy preflight failed: %s",
                    (result.stderr or b"").decode("utf-8", errors="replace").strip()[-500:])
        return False
    return True


def _probe_ddagrab_support() -> bool:
    """Real bug found here: `ddagrab` is an avfilter SOURCE (invoked as
    `-f lavfi -i "ddagrab=..."`), not an avdevice — ffmpeg lists it under
    `-filters`, never under `-devices`. Checking `-devices` (the original
    code here) meant this returned False on EVERY ffmpeg build, including
    ones that genuinely support ddagrab, silently forcing the `gdigrab`
    fallback — and its C2 exclusive-fullscreen black-capture limitation —
    on every machine, not just ones that actually lack it.

    Listing the filter alone isn't sufficient either (see `_ddagrab_opens`
    docstring) — a real preflight open-test is required, same as A1's
    hardware-encoder check."""
    listing = _run_ffmpeg_query(["-hide_banner", "-filters"])
    return "ddagrab" in listing and _ddagrab_opens()


class _StderrMonitor:
    """Tails ffmpeg's stderr on a background thread, extracting the running
    `frame=`/`drop=` counters ffmpeg itself reports (A1/D2) — this is the
    authoritative drop count, not a PTS-gap inference after the fact.

    Also captures the real A2 anchor data: the first `time=` progress value
    ffmpeg reports, paired with the monotonic instant WE read that line.
    This replaces the original approach of reading the first frame's PTS
    back out of the muxed file via ffprobe and treating it as wallclock
    time — confirmed broken on a real delivery: ffmpeg's `-avoid_negative_ts`
    normalizes the file's PTS back to relative/near-zero regardless of
    `-use_wallclock_as_timestamps`, so that number was never actually
    wallclock-scale after muxing (see anchor.py's implausibility guard,
    which caught exactly this).

    This approach never depends on what survives muxing: `time=T` at real
    monotonic instant `M` means frame/time zero occurred at `M - T`,
    algebraically, for any progress line — no container round-trip, no
    wallclock assumption, nothing to normalize away.
    """

    def __init__(self, stream) -> None:
        self._stream = stream
        self.frames_encoded = 0
        self.frames_dropped = 0
        self.first_progress_monotonic_s: float | None = None
        self.first_progress_encoded_s: float | None = None
        # Real hypothesis under investigation (partial-fix #3/#4 follow-up):
        # `-fps_mode cfr` forces a constant OUTPUT frame rate by duplicating
        # or dropping frames when the real capture can't sustain exactly
        # `cfg.fps` — that's the same mechanism producing `frames_dropped`.
        # Every duplicate/drop nudges "video content time" further away from
        # real wallclock time, so the single-sample anchor (`first_progress_*`,
        # which only ever measures a single (monotonic, encoded) point near
        # the START of the recording) can correct the startup offset but
        # CANNOT correct drift that accumulates over the rest of the
        # recording — a fixed correction is, by definition, unable to track
        # a value that changes over time. Recording EVERY progress sample
        # (not just the first) lets anchor.py fit a line through
        # (monotonic_s, encoded_s) across the whole session: if ffmpeg's
        # `time=` truly advanced in lockstep with real wallclock time, that
        # line's slope would be 1.0; a slope that measurably differs from
        # 1.0 is direct, physical evidence of exactly this drift, and lets
        # the correction scale with elapsed time instead of being a single
        # constant — see anchor.py's `fit_progress_drift`.
        self.progress_samples: list[tuple[float, float]] = []
        self._lines: list[str] = []
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        # Real precision bug avoided here: ffmpeg's own "-stats" progress
        # line is terminated with \r (it overwrites itself in a terminal),
        # not \n. `iter(stream.readline, b"")` only splits on \n, so it
        # could buffer several \r-separated stats updates together and only
        # capture time.perf_counter() once a real \n finally arrives —
        # silently delaying the exact timestamp the A2 fix's precision
        # depends on. Read raw bytes and split on EITHER \r or \n instead,
        # so each stats update is timestamped the instant it actually
        # arrives.
        buf = b""
        while True:
            chunk = self._stream.read(1)
            if not chunk:
                break
            if chunk in (b"\r", b"\n"):
                if buf:
                    self._handle_line(buf, time.perf_counter())
                    buf = b""
            else:
                buf += chunk
        if buf:
            self._handle_line(buf, time.perf_counter())

    def _handle_line(self, raw: bytes, read_at: float) -> None:
        try:
            line = raw.decode("utf-8", errors="replace")
        except Exception:
            return
        self._lines.append(line)
        m = _FRAME_RE.search(line)
        if m:
            self.frames_encoded = int(m.group(1))
        m = _DROP_RE.search(line)
        if m:
            self.frames_dropped = int(m.group(1))
        m = _TIME_RE.search(line)
        if m:
            h, mi, s = m.groups()
            encoded_s = int(h) * 3600 + int(mi) * 60 + float(s)
            self.progress_samples.append((read_at, encoded_s))
            if self.first_progress_monotonic_s is None:
                self.first_progress_encoded_s = encoded_s
                self.first_progress_monotonic_s = read_at

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

        # Partial-fix #4: only take the zero-copy path when there's no
        # scale/pad work to do anyway (capture rect already matches the
        # target dims — the common case for a native-resolution capture).
        # A mismatched aspect ratio still needs the CPU-side scale/pad
        # filters, so it falls back to the existing hwdownload path rather
        # than trying to reimplement pad-with-black-bars in scale_qsv.
        use_qsv_zerocopy = (
            use_ddagrab and encoder == "h264_qsv"
            and w == cfg.width and h == cfg.height
            and _qsv_zerocopy_opens())
        self.health.hw_pipeline = "qsv_zerocopy" if use_qsv_zerocopy else "cpu_roundtrip"

        cmd = [str(ffmpeg_exe()), "-y", "-loglevel", "warning", "-stats"]
        if use_qsv_zerocopy:
            cmd += ["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"]

        # A2: stamp frame PTS from the wallclock rather than a pipeline-
        # relative clock, so finalize's anchor step (app/core/finalize/
        # anchor.py) can read the first frame's *wallclock* instant back out
        # with ffprobe and re-anchor input timestamps to it.
        wallclock_ts = ["-use_wallclock_as_timestamps", "1"]
        if use_ddagrab:
            # Real bug found here too: ddagrab was invoked as a device
            # (`-f ddagrab -i 0`), which isn't how it works at all — it's a
            # lavfi filter, invoked as `-f lavfi -i "ddagrab=..."`, with
            # output_idx/framerate/video_size/offset_x/offset_y all as
            # named params INSIDE that one filter string (no separate crop
            # filter needed — ddagrab crops natively). It exclusively
            # returns D3D11 hardware frames, so hwdownload+format is still
            # required before any software filter (scale/pad below).
            ddagrab = (f"ddagrab=output_idx=0:framerate={cfg.fps}:"
                       f"video_size={w}x{h}:offset_x={x}:offset_y={y}")
            cmd += ["-f", "lavfi", *wallclock_ts, "-i", ddagrab]
            # Partial-fix #4: keep frames in GPU memory end-to-end when
            # possible (no hwdownload/CPU-scale round trip) — see
            # _qsv_zerocopy_opens's docstring. Dims already match cfg.width/
            # height here (checked above), so no scale/pad filter is needed
            # either way — the two paths differ only in whether the frame
            # ever touches CPU memory before h264_qsv encodes it.
            vf = "hwmap=derive_device=qsv,format=qsv" if use_qsv_zerocopy else "hwdownload,format=bgra"
        else:
            # Fallback path — same limitations as the original tool (issue
            # C2 applies): a desktop GDI surface, not the composited output.
            cmd += ["-f", "gdigrab", "-framerate", str(cfg.fps),
                    "-offset_x", str(x), "-offset_y", str(y),
                    "-video_size", f"{w}x{h}", *wallclock_ts, "-i", "desktop"]
            vf = None

        if cfg.audio_enabled:
            cmd += ["-f", "wasapi", "-i", "default"]

        if use_qsv_zerocopy:
            # Dims already match cfg.width/height (checked above) — skip
            # scale/pad entirely. Appending it here would force the QSV
            # hardware frame back through a software filter anyway,
            # defeating the whole point of the zero-copy path.
            pass
        else:
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

        if not use_qsv_zerocopy:
            # `-pix_fmt yuv420p` names a SOFTWARE pixel format. On the
            # zero-copy path the frame is still a QSV hardware surface at
            # this point (nv12 internally) — forcing yuv420p here would
            # make ffmpeg convert it back to a software frame right before
            # h264_qsv encodes it, silently re-introducing the exact
            # GPU->CPU round trip this whole path exists to avoid. The
            # hardware encoder picks its own correct internal format
            # without this flag.
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
            self.health.first_progress_monotonic_s = self._stderr_monitor.first_progress_monotonic_s
            self.health.first_progress_encoded_s = self._stderr_monitor.first_progress_encoded_s
            self.health.progress_samples = list(self._stderr_monitor.progress_samples)

        return self._output_path

    async def start_async(self, capture_rect: tuple[int, int, int, int],
                           output_path: Path) -> float:
        return await asyncio.to_thread(self.start, capture_rect, output_path)

    async def stop_async(self, timeout: float | None = None) -> Path:
        return await asyncio.to_thread(self.stop, timeout)
