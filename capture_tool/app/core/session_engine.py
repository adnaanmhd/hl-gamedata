"""
Session engine — orchestrates one recording session.

Pipeline:
    setup -> locate game window -> start ffmpeg -> start input/raw_mouse
    capture -> start focus tracker -> wait for game exit or user stop ->
    stop everything -> merge inputs -> finalize (native v2 write + self-check)

Output: sessions/<session_id>/  (native spec-v2 delivery — see
app/core/finalize/pipeline.py)

The engine is GUI-framework-agnostic — it accepts a plain status callback.

--- Fixes applied here ---
A2  : re-anchor timestamps to the first video frame's real PTS instead of
      process-launch time (app.core.finalize.anchor), applied during
      finalize before any binning happens.
B1  : check AsyncRawMouseCapture.last_error right after start() (was never
      read); _merge_inputs falls back to ts_monotonic_ns instead of
      dropping a raw-mouse record whose ts_offset_ns is None.
B2/E1/E2: SubsystemMonitor polls every capture subsystem's last_error
      during recording (not just at the very end); run_self_check gates
      `ready_for_upload` on modality presence, subsystem health, frame
      drops, sync, and known-game resolution.
D1  : game.name is resolved through app.core.games.resolve_game — never
      free text — and both the canonical slug and display title are stored.
D2  : metadata gains fps_actual/frames_dropped/modalities_present/
      capture_health (A2's anchor diagnostics) instead of a bare nominal fps.
"""
from __future__ import annotations

import asyncio
import json
import logging
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.core.contributor import mask_email
from app.core.ffmpeg_recorder import FFmpegRecorder, RecorderConfig
from app.core.ffprobe import probe_video
from app.core.finalize.anchor import compute_anchor_correction
from app.core.finalize.pipeline import run_finalize
from app.core.focus_tracker import FocusTracker
from app.core.games import resolve_game, KNOWN_GAMES
from app.core.health import SubsystemMonitor
from app.core.keyboard_capture import InputCapture
from app.core.paths import SESSIONS_DIR, ensure_dirs
from app.core.process_watcher import wait_for_exit
from app.core.raw_mouse import AsyncRawMouseCapture

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2  # matches the native v2 delivery this engine now writes
HUMYN_VERSION = "0.2.0"

StatusFn = Callable[[str, str, "int | None"], None]


def _noop_status(stage: str = "", detail: str = "", progress: int | None = None) -> None:
    pass


@dataclass
class SessionMetadata:
    """What the UI collects before recording starts."""
    contributor_email: str
    skill_level: str
    role: str
    objective_task: str
    game_pid: int
    game_exe_name: str
    game_display_pick: str | None = None  # UI dropdown pick — D1: display only


@dataclass
class SessionResult:
    session_id: str
    out_dir: Path
    ready_for_upload: bool
    qa_status: str
    self_check_failures: list[str] = field(default_factory=list)
    self_check_warnings: list[str] = field(default_factory=list)


def _slugify(text: str) -> str:
    """Make a filesystem-safe short slug. Used in session_id."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return cleaned[:30] or "game"


def _timestamp_for_id() -> str:
    """ISO-ish UTC timestamp safe for filenames: 2026-05-07T18-32-15Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _primary_display_refresh_hz() -> int | None:
    """Read the primary monitor's refresh rate via EnumDisplaySettingsW with
    ENUM_CURRENT_SETTINGS. Returns Hz (e.g. 60, 144, 240) or None on failure."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
            ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
            ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
            ("dmOrientation", ctypes.c_short), ("dmPaperSize", ctypes.c_short),
            ("dmPaperLength", ctypes.c_short), ("dmPaperWidth", ctypes.c_short),
            ("dmScale", ctypes.c_short), ("dmCopies", ctypes.c_short),
            ("dmDefaultSource", ctypes.c_short), ("dmPrintQuality", ctypes.c_short),
            ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
            ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
            ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
            ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
            ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
        ]

    try:
        user32 = ctypes.windll.user32
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(DEVMODEW)
        ENUM_CURRENT_SETTINGS = -1
        if user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
            return int(dm.dmDisplayFrequency)
    except Exception:
        log.exception("could not read display refresh rate")
    return None


def _system_info() -> dict[str, Any]:
    """Collect OS / display info for metadata.system. Best-effort."""
    info: dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()}",
        "humyncapture_version": HUMYN_VERSION,
    }
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            info["screen_width"] = int(user32.GetSystemMetrics(0))
            info["screen_height"] = int(user32.GetSystemMetrics(1))
            info["screen_refresh_hz"] = _primary_display_refresh_hz()
            info["keyboard_layout"] = hex(ctypes.windll.user32.GetKeyboardLayout(0))  # D2
        except Exception:
            log.exception("system info collection failed")
    return info


class SessionEngine:
    """One instance per session. Use `await engine.run(meta)`."""

    def __init__(self, status_fn: StatusFn | None = None,
                 recorder_config: RecorderConfig | None = None) -> None:
        self._status = status_fn or _noop_status
        self._cancelled = False
        self._recorder_config = recorder_config or RecorderConfig()

    def cancel(self) -> None:
        """Best-effort cancel. The current session wraps up cleanly."""
        self._cancelled = True

    async def run(self, meta: SessionMetadata) -> SessionResult:
        ensure_dirs()
        contributor_id = mask_email(meta.contributor_email)

        # D1: exe name is authoritative; the UI's dropdown pick is display-
        # only and itself constrained to KNOWN_GAMES, so it can never
        # reintroduce a free-text misspelling.
        game_slug, game_title = resolve_game(meta.game_display_pick, meta.game_exe_name)
        game_slug_is_known = game_slug in {g.slug for g in KNOWN_GAMES}

        session_id = f"{_timestamp_for_id()}_{_slugify(game_title)}_{contributor_id}"
        raw_dir = SESSIONS_DIR / f"{session_id}_raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        video_path = raw_dir / "video.mp4"

        self._status("locating_window", f"Waiting for {meta.game_exe_name}...", None)
        title, hwnd = await self._find_game_window(meta.game_pid, meta.game_exe_name)
        rect = self._get_window_screen_rect(hwnd)

        recorder = FFmpegRecorder(self._recorder_config)
        self._status("starting_recorder", "Starting capture...", None)
        old_anchor_monotonic = recorder.start(rect, video_path)
        # From this point, InputCapture/RawMouseCapture/FocusTracker all
        # timestamp relative to `old_anchor_monotonic` — A2's finalize-time
        # correction re-anchors everything to the real first frame later.

        input_capture = InputCapture(anchor_monotonic=old_anchor_monotonic)
        raw_mouse = AsyncRawMouseCapture(raw_dir / "raw_mouse.jsonl")
        focus_tracker = FocusTracker(meta.game_pid, old_anchor_monotonic, input_capture)

        monitor = SubsystemMonitor()
        monitor.register("keyboard_mouse_buttons", input_capture)
        monitor.register("raw_mouse_motion", raw_mouse)
        monitor.register("focus_tracker", focus_tracker)

        await input_capture.start()
        await raw_mouse.start()
        raw_mouse.set_t_zero(time.monotonic_ns())
        await focus_tracker.start()

        # B1 (E1 generally): check subsystem health right after start —
        # before this fix, RawMouseCapture.last_error was never read and a
        # dead sink produced a silent zero-motion session.
        startup_issues = monitor.any_fatal()
        for issue in startup_issues:
            log.error("subsystem '%s' failed to start: %s", issue.name, issue.error)
            self._status("subsystem_warning", f"{issue.name}: {issue.error}", None)

        self._status("recording", "Recording...", None)
        started_utc = datetime.now(timezone.utc)

        drain_task = asyncio.create_task(self._drain_queue(input_capture))
        cancel_task = asyncio.create_task(self._poll_cancel())
        watch_task = asyncio.create_task(wait_for_exit(meta.game_pid))
        health_task = asyncio.create_task(self._poll_health(monitor))

        done, pending = await asyncio.wait(
            {cancel_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        health_task.cancel()

        self._status("stopping", "Finishing up...", None)
        ended_utc = datetime.now(timezone.utc)

        await focus_tracker.stop()
        await input_capture.stop()
        await raw_mouse.stop()
        recorder.stop()

        drain_task.cancel()
        try:
            queue_events = await drain_task
        except asyncio.CancelledError:
            queue_events = drain_task.result() if drain_task.done() else []

        events_total, events_by_type = self._merge_inputs(
            queue_events, raw_dir / "raw_mouse.jsonl", raw_dir / "inputs.jsonl")

        warnings: list[str] = []
        video_info = self._probe_video(video_path, warnings)
        duration_sec = (ended_utc - started_utc).total_seconds()

        metadata = self._build_metadata(
            session_id=session_id, contributor_id=contributor_id, meta=meta,
            game_slug=game_slug, game_title=game_title,
            recording_started_utc=started_utc, recording_ended_utc=ended_utc,
            duration_sec=duration_sec, video_info=video_info,
            events_total=events_total, events_by_type=events_by_type,
            recorder_health=recorder.health,
        )
        (raw_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

        self._status("finalizing", "Writing delivery files...", None)
        anchor = compute_anchor_correction(
            launch_pairing=recorder.launch_pairing,
            old_anchor_monotonic_s=old_anchor_monotonic,
            video_path=video_path,
        )
        if anchor.method == "unavailable":
            warnings.append("A2 anchor correction unavailable (first-frame "
                             "PTS unreadable) — input/video sync not verified")

        final_issues = monitor.any_fatal()
        finalize_result = run_finalize(
            raw_session_dir=raw_dir,
            out_root=SESSIONS_DIR,
            anchor=anchor,
            game_slug=game_slug,
            game_slug_is_known=game_slug_is_known,
            subsystem_issues=final_issues,
            frames_dropped=recorder.health.frames_dropped,
            require_audio=self._recorder_config.audio_enabled,
            has_audio=bool(video_info.get("has_audio")) if video_info else False,
        )

        self._status(
            "done" if finalize_result.ready_for_upload else "needs_attention",
            f"qa={finalize_result.qa_status}", None)

        return SessionResult(
            session_id=session_id,
            out_dir=finalize_result.out_dir,
            ready_for_upload=finalize_result.ready_for_upload,
            qa_status=finalize_result.qa_status,
            self_check_failures=finalize_result.self_check_failures,
            self_check_warnings=finalize_result.self_check_warnings,
        )

    async def _poll_cancel(self) -> None:
        """Wait for cancel flag; used as a peer of game-exit watch."""
        while not self._cancelled:
            await asyncio.sleep(0.25)

    async def _poll_health(self, monitor: SubsystemMonitor) -> None:
        """B2/E1: periodic live health poll during recording (not just at
        start/end) so a subsystem that dies mid-session is caught promptly."""
        try:
            while True:
                await asyncio.sleep(2.0)
                for issue in monitor.poll():
                    log.error("subsystem '%s' failed mid-session: %s",
                              issue.name, issue.error)
                    self._status("subsystem_warning",
                                  f"{issue.name}: {issue.error}", None)
        except asyncio.CancelledError:
            pass

    async def _drain_queue(self, capture: InputCapture) -> list[dict]:
        events: list[dict] = []
        try:
            while True:
                events.append(await capture.queue.get())
        except asyncio.CancelledError:
            while not capture.queue.empty():
                events.append(capture.queue.get_nowait())
            return events

    async def _find_game_window(self, pid: int, exe_name: str) -> tuple[str, int]:
        """
        Return (title, hwnd) of a visible window owned by `pid` OR by any
        process matching `exe_name`. Polls for up to 5 seconds.
        """
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            found = self._get_window_for_pid(pid, exe_name)
            if found is not None:
                return found
            await asyncio.sleep(0.25)
        raise RuntimeError(f"could not find a window for pid={pid} exe={exe_name}")

    def _get_window_for_pid(self, pid: int, exe_name: str) -> tuple[str, int] | None:
        """
        Enumerate ALL visible top-level windows; return the title of the
        BIGGEST one whose owning process either:
          (a) matches `pid` exactly, OR
          (b) has an exe basename matching `exe_name` (case-insensitive)

        Strategy (b) is required because some games (Unreal, Unity, etc.)
        spawn a launcher -> rendering-child architecture where the PID we
        track and the PID owning the visible window aren't the same.

        We rank by window size because games can end up with multiple
        windows under the same exe (the real game window plus tiny overlay
        helper windows) — the real game window is always the biggest.
        """
        if sys.platform != "win32":
            return None
        import ctypes
        from ctypes import wintypes
        import psutil

        user32 = ctypes.windll.user32
        target_exe = exe_name.lower()
        candidates: list[tuple[int, str, int]] = []  # (area, title, hwnd)

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            owner_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            match = owner_pid.value == pid
            if not match:
                try:
                    proc = psutil.Process(owner_pid.value)
                    match = proc.name().lower() == target_exe
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    match = False
            if not match:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            area = max(rect.right - rect.left, 0) * max(rect.bottom - rect.top, 0)
            candidates.append((area, buf.value, hwnd))
            return True

        user32.EnumWindows(_callback, 0)
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0], reverse=True)
        _, title, hwnd = candidates[0]
        return title, hwnd

    def _get_window_screen_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        """
        Return (x, y, width, height) in screen coordinates for the window's
        client area. We use the client rect (not the full window rect) so we
        don't capture title bars or borders for windowed-mode games.
        Width/height are forced even because libx264/nvenc with yuv420p
        needs even dimensions.
        """
        if sys.platform != "win32":
            return (0, 0, 1920, 1080)
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        top_left = wintypes.POINT(rect.left, rect.top)
        user32.ClientToScreen(hwnd, ctypes.byref(top_left))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        w -= w % 2
        h -= h % 2
        return (top_left.x, top_left.y, w, h)

    def _merge_inputs(self, queue_events: list[dict], raw_mouse_path: Path,
                       out_path: Path) -> tuple[int, dict[str, int]]:
        """
        Merge:
          - queue_events: from InputCapture/FocusTracker, already in target schema
          - raw_mouse records: from raw_mouse.py, in OLD schema
            {ts_offset_ns, ts_monotonic_ns, dx, dy, buttons, wheel}

        Sort all events by `t` (microseconds) and write inputs.jsonl.
        Returns (total_count, by_type_dict).

        --- B1 fix (second half) ---
        The original merge DROPPED any raw-mouse record whose `ts_offset_ns`
        was None (e.g. it arrived before `set_t_zero` was called, or
        set_t_zero was never reached due to a startup race) instead of
        falling back to `ts_monotonic_ns` — so even partially-captured
        motion could be silently discarded. We now fall back to
        `ts_monotonic_ns - anchor_monotonic_ns`, matching the same clock
        `set_t_zero` was trying to establish, whenever ts_offset_ns is
        missing.
        """
        all_events: list[dict] = list(queue_events)
        by_type: dict[str, int] = {}

        if raw_mouse_path.exists():
            lines = raw_mouse_path.read_text().splitlines()
            first_ts_monotonic_ns: int | None = None
            for line in lines:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if first_ts_monotonic_ns is None:
                    first_ts_monotonic_ns = rec.get("ts_monotonic_ns")
                t_ns = rec.get("ts_offset_ns")
                if t_ns is None:
                    # B1 fallback: derive an offset from ts_monotonic_ns
                    # relative to the first record we saw, rather than
                    # dropping the sample outright.
                    mono_ns = rec.get("ts_monotonic_ns")
                    if mono_ns is None or first_ts_monotonic_ns is None:
                        continue
                    t_ns = mono_ns - first_ts_monotonic_ns
                all_events.append({
                    "t": int(t_ns // 1000),
                    "type": "mouse_raw",
                    "dx": rec.get("dx", 0),
                    "dy": rec.get("dy", 0),
                    "buttons": rec.get("buttons", []),
                    "wheel": rec.get("wheel"),
                })

        all_events.sort(key=lambda e: e.get("t", 0))
        for e in all_events:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for e in all_events:
                f.write(json.dumps(e) + "\n")

        return len(all_events), by_type

    def _probe_video(self, video_path: Path, warnings: list[str]) -> dict[str, Any]:
        """Best-effort: extract video metadata via ffprobe. Returns dict
        suitable for embedding in metadata.json."""
        result: dict[str, Any] = {
            "filename": "video.mp4", "codec": None, "width": None,
            "height": None, "fps_nominal": None, "fps_actual": None,
            "has_audio": False, "size_bytes": None,
        }
        if video_path.exists():
            result["size_bytes"] = video_path.stat().st_size
        try:
            probed = probe_video(video_path)
            if probed:
                result.update({
                    "codec": probed.get("codec"),
                    "width": probed.get("width"),
                    "height": probed.get("height"),
                    "fps_nominal": probed.get("fps_nominal"),
                    # A3: real achieved average, never the nominal 30.
                    "fps_actual": round(probed["fps_avg"], 3) if probed.get("fps_avg") else None,
                    "has_audio": bool(probed.get("has_audio")),
                })
        except Exception as e:
            log.exception("ffprobe failed")
            warnings.append(f"Could not probe video metadata: {e}")
        return result

    def _build_metadata(
        self, *, session_id: str, contributor_id: str, meta: SessionMetadata,
        game_slug: str, game_title: str, recording_started_utc: datetime,
        recording_ended_utc: datetime, duration_sec: float,
        video_info: dict[str, Any], events_total: int,
        events_by_type: dict[str, int], recorder_health: Any,
    ) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "player": {"contributor_id": contributor_id, "skill_level": meta.skill_level},
            "game": {
                # D1: both the canonical (never free-text) slug/title AND
                # what the exe/UI actually reported, so a resolver bug is
                # detectable even for old sessions.
                "name": game_title,
                "slug": game_slug,
                "exe_name": meta.game_exe_name,
                "pid_at_capture": meta.game_pid,
                "ui_pick_raw": meta.game_display_pick,
            },
            "session": {"role": meta.role, "objective_task": meta.objective_task},
            "recording": {
                "started_at_utc": recording_started_utc.isoformat().replace("+00:00", "Z"),
                "ended_at_utc": recording_ended_utc.isoformat().replace("+00:00", "Z"),
                "duration_seconds": round(duration_sec, 3),
            },
            "video": video_info,
            "input_capture": {
                "filename": "inputs.jsonl",
                "events_total": events_total,
                "events_by_type": events_by_type,
            },
            # D2: drop/quality telemetry that used to be entirely absent.
            "capture_health": {
                "backend": recorder_health.backend,
                "encoder": recorder_health.encoder,
                "frames_dropped": recorder_health.frames_dropped,
                "frames_encoded": recorder_health.frames_encoded,
                "finalize_forced_kill": recorder_health.finalize_forced_kill,
                "remux_repair_attempted": recorder_health.remux_repair_attempted,
                "remux_repair_succeeded": recorder_health.remux_repair_succeeded,
                # time_anchor/correction fields are merged in by
                # finalize.pipeline.run_finalize (anchor.to_capture_health_dict()).
            },
            "system": _system_info(),
        }
