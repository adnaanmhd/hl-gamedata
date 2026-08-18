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


# ------- r11 #2/#13/#20: the orphan void reconciles against DELIVERED
# ------- nodes, not id presence

def _paid_tree(tmp_path, player):
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import _put
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_0000000000000rcv"
    _put(led, root, state="DISCOVERED", raw=3600.0, player=player)
    led.update(root, uploaded_reported_at="2026-08-15T00:00:00+00:00")
    return led, root


def test_recut_sibling_of_a_matching_paid_id_is_not_counted(tmp_path,
                                                            capsys):
    """Same-level re-cut: the re-run re-creates R-p1 (deterministic
    cutter ids) over DIFFERENT footage, so the id-presence void never
    fired — R-p1 printed AMBIGUOUS but R-p2, carrying part of the same
    already-paid 1700s, was counted IN FULL and stamped, silently."""
    from pipeline.tests.test_payment_split_r6 import W2, _put, _row, _sheet
    led, root = _paid_tree(tmp_path, "recut@x.com")
    led.record_paid_piece(root, f"{root}-p1", 1700.0, None)
    _put(led, f"{root}-p1", state="DELIVERED", parent=root,
         delivered=900.0, player="recut@x.com")
    _put(led, f"{root}-p2", state="DELIVERED", parent=root,
         delivered=800.0, player="recut@x.com")
    led.set_state(root, "SPLIT")
    s = _row(_sheet(led, W2), "recut@x.com")
    err = capsys.readouterr().err
    assert "ORPHANED paid-piece memory" in err
    assert s is None or s["kamla_accepted_hrs"] == 0.0, \
        "the sibling may contain already-paid footage — never auto-paid"
    assert led.get(f"{root}-p2")["accepted_reported_at"] is None
    led.close()


def test_nested_resplit_of_a_paid_id_is_not_counted(tmp_path, capsys):
    """Nested re-split (the EXACT case the r10 comment claimed covered):
    R-p1 exists again but as a SPLIT node, which the walk never compares
    against memory — its grandchildren, carrying the already-paid
    footage, were counted in full and stamped with zero loud lines."""
    from pipeline.tests.test_payment_split_r6 import W2, _put, _row, _sheet
    led, root = _paid_tree(tmp_path, "nest@x.com")
    led.record_paid_piece(root, f"{root}-p1", 1700.0, None)
    _put(led, f"{root}-p1", state="DISCOVERED", parent=root,
         player="nest@x.com")
    led.set_state(f"{root}-p1", "SPLIT")
    for kid, secs in ((f"{root}-p1-p1", 150.0), (f"{root}-p1-p2", 140.0),
                      (f"{root}-p2", 200.0)):
        _put(led, kid, state="DELIVERED",
             parent=f"{root}-p1" if "-p1-p" in kid else root,
             delivered=secs, player="nest@x.com")
    led.set_state(root, "SPLIT")
    s = _row(_sheet(led, W2), "nest@x.com")
    err = capsys.readouterr().err
    assert "ORPHANED paid-piece memory" in err
    assert s is None or s["kamla_accepted_hrs"] == 0.0, \
        "grandchildren carry the already-paid footage"
    for kid in (f"{root}-p1-p1", f"{root}-p1-p2", f"{root}-p2"):
        assert led.get(kid)["accepted_reported_at"] is None, kid
    led.close()


def test_all_matched_orphan_tree_stays_loud_every_sheet(tmp_path, capsys):
    """#20's silent half: when every surviving DELIVERED node id-matches
    memory, no loud line ever printed — the void kept the root
    re-entering every future sheet, silently, forever. The ROOT now
    prints one reconcile line per sheet; the re-entry itself pins the
    void (orphaned-off would suppress the line entirely)."""
    from pipeline.tests.test_payment_split_r6 import W2, W3, _put, _row, \
        _sheet
    led, root = _paid_tree(tmp_path, "allm@x.com")
    led.record_paid_piece(root, f"{root}-p1", 1700.0, None)
    led.record_paid_piece(root, f"{root}-p2", 1600.0, None)
    # only -p1 survives the re-run, id- AND seconds-identical; -p2's
    # footage was dropped by the new cut
    _put(led, f"{root}-p1", state="DELIVERED", parent=root,
         delivered=1700.0, player="allm@x.com")
    led.set_state(root, "SPLIT")
    s = _row(_sheet(led, W2), "allm@x.com")
    assert "ORPHANED paid-piece memory" in capsys.readouterr().err
    assert s is None or s["kamla_accepted_hrs"] == 0.0
    _sheet(led, W3)
    assert "ORPHANED paid-piece memory" in capsys.readouterr().err, \
        "the reconcile line must repeat until a human reconciles"
    led.close()


# ------- r11 #3: fix_lagshift_csv must not swallow host classes

def _lagshift_boom(tmp_path, monkeypatch, exc):
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop7 import make_gate_csv
    from translator import sync as syncmod
    work = tmp_path / "L"
    work.mkdir()
    make_gate_csv(work)
    (work / "session.json").write_text('{"fps": 30.0}')
    monkeypatch.setattr(syncmod, "available", lambda: True)

    def boom(path):
        raise exc
    monkeypatch.setattr(syncmod, "motion_track", boom)
    return fixmod.apply_fixes(work,
                              {"steps": [("FIX_LAGSHIFT_CSV", {})]},
                              game="kamla", dossier_dir=tmp_path / "d")


def test_lagshift_memoryerror_stays_host_classed(tmp_path, monkeypatch):
    """The r10 except-Exception guard re-typed MemoryError/OSError (host:
    attempt refunded, cooldown) as session-kind FixFailed (attempt
    burned) — an OOM burst spent both attempts on a terminal reject."""
    out = _lagshift_boom(tmp_path, monkeypatch,
                         MemoryError("Unable to allocate 1.9 GiB"))
    assert out["kind"] == "host", out
    assert "MemoryError" in out["error"]


def test_lagshift_decode_failure_stays_typed_session(tmp_path,
                                                     monkeypatch):
    """Control: the motivating r10 #10 error class (opencv cannot open
    the video) keeps its typed session-kind FixFailed."""
    out = _lagshift_boom(tmp_path, monkeypatch,
                         ValueError("could not open video"))
    assert out["kind"] == "session", out
    assert "not decodable by opencv" in out["error"]


# ------- r11 #4/#11: hygiene judges the SESSION's authoritative keybind

def _hygiene_work(tmp_path, keybind: dict | None):
    from pipeline.tests.test_r_loop7 import make_gate_csv
    work = tmp_path / "H"
    (work / "raw").mkdir(parents=True)
    make_gate_csv(work, inputs={i: ("LShift", "") for i in range(20, 26)})
    if keybind is not None:
        (work / "raw" / "keybind.json").write_text(json.dumps(keybind))
    return work


def test_hygiene_honors_the_sessions_own_keybind(tmp_path):
    """`bound` was built from the built-ins only, so the r10 unbound
    strip deleted every key the session's own raw/keybind.json binds
    (6/6 custom-bound LShift presses in the probe) and the action
    re-resolution erased their actions — the corrupted session then
    passed the checker cleanly."""
    from pipeline import fix as fixmod
    work = _hygiene_work(tmp_path, {"sprint": "shift_l"})
    note = fixmod.fix_key_hygiene(work, "kamla")
    header, rows = fixmod._read_csv(work)
    col = {c: i for i, c in enumerate(header)}
    carrying = [r for r in rows if "LShift" in r[col["input_keys"]]]
    assert len(carrying) == 6, note
    assert all("sprint" in r[col["input_actions"]] for r in carrying), \
        "the custom bind's action must resolve from the surviving key"


def test_hygiene_without_sidecar_still_strips_unbound(tmp_path):
    """Control (r10 #9 preserved): with no session keybind the built-in
    governs, and a key it does not bind is still stripped."""
    from pipeline import fix as fixmod
    work = _hygiene_work(tmp_path, None)
    fixmod.fix_key_hygiene(work, "kamla")
    header, rows = fixmod._read_csv(work)
    col = {c: i for i, c in enumerate(header)}
    assert not [r for r in rows if "LShift" in r[col["input_keys"]]]
