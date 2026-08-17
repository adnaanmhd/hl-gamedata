"""Continuous-driver tests (PIPELINE_CONTINUOUS_DESIGN.md).

The ledger stays the source of truth, so the harness seeds ledger rows (the
existing _seed pattern) and fakes only the I/O seams: ingest.list_drive
(the fake Drive), ingest.download, ContinuousDriver._validate_one (scripted
verdicts — spawn workers cannot see monkeypatches, so the seam sits above
the pool), fix.plan_fixes/apply_fixes, deliver.deliver_session.
run_continuous(until_idle=True) is the bounded-run boundary every
resume/kill test needs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import config as C
from pipeline import continuous as cont
from pipeline import deliver, fix, ingest, reports, telegram
from pipeline import vlm as vlmmod
from pipeline.ledger import Ledger
from pipeline.tests.conftest import make_session_entries

UTC = timezone.utc


def _seed(led, sid, state="DISCOVERED", game="kamla", parent=None,
          ctime="2026-08-14T10:00:00.000Z", attempts=0):
    led.insert_session(
        session_id=sid, game=game, operator_email="op@x.com",
        player_email="p@x.com", drive_path=f"{game}/op/p/{sid}",
        drive_ctime=ctime, md5_video="m" + sid[-4:], bytes_=100,
        state=state, parent_id=parent)
    if attempts:
        led.update(sid, fix_attempts=attempts)


SID1 = "2026-08-14T10-00-00Z_kamla_c_0123456789abcdef"
SID2 = "2026-08-14T11-00-00Z_kamla_c_abcdef0123456789"


# ------------------------------------------------------------- primitives

def test_gate_resize_and_stop():
    g = cont.ResizableGate(2)
    stop = threading.Event()
    assert g.acquire(stop) and g.acquire(stop)
    assert g.active == 2
    got = []
    t = threading.Thread(target=lambda: got.append(g.acquire(stop)))
    t.start()
    time.sleep(0.15)
    assert not got                       # blocked at target
    g.set_target(3)                      # raise wakes the waiter
    t.join(2)
    assert got == [True]
    g.set_target(1)                      # lower never interrupts the active
    assert g.active == 3
    g.release(); g.release(); g.release()
    assert g.active == 0
    t2 = threading.Thread(target=lambda: got.append(g.acquire(stop)))
    g.set_target(0)                      # floor is 1, so this means 1
    assert g.target == 1
    t2.start(); t2.join(2)
    assert got == [True, True]
    g.release()
    stop.set()
    assert g.acquire(stop) is False      # stop aborts


def test_cooldowns_fake_clock():
    now = [0.0]
    c = cont.Cooldowns(mono_fn=lambda: now[0])
    assert c.ready("a")
    c.set("a", 60)
    assert not c.ready("a")
    assert c.blocked() == {"a"}
    now[0] = 61
    assert c.ready("a")
    assert c.blocked() == set()


def test_alertbook_ttl(cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send_message",
                        lambda _cfg, text: sent.append(text))
    now = [0.0]
    book = cont.AlertBook(cfg, ttl_s=100, mono_fn=lambda: now[0])
    book.alert("disk low")
    book.alert("disk low")               # deduped inside TTL
    assert len(sent) == 1
    now[0] = 101
    book.alert("disk low")               # re-raised after TTL — the point
    assert len(sent) == 2


def test_autoscale_decision_rules():
    kw = dict(target=10, active=10, queue_depth=20, cpu_pct=50.0,
              p429_per_min=0.0, rung_climb=False, cpu_crit_streak=False,
              lo=8, hi=44)
    # backpressure outranks everything and steps down hard
    n, why = cont.autoscale_decision(**{**kw, "target": 14,
                                        "p429_per_min": 2.0})
    assert n == 14 - C.CONT_STEP_DOWN and "429" in why
    n, _ = cont.autoscale_decision(**{**kw, "target": 14,
                                      "rung_climb": True})
    assert n == 14 - C.CONT_STEP_DOWN
    # floor clamp
    n, _ = cont.autoscale_decision(**{**kw, "target": 8,
                                      "p429_per_min": 9.0})
    assert n == 8
    # sustained crit cpu steps down
    n, _ = cont.autoscale_decision(**{**kw, "cpu_pct": 97.0,
                                      "cpu_crit_streak": True})
    assert n == 8
    # one crit interval alone holds
    n, _ = cont.autoscale_decision(**{**kw, "cpu_pct": 97.0})
    assert n == 10
    # scale up under low cpu + queue
    n, why = cont.autoscale_decision(**kw)
    assert n == 10 + C.CONT_STEP_UP and "queue" in why
    # ceiling clamp
    n, _ = cont.autoscale_decision(**{**kw, "target": 44})
    assert n == 44
    # no up-step when queue <= active
    n, _ = cont.autoscale_decision(**{**kw, "queue_depth": 5})
    assert n == 10
    # unknown cpu holds
    n, _ = cont.autoscale_decision(**{**kw, "cpu_pct": None})
    assert n == 10


# ----------------------------------------------------------- driver fakes

def _fake_download(monkeypatch, log=None):
    def dl(cfg, led, sid):
        if log is not None:
            log.append(("dl", sid))
        (cfg.work / sid).mkdir(parents=True, exist_ok=True)
        led.set_state(sid, "INGESTED")
        return "v2"
    monkeypatch.setattr(ingest, "download", dl)


def _script_validate(monkeypatch, script: dict[str, list[str]], log=None):
    """Scripted _validate_one: pops the sid's next outcome and performs the
    matching ledger transition (the runner reads state, so the fake must
    write it exactly like the real one)."""
    def v(self, led, sid, row):
        out = script[sid].pop(0)
        if log is not None:
            log.append(("val", sid, out))
        led.set_state(sid, "VALIDATING")
        if out == "READY":
            led.set_reasons(sid, [], 1)
            led.set_state(sid, "READY")
        elif out == "FIX_QUEUED":
            led.set_reasons(
                sid, [{"code": "STR_CAMERA_NONNULL", "blocking": True,
                       "fixable": True}], 2)
            led.set_state(sid, "FIX_QUEUED")
        elif out == "HOLD_VLM":
            led.set_state(sid, "HOLD_VLM", "sweep unfinished (F5)")
        elif out == "REJECTED":
            led.set_reasons(
                sid, [{"code": "CNT_SHORT", "blocking": True,
                       "fixable": False}], 3)
            led.set_state(sid, "REJECTED", "CNT_SHORT")
            self._finalize_reject(led, sid)
        return out
    monkeypatch.setattr(cont.ContinuousDriver, "_validate_one", v)


def _fake_deliver(monkeypatch, outcomes=None, log=None):
    """outcomes: sid -> list of statuses to emit; default always delivered."""
    def ds(cfg, led, sid, dest_prefix=C.VENDOR):
        status = (outcomes[sid].pop(0) if outcomes and outcomes.get(sid)
                  else "delivered")
        if log is not None:
            log.append(("up", sid, status))
        if status == "delivered":
            led.set_state(sid, "PACKAGED")
            led.set_state(sid, "UPLOADED")
            led.update(sid, duration_delivered_s=120.0,
                       delivered_at=datetime.now(UTC).isoformat(
                           timespec="seconds"))
            led.set_state(sid, "DELIVERED")
            return deliver.DeliveryOutcome(sid, "delivered", hours=120 / 3600)
        if status == "failed_gate":
            return deliver.DeliveryOutcome(
                sid, "failed_gate", detail="camera cols non-null",
                gate_fails=["FAIL: camera columns must be empty"])
        return deliver.DeliveryOutcome(sid, "failed_upload",
                                       detail="rclone rc=1")
    monkeypatch.setattr(deliver, "deliver_session", ds)


def _run(cfg, **kw):
    return cont.run_continuous(cfg, until_idle=True, send_telegram=False,
                               install_signals=False, max_wall_s=60, **kw)


@pytest.fixture(autouse=True)
def _fast_knobs(monkeypatch):
    monkeypatch.setattr(C, "CONT_SCAN_INTERVAL_S", 0.2)
    monkeypatch.setattr(C, "CONT_DISPATCH_IDLE_S", 0.05)
    monkeypatch.setattr(C, "CONT_DRAIN_GRACE_S", 10)


# ------------------------------------------------------------ end-to-end

def test_full_flow_two_sessions(cfg, monkeypatch):
    entries = (make_session_entries(sid=SID1, md5="a" * 32)
               + make_session_entries(sid=SID2, md5="b" * 32,
                                      ctime="2026-08-14T11:00:00.000Z"))
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: entries)
    log = []
    _fake_download(monkeypatch, log)
    _script_validate(monkeypatch, {SID1: ["READY"], SID2: ["READY"]}, log)
    _fake_deliver(monkeypatch, log=log)
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        for sid in (SID1, SID2):
            assert led.get(sid)["state"] == "DELIVERED"
            n = led.db.execute(
                "SELECT COUNT(*) n FROM events WHERE session_id=? AND "
                "to_state='DELIVERED'", (sid,)).fetchone()["n"]
            assert n == 1                      # exactly-once
        # the continuous driver NEVER touches the batches table
        assert led.db.execute(
            "SELECT COUNT(*) n FROM batches").fetchone()["n"] == 0
    finally:
        led.close()


def test_fix_reentry_immediate_same_run(cfg, ledger, monkeypatch):
    """The parked-fix-tail killer: FIX_QUEUED -> fix -> revalidate ->
    deliver inside ONE run (the batch driver needed the next 30-min tick)."""
    _seed(ledger, SID1, state="INGESTED")
    (cfg.work / SID1).mkdir(parents=True)
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    _script_validate(monkeypatch, {SID1: ["FIX_QUEUED", "READY"]})
    fixed = []

    def fake_fix(self, led, sid):
        fixed.append(sid)
        led.update(sid, fix_attempts=1)
        led.set_state(sid, "FIXING", "attempt 1")
        led.set_state(sid, "REVALIDATING", "fixes applied")
        return True
    monkeypatch.setattr(cont.ContinuousDriver, "_fix_one", fake_fix)
    _fake_deliver(monkeypatch)
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(SID1)["state"] == "DELIVERED"
        assert fixed == [SID1]
    finally:
        led.close()


def test_gate_fail_handback_same_run(cfg, ledger, monkeypatch):
    _seed(ledger, SID1, state="READY")
    (cfg.work / SID1).mkdir(parents=True)
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    _script_validate(monkeypatch, {SID1: ["READY"]})
    fixed = []

    def fake_fix(self, led, sid):
        fixed.append(sid)
        led.update(sid, fix_attempts=1)
        led.set_state(sid, "FIXING", "attempt 1")
        led.set_state(sid, "REVALIDATING", "fixes applied")
        return True
    monkeypatch.setattr(cont.ContinuousDriver, "_fix_one", fake_fix)
    _fake_deliver(monkeypatch,
                  outcomes={SID1: ["failed_gate", "delivered"]})
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(SID1)["state"] == "DELIVERED"
        assert fixed == [SID1]             # gate-fail went through fix NOW
    finally:
        led.close()


def test_split_children_flow_same_run(cfg, ledger, monkeypatch):
    """A real _fix_one split: children become INGESTED rows, the dispatcher
    picks them (only claimant — no children_sink), and they deliver in the
    same run while the parent ends SPLIT."""
    child = SID1 + "-p1"
    _seed(ledger, SID1, state="FIX_QUEUED")
    ledger.set_reasons(SID1, [{"code": "CNT_MID_NONGAMEPLAY",
                               "blocking": True, "fixable": True}], 2)
    (cfg.work / SID1).mkdir(parents=True)
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    monkeypatch.setattr(fix, "plan_fixes", lambda reasons, game, has_raw: {
        "steps": [("FIX_CUT_SEGMENTS", {"cut": [[10.0, 20.0]]})],
        "unfixable": []})
    monkeypatch.setattr(fix, "apply_fixes",
                        lambda work, plan, game, dossier_dir, split_root: {
                            "applied": ["FIX_CUT_SEGMENTS"],
                            "children": {"segments": [
                                {"id": child, "t0": 0, "t1": 80,
                                 "duration_s": 80.0}], "dropped": []},
                            "error": None})
    _script_validate(monkeypatch, {child: ["READY"]})
    _fake_deliver(monkeypatch)
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(SID1)["state"] == "SPLIT"
        crow = led.get(child)
        assert crow["state"] == "DELIVERED" and crow["parent_id"] == SID1
    finally:
        led.close()


def test_fix_budget_exhausted_rejects(cfg, ledger, monkeypatch):
    _seed(ledger, SID1, state="FIX_QUEUED", attempts=C.FIX_RETRIES)
    ledger.set_reasons(SID1, [{"code": "STR_CAMERA_NONNULL",
                               "blocking": True, "fixable": True}], 2)
    (cfg.work / SID1).mkdir(parents=True)
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    finalized = []
    monkeypatch.setattr(deliver, "finalize_rejected",
                        lambda _c, _l, sid: finalized.append(sid))
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        row = led.get(SID1)
        assert row["state"] == "REJECTED"
        assert "R2" in led.db.execute(
            "SELECT detail FROM events WHERE session_id=? AND "
            "to_state='REJECTED'", (SID1,)).fetchone()["detail"]
        # per-session terminal hook fired (the hourly orphan sweep may
        # ALSO re-check it because the no-op fake never set dossier_path —
        # the real finalize_rejected is idempotent by design)
        assert set(finalized) == {SID1}
    finally:
        led.close()


def test_hold_vlm_30min_cooldown(cfg, ledger, monkeypatch):
    _seed(ledger, SID1, state="INGESTED")
    (cfg.work / SID1).mkdir(parents=True)
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    log = []
    _script_validate(monkeypatch, {SID1: ["HOLD_VLM", "HOLD_VLM"]}, log)
    mono = [1000.0]
    clocks = cont._Clocks(mono=lambda: mono[0])
    # IN-RUN cooldown: after the HOLD verdict the session is not re-picked
    # (idle exit with exactly ONE validation despite HOLD_VLM sitting there)
    assert _run(cfg, clocks=clocks) == 0
    assert [e for e in log if e[0] == "val"] == [("val", SID1, "HOLD_VLM")]
    led = Ledger(cfg.ledger_path)
    assert led.get(SID1)["state"] == "HOLD_VLM"
    led.close()
    # RESTART semantics (documented): cooldowns are in-memory per-driver —
    # a fresh driver retries the held session once immediately. The 30-min
    # eligibility clock itself is proven in test_cooldowns_fake_clock.
    assert _run(cfg, clocks=clocks) == 0
    assert len([e for e in log if e[0] == "val"]) == 2


def test_pick_download_media_cap_and_disk(cfg, ledger, monkeypatch):
    _seed(ledger, SID1, state="DISCOVERED")
    _seed(ledger, SID2, state="INGESTED")   # occupies one local slot
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    # disk patched HIGH and alerts captured FIRST: on a host under the
    # 100 GB low-water the first assertion would otherwise pass via the
    # disk branch (cap unproven) and fire a real Telegram attempt
    alerts = []
    monkeypatch.setattr(cont.AlertBook, "alert",
                        lambda self, text: alerts.append(text))
    monkeypatch.setattr(deliver, "disk_free_gb", lambda p: 500)
    monkeypatch.setattr(C, "CONT_MEDIA_CAP_SESSIONS", 1)
    assert drv._pick_download(ledger) is None          # cap reached
    monkeypatch.setattr(C, "CONT_MEDIA_CAP_SESSIONS", 5)
    monkeypatch.setattr(deliver, "disk_free_gb", lambda p: 50)
    assert drv._pick_download(ledger) is None          # low water
    assert any("downloads paused" in a for a in alerts)
    monkeypatch.setattr(deliver, "disk_free_gb", lambda p: 500)
    assert drv._pick_download(ledger) == SID1          # FIFO pick + claim
    # the pick committed DOWNLOADING inside the intake lock (scan-race fix)
    assert ledger.get(SID1)["state"] == "DOWNLOADING"
    assert drv._pick_download(ledger) is None          # owned now


def test_download_transient_cooldown(cfg, ledger, monkeypatch):
    _seed(ledger, SID1, state="DISCOVERED")
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])

    def boom(cfg_, led_, sid_):
        raise ingest.DownloadError("rclone rc=1", kind="transient")
    monkeypatch.setattr(ingest, "download", boom)
    monkeypatch.setattr(telegram, "send_message", lambda *_a: None)
    assert _run(cfg) == 0                  # exits idle: session cooling
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(SID1)["state"] == "DISCOVERED"   # retryable, kept
    finally:
        led.close()


def test_validating_row_resumes(cfg, ledger, monkeypatch):
    """kill -9 mid-validation leaves VALIDATING; restart re-validates."""
    _seed(ledger, SID1, state="VALIDATING")
    (cfg.work / SID1).mkdir(parents=True)
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    _script_validate(monkeypatch, {SID1: ["READY"]})
    _fake_deliver(monkeypatch)
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(SID1)["state"] == "DELIVERED"
    finally:
        led.close()


def test_fixing_row_triage_revalidates(cfg, ledger, monkeypatch):
    """FIXING with no manifest/partials -> REVALIDATING (run._recover_split
    semantics), then the normal path."""
    _seed(ledger, SID1, state="FIXING")
    (cfg.work / SID1).mkdir(parents=True)
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    _script_validate(monkeypatch, {SID1: ["READY"]})
    _fake_deliver(monkeypatch)
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(SID1)["state"] == "DELIVERED"
        evs = [r["to_state"] for r in led.db.execute(
            "SELECT to_state FROM events WHERE session_id=? ORDER BY id",
            (SID1,)).fetchall()]
        assert "REVALIDATING" in evs
    finally:
        led.close()


# ---------------------------------------------------------------- digest

def test_digest_message_format():
    d = reports.DigestStats(
        now_ist=datetime(2026, 8, 18, 14, 5, tzinfo=C.IST), window_h=3.0,
        delivered_n=2, delivered_hours=0.42, rejected_n=1,
        reject_labels=["black-frozen"], hours_kamla=29.4, hours_ow=0.0,
        backlog_undownloaded=135, backlog_inflight=45, backlog_fix=99,
        backlog_hold=0, incomplete=3, quarantined_n=2, on_fallback=1,
        pool_target=10, pool_active=9, vlm_rung=1,
        stuck=["sid1 (FIX_QUEUED 8.1h)"], stuck_total=4)
    msg = reports.build_digest_message(d, None)
    assert msg == (
        "📡 digest 14:05 · last 3.0h\n"
        "window: 2 delivered (+0.4 h) · 1 rejected (black-frozen) · "
        "2 quarantined\n"
        "totals: Kamla 29.4/500 · OW 0.0/500 (Σ 29.4/1000)\n"
        "backlog: 135 undownloaded · 45 in-flight · 99 fix · 0 hold · "
        "3 incomplete\n"
        "pool: 9/10 active · rung 1\n"
        "1 on fallback model\n"
        "stuck: sid1 (FIX_QUEUED 8.1h) (+3 more)")


def test_digest_anchor_after_send(cfg, ledger, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send_message",
                        lambda _cfg, text: sent.append(text))
    _seed(ledger, SID1, state="DISCOVERED")
    now = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
    drv = cont.ContinuousDriver(
        cfg, clocks=cont._Clocks(utcnow=lambda: now))
    drv._counts = ledger.counts_by_state()
    drv._send_digest(ledger)               # no anchor -> first digest fires
    assert len(sent) == 1 and "📡 digest" in sent[0]
    anchor = (cfg.reports_dir / ".last_digest").read_text()
    assert anchor == now.isoformat(timespec="seconds")
    drv._send_digest(ledger)               # 0h elapsed -> silent
    assert len(sent) == 1
    now = now + timedelta(hours=3, minutes=1)
    drv.clk.utcnow = lambda: now
    drv._send_digest(ledger)
    assert len(sent) == 2
    # anchor advanced: contiguous windows
    assert (cfg.reports_dir / ".last_digest").read_text() \
        == now.isoformat(timespec="seconds")


def test_digest_send_failure_keeps_anchor(cfg, ledger, monkeypatch):
    def boom(_cfg, _text):
        raise telegram.TelegramError("api down")
    monkeypatch.setattr(telegram, "send_message", boom)
    drv = cont.ContinuousDriver(cfg)
    drv._counts = {}
    drv._send_digest(ledger)
    assert not (cfg.reports_dir / ".last_digest").exists()   # retried later


def test_digest_windows_delivered_and_rejected(cfg, ledger, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send_message",
                        lambda _cfg, text: sent.append(text))
    # real wall-clock window: the REJECTED transition's events row is
    # stamped by the ledger with REAL now, so the window must cover it —
    # hi sits WELL ahead ([lo, hi) semantics don't depend on the margin;
    # a 5 s margin flaked under load, r-loop 1)
    now = datetime.now(UTC) + timedelta(seconds=60)
    lo = (now - timedelta(hours=3)).isoformat(timespec="seconds")
    (cfg.reports_dir / ".last_digest").write_text(lo)
    # inside the window
    _seed(ledger, SID1, state="READY")
    ledger.update(SID1, duration_delivered_s=360.0,
                  delivered_at=(now - timedelta(hours=1)).isoformat(
                      timespec="seconds"))
    ledger.set_state(SID1, "DELIVERED")
    _seed(ledger, SID2, state="VALIDATING")
    ledger.set_reasons(SID2, [{"code": "CNT_SHORT", "blocking": True,
                               "fixable": False}], 3)
    ledger.set_state(SID2, "REJECTED", "CNT_SHORT")
    drv = cont.ContinuousDriver(cfg,
                                clocks=cont._Clocks(utcnow=lambda: now))
    drv._counts = ledger.counts_by_state()
    drv._send_digest(ledger)
    assert len(sent) == 1
    assert "window: 1 delivered (+0.1 h) · 1 rejected (<70s)" in sent[0]


# ------------------------------------------------- pressure/rung plumbing

def test_vlm_pressure_hook_writes_line(cfg, monkeypatch):
    path = cfg.logs / "vlm-pressure.jsonl"
    monkeypatch.setattr(vlmmod, "_pressure_path", str(path))
    monkeypatch.setattr(C, "VLM_MAX_TRIES", 1)

    def post_429(url, headers, body, timeout_s=180):
        raise urllib.error.HTTPError(url, 429, "quota", {}, None)
    monkeypatch.setattr(vlmmod, "_post", post_429)
    with pytest.raises(vlmmod.VLMError):
        vlmmod.generate("k" * 20, "gemini-3.7-flash", [{"text": "hi"}])
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert lines and all(ev["status"] == 429 for ev in lines)
    assert all("k" * 20 not in json.dumps(ev) for ev in lines)  # no secrets


def test_pressure_read_and_rung_quiet_reset(cfg):
    now = [10_000.0]
    drv = cont.ContinuousDriver(
        cfg, clocks=cont._Clocks(now=lambda: now[0]))
    p = drv.pressure_path
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        for dt in (500, 400, 30):
            f.write(json.dumps({"ts": now[0] - dt, "status": 429,
                                "rung": 0, "tag": "genlang r0"}) + "\n")
        f.write(json.dumps({"ts": now[0] - 20, "status": 503,
                            "rung": 0, "tag": "genlang r0"}) + "\n")
    drv._read_pressure()
    assert len(drv._pressure_recent) == 3          # 5xx not counted as 429
    assert drv._last_pressure_ep == now[0] - 20
    # rung stickiness + quiet reset
    drv.absorb_rung(2)
    assert drv.current_rung() == 2
    drv._maybe_reset_rung()
    assert drv.current_rung() == 2                 # not quiet yet
    now[0] += C.CONT_RUNG_QUIET_RESET_MIN * 60 + 1
    drv._maybe_reset_rung()
    assert drv.current_rung() == 0                 # model of record restored
    # incremental read: nothing new -> window empties over time
    drv._read_pressure()
    assert drv._pressure_recent == []


# --------------------------------------------------------- lock and flag

def test_flag_interlock(cfg, monkeypatch):
    monkeypatch.setattr(C, "PIPELINE_CONTINUOUS", False)
    assert cont.run_continuous(cfg, install_signals=False) == 2


def test_lock_refusal(cfg, monkeypatch):
    from pipeline import run as runmod
    assert runmod.acquire_lock(cfg)
    try:
        assert cont.run_continuous(cfg, install_signals=False) == 1
    finally:
        runmod.release_lock(cfg)


def test_lock_released_after_run(cfg, monkeypatch):
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    assert _run(cfg) == 0
    assert not cfg.lock_dir.exists()


# ------------------------------------------------- r-loop 1 coverage adds

def test_downloading_row_resumes(cfg, ledger, monkeypatch):
    """kill -9 mid-download leaves DOWNLOADING; D must re-pick it (rclone
    is idempotent) — the orphan class from review iteration 1."""
    _seed(ledger, SID1, state="DOWNLOADING")
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    _fake_download(monkeypatch)
    _script_validate(monkeypatch, {SID1: ["READY"]})
    _fake_deliver(monkeypatch)
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(SID1)["state"] == "DELIVERED"
    finally:
        led.close()


def test_hold_picked_before_ingested(cfg, ledger, monkeypatch):
    """Ruling 6: a cooldown-expired HOLD_VLM session outranks fresh
    intake — a steady INGESTED stream must not starve it."""
    _seed(ledger, SID1, state="HOLD_VLM")
    _seed(ledger, SID2, state="INGESTED",
          ctime="2026-08-14T09:00:00.000Z")   # earlier ctime, tier lower
    for s in (SID1, SID2):
        (cfg.work / s).mkdir(parents=True)
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    monkeypatch.setattr(C, "CONT_POOL_MIN", 1)
    monkeypatch.setattr(C, "CONT_POOL_MAX", 1)   # serialize runners
    log = []
    _script_validate(monkeypatch, {SID1: ["READY"], SID2: ["READY"]}, log)
    _fake_deliver(monkeypatch)
    assert _run(cfg) == 0
    vals = [e for e in log if e[0] == "val"]
    assert vals and vals[0][1] == SID1


def test_upload_lane_resume_packaged_uploaded(cfg, ledger, monkeypatch):
    """kill during upload leaves PACKAGED/UPLOADED; U re-picks both and
    each delivers exactly once (events oracle)."""
    _seed(ledger, SID1, state="PACKAGED")
    _seed(ledger, SID2, state="UPLOADED")
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    _fake_deliver(monkeypatch)
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        for sid in (SID1, SID2):
            assert led.get(sid)["state"] == "DELIVERED"
            n = led.db.execute(
                "SELECT COUNT(*) n FROM events WHERE session_id=? AND "
                "to_state='DELIVERED'", (sid,)).fetchone()["n"]
            assert n == 1
    finally:
        led.close()


def test_failed_upload_cooldown_keeps_state(cfg, ledger, monkeypatch):
    _seed(ledger, SID1, state="READY")
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    log = []
    _fake_deliver(monkeypatch, outcomes={SID1: ["failed_upload"]}, log=log)
    alerts = []
    monkeypatch.setattr(cont.AlertBook, "alert",
                        lambda self, t: alerts.append(t))
    assert _run(cfg) == 0                  # cooldown blocks -> idle exit
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(SID1)["state"] == "READY"   # retryable, kept
    finally:
        led.close()
    assert [e for e in log if e[0] == "up"] == [("up", SID1,
                                                 "failed_upload")]
    assert any("upload failed" in a for a in alerts)


def test_lane_survives_iteration_exception(cfg, ledger, monkeypatch):
    """The r-loop 1 blocker: one escaping exception must never kill a
    lane — the guard alerts, reopens the ledger, and the lane continues."""
    _seed(ledger, SID1, state="READY")
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    _fake_deliver(monkeypatch)
    calls = {"n": 0}
    orig = cont.ContinuousDriver._pick_upload

    def flaky(self, led):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient ledger hiccup")
        return orig(self, led)
    monkeypatch.setattr(cont.ContinuousDriver, "_pick_upload", flaky)
    alerts = []
    monkeypatch.setattr(cont.AlertBook, "alert",
                        lambda self, t: alerts.append(t))
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(SID1)["state"] == "DELIVERED"
    finally:
        led.close()
    assert any("upload lane iteration failed" in a for a in alerts)


def test_rung_injection_and_true_climb_absorb(cfg, ledger, monkeypatch):
    """Design §4: jobs carry the driver's current rung + pressure path;
    only reported > INJECTED counts as a climb (a worker echoes
    max(injected, climbed), so echoes must never re-pin after a reset)."""
    _seed(ledger, SID1, state="INGESTED")
    (cfg.work / SID1).mkdir(parents=True)
    jobs = []

    def climb_worker(job):
        jobs.append(job)
        return {"sid": job["sid"], "bin": 1, "hold_vlm": False,
                "reasons": [], "advisories": [], "engine_verdict": "ok",
                "vlm_rung": 2, "vlm_fallback": True}
    monkeypatch.setattr(cont, "_POOL_DISABLED", True)
    monkeypatch.setattr(cont, "_WORKER_FN", climb_worker)
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    assert drv._validate_one(ledger, SID1, ledger.get(SID1)) == "READY"
    assert jobs[0]["vlm_rung"] == 0
    assert jobs[0]["pressure_path"].endswith("vlm-pressure.jsonl")
    assert drv.current_rung() == 2          # true climb 0->2 absorbed
    # echo case: injected at rung 2, quiet reset lands mid-flight, worker
    # echoes 2 -> must NOT resurrect the rung
    _seed(ledger, SID2, state="INGESTED")
    (cfg.work / SID2).mkdir(parents=True)

    def echo_worker(job):
        with drv._rung_lock:
            drv._rung = 0                   # simulate H's quiet reset
        return {"sid": job["sid"], "bin": 1, "hold_vlm": False,
                "reasons": [], "advisories": [], "engine_verdict": "ok",
                "vlm_rung": job["vlm_rung"], "vlm_fallback": False}
    monkeypatch.setattr(cont, "_WORKER_FN", echo_worker)
    assert drv._validate_one(ledger, SID2, ledger.get(SID2)) == "READY"
    assert drv.current_rung() == 0          # echo did not re-pin


def test_autoscale_tick_backpressure_moves_gate(cfg, monkeypatch):
    monkeypatch.setattr(C, "CONT_POOL_MIN", 8)
    monkeypatch.setattr(C, "CONT_POOL_MAX", 44)   # band is host-dependent
    now = [50_000.0]
    drv = cont.ContinuousDriver(cfg, clocks=cont._Clocks(now=lambda: now[0]))
    drv.gate.set_target(14)
    drv._counts = {"INGESTED": 30}
    p = drv.pressure_path
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        for i in range(15):
            f.write(json.dumps({"ts": now[0] - i * 10, "status": 429,
                                "rung": 0, "tag": "genlang r0"}) + "\n")
    drv._autoscale_tick()
    assert drv.gate.target == 14 - C.CONT_STEP_DOWN


def test_batches_table_left_byte_identical(cfg, ledger, monkeypatch):
    """Design §3: leftover open batch rows are the dormant batch driver's
    ROLLBACK state — the continuous driver must not close or mutate them."""
    bno = ledger.start_batch(sessions=["ghost-member-sid"])
    before = dict(ledger.db.execute(
        "SELECT * FROM batches WHERE batch_no=?", (bno,)).fetchone())
    _seed(ledger, SID1, state="READY")
    monkeypatch.setattr(ingest, "list_drive", lambda _cfg: [])
    _fake_deliver(monkeypatch)
    assert _run(cfg) == 0
    led = Ledger(cfg.ledger_path)
    try:
        after = dict(led.db.execute(
            "SELECT * FROM batches WHERE batch_no=?", (bno,)).fetchone())
    finally:
        led.close()
    assert after == before and after["finished"] is None


# ------------------------------------------------------------- CLI smoke

def test_run_continuous_cli_smoke(tmp_path):
    """REAL `python -m pipeline run-continuous` (plan §6: pytest-context
    pools cannot see __main__ re-import failures under spawn). Two seeded
    garbage sessions ride the REAL spawn worker path. PATH is stripped so
    rclone/ffmpeg are absent: the scan degrades to an alert (never fatal)
    and fixes fail cleanly into the R2 budget -> REJECTED. HOME is
    redirected so the real secrets.env can never be read (no Telegram)."""
    home = tmp_path / "hl-pipeline"
    cfg = C.Config(home=home)
    cfg.ensure_dirs()
    led = Ledger(cfg.ledger_path)
    for sid in (SID1, SID2):
        _seed(led, sid, state="INGESTED")
        (cfg.work / sid).mkdir(parents=True, exist_ok=True)
        (cfg.work / sid / "junk.bin").write_bytes(b"garbage")
    led.close()
    env = {**os.environ,
           "HL_PIPELINE_HOME": str(home),
           "HOME": str(tmp_path),          # no real secrets/telegram
           "PATH": "/usr/bin:/bin",        # no rclone, no ffmpeg
           "HL_PIPELINE_TEST_MODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "pipeline", "run-continuous",
         "--until-idle", "--quiet"],
        env=env, cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    led = Ledger(cfg.ledger_path)
    try:
        for sid in (SID1, SID2):
            row = led.get(sid)
            # garbage payload -> fixable verdict -> fix attempts burn ->
            # rejected; any terminal-or-hold state proves the real spawn
            # worker ran and the driver routed its verdicts
            assert row["state"] in ("REJECTED", "HOLD_VLM", "QUARANTINED"), \
                (sid, row["state"], proc.stdout[-2000:])
            n = led.db.execute(
                "SELECT COUNT(*) n FROM events WHERE session_id=?",
                (sid,)).fetchone()["n"]
            assert n > 1
    finally:
        led.close()
    assert not cfg.lock_dir.exists()
