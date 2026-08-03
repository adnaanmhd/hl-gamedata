"""Find a running process by exe name; await its exit without blocking the loop."""
from __future__ import annotations

import asyncio
import logging
import time

import psutil

log = logging.getLogger(__name__)

# Processes that are never the game under test, even though they're
# long-running .exe's a naive process scan would otherwise surface.
#
# Restored verbatim from the shipped exe (recovered via pycdas bytecode
# disassembly — list_likely_games's own bytecode failed to decompile, same
# as the UI files, so this whole function was previously a from-scratch
# guess). Real bug this caused: the guessed version's exclusion list was a
# fraction of this size, and — more importantly — it deduplicated results
# down to one entry per exe NAME, which the real implementation never does.
# A real game (Outer Wilds) went missing from the dropdown as a result; the
# exact mechanism didn't fully reproduce from reading the smaller version's
# logic alone, so rather than keep guessing, this restores the exact real
# list + logic instead, which is proven correct (it's what actually shipped).
_SYSTEM_NOISE = frozenset({
    "py.exe", "cmd.exe", "dwm.exe", "gog.exe", "upc.exe", "code.exe",
    "smss.exe", "zoom.exe", "agent.exe", "brave.exe", "csrss.exe",
    "eaapp.exe", "lsass.exe", "obs64.exe", "opera.exe", "slack.exe",
    "steam.exe", "teams.exe", "chrome.exe", "ctfmon.exe", "ffmpeg.exe",
    "msedge.exe", "nissrv.exe", "python.exe", "sihost.exe", "audiodg.exe",
    "conhost.exe", "discord.exe", "dllhost.exe", "ffprobe.exe",
    "firefox.exe", "lockapp.exe", "msmpeng.exe", "notepad.exe",
    "pythonw.exe", "spoolsv.exe", "spotify.exe", "svchost.exe",
    "wininit.exe", "explorer.exe", "services.exe", "winlogon.exe",
    "wmiprvse.exe", "battlenet.exe", "eadesktop.exe", "goggalaxy.exe",
    "notepad++.exe", "searchapp.exe", "taskhostw.exe", "battle.net.exe",
    "powershell.exe", "searchhost.exe", "fontdrvhost.exe",
    "nvcontainer.exe", "smartscreen.exe", "galaxyclient.exe",
    "humyncapture.exe", "nvidia share.exe", "riotclientux.exe",
    "steamservice.exe", "epicwebhelper.exe", "razer central.exe",
    "runtimebroker.exe", "textinputhost.exe", "blizzardupdate.exe",
    "memory compression", "msedgewebview2.exe", "nvidia overlay.exe",
    "steamwebhelper.exe", "systemsettings.exe", "ubisoftconnect.exe",
    "useroobebroker.exe", "logioptionsplus.exe", "nvidiawebhelper.exe",
    "logitech options.exe", "rockstarservices.exe", "rtkauduservice64.exe",
    "epicgameslauncher.exe", "riotclientservices.exe",
    "eabackgroundservice.exe", "shellexperiencehost.exe",
    "applicationframehost.exe", "nvbroadcast.container.exe",
    "razer synapse service.exe", "securityhealthservice.exe",
    "securityhealthsystray.exe", "startmenuexperiencehost.exe",
    "system", "registry",
})

# Path fragments that mark an exe as "probably a game, not a launcher/
# overlay" — used only to rank likely-game entries first, never to exclude.
_GAMEY_PATH_HINTS = (
    "steamapps", "epic games", "gog galaxy", "ubisoft", "ea games",
    "battle.net", "riot games", "rockstar games", "program files\\games",
    "program files (x86)\\games",
)


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
        except Exception:
            # Same reasoning as list_likely_games: don't let one unreadable
            # process abort the whole scan and report "not running" for a
            # game that's actually there.
            log.exception("find_pid_by_exe: skipping unreadable process")
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda p: p["create_time"], reverse=True)
    return candidates[0]["pid"]


def list_likely_games(min_uptime_sec: float = 5.0) -> list[dict]:
    """Heuristic list of running .exe processes that might be the game.
    Used by the GUI to populate a 'Pick the game' dropdown so the user
    doesn't have to type the exe name.

    Restored verbatim from the shipped exe (see _SYSTEM_NOISE comment) —
    deliberately does NOT deduplicate by name; every matching process is
    returned. `is_gamey` (exe path under steamapps/epic games/etc.) only
    affects sort order (gamey processes first, then by ascending uptime),
    never exclusion.
    """
    out: list[dict] = []
    now = time.time()
    skip = _SYSTEM_NOISE
    for proc in psutil.process_iter(["pid", "name", "create_time", "exe"]):
        try:
            name = proc.info["name"] or ""
            lower = name.lower()
            if not lower.endswith(".exe") or lower in skip:
                continue
            uptime = now - proc.info["create_time"]
            if uptime < min_uptime_sec:
                continue
            exe_path = (proc.info.get("exe") or "").lower()
            is_gamey = any(s in exe_path for s in _GAMEY_PATH_HINTS)
            out.append({
                "pid": proc.info["pid"], "name": name, "uptime_sec": uptime,
                "exe": proc.info.get("exe"), "is_gamey": is_gamey,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Expected and common (a process exited mid-scan, or we don't
            # have rights to read it, e.g. an anti-cheat-protected process).
            continue
        except Exception:
            # Real bug this guards against: ANY other exception here used
            # to propagate out of this whole function — one problematic
            # process would abort the scan and silently return an EMPTY
            # list, making it look like nothing was running rather than
            # "one process couldn't be read." Log and skip it instead.
            log.exception("list_likely_games: skipping unreadable process")
    out.sort(key=lambda p: (not p["is_gamey"], p["uptime_sec"]))
    return out


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
