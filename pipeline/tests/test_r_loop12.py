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
    NEW bytes inside the stamp window, the stamp is still skipped loudly
    — the sheet counted the old bytes and the new hours stay countable.

    Re-keyed by G1 (r13 #2): the adjudication is now represented the
    way production records it — the DURABLE ZIP_ADJ_CHANGED marker
    event ingest.download writes beside its clear — because the old
    transient signature (real md5 + NULL duration) is legitimately
    erased by the probe/F6 refill and no longer means anything to
    _stamp. The end-to-end shapes live in test_r_loop13."""
    from pipeline import ingest
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
            ledger.set_state(
                sid, ledger.get(sid)["state"],
                f"{ingest.ZIP_ADJ_CHANGED} (md5 00c5 -> {'e' * 32})")
    monkeypatch.setattr(runmod.telegram, "send_message", newbytes_mid_send)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] is None, \
        "adjudicated-new bytes must not be stamped from the old sheet"
    assert "SKIPPED" in capsys.readouterr().err


# ------- r12 #5/#8: fix_actions_context resolves the session's keybind

def _context_work(tmp_path, monkeypatch, key, keybind: dict | None):
    from pipeline.tests.test_r_loop7 import make_gate_csv
    from translator import context as ctxmod
    work = tmp_path / "C"
    (work / "raw").mkdir(parents=True)
    make_gate_csv(work, n=60,
                  inputs={i: (key, "general_flashlight")
                          for i in range(10, 16)})
    (work / "session.json").write_text('{"fps": 30.0}')
    if keybind is not None:
        (work / "raw" / "keybind.json").write_text(json.dumps(keybind))
    monkeypatch.setattr(ctxmod, "available", lambda: True)
    monkeypatch.setattr(ctxmod, "classify_video",
                        lambda video, fps, game: ["on_foot"] * 60)
    return work


def test_actions_context_honors_the_sessions_own_keybind(tmp_path,
                                                         monkeypatch):
    """F4 fixed hygiene but FIX_ACTIONS_CONTEXT — mandatory after any OW
    hygiene plan, and an action REWRITER for every row — still resolved
    with the built-ins: custom-bound presses lost their actions (or were
    silently re-labeled with the built-in semantic) one step after
    hygiene resolved them correctly."""
    from pipeline import fix as fixmod
    work = _context_work(tmp_path, monkeypatch, "G",
                         {"general_flashlight": "g"})
    note = fixmod.fix_actions_context(work, "outer_wilds")
    header, rows = fixmod._read_csv(work)
    col = {c: i for i, c in enumerate(header)}
    carrying = [r for r in rows
                if "G" in r[col["input_keys"]].split("|")]
    assert len(carrying) == 6, note
    assert all("general_flashlight" in r[col["input_actions"]]
               for r in carrying), \
        "the custom bind's action must survive the context rewrite"


def test_actions_context_without_sidecar_uses_the_builtin(tmp_path,
                                                          monkeypatch):
    """Control: no session keybind — the built-in governs unchanged."""
    from pipeline import fix as fixmod
    work = _context_work(tmp_path, monkeypatch, "F", None)
    fixmod.fix_actions_context(work, "outer_wilds")
    header, rows = fixmod._read_csv(work)
    col = {c: i for i, c in enumerate(header)}
    carrying = [r for r in rows
                if "F" in r[col["input_keys"]].split("|")]
    assert len(carrying) == 6
    assert all("general_flashlight" in r[col["input_actions"]]
               for r in carrying)


# ------- r12 #6: structural surgery precedes hygiene/context in
# ------- cut-less plans too

def _plan_ids(reasons, game="outer_wilds"):
    from pipeline import fix as fixmod
    plan = fixmod.plan_fixes(reasons, game=game, has_raw=False)
    return [fid for fid, _p in plan["steps"]]


def _fixable(code, **params):
    return {"code": code, "blocking": True, "fixable": True,
            "params": params, "evidence": "e"}


def test_rows_surgery_precedes_context_in_cutless_plans():
    """fix_actions_context hard-fails on any rows/video mismatch (one
    track label per VIDEO frame), so [CONTEXT, ROWS] burned both
    attempts on a FixFailed whose cure was one step later in the same
    plan — a wrongful terminal reject of the INP_FANOUT pairing."""
    ids = _plan_ids([_fixable("INP_FANOUT"),
                     _fixable("STR_ROWS_MISMATCH")])
    assert "FIX_ROWS_SURGERY" in ids and "FIX_ACTIONS_CONTEXT" in ids, ids
    assert ids.index("FIX_ROWS_SURGERY") < \
        ids.index("FIX_ACTIONS_CONTEXT"), ids


def test_rows_surgery_precedes_hygiene_in_cutless_plans():
    ids = _plan_ids([_fixable("INP_OSKEYS"),
                     _fixable("STR_ROWS_MISMATCH")])
    assert ids.index("FIX_ROWS_SURGERY") < \
        ids.index("FIX_KEY_HYGIENE"), ids
    assert ids.index("FIX_KEY_HYGIENE") < \
        ids.index("FIX_ACTIONS_CONTEXT"), \
        "hygiene-before-context stays intact"


# ------- r12 #7: edge notif/chat cuts get the map-time length check

def test_edge_notification_on_a_barely_long_clip_is_short_at_map_time():
    """The CNT_EDGE arm has had this check since day one; the notif/chat
    siblings did not — a 70-74s clip burned a fix attempt and a paid
    sweep reaching an INEVITABLE CNT_SHORT under a reason that
    misdirects the re-record coaching."""
    from pipeline import validate
    reasons: list = []
    validate._map_flags(
        {"duration_s": 71.0},
        {"probed_duration_s": 71.0,
         "notifs": [{"t": 2.5, "confirmed": True, "what": "steam toast"}]},
        reasons, [])
    assert [r["code"] for r in reasons] == ["CNT_SHORT"], reasons
    assert reasons[0]["fixable"] is False
    assert reasons[0]["params"]["post_cut_s"] == 67.5


def test_edge_chat_tail_on_a_barely_long_clip_is_short_at_map_time():
    from pipeline import validate
    reasons: list = []
    validate._map_flags(
        {"duration_s": 71.0},
        {"probed_duration_s": 71.0,
         "chats": [{"t": 69.5, "confirmed": True, "what": "pii"}]},
        reasons, [])
    assert [r["code"] for r in reasons] == ["CNT_SHORT"], reasons
    assert reasons[0]["fixable"] is False


def test_edge_flags_on_a_long_clip_keep_their_fixable_cuts():
    """Control: plenty of clip left — the fixable edge codes stand."""
    from pipeline import validate
    reasons: list = []
    validate._map_flags(
        {"duration_s": 200.0},
        {"probed_duration_s": 200.0,
         "notifs": [{"t": 2.5, "confirmed": True, "what": "toast"}],
         "chats": [{"t": 198.0, "confirmed": True, "what": "pii"}]},
        reasons, [])
    assert sorted(r["code"] for r in reasons) == \
        ["CNT_CHAT_PII", "CNT_NOTIF_EDGE"], reasons
    assert all(r["fixable"] for r in reasons)


# ------- r12 #9: the VLM sweep grid is clamped to the real timeline

def test_vlm_sweep_grid_is_bounded_for_absurd_durations(monkeypatch):
    """The grid loop walked the player-supplied duration claim verbatim
    — json.loads accepts Infinity/1e999, and neither driver bounds the
    validation worker: one such session pinned a runner slot forever."""
    import signal
    import types

    from pipeline.validate import load_engine
    az = load_engine()
    seen: dict = {}

    def fake_classify(gem, grabber, title, ts):
        seen.setdefault("n", len(ts))
        return []
    monkeypatch.setattr(az, "classify_frames", fake_classify)

    def alarm(*a):
        raise TimeoutError("unbounded sweep grid")
    old = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(20)
    try:
        az.vlm_sweep(types.SimpleNamespace(model="stub", requests=0),
                     None, "Kamla", float("inf"), 4.0, 1.0)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert seen["n"] <= 86400 / 4.0 + 3, seen


@needs_ffmpeg
def test_analyze_clamps_the_sweep_to_the_probed_timeline(tmp_path,
                                                         monkeypatch):
    """The analyze site passes min(claimed, probed) — the D2 doctrine
    (claimed duration is untrusted) applied to the sweep grid."""
    import types

    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.validate import load_engine
    az = load_engine()
    d = _make_session(tmp_path, seconds=80, name="clamp")
    s = json.loads((d / "session.json").read_text())
    s["duration_seconds"] = 1e999
    (d / "session.json").write_text(json.dumps(s))
    seen: dict = {}

    def fake_sweep(gem, grabber, title, duration_s, interval, refine_step):
        seen["dur"] = duration_s
        return {"samples": [{"t": 1.0, "label": "gameplay",
                             "notif": False, "guess": "", "note": ""}],
                "windows": [], "notif_ts": [], "chat_ts": [],
                "combat_ts": [], "game_votes": {}, "model": "stub",
                "requests": 0, "baseline_interval_s": interval,
                "refine_step_s": refine_step}
    monkeypatch.setattr(az, "vlm_sweep", fake_sweep)
    az.analyze(d, {}, types.SimpleNamespace(requests=0), 4.0, 1.0)
    assert seen["dur"] < 90.0, seen


# ------- r12 #10: inventory's timestamp cast catches OverflowError

@needs_ffmpeg
def test_inf_timestamp_cell_does_not_crash_analyze(tmp_path):
    """'1e999' floats to inf and int(inf) raised straight out of
    inventory() — even when check_session_v2 had just produced the typed
    FAIL whose designed route is a one-attempt automated repair, the
    session went terminally QUARANTINED as 'validation crashed'."""
    import csv as _csv

    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.validate import load_engine
    az = load_engine()
    d = _make_session(tmp_path, seconds=80, name="infts")
    with (d / "frames.csv").open(newline="") as f:
        rows = list(_csv.reader(f))
    header, body = rows[0], rows[1:]
    ti = header.index("timestamp_ms")
    body[10][ti] = "1e999"
    with (d / "frames.csv").open("w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(header)
        w.writerows(body)
    a = az.analyze(d, {}, None, 4.0, 1.0)      # must not raise
    assert not a.error, a.error


# ------- r12 #11: the DAILY send honors the regen interlock too

def test_daily_send_refuses_while_a_regen_send_is_pending(
        cfg, ledger, monkeypatch, capsys):
    """F10 taught only the reset tools about the regen's resumable
    record; the daily send still counted the whole unstamped cohort
    through the late-arrival guard, sent + stamped it, and the regen
    re-run then re-sent the SAME hours on a SUPERSEDES sheet — two
    authoritative payment documents, zero loud lines."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    rd = cfg.reports_dir / "2026-08-16"
    rd.mkdir(parents=True)
    (rd / ".regen-v2-counted.json").write_text(
        '{"counted": [], "accepted": []}')
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is False
    assert "REFUSING" in capsys.readouterr().err
    assert not csv_path.exists(), \
        "no sheet may be generated under the regen interlock"
    assert ledger.get(sid)["uploaded_reported_at"] is None
    # the regen completes: the daily proceeds normally
    (rd / ".regen-v2-done").touch()
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    assert ledger.get(sid)["uploaded_reported_at"]


# ------- r12 #13: pending_daily_send fails CLOSED when it cannot look

def test_pending_daily_send_fails_closed_when_it_cannot_look(cfg):
    """'Could not check' must never read as 'checked clean' in a
    teardown gate: a transient EMFILE/permissions failure at exactly
    reset-tool entry silently disabled the interlock."""
    import shutil

    from pipeline import reports
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    cfg.reports_dir.chmod(0o000)
    try:
        assert reports.pending_daily_send(cfg), \
            "an unreadable reports dir must read as PENDING, not clean"
        assert reports.pending_regen_send(cfg)
    finally:
        cfg.reports_dir.chmod(0o755)
    # a MISSING reports dir is genuinely nothing-pending (fresh home)
    shutil.rmtree(cfg.reports_dir, ignore_errors=True)
    assert reports.pending_daily_send(cfg) is None
    assert reports.pending_regen_send(cfg) is None


# ------- r12 #14: the F6 producer half pinned (metrics -> worker)

def test_metrics_carries_the_probed_duration():
    """Deleting the _metrics wiring kept the 674-test gate green while
    NULL-duration roots silently lost their uploaded hours — the F6
    backfill tests all faked the worker dict, never the producer."""
    from pipeline import validate
    m = validate._metrics({}, {"probed_duration_s": 55.5})
    assert m["probed_duration_s"] == 55.5


def test_validate_worker_forwards_the_probed_duration(cfg, monkeypatch):
    """The worker-side forwarding link, exercised through the REAL
    _validate_worker (validate_session stubbed at its import site)."""
    from pipeline import validate
    monkeypatch.setattr(
        validate, "validate_session",
        lambda *a, **k: validate.MapResult(
            bin=1, hold_vlm=False, engine_verdict="",
            metrics={"probed_duration_s": 77.0, "models_used": []}))
    res = runmod._validate_worker(
        {"sid": "s-fwd", "work_dir": str(cfg.work / "s-fwd"),
         "dossier_dir": str(cfg.dossiers / "s-fwd"), "payload": "v2",
         "expected_game": None, "gemini_key": "", "gemini_model": "",
         "vlm_rung": 0})
    assert res["probed_duration_s"] == 77.0, res


# ------- r12 #4: the DISCOVERED-media reclaim grace re-arms per reclaim

def test_reclaim_grace_rearms_after_a_sweep(cfg, ledger):
    """The reclaim wrote no event, so the age anchor stayed frozen at
    the FIRST failure forever: after the first legitimate reclaim, every
    later hourly sweep wiped whatever minutes-old parts the 5-min
    retries had re-accumulated — effective grace ZERO, the exact
    resumable-transfer loss the r-loop-6 comment documents."""
    from datetime import datetime, timedelta, timezone

    from pipeline import config as C
    from pipeline import continuous as cont
    from pipeline.tests.test_r_loop5 import _age_discovered_event, \
        _seed_disc
    sid = "s-rearm"
    _seed_disc(ledger, sid)
    (cfg.work / sid).mkdir(parents=True)
    (cfg.work / sid / "video.mp4").write_bytes(b"x" * 16)
    _age_discovered_event(ledger, sid, C.CONT_DISCOVERED_RECLAIM_H + 8)
    runmod._sweep_terminal_work(cfg, ledger)
    assert not (cfg.work / sid).exists(), "first reclaim is legitimate"
    # the 5-min retries re-accumulate parts and fail again 10 min ago
    (cfg.work / sid).mkdir(parents=True)
    (cfg.work / sid / "video.mp4").write_bytes(b"y" * 16)
    now = datetime.now(timezone.utc)

    def iso(d):
        return d.isoformat(timespec="seconds")
    ledger.db.executemany(
        "INSERT INTO events(session_id, from_state, to_state, ts, detail)"
        " VALUES(?,?,?,?,?)",
        [(sid, "DISCOVERED", "DOWNLOADING",
          iso(now - timedelta(minutes=15)), "claimed by D"),
         (sid, "DOWNLOADING", "DISCOVERED",
          iso(now - timedelta(minutes=10)), "download failed")])
    ledger.db.commit()
    runmod._sweep_terminal_work(cfg, ledger)
    assert (cfg.work / sid).exists(), \
        "post-reclaim re-accumulated parts get a fresh 12h grace"
    # and the digest reports the true (young) age, not the frozen one
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    lines, _n = drv._stuck_lines(ledger)
    assert sid not in " ".join(lines), lines


# ------- r12 #3/#12: recal_rebuild_reset HOLDS the run lock

def test_rebuild_reset_holds_the_run_lock(cfg, monkeypatch):
    """The tool kept the bare lock-existence check r-loop 1 condemned in
    every sibling: a systemd Restart=always respawn (or timer tick)
    landing mid-teardown found the lock FREE and ran a full pipeline
    concurrently with the blanket child DELETE and work-dir wipes."""
    import sys as _sys

    from pipeline import reports
    from pipeline.tests.test_payment_split_r6 import _load
    reset = _load("recal_rebuild_reset")
    parachute = cfg.home / "parachute.db"
    parachute.write_bytes(b"x" * 2048)
    monkeypatch.setenv("HL_PIPELINE_HOME", str(cfg.home))
    seen: dict = {}
    real = reports.pending_daily_send

    def spy(c):
        seen["lock_held"] = cfg.lock_dir.exists()
        return real(c)
    monkeypatch.setattr(reports, "pending_daily_send", spy)
    monkeypatch.setattr(_sys, "argv", ["recal_rebuild_reset.py", "--yes",
                                       "--backup", str(parachute)])
    assert reset.main() == 0
    assert seen.get("lock_held") is True, \
        "the lock must be HELD across the teardown, not just checked"
    assert not cfg.lock_dir.exists(), "and released afterwards"


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
