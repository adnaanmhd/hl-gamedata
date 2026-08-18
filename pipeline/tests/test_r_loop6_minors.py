"""r-loop 6 — the five confirmed minors (R6_HANDOFF_KICKOFF_PROMPT §6).

Each is a case where a surface stated something that was not true: a fix
planned that provably cannot run, a check reported OK that never executed,
a crash where a FAIL belonged, an evidence section describing a different
population than the columns above it, and a coaching note that tells the
player who uploaded FIRST that only the first upload counts.
"""
from __future__ import annotations

import json
from datetime import datetime

from pipeline import config as C
from pipeline import deliver, reports, validate
from pipeline.ledger import Ledger


# ---------------------------------------------- #1 validate.py mapping

def _map(issue: str, has_raw: bool) -> list[dict]:
    reasons: list[dict] = []
    validate._map_qa_issues([f"FAIL: {issue}"], reasons, has_raw)
    return reasons


def test_frame_id_not_zero_based_no_longer_plans_a_dead_fix():
    """It mapped to STR_ROWS_MISMATCH, which with no sidecars plans
    FIX_ROWS_SURGERY — a fix that only truncates or appends up to 2 TAIL
    rows and never rewrites an existing frame_id cell. At the usual delta
    of 0 it rewrites the identical rows and REPORTS SUCCESS, so both fix
    attempts and three paid VLM sweeps are spent reaching the same reject,
    under the bare fix-failed marker. Exactly the trap the file's own
    r-loop-4 note avoids for 'frame_id column unparseable'."""
    reasons = _map("frame_id not zero-based sequential", has_raw=False)
    codes = [r["code"] for r in reasons]
    assert "STR_ROWS_MISMATCH" not in codes, \
        "still plans the surgery that cannot touch frame_id"
    assert codes == ["QA_FAIL_UNMAPPED"]
    assert reasons[0]["blocking"] and not reasons[0]["fixable"]


def test_frame_id_not_zero_based_still_retranslates_with_sidecars():
    """Nothing is lost by unmapping: the retranslate is the only repair
    that re-zeroes ids, and QA_FAIL_UNMAPPED still plans it."""
    reasons = _map("frame_id not zero-based sequential", has_raw=True)
    assert reasons[0]["code"] == "QA_FAIL_UNMAPPED"
    assert reasons[0]["blocking"] and reasons[0]["fixable"]
    import pipeline.fix as fixmod
    plan = fixmod.plan_fixes(reasons, game="kamla", has_raw=True)
    steps = [fid for fid, _params in plan["steps"]]
    assert "FIX_RETRANSLATE" in steps, plan
    assert plan["unfixable"] == [], plan


def test_frame_id_unparseable_and_zero_based_are_treated_alike():
    """The two siblings differ only in whether the ids parse; both are
    unfixable without sidecars, so neither may plan a burn."""
    a = _map("frame_id column unparseable (non-integer or short row)", False)
    b = _map("frame_id not zero-based sequential", False)
    assert [r["code"] for r in a] == [r["code"] for r in b]


# -------------------------------------- #2 analyze_sample frame_sync line

def test_frame_sync_ok_requires_the_check_to_have_run():
    """`OK` was inferred from the absence of a complaint, so seven of
    check_session_v2's nine early returns printed 'OK (<=100ms vs real
    PTS)' for a check that never executed."""
    eng = validate.load_engine()

    class _R:
        def __init__(self, issues, checked):
            self.issues = issues
            self.checked = checked

    # early return: the checker stopped before the frame-sync check
    for stopper in ("FAIL: session.json unreadable: JSONDecodeError",
                    "FAIL: session.json is not a JSON object",
                    "FAIL: frames.csv unreadable: UnicodeDecodeError",
                    "FAIL: frames.csv is empty (no header row)",
                    "FAIL: frames.csv has 3 short/ragged row(s)",
                    "FAIL: frame_id column unparseable (non-integer)",
                    "FAIL: timestamp_ms column unparseable (non-integer)",
                    "FAIL: key_binding.json present — removed from the "
                    "v2 delivery"):
        line = eng.frame_sync_line(_R([stopper], set()))
        assert "OK" not in line, f"false OK after {stopper!r}"
        assert "not checked" in line
    # it ran and passed
    assert eng.frame_sync_line(_R([], {"frame_sync"})).startswith("OK")
    # it ran and found drift / could not read PTS — reported verbatim
    drift = "FAIL: frame-sync drift: worst row timestamp 900ms off real PTS"
    assert eng.frame_sync_line(_R([drift], {"frame_sync"})) == drift
    unv = "WARN: cannot verify frame sync (PTS unreadable)"
    assert eng.frame_sync_line(_R([unv], {"frame_sync"})) == unv


def test_irregular_spacing_warn_does_not_masquerade_as_a_sync_verdict():
    """The property the old comment was protecting stays true: the
    irregular-spacing WARN also says 'REAL frame PTS'."""
    eng = validate.load_engine()

    class _R:
        issues = ["WARN: irregular frame intervals vs REAL frame PTS (2.1%)"]
        checked = {"frame_sync"}
    assert eng.frame_sync_line(_R()).startswith("OK")


# --------------------------------------- #3 unhashable dx/dy convention

def test_unhashable_camera_fields_fail_instead_of_crashing():
    """`x not in {"right","left"}` HASHES x. A list or dict in any of the
    four dx/dy fields raised TypeError, and the session was QUARANTINED as
    'validation crashed' — terminal, media held, manual queue — instead of
    getting an actionable FAIL. These are the four fields r-loop 2's
    container-type guard skipped."""
    from translator.v2 import _check_session_json, V2Result
    base = {
        "vendor_name": "humynlabs", "game_title": "Kamla", "session_id": "s",
        "created_at_utc": "2026-08-14T10:00:00Z",
        "ended_at_utc": "2026-08-14T10:02:00Z", "duration_ms": 120000,
        "duration_seconds": 120.0, "fps": 30.0, "frame_count": 2,
        "record_width_px": 1920, "record_height_px": 1080,
        "screen_width_px": 1920, "screen_height_px": 1080,
        "localization": "en-US", "platform": "PC",
    }
    for field in ("dx_positive", "dx_negative", "dy_positive", "dy_negative"):
        for bad in ([], {}, ["right"], {"v": "right"}):
            conv = {"maps_to": "camera_look_velocity", "dx_positive": "right",
                    "dx_negative": "left", "dy_positive": "down",
                    "dy_negative": "up"}
            conv[field] = bad
            r = V2Result("s")
            _check_session_json({**base, "input_mouse_convention": conv}, r)
            assert r.status == "FAIL"
            assert any(field in i for i in r.issues), (field, bad, r.issues)


def test_valid_camera_convention_still_passes():
    from translator.v2 import _check_session_json, V2Result
    conv = {"maps_to": "camera_look_velocity", "dx_positive": "right",
            "dx_negative": "left", "dy_positive": "down", "dy_negative": "up"}
    base = {
        "vendor_name": "humynlabs", "game_title": "Kamla", "session_id": "s",
        "created_at_utc": "2026-08-14T10:00:00Z",
        "ended_at_utc": "2026-08-14T10:02:00Z", "duration_ms": 120000,
        "duration_seconds": 120.0, "fps": 30.0, "frame_count": 2,
        "record_width_px": 1920, "record_height_px": 1080,
        "screen_width_px": 1920, "screen_height_px": 1080,
        "localization": "en-US", "platform": "PC",
        "input_mouse_convention": conv,
    }
    r = V2Result("s")
    _check_session_json(base, r)
    assert not any("dx_" in i or "dy_" in i for i in r.issues), r.issues


# ------------------------------- #4 reject detail vs the columns above it

_UNFIXABLE = [{"code": "CNT_BLACK_FROZEN", "blocking": True,
               "fixable": False, "params": {}, "evidence": "dead black"}]


def test_reject_detail_describes_the_same_population_as_the_columns(cfg):
    """It windowed on REJECTED-transition time while the columns window on
    upload COHORT, so the two disagreed in both directions. Here the
    reject happened INSIDE the window but its root uploaded before it —
    the columns (correctly) do not carry it, so the evidence section must
    not name it either."""
    led = Ledger(cfg.ledger_path)
    try:
        old = "2026-08-10T09-00-00Z_kamla_c_00000000000000e1"
        led.insert_session(
            session_id=old, game="kamla", operator_email="Op",
            player_email="old@x.com", drive_path="kamla/Op/old@x.com/x",
            drive_ctime="2026-08-10T09:00:00.000Z", md5_video="o",
            bytes_=1, state="DISCOVERED")
        led.set_reasons(old, _UNFIXABLE, 3)
        led.set_state(old, "REJECTED")
        # already counted and evidenced by an earlier sheet
        led.update(old, uploaded_reported_at="2026-08-11T00:00:00+00:00",
                   accepted_reported_at="2026-08-11T00:00:00+00:00")

        inw = "2026-08-15T09-00-00Z_kamla_c_00000000000000e2"
        led.insert_session(
            session_id=inw, game="kamla", operator_email="Op",
            player_email="new@x.com", drive_path="kamla/Op/new@x.com/x",
            drive_ctime="2026-08-15T09:00:00.000Z", md5_video="n",
            bytes_=1, state="DISCOVERED")
        led.set_reasons(inw, _UNFIXABLE, 3)
        led.set_state(inw, "REJECTED")

        _csv, md = reports.write_payment_sheet(
            cfg, led, datetime.now(C.IST),
            bounds=("2026-08-15T00:00:00+00:00",
                    "2026-08-16T00:00:00+00:00"))
        text = md.read_text()
        detail = text.split("## Reject detail", 1)[1].split("##", 1)[0]
        assert inw in detail, detail
        assert old not in detail, \
            "evidenced a reject the columns above do not carry"
        # and the columns agree: only the in-window player has a cell
        assert "new@x.com" in text and "old@x.com" not in text
    finally:
        led.close()


# ------------------------------------- #5 F3-deviation coaching wording

def test_f3_deviation_coaching_does_not_blame_the_first_uploader(cfg):
    """On an F3 deviation the copy we REJECT is the one with the EARLIER
    createdTime — the other was already in flight or already shipped. The
    generic note ('only the first upload counts') states the opposite of
    what happened, to the player who was in fact first."""
    led = Ledger(cfg.ledger_path)
    try:
        sid = "2026-08-15T09-00-00Z_kamla_c_00000000000000f5"
        led.insert_session(
            session_id=sid, game="kamla", operator_email="Op",
            player_email="first@x.com", drive_path="kamla/Op/first@x.com/x",
            drive_ctime="2026-08-15T09:00:00.000Z", md5_video="d",
            bytes_=1, state="DISCOVERED")
        led.set_reasons(sid, [{
            "code": "INT_DUP_CROSS", "blocking": True, "fixable": False,
            "params": {"f3_deviation": True},
            "evidence": "kept in-flight later upload other-sid — F3 "
                        "deviation: this copy has the earlier createdTime",
        }], 3)
        led.set_state(sid, "REJECTED")
        deliver.finalize_rejected(cfg, led, sid)
        note = (cfg.dossiers / sid / "coaching.md").read_text()
        assert "only the first upload counts" not in note, note
        assert "EARLIER upload time" in note
    finally:
        led.close()


def test_plain_duplicate_still_gets_the_plain_note(cfg):
    """The ordinary case — we kept the earlier copy — is unchanged."""
    led = Ledger(cfg.ledger_path)
    try:
        sid = "2026-08-15T09-00-00Z_kamla_c_00000000000000f6"
        led.insert_session(
            session_id=sid, game="kamla", operator_email="Op",
            player_email="later@x.com", drive_path="kamla/Op/later@x.com/x",
            drive_ctime="2026-08-15T09:00:00.000Z", md5_video="d",
            bytes_=1, state="DISCOVERED")
        led.set_reasons(sid, [{
            "code": "INT_DUP_CROSS", "blocking": True, "fixable": False,
            "params": {}, "evidence": "kept earlier upload other-sid",
        }], 3)
        led.set_state(sid, "REJECTED")
        deliver.finalize_rejected(cfg, led, sid)
        note = (cfg.dossiers / sid / "coaching.md").read_text()
        assert "only the first upload counts" in note
    finally:
        led.close()


def test_ingest_marks_the_deviation_branches(cfg, ledger, monkeypatch):
    """The flag is set where the deviation is decided, not sniffed out of
    the evidence text later."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    md5 = "d41d8cd98f00b204e9800998ecf8427e"
    # the LATER copy is already in flight
    inflight = "2026-08-15T12-00-00Z_kamla_c_00000000000000aa"
    ledger.insert_session(
        session_id=inflight, game="kamla", operator_email="Op",
        player_email="b@x.com", drive_path="kamla/Op/b@x.com/" + inflight,
        drive_ctime="2026-08-15T12:00:00.000Z", md5_video=md5, bytes_=1,
        state="DISCOVERED")
    ledger.set_state(inflight, "VALIDATING")
    # the EARLIER copy arrives now
    early = "2026-08-15T09-00-00Z_kamla_c_00000000000000ab"
    entries = make_session_entries(player="a@x.com", sid=early, md5=md5,
                                   ctime="2026-08-15T09:00:00.000Z")
    monkeypatch.setattr(ingest, "list_drive", lambda _c: entries)
    res = ingest.scan(cfg, ledger, entries)
    assert early in res.dup_cross, res
    reasons = json.loads(ledger.get(early)["reasons_json"])
    assert reasons[0]["code"] == "INT_DUP_CROSS"
    assert reasons[0]["params"].get("f3_deviation") is True, reasons
