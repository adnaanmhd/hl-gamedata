"""r-loop 13 fixes (G1–G9, R8_IMPLEMENTATION_PLAN §3) — pipeline side.

Each test cites the iteration-13 finding it pins (r13 #N, findings of
record in R13_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at b69fee1 (session scratchpad), per plan §1/§3.
"""
from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timedelta, timezone

import csv
import json

from pipeline import run as runmod
from pipeline.tests.test_r_loop8 import needs_ffmpeg

# The durable adjudication marker's ON-DISK detail prefix, pinned as a
# LITERAL on purpose: markers already written into production ledgers
# must stay readable by _stamp's query forever — a rename of the
# ingest.ZIP_ADJ_CHANGED constant would silently orphan them.
_ADJ_PREFIX = "zip-backfill: bytes CHANGED"


# ------- r13 #1/#2/#3 (G1): the zip-class '' adjudication made durable

def _download_with_bytes(cfg, ledger, monkeypatch, sid, payload):
    """Run the REAL ingest.download over planted payload bytes (the
    test_r_loop10 fake_rclone pattern) so the prev_md5 deferral, the
    clear, and the adjudication marker all come from production code."""
    from pipeline import ingest

    def fake_rclone(args, **kw):
        d = None
        for a in args:
            if str(cfg.work) in str(a):
                d = ingest.Path(a)
        d.mkdir(parents=True, exist_ok=True)
        (d / "video.mp4").write_bytes(payload)
        (d / "frames.csv").write_text("frame_id\n")
        (d / "session.json").write_text('{"game_title": "Kamla"}')
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(ingest, "run_rclone", fake_rclone)
    monkeypatch.setattr(ingest, "_probe_duration", lambda v: 7200.0)
    return ingest.download(cfg, ledger, sid)


def _mark_delivered(ledger, sid, seconds=1850.0):
    ledger.update(sid, duration_delivered_s=seconds,
                  delivered_at=datetime.now(timezone.utc)
                  .isoformat(timespec="seconds"))
    ledger.set_state(sid, "DELIVERED")


def _adj_events(ledger, sid):
    return ledger.db.execute(
        "SELECT detail FROM events WHERE session_id=? AND detail LIKE ?",
        (sid, _ADJ_PREFIX + "%")).fetchall()


def test_zip_supersede_mid_send_self_heals_at_download(cfg, ledger,
                                                       monkeypatch,
                                                       capsys):
    """r13 #1≡#3: ingest's zip re-upload branch calls
    ledger.supersede(new_md5="") — a stamp-CLEARING '' writer the r12
    CAS-miss arm could not tell from the stamp-PRESERVING heal, so the
    stamps re-landed on the freshly-reset slot and the corrected
    re-upload's hours were stranded off every sheet, silently. The
    supersede now leaves the heal-format prev_md5 breadcrumb, so the
    download-time deferral adjudicates the class: the falsely-landed
    stamp SELF-HEALS on changed bytes and the hours re-enter loudly."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    done = {"x": False}

    def supersede_mid_send(c, t):
        # the zip re-upload branch's exact call (ingest.py, review-r2 #9
        # flow): changed bytes/newer ctime on a REJECTED/QUARANTINED
        # zip slot -> supersede with an UNKNOWABLE new md5
        if not done["x"]:
            done["x"] = True
            ledger.supersede(sid, new_md5="", new_bytes=22,
                             new_ctime=ledger.get(sid)["drive_ctime"],
                             dossier_root=cfg.dossiers)
    monkeypatch.setattr(runmod.telegram, "send_message",
                        supersede_mid_send)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    row = ledger.get(sid)
    # arm 4 stamps the ''-holding row (unchanged r12 #1/#2 behavior —
    # the #3 refuter's own recommendation): the false stamp is accepted
    # here BECAUSE the breadcrumb makes it deferral-covered below
    assert row["uploaded_reported_at"] and row["accepted_reported_at"]
    ev = ledger.db.execute(
        "SELECT detail FROM events WHERE session_id=? AND "
        "detail LIKE 'superseded: new md5 %'", (sid,)).fetchone()
    assert "prev_md5=" in ev["detail"], \
        "the zip-class supersede must leave the heal-format breadcrumb"
    # the corrected re-upload downloads: the REAL deferral adjudicates
    _download_with_bytes(cfg, ledger, monkeypatch, sid,
                         b"genuinely-new-bytes")
    row = ledger.get(sid)
    assert not row["uploaded_reported_at"] and \
        not row["accepted_reported_at"], \
        "changed bytes: the falsely-landed stamps must self-heal"
    assert _adj_events(ledger, sid), \
        "the adjudication must leave its durable marker event"
    # the new bytes deliver -> the hours reach exactly ONE later sheet
    _mark_delivered(ledger, sid)
    capsys.readouterr()
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=1)) is True
    assert b"p@x.com" in docs[-1], \
        "the corrected re-upload's hours must reach the next sheet"
    assert "LATE ARRIVAL" in capsys.readouterr().err
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=2)) is True
    assert b"p@x.com" not in docs[-1], "…and exactly once"


def test_resume_skips_recorded_blank_after_adjudicated_new_bytes(
        cfg, ledger, monkeypatch, capsys):
    """r13 #2: arm 2's old discriminator (real md5 beside a NULL
    duration) was TRANSIENT — the probe/F6 refill erases it the moment
    the new bytes land, so a resume after the crash-recovery gap
    silently stamped a recorded-'' root whose bytes the deferral had
    adjudicated CHANGED. The skip now keys on the durable marker event
    postdating the count record's "at"."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    from pipeline.tests.test_r_loop9 import _interrupt_before_stamps
    from pipeline.tests.test_review_r5_driver import _send_time
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    # a zip-class heal wrote '' pre-count (stamp-preserving; r10 #4
    # shape, breadcrumb included) — the sheet records the '' sentinel
    old_md5 = ledger.get(sid)["md5_video"]
    ledger.update(sid, md5_video="")
    ledger.db.execute(
        "INSERT INTO events(session_id, ts, from_state, to_state,"
        " detail) VALUES(?,?,?,?,?)",
        (sid, "2026-08-15T00:00:00+00:00", "QUARANTINED", "DISCOVERED",
         f"re-registered: quarantined path healed to x; "
         f"prev_md5={old_md5}"))
    ledger.db.commit()
    _interrupt_before_stamps(cfg, ledger, monkeypatch, send)
    # in the gap: the download runs the deferral (adjudicates CHANGED,
    # writes the marker, clears nothing that had landed) and the probe
    # refill erases the transient NULL-duration signature
    ledger.set_state(sid, "DISCOVERED")
    _download_with_bytes(cfg, ledger, monkeypatch, sid, b"new-bytes")
    row = ledger.get(sid)
    assert row["duration_raw_s"] is not None, \
        "the refill must have erased the transient discriminator"
    assert _adj_events(ledger, sid)
    capsys.readouterr()
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=15)) is True
    row = ledger.get(sid)
    assert not row["uploaded_reported_at"] and \
        not row["accepted_reported_at"], \
        "resume must NOT stamp a recorded-'' root the deferral has " \
        "since adjudicated as NEW bytes"
    assert "SKIPPED" in capsys.readouterr().err, "…and must say so"
    # the new hours reach the NEXT sheet exactly once
    _mark_delivered(ledger, sid)
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=1)) is True
    assert b"p@x.com" in docs[-1]
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=2)) is True
    assert b"p@x.com" not in docs[-1]


def test_identical_rezip_supersede_stamp_stands(cfg, ledger, monkeypatch):
    """Control: a ctime-only re-zip (identical bytes) superseded with ''
    mid-send — the arm-4 stamp correctly STANDS at download time (the
    deferral finds old == local, no clear, no marker) and the hours
    never re-enter a later sheet."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    payload = b"same-old-bytes"
    ledger.update(sid, md5_video=hashlib.md5(payload).hexdigest())
    done = {"x": False}

    def supersede_mid_send(c, t):
        if not done["x"]:
            done["x"] = True
            ledger.supersede(sid, new_md5="", new_bytes=22,
                             new_ctime=ledger.get(sid)["drive_ctime"],
                             dossier_root=cfg.dossiers)
    monkeypatch.setattr(runmod.telegram, "send_message",
                        supersede_mid_send)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    assert ledger.get(sid)["uploaded_reported_at"]
    _download_with_bytes(cfg, ledger, monkeypatch, sid, payload)
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["accepted_reported_at"], \
        "identical bytes: the stamp correctly stands"
    assert not _adj_events(ledger, sid), \
        "no adjudication marker for unchanged bytes"
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=1)) is True
    assert b"p@x.com" not in docs[-1], \
        "the same hours must never re-enter a later sheet"


# ------- r13 #4 (G2): FIX_RETRANSLATE honors the session's own keybind

def _custom_bound_bundle(tmp_path, name):
    """A real bundle whose raw/keybind.json binds q -> interact and
    w -> move_up: presses of q survive only if the session keybind
    governs (the kamla built-in binds interact to e, leaving q
    unbound); w resolves either way (control within the test)."""
    from datetime import timedelta

    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.tests.test_r_loop8 import _created_at, _sidecars
    work = _make_session(tmp_path, seconds=100, name=name)
    created = _created_at(work)
    started = created - timedelta(seconds=0.0)
    evs = []
    for k, t0 in (("q", 10.0), ("w", 30.0), ("q", 50.0)):
        evs.append({"t": int(t0 * 1e6), "type": "key", "key": k,
                    "action": "down"})
        evs.append({"t": int((t0 + 2.0) * 1e6), "type": "key", "key": k,
                    "action": "up"})
    _sidecars(work, started, evs)
    (work / "raw" / "keybind.json").write_text(
        json.dumps({"interact": "q", "move_up": "w"}))
    return work


def _key_rows(work):
    with (work / "frames.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    keys = {k for r in rows
            for k in (r["input_keys"] or "").split("|") if k}
    q_actions = {a for r in rows
                 if "Q" in (r["input_keys"] or "").split("|")
                 for a in (r["input_actions"] or "").split("|") if a}
    return keys, q_actions


def _fixable(code, **params):
    return {"code": code, "blocking": True, "fixable": True,
            "params": params, "evidence": "e"}


@needs_ffmpeg
def test_retranslate_dispatch_honors_the_sessions_own_keybind(tmp_path):
    """r13 #4: _dispatch passed game_override=game whenever game is in
    C.GAMES — and the ledger game ALWAYS is (ingest scoping), so the
    built-in keybind overrode the session's own raw/keybind.json on
    EVERY production retranslate; the session-keybind branch was dead
    code there. Driven through the PRODUCTION path: plan_fixes over a
    has_raw-routed FAIL -> apply_fixes -> _dispatch."""
    from pipeline import fix as fixmod
    work = _custom_bound_bundle(tmp_path, "kbdispatch")
    plan = fixmod.plan_fixes([_fixable("SYN_TS_NOT_PTS")],
                             game="kamla", has_raw=True)
    assert [f for f, _ in plan["steps"]][0] == "FIX_RETRANSLATE"
    out = fixmod.apply_fixes(work, plan, game="kamla",
                             dossier_dir=tmp_path / "d1")
    assert not out["error"], out
    keys, q_actions = _key_rows(work)
    assert "Q" in keys and "W" in keys, keys
    assert "interact" in q_actions, \
        "the custom bind's press must keep its action through the " \
        "production dispatch path"


@needs_ffmpeg
def test_retranslate_reroute_plan_keeps_the_builtin_override(tmp_path):
    """Control (pins review-2 #5): on a REROUTE plan the raw metadata is
    exactly what the mismatch falsified — the corrected game's built-in
    governs and the sidecar keybind is ignored (q stays unbound)."""
    from pipeline import fix as fixmod
    work = _custom_bound_bundle(tmp_path, "kbreroute")
    plan = fixmod.plan_fixes(
        [_fixable("STR_GAME_MISMATCH", actual="kamla"),
         _fixable("SYN_TS_NOT_PTS")],
        game="kamla", has_raw=True)
    ids = [f for f, _ in plan["steps"]]
    assert ids[:2] == ["FIX_REROUTE_GAME", "FIX_RETRANSLATE"], ids
    out = fixmod.apply_fixes(work, plan, game="kamla",
                             dossier_dir=tmp_path / "d2")
    assert not out["error"], out
    keys, q_actions = _key_rows(work)
    assert "Q" not in keys, \
        "reroute: the built-in must govern — q stays unbound"
    assert "W" in keys
