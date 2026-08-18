"""r-loop 9 fixes (D1–D8) — pipeline side.

Each test cites the R9_FINDINGS.md number it pins. Fail-first proofs run in
a scratch copy of the pre-fix tree (session scratchpad), per plan §1.
"""
from __future__ import annotations

import csv
import json
from datetime import timedelta, timezone

import pytest

from pipeline import fix as fixmod
from pipeline import run as runmod
from pipeline.tests.test_r_loop8 import (_created_at, _sidecars,
                                         needs_ffmpeg)


# ------- D1c (#2): the zero-events guard must not be defeated by carries

@needs_ffmpeg
def test_carried_only_rebase_is_refused_as_zero_events(tmp_path):
    """With bogus stamps (head beyond the whole recording) every unmatched
    'down' in the sidecar is re-pressed at t=0, so `events` was non-empty
    and the r8 guard passed — the binner then held that key on EVERY row
    of a clip the stamps do not describe (fabricated input)."""
    from pipeline.tests.test_fix_cut_gate import _make_session
    work = _make_session(tmp_path, seconds=100, name="carryonly")
    created = _created_at(work)
    started = created - timedelta(seconds=725.0)
    # both events precede the bogus head cut; w is never released — its
    # t=0 carry is the ONLY rebase survivor
    evs = [{"t": int(10 * 1e6), "type": "key", "key": "a", "action": "down"},
           {"t": int(11 * 1e6), "type": "key", "key": "a", "action": "up"},
           {"t": int(12 * 1e6), "type": "key", "key": "w", "action": "down"}]
    _sidecars(work, started, evs)
    with pytest.raises(fixmod.FixFailed) as e:
        fixmod.retranslate_from_sidecars(work)
    assert "zero events" in str(e.value)
    assert "carries" in str(e.value)


@needs_ffmpeg
def test_split_child_with_carried_hold_and_in_band_events_succeeds(
        tmp_path):
    """Protects BOTH prior rulings at once: the r8 split-child fix (head_s
    far beyond the clip is legitimate) and the r-loop-4 carry (a key held
    across the cut is re-pressed at t=0) — in-band events beside a carry
    must still retranslate."""
    from pipeline.tests.test_fix_cut_gate import _make_session
    work = _make_session(tmp_path, seconds=100, name="carrymix")
    created = _created_at(work)
    started = created - timedelta(seconds=725.0)
    evs = [
        # held across the child's cut: down before 725, no up until in-band
        {"t": int(700 * 1e6), "type": "key", "key": "w", "action": "down"},
        {"t": int(740 * 1e6), "type": "key", "key": "w", "action": "up"},
        # genuinely in-band presses
        {"t": int(750 * 1e6), "type": "key", "key": "a", "action": "down"},
        {"t": int(752 * 1e6), "type": "key", "key": "a", "action": "up"},
        {"t": int(760 * 1e6), "type": "key", "key": "e", "action": "down"},
        {"t": int(762 * 1e6), "type": "key", "key": "e", "action": "up"},
    ]
    _sidecars(work, started, evs)
    note = fixmod.retranslate_from_sidecars(work)
    assert "re-translated from sidecars" in note
    with (work / "frames.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    keys = {k for r in rows for k in (r["input_keys"] or "").split("|") if k}
    assert {"W", "A", "E"} <= keys, keys
    # the carried W is a hold from row 0, not a single-frame blip
    first_keys = (rows[0]["input_keys"] or "").split("|")
    assert "W" in first_keys


# ------- D1a (#16 mirror): retranslate survives a numeric exe_name

@needs_ffmpeg
def test_retranslate_survives_numeric_exe_name(tmp_path):
    """Same provenance and crash as translate_bundle_v2: a numeric
    exe_name in the raw metadata reached game_key_from_name's re.sub."""
    from pipeline.tests.test_fix_cut_gate import _make_session
    work = _make_session(tmp_path, seconds=100, name="exenum")
    created = _created_at(work)
    started = created - timedelta(seconds=725.0)
    evs = []
    for k, t0 in (("w", 726.0), ("a", 740.0), ("e", 755.0)):
        evs.append({"t": int(t0 * 1e6), "type": "key", "key": k,
                    "action": "down"})
        evs.append({"t": int((t0 + 2.0) * 1e6), "type": "key", "key": k,
                    "action": "up"})
    _sidecars(work, started, evs)
    raw_meta = json.loads((work / "raw" / "metadata.json").read_text())
    raw_meta["game"]["exe_name"] = 123
    (work / "raw" / "metadata.json").write_text(json.dumps(raw_meta))
    note = fixmod.retranslate_from_sidecars(work)
    assert "re-translated from sidecars" in note


# ------- D5 (#6/#21, #4, #8): daily-send resume robustness

def _interrupt_before_stamps(cfg, ledger, monkeypatch, send):
    """Drive the fresh path to write its durable record, then die at the
    message (record present, ZERO stamps) — the widest resume gap."""
    from pipeline import telegram as tgmod

    calls = {"n": 0}

    def flaky_msg(c, t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise tgmod.TelegramError("outage")
    monkeypatch.setattr(runmod.telegram, "send_message", flaky_msg)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is False
    monkeypatch.setattr(runmod.telegram, "send_message", lambda c, t: None)


def test_daily_resume_survives_ist_midnight(cfg, ledger, monkeypatch):
    """The resume record was looked up under TODAY's key only — an
    interruption outliving IST midnight stranded the whole stamped
    cohort's hours off every sheet ever delivered."""
    import sqlite3

    from pipeline import reports
    from pipeline.tests.test_r_loop8 import _daily_seed
    from pipeline.tests.test_review_r5_driver import _send_time
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    calls = {"n": 0}
    real = reports.mark_uploads_reported

    def flaky(led_, lo, hi, sids=None, md5s=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(led_, lo, hi, sids=sids, md5s=md5s, **kw)
    monkeypatch.setattr(reports, "mark_uploads_reported", flaky)
    with pytest.raises(sqlite3.OperationalError):
        runmod.send_daily_report_if_due(cfg, ledger, send)
    first = csv_path.read_bytes()

    next_day = send + timedelta(days=1)
    day2 = next_day.strftime("%Y-%m-%d")
    # tick on D+1 resumes DAY D first — document sent, marker lands, and
    # no D+1 generation happens on this tick
    assert runmod.send_daily_report_if_due(cfg, ledger, next_day) is True
    assert (cfg.reports_dir / day / ".sent").exists()
    assert docs and docs[-1] == first
    assert not (cfg.reports_dir / day2 / ".daily-counted.json").exists()
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["accepted_reported_at"]
    # conservation: the NEXT tick opens D+1 fresh, without the stamped root
    assert runmod.send_daily_report_if_due(cfg, ledger, next_day) is True
    assert b"p@x.com" not in docs[-1]


def test_resume_skips_superseded_sid_and_new_bytes_count_once(
        cfg, ledger, monkeypatch):
    """A supersede in the crash-recovery gap deliberately cleared the
    marks (new bytes = new hours); the blind resume re-stamp made the new
    upload's hours stamped-but-never-counted — invisible to every future
    sheet."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    from pipeline.tests.test_review_r5_driver import _send_time
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    _interrupt_before_stamps(cfg, ledger, monkeypatch, send)
    first = csv_path.read_bytes()
    ledger.supersede(sid, new_md5="b" * 32, new_bytes=11,
                     new_ctime=ledger.get(sid)["drive_ctime"],
                     dossier_root=cfg.dossiers)

    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=15)) is True
    assert csv_path.read_bytes() == first
    assert ledger.get(sid)["uploaded_reported_at"] is None, \
        "a superseded sid must NOT be re-stamped from the stale record"
    assert ledger.get(sid)["accepted_reported_at"] is None, \
        "the ACCEPTED-side skip too (r10 #13: it was unpinned — the " \
        "uploaded column masked it)"

    # the new bytes re-deliver; their hours reach the D+1 sheet once and
    # the D+2 sheet not at all
    ledger.update(sid, duration_raw_s=3600.0, duration_delivered_s=3600.0,
                  delivered_at=(send + timedelta(hours=20))
                  .astimezone(timezone.utc).isoformat(timespec="seconds"))
    ledger.set_state(sid, "DELIVERED")
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=1)) is True
    assert b"p@x.com" in docs[-1], "the new upload's hours must be counted"
    # column-precise (r10 #13): the accepted hours themselves must land —
    # a substring test was satisfied by the uploaded column alone
    rows = list(csv.DictReader(docs[-1].decode().splitlines()))
    mine = [r for r in rows if r.get("player_email") == "p@x.com"]
    assert mine and float(mine[0]["kamla_accepted_hrs"]) == 1.0, mine
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=2)) is True
    assert b"p@x.com" not in docs[-1], "and only once"


def test_resume_still_stamps_an_innocently_updated_root(cfg, ledger,
                                                        monkeypatch):
    """Control for the D5b deviation (md5 discriminator, plan §9): roots
    are counted while still in flight, so plain state churn between kill
    and resume must NOT suppress the stamp — that would double-count the
    root via the late-arrival guard."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    from pipeline.tests.test_review_r5_driver import _send_time
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch)
    _interrupt_before_stamps(cfg, ledger, monkeypatch, send)
    ledger.update(sid, rrd_sampled=1)          # updated_at bumps, same md5
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=15)) is True
    assert ledger.get(sid)["uploaded_reported_at"], \
        "same-bytes churn must not suppress the resume stamp"


def test_resume_refuses_when_a_counted_row_was_deleted(cfg, ledger,
                                                       monkeypatch,
                                                       capsys):
    """A deleted counted row means a recal tool tore the cohort down
    under the record — a blind resume sent a stale sheet crediting
    deleted rows and let the re-run's same-id children be counted
    again."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    from pipeline.tests.test_review_r5_driver import _send_time
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    _interrupt_before_stamps(cfg, ledger, monkeypatch, send)
    first = csv_path.read_bytes()
    ledger.db.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
    ledger.db.commit()

    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=15)) is False
    assert "no longer exists" in capsys.readouterr().err
    assert not (cfg.reports_dir / day / ".sent").exists()
    assert docs == [] and csv_path.read_bytes() == first


def test_kill_between_marker_and_document_resends_document_only(
        cfg, ledger, monkeypatch):
    """.sent was touched BEFORE the document went out, so a kill in
    between suppressed the payment CSV forever behind a dangling
    'attached' message."""
    import json as _json

    from pipeline import reports
    from pipeline.tests.test_r_loop8 import _daily_seed
    from pipeline.tests.test_review_r5_driver import _send_time
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    record = cfg.reports_dir / day / ".daily-counted.json"
    rec = _json.loads(record.read_text())
    assert rec.get("doc_sent") is True
    # steady state: fully settled day -> False
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=15)) is False

    # simulate the kill window: marker present, document never delivered
    del rec["doc_sent"]
    record.write_text(_json.dumps(rec))
    n_docs = len(docs)
    msgs = []
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda c, t: msgs.append(t))
    builds = []
    monkeypatch.setattr(reports, "build_sheet_rows",
                        lambda *a, **k: builds.append(1) or [])
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=16)) is True
    assert len(docs) == n_docs + 1 and docs[-1] == csv_path.read_bytes()
    assert msgs == [] and builds == [], \
        "document-only resend: no message, no regeneration"
    assert _json.loads(record.read_text()).get("doc_sent") is True
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=17)) is False


# ------- D6 (#5): identical-md5 heal preserves payment stamps

def test_identical_md5_heal_preserves_payment_stamps(cfg, ledger,
                                                     monkeypatch):
    """An operator fixing a folder-name typo re-paths the sessions under
    it — same bytes, new path. The heal cleared the payment stamps
    unconditionally, so an already-counted root re-entered via the
    late-arrival guard and its uploaded hours landed on a SECOND sent
    sheet (probe broke d3's counted-exactly-once invariant: 2.0 counted
    for 1.0 uploaded). The existing r6/r8 heal tests seed md5 'old' and
    keep pinning the different-md5 full clear."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    sid = "2026-08-14T10-00-00Z_kamla_c_0123456789abcdef"
    same_md5 = "d41d8cd98f00b204e9800998ecf8427e"   # the builder default
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="op@x.com",
        player_email="p1@x.com", drive_path="kamla/BADPATH",
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video=same_md5,
        bytes_=1, state="DISCOVERED")
    ledger.set_state(sid, "QUARANTINED", "work copy missing")
    ledger.update(sid, uploaded_reported_at="2026-08-15T00:00:00+00:00",
                  accepted_reported_at="2026-08-15T00:00:00+00:00",
                  duration_raw_s=3600.0)
    entries = make_session_entries(sid=sid)
    monkeypatch.setattr(ingest, "list_drive", lambda _c: entries)
    ingest.scan(cfg, ledger, entries)
    row = ledger.get(sid)
    assert row["state"] == "DISCOVERED"          # the heal itself happened
    assert row["drive_path"] != "kamla/BADPATH"
    assert row["uploaded_reported_at"], "same bytes — stamp must survive"
    assert row["accepted_reported_at"]
    assert row["duration_raw_s"] == 3600.0, \
        "same bytes — hours must not be re-countable on re-probe"
    # conservation: the next payment sheet must NOT count this root again
    monkeypatch.setattr(runmod.telegram, "send_message", lambda c, t: None)
    docs: list[bytes] = []
    monkeypatch.setattr(
        runmod.telegram, "send_document",
        lambda c, p, caption="": docs.append(
            __import__("pathlib").Path(p).read_bytes()))
    from pipeline.tests.test_review_r5_driver import _send_time
    assert runmod.send_daily_report_if_due(cfg, ledger, _send_time()) is True
    assert docs and b"p1@x.com" not in docs[-1], \
        "an already-counted root must stay off post-heal sheets"


# ------- D7 (#1/#18 ruling C, #7, #19): refix per-piece payment memory

def _refix_rc(cfg, monkeypatch, rclone=None):
    from pipeline.tests.test_payment_split_r6 import _load
    refix = _load("recal_refix_reset")
    monkeypatch.setattr(refix, "rclone", rclone or (lambda args: (0, "")))

    class _Args:
        yes = True
        allow_reported = True
    return refix._locked_main(cfg, _Args)


def test_refix_refuses_a_sealed_root_and_preserves_the_seal(
        cfg, monkeypatch, capsys):
    """#1's second-pass erasure: the teardown used to overwrite an
    existing tree_sealed_at with NULL, re-opening already-paid footage.
    Ruling C: a sealed root (r-loop-8-era) is REFUSED into
    skipped_sealed — the seal is never touched."""
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import FIXABLE, _put
    led = Ledger(cfg.ledger_path)
    root = "2026-08-14T09-00-00Z_kamla_c_0000000000000sl1"
    try:
        _put(led, root, state="SPLIT", raw=3600.0, player="sealed@x.com")
        _put(led, f"{root}-p1", state="DELIVERED", parent=root,
             raw=1800.0, delivered=1700.0, player="sealed@x.com")
        _put(led, f"{root}-p2", state="REJECTED", parent=root, raw=1800.0,
             player="sealed@x.com", reasons=FIXABLE)
        led.update(root, tree_sealed_at="2026-08-15T00:00:00+00:00",
                   uploaded_reported_at="2026-08-15T00:00:00+00:00")
    finally:
        led.close()
    assert _refix_rc(cfg, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "REFUSED (sealed tree)" in out
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(root)["state"] == "SPLIT"           # untouched
        assert led.get(root)["tree_sealed_at"], "seal must survive"
        assert led.get(f"{root}-p1") is not None
    finally:
        led.close()


def test_refix_second_pass_never_double_pays(cfg, monkeypatch):
    """#1's substance under ruling C: the paid piece stays excluded
    across a SECOND refix pass (memory is durable, INSERT OR IGNORE),
    and the recovered sibling's hours are counted exactly once in the
    whole history."""
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import (FIXABLE, W2, W3,
                                                      _put, _row, _sheet)
    led = Ledger(cfg.ledger_path)
    root = "2026-08-14T09-00-00Z_kamla_c_0000000000000p21"
    try:
        _put(led, root, state="SPLIT", raw=3600.0, player="pp@x.com")
        _put(led, f"{root}-p1", state="DELIVERED", parent=root,
             raw=1800.0, delivered=1700.0, player="pp@x.com")
        led.update(f"{root}-p1",
                   accepted_reported_at="2026-08-15T00:00:00+00:00")
        _put(led, f"{root}-p2", state="REJECTED", parent=root, raw=1800.0,
             player="pp@x.com", reasons=FIXABLE)
        led.update(root, uploaded_reported_at="2026-08-15T00:00:00+00:00")
    finally:
        led.close()
    assert _refix_rc(cfg, monkeypatch) == 0            # pass 1

    led = Ledger(cfg.ledger_path)
    try:
        # re-run: p1 re-delivers (unpaid — the memory skip never stamps);
        # p2 fix-fails AGAIN
        _put(led, f"{root}-p1", state="DELIVERED", parent=root,
             raw=1800.0, delivered=1700.0, player="pp@x.com")
        _put(led, f"{root}-p2", state="REJECTED", parent=root, raw=1800.0,
             player="pp@x.com", reasons=FIXABLE)
        led.set_state(root, "SPLIT")
    finally:
        led.close()
    assert _refix_rc(cfg, monkeypatch) == 0            # pass 2 proceeds

    led = Ledger(cfg.ledger_path)
    try:
        assert led.paid_pieces_for(root) == {f"{root}-p1": 1700.0}, \
            "memory survives every pass — the #1 erasure is closed"
        assert led.get(root)["tree_sealed_at"] is None
        # re-run 2: p1 re-delivers again, p2 finally recovers
        _put(led, f"{root}-p1", state="DELIVERED", parent=root,
             raw=1800.0, delivered=1700.0, player="pp@x.com")
        _put(led, f"{root}-p2", state="DELIVERED", parent=root,
             raw=1800.0, delivered=1500.0, player="pp@x.com")
        led.set_state(root, "SPLIT")
        s2 = _row(_sheet(led, W2), "pp@x.com")
        assert s2 is not None
        assert s2["kamla_accepted_hrs"] == round(1500 / 3600.0, 2), \
            "recovered hours once; the paid piece never again"
        assert _row(_sheet(led, W3), "pp@x.com") is None
    finally:
        led.close()


def test_id_collision_with_different_seconds_is_excluded_loudly(
        tmp_path, capsys):
    """Deterministic -pN ids make collisions the thing to get right
    (ruling C): a re-run that cut DIFFERENTLY re-creates a paid piece's
    id with different seconds. Money-safe: excluded, never auto-paid,
    LOUD for a human."""
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import W2, _put, _row, _sheet
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_0000000000000ic1"
    _put(led, root, state="SPLIT", raw=3600.0, player="coll@x.com")
    led.update(root, uploaded_reported_at="2026-08-15T00:00:00+00:00")
    led.record_paid_piece(root, f"{root}-p1", 1700.0, None)
    _put(led, f"{root}-p1", state="DELIVERED", parent=root, raw=1800.0,
         delivered=900.0, player="coll@x.com")        # different geometry
    s = _row(_sheet(led, W2), "coll@x.com")
    err = capsys.readouterr().err
    assert "AMBIGUOUS re-delivered piece" in err
    assert s is None or s["kamla_accepted_hrs"] == 0.0, \
        "a colliding id must never be auto-paid"
    assert led.get(f"{root}-p1")["accepted_reported_at"] is None
    led.close()


def test_recal_tools_refuse_while_a_daily_send_is_pending(cfg, monkeypatch,
                                                          capsys):
    """#7: with .daily-counted.json durable but stamps not yet applied,
    the tools saw zero reported roots and tore the cohort down — the
    later resume then credited deleted rows and the re-run's same-id
    children were counted again."""
    import sys as _sys

    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import FIXABLE, _load, _put
    led = Ledger(cfg.ledger_path)
    sid = "2026-08-14T09-00-00Z_kamla_c_0000000000000il1"
    try:
        _put(led, sid, state="REJECTED", raw=3600.0, reasons=FIXABLE)
    finally:
        led.close()
    day_dir = cfg.reports_dir / "2026-08-17"
    day_dir.mkdir(parents=True)
    (day_dir / ".daily-counted.json").write_text(
        json.dumps({"lo": "x", "hi": "y", "counted": [sid],
                    "accepted": []}))

    assert _refix_rc(cfg, monkeypatch) == 2
    assert "pending resume" in capsys.readouterr().out
    led = Ledger(cfg.ledger_path)
    assert led.get(sid)["state"] == "REJECTED", "no action before refusal"
    led.close()

    reset = _load("recal_rebuild_reset")
    parachute = cfg.home / "parachute.db"
    parachute.write_bytes(b"x" * 2048)
    monkeypatch.setenv("HL_PIPELINE_HOME", str(cfg.home))
    monkeypatch.setattr(_sys, "argv", ["recal_rebuild_reset.py", "--yes",
                                       "--backup", str(parachute)])
    assert reset.main() == 2
    assert "pending resume" in capsys.readouterr().out

    # settled day (marker + doc_sent) -> both proceed
    (day_dir / ".sent").touch()
    rec = json.loads((day_dir / ".daily-counted.json").read_text())
    rec["doc_sent"] = True
    (day_dir / ".daily-counted.json").write_text(json.dumps(rec))
    assert _refix_rc(cfg, monkeypatch) == 0
    led = Ledger(cfg.ledger_path)
    assert led.get(sid)["state"] == "DISCOVERED"
    led.close()


def test_lsf_failure_aborts_instead_of_skipping(cfg, monkeypatch, capsys):
    """#19: only rc 3/4 mean 'not found'. Any other non-zero lsf rc
    (network outage = 1) used to print 'remote dir absent', skip the
    compensating moveto, and proceed to teardown — the re-run then
    re-delivered a duplicate to the client."""
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import FIXABLE, _put

    def seed(tag):
        led = Ledger(cfg.ledger_path)
        root = f"2026-08-14T09-00-00Z_kamla_c_000000000000{tag}"
        try:
            _put(led, root, state="SPLIT", raw=3600.0, player="ls@x.com")
            _put(led, f"{root}-p1", state="DELIVERED", parent=root,
                 raw=1800.0, delivered=1700.0, player="ls@x.com")
            led.db.execute(
                "INSERT INTO events(session_id, ts, from_state, to_state,"
                " detail) VALUES(?,?,?,?,?)",
                (f"{root}-p1", "2026-08-15T00:00:00+00:00", "PACKAGED",
                 "UPLOADED", f"verified at humynlabs/kamla/x/{root}-p1"))
            _put(led, f"{root}-p2", state="REJECTED", parent=root,
                 raw=1800.0, player="ls@x.com", reasons=FIXABLE)
            led.db.commit()
        finally:
            led.close()
        return root

    root = seed("0lf1")
    rc = _refix_rc(cfg, monkeypatch,
                   rclone=lambda args: (1, "network unreachable")
                   if args[0] == "lsf" else (0, ""))
    assert rc == 3, "an lsf FAILURE must abort pre-DB"
    assert "not an absence" in capsys.readouterr().out
    led = Ledger(cfg.ledger_path)
    assert led.get(root)["state"] == "SPLIT", "no DB change on abort"
    assert led.get(f"{root}-p1") is not None
    led.close()

    # rc=3 (genuinely absent) keeps today's skip-and-proceed
    rc = _refix_rc(cfg, monkeypatch,
                   rclone=lambda args: (3, "directory not found")
                   if args[0] == "lsf" else (0, ""))
    assert rc == 0
    led = Ledger(cfg.ledger_path)
    assert led.get(root)["state"] == "DISCOVERED"
    led.close()


# ------- D2a (#12): the hard length gates judge the PROBED duration

def test_cnt_short_prefers_the_probed_duration():
    """A present-but-wrong duration_seconds claim under 70 terminally
    rejected real >=70s footage (blocking, unfixable, video-independent)
    while the same verdict planned the rewrite that recomputes the very
    field."""
    from pipeline.tests.test_validate_mapper import aux, codes, rep
    from pipeline.validate import map_reasons
    res = map_reasons(rep(duration_s=45.0),
                      aux(probed_duration_s=120.0), "kamla")
    assert "CNT_SHORT" not in codes(res)


def test_cnt_short_fires_on_the_probed_duration_regardless_of_claim():
    from pipeline.tests.test_validate_mapper import aux, codes, rep
    from pipeline.validate import map_reasons
    res = map_reasons(rep(duration_s=120.0),
                      aux(probed_duration_s=45.0), "kamla")
    assert "CNT_SHORT" in codes(res) and res.bin == 3


def test_cnt_short_falls_back_to_the_claim_without_a_probe():
    """Control: callers that never probed (hand-built aux, older paths)
    keep today's claim-based behaviour."""
    from pipeline.tests.test_validate_mapper import aux, codes, rep
    from pipeline.validate import map_reasons
    res = map_reasons(rep(duration_s=50.0), aux(), "kamla")
    assert "CNT_SHORT" in codes(res)


# ------- D2b (#15): typed qa FAILs beat the engine-error quarantine

@needs_ffmpeg
def test_empty_frames_csv_routes_to_unmapped_not_quarantine(tmp_path):
    """A 0-byte frames.csv beside intact raw sidecars had its TYPED
    checker FAIL preempted by a.error -> RuntimeError -> QUARANTINED,
    although QA_FAIL_UNMAPPED -> FIX_RETRANSLATE rebuilds the file
    completely (validate.py's own designed routing)."""
    from dataclasses import asdict

    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.validate import load_engine, map_reasons
    work = _make_session(tmp_path, seconds=100, name="emptycsv")
    created = _created_at(work)
    started = created - timedelta(seconds=5.0)
    evs = [{"t": int(6 * 1e6), "type": "key", "key": "w", "action": "down"},
           {"t": int(8 * 1e6), "type": "key", "key": "w", "action": "up"}]
    _sidecars(work, started, evs)
    (work / "frames.csv").write_bytes(b"")

    eng = load_engine()
    a = eng.analyze(work, {work.name: work / "raw"}, None, 4.0, 1.0)
    assert a.error == "", a.error
    assert any(i.startswith("FAIL") for i in a.qa_issues)

    rep = asdict(a)
    rep["findings"] = []
    res = map_reasons(rep, {"has_raw": True, "vlm_required": False},
                      "kamla")
    unmapped = [r for r in res.reasons if r["code"] == "QA_FAIL_UNMAPPED"]
    assert unmapped and unmapped[0]["fixable"] is True


# ------- D3a (#9): first worker death is host-suspect, second terminal

def _ins(ledger, sid, state="INGESTED"):
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="op@x.com",
        player_email="p@x.com", drive_path=f"kamla/op@x.com/p@x.com/{sid}",
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video="a" * 32,
        bytes_=10, state=state)


def test_first_worker_death_is_host_suspect_not_quarantine(cfg, ledger,
                                                           monkeypatch):
    """An externally SIGKILLed spawn worker (kernel OOM killer,
    systemd-oomd, cgroup MemoryMax, admin kill -9) presents ONLY as
    BrokenProcessPool with stop unset — branding it a session crash
    bypassed the r-loop-6 host carve-out and one OOM burst terminally
    quarantined every in-flight validation."""
    from pipeline import continuous as cont
    sid = "s-oom"
    _ins(ledger, sid)
    (cfg.work / sid).mkdir(parents=True)
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    alerts: list[str] = []
    monkeypatch.setattr(cont.AlertBook, "alert",
                        lambda self, t: alerts.append(t))

    class _BrokenPool:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def map(self, *a, **k):
            raise cont.concurrent.futures.process.BrokenProcessPool("oom")
    monkeypatch.setattr(cont.concurrent.futures, "ProcessPoolExecutor",
                        _BrokenPool)
    monkeypatch.setattr(cont, "_POOL_DISABLED", False)

    # first death: host-suspect — VALIDATING + cooldown, NOT terminal
    assert drv._validate_one(ledger, sid, ledger.get(sid)) is None
    assert ledger.get(sid)["state"] == "VALIDATING"
    assert not drv.cool.ready(sid), "cooldown must be pending"
    assert any("host-suspect" in a for a in alerts)
    # second death for the SAME sid: reproducible — terminal as before
    assert drv._validate_one(ledger, sid, ledger.get(sid)) == "QUARANTINED"
    assert ledger.get(sid)["state"] == "QUARANTINED"


# ------- D3b (#10): rrd-child CalledProcessError is host-class in U lane

def test_rrd_child_calledprocesserror_defers_delivery_continuous(
        cfg, ledger, monkeypatch):
    """A non-zero rrd_creation.py exit (ENOSPC writing the multi-GB rrd,
    OOM kill, broken rerun-sdk pin) terminally QUARANTINED a
    fully-validated READY session — during the exact disk-low incident
    the lane's own carve-out documents. A HUNG rrd child was already
    host-classed; a DEAD one must be too."""
    import subprocess as sp

    from pipeline import continuous as cont
    sid = "s-rrd"
    _ins(ledger, sid, state="READY")
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    monkeypatch.setattr(cont.AlertBook, "alert", lambda self, t: None)

    def boom(*a, **k):
        raise sp.CalledProcessError(1, ["python", "rrd_creation.py"])
    monkeypatch.setattr(cont.deliver, "deliver_session", boom)
    drv._deliver_one(ledger, sid)
    assert ledger.get(sid)["state"] == "READY", \
        "a dead rrd child is host-class: the session must stay resumable"
    assert not drv.cool.ready(sid), "cooldown must be pending"


def test_rrd_child_calledprocesserror_defers_delivery_batch(
        cfg, ledger, monkeypatch):
    import subprocess as sp

    from pipeline import run as runmod
    sid = "s-rrd-b"
    _ins(ledger, sid, state="READY")

    def boom(*a, **k):
        raise sp.CalledProcessError(1, ["python", "rrd_creation.py"])
    monkeypatch.setattr(runmod.deliver, "deliver_session", boom)
    stats = runmod._deliver_phase(cfg, ledger, [sid], [])
    assert ledger.get(sid)["state"] == "READY"
    assert stats["upload_failures"] == 1


# ------- D4a (#11/#20): gate-record spans rebased across clock shifts

def _gate_parent(tmp_path, inputs, windows):
    """Real-writer gate entry (C7 discipline): synthetic frames.csv at
    1 row/s, real gate.gate_windows note."""
    from pipeline import gate
    from pipeline.tests.test_r_loop7 import make_gate_csv
    work = tmp_path / "gatework"
    work.mkdir(exist_ok=True)
    make_gate_csv(work, inputs=inputs)
    note = gate.gate_windows(work, windows)
    return {"fix": "FIX_GATE_WINDOW", "ok": True,
            "params": {"windows": [[a, b] for a, b in windows]},
            "note": note}


def test_level2_split_keeps_the_gate_record_on_the_child_clock(tmp_path):
    """Every span in a gate record was on the parent clock AT GATE TIME,
    but cutter rebases child rows to the segment's own PTS — a level-2
    split compared child-clock bounds against parent-clock spans and
    dropped the record from ALL grandchildren (the r-loop-6 blocker shape
    one level down; grandchildren exist in production)."""
    from pipeline.validate import _gate_destroyed
    entry = _gate_parent(tmp_path, {300: ("Q", "general_cancel"),
                                    301: ("Q", "general_cancel"),
                                    302: ("Q", "general_cancel")},
                         [(300.0, 302.0)])
    parent = tmp_path / "dossiers" / "S"
    parent.mkdir(parents=True)
    # level 1: blanked rows land in S-p2 (t0=200) -> child clock ~100
    fixmod._propagate_gate_record(parent, tmp_path / "dossiers", [entry],
                                  [{"id": "S-p1", "t0": 0.0, "t1": 200.0},
                                   {"id": "S-p2", "t0": 200.0,
                                    "t1": 400.0}])
    # level 2: split S-p2 at ITS OWN clock 150 — the blanked rows (~100)
    # belong to the first grandchild
    fixmod._propagate_gate_record(
        tmp_path / "dossiers" / "S-p2", tmp_path / "dossiers", [],
        [{"id": "S-p2-p1", "t0": 0.0, "t1": 150.0},
         {"id": "S-p2-p2", "t0": 150.0, "t1": 200.0}])
    g1 = _gate_destroyed(tmp_path / "dossiers" / "S-p2-p1")
    g2 = _gate_destroyed(tmp_path / "dossiers" / "S-p2-p2")
    assert g1 == {"actions": ["general_cancel"], "key_frames": 3}, g1
    assert g2 == {"actions": [], "key_frames": 0}, g2


def test_retrim_after_gate_rebases_the_record_spans(tmp_path):
    """FIX_RETRIM_HEAD rebases the parent's surviving rows; an attempt-2
    cut then WITHHELD the record from the segment containing the blanked
    rows and wrongly handed it to the sibling — the r-loop-7 harm
    resurrected."""
    from pipeline.validate import _gate_destroyed
    entry = _gate_parent(tmp_path, {200: ("E", "interact"),
                                    201: ("E", "interact"),
                                    202: ("E", "interact")},
                         [(200.0, 202.0)])
    parent = tmp_path / "dossiers" / "R"
    parent.mkdir(parents=True)
    fixmod._append_fixlog(parent, [entry])
    fixmod._append_fixlog(parent, [{
        "fix": "FIX_RETRIM_HEAD", "ok": True, "params": {"head_s": 30.0},
        "note": {"session": "R", "head_cut_s": 30.0}}])
    # post-trim clocks: blanked rows now at ~170-172
    fixmod._propagate_gate_record(parent, tmp_path / "dossiers", [],
                                  [{"id": "R-p1", "t0": 0.0, "t1": 180.0},
                                   {"id": "R-p2", "t0": 180.0,
                                    "t1": 370.0}])
    g1 = _gate_destroyed(tmp_path / "dossiers" / "R-p1")
    g2 = _gate_destroyed(tmp_path / "dossiers" / "R-p2")
    assert g1 == {"actions": ["interact"], "key_frames": 3}, g1
    assert g2 == {"actions": [], "key_frames": 0}, g2


# ------- D4b (#14): adoption propagates; child-write OSError is host

def test_adoption_propagates_the_gate_record(cfg, ledger, monkeypatch):
    """Both mid-split crash-adoption paths completed the SPLIT without
    ever calling _propagate_gate_record — a kill between the cutter's
    manifest write and the propagation loop shipped children with no
    inherited record."""
    import json as _json

    from pipeline import continuous as cont
    sid = "P"
    _ins(ledger, sid, state="FIXING")
    for kid, t0, t1 in ((f"{sid}-p1", 0.0, 200.0),
                        (f"{sid}-p2", 200.0, 400.0)):
        ledger.insert_session(
            session_id=kid, game="kamla", operator_email="op@x.com",
            player_email="p@x.com", drive_path=f"kamla/op/p/{sid}",
            drive_ctime="2026-08-14T10:00:00.000Z", md5_video="", bytes_=0,
            state="INGESTED", parent_id=sid,
            detail=f"split segment {t0}-{t1}s")
    (cfg.work / f"{sid}.split-manifest.json").write_text(
        _json.dumps({"segments": [f"{sid}-p1", f"{sid}-p2"]}))
    entry = _gate_parent(cfg.work, {300: ("Q", "general_cancel"),
                                    301: ("Q", "general_cancel")},
                         [(300.0, 301.0)])
    fixmod._append_fixlog(cfg.dossiers / sid, [entry])

    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    assert drv._fixing_triage(ledger, sid, ledger.get(sid)) is False
    assert ledger.get(sid)["state"] == "SPLIT"
    from pipeline.validate import _gate_destroyed
    g2 = _gate_destroyed(cfg.dossiers / f"{sid}-p2")
    assert g2 == {"actions": ["general_cancel"], "key_frames": 2}, g2
    assert not (cfg.dossiers / f"{sid}-p1" / "fixlog.json").exists()


def test_child_fixlog_oserror_is_host_kind_through_apply_fixes(
        tmp_path, monkeypatch):
    """`except OSError: pass` around the per-child write silently shipped
    a child without its record on ENOSPC — now the OSError surfaces and
    apply_fixes classifies it HOST, so the carve-out discards the
    rescinded cut and re-derives."""
    entry = _gate_parent(tmp_path, {40: ("E", "interact")}, [(40.0, 41.0)])
    dossiers = tmp_path / "dossiers"
    (dossiers / "K").mkdir(parents=True)
    fixmod._append_fixlog(dossiers / "K", [entry])
    # a FILE squatting on the child-dossier path makes mkdir raise
    # FileExistsError (an OSError) — a stand-in for ENOSPC
    (dossiers / "K-p1").write_text("squatter")
    monkeypatch.setattr(
        fixmod, "_dispatch",
        lambda fix_id, params, work, game, root: {
            "segments": [{"id": "K-p1", "t0": 0.0, "t1": 100.0}],
            "dropped": []})
    work = tmp_path / "K"
    work.mkdir()
    out = fixmod.apply_fixes(work, {"steps": [("FIX_CUT_SEGMENTS", {})]},
                             game="kamla", dossier_dir=dossiers / "K")
    assert out["kind"] == "host", out
    assert "FIX_CUT_SEGMENTS" in (out["error"] or "")


# ------- D4c (#22): applied-span preference pinned at both sites

def test_pad_spill_propagates_via_applied_spans_legacy(tmp_path):
    """A window ending exactly at a cut boundary: the pad rows spill into
    the next segment, so the APPLIED span (note.windows) must decide —
    the requested window alone would withhold the record (preference was
    mutation-proved suite-invisible)."""
    entry = _gate_parent(tmp_path, {100: ("E", "interact"),
                                    101: ("E", "interact"),
                                    102: ("E", "interact")},
                         [(100.0, 102.0)])
    applied_spans = entry["note"]["windows"]
    assert applied_spans and applied_spans[0][1] > 102.0, \
        "the real gate must pad beyond the requested end"
    legacy = {"fix": "FIX_GATE_WINDOW", "ok": True,
              "params": dict(entry["params"]),
              "note": {"windows": applied_spans,
                       "destroyed": entry["note"]["destroyed"]}}
    # requested [100,102] does not touch [102.5, 200); the pad does
    assert fixmod._gate_entry_touches(legacy, 102.5, 200.0) is True
    bare = {"fix": "FIX_GATE_WINDOW", "ok": True,
            "params": dict(entry["params"]), "note": {}}
    assert fixmod._gate_entry_touches(bare, 102.5, 200.0) is False


def test_pad_spill_selects_the_window_per_window(tmp_path):
    entry = _gate_parent(tmp_path, {100: ("E", "interact"),
                                    101: ("E", "interact"),
                                    102: ("E", "interact")},
                         [(100.0, 102.0)])
    pw = entry["note"]["per_window"]
    assert pw and pw[0]["windows"][0][1] > 102.0
    mine = fixmod._entries_for_segment([entry], 102.5, 200.0, "S")
    assert mine, "the pad-widened applied span must select the window"
    assert mine[0]["note"]["destroyed"]["key_frames"] > 0


def test_engine_oserror_is_host_classed_through_validate(tmp_path,
                                                         monkeypatch):
    """The OSError type was laundered into the a.error STRING, so
    run.py's isinstance host/crash split could never see host — a
    transient I/O error became a terminal quarantine."""
    import types

    from pipeline import validate as valmod

    class _A:
        error = "frames.csv unreadable: OSError"
        error_kind = "host"

    stub = types.SimpleNamespace(analyze=lambda *a, **k: _A())
    monkeypatch.setattr(valmod, "_ENGINE", stub)
    d = tmp_path / "s"
    d.mkdir()
    # a real (tiny, junk) video is not needed: monkeypatch the probe too
    monkeypatch.setattr(
        "translator.video.probe",
        lambda p: types.SimpleNamespace(duration_s=100.0))
    with pytest.raises(OSError):
        valmod.validate_session(d, tmp_path / "dossier", skip_vlm=True)
