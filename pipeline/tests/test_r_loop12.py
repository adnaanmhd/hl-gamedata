"""r-loop 12 fixes — pipeline side.

Each test cites the iteration-12 finding it pins (r12 #N, findings of
record in R12_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 11af5a0 (session scratchpad), per plan §1.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from pipeline import run as runmod
from pipeline.tests.test_r_loop10 import needs_ffmpeg


# ------- r12 #1/#2: '' is the UNKNOWABLE-md5 sentinel, never "changed"

def test_stamps_survive_zip_md5_backfill_mid_send(cfg, ledger,
                                                  monkeypatch):
    """The F7 CAS read the zip class's '' sentinel as byte identity: the
    download-time backfill replaces '' with a real hash WITHOUT any byte
    change, the CAS missed, the stamp was skipped, and the late-arrival
    guard re-counted the same uploaded hours on a second sent sheet."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    ledger.update(sid, md5_video="")     # zip class: unknowable at count
    done = {"x": False}

    def backfill_mid_send(c, t):
        # identical-bytes backfill lands inside the stamp window: real
        # hash written, NO clears (the deferral stood down)
        if not done["x"]:
            done["x"] = True
            ledger.update(sid, md5_video="f" * 32)
    monkeypatch.setattr(runmod.telegram, "send_message", backfill_mid_send)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["accepted_reported_at"], \
        "'' means UNKNOWABLE, not changed — the stamp must land"
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=1)) is True
    assert b"p@x.com" not in docs[-1], \
        "the same hours must never reach a second sent sheet"


def test_resume_stamps_after_zip_heal_in_the_gap(cfg, ledger,
                                                 monkeypatch):
    """The resume pre-filter had the mirror hole: a zip-class heal in
    the crash-recovery gap rewrites a REAL md5 to '' while deliberately
    preserving the stamps — reading that as 'new bytes' skipped the
    re-stamp and the next sheet re-counted the identical bytes."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    from pipeline.tests.test_r_loop9 import _interrupt_before_stamps
    from pipeline.tests.test_review_r5_driver import _send_time
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    _interrupt_before_stamps(cfg, ledger, monkeypatch, send)
    ledger.update(sid, md5_video="")     # the heal's stamp-preserving ''
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=15)) is True
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["accepted_reported_at"], \
        "unknowable-md5 must not read as a clearing tool having run"
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=1)) is True
    assert b"p@x.com" not in docs[-1]


def test_deferral_adjudicated_new_bytes_still_skip(cfg, ledger,
                                                   monkeypatch, capsys):
    """Control: when the download-time deferral has ALREADY adjudicated
    NEW bytes inside the stamp window (real md5 backfilled beside its
    supersede-style clear), the stamp is still skipped loudly — the
    sheet counted the old bytes and the new hours stay countable."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    ledger.update(sid, md5_video="")
    done = {"x": False}

    def newbytes_mid_send(c, t):
        if not done["x"]:
            done["x"] = True
            ledger.update(sid, md5_video="e" * 32, duration_raw_s=None,
                          uploaded_reported_at=None,
                          accepted_reported_at=None)
    monkeypatch.setattr(runmod.telegram, "send_message", newbytes_mid_send)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] is None, \
        "adjudicated-new bytes must not be stamped from the old sheet"
    assert "SKIPPED" in capsys.readouterr().err


def test_real_vs_real_mismatch_still_skips(cfg, ledger, monkeypatch,
                                           capsys):
    """Control: the motivating F7 race (supersede writes a REAL new md5)
    keeps its skip — only the '' sentinel changed meaning."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch)
    done = {"x": False}

    def supersede_mid_send(c, t):
        if not done["x"]:
            done["x"] = True
            ledger.supersede(sid, new_md5="c" * 32, new_bytes=22,
                             new_ctime=ledger.get(sid)["drive_ctime"],
                             dossier_root=cfg.dossiers)
    monkeypatch.setattr(runmod.telegram, "send_message",
                        supersede_mid_send)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    assert ledger.get(sid)["uploaded_reported_at"] is None
    assert "SKIPPED" in capsys.readouterr().err
