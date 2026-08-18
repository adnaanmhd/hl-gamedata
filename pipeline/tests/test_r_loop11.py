"""r-loop 11 fixes — pipeline side.

Each test cites the iteration-11 finding it pins (r11 #N, findings of
record in R11_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 1500d95 (session scratchpad), per plan §1.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from pipeline import run as runmod


# ------- r11 #1/#14/#16 BLOCKER: a pending/wedged TODAY must never
# ------- reach the fresh path

def _wedge_today(cfg, ledger, monkeypatch):
    """Drive today's send into the wedged state the blocker probes used:
    record written, stamps not landed, then the counted row deleted so
    the resume refuses permanently. Returns (send, sid, csv_path, day)."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    from pipeline.tests.test_r_loop9 import _interrupt_before_stamps
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    _interrupt_before_stamps(cfg, ledger, monkeypatch, send)
    ledger.db.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
    ledger.db.commit()
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is False
    assert (cfg.reports_dir / day / ".wedged").exists()
    return send, sid, csv_path, day, docs


def test_wedged_today_is_never_regenerated(cfg, ledger, monkeypatch,
                                           capsys):
    """The r10 wedge skip `continue`d past a wedged day, but when that day
    IS today the loop fell through to the fresh path — which guarded only
    on `.sent` and so REGENERATED post-stamp: payment CSV and counted
    record overwritten (reconciliation evidence destroyed), the smaller
    regenerated sheet sent as the payment document."""
    from pipeline.tests.test_review_r5_driver import _send_time
    send, sid, csv_path, day, docs = _wedge_today(cfg, ledger, monkeypatch)
    first_csv = csv_path.read_bytes()
    record = cfg.reports_dir / day / ".daily-counted.json"
    first_rec = record.read_bytes()

    # the very next tick, SAME day — pre-fix this returned True and
    # regenerated over the wedge
    capsys.readouterr()
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=15)) is False
    err = capsys.readouterr().err
    assert "WEDGED" in err
    assert csv_path.read_bytes() == first_csv, \
        "the payment CSV of record must never be overwritten"
    assert record.read_bytes() == first_rec, \
        "the counted record is the human's reconciliation evidence"
    assert not (cfg.reports_dir / day / ".sent").exists()
    assert docs == [], "no payment document may go out for a wedged day"


def test_settled_today_refuses_silently_and_doc_resume_survives(
        cfg, ledger, monkeypatch, capsys):
    """Control on the #1 guard: today's record also reaches it when the
    day is fully SETTLED (the scan `continue`s past sent + doc_sent) —
    that refusal must stay SILENT like the old marker check, and the
    guard must not swallow the r9 #8 document-only resume on the way."""
    from pipeline import telegram as tgmod
    from pipeline.tests.test_r_loop8 import _daily_seed
    from pipeline.tests.test_review_r5_driver import _send_time
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)

    def doc_down(c, p, caption=""):
        raise tgmod.TelegramError("attachment outage")
    monkeypatch.setattr(runmod.telegram, "send_document", doc_down)
    monkeypatch.setattr(runmod.telegram, "send_message", lambda c, t: None)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    record = cfg.reports_dir / day / ".daily-counted.json"
    assert not json.loads(record.read_text()).get("doc_sent")
    first_csv = csv_path.read_bytes()

    # next tick resumes the document only — never the fresh path
    monkeypatch.setattr(
        runmod.telegram, "send_document",
        lambda c, p, caption="": docs.append(csv_path.read_bytes()))
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=15)) is True
    assert docs[-1] == first_csv
    assert json.loads(record.read_text()).get("doc_sent") is True
    # fully settled now: later ticks refuse SILENTLY (no scare line)
    capsys.readouterr()
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=16)) is False
    assert "WEDGED/pending" not in capsys.readouterr().err
