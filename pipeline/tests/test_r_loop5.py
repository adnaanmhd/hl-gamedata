"""r-loop 5 regression tests."""
from __future__ import annotations

import json
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


def _age_discovered_event(ledger, sid, hours, *, seen_days_before=3):
    """Write a REALISTIC event history and backdate the first failure.

    r-loop 6: the age must come from the first DISCOVERED event AFTER a
    DOWNLOADING event — the first FAILURE — not from first sight on Drive.
    So the history always includes the ingest.scan insert (much older, and
    it must NOT set the clock), then the first download attempt, then the
    failure, then a retry bounce that must not reset the clock either.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    fail = now - timedelta(hours=hours)
    seen = fail - timedelta(days=seen_days_before)
    iso = lambda d: d.isoformat(timespec="seconds")
    ledger.db.execute("DELETE FROM events WHERE session_id=?", (sid,))
    rows = [
        (sid, None, "DISCOVERED", iso(seen), "scanned"),
        (sid, "DISCOVERED", "DOWNLOADING", iso(fail - timedelta(minutes=5)),
         "claimed by D"),
        (sid, "DOWNLOADING", "DISCOVERED", iso(fail), "download failed"),
        # a later retry bounce must not reset the clock
        (sid, "DISCOVERED", "DOWNLOADING", iso(fail + timedelta(minutes=5)),
         "claimed by D"),
        (sid, "DOWNLOADING", "DISCOVERED", iso(fail + timedelta(minutes=10)),
         "download failed"),
    ]
    ledger.db.executemany(
        "INSERT INTO events(session_id, from_state, to_state, ts, detail) "
        "VALUES(?,?,?,?,?)", rows)
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

    # an EMPTY dir is not media (r-loop 6): ingest.download creates
    # work/<sid> BEFORE the first rclone attempt, so a transfer of zero
    # bytes still leaves a dir, and scoring it let one transient outage
    # fill the whole cap with an empty disk
    _seed_disc(ledger, "s-empty")
    (cfg.work / "s-empty").mkdir(parents=True)
    assert drv._local_count(ledger) == 0

    _seed_disc(ledger, "s-media")
    (cfg.work / "s-media").mkdir(parents=True)
    (cfg.work / "s-media" / "video.mp4").write_bytes(b"x")
    assert drv._local_count(ledger) == 1

    # sid and sid-analysis are ONE session, not two
    (cfg.work / "s-media-analysis").mkdir(parents=True)
    (cfg.work / "s-media-analysis" / "report.json").write_bytes(b"{}")
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
    # BYTES, not just a dir (r-loop 7): ingest.download mkdirs work/<sid>
    # BEFORE the first rclone attempt, so an empty dir is what a download
    # that transferred NOTHING leaves behind. The cap already scored it as
    # 0 media; labelling it DISCOVERED(media) in the digest sent an
    # operator hunting for disk that was never used. The condition this
    # surface exists for is a row HOLDING bytes.
    (cfg.work / "s-loud" / "video.mp4").write_bytes(b"x" * 16)
    _age_discovered_event(ledger, "s-loud", C.CONT_STUCK_H + 5)

    _seed_disc(ledger, "s-empty")               # dir, but zero bytes
    (cfg.work / "s-empty").mkdir(parents=True)
    _age_discovered_event(ledger, "s-empty", C.CONT_STUCK_H + 5)

    lines, _n = drv._stuck_lines(ledger)
    text = " ".join(lines)
    assert "s-loud" in text
    assert "s-quiet" not in text
    assert "s-empty" not in text


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


# ---------- content bars must be blind to rows the PIPELINE blanked ----------

def _inv(distinct, actions, key_frames=5, rows=6000):
    return {"rows": rows, "distinct_actions": distinct,
            "actions": {a: 10 for a in actions},
            "key_frames": key_frames, "btn_frames": 3, "motion_frames": 900,
            "irregular_pct": 0.0}


def _map_full(inv, gate_destroyed=None):
    rep = {"duration_s": 210.0, "inventory": inv, "qa_issues": [],
           "vlm": {"samples": [{"t": 1.0, "label": "gameplay"}],
                   "windows": []}}
    aux = {"refined": {}, "extra_windows": [], "afk_windows": [],
           "has_raw": False, "vlm_required": True, "video_active": True,
           "gate_destroyed": gate_destroyed or {"actions": [],
                                                "key_frames": 0}}
    return validate.map_reasons(rep, aux)


def test_actions_few_not_raised_when_the_gate_destroyed_the_action():
    """r-loop 5: FIX_GATE_WINDOW blanks input_keys AND input_actions, then
    the session is FULLY re-validated and the inventory is recomputed from
    the gated frames.csv — with nothing subtracting the rows the pipeline
    itself emptied. A session whose 3rd action occurs only inside a frozen
    context (the OW Observatory terminal is an unmodelled one) came back
    with 2 and was rejected on a blocking, UNFIXABLE reason, with
    coaching.md telling the player to 'play actively' for a stretch we
    erased."""
    res = _map_full(_inv(2, ["look", "thrust"]),
                    {"actions": ["interact"], "key_frames": 0})
    assert "CNT_ACTIONS_FEW" not in [r["code"] for r in res.reasons]
    assert any("gated" in a for a in res.advisories)


def test_actions_few_still_raised_without_a_gate():
    """A genuine 2-action session must still be rejected."""
    res = _map_full(_inv(2, ["look", "thrust"]))
    assert "CNT_ACTIONS_FEW" in [r["code"] for r in res.reasons]


def test_actions_few_still_raised_when_the_gate_does_not_close_the_gap():
    """Restoring what we blanked must still leave the session under the
    bar to be rejected — the carve-out is attribution, not amnesty."""
    res = _map_full(_inv(1, ["look"]), {"actions": ["look"],
                                        "key_frames": 0})
    assert "CNT_ACTIONS_FEW" in [r["code"] for r in res.reasons]


def test_keys_missing_not_raised_when_the_gate_destroyed_the_key_frames():
    """'re-record (never fabricate)' must never be said about rows we
    blanked ourselves — reachable for a mouse-heavy session whose only key
    presses fall inside frozen contexts."""
    res = _map_full(_inv(3, ["look", "thrust", "interact"], key_frames=0),
                    {"actions": [], "key_frames": 12})
    assert "INP_KEYS_MISSING" not in [r["code"] for r in res.reasons]


def test_keys_missing_still_raised_without_a_gate():
    res = _map_full(_inv(3, ["look", "thrust", "interact"], key_frames=0))
    assert "INP_KEYS_MISSING" in [r["code"] for r in res.reasons]


def test_gate_destroyed_reads_a_log_written_by_the_REAL_writer(tmp_path):
    """r-loop 6 blocker: this test used to hand-build a FLAT list, but
    fix._append_fixlog writes ONE record per apply_fixes call with the
    per-fix entries NESTED under "fixes". The reader parsed the top level,
    so it matched nothing, aux["gate_destroyed"] was ALWAYS empty in
    production, and the whole r-loop-5 #11 carve-out was dead code — while
    this test passed against a shape production never produces.

    Go through the real writer, so reader and writer cannot diverge again.
    """
    fix._append_fixlog(tmp_path, [
        {"fix": "FIX_KEY_HYGIENE", "params": {}, "ok": True, "note": {}},
        {"fix": "FIX_GATE_WINDOW", "params": {}, "ok": True,
         "note": {"gated_frames": 7,
                  "destroyed": {"actions": ["interact"], "key_frames": 4}}},
    ])
    fix._append_fixlog(tmp_path, [
        {"fix": "FIX_GATE_WINDOW", "params": {}, "ok": True,
         "note": {"destroyed": {"actions": ["map"], "key_frames": 2}}},
        {"fix": "FIX_GATE_WINDOW", "params": {}, "ok": False,
         "note": {"destroyed": {"actions": ["ignored"], "key_frames": 9}}},
    ])
    assert validate._gate_destroyed(tmp_path) == {
        "actions": ["interact", "map"], "key_frames": 6}


def test_gate_destroyed_tolerates_a_missing_or_damaged_log(tmp_path):
    """The fixlog is the evidence of record; a missing or corrupt one must
    degrade to today's behaviour, never crash validation."""
    assert validate._gate_destroyed(tmp_path) == {"actions": [],
                                                  "key_frames": 0}
    (tmp_path / "fixlog.json").write_text("{not json")
    assert validate._gate_destroyed(tmp_path) == {"actions": [],
                                                  "key_frames": 0}
    (tmp_path / "fixlog.json").write_text(json.dumps({"not": "a list"}))
    assert validate._gate_destroyed(tmp_path) == {"actions": [],
                                                  "key_frames": 0}
    # a legacy/hand-written FLAT record must still be read
    (tmp_path / "fixlog.json").write_text(json.dumps([
        {"fix": "FIX_GATE_WINDOW", "ok": True,
         "note": {"destroyed": {"actions": ["flat"], "key_frames": 1}}}]))
    assert validate._gate_destroyed(tmp_path) == {"actions": ["flat"],
                                                  "key_frames": 1}


# ------------------------------- the batch driver must honour the flag too

def test_batch_driver_refuses_when_the_continuous_flag_is_on(cfg,
                                                             monkeypatch):
    """r-loop 5: PIPELINE_CONTINUOUS was read in exactly ONE place
    (run_continuous), so it was a one-way interlock — it stopped the
    continuous unit when False, but nothing stopped the BATCH driver when
    True. The run lock only stops them running at the same instant, not
    the batch driver taking over during a continuous restart window: both
    armed, the next tick wins run.lock, hl-pipeline.service holds it for
    hours (Type=oneshot, TimeoutStartSec=infinity), hl-continuous burns
    StartLimitBurst and enters 'failed' with an alert naming the wrong
    cause, and production silently runs on the batch driver."""
    monkeypatch.setattr(C, "PIPELINE_CONTINUOUS", True)
    called = {"lock": False}
    monkeypatch.setattr(runmod, "acquire_lock",
                        lambda _c: called.__setitem__("lock", True) or True)
    assert runmod.run(cfg, send_telegram=False) == 0
    assert not called["lock"], "must decline BEFORE taking the run lock"


# ------------------- the digest must not report resolutions as quarantines

def test_digest_counts_quarantine_ENTRIES_not_same_state_stamps(
        cfg, ledger, monkeypatch):
    """r-loop 5: ingest.scan's bad-path chase writes a
    QUARANTINED->QUARANTINED transition for every INT_PATH row whose
    folder has vanished from the listing — which happens precisely when an
    operator has CORRECTED a badly-named folder. The digest printed those
    resolutions as fresh quarantines, and during the canary that reads as
    the new driver quarantining sessions: exactly the signal that would
    trigger a rollback, on the only ops surface there is (the 3h digest,
    since CONT_DAILY_REPORTS ships False at the flip)."""
    from datetime import datetime, timedelta, timezone
    from pipeline import reports, telegram
    ts = (datetime.now(timezone.utc)
          - timedelta(minutes=5)).isoformat(timespec="seconds")
    for sid, frm in (("real", "INGESTED"),        # a true entry
                     ("fixed1", "QUARANTINED"),   # operator FIXED the path
                     ("fixed2", "QUARANTINED")):
        ledger.db.execute(
            "INSERT INTO events(session_id, from_state, to_state, ts, "
            "detail) VALUES(?,?,?,?,?)", (sid, frm, "QUARANTINED", ts, ""))
    ledger.db.commit()

    seen = {}
    monkeypatch.setattr(reports, "build_digest_message",
                        lambda d, p: seen.update(q=d.quarantined_n) or "x")
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: None)
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    drv._send_digest(ledger)

    assert seen.get("q") == 1, \
        f"operator fixes must not read as new quarantines (got {seen})"


# ---------------- the fix plan must be built with the REROUTED game

def test_fix_plan_uses_the_rerouted_game_not_the_drive_folder(cfg, ledger,
                                                              monkeypatch):
    """r-loop 5: _fix_one planned with row['game'] — the DRIVE-FOLDER
    game, the very value STR_GAME_MISMATCH says is wrong. plan_fixes
    branches on game, so an Outer Wilds session uploaded into the kamla/
    tree had INP_FANOUT classified UNFIXABLE and was rejected outright,
    although FIX_REROUTE_GAME + FIX_ACTIONS_CONTEXT is the designed fix."""
    sid = "s-misfiled"
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="op@x.com", player_email="p@x.com",
                          drive_path="kamla/op@x.com/p@x.com/" + sid,
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="a" * 32, bytes_=10, state="FIX_QUEUED")
    reasons = [{"code": "STR_GAME_MISMATCH", "blocking": True,
                "fixable": True, "params": {"actual": "outer_wilds"}},
               {"code": "INP_FANOUT", "blocking": True, "fixable": True,
                "params": {}}]
    ledger.update(sid, reasons_json=json.dumps(reasons))
    (cfg.work / sid).mkdir(parents=True)

    seen = {}
    monkeypatch.setattr(fix, "apply_fixes",
                        lambda *a, **k: seen.update(game=k.get("game"))
                        or {"applied": [], "children": None, "error": None})
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    drv._fix_one(ledger, sid)

    assert ledger.get(sid)["state"] != "REJECTED", \
        "a misfiled OW session must be rerouted, not rejected unfixable"
    assert seen.get("game") == "outer_wilds"
    assert ledger.get(sid)["game"] == "outer_wilds"


# ------------------- the fix path must not render a video-sized rrd

def test_retrim_dispatch_skips_the_rrd_render(tmp_path, monkeypatch):
    """r-loop 5: in the fix path out_dir IS the work dir, and NOTHING
    reads that rrd — translator/v2.py only checks session.rrd EXISTS
    (which is why ingest.download touches a 0-byte stub) and
    deliver.stage_session regenerates it inside the stage dir. The render
    embeds the whole clip via rr.AssetVideo and logs 5 entries per frame,
    so it came out roughly VIDEO-SIZED: minutes inside the runner's gate
    slot, and ~2x that session's bytes on disk against a cap that counts
    SESSIONS as its bytes bound."""
    seen = {}

    class _Tool:
        @staticmethod
        def retrim(work, head_s, out, **kw):
            seen.update(kw)
            return {"head_cut_s": head_s}
    monkeypatch.setattr(fix, "_retrim_tool", lambda: _Tool)
    fix._dispatch("FIX_RETRIM_HEAD", {"head_s": 7.0}, tmp_path, "kamla",
                  tmp_path)
    assert seen.get("make_rrd") is False


# --------------- keybind.json / metadata.json are untrusted player files

def test_keybind_with_non_utf8_bytes_does_not_raise(tmp_path):
    """r-loop 4 hardened inputs.jsonl with errors='replace' and left its
    two siblings strict. keybind.json consists ENTIRELY of key names, so a
    single cp1252 byte from a non-US layout raised straight out of
    FIX_RETRANSLATE: apply_fixes recorded the step failed, the reason
    survived revalidation untouched, attempt 2 failed identically, and the
    session was REJECTED 'fix retries exhausted' with only a bare
    fix-failed marker for ops."""
    from translator.translate import resolve_keybind
    p = tmp_path / "keybind.json"
    p.write_bytes('{"move_up": "w", "caf\xe9": "e"}'.encode("cp1252"))
    kb = resolve_keybind(keybind_path=p, game_name="Kamla")
    assert isinstance(kb, dict) and kb          # parsed, did not raise


def test_unreadable_keybind_falls_back_to_the_builtin(tmp_path):
    """Garbled beyond parsing must fall back, never hand
    _as_semantic_to_literal something without .values()."""
    from translator.keybinds import KEYBINDS
    from translator.translate import resolve_keybind
    p = tmp_path / "keybind.json"
    p.write_text("{ not json at all")
    assert resolve_keybind(keybind_path=p,
                           game_name="Kamla") == KEYBINDS["kamla"]
    p.write_text('["a", "list", "not", "a", "dict"]')
    assert resolve_keybind(keybind_path=p,
                           game_name="Kamla") == KEYBINDS["kamla"]


# ------------- backpressure must see the outage shape that stalls the pool

def test_transport_failure_writes_a_pressure_event(tmp_path, monkeypatch):
    """r-loop 5: _pressure was called ONLY from the HTTPError 429/5xx
    branch. URLError / TimeoutError / SSL / ConnectionReset /
    HTTPException / JSONDecodeError slept the same exponential backoff and
    wrote NOTHING, so p429_per_min stayed 0 and the step-down arm could
    never fire — while autoscale rule 3 (cpu low, queue deep) is SATISFIED
    by exactly the state a backoff storm creates, because every worker is
    asleep in time.sleep() rather than burning CPU. The pool scaled UP
    into the outage until CONT_POOL_MAX, each runner paying a doomed sweep
    -> VLMError -> HOLD_VLM, which counts in LOCAL_STATES and so stopped
    intake at the media cap."""
    import urllib.error
    from pipeline import vlm as vlmmod

    path = tmp_path / "vlm-pressure.jsonl"
    monkeypatch.setattr(vlmmod, "_pressure_path", path)
    monkeypatch.setattr(C, "VLM_MAX_TRIES", 1)

    def post_dead(url, headers, body, timeout_s=180):
        raise urllib.error.URLError("connection reset by peer")
    monkeypatch.setattr(vlmmod, "_post", post_dead)
    with pytest.raises(vlmmod.VLMError):
        vlmmod.generate("k" * 20, "gemini-3.7-flash", [{"text": "hi"}])

    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert lines, "a transport outage must reach the backpressure channel"
    assert all("k" * 20 not in json.dumps(ev) for ev in lines)  # no secrets


# ---------------- a fresh upload must not merge into the old evidence

def test_archive_dossier_preserves_the_prior_generation(tmp_path, ledger):
    """r-loop 5: the QUARANTINED-path heal says it is 'a FRESH-upload
    event: reset the slot like supersede does' and duplicated every part
    of supersede EXCEPT the dossier archive. fix._append_fixlog APPENDS to
    fixlog.json while _write_verdict overwrites verdict.json, so the new
    pass's audit trail silently contained fixes applied to bytes that were
    no longer there — and a payment dispute is adjudicated against exactly
    that record (design §13)."""
    root = tmp_path / "dossiers"
    d = root / "s1"
    d.mkdir(parents=True)
    (d / "verdict.json").write_text('{"bin": 3}')
    (d / "fixlog.json").write_text('[{"fix": "FIX_KEY_HYGIENE"}]')

    ledger.archive_dossier("s1", root)

    assert not (d / "verdict.json").exists(), "prior generation left in place"
    hist = list((d / "history").iterdir())
    assert len(hist) == 1
    assert (hist[0] / "verdict.json").read_text() == '{"bin": 3}'
    assert (hist[0] / "fixlog.json").exists()

    # idempotent / safe on a session that has no dossier yet
    ledger.archive_dossier("never-seen", root)


def test_heal_actually_archives_the_dossier(cfg, ledger):
    """Behavioural, not a source grep (r-loop 6): the previous version
    asserted "archive_dossier" appeared in ingest.scan's source, so a
    wrong-argument regression at the call site would have shipped green.
    Drive the real heal branch and check the evidence actually moved."""
    from pipeline import ingest as ingestmod
    from pipeline.tests.conftest import make_session_entries
    sid = "2026-08-15T08-30-00Z_kamla_c_00000000000000bf"
    ledger.insert_session(
        session_id=sid, game="", operator_email="", player_email="",
        drive_path=f"kamla/Op/badplayer/{sid}", drive_ctime="",
        md5_video="", bytes_=0, state="QUARANTINED",
        detail="player folder 'badplayer' is not an email")
    ledger.set_reasons(sid, [
        {"code": "INT_PATH", "blocking": True, "fixable": False,
         "params": {}, "evidence": "player folder is not an email"}], 3)

    # evidence from the FIRST upload's pass
    dossier = cfg.dossiers / sid
    dossier.mkdir(parents=True)
    (dossier / "verdict.json").write_text('{"bin": 3, "generation": 1}')
    (dossier / "fixlog.json").write_text('[{"ts": "t1", "fixes": []}]')

    ingestmod.scan(cfg, ledger,
                   entries=make_session_entries(sid=sid, player="ok@x.com"))

    assert ledger.get(sid)["state"] == "DISCOVERED"      # healed
    assert not (dossier / "verdict.json").exists(), \
        "the prior generation must not stay in place to be overwritten"
    hist = list((dossier / "history").iterdir())
    assert len(hist) == 1
    assert (hist[0] / "verdict.json").read_text() == \
        '{"bin": 3, "generation": 1}'
    assert (hist[0] / "fixlog.json").exists(), \
        "fixlog must be archived too — fix._append_fixlog APPENDS"


# ------------- a failed daily send must not regenerate the sheet every 20s

def test_failed_daily_send_backs_off_instead_of_rebuilding_every_tick(
        cfg, ledger, monkeypatch):
    """r-loop 5: the H lane body runs every ~20s, and
    send_daily_report_if_due BUILDS the sheet before it sends, returning
    False without writing the marker or the anchor when Telegram fails.
    Under the batch driver it was reached at most once per batch drain;
    under the continuous driver a Telegram outage after 14:00 IST turned
    it into ~180 full sheet generations an hour — each a whole-table
    player_rollup + build_folder_issues scan, each rewriting
    reports/<day>/payment-<day>.csv NON-atomically, so an scp of that path
    during the payment endgame could capture a truncated file."""
    monkeypatch.setattr(C, "CONT_DAILY_REPORTS", True)
    calls = {"n": 0}
    monkeypatch.setattr(runmod, "send_daily_report_if_due",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(runmod, "send_folder_issues_if_due",
                        lambda *a, **k: None)

    clock = [1000.0]
    captured = {}
    monkeypatch.setattr(
        cont.ContinuousDriver, "_lane_loop",
        lambda self, name, body, idle_s=0: captured.__setitem__("body", body))
    drv = cont.ContinuousDriver(cfg, send_telegram=True,
                                clocks=cont._Clocks(mono=lambda: clock[0]))
    monkeypatch.setattr(drv, "_send_digest", lambda _led: None)
    drv._housekeeping_thread()
    body = captured["body"]
    # isolate the duty under test: push the autoscale and hourly-sweep
    # cadences out of reach so this exercises only the daily send
    drv._next_scale = clock[0] + 1e9
    drv._next_sweep = clock[0] + 1e9
    drv.stop.set()          # body ends with stop.wait(20) — don't sleep it

    body(ledger)
    for _ in range(5):                 # five more 20s ticks
        clock[0] += 20
        body(ledger)
    assert calls["n"] == 1, \
        f"a failed send must back off, not rebuild every tick ({calls})"

    clock[0] += C.CONT_DAILY_RETRY_S + 1
    body(ledger)
    assert calls["n"] == 2, "it must still retry once the backoff elapses"


# --------- r-loop-4's delivery aging is untested: pin it (mutation survived)

def test_stuck_list_ages_undelivered_rows_from_the_events_audit(cfg, ledger):
    """r-loop 4 taught the stuck list to age READY/PACKAGED/UPLOADED from
    the events audit, because every delivery retry bumps updated_at via
    deliver_session's ledger.update(rrd_sampled=...) — so a permanently
    failing upload never matched the updated_at<cut query and never
    appeared. That fix had NO regression test: deleting the loop left the
    suite green (r-loop 5), and the `rows` comprehension filters those
    three states out, so they become structurally unreachable again.

    At the flip the 3h digest is the only ops surface (CONT_DAILY_REPORTS
    ships False), so the session is invisible until someone reads the
    ledger by hand."""
    from datetime import datetime, timedelta, timezone
    sid = "s-stuck-ready"
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="op@x.com", player_email="p@x.com",
                          drive_path="kamla/op@x.com/p@x.com/" + sid,
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="a" * 32, bytes_=10, state="READY")
    old = (datetime.now(timezone.utc)
           - timedelta(hours=C.CONT_STUCK_H + 4)).isoformat(
               timespec="seconds")
    ledger.db.execute("DELETE FROM events WHERE session_id=?", (sid,))
    ledger.db.execute(
        "INSERT INTO events(session_id, from_state, to_state, ts, detail) "
        "VALUES(?,?,?,?,?)", (sid, "VALIDATING", "READY", old, "staged"))
    # the retry loop keeps updated_at FRESH — this is the whole trap
    ledger.update(sid, rrd_sampled=1)
    ledger.db.commit()

    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    lines, total = drv._stuck_lines(ledger)
    assert any(sid in ln for ln in lines), \
        f"a forever-retrying delivery must appear in the stuck list: {lines}"


# ------- R1's ruled-KEPT VLM label+confidence filter is executed by no test

def _statics_aux(tmp_path, monkeypatch, *, label, conf,
                 action_at=None, win=(3.0, 5.0), fps=10.0, dur=10.0):
    """Drive _build_aux's scanner-statics arm through the module seams."""
    from translator import video as Vmod
    from translator.v2 import V2_FRAME_COLS
    from pipeline import scanner as scannermod
    from pipeline import vlm as vlmmod

    n = int(dur * fps)
    times = [round(i / fps, 3) for i in range(n)]
    tl = scannermod.MotionTimeline(
        n_frames=n, fps=fps, duration_s=dur, times_s=times,
        diffs=[5.0] * (n - 1), luma=[100.0] * n)

    work = tmp_path / "w"
    work.mkdir(parents=True)
    (work / "video.mp4").write_bytes(b"x")
    col = {c: i for i, c in enumerate(V2_FRAME_COLS)}
    rows = []
    for i in range(n):
        r = [""] * len(V2_FRAME_COLS)
        r[col["frame_id"]] = str(i)
        r[col["timestamp_ms"]] = str(int(times[i] * 1000))
        if action_at is not None and i == action_at:
            r[col["input_actions"]] = "interact"
            r[col["input_keys"]] = "e"
        rows.append(",".join(r))
    (work / "frames.csv").write_text(
        ",".join(V2_FRAME_COLS) + "\n" + "\n".join(rows) + "\n")

    monkeypatch.setattr(Vmod, "frame_pts", lambda p: [
        int(t * 1e6) for t in times])
    monkeypatch.setattr(scannermod, "available", lambda: True)
    monkeypatch.setattr(scannermod, "scan_video", lambda p, pts_us=None: tl)
    monkeypatch.setattr(scannermod, "static_windows",
                        lambda *a, **k: [win])
    monkeypatch.setattr(scannermod, "zero_input_runs", lambda *a, **k: [])
    mid = round((win[0] + win[1]) / 2, 2)
    monkeypatch.setattr(vlmmod, "classify_stills",
                        lambda *a, **k: [{"t": mid, "label": label,
                                          "conf": conf}])

    class _Eng:
        class FrameGrabber:
            def __init__(self, *a, **k): pass
            def close(self): pass
    monkeypatch.setattr(validate, "load_engine", lambda: _Eng)

    rep = {"game_title": "Kamla", "duration_s": dur,
           "vlm": {"samples": [{"t": 1.0, "label": "gameplay"}],
                   "windows": []}}
    return validate._build_aux(work, rep, object(), gemini_key="k",
                               gemini_model="m", vlm_expected=True)


def test_low_confidence_gameplay_still_produces_no_extra_window(tmp_path,
                                                                monkeypatch):
    """Ruling R1 KEEPS the VLM label+confidence filter. Replacing the whole
    condition with `if True:` left the suite green, and inserting a raise
    before load_engine failed exactly ONE unrelated test — so the statics
    arm, the AFK carve-out and the inclusive-end count were executed by no
    test at all (r-loop 5). Widening the filter while tuning R1 would make
    every measured-still window an acted-on extra_window: a >5s window the
    model calls 'gameplay' would raise CNT_MID_NONGAMEPLAY and cut a child
    out of real play, re-arming the exact cascade R1 exists to stop."""
    aux = _statics_aux(tmp_path, monkeypatch, label="gameplay", conf="high")
    assert aux["extra_windows"] == []
    aux = _statics_aux(tmp_path / "b", monkeypatch, label="loading",
                       conf="low")
    assert aux["extra_windows"] == [], "low confidence must not act"


def test_high_confidence_nongameplay_label_produces_an_extra_window(
        tmp_path, monkeypatch):
    aux = _statics_aux(tmp_path, monkeypatch, label="loading", conf="high")
    assert len(aux["extra_windows"]) == 1
    assert aux["extra_windows"][0]["label"] == "loading"


def test_action_frame_count_includes_the_window_end_frame(tmp_path,
                                                          monkeypatch):
    """r-loop 3: gate.gate_windows selects rows on `t0 <= t <= t1` and the
    engine's rows_in_window uses bisect_right, so a half-open count missed
    the frame AT the window end — and the input that ENDS a freeze (the
    click dismissing the loading screen) lands exactly there, so the
    window was reported 'kept (no inputs inside)' and shipped an action
    recorded on a non-gameplay frame."""
    aux = _statics_aux(tmp_path, monkeypatch, label="loading", conf="high",
                       win=(3.0, 5.0), action_at=50)   # 5.0s at 10fps
    assert aux["extra_windows"][0]["action_frames"] == 1


def test_capped_driver_can_still_resume_a_DISCOVERED_row_holding_media(
        cfg, ledger, monkeypatch):
    """r-loop 6 blocker: r-loop 5 taught _local_count to score DISCOVERED
    rows holding media, but gave them no carve-out. _download_one returns
    EVERY failure to DISCOVERED (never DOWNLOADING), so their only route
    back is next_batch — which sits behind the cap return. The rows
    holding the cap were exactly the rows that had to be re-picked to
    release it, so one transient rclone outage across a full intake wave
    stopped ALL intake until the 12h reclaim, with the disk empty and F7
    never firing."""
    monkeypatch.setattr(C, "CONT_MEDIA_CAP_SESSIONS", 2)
    for n in range(2):
        sid = f"s-held{n}"
        _seed_disc(ledger, sid)
        (cfg.work / sid).mkdir(parents=True)
        (cfg.work / sid / "part001.zip").write_bytes(b"partial")
    _seed_disc(ledger, "s-fresh")          # no media: must stay gated

    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    assert drv._local_count(ledger) == 2   # cap is full

    picked = drv._pick_download(ledger)
    assert picked in ("s-held0", "s-held1"), \
        f"a capped driver must still resume its own held media, got {picked}"
    assert ledger.get(picked)["state"] == "DOWNLOADING"


def test_reclaim_clock_starts_at_the_first_failure_not_first_sight(
        cfg, ledger):
    """r-loop 6: r-loop 5 anchored the DISCOVERED reclaim on the last
    INGESTED event — which can NEVER exist for these rows, because
    INGESTED is written only at the END of a SUCCESSFUL download and a row
    only returns to DISCOVERED holding media from a FAILED one. COALESCE
    yielded '' and MIN() returned the ingest.scan insert: first sight on
    Drive. With intake cap-throttled and one serial download worker a
    session routinely waits far longer than the 12h grace between
    discovery and its first attempt, so the grace was effectively ZERO and
    the next hourly sweep deleted a partial multi-part transfer that
    rclone --checksum would have resumed."""
    sid = "s-seen-long-ago"
    _seed_disc(ledger, sid)
    (cfg.work / sid).mkdir(parents=True)
    (cfg.work / sid / "part001.zip").write_bytes(b"partial")
    # first seen on Drive 5 days ago; first download failure only 1h ago
    _age_discovered_event(ledger, sid, 1, seen_days_before=5)

    runmod._sweep_terminal_work(cfg, ledger)
    assert (cfg.work / sid).exists(), \
        "a transfer that first FAILED an hour ago must not be reclaimed " \
        "because the folder was first SEEN five days ago"


# ------------- split children must inherit the parent's GATE record

def test_split_children_inherit_the_parents_gate_record(tmp_path):
    """r-loop 6: since r-loop 5 a cut-bearing plan gates BEFORE it cuts, so
    the parent's rows are blanked and cutter copies the blanked rows into
    every child verbatim. But the destroyed-inventory record lived only in
    the PARENT's dossier while each child is validated against its own
    fresh one — so _gate_destroyed saw nothing and the child took exactly
    the wrongful CNT_ACTIONS_FEW / INP_KEYS_MISSING reject the record
    exists to prevent. Same shape as _propagate_shift_record, same reason:
    state established on the parent's timeline must follow the footage."""
    root = tmp_path / "dossiers"
    parent_sid = "2026-08-15T10-00-00Z_kamla_c_00000000000000aa"
    parent = root / parent_sid
    applied = [
        {"fix": "FIX_GATE_WINDOW", "params": {}, "ok": True,
         "note": {"destroyed": {"actions": ["interact"], "key_frames": 5}}},
        {"fix": "FIX_CUT_SEGMENTS", "params": {}, "ok": True, "note": {}},
    ]
    segments = [{"id": f"{parent_sid}-p1"}, {"id": f"{parent_sid}-p2"}]

    fix._propagate_gate_record(parent, root, applied, segments)

    for seg in segments:
        got = validate._gate_destroyed(root / seg["id"])
        assert got == {"actions": ["interact"], "key_frames": 5}, \
            f"child {seg['id']} did not inherit the gate record: {got}"


def test_gate_record_propagation_includes_earlier_attempts(tmp_path):
    """A gate applied on attempt 1 and a cut on attempt 2 is the common
    case — the earlier record is on disk, not in this pass's `applied`."""
    root = tmp_path / "dossiers"
    parent_sid = "2026-08-15T11-00-00Z_kamla_c_00000000000000ab"
    fix._append_fixlog(root / parent_sid, [
        {"fix": "FIX_GATE_WINDOW", "params": {}, "ok": True,
         "note": {"destroyed": {"actions": ["map"], "key_frames": 2}}}])

    fix._propagate_gate_record(root / parent_sid, root, [],
                               [{"id": f"{parent_sid}-p1"}])
    assert validate._gate_destroyed(root / f"{parent_sid}-p1") == {
        "actions": ["map"], "key_frames": 2}


def test_gate_record_propagation_is_a_noop_without_a_gate(tmp_path):
    root = tmp_path / "dossiers"
    fix._propagate_gate_record(root / "p", root, [], [{"id": "p-p1"}])
    assert not (root / "p-p1").exists()


# --------- the V lane needs the transient carve-out D and U already have

def test_host_level_validation_error_is_not_terminal(cfg, ledger,
                                                     monkeypatch):
    """r-loop 6: _validate_worker wrapped everything in a bare `except
    Exception` and _validate_one turned any error dict into QUARANTINED —
    a TERMINAL state with no automatic re-entry — with no discrimination.
    Both sibling lanes were hardened for exactly this: _download_one
    catches (OSError, sqlite3.OperationalError) and cools down, and
    _deliver_one's own comment records that a bare except there once
    'converted the whole READY/PACKAGED/UPLOADED backlog to QUARANTINED'.
    A full disk or an ENOMEM during one sweep therefore terminally
    rejected every session that happened to be validating, each then
    holding its media for CONT_QUARANTINE_RECLAIM_H."""
    sid = "s-hosterr"
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="op@x.com", player_email="p@x.com",
                          drive_path="kamla/op@x.com/p@x.com/" + sid,
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="a" * 32, bytes_=10, state="VALIDATING")
    (cfg.work / sid).mkdir(parents=True)
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    alerts = []
    monkeypatch.setattr(cont.AlertBook, "alert",
                        lambda self, t: alerts.append(t))
    monkeypatch.setattr(cont, "_POOL_DISABLED", True)   # run inline
    monkeypatch.setattr(
        cont, "_WORKER_FN",
        lambda job: {"sid": sid, "error": "OSError: [Errno 28] No space "
                                          "left on device", "kind": "host"})

    out = drv._validate_one(ledger, sid, ledger.get(sid))
    assert out != "QUARANTINED", "a host-level error must not be terminal"
    assert ledger.get(sid)["state"] == "VALIDATING"
    assert sid in drv.cool.blocked(), "it must be cooled down for a retry"
    assert any("host-level" in a for a in alerts)


def test_a_genuine_decode_crash_is_still_quarantined(cfg, ledger,
                                                     monkeypatch):
    """The carve-out is for the MACHINE having a bad minute, not for this
    session's bytes crashing the decoder."""
    sid = "s-crash"
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="op@x.com", player_email="p@x.com",
                          drive_path="kamla/op@x.com/p@x.com/" + sid,
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="b" * 32, bytes_=10, state="VALIDATING")
    (cfg.work / sid).mkdir(parents=True)
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    monkeypatch.setattr(cont.AlertBook, "alert", lambda self, t: None)
    monkeypatch.setattr(cont, "_POOL_DISABLED", True)   # run inline
    monkeypatch.setattr(
        cont, "_WORKER_FN",
        lambda job: {"sid": sid, "error": "ValueError: bad frame",
                     "kind": "crash"})
    assert drv._validate_one(ledger, sid, ledger.get(sid)) == "QUARANTINED"
