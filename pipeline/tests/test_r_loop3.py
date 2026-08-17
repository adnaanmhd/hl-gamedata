"""r-loop 3 — regression tests for the confirmed findings of review
iteration 3 (FLIP_SESSION_KICKOFF_PROMPT §5).

Each test names the defect it pins and the consequence of losing it.
"""
import json
import sqlite3
import subprocess

import pytest

from pipeline import config as C
from pipeline import continuous as cont
from pipeline import fix, gate, validate
from pipeline.ledger import Ledger
from pipeline.scanner import MotionTimeline


# ---------------------------------------------------- BLOCKER: SIGTERM path

def test_shutdown_broken_pool_does_not_quarantine(cfg, ledger, monkeypatch):
    """systemd's default KillMode=control-group SIGTERMs every pid in the
    unit cgroup, including the spawn validation worker — which does NOT
    inherit the driver's handler. The parent read the resulting
    BrokenProcessPool as a native crash and wrote QUARANTINED, a TERMINAL
    state with no automatic re-entry, for EVERY in-flight session. A
    graceful `systemctl stop` was therefore strictly worse than kill -9,
    the case the design is hardened for. The row must stay VALIDATING."""
    sid = "s-term"
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="op@x.com", player_email="p@x.com",
                          drive_path="kamla/op@x.com/p@x.com/" + sid,
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="a" * 32, bytes_=10, state="INGESTED")
    (cfg.work / sid).mkdir(parents=True)
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    alerts = []
    monkeypatch.setattr(cont.AlertBook, "alert",
                        lambda self, t: alerts.append(t))

    class _BrokenPool:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def map(self, *a, **k):
            raise cont.concurrent.futures.process.BrokenProcessPool("term")
    monkeypatch.setattr(cont.concurrent.futures, "ProcessPoolExecutor",
                        _BrokenPool)
    monkeypatch.setattr(cont, "_POOL_DISABLED", False)
    drv.stop.set()                      # we are shutting down

    row = ledger.get(sid)
    assert drv._validate_one(ledger, sid, row) is None
    assert ledger.get(sid)["state"] == "VALIDATING", \
        "a shutdown-broken pool must leave the row resumable, not terminal"
    assert not any("validation crashed" in a for a in alerts)


def test_broken_pool_while_running_still_quarantines(cfg, ledger,
                                                     monkeypatch):
    """The genuine native-crash path must be untouched by the fix above."""
    sid = "s-crash"
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="op@x.com", player_email="p@x.com",
                          drive_path="kamla/op@x.com/p@x.com/" + sid,
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="a" * 32, bytes_=10, state="INGESTED")
    (cfg.work / sid).mkdir(parents=True)
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    monkeypatch.setattr(cont.AlertBook, "alert", lambda self, t: None)

    class _BrokenPool:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def map(self, *a, **k):
            raise cont.concurrent.futures.process.BrokenProcessPool("boom")
    monkeypatch.setattr(cont.concurrent.futures, "ProcessPoolExecutor",
                        _BrokenPool)
    monkeypatch.setattr(cont, "_POOL_DISABLED", False)
    monkeypatch.setattr(C, "CONT_DRAIN_GRACE_S", 0)
    # stop NOT set: this is a real crash
    assert drv._validate_one(ledger, sid, ledger.get(sid)) == "QUARANTINED"


def test_unit_sets_killmode_mixed():
    """The belt for the above: systemd must signal only the main process.
    Verified separately that `uv run` forwards SIGTERM to its python child
    on both hosts, so the driver still gets its graceful stop."""
    unit = (C.REPO_ROOT / "pipeline" / "systemd"
            / "hl-continuous.service.in").read_text()
    assert "KillMode=mixed" in unit
    stop_s = int([l for l in unit.splitlines()
                  if l.startswith("TimeoutStopSec=")][0].split("=")[1])
    assert stop_s > C.CONT_DRAIN_GRACE_S, \
        "systemd must not SIGKILL before the driver's own drain completes"


# ------------------------------------------------- delivery / disk recovery

def test_delivery_enospc_is_transient_not_quarantine(cfg, ledger,
                                                     monkeypatch):
    """F7 keeps U running when the disk is low, precisely so the reclaim
    path is not starved. With a bare `except Exception -> QUARANTINED`, an
    ENOSPC from stage_session's multi-GB copy converted the whole
    READY/PACKAGED/UPLOADED backlog into terminal rows within minutes, each
    leaking its work dir and making the disk worse."""
    sid = "s-enospc"
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="op@x.com", player_email="p@x.com",
                          drive_path="kamla/op@x.com/p@x.com/" + sid,
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="a" * 32, bytes_=10, state="READY")
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    monkeypatch.setattr(cont.AlertBook, "alert", lambda self, t: None)

    def boom(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(cont.deliver, "deliver_session", boom)
    drv._deliver_one(ledger, sid)
    assert ledger.get(sid)["state"] == "READY", \
        "a host-level error must leave the session resumable"

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("ffmpeg", 1800)
    monkeypatch.setattr(cont.deliver, "deliver_session", timeout)
    drv._deliver_one(ledger, sid)
    assert ledger.get(sid)["state"] == "READY"

    # a genuine logic error still quarantines
    def logic(*a, **k):
        raise KeyError("bad plan")
    monkeypatch.setattr(cont.deliver, "deliver_session", logic)
    drv._deliver_one(ledger, sid)
    assert ledger.get(sid)["state"] == "QUARANTINED"


def test_quarantined_media_counts_then_is_reclaimed(cfg, ledger):
    """QUARANTINED was the one terminal state with no wipe AND was absent
    from the media cap, so ~90 GB could sit unseen while intake stopped on
    the disk low-water with no path back."""
    from pipeline import run as runmod
    sid = "s-quar"
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="op@x.com", player_email="p@x.com",
                          drive_path="kamla/op@x.com/p@x.com/" + sid,
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="a" * 32, bytes_=10, state="INGESTED")
    ledger.set_state(sid, "QUARANTINED", "validation crashed")
    (cfg.work / sid).mkdir(parents=True)
    (cfg.work / sid / "video.mp4").write_bytes(b"x" * 64)

    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    assert drv._local_count(ledger) == 1, \
        "fresh quarantined media must count against the cap"

    # fresh: the sweep leaves it alone for the triage window
    runmod._sweep_terminal_work(cfg, ledger)
    assert (cfg.work / sid).exists()

    # aged past the reclaim window: media goes, row stays, cap frees up
    old = "2026-08-01T00:00:00+00:00"
    ledger.db.execute("UPDATE sessions SET updated_at=? WHERE session_id=?",
                      (old, sid))
    ledger.db.commit()
    runmod._sweep_terminal_work(cfg, ledger)
    assert not (cfg.work / sid).exists()
    assert ledger.get(sid)["state"] == "QUARANTINED"
    assert drv._local_count(ledger) == 0, \
        "reclaimed media must stop counting, or the cap ratchets shut"


def test_terminal_age_unparseable_never_authorises_deletion():
    from pipeline import run as runmod
    assert runmod._terminal_age_h({"updated_at": None}) == 0.0
    assert runmod._terminal_age_h({"updated_at": "not-a-date"}) == 0.0


# --------------------------------------------------------- housekeeping H

def test_housekeeping_duty_failure_does_not_kill_the_others(cfg,
                                                            monkeypatch):
    """The block ran seven duties unguarded with the cadence stamp LAST, so
    the first to raise (backup_daily on a full disk) skipped every later
    duty for the life of the process and left the hourly block retrying
    every 20s — disabling the only disk-reclaim sweeps during the exact
    incident they exist for, while the digest still read healthy."""
    from pipeline import ingest
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    ran = []
    monkeypatch.setattr(Ledger, "backup_daily",
                        lambda self, *a, **k: (_ for _ in ()).throw(
                            sqlite3.OperationalError("disk is full")))
    from pipeline import run as runmod
    monkeypatch.setattr(runmod, "_finalize_orphan_rejects",
                        lambda *a, **k: ran.append("orphans"))
    monkeypatch.setattr(runmod, "_sweep_terminal_work",
                        lambda *a, **k: ran.append("sweep"))
    monkeypatch.setattr(runmod, "_upload_ceiling_alert",
                        lambda *a, **k: ran.append("ceiling"))
    alerts = []
    monkeypatch.setattr(cont.AlertBook, "alert",
                        lambda self, t: alerts.append(t))
    assert cont.run_continuous(cfg, until_idle=True, max_wall_s=20,
                               send_telegram=False,
                               install_signals=False) == 0
    assert "sweep" in ran and "orphans" in ran and "ceiling" in ran, \
        "a failing backup must not take the reclaim sweeps down with it"
    assert any("ledger backup" in a for a in alerts)


def test_digest_anchor_write_failure_does_not_loop(cfg, monkeypatch):
    """The anchor is written AFTER the send so a kill duplicates rather
    than loses a digest — but an OSError from that write escaped, the lane
    re-entered 20s later, re-read the stale anchor and sent again: 180
    digests an hour, forever, during the very incident (disk full) when
    Telegram is the operator's only view."""
    drv = cont.ContinuousDriver(cfg, send_telegram=True)
    sent = []
    monkeypatch.setattr(cont.telegram, "send_message",
                        lambda _c, m: sent.append(m))
    monkeypatch.setattr(cont.AlertBook, "alert", lambda self, t: None)
    real_write = type(cfg.reports_dir).write_text

    def no_space(self, *a, **k):
        if self.name.startswith(".last_digest"):
            raise OSError(28, "No space left on device")
        return real_write(self, *a, **k)
    monkeypatch.setattr(type(cfg.reports_dir), "write_text", no_space)

    led = Ledger(cfg.ledger_path)
    try:
        drv._send_digest(led)
        assert len(sent) == 1
        drv._send_digest(led)             # next tick
        drv._send_digest(led)
        assert len(sent) == 1, \
            "an unwritable anchor must not re-send every tick"
    finally:
        led.close()


def test_stuck_list_sorted_so_oldest_hold_is_visible(cfg, ledger):
    """HOLD rows were appended AFTER the age-sorted query rows and the list
    was sliced [:5] without re-sorting, so a 40h-held session stayed
    invisible behind five 7h ones — exactly what the HOLD-aging fix existed
    to surface."""
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    for i in range(5):
        sid = f"fixq-{i}"
        ledger.insert_session(session_id=sid, game="kamla",
                              operator_email="o@x.com", player_email="p@x.com",
                              drive_path=f"kamla/o@x.com/p@x.com/{sid}",
                              drive_ctime="2026-08-14T10:00:00.000Z",
                              md5_video="a" * 32, bytes_=1,
                              state="FIX_QUEUED")
        ledger.db.execute(
            "UPDATE sessions SET updated_at=? WHERE session_id=?",
            ("2026-08-17T00:00:00+00:00", sid))
    sid = "held-old"
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="o@x.com", player_email="p@x.com",
                          drive_path=f"kamla/o@x.com/p@x.com/{sid}",
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="b" * 32, bytes_=1, state="HOLD_VLM")
    ledger.db.execute(
        "UPDATE sessions SET updated_at=? WHERE session_id=?",
        ("2026-08-10T00:00:00+00:00", sid))
    ledger.db.execute(
        "INSERT INTO events(session_id, ts, from_state, to_state, detail) "
        "VALUES(?,?,?,?,?)",
        (sid, "2026-08-10T00:00:00+00:00", "VALIDATING", "HOLD_VLM", ""))
    ledger.db.commit()
    lines, total = drv._stuck_lines(ledger)
    assert total == 6
    assert any("held-old" in l for l in lines), \
        "the oldest stuck session must appear in the top-5 list"
    assert lines[0].startswith("held-old")


# ------------------------------------------------------------ CLI safety

def test_run_continuous_rejects_unknown_args(monkeypatch, capsys):
    """The default destination is the REAL client tree; a mistyped
    --dest-prefix silently fell back to it, which at canary time would
    upload test sessions into production where the teardown never looks."""
    from pipeline import run as runmod
    assert runmod.main(["run-continuous", "--dest-prefix", "_pipeline_test"]) \
        == 2
    assert runmod.main(["run-continuous", "--dest_prefix=_x"]) == 2
    assert runmod.main(["run-continuous", "--dest-prefix="]) == 2
    out = capsys.readouterr().out
    assert "humynlabs" in out or "non-empty" in out
