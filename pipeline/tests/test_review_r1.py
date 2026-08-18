"""Tests added by adversarial-review round 1 (findings #14, #16, #17, #30):
BrokenProcessPool per-job retry, final-gate budget exhaustion, the payment
sheet, and daily-report partial-failure semantics."""
import concurrent.futures
import json
from datetime import datetime

from pipeline import config as C
from pipeline import deliver, reports, run as runmod, telegram
from pipeline.ledger import Ledger

SID = "2026-08-14T10-00-00Z_kamla_c_00000000000000aa"


def _seed(ledger, sid=SID, state="INGESTED", player="p@x.com", **extra):
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email=player, drive_path=f"kamla/Op/{player}/{sid}",
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video=sid[:8], bytes_=1,
        state=state)
    for k, v in extra.items():
        ledger.update(sid, **{k: v})


class _FlakyPool:
    """First multi-worker pool breaks like a native crash; the per-job
    single-worker retry pools run the job inline."""
    def __init__(self, max_workers=None, mp_context=None):
        self.mw = max_workers or 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def map(self, fn, jobs):
        if self.mw > 1:
            raise concurrent.futures.process.BrokenProcessPool("boom")
        return [fn(j) for j in jobs]


def test_broken_pool_retries_per_job_instead_of_quarantining_batch(
        cfg, ledger, monkeypatch):
    """review-r1 #14: a BrokenProcessPool must not quarantine the whole
    batch — each job retries in its own pool and real verdicts land."""
    for i in range(2):
        sid = f"2026-08-14T1{i}-00-00Z_kamla_c_{i:016x}"
        _seed(ledger, sid)
        (cfg.work / sid).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor",
                        _FlakyPool)
    sids = [f"2026-08-14T1{i}-00-00Z_kamla_c_{i:016x}" for i in range(2)]
    runmod._validate_phase(cfg, ledger, sids, [], workers=2)
    for sid in sids:
        row = ledger.get(sid)
        assert row["state"] == "FIX_QUEUED", (row["state"],
                                              row["reasons_json"])
        assert "STR_VIDEO_UNREADABLE" in row["reasons_json"]


def test_final_gate_failure_with_exhausted_budget_rejects(cfg, ledger,
                                                          monkeypatch):
    """review-r1 #16: the third leg of the gate-failure hand-back — a
    session whose fix budget is spent REJECTS at the final gate instead of
    cycling FIX_QUEUED forever."""
    _seed(ledger, state="READY", fix_attempts=C.FIX_RETRIES)
    (cfg.work / SID).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        runmod.deliver, "deliver_session",
        lambda cfg_, ledger_, sid, dest_prefix=C.VENDOR:
        deliver.DeliveryOutcome(sid, "failed_gate", detail="test gate",
                                gate_fails=["FAIL: frame drift"]))
    stats = runmod._deliver_phase(cfg, ledger, [SID], [])
    assert stats["delivered"] == 0
    row = ledger.get(SID)
    assert row["state"] == "REJECTED"
    assert row["fix_attempts"] == C.FIX_RETRIES
    last = ledger.db.execute(
        "SELECT detail FROM events WHERE session_id=? "
        "ORDER BY id DESC LIMIT 1", (SID,)).fetchone()["detail"]
    assert "final gate" in last


def test_payment_sheet_hours_only_and_grouped_by_player(cfg, ledger):
    """review-r1 #17: the paid-number document — rows carry per-player
    hours, and no money figures anywhere (R11)."""
    now = datetime.now(C.IST).replace(hour=15)
    for i, (player, secs) in enumerate(
            [("a@x.com", 3600.0), ("a@x.com", 1800.0), ("b@y.com", 900.0)]):
        sid = f"2026-08-15T0{i}-00-00Z_kamla_c_{i + 16:016x}"
        _seed(ledger, sid, state="INGESTED", player=player)
        ledger.update(sid, duration_delivered_s=secs,
                      delivered_at=now.isoformat(timespec="seconds"))
        ledger.set_state(sid, "READY")
        ledger.set_state(sid, "PACKAGED")
        ledger.set_state(sid, "UPLOADED")
        ledger.set_state(sid, "DELIVERED")
    # explicit bounds covering the seeded drive_ctime (v4 cohort keys the
    # whole row on the upload time)
    csv_path, md_path = reports.write_payment_sheet(
        cfg, ledger, now,
        bounds=("2026-08-14T00:00:00+00:00", "2026-08-16T00:00:00+00:00"))
    assert csv_path.exists() and md_path.exists()
    text = csv_path.read_text()
    assert "a@x.com" in text and "b@y.com" in text
    assert "1.5" in text          # a@x.com: 1.5 h
    assert "0.25" in text         # b@y.com: 0.25 h
    for money in ("$", "USD", "INR", "₹"):
        assert money not in text + md_path.read_text()


def test_daily_report_sheet_failure_does_not_resend_message(cfg, ledger,
                                                            monkeypatch):
    """review-r1 #30/#20/#26: a payment-sheet send failure must never
    re-send the report MESSAGE. Since r-loop 9 (#8) the DOCUMENT half
    retries until it lands (doc_sent in the durable record,
    dup-over-silence) — pre-r9 the marker alone suppressed the CSV
    forever behind a dangling 'attached' message."""
    sent = []
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda cfg_, text: sent.append(text))

    def doc_fail(cfg_, path, caption=""):
        raise telegram.TelegramError("document too large")
    monkeypatch.setattr(runmod.telegram, "send_document", doc_fail)

    def reports_sent():
        return sum(1 for t in sent
                   if "payment sheet attachment failed" not in t)
    now = datetime.now(C.IST).replace(hour=C.DAILY_REPORT_HOUR_IST)
    assert runmod.send_daily_report_if_due(cfg, ledger, now) is True
    # the report message + the sheet-failure alert (review-r2 #46) — but
    # never a duplicate report
    assert len(sent) == 2
    assert "payment sheet attachment failed" in sent[1]
    # second invocation: marker present, document still undelivered —
    # document-only retry (fails again -> one more alert), report message
    # NOT duplicated (r-loop 9 #8)
    assert runmod.send_daily_report_if_due(cfg, ledger, now) is True
    assert reports_sent() == 1
    # the outage clears: the document lands and doc_sent settles the day
    docs = []
    monkeypatch.setattr(runmod.telegram, "send_document",
                        lambda cfg_, path, caption="": docs.append(path))
    assert runmod.send_daily_report_if_due(cfg, ledger, now) is True
    assert len(docs) == 1
    assert runmod.send_daily_report_if_due(cfg, ledger, now) is False
    assert len(docs) == 1 and reports_sent() == 1
