"""Orchestrator tests: lock, batch flow, fix loop, split children,
state-machine resume after an interrupted batch, daily report gating.
Heavy phases (download / engine validation / rclone) are stubbed — their
own tests cover them."""
import json
from datetime import datetime

from pipeline import config as C
from pipeline import deliver, ingest, run as runmod
from pipeline.tests.conftest import make_session_entries
import pytest


@pytest.fixture(autouse=True)
def _arm_the_batch_driver(monkeypatch):
    """run() now declines when PIPELINE_CONTINUOUS is True (r-loop 5): the
    flag used to be a ONE-WAY interlock that stopped the continuous unit
    when False but never stopped the batch driver when True, so a
    roll-forward could leave both armed and let a batch tick take over
    production. These tests exercise the (dormant) batch driver itself, so
    they arm it explicitly."""
    monkeypatch.setattr(C, "PIPELINE_CONTINUOUS", False)


SID1 = "2026-08-14T10-00-00Z_kamla_c_0123456789abcdef"
SID2 = "2026-08-14T11-00-00Z_kamla_c_fedcba9876543210"


def test_lock_exclusive_and_stale_reclaim(cfg):
    assert runmod.acquire_lock(cfg)
    assert not runmod.acquire_lock(cfg)          # held by a live pid (us)
    (cfg.lock_dir / "pid").write_text("999999")  # dead pid -> stale
    assert runmod.acquire_lock(cfg)
    runmod.release_lock(cfg)


def _seed(cfg, ledger, sid=SID1, md5="m1", ctime="2026-08-14T10:00:00.000Z"):
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=sid, md5=md5, ctime=ctime))


def _stub_phases(monkeypatch, verdicts):
    """verdicts: sid -> list of result dicts, consumed per validation."""
    def fake_download(cfg, ledger, sid):
        w = cfg.work / sid
        w.mkdir(parents=True, exist_ok=True)
        (w / "frames.csv").write_text("x")
        (w / "session.json").write_text('{"game_title": "Kamla"}')
        ledger.set_state(sid, "INGESTED", "payload=v2")
        return "v2"

    def fake_validate_phase(cfg, ledger, sids, alerts, workers):
        for sid in sids:
            row = ledger.get(sid)
            if row["state"] not in ("INGESTED", "VALIDATING", "HOLD_VLM",
                                    "REVALIDATING"):
                continue
            r = verdicts[sid].pop(0)
            ledger.set_reasons(sid, r.get("reasons", []), r.get("bin"))
            if r.get("hold"):
                ledger.set_state(sid, "HOLD_VLM")
            elif r["bin"] == 1:
                ledger.set_state(sid, "READY")
            elif r["bin"] == 2:
                ledger.set_state(sid, "FIX_QUEUED")
            else:
                ledger.set_state(sid, "REJECTED")

    def fake_deliver(cfg, ledger, sid, dest_prefix=C.VENDOR):
        ledger.set_state(sid, "PACKAGED")
        ledger.set_state(sid, "UPLOADED")
        ledger.update(sid, duration_delivered_s=3600.0,
                      delivered_at="2026-08-14T12:00:00+00:00")
        ledger.set_state(sid, "DELIVERED")
        return deliver.DeliveryOutcome(sid, "delivered", hours=1.0)

    monkeypatch.setattr(ingest, "download", fake_download)
    monkeypatch.setattr(runmod, "_validate_phase",
                        lambda cfg, ledger, sids, alerts, workers:
                        fake_validate_phase(cfg, ledger, sids, alerts,
                                            workers))
    monkeypatch.setattr(deliver, "deliver_session", fake_deliver)


def test_process_batch_deliver_and_reject(cfg, ledger, monkeypatch):
    # one scan, both folders listed: sequential single-folder scans left
    # SID1 absent from the second (healthy) listing, which the r14 #5
    # vanished-folder arm rightly prunes — a partial-listing artifact,
    # not the production shape (scan always sees the full Drive listing)
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=SID1, md5="m1")
        + make_session_entries(sid=SID2, md5="m2",
                               ctime="2026-08-14T11:00:00.000Z"))
    _stub_phases(monkeypatch, {
        SID1: [{"bin": 1}],
        SID2: [{"bin": 3, "reasons": [
            {"code": "CNT_SHORT", "blocking": True, "fixable": False,
             "params": {}, "evidence": "50s"}]}]})
    b = runmod.process_batch(cfg, ledger, [SID1, SID2], alerts=[])
    assert b.delivered == 1 and b.rejected == 1
    assert b.reject_labels == ["<70s"]
    assert ledger.get(SID1)["state"] == "DELIVERED"
    assert ledger.get(SID2)["state"] == "REJECTED"
    assert (cfg.dossiers / SID2 / "coaching.md").exists()
    assert b.hours_kamla == 1.0 and b.hours_delta == 1.0


def test_fix_loop_fixes_then_delivers(cfg, ledger, monkeypatch):
    _seed(cfg, ledger)
    _stub_phases(monkeypatch, {SID1: [
        {"bin": 2, "reasons": [{"code": "INP_OSKEYS", "blocking": True,
                                "fixable": True, "params": {},
                                "evidence": ""}]},
        {"bin": 1}]})
    monkeypatch.setattr(runmod.fix, "apply_fixes",
                        lambda *a, **k: {"applied": [], "children": None,
                                         "error": None})
    b = runmod.process_batch(cfg, ledger, [SID1], alerts=[])
    row = ledger.get(SID1)
    assert row["state"] == "DELIVERED"
    assert row["fix_attempts"] == 1
    assert b.auto_fixed == 1 and b.delivered == 1


def test_fix_retries_exhausted_rejects(cfg, ledger, monkeypatch):
    _seed(cfg, ledger)
    bad = {"bin": 2, "reasons": [{"code": "INP_OSKEYS", "blocking": True,
                                  "fixable": True, "params": {},
                                  "evidence": ""}]}
    _stub_phases(monkeypatch, {SID1: [dict(bad), dict(bad), dict(bad)]})
    monkeypatch.setattr(runmod.fix, "apply_fixes",
                        lambda *a, **k: {"applied": [], "children": None,
                                         "error": None})
    runmod.process_batch(cfg, ledger, [SID1], alerts=[])
    row = ledger.get(SID1)
    assert row["state"] == "REJECTED"
    assert row["fix_attempts"] == C.FIX_RETRIES


def test_split_children_enter_and_deliver(cfg, ledger, monkeypatch):
    _seed(cfg, ledger)
    kids = {"segments": [
        {"id": SID1 + "-p1", "dir": "x", "t0": 0.0, "t1": 100.0,
         "duration_s": 100.0, "frames": 1000},
        {"id": SID1 + "-p2", "dir": "y", "t0": 105.0, "t1": 300.0,
         "duration_s": 195.0, "frames": 1950}], "dropped": []}
    _stub_phases(monkeypatch, {
        SID1: [{"bin": 2, "reasons": [
            {"code": "CNT_MID_NONGAMEPLAY", "blocking": True,
             "fixable": True, "params": {"cut": [100.0, 105.0]},
             "evidence": ""}]}],
        SID1 + "-p1": [{"bin": 1}],
        SID1 + "-p2": [{"bin": 1}]})
    monkeypatch.setattr(runmod.fix, "apply_fixes",
                        lambda *a, **k: {"applied": [], "children": kids,
                                         "error": None})
    b = runmod.process_batch(cfg, ledger, [SID1], alerts=[])
    assert ledger.get(SID1)["state"] == "SPLIT"
    assert ledger.get(SID1 + "-p1")["state"] == "DELIVERED"
    assert ledger.get(SID1 + "-p2")["parent_id"] == SID1
    assert b.delivered == 2


def test_resume_interrupted_batch_no_double_count(cfg, ledger, monkeypatch):
    """Kill-between-phases simulation: first run stops after validation
    (states READY); the next process_batch call picks the session up
    without re-downloading and delivers exactly once."""
    _seed(cfg, ledger)
    _stub_phases(monkeypatch, {SID1: [{"bin": 1}]})
    # crash before delivery: run only download+validate
    runmod._download_phase(cfg, ledger, [SID1], [])
    runmod._validate_phase(cfg, ledger, [SID1], [], workers=1)
    assert ledger.get(SID1)["state"] == "READY"
    # new run resumes from ledger state alone
    b = runmod.process_batch(cfg, ledger, [SID1], alerts=[])
    assert b.delivered == 1
    assert ledger.get(SID1)["duration_delivered_s"] == 3600.0
    ev = ledger.db.execute(
        "SELECT COUNT(*) n FROM events WHERE session_id=? AND to_state="
        "'DELIVERED'", (SID1,)).fetchone()["n"]
    assert ev == 1


def test_run_picks_up_resumable_before_new(cfg, monkeypatch):
    from pipeline.ledger import Ledger
    led = Ledger(cfg.ledger_path)
    led.insert_session(session_id="old", game="kamla",
                       operator_email="o@x.com", player_email="p@x.com",
                       drive_path="kamla/o/p/old", drive_ctime="2026",
                       md5_video="m", bytes_=1, state="PACKAGED")
    led.close()
    seen = []
    monkeypatch.setattr(runmod.ingest, "scan",
                        lambda cfg, ledger: ingest.ScanResult())
    monkeypatch.setattr(runmod, "process_batch",
                        lambda cfg, ledger, sids, alerts, dest_prefix:
                        seen.append(list(sids)) or __import__(
                            "pipeline.reports", fromlist=["BatchStats"]
                        ).BatchStats(batch_no=1,
                                     finished_ist=datetime.now(C.IST),
                                     duration_min=1, delivered=0, total=0,
                                     auto_fixed=0, rejected=0))
    monkeypatch.setattr(runmod, "send_daily_report_if_due",
                        lambda cfg, ledger: False)
    runmod.run(cfg, max_batches=1, send_telegram=False)
    assert seen and seen[0] == ["old"]


def test_hold_vlm_retried_not_passed(cfg, ledger, monkeypatch):
    _seed(cfg, ledger)
    _stub_phases(monkeypatch, {SID1: [{"bin": None, "hold": True}]})
    b = runmod.process_batch(cfg, ledger, [SID1], alerts=[])
    assert ledger.get(SID1)["state"] == "HOLD_VLM"
    assert b.delivered == 0 and b.rejected == 0


def test_daily_report_gating(cfg, ledger, monkeypatch):
    sent = []
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda cfg, text: sent.append(text))
    monkeypatch.setattr(runmod.telegram, "send_document",
                        lambda cfg, p, caption="": sent.append(p.name))
    before = datetime(2026, 8, 17, 9, 0, tzinfo=C.IST)
    assert not runmod.send_daily_report_if_due(cfg, ledger, before)
    after = datetime(2026, 8, 17, 14, 5, tzinfo=C.IST)
    assert runmod.send_daily_report_if_due(cfg, ledger, after)
    assert len(sent) == 2 and sent[0].startswith("💰 daily — Aug 17")
    # marker prevents a resend
    assert not runmod.send_daily_report_if_due(cfg, ledger, after)


def test_download_pauses_below_low_water(cfg, ledger, monkeypatch):
    _seed(cfg, ledger)
    monkeypatch.setattr(runmod.deliver, "disk_free_gb", lambda p: 50.0)
    alerts = []
    monkeypatch.setattr(runmod, "_alert",
                        lambda cfg, text, sent: alerts.append(text))
    runmod._download_phase(cfg, ledger, [SID1], alerts)
    assert ledger.get(SID1)["state"] == "DISCOVERED"   # untouched
    assert any("downloads paused" in a for a in alerts)


def test_fixing_state_resumes_via_revalidation(cfg, ledger, monkeypatch):
    """Review finding #6: a mid-fix crash must NOT re-run the stale plan
    (RETRIM would trim twice) — it re-validates and re-derives."""
    _seed(cfg, ledger)
    _stub_phases(monkeypatch, {SID1: [{"bin": 1}]})
    ledger.set_state(SID1, "FIX_QUEUED")
    ledger.update(SID1, fix_attempts=1)
    ledger.set_state(SID1, "FIXING", "attempt 1")
    applied = []
    monkeypatch.setattr(runmod.fix, "apply_fixes",
                        lambda *a, **k: applied.append(1))
    b = runmod.process_batch(cfg, ledger, [SID1], alerts=[])
    assert applied == []                     # stale plan never re-ran
    assert ledger.get(SID1)["state"] == "DELIVERED"
    assert ledger.get(SID1)["fix_attempts"] == 1   # no extra attempt burned


def test_fix_error_goes_to_revalidation_not_stale_requeue(cfg, ledger,
                                                          monkeypatch):
    _seed(cfg, ledger)
    _stub_phases(monkeypatch, {SID1: [
        {"bin": 2, "reasons": [{"code": "INP_OSKEYS", "blocking": True,
                                "fixable": True, "params": {},
                                "evidence": ""}]},
        {"bin": 1}]})
    monkeypatch.setattr(runmod.fix, "apply_fixes",
                        lambda *a, **k: {"applied": [], "children": None,
                                         "error": "FIX_KEY_HYGIENE: boom"})
    b = runmod.process_batch(cfg, ledger, [SID1], alerts=[])
    # the error routed through REVALIDATING; second verdict bin 1 delivers
    assert ledger.get(SID1)["state"] == "DELIVERED"


def test_split_with_no_survivors_rejects(cfg, ledger, monkeypatch):
    _seed(cfg, ledger)
    _stub_phases(monkeypatch, {SID1: [{"bin": 2, "reasons": [
        {"code": "CNT_MID_NONGAMEPLAY", "blocking": True, "fixable": True,
         "params": {"cut": [10.0, 100.0]}, "evidence": ""}]}]})
    monkeypatch.setattr(
        runmod.fix, "apply_fixes",
        lambda *a, **k: {"applied": [], "error": None,
                         "children": {"segments": [], "dropped": [
                             {"t0": 0, "t1": 10, "why": "under minimum"}]}})
    b = runmod.process_batch(cfg, ledger, [SID1], alerts=[])
    assert ledger.get(SID1)["state"] == "REJECTED"
    assert b.rejected == 1


def test_delivery_crash_quarantines_with_alert(cfg, ledger, monkeypatch):
    _seed(cfg, ledger)
    _stub_phases(monkeypatch, {SID1: [{"bin": 1}]})

    def boom(cfg_, ledger_, sid, dest_prefix=C.VENDOR):
        raise RuntimeError("rrd generation exploded")

    monkeypatch.setattr(runmod.deliver, "deliver_session", boom)
    alerts = []
    monkeypatch.setattr(runmod, "_alert",
                        lambda cfg_, text, sent: alerts.append(text))
    runmod.process_batch(cfg, ledger, [SID1], alerts=alerts)
    assert ledger.get(SID1)["state"] == "QUARANTINED"
    assert any("delivery crashed" in a for a in alerts)


def test_failed_gate_requeues_with_real_reasons(cfg, ledger, monkeypatch):
    """Review finding #2: gate failures must become reasons, or the fix
    pass plans nothing and wrongfully rejects."""
    _seed(cfg, ledger)
    _stub_phases(monkeypatch, {SID1: [{"bin": 1}]})

    def gate_fail(cfg_, ledger_, sid, dest_prefix=C.VENDOR):
        from pipeline.deliver import DeliveryOutcome
        return DeliveryOutcome(sid, "failed_gate", detail="camera",
                               gate_fails=["FAIL: camera columns non-null "
                                           "in 2 rows (input-only "
                                           "session)"])

    monkeypatch.setattr(runmod.deliver, "deliver_session", gate_fail)
    runmod.process_batch(cfg, ledger, [SID1], alerts=[])
    row = ledger.get(SID1)
    assert row["state"] == "FIX_QUEUED"
    assert "STR_CAMERA_NONNULL" in row["reasons_json"]


def test_gate_requeue_fix_actually_runs_at_attempts_one(cfg, ledger,
                                                        monkeypatch):
    """Review-2 #11: a session that used one fix attempt and then fails
    the final gate must still get its gate-failure fix, not a
    dead-on-arrival budget reject."""
    _seed(cfg, ledger)
    _stub_phases(monkeypatch, {SID1: [
        {"bin": 2, "reasons": [{"code": "INP_OSKEYS", "blocking": True,
                                "fixable": True, "params": {},
                                "evidence": ""}]},
        {"bin": 1},          # after fix 1
        {"bin": 1}]})        # after gate-failure fix
    fixes_ran = []
    monkeypatch.setattr(runmod.fix, "apply_fixes",
                        lambda *a, **k: fixes_ran.append(1) or
                        {"applied": [], "children": None, "error": None})
    gate_state = {"failed_once": False}
    real_deliver = None

    def deliver_once_fails(cfg_, ledger_, sid, dest_prefix=C.VENDOR):
        from pipeline.deliver import DeliveryOutcome
        if not gate_state["failed_once"]:
            gate_state["failed_once"] = True
            return DeliveryOutcome(sid, "failed_gate", detail="camera",
                                   gate_fails=["FAIL: camera columns "
                                               "non-null in 1 rows "
                                               "(input-only session)"])
        ledger_.set_state(sid, "PACKAGED")
        ledger_.set_state(sid, "UPLOADED")
        ledger_.update(sid, duration_delivered_s=100.0,
                       delivered_at="2026-08-14T12:00:00+00:00")
        ledger_.set_state(sid, "DELIVERED")
        from pipeline.deliver import DeliveryOutcome as DO
        return DO(sid, "delivered", hours=0.03)

    monkeypatch.setattr(runmod.deliver, "deliver_session",
                        deliver_once_fails)
    runmod.process_batch(cfg, ledger, [SID1], alerts=[])       # gate fails
    assert ledger.get(SID1)["state"] == "FIX_QUEUED"
    runmod.process_batch(cfg, ledger, [SID1], alerts=[])       # fix + ship
    assert ledger.get(SID1)["state"] == "DELIVERED"
    assert len(fixes_ran) == 2          # the gate-failure fix really ran


def test_run_processes_each_session_once_per_run(cfg, monkeypatch):
    """Review-2 #9: a stuck PACKAGED session must not spin the batch loop
    for the whole run while holding the lock."""
    from pipeline.ledger import Ledger
    led = Ledger(cfg.ledger_path)
    led.insert_session(session_id="stuck", game="kamla",
                       operator_email="o@x.com", player_email="p@x.com",
                       drive_path="kamla/o/p/stuck", drive_ctime="2026",
                       md5_video="m", bytes_=1, state="PACKAGED")
    led.close()
    calls = []
    monkeypatch.setattr(runmod.ingest, "scan",
                        lambda cfg, ledger: ingest.ScanResult())
    monkeypatch.setattr(
        runmod, "process_batch",
        lambda cfg, ledger, sids, alerts, dest_prefix:
        calls.append(list(sids)) or __import__(
            "pipeline.reports", fromlist=["BatchStats"]).BatchStats(
            batch_no=1, finished_ist=datetime.now(C.IST), duration_min=1,
            delivered=0, total=0, auto_fixed=0, rejected=0))
    monkeypatch.setattr(runmod, "send_daily_report_if_due",
                        lambda cfg, ledger: False)
    runmod.run(cfg, max_batches=50, send_telegram=False)
    assert calls == [["stuck"]]          # exactly one pass, not fifty


def test_zip_download_error_stays_retryable(cfg, ledger, monkeypatch):
    _seed(cfg, ledger)

    def bad_zip(cfg_, ledger_, sid):
        ledger_.set_state(sid, "DOWNLOADING")
        raise ingest.DownloadError("unreadable zip payload: BadZipFile",
                                   kind="zip_incomplete")

    monkeypatch.setattr(ingest, "download", bad_zip)
    runmod._download_phase(cfg, ledger, [SID1], [])
    assert ledger.get(SID1)["state"] == "DISCOVERED"     # not QUARANTINED
    assert ledger.incomplete_list()


def test_main_quiet_flag_suppresses_telegram(monkeypatch):
    """--quiet (recal rebuild runs, 08-16): main() wires
    send_telegram=False into run(); the default invocation (what the
    systemd unit does) keeps it True."""
    calls = []
    monkeypatch.setattr(
        runmod, "run",
        lambda cfg, send_telegram=True, **kw:
        calls.append(send_telegram) or 0)
    assert runmod.main(["run", "--quiet"]) == 0
    assert runmod.main(["run"]) == 0
    assert calls == [False, True]
