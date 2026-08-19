"""r-loop 14 fixes (H1–H9, R8_IMPLEMENTATION_PLAN §3) — pipeline side.

Each test cites the iteration-14 finding it pins (r14 #N, findings of
record in R14_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 5f7015b (session scratchpad), per plan §1/§3.
"""
from __future__ import annotations

import json
import time
from datetime import timedelta, timezone
from types import SimpleNamespace

from pipeline import run as runmod


# ------- r14 #2≡#3 (H1): counted_at captured BEFORE the sheet's row read

def test_adjudication_during_the_sheet_build_skips_the_stamp(
        cfg, ledger, monkeypatch, capsys):
    """r14 #2≡#3 (H1): the count-time anchor used to be captured AFTER
    write_payment_sheet returned, so a ZIP_ADJ_CHANGED adjudication
    landing during the build (row read done, counted_at not yet taken)
    was neither "before the row read" nor ">= counted_at" — the stamp
    landed silently and the re-upload's hours reached no sheet, ever.
    With the anchor captured pre-build, the whole build window is
    covered by the `>=` arm."""
    from pipeline import ingest, reports
    from pipeline.tests.test_r_loop8 import _daily_seed
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    ledger.update(sid, md5_video="")     # zip class: unknowable at count
    real_build = reports.write_payment_sheet
    done = {"x": False}

    def adjudicate_mid_build(*a, **kw):
        out = real_build(*a, **kw)
        if not done["x"]:
            done["x"] = True
            # the download-time deferral's effects (the r12 test idiom
            # for ingest's clear + marker + backfill), landing AFTER
            # the row read but BEFORE the pre-H1 post-build capture
            ledger.update(sid, md5_video="e" * 32, duration_raw_s=None,
                          uploaded_reported_at=None,
                          accepted_reported_at=None)
            ledger.set_state(
                sid, ledger.get(sid)["state"],
                f"{ingest.ZIP_ADJ_CHANGED} (md5  -> {'e' * 32})")
            # roll the wall clock into the NEXT second so a POST-build
            # counted_at capture (the pre-H1 shape) provably postdates
            # the marker — the exact blind window r14 #2 proved
            time.sleep(1.1)
        return out
    monkeypatch.setattr(reports, "write_payment_sheet",
                        adjudicate_mid_build)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    row = ledger.get(sid)
    assert not row["uploaded_reported_at"] and \
        not row["accepted_reported_at"], \
        "a mid-build adjudication must NOT be stamped from the old sheet"
    assert "SKIPPED" in capsys.readouterr().err, "…and must say so"
    # the F6 probe refill restores the duration; the corrected
    # re-upload's hours then reach exactly ONE later sheet
    ledger.update(sid, duration_raw_s=7200.0)
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=1)) is True
    assert b"p@x.com" in docs[-1], \
        "the re-upload's hours must reach the next sheet"
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=2)) is True
    assert b"p@x.com" not in docs[-1], "…and exactly once"


def test_pre_build_adjudication_leaves_a_real_md5_for_the_cas_arm(
        cfg, ledger, monkeypatch, capsys):
    """r14 #2 control (H1, the other side of the guard): an adjudication
    strictly BEFORE the sheet build backfills the real md5, so the row
    read snapshots a REAL hash — the recorded-'' arm is never in play,
    the CAS arm governs, and the stamp lands on the bytes the sheet
    actually counted (no over-skip from the earlier anchor)."""
    from pipeline import ingest
    from pipeline.tests.test_r_loop8 import _daily_seed
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch)
    # the deferral ran to completion pre-count: real-md5 backfill +
    # durable marker (the probe refill already restored the duration)
    ledger.update(sid, md5_video="e" * 32)
    ledger.set_state(sid, ledger.get(sid)["state"],
                     f"{ingest.ZIP_ADJ_CHANGED} (md5  -> {'e' * 32})")
    capsys.readouterr()
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["accepted_reported_at"], \
        "the sheet counted the NEW bytes — the CAS stamp must land"
    assert "SKIPPED" not in capsys.readouterr().err


def test_record_at_and_stamp_anchor_are_the_identical_string(
        cfg, ledger, monkeypatch):
    """H1 pin: the durable record's "at" and both stamp calls'
    counted_at are ONE capture. Under a ticking clock (every now()
    call differs by a full second) any re-capture in the chain would
    desynchronize the resume path's replay from the fresh path's skip
    decisions — the identical-string property G1 relied on and H1
    keeps."""
    from pipeline import reports
    from pipeline.tests.test_r_loop8 import _daily_seed
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch)
    base = {"t": send.astimezone(timezone.utc)}

    def ticking_now(tz=None):
        base["t"] += timedelta(seconds=1)
        return base["t"].astimezone(tz) if tz else base["t"]
    monkeypatch.setattr(runmod, "datetime",
                        SimpleNamespace(now=ticking_now))
    seen: dict[str, str | None] = {}
    real_up = reports.mark_uploads_reported
    real_acc = reports.mark_accepted_reported

    def up(led, lo, hi, sids=None, md5s=None, counted_at=None):
        seen["up"] = counted_at
        return real_up(led, lo, hi, sids=sids, md5s=md5s,
                       counted_at=counted_at)

    def acc(led, sids, md5s=None, counted_at=None):
        seen["acc"] = counted_at
        return real_acc(led, sids, md5s=md5s, counted_at=counted_at)
    monkeypatch.setattr(reports, "mark_uploads_reported", up)
    monkeypatch.setattr(reports, "mark_accepted_reported", acc)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    rec = json.loads(
        (cfg.reports_dir / day / ".daily-counted.json").read_text())
    assert rec["at"] == seen["up"] == seen["acc"], \
        "the record and both stamps must carry the IDENTICAL anchor"
