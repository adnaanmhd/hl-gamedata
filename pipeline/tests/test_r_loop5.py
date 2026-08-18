"""r-loop 5 regression tests."""
from __future__ import annotations

import shutil
import time

import pytest

from pipeline import config as C
from pipeline import continuous as cont
from pipeline import fix
from pipeline import ingest, validate
from pipeline import run as runmod


def test_unclean_drain_returns_when_not_owning_the_process(cfg, monkeypatch):
    """r-loop 5: run_continuous is a LIBRARY call. When the drain grace
    expires with a lane still alive, the r-loop-4 fast path left by
    os._exit(0) — which, in-process, terminates the pytest interpreter
    with status 0: the suite stops mid-run and the shell reads success.
    Reproduced before this fix: pytest collected 2 tests, ran one, never
    ran a guaranteed failure, printed no summary and exited 0.

    install_signals is the ownership flag (only a process owner installs
    handlers; every test passes False), so a non-owning caller must
    RETURN normally instead. Before the fix this test could not fail —
    it killed the interpreter and took the rest of the suite with it.
    """
    monkeypatch.setattr(C, "CONT_DRAIN_GRACE_S", 0.5)

    def slow_list(_cfg):
        time.sleep(4)          # outlives the drain grace
        return []
    monkeypatch.setattr(ingest, "list_drive", slow_list)

    rc = cont.run_continuous(cfg, until_idle=True, send_telegram=False,
                             install_signals=False, max_wall_s=1.5)
    # reaching this line at all is the assertion: the interpreter lived
    assert rc == 0


# ------------------------------------ BLOCKER: DISCOVERED media is invisible

def _seed_disc(ledger, sid, *, state="DISCOVERED"):
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="op@x.com", player_email="p@x.com",
                          drive_path="kamla/op@x.com/p@x.com/" + sid,
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="a" * 32, bytes_=10, state=state)


def _age_discovered_event(ledger, sid, hours):
    """Backdate the DISCOVERED event so the reclaim/stuck age is measured
    from the audit, not from updated_at."""
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc)
          - timedelta(hours=hours)).isoformat(timespec="seconds")
    ledger.db.execute("DELETE FROM events WHERE session_id=?", (sid,))
    ledger.db.execute(
        "INSERT INTO events(session_id, from_state, to_state, ts, detail) "
        "VALUES(?,?,?,?,?)", (sid, "DOWNLOADING", "DISCOVERED", ts, "fail"))
    ledger.db.commit()


def test_local_count_sees_media_held_by_a_DISCOVERED_row(cfg, ledger):
    """r-loop 5 blocker: _download_one returns a row to DISCOVERED on every
    transient/zip_incomplete failure while ingest.download leaves what
    rclone already transferred in work/<sid>. LOCAL_STATES excludes
    DISCOVERED, so gigabytes were invisible to the ~40-session cap: the
    disk filled, _pick_download refused on the F7 low-water check, and cap
    pressure stayed silent so nothing named the cause. Same class as the
    QUARANTINED leak fixed in r-loop 3/4."""
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    _seed_disc(ledger, "s-nomedia")
    assert drv._local_count(ledger) == 0        # no work dir -> not counted

    _seed_disc(ledger, "s-media")
    (cfg.work / "s-media").mkdir(parents=True)
    assert drv._local_count(ledger) == 1

    # sid and sid-analysis are ONE session, not two
    (cfg.work / "s-media-analysis").mkdir(parents=True)
    assert drv._local_count(ledger) == 1


def test_sweep_reclaims_stale_DISCOVERED_media_but_spares_fresh(cfg, ledger):
    """The cap alone would stop intake with no way back (the r-loop-3
    QUARANTINED lesson), so aged media must be reclaimable. Age comes from
    the events audit: the 5-min retry bounces DISCOVERED->DOWNLOADING->
    DISCOVERED forever, re-stamping updated_at every time."""
    for sid, age in (("s-old", C.CONT_DISCOVERED_RECLAIM_H + 1),
                     ("s-new", 1)):
        _seed_disc(ledger, sid)
        (cfg.work / sid).mkdir(parents=True)
        (cfg.work / sid / "video.mp4").write_bytes(b"x")
        _age_discovered_event(ledger, sid, age)

    runmod._sweep_terminal_work(cfg, ledger)

    assert not (cfg.work / "s-old").exists(), \
        "stale DISCOVERED media must be reclaimed (rclone re-downloads)"
    assert (cfg.work / "s-new").exists(), \
        "a download failing for an hour must keep its partial transfer"
    # the row itself survives — only the bytes are reclaimed
    assert ledger.get("s-old")["state"] == "DISCOVERED"


def test_stuck_list_names_a_DISCOVERED_row_that_holds_media(cfg, ledger):
    """_stuck_lines excludes DISCOVERED by design (it is the unbounded
    'seen on Drive' population), which left the ONE failure that fills the
    disk with no ops surface at all — and at the flip the 3h digest is the
    only surface, since CONT_DAILY_REPORTS ships False."""
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    _seed_disc(ledger, "s-quiet")               # no media: stays invisible
    _age_discovered_event(ledger, "s-quiet", C.CONT_STUCK_H + 5)

    _seed_disc(ledger, "s-loud")
    (cfg.work / "s-loud").mkdir(parents=True)
    _age_discovered_event(ledger, "s-loud", C.CONT_STUCK_H + 5)

    lines, _n = drv._stuck_lines(ledger)
    text = " ".join(lines)
    assert "s-loud" in text
    assert "s-quiet" not in text


# ------------------------------ the gate must survive every cut-bearing plan

def _reason(code, **params):
    return {"code": code, "blocking": True, "fixable": True,
            "params": params}


_FROZEN = _reason("INP_FROZEN_ACTIONS", t0=300.0, t1=303.0)


@pytest.mark.parametrize("name,extra", [
    ("tail", [_reason("CNT_EDGE_NONGAMEPLAY", edge="tail", cut_at_s=300.0)]),
    ("mid", [_reason("CNT_MID_NONGAMEPLAY", cut=[100.0, 120.0])]),
    ("afk", [_reason("CNT_AFK", cut=[500.0, 540.0])]),
    ("head+tail", [_reason("CNT_EDGE_NONGAMEPLAY", edge="head", cut_at_s=8.0),
                   _reason("CNT_EDGE_NONGAMEPLAY", edge="tail",
                           cut_at_s=300.0)]),
])
def test_gate_survives_every_cut_bearing_plan(name, extra):
    """r-loop 5: r-loop 4 made the gate 'DEFERRED, not dropped' for a HEAD
    trim, but the three cut-bearing exits still returned with gate_windows
    discarded. Nothing carried the window forward — the sidecar was
    reverted, children start with reasons_json='[]', and cutter copies the
    parent's rows through — so a child could ship semantic actions
    recorded during a CONFIRMED freeze, the exact complaint the gate
    exists to prevent. Re-deriving it in the child is not equivalent: it
    costs a paid Gemini sweep plus one of the child's two attempts, and
    a 3s freeze can fall between the VLM's 4s samples and be missed."""
    plan = fix.plan_fixes([_FROZEN] + extra, game="kamla", has_raw=False)
    ids = [s[0] for s in plan["steps"]]
    assert "FIX_GATE_WINDOW" in ids, f"{name}: gate dropped -> {ids}"
    assert ids.index("FIX_GATE_WINDOW") < ids.index("FIX_CUT_SEGMENTS"), \
        f"{name}: gate must run on the parent's timeline, before the cut"


def test_cut_without_a_gate_is_unchanged():
    """Do not start emitting an empty gate step."""
    plan = fix.plan_fixes([_reason("CNT_MID_NONGAMEPLAY", cut=[100.0, 120.0])],
                          game="kamla", has_raw=False)
    assert [s[0] for s in plan["steps"]] == ["FIX_CUT_SEGMENTS"]


# ------------------- a rescinded holder must not delete its successor's lock

def test_rescinded_holder_does_not_disarm_the_successors_lock(tmp_path,
                                                              monkeypatch):
    """r-loop 5: the release was `if held: os.rmdir(lock)` — it removed
    whatever sat at the PATH, with no check that it was still ours. The
    staleness breaker rescinds a slow holder's lock by renaming it aside;
    that holder's `held` is still True, so its finally deleted the
    SUCCESSOR's lock and a third racer walked in. Two concurrent
    read-modify-writes lose a {"shift_us": ...} entry, after which
    _applied_shift_us returns 0, qa-v2 re-bins raw mouse at head offset 0,
    and the session takes a spurious SYN_TS_NOT_PTS — one of only two fix
    attempts and one paid Gemini sweep, on a clean session."""
    import os as _os
    import threading as _th

    report = tmp_path / "translation_report.json"
    lock = report.parent / (report.name + ".lock")

    in_section = _th.Event()
    may_finish = _th.Event()
    real_replace = _os.replace

    def slow_replace(src, dst):
        in_section.set()
        may_finish.wait(5)
        return real_replace(src, dst)
    monkeypatch.setattr(validate.os, "replace", slow_replace)

    t = _th.Thread(target=validate._locked_report_update,
                   args=(report, "sessA", {"shift_us": 1}), daemon=True)
    t.start()
    assert in_section.wait(5), "holder never entered its critical section"

    # the breaker rescinds A's lock, and a successor takes a fresh one
    grave = lock.with_name(lock.name + ".stale-test")
    _os.rename(lock, grave)
    shutil.rmtree(grave, ignore_errors=True)
    # Deliberately an EMPTY dir -- exactly what a bare os.mkdir successor
    # leaves. Stamping it would make the OLD code's os.rmdir fail with
    # ENOTEMPTY and the lock would survive for the wrong reason, so the
    # test would pass against the very bug it exists to pin.
    _os.mkdir(lock)

    may_finish.set()
    t.join(5)
    assert not t.is_alive()

    assert lock.exists(), "A deleted the successor's lock — mutex disarmed"


# --------------- an unmapped FAIL must not upgrade a repairable verdict

def _codes(issues, has_raw=False):
    rs: list[dict] = []
    validate._map_qa_issues(issues, rs, has_raw)
    return {r["code"]: (r["blocking"], r["fixable"]) for r in rs}


def test_unmapped_fail_is_advisory_beside_a_repairable_one():
    """r-loop 5: QA_FAIL_UNMAPPED is blocking+unfixable when has_raw is
    False, and ONE unfixable blocking reason forces bin 3 — so a session
    that also raised a blocking+fixable reason in the same qa pass was
    REJECTED with zero fix attempts, though the planned FIX_ROWS_SURGERY
    would have cleared both FAILs. Player unpaid, for a repairable file."""
    got = _codes(["FAIL: row count 1801 != session.json frame_count 1800",
                  "FAIL: frame_id column unparseable (row 1801)"])
    assert got["STR_ROWS_MISMATCH"] == (True, True)
    assert got["QA_FAIL_UNMAPPED"][0] is False, \
        "must not upgrade a repairable bin-2 verdict into a bin-3 reject"


def test_unmapped_fail_alone_still_blocks():
    """Keep the r-loop-4 reasoning: mapping frame_id-unparseable outright
    would plan a surgery that no-ops, burning both attempts and two paid
    VLM sweeps before rejecting anyway. Alone, it must still block."""
    got = _codes(["FAIL: frame_id column unparseable (row 1801)"])
    assert got["QA_FAIL_UNMAPPED"] == (True, False)


def test_unmapped_fail_with_sidecars_stays_fixable():
    """R3: retranslate is the universal strong fix when raw exists."""
    got = _codes(["FAIL: frame_id column unparseable (row 1801)"], True)
    assert got["QA_FAIL_UNMAPPED"] == (True, True)


# ------------- RULED (Adnaan 2026-08-18): the trigger rides the MEASURED span

def _frozen_window(t0, t1, vlm_action_frames):
    return {"t0": t0, "t1": t1, "labels": ["pause"], "tier": "high",
            "gating": True, "n_samples": 3,
            "inputs": {"action_frames": vlm_action_frames},
            "stillness_ratio": 0.05}


def _map_one(w, refined_span, refined_af, dur=300.0):
    rep = {"duration_s": dur, "vlm": {"windows": [w]}}
    aux = {"refined": {(w["t0"], w["t1"]): refined_span},
           "refined_action_frames": {(w["t0"], w["t1"]): refined_af},
           "extra_windows": [], "afk_windows": []}
    reasons, advisories = [], []
    validate._map_windows(rep, aux, reasons, advisories)
    return reasons, advisories


def test_same_frozen_run_gates_identically_across_vlm_boundary_drift():
    """RULED (Adnaan 2026-08-18, r-loop-3 #6). analyze_sample._windows sets
    window bounds as MIDPOINTS between VLM sample times, so both the
    trigger and the gate span were derived from VLM label boundaries —
    which are not stable across passes. One boundary sample flipping label
    moves a bound 15-30 frames; the recheck recounts, re-raises
    INP_FROZEN_ACTIONS, spends attempt 2 re-gating and rejects on pass 3.
    The measured frozen run is identical in both passes, so the verdict
    and the gate params must be identical too."""
    span = (60.0, 63.0)          # what the scanner actually measured
    a, _ = _map_one(_frozen_window(59.5, 63.5, 9), span, 4)
    b, _ = _map_one(_frozen_window(59.0, 64.0, 14), span, 4)

    assert [r["code"] for r in a] == ["INP_FROZEN_ACTIONS"]
    assert [r["code"] for r in b] == ["INP_FROZEN_ACTIONS"]
    assert a[0]["params"] == b[0]["params"] == {"t0": 60.0, "t1": 63.0}, \
        "gate params must follow the measurement, not the VLM boundary"


def test_action_on_a_moving_frame_outside_the_run_is_not_counted():
    """'Frozen' is a MEASUREMENT. An action on a MOVING frame outside the
    VLM's fuzzy edge is real gameplay: counting it is a false positive and
    blanking it destroys real data. The VLM stays the classifier, not the
    boundary-finder."""
    # the VLM window claims 5 action frames; none of them are inside the
    # measured frozen run
    reasons, advisories = _map_one(_frozen_window(59.0, 64.0, 5),
                                   (60.0, 63.0), 0)
    assert [r["code"] for r in reasons] == [], \
        "an action outside the measured freeze must not raise a reason"
    assert any("no inputs inside" in a for a in advisories)
