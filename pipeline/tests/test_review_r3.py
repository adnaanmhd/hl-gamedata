"""Tests added by adversarial-review round 3: dead-U drain, lock
hardening, quarantine healing, zip supersede from QUARANTINED, daily
window anchoring, frozen-window gate params, dedupe-loser finalize,
resume membership, fallback count."""
import json
import threading
from datetime import datetime, timedelta, timezone

from pipeline import config as C
from pipeline import ingest, reports, run as runmod, validate
from pipeline.ledger import Ledger

SID = "2026-08-14T10-00-00Z_kamla_c_00000000000000cc"


def _seed(ledger, sid=SID, state="DISCOVERED", player="p@x.com",
          md5="", ctime="2026-08-14T10:00:00.000Z", path=None):
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email=player,
        drive_path=path or f"kamla/Op/{player}/{sid}",
        drive_ctime=ctime, md5_video=md5, bytes_=1, state=state)


# ---------------------------------------------- dead-U drain (r3 #0)

def test_u_thread_startup_death_does_not_deadlock_run(cfg, monkeypatch):
    """Ledger() failing ONLY in the U thread: the run must still complete
    (drain releases slots), sessions park in READY, next run delivers."""
    monkeypatch.setattr(C, "PIPELINE_OVERLAP", True)
    led = Ledger(cfg.ledger_path)
    for i in range(4):
        _seed(led, f"2026-08-14T1{i}-00-00Z_kamla_c_{i + 96:016x}",
              ctime=f"2026-08-14T1{i}:00:00.000Z")
    led.close()

    real_ledger = runmod.Ledger
    main_thread = threading.main_thread()

    def picky_ledger(path):
        if threading.current_thread().name == "hl-U":
            raise RuntimeError("disk hiccup at U startup")
        return real_ledger(path)
    monkeypatch.setattr(runmod, "Ledger", picky_ledger)
    monkeypatch.setattr(runmod, "_download_phase",
                        lambda cfg_, l, sids, a:
                        [l.set_state(s, "INGESTED") for s in sids])
    monkeypatch.setattr(runmod, "_validate_phase",
                        lambda cfg_, l, sids, a, workers, **kw:
                        [l.set_state(s, "READY") for s in sids
                         if l.get(s)["state"] == "INGESTED"])
    monkeypatch.setattr(runmod, "_fix_phase",
                        lambda cfg_, l, sids, a, workers, **kw: [])
    monkeypatch.setattr(runmod.ingest, "scan",
                        lambda cfg_, l: ingest.ScanResult())
    alerts = []
    monkeypatch.setattr(runmod, "_alert",
                        lambda c, t, s: alerts.append(t))
    assert runmod.run(cfg, send_telegram=False) == 0     # no wedge
    led = Ledger(cfg.ledger_path)
    states = {r["state"] for r in led.by_state("READY")}
    assert states == {"READY"}                            # work preserved
    led.close()
    assert any("upload thread failed to start" in a for a in alerts)


# ------------------------------------------------ lock hardening (r3 #4)

def test_lock_pidless_fresh_lock_yields_not_steals(cfg):
    cfg.lock_dir.mkdir(parents=True)          # winner mid-write, no pid yet
    assert runmod.acquire_lock(cfg) is False  # yields; no steal
    assert cfg.lock_dir.exists()


def test_lock_dead_pid_reclaims(cfg):
    cfg.lock_dir.mkdir(parents=True)
    (cfg.lock_dir / "pid").write_text("999999999")       # not a live pid
    assert runmod.acquire_lock(cfg) is True
    assert int((cfg.lock_dir / "pid").read_text()) > 0


# ---------------------------------------- quarantine healing (r3 #7)

def _entries(sid, op="Op", player="p@x.com", md5="mm"):
    base = f"kamla/{op}/{player}/{sid}"
    return [{"Path": f"{base}/{n}", "Name": n, "IsDir": False, "Size": 5,
             "ModTime": "2026-08-15T12:00:00.000Z", "Hashes": {"md5": md5}}
            for n in C.REQUIRED_FILES]


def test_quarantined_path_heals_on_clean_reupload(cfg, ledger):
    """A session first seen at a malformed path (QUARANTINED) re-registers
    as DISCOVERED when it reappears parsing clean at its proper path."""
    _seed(ledger, SID, state="QUARANTINED",
          path=f"kamla/badpath/{SID}")
    res = ingest.scan(cfg, ledger, entries=_entries(SID))
    assert ledger.get(SID)["state"] == "DISCOVERED"
    assert ledger.get(SID)["drive_path"] == f"kamla/Op/p@x.com/{SID}"
    assert any("healed" in f for f in res.integrity_flags)


def test_active_session_id_collision_still_ignored(cfg, ledger):
    _seed(ledger, SID, state="VALIDATING", path=f"kamla/Op/x@y.com/{SID}")
    ingest.scan(cfg, ledger, entries=_entries(SID))
    assert ledger.get(SID)["state"] == "VALIDATING"      # untouched


# ------------------------------------- zip supersede from QUARANTINED

def test_quarantined_zip_slot_supersedes_on_reupload(cfg, ledger):
    zsid = "2026-08-14T10-00-00Z_kamla_c_0000000000000f11"
    base = f"kamla/Op/p@x.com/{zsid}"
    _seed(ledger, zsid, state="QUARANTINED", path=base)
    entries = [{"Path": f"{base}/session-001.zip", "Name": "session-001.zip",
                "IsDir": False, "Size": 777,
                "ModTime": "2026-08-15T09:00:00.000Z", "Hashes": {}}]
    res = ingest.scan(cfg, ledger, entries=entries)
    assert zsid in res.superseded
    assert ledger.get(zsid)["state"] == "DISCOVERED"


# --------------------------------------- daily window anchor (r3 #24)

def test_daily_window_contiguous_via_persisted_anchor(cfg, ledger,
                                                      monkeypatch):
    """Consecutive reports form contiguous windows: window 2 starts at
    send 1, regardless of send-time drift."""
    sent_bounds = []
    real_sheet = reports.write_payment_sheet

    def spy_sheet(cfg_, ledger_, day, bounds=None, **kw):
        sent_bounds.append(bounds)
        return real_sheet(cfg_, ledger_, day, bounds, **kw)
    monkeypatch.setattr(runmod.reports, "write_payment_sheet", spy_sheet)
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda cfg_, text: None)
    monkeypatch.setattr(runmod.telegram, "send_document",
                        lambda cfg_, path, caption="": None)
    day1 = datetime.now(C.IST).replace(hour=14, minute=29)
    assert runmod.send_daily_report_if_due(cfg, ledger, day1) is True
    day2 = (day1 + timedelta(days=1)).replace(minute=1)
    assert runmod.send_daily_report_if_due(cfg, ledger, day2) is True
    (lo1, hi1), (lo2, hi2) = sent_bounds
    assert lo2 == hi1                       # contiguous: no gap, no overlap
    # the window edge sits REPORT_OFFSET_H before send time (Adnaan 08-15)
    from datetime import datetime as _dt
    expect_hi1 = (day1.astimezone(runmod.timezone.utc)
                  - timedelta(hours=C.REPORT_OFFSET_H)
                  ).isoformat(timespec="seconds")
    assert hi1 == expect_hi1


# ------------------------------- frozen-window gate params (r3 #3)

def test_engine_window_gate_params_cover_full_vlm_window():
    """INP_FROZEN_ACTIONS must gate the FULL flagged window — the span its
    trigger counted actions over — not just the refined frozen span."""
    dur = 1200.0
    rep = {"duration_s": dur, "vlm": {"windows": [{
        "t0": 100.0, "t1": 106.0, "gating": True, "tier": "high",
        "labels": ["loading"], "stillness_ratio": 0.1,
        "inputs": {"action_frames": 7}}]}}
    aux = {"refined": {(100.0, 106.0): (102.0, 103.5)}}
    reasons, advisories = [], []
    validate._map_windows(rep, aux, reasons, advisories)
    frozen = [r for r in reasons if r["code"] == "INP_FROZEN_ACTIONS"]
    assert len(frozen) == 1
    p = frozen[0]["params"]
    assert p["t0"] <= 100.0 and p["t1"] >= 106.0       # full window gated


# ------------------------------ dedupe loser finalize + F3 note (r3 #29)

def test_download_zip_dedupe_loser_gets_dossier(cfg, ledger, monkeypatch):
    keeper = SID.replace("cc", "ee")
    _seed(ledger, keeper, state="VALIDATING", md5="zipmd5",
          player="other@z.com", ctime="2026-08-14T09:00:00.000Z")
    _seed(ledger, SID, state="DISCOVERED", md5="",
          ctime="2026-08-14T10:00:00.000Z")
    work = cfg.work / SID
    work.mkdir(parents=True)
    for n in C.REQUIRED_FILES:
        (work / n).write_text("{}" if n.endswith(".json") else "x")
    monkeypatch.setattr(ingest, "run_rclone",
                        lambda args, timeout_s=None:
                        type("P", (), {"returncode": 0, "stderr": ""})())
    monkeypatch.setattr(ingest, "_md5_file", lambda p: "zipmd5")
    out = ingest.download(cfg, ledger, SID)
    assert out == "duplicate"
    row = ledger.get(SID)
    assert row["state"] == "REJECTED"
    assert row["dossier_path"]                          # finalized
    assert (cfg.dossiers / SID / "coaching.md").exists()


# ------------------------------------ resume keeps full membership

def test_partition_resume_keeps_delivered_members(cfg):
    led = Ledger(cfg.ledger_path)
    _seed(led, "d1", state="DISCOVERED")
    led.set_state("d1", "INGESTED")
    _seed(led, "d2", state="DISCOVERED")
    for st in ("INGESTED", "VALIDATING", "READY", "PACKAGED", "UPLOADED"):
        led.set_state("d2", st)
    led.update("d2", duration_delivered_s=100.0,
               delivered_at="2026-08-15T10:00:00+00:00")
    led.set_state("d2", "DELIVERED")
    b = led.start_batch(sessions=["d1", "d2"])
    d_q, v_q, u_q = runmod._partition_resume(led)
    all_batches = d_q + v_q + u_q
    mine = next(x for x in all_batches if x.no == b)
    assert sorted(mine.sids) == ["d1", "d2"]     # DELIVERED member kept
    led.close()


# ------------------------------------------------ fallback count (r2 #32)

def test_batch_fallback_count_reads_dossiers(cfg):
    d = cfg.dossiers / "s-fall"
    d.mkdir(parents=True)
    (d / "verdict.json").write_text(json.dumps({
        "metrics": {"models_used": [
            {"rung": 1, "key": "current", "model": "gemini-3.5-flash",
             "endpoint": "genlang"}]}}))
    d0 = cfg.dossiers / "s-top"
    d0.mkdir(parents=True)
    (d0 / "verdict.json").write_text(json.dumps({
        "metrics": {"models_used": [
            {"rung": 0, "key": "current", "model": "gemini-3.7-flash",
             "endpoint": "genlang"}]}}))
    assert runmod._batch_fallback_count(
        cfg, ["s-fall", "s-top", "s-missing"]) == 1
