"""Find a running process by exe name; await its exit without blocking the loop."""
from __future__ import annotations

import asyncio
import time

import psutil

# Processes that are never the game under test, even though they're
# long-running .exe's a naive process scan would otherwise surface.
_SYSTEM_NOISE = {
    "explorer.exe", "steam.exe", "steamwebhelper.exe", "discord.exe",
    "discordptt.exe", "epicgameslauncher.exe", "epicwebhelper.exe",
    "humyncapture.exe", "obs64.exe", "msedge.exe", "chrome.exe",
    "searchhost.exe", "textinputhost.exe", "shellexperiencehost.exe",
    "ctfmon.exe", "dwm.exe", "csrss.exe", "svchost.exe", "conhost.exe",
    "gamebar.exe", "gamebarftserver.exe", "cmd.exe", "python.exe",
    "pythonw.exe",
}


def find_pid_by_exe(exe_name: str) -> int | None:
    target = exe_name.lower()
    if not target.endswith(".exe"):
        target += ".exe"
    candidates = []
    for proc in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name == target:
                candidates.append(proc.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda p: p["create_time"], reverse=True)
    return candidates[0]["pid"]


def list_likely_games(min_uptime_sec: float = 5.0) -> list[str]:
    """Heuristic list of running .exe basenames that might be the game.

    Used by the GUI to populate a 'Pick the game' dropdown so the user
    doesn't have to type the exe name. We exclude well-known system /
    launcher / capture-tool-itself processes and require a minimum uptime
    so freshly-spawned helper processes (splash screens, updaters) don't
    flash through the list.
    """
    now = time.time()
    seen: dict[str, float] = {}
    for proc in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            name = proc.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not name.lower().endswith(".exe"):
            continue
        if name.lower() in _SYSTEM_NOISE:
            continue
        create_time = proc.info.get("create_time") or now
        if now - create_time < min_uptime_sec:
            continue
        # Keep the earliest-seen create_time per name (stable ordering if a
        # game relaunches a same-named child during the scan window).
        seen.setdefault(name, create_time)
    return sorted(seen, key=lambda n: seen[n])


async def wait_for_exit(pid: int, poll_interval: float = 1.0) -> None:
    """Await process exit without blocking the asyncio loop.

    psutil has no async API; pid_exists()/Process.is_running() are cheap
    syscalls so a plain poll loop (yielding via asyncio.sleep) is fine —
    this only needs sub-second-ish responsiveness, not tight timing.
    """
    while psutil.pid_exists(pid):
        try:
            proc = psutil.Process(pid)
            if proc.status() == psutil.STATUS_ZOMBIE:
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        await asyncio.sleep(poll_interval)
