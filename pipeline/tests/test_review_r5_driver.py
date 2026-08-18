"""Tests added by adversarial-review round 5 (overlap driver + daily-report
wiring): guaranteed HOLD_VLM batch under competing intake, the
stamps->anchor->marker send sequence + counted-set threading + the
kill-before-marker resend, the host-level transient download arm, and
_partition_resume's carry exclusions (DISCOVERED/HOLD_VLM, already-reported
terminals)."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline import config as C
from pipeline import ingest, reports, run as runmod
from pipeline.ledger import Ledger
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


HOLD_SID = "2026-08-14T09-00-00Z_kamla_c_00000000000000aa"
INTAKE = [f"2026-08-14T1{i}-00-00Z_kamla_c_{0xf0 + i:016x}"
          for i in range(C.BATCH_SIZE)]


def _seed(ledger, sid, state="DISCOVERED",
          ctime="2026-08-14T10:00:00.000Z"):
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path=f"kamla/Op/p@x.com/{sid}",
        drive_ctime=ctime, md5_video=sid[-4:], bytes_=1, state=state)


def _driver_fakes(monkeypatch):
    """Instant stage fakes (test_overlap's pattern, no timeline): D moves
    DISCOVERED->INGESTED, V ->READY (HOLD_VLM included), U ->DELIVERED."""
    def fake_download(cfg_, ledger_, sids, alerts):
        for sid in sids:
            if ledger_.get(sid)["state"] in ("DISCOVERED", "DOWNLOADING"):
                ledger_.set_state(sid, "INGESTED")

    def fake_validate(cfg_, ledger_, sids, alerts, workers, **kw):
        for sid in sids:
            if ledger_.get(sid)["state"] in ("INGESTED", "VALIDATING",
                                             "HOLD_VLM", "REVALIDATING"):
                ledger_.set_state(sid, "READY")

    def fake_deliver(cfg_, ledger_, sids, alerts, dest_prefix=C.VENDOR):
        n = 0
        for sid in sids:
            if ledger_.get(sid)["state"] != "READY":
                continue
            ledger_.set_state(sid, "PACKAGED")
            ledger_.set_state(sid, "UPLOADED")
            ledger_.update(sid, duration_delivered_s=360.0,
                           delivered_at="2026-08-15T12:00:00+00:00")
            ledger_.set_state(sid, "DELIVERED")
            n += 1
        return {"delivered": n, "hours": n * 0.1, "upload_failures": 0}

    monkeypatch.setattr(runmod, "_download_phase", fake_download)
    monkeypatch.setattr(runmod, "_validate_phase", fake_validate)
    monkeypatch.setattr(runmod, "_fix_phase",
                        lambda cfg_, ledger_, sids, alerts, workers, **kw: [])
    monkeypatch.setattr(runmod, "_deliver_phase", fake_deliver)
    monkeypatch.setattr(runmod.ingest, "scan",
                        lambda cfg_, ledger_: ingest.ScanResult())
    monkeypatch.setattr(runmod.deliver, "disk_free_gb", lambda p: 500.0)


# ------------------- HOLD_VLM guaranteed batch vs fresh intake (r5 #15)

def test_hold_vlm_guaranteed_batch_beats_competing_intake(cfg, monkeypatch):
    """r5 #15: the guaranteed pre-intake HOLD_VLM batch (r4 #9) must fire
    even when a FULL batch of fresh DISCOVERED intake competes for the
    run's only new-batch slot (max_batches=1). The idle-only fallback
    cannot save it here — next_batch is never empty — so moving the hold
    block back behind the intake loop starves the held session for as
    long as uploads keep arriving."""
    monkeypatch.setattr(C, "PIPELINE_OVERLAP", True)
    led = Ledger(cfg.ledger_path)
    _seed(led, HOLD_SID, ctime="2026-08-14T09:00:00.000Z")
    led.set_state(HOLD_SID, "INGESTED")
    led.set_state(HOLD_SID, "HOLD_VLM", "sweep failed last run")
    for i, sid in enumerate(INTAKE):
        _seed(led, sid, ctime=f"2026-08-14T10:{i:02d}:00.000Z")
    led.close()
    _driver_fakes(monkeypatch)
    assert runmod.run(cfg, max_batches=1, send_telegram=False) == 0
    led = Ledger(cfg.ledger_path)
    assert led.get(HOLD_SID)["state"] == "DELIVERED"   # retried THIS run
    # the intake really competed: its full batch also ran this run
    assert all(led.get(s)["state"] == "DELIVERED" for s in INTAKE)
    assert led.open_batches() == []                    # both batches closed
    led.close()


# ------------------- daily-send wiring: order, counted set, kill (r5 #16)

DROOT = "2026-08-15T05-00-00Z_kamla_c_00000000000000d1"
DUNPROBED = "2026-08-15T04-00-00Z_kamla_c_00000000000000d2"


def _send_time(hour=14):
    return datetime.now(C.IST).replace(hour=hour, minute=7, second=3,
                                       microsecond=0)


def _window_hi(send):
    return send.astimezone(timezone.utc) - timedelta(hours=C.REPORT_OFFSET_H)


def _mk_delivered_root(led, sid, up_dt):
    """A countable root DELIVERED inside the window ending at up_dt+2h."""
    _seed(led, sid, ctime=up_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    led.update(sid, duration_raw_s=3600.0, duration_delivered_s=3600.0,
               delivered_at=(up_dt + timedelta(hours=1))
               .isoformat(timespec="seconds"))
    led.set_state(sid, "DELIVERED")


def _stub_telegram(monkeypatch):
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda cfg_, text: None)
    monkeypatch.setattr(runmod.telegram, "send_document",
                        lambda cfg_, path, caption="": None)


def test_daily_send_order_stamps_then_anchor_then_marker(cfg, ledger,
                                                         monkeypatch):
    """r5 #16(a), pinning uncommitted fix 1 (r5 #39, BLOCKER): the send
    sequence is stamps -> anchor -> marker. Anchor-before-stamps left a
    kill window where the next tick opened the NEXT window and every
    unstamped root of the just-reported one re-entered as a late arrival
    — a full window of payment hours counted twice."""
    send = _send_time()
    hi_dt = _window_hi(send)
    _mk_delivered_root(ledger, DROOT, hi_dt - timedelta(hours=2))
    _stub_telegram(monkeypatch)
    anchor = cfg.reports_dir / ".last_daily_sent"
    marker = cfg.reports_dir / send.strftime("%Y-%m-%d") / ".sent"
    events = []
    real_mark = reports.mark_uploads_reported

    def spy_mark(led_, lo, hi, sids=None):
        events.append(("stamps", anchor.exists(), marker.exists()))
        return real_mark(led_, lo, hi, sids=sids)
    monkeypatch.setattr(reports, "mark_uploads_reported", spy_mark)
    real_touch = Path.touch

    def spy_touch(self, *a, **k):
        events.append((f"touch:{self.name}", anchor.exists(),
                       marker.exists()))
        return real_touch(self, *a, **k)
    monkeypatch.setattr(Path, "touch", spy_touch)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    # stamps saw NEITHER anchor nor marker; the marker touch (the only
    # touch, and the last write) saw the anchor already on disk
    assert events == [("stamps", False, False), ("touch:.sent", True, False)]
    assert anchor.read_text() == hi_dt.isoformat(timespec="seconds")
    assert ledger.get(DROOT)["uploaded_reported_at"]


def test_daily_send_stamps_exactly_the_counted_roots(cfg, ledger,
                                                     monkeypatch):
    """r5 #16(b), pinning uncommitted fix 2 (r5 #3): the stamp call gets
    build_sheet_rows' counted_out — never sids=None (the re-derive raced
    D: a root probed between generation and stamping got stamped without
    ever being counted, its hours gone from every sheet)."""
    send = _send_time()
    hi_dt = _window_hi(send)
    _mk_delivered_root(ledger, DROOT, hi_dt - timedelta(hours=2))
    # in-window but NOT countable at generation (still awaiting download):
    # the sheet skips it, so the stamp must too — the late guard picks its
    # hours up once probed
    _seed(ledger, DUNPROBED,
          ctime=(hi_dt - timedelta(hours=3))
          .strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    _stub_telegram(monkeypatch)
    calls = []
    real_mark = reports.mark_uploads_reported

    def spy_mark(led_, lo, hi, sids=None):
        calls.append(sids if sids is None else list(sids))
        return real_mark(led_, lo, hi, sids=sids)
    monkeypatch.setattr(reports, "mark_uploads_reported", spy_mark)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    assert calls == [[DROOT]]              # exactly the counted roots
    assert ledger.get(DROOT)["uploaded_reported_at"]
    assert ledger.get(DUNPROBED)["uploaded_reported_at"] is None


def test_daily_resend_after_kill_before_marker_no_double_count(cfg, ledger,
                                                               monkeypatch):
    """r5 #16(c), REWRITTEN by r-loop 8 (C5): the old assertion pinned the
    resend REGENERATING a smaller sheet — that was the bug's benign half
    (conservation held, but a partial-stamp interruption shipped a
    shrunken, even header-only, payment sheet). With the durable
    .daily-counted.json record the resend RESUMES: the sheet is generated
    exactly once, the resent CSV is byte-identical, and hours are still
    counted exactly once."""
    send1 = _send_time()
    hi1 = _window_hi(send1)
    _mk_delivered_root(ledger, DROOT, hi1 - timedelta(hours=2))
    _stub_telegram(monkeypatch)
    sheets = []
    real_rows = reports.build_sheet_rows

    def spy_rows(led_, day, bounds=None, **kw):
        rows = real_rows(led_, day, bounds, **kw)
        sheets.append(rows)
        return rows
    monkeypatch.setattr(reports, "build_sheet_rows", spy_rows)
    assert runmod.send_daily_report_if_due(cfg, ledger, send1) is True
    assert len(sheets) == 1 and sheets[0][0]["kamla_hrs_uploaded"] == 1.0
    day = send1.strftime("%Y-%m-%d")
    csv_path = cfg.reports_dir / day / f"payment-{day}.csv"
    first_bytes = csv_path.read_bytes()
    # the kill: marker never landed; stamps + anchor + record persist
    (cfg.reports_dir / day / ".sent").unlink()
    assert runmod.send_daily_report_if_due(cfg, ledger,
                                           _send_time(hour=15)) is True
    assert len(sheets) == 1, \
        "the resend must RESUME from the record, never regenerate (r-loop 8)"
    assert csv_path.read_bytes() == first_bytes
    assert (cfg.reports_dir / day / ".sent").exists()
    # conservation: 1.0 h generated once, across both sends
    assert sum(r["total_uploaded_hours"]
               for s in sheets for r in s) == 1.0


# ------------------------- download host-level transient arm (r5 #18)

def test_download_phase_host_errors_requeue_transient(cfg, ledger,
                                                      monkeypatch):
    """r5 #18, pinning uncommitted fix 4: an OSError (and, widened, a
    sqlite3.OperationalError) from the download path is TRANSIENT — back
    to DISCOVERED for the next run, never QUARANTINED. A raw ValueError
    still quarantines: collapsing the arms 'because OSError is an
    Exception' is exactly the r4 #3/#17 regression."""
    sid_os = "2026-08-14T10-00-00Z_kamla_c_00000000000000e1"
    sid_sql = "2026-08-14T10-01-00Z_kamla_c_00000000000000e2"
    sid_val = "2026-08-14T10-02-00Z_kamla_c_00000000000000e3"
    for i, sid in enumerate((sid_os, sid_sql, sid_val)):
        _seed(ledger, sid, ctime=f"2026-08-14T10:0{i}:00.000Z")

    def boom(cfg_, ledger_, sid):
        ledger_.set_state(sid, "DOWNLOADING")
        if sid == sid_os:
            raise OSError(28, "No space left on device")
        if sid == sid_sql:
            raise sqlite3.OperationalError("database is locked")
        raise ValueError("corrupt listing entry")
    monkeypatch.setattr(runmod.ingest, "download", boom)
    monkeypatch.setattr(runmod.deliver, "disk_free_gb", lambda p: 500.0)
    alerts = []
    monkeypatch.setattr(runmod, "_alert",
                        lambda cfg_, text, sent: alerts.append(text))
    runmod._download_phase(cfg, ledger, [sid_os, sid_sql, sid_val], [])
    assert ledger.get(sid_os)["state"] == "DISCOVERED"
    assert ledger.get(sid_sql)["state"] == "DISCOVERED"
    assert ledger.get(sid_val)["state"] == "QUARANTINED"   # contrast holds
    assert sum("host-level" in a for a in alerts) == 2
    # the requeue detail names the cause, so next run's retry is on record
    det = ledger.db.execute(
        "SELECT detail FROM events WHERE session_id=? AND "
        "to_state='DISCOVERED' ORDER BY rowid DESC LIMIT 1",
        (sid_os,)).fetchone()["detail"]
    assert "host-level" in det


# --------------------------- resume-carry exclusions (r5 #19 / r5 #22)

def test_partition_resume_excludes_discovered_and_hold_members(cfg):
    """r5 #19, pinning r4 #35: DISCOVERED/HOLD_VLM members of an open
    batch must NOT ride the terminal-member carry — carrying them marks
    them `attempted`, which defers their re-intake a full tick and closes
    their batch around them. Terminal (DELIVERED) and resumable (READY)
    members still ride."""
    led = Ledger(cfg.ledger_path)
    _seed(led, "m-ready", state="READY")
    _seed(led, "m-done", state="DELIVERED")
    _seed(led, "m-hold")
    led.set_state("m-hold", "HOLD_VLM", "sweep failed")
    _seed(led, "m-disc")                            # stays DISCOVERED
    b = led.start_batch(sessions=["m-ready", "m-done", "m-hold", "m-disc"])
    d_q, v_q, u_q = runmod._partition_resume(led)
    mine = next(x for x in d_q + v_q + u_q if x.no == b)
    assert sorted(mine.sids) == ["m-done", "m-ready"]
    for x in d_q + v_q + u_q:                       # nowhere at all
        assert "m-hold" not in x.sids and "m-disc" not in x.sids
    led.close()


def test_partition_resume_skips_terminals_reported_by_finished_batch(cfg):
    """r5 #22, pinning uncommitted fix 8: a terminal member that a
    FINISHED batch's summary already lists (e.g. a HOLD_VLM member that
    delivered via the guaranteed hold batch) must NOT be carried back
    into its still-open original batch — its delivery/hours would appear
    in two batch messages. Unclaimed terminals still ride (r3 #43)."""
    led = Ledger(cfg.ledger_path)
    _seed(led, "x-held", state="DELIVERED")   # reported by the hold batch
    _seed(led, "y-stuck", state="READY")      # keeps the old batch open
    _seed(led, "z-done", state="DELIVERED")   # terminal, unclaimed
    b1 = led.start_batch(sessions=["x-held", "y-stuck", "z-done"])
    b2 = led.start_batch(sessions=["x-held"])
    led.finish_batch(b2, {"delivered": 1, "rejected": 0,
                          "hours_delta": 0.1, "sessions": ["x-held"]})
    d_q, v_q, u_q = runmod._partition_resume(led)
    mine = next(x for x in d_q + v_q + u_q if x.no == b1)
    assert sorted(mine.sids) == ["y-stuck", "z-done"]   # x-held skipped
    led.close()
