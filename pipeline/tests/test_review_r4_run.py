"""Tests added by adversarial-review round 4 (run.py): recycled-pid lock
check + atomic rename-reclaim, scan-crash containment, orphan-reject
sweep triggers."""
import json
import os
import subprocess
import sys
from datetime import datetime

import pytest

from pipeline import config as C
from pipeline import reports, run as runmod
from pipeline.ledger import Ledger


@pytest.fixture(autouse=True)
def _arm_the_batch_driver(monkeypatch):
    """run() now declines when PIPELINE_CONTINUOUS is True (r-loop 5): the
    flag used to be a ONE-WAY interlock that stopped the continuous unit
    when False but never stopped the batch driver when True, so a
    roll-forward could leave both armed and let a batch tick take over
    production. These tests exercise the (dormant) batch driver itself, so
    they arm it explicitly."""
    monkeypatch.setattr(C, "PIPELINE_CONTINUOUS", False)



def _dead_pid() -> int:
    """A pid that was just alive and is now reaped — the realistic stale
    lock case. Falls back to an absurd-but-valid pid if the child's pid
    got recycled between reap and check (theoretically possible)."""
    p = subprocess.Popen([sys.executable, "-c", "pass"],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    p.wait()
    try:
        os.kill(p.pid, 0)
    except ProcessLookupError:
        return p.pid
    return 999999999


# ------------------------- recycled pid + rename-reclaim (review-r4 #16)

def test_pid_is_pipeline_own_and_dead_pid():
    """review-r4 #16: own (live) test process is recognized — on Linux via
    /proc cmdline ("pytest"), on macOS via the liveness-only fallback (no
    /proc -> OSError -> True). A reaped child is always False."""
    assert runmod._pid_is_pipeline(os.getpid()) is True
    pid = _dead_pid()
    if pid == 999999999:
        pytest.skip("child pid recycled immediately; dead-pid half moot")
    assert runmod._pid_is_pipeline(pid) is False       # ProcessLookupError


def test_stale_lock_rename_reclaim_leaves_no_grave(cfg):
    """review-r4 #16: reclaim goes rename-then-rmtree; the winner rewrites
    the pid file with its own pid and no run.lock.stale-* grave survives."""
    cfg.lock_dir.mkdir(parents=True)
    (cfg.lock_dir / "pid").write_text(str(_dead_pid()))
    assert runmod.acquire_lock(cfg) is True
    assert (cfg.lock_dir / "pid").read_text() == str(os.getpid())
    assert list(cfg.home.glob("run.lock.stale-*")) == []
    runmod.release_lock(cfg)


def test_rename_loser_yields_without_deleting(cfg, monkeypatch):
    """review-r4 #16: os.rename raising FileNotFoundError means someone
    else reclaimed — the loser must return False and touch NOTHING (the
    old rmtree-then-retry could delete a rival's fresh lock)."""
    cfg.lock_dir.mkdir(parents=True)
    dead = _dead_pid()
    (cfg.lock_dir / "pid").write_text(str(dead))
    real_rename = os.rename

    def lose_rename(src, dst, *a, **k):
        if "run.lock" in str(src):
            raise FileNotFoundError(src)   # rival renamed it away first
        return real_rename(src, dst, *a, **k)
    monkeypatch.setattr(runmod.os, "rename", lose_rename)
    removed = []
    monkeypatch.setattr(runmod.shutil, "rmtree",
                        lambda p, **k: removed.append(p))
    assert runmod.acquire_lock(cfg) is False           # yields to next tick
    assert removed == []                               # nothing deleted
    assert cfg.lock_dir.exists()
    assert (cfg.lock_dir / "pid").read_text() == str(dead)
    assert list(cfg.home.glob("run.lock.stale-*")) == []


# --------------------------------- scan-crash containment (review-r4 #29)

def test_scan_crash_degrades_to_alert_and_run_continues(cfg, monkeypatch):
    """review-r4 #29: a non-RuntimeError scan crash (rc=0 garbage JSON)
    must alert and let the run drain the resumable backlog, not abort."""
    led = Ledger(cfg.ledger_path)
    led.insert_session(session_id="resume-me", game="kamla",
                       operator_email="o@x.com", player_email="p@x.com",
                       drive_path="kamla/o/p/resume-me", drive_ctime="2026",
                       md5_video="m", bytes_=1, state="PACKAGED")
    led.close()

    def bad_scan(cfg_, ledger_):
        raise json.JSONDecodeError("truncated", "", 0)
    monkeypatch.setattr(runmod.ingest, "scan", bad_scan)
    alerts = []
    monkeypatch.setattr(runmod, "_alert",
                        lambda cfg_, text, sent: alerts.append(text))
    batches = []
    monkeypatch.setattr(
        runmod, "process_batch",
        lambda cfg_, ledger_, sids, alerts, dest_prefix:
        batches.append(list(sids)) or reports.BatchStats(
            batch_no=1, finished_ist=datetime.now(C.IST), duration_min=1,
            delivered=0, total=0, auto_fixed=0, rejected=0))
    monkeypatch.setattr(runmod, "send_daily_report_if_due",
                        lambda cfg_, ledger_: False)
    assert runmod.run(cfg, max_batches=2, send_telegram=False) == 0
    assert any("Drive scan failed" in a for a in alerts)
    assert batches == [["resume-me"]]      # backlog still processed
    assert not cfg.lock_dir.exists()       # lock released cleanly


# ------------------------------------ orphan-sweep triggers (review-r4 #30)

def test_orphan_sweep_all_three_triggers_and_no_trigger(cfg, ledger,
                                                        monkeypatch):
    """review-r4 #30: finalize fires on leftover work dir, leftover
    -analysis dir, OR missing dossier_path — and NOT on a clean reject."""
    def seed(sid, dossier):
        ledger.insert_session(
            session_id=sid, game="kamla", operator_email="o@x.com",
            player_email="p@x.com", drive_path=f"kamla/o/p/{sid}",
            drive_ctime="2026", md5_video=sid, bytes_=1, state="REJECTED")
        if dossier:
            ledger.update(sid, dossier_path=str(cfg.dossiers / sid))
    seed("r-workdir", dossier=True)        # (a) work dir alone triggers
    (cfg.work / "r-workdir").mkdir(parents=True)
    seed("r-analysis", dossier=True)       # (b) -analysis dir alone
    (cfg.work / "r-analysis-analysis").mkdir(parents=True)
    seed("r-nodossier", dossier=False)     # (c) NULL dossier_path alone
    seed("r-clean", dossier=True)          # (d) nothing leftover
    finalized = []
    monkeypatch.setattr(runmod.deliver, "finalize_rejected",
                        lambda cfg_, ledger_, sid: finalized.append(sid))
    runmod._finalize_orphan_rejects(cfg, ledger)
    assert sorted(finalized) == ["r-analysis", "r-nodossier", "r-workdir"]
    assert "r-clean" not in finalized
