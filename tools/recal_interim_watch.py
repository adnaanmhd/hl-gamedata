#!/usr/bin/env python3
"""Interim session-independent watcher (2026-08-17): lives ON THE VM under
systemd, so it survives every Mac session close. While hl-recal-rebuild
runs: one Telegram digest every 3 h. When the unit ends: one final
Telegram message with the closing state counts, then exit. The continuous
driver's built-in digest supersedes this at the flip — the flip session
should `systemctl stop hl-recal-watch` then."""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "hl-gamedata"))
from pipeline import config as C          # noqa: E402
from pipeline import telegram             # noqa: E402
from pipeline.ledger import Ledger        # noqa: E402

cfg = C.load()


def counts() -> str:
    led = Ledger(cfg.ledger_path)
    try:
        c = led.counts_by_state()
    finally:
        led.close()
    return " ".join(f"{k}:{v}" for k, v in sorted(c.items()))


def rebuild_active() -> bool:
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", "hl-recal-rebuild"]
    ).returncode == 0


def send(msg: str) -> None:
    try:
        telegram.send_message(cfg, msg)
    except telegram.TelegramError as e:
        print(f"[watch] telegram failed: {e}", file=sys.stderr)


send(f"👁 interim watcher live on the VM (survives Mac session closes): "
     f"3h digests until the rebuild ends. Now: {counts()}")
last = time.time()
inactive_streak = 0
# one is-active sample also reads false during 'activating'/'deactivating'
# or a restart backoff window — a single sample sent a false REBUILD ENDED
# and exited permanently (r-loop 1). Require 3 consecutive inactive reads
# ~30 s apart before declaring the run over.
while inactive_streak < 3:
    if rebuild_active():
        inactive_streak = 0
        time.sleep(300)
        if time.time() - last >= 3 * 3600:
            last = time.time()
            send(f"⏱ rebuild digest: {counts()}")
    else:
        inactive_streak += 1
        if inactive_streak < 3:
            time.sleep(30)
send(f"🏁 REBUILD RUN ENDED — final states: {counts()} — the continuous-"
     f"pipeline session owns the flip + endgame from here")
