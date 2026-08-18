"""r-loop 10 fixes — pipeline side.

Each test cites the iteration-10 finding it pins (r10 #N, results of
record in R10_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree (session scratchpad), per plan §1.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from pipeline import fix as fixmod
from pipeline import run as runmod
from pipeline.tests.test_r_loop9 import _ins


# ------- r10 #1: the gate record is durable BEFORE the cut can start

def test_gate_record_survives_a_kill_during_the_cut(tmp_path, monkeypatch):
    """The gate blanked frames.csv durably, but its destroyed-inventory
    record lived only in memory until the single post-loop fixlog write —
    a kill anywhere in the (long) cut dispatch lost it forever, and both
    recovery routes (adoption, REVALIDATING) then terminally rejected the
    session for a deficit the pipeline created."""
    from pipeline import gate
    from pipeline.tests.test_r_loop7 import make_gate_csv
    from pipeline.validate import _gate_destroyed
    work = tmp_path / "K"
    work.mkdir()
    make_gate_csv(work, inputs={300: ("Q", "general_cancel"),
                                301: ("Q", "general_cancel"),
                                302: ("Q", "general_cancel")})
    dossiers = tmp_path / "dossiers"
    real_dispatch = fixmod._dispatch

    class _Kill(BaseException):
        pass

    def dispatch(fix_id, params, w, game, root):
        if fix_id == "FIX_GATE_WINDOW":
            return gate.gate_windows(w, params["windows"])
        if fix_id == "FIX_CUT_SEGMENTS":
            raise _Kill("kill -9 mid-cut")
        return real_dispatch(fix_id, params, w, game, root)
    monkeypatch.setattr(fixmod, "_dispatch", dispatch)

    plan = {"steps": [("FIX_GATE_WINDOW",
                       {"windows": [[300.0, 302.0]]}),
                      ("FIX_CUT_SEGMENTS", {"cut": [[0.0, 1.0]]})]}
    with pytest.raises(_Kill):
        fixmod.apply_fixes(work, plan, game="kamla",
                           dossier_dir=dossiers / "K")
    log = json.loads((dossiers / "K" / "fixlog.json").read_text())
    gates = [e for rec in log for e in rec["fixes"]
             if e["fix"] == "FIX_GATE_WINDOW" and e["ok"]]
    assert gates, "the gate entry must be durable before the cut starts"
    # the adoption path can now recover it (applied=[] by construction)
    fixmod._propagate_gate_record(
        dossiers / "K", dossiers, [],
        [{"id": "K-p1", "t0": 0.0, "t1": 200.0},
         {"id": "K-p2", "t0": 200.0, "t1": 400.0}])
    g2 = _gate_destroyed(dossiers / "K-p2")
    assert g2 == {"actions": ["general_cancel"], "key_frames": 3}, g2


def test_gate_record_not_double_propagated_on_success(tmp_path,
                                                      monkeypatch):
    """Control: with the pre-cut persist in place, the success path must
    still hand each child its share exactly ONCE (the fixlog walk sees
    the persisted entry; `applied` carries only the unpersisted tail)."""
    from pipeline import gate
    from pipeline.tests.test_r_loop7 import make_gate_csv
    from pipeline.validate import _gate_destroyed
    work = tmp_path / "S"
    work.mkdir()
    make_gate_csv(work, inputs={300: ("Q", "general_cancel"),
                                301: ("Q", "general_cancel"),
                                302: ("Q", "general_cancel")})
    dossiers = tmp_path / "dossiers"
    real_dispatch = fixmod._dispatch

    def dispatch(fix_id, params, w, game, root):
        if fix_id == "FIX_GATE_WINDOW":
            return gate.gate_windows(w, params["windows"])
        if fix_id == "FIX_CUT_SEGMENTS":
            return {"segments": [{"id": "S-p1", "t0": 0.0, "t1": 200.0},
                                 {"id": "S-p2", "t0": 200.0, "t1": 400.0}],
                    "dropped": []}
        return real_dispatch(fix_id, params, w, game, root)
    monkeypatch.setattr(fixmod, "_dispatch", dispatch)
    out = fixmod.apply_fixes(
        work, {"steps": [("FIX_GATE_WINDOW", {"windows": [[300.0, 302.0]]}),
                         ("FIX_CUT_SEGMENTS", {"cut": [[0.0, 1.0]]})]},
        game="kamla", dossier_dir=dossiers / "S")
    assert out["error"] is None
    g2 = _gate_destroyed(dossiers / "S-p2")
    assert g2 == {"actions": ["general_cancel"], "key_frames": 3}, g2
    log = json.loads((dossiers / "S-p2" / "fixlog.json").read_text())
    entries = [e for rec in log for e in rec["fixes"]]
    assert len(entries) == 1, f"exactly one inherited entry: {entries}"


# ------- r10 #2: one wedged day must not starve every later day

def test_wedged_day_does_not_starve_later_dailies(cfg, ledger,
                                                  monkeypatch, capsys):
    """A permanently-refusing resume returned False on every tick, and the
    day-agnostic scan resumes oldest-first — one bad day silently shut
    down ALL future reports and payment sheets."""
    from pipeline.tests.test_r_loop8 import _daily_seed
    from pipeline.tests.test_r_loop9 import _interrupt_before_stamps
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    _interrupt_before_stamps(cfg, ledger, monkeypatch, send)
    ledger.db.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
    ledger.db.commit()

    # tick 1: the bad day refuses AND is marked wedged (alerted)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is False
    assert (cfg.reports_dir / day / ".wedged").exists()
    # tick 2, next day: the wedge is SKIPPED (loudly) and the fresh day
    # proceeds — pre-fix this returned False forever
    next_day = send + timedelta(days=1)
    assert runmod.send_daily_report_if_due(cfg, ledger, next_day) is True
    day2 = next_day.strftime("%Y-%m-%d")
    assert (cfg.reports_dir / day2 / ".sent").exists()
    assert "WEDGED" in capsys.readouterr().err


def test_daily_report_cli_respects_the_run_lock(cfg, monkeypatch, capsys):
    """Lockless, the CLI could write a fresh counted record MID
    recal-tool teardown, crediting rows being deleted."""
    monkeypatch.setenv("HL_PIPELINE_HOME", str(cfg.home))
    monkeypatch.setattr(runmod, "send_daily_report_if_due",
                        lambda *a, **k: False)
    monkeypatch.setattr(runmod, "send_folder_issues_if_due",
                        lambda *a, **k: False)
    cfg.lock_dir.mkdir(parents=True, exist_ok=True)
    assert runmod.main(["daily-report"]) == 2
    assert "run lock held" in capsys.readouterr().out
    cfg.lock_dir.rmdir()
    assert runmod.main(["daily-report"]) == 0
    assert not cfg.lock_dir.exists(), "the lock must be released"


# ------- r10 #3/#6: the worker-death count is evidence-scoped

class _BrokenPool:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def map(self, *a, **k):
        from pipeline import continuous as cont
        raise cont.concurrent.futures.process.BrokenProcessPool("oom")


def test_death_count_resets_after_a_successful_worker_return(
        cfg, ledger, monkeypatch):
    """'Second death = bytes that reproducibly kill the decoder' is
    falsified by an intervening successful decode: a worker that returned
    ANY verdict (HOLD included) proved the bytes decode, so a later
    SIGKILL is a new host episode — pre-fix it was terminal."""
    from pipeline import continuous as cont
    sid = "s-cycle"
    _ins(ledger, sid)
    (cfg.work / sid).mkdir(parents=True)
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    monkeypatch.setattr(cont.AlertBook, "alert", lambda self, t: None)
    monkeypatch.setattr(cont.concurrent.futures, "ProcessPoolExecutor",
                        _BrokenPool)
    monkeypatch.setattr(cont, "_POOL_DISABLED", False)
    # death 1 -> host-suspect
    assert drv._validate_one(ledger, sid, ledger.get(sid)) is None
    # a successful worker RETURN (HOLD verdict) proves the bytes decode
    monkeypatch.setattr(cont, "_POOL_DISABLED", True)
    monkeypatch.setattr(cont, "_WORKER_FN",
                        lambda job: {"sid": job["sid"], "reasons": [],
                                     "bin": 1, "hold_vlm": True,
                                     "vlm_rung": 0})
    assert drv._validate_one(ledger, sid, ledger.get(sid)) == "HOLD_VLM"
    # a later death is a NEW first strike, not strike two
    monkeypatch.setattr(cont, "_POOL_DISABLED", False)
    assert drv._validate_one(ledger, sid, ledger.get(sid)) is None
    assert ledger.get(sid)["state"] == "VALIDATING"


def test_death_count_resets_on_new_bytes(cfg, ledger, monkeypatch):
    """Superseded new bytes inherited the old generation's death and went
    terminal on their FIRST worker death."""
    from pipeline import continuous as cont
    sid = "s-newbytes"
    _ins(ledger, sid)
    (cfg.work / sid).mkdir(parents=True)
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    monkeypatch.setattr(cont.AlertBook, "alert", lambda self, t: None)
    monkeypatch.setattr(cont.concurrent.futures, "ProcessPoolExecutor",
                        _BrokenPool)
    monkeypatch.setattr(cont, "_POOL_DISABLED", False)
    assert drv._validate_one(ledger, sid, ledger.get(sid)) is None
    ledger.supersede(sid, new_md5="b" * 32, new_bytes=11,
                     new_ctime="2026-08-15T10:00:00.000Z",
                     dossier_root=cfg.dossiers)
    assert drv._validate_one(ledger, sid, ledger.get(sid)) is None, \
        "new bytes must start a fresh death count"
    assert ledger.get(sid)["state"] == "VALIDATING"
    # control: back-to-back deaths on the SAME bytes still terminate
    assert drv._validate_one(ledger, sid, ledger.get(sid)) == "QUARANTINED"


# ------- r10 #4: zip-origin heal (no Drive-side md5) is unknowable

def test_zip_heal_preserves_stamps_and_defers_the_md5_decision(
        cfg, ledger, monkeypatch):
    """A zip payload lists no video.mp4 md5, so vmd5 is always '' — the
    '' != stored test cleared the stamps of every already-counted
    zip-origin root on a routine typo-fix rename."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    sid = "2026-08-14T10-00-00Z_kamla_c_0123456789abcdef"
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="op@x.com",
        player_email="p1@x.com", drive_path="kamla/BADPATH",
        drive_ctime="2026-08-14T10:00:00.000Z",
        md5_video="localbackfill00000000000000000aa", bytes_=1,
        state="DISCOVERED")
    ledger.set_state(sid, "QUARANTINED", "work copy missing")
    ledger.update(sid, uploaded_reported_at="2026-08-15T00:00:00+00:00",
                  accepted_reported_at="2026-08-15T00:00:00+00:00",
                  duration_raw_s=3600.0)
    entries = make_session_entries(sid=sid, files=["capture.zip"])
    monkeypatch.setattr(ingest, "list_drive", lambda _c: entries)
    ingest.scan(cfg, ledger, entries)
    row = ledger.get(sid)
    assert row["state"] == "DISCOVERED"
    assert row["uploaded_reported_at"], \
        "no Drive-side md5 = UNKNOWABLE, not different — stamps survive"
    assert row["duration_raw_s"] == 3600.0
    ev = ledger.db.execute(
        "SELECT detail FROM events WHERE session_id=? AND detail LIKE "
        "'%prev_md5=%'", (sid,)).fetchone()
    assert ev and "prev_md5=localbackfill" in ev["detail"]


def test_zip_heal_deferred_clear_fires_on_changed_bytes(cfg, ledger,
                                                        monkeypatch):
    """The deferred half: the download-time backfill compares the local
    hash against the remembered pre-heal md5 — different bytes get the
    supersede-style clear the heal withheld; identical bytes keep it."""
    import hashlib
    import subprocess

    from pipeline import ingest
    old_payload = b"same-old-bytes"
    old_md5 = hashlib.md5(old_payload).hexdigest()

    def seed(sid, payload):
        _ins(ledger, sid, state="DISCOVERED")
        ledger.update(sid, md5_video="",
                      uploaded_reported_at="2026-08-15T00:00:00+00:00",
                      accepted_reported_at="2026-08-15T00:00:00+00:00")
        ledger.db.execute(
            "INSERT INTO events(session_id, ts, from_state, to_state,"
            " detail) VALUES(?,?,?,?,?)",
            (sid, "2026-08-16T00:00:00+00:00", "QUARANTINED", "DISCOVERED",
             f"re-registered: quarantined path healed to x; "
             f"prev_md5={old_md5}"))
        ledger.db.commit()

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
        monkeypatch.setattr(ingest, "_probe_duration", lambda v: 123.0)
        ingest.download(cfg, ledger, sid)
        return ledger.get(sid)

    same = seed("s-zip-same", old_payload)
    assert same["uploaded_reported_at"], "identical bytes keep the stamps"
    diff = seed("s-zip-diff", b"genuinely-new-bytes")
    assert diff["uploaded_reported_at"] is None, \
        "changed bytes get the supersede-style clear at download time"
    assert diff["accepted_reported_at"] is None


# ------- r10 #5: adoption bounds come from the NEWEST insert event

def test_adopted_segments_use_the_newest_split_event(cfg, ledger):
    sid = "P"
    kid = f"{sid}-p1"
    _ins(ledger, kid)
    for ts, det in (("2026-08-15T00:00:00+00:00",
                     "split segment 0.0-200.0s"),
                    ("2026-08-16T00:00:00+00:00",
                     "split segment 150.0-400.0s")):
        ledger.db.execute(
            "INSERT INTO events(session_id, ts, from_state, to_state,"
            " detail) VALUES(?,?,?,?,?)", (kid, ts, "", "INGESTED", det))
    ledger.db.commit()
    segs = runmod._adopted_segments(ledger, [kid])
    assert segs == [{"id": kid, "t0": 150.0, "t1": 400.0}], segs


# ------- r10 #15: the BATCH adoption path propagates too

def test_batch_adoption_propagates_the_gate_record(cfg, ledger,
                                                   monkeypatch):
    import json as _json

    from pipeline.tests.test_r_loop9 import _gate_parent
    from pipeline.validate import _gate_destroyed
    sid = "B"
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
    monkeypatch.setattr(runmod, "_validate_phase", lambda *a, **k: None)
    runmod._fix_phase(cfg, ledger, [sid], [], workers=1)
    assert ledger.get(sid)["state"] == "SPLIT"
    g2 = _gate_destroyed(cfg.dossiers / f"{sid}-p2")
    assert g2 == {"actions": ["general_cancel"], "key_frames": 2}, g2
    assert not (cfg.dossiers / f"{sid}-p1" / "fixlog.json").exists()


# ------- r10 #7/#8/#9: planned fixes must CLEAR the FAILs they map from

import csv as _csv
import shutil as _shutil

import pytest as _pytest

HAVE_FFMPEG = bool(_shutil.which("ffmpeg") and _shutil.which("ffprobe"))
needs_ffmpeg = _pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")


def _frames_round_trip(tmp_path, mutate):
    """corrupt frames.csv -> checker FAIL -> map -> plan -> apply ->
    checker. Returns the AFTER result."""
    from pipeline import validate
    from pipeline.tests.test_fix_cut_gate import _make_session
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80)
    with (d / "frames.csv").open(newline="") as f:
        rows = list(_csv.reader(f))
    header, body = rows[0], rows[1:]
    col = {c: i for i, c in enumerate(header)}
    mutate(body, col)
    with (d / "frames.csv").open("w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(header)
        w.writerows(body)
    before = check_session_v2(d)
    assert before.status == "FAIL", "the corruption must trip the checker"
    reasons: list = []
    validate._map_qa_issues(before.issues, reasons, has_raw=False)
    plan = fixmod.plan_fixes(reasons, game="kamla", has_raw=False)
    out = fixmod.apply_fixes(d, plan, game="kamla",
                             dossier_dir=tmp_path / "dossier")
    assert out["error"] is None, out
    return check_session_v2(d)


@needs_ffmpeg
def test_key_hygiene_clears_foreign_button_tokens(tmp_path):
    """r10 #7: the exact-name round-trip passed 'left'/'Mouse4'/'LMB'
    through verbatim — the non-v2-token FAIL re-fired identically and the
    budget burned into a wrongful reject."""
    def mutate(body, col):
        bi = col["input_mouse_buttons"]
        body[5][bi] = "left"
        body[6][bi] = "Mouse4"
        body[7][bi] = "LMB"
    after = _frames_round_trip(tmp_path, mutate)
    assert not any("mouse button" in i for i in after.issues
                   if i.startswith("FAIL")), after.issues


@needs_ffmpeg
def test_sentinels_clear_nonconformant_float_cells(tmp_path):
    """r10 #8: dotted-but-nonconformant cells survived verbatim and
    dotless non-numeric cells crashed with an uncaught ValueError."""
    def mutate(body, col):
        dxi = col["input_mouse_dx"]
        body[0][dxi] = ".5"
        body[200][dxi] = "1."
        body[400][dxi] = "+1.0"
        body[600][dxi] = "1.2e3"
        body[10][dxi] = "abc"
    after = _frames_round_trip(tmp_path, mutate)
    assert not any("float-formatted" in i for i in after.issues
                   if i.startswith("FAIL")), after.issues


@needs_ffmpeg
def test_key_hygiene_strips_unbound_keys(tmp_path):
    """r10 #9: an unbound key resolves no action, so keeping it re-fired
    the keys-without-actions FAIL the fix was planned for."""
    def mutate(body, col):
        ki, ai = col["input_keys"], col["input_actions"]
        for i in (20, 21, 22, 23):
            body[i][ki] = "T"
            body[i][ai] = ""
    after = _frames_round_trip(tmp_path, mutate)
    assert not any("null input_actions" in i for i in after.issues
                   if i.startswith("FAIL")), after.issues


# ------- r10 #10: opencv open-failure degrades to a WARN, not a crash

@needs_ffmpeg
def test_undecodable_video_degrades_sync_check_to_warn(tmp_path,
                                                       monkeypatch):
    from pipeline.tests.test_fix_cut_gate import _make_session
    from translator import sync as syncmod
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80)

    def boom(path):
        raise ValueError(f"could not open video: {path}")
    monkeypatch.setattr(syncmod, "motion_track", boom)
    r = check_session_v2(d)          # must not raise
    assert any("not decodable by opencv" in i for i in r.issues), r.issues


# ------- r10 #11: orphaned paid-piece memory never silently double-pays

def test_orphaned_paid_memory_excludes_unsplit_redelivery(tmp_path,
                                                          capsys):
    """The re-run delivering the SAME footage under DIFFERENT ids (the
    whole root unsplit — the expected outcome when rules loosen) matched
    no memory row and was counted in full, silently re-paying footage
    already on a sent sheet."""
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import W2, W3, _put, _row, \
        _sheet
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_0000000000000or1"
    _put(led, root, state="DISCOVERED", raw=3600.0, player="orph@x.com")
    led.update(root, uploaded_reported_at="2026-08-15T00:00:00+00:00")
    led.record_paid_piece(root, f"{root}-p1", 1700.0, None)
    # the re-run delivers the ROOT itself — no id matches the memory
    led.update(root, duration_delivered_s=3400.0,
               delivered_at="2026-08-15T10:00:00+00:00")
    led.set_state(root, "DELIVERED")
    s = _row(_sheet(led, W2), "orph@x.com")
    err = capsys.readouterr().err
    assert "ORPHANED paid-piece memory" in err
    assert s is None or s["kamla_accepted_hrs"] == 0.0, \
        "may contain already-paid footage — never auto-paid"
    assert led.get(root)["accepted_reported_at"] is None
    # and it stays loud on the next sheet too
    _sheet(led, W3)
    assert "ORPHANED paid-piece memory" in capsys.readouterr().err
    led.close()


# ------- r10 #12: probed_duration_s wiring pinned END TO END

@needs_ffmpeg
def test_validate_session_judges_probed_duration_end_to_end(tmp_path):
    """The aux wiring at validate_session (not just map_reasons'
    arithmetic) — deleting it left the suite green."""
    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.validate import validate_session
    d = _make_session(tmp_path, seconds=100, name="claim45")
    s = json.loads((d / "session.json").read_text())
    s["duration_seconds"] = 45.0
    (d / "session.json").write_text(json.dumps(s))
    res = validate_session(d, tmp_path / "dossier", skip_vlm=True)
    assert not any(r["code"] == "CNT_SHORT" for r in res.reasons), \
        [r["code"] for r in res.reasons]

    d2 = _make_session(tmp_path, seconds=45, name="claim120")
    s = json.loads((d2 / "session.json").read_text())
    s["duration_seconds"] = 120.0
    (d2 / "session.json").write_text(json.dumps(s))
    res2 = validate_session(d2, tmp_path / "dossier2", skip_vlm=True)
    assert any(r["code"] == "CNT_SHORT" for r in res2.reasons), \
        [r["code"] for r in res2.reasons]


# ------- r10 #14: analyze()'s OSError -> error_kind='host' producer half

@needs_ffmpeg
def test_analyze_stamps_host_kind_on_inventory_oserror(tmp_path,
                                                       monkeypatch):
    """The only prior test stubbed the engine, pinning the consumer half
    alone — the producer lines survived deletion."""
    import types

    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.validate import load_engine
    eng = load_engine()
    d = _make_session(tmp_path, seconds=80, name="hostkind")
    stub = types.SimpleNamespace(status="PASS", issues=[])
    monkeypatch.setattr(eng, "check_session_v2",
                        lambda sdir, raw_bundle=None: stub)
    (d / "frames.csv").chmod(0o000)
    try:
        a = eng.analyze(d, {}, None, 4.0, 1.0)
    finally:
        (d / "frames.csv").chmod(0o644)
    assert a.error and "frames.csv unreadable" in a.error
    assert a.error_kind == "host", \
        "an OSError with no typed FAILs must stay host-classed"


# ------- r10 #16: the non-dict game_info guard in retranslate

@needs_ffmpeg
def test_retranslate_survives_string_game_info(tmp_path):
    from datetime import timedelta as _td

    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.tests.test_r_loop8 import _created_at, _sidecars
    work = _make_session(tmp_path, seconds=100, name="gamestr")
    created = _created_at(work)
    started = created - _td(seconds=725.0)
    evs = []
    for k, t0 in (("w", 726.0), ("a", 740.0), ("e", 755.0)):
        evs.append({"t": int(t0 * 1e6), "type": "key", "key": k,
                    "action": "down"})
        evs.append({"t": int((t0 + 2.0) * 1e6), "type": "key", "key": k,
                    "action": "up"})
    _sidecars(work, started, evs)
    raw_meta = json.loads((work / "raw" / "metadata.json").read_text())
    raw_meta["game"] = "kamla"          # a STRING, not an object
    (work / "raw" / "metadata.json").write_text(json.dumps(raw_meta))
    note = fixmod.retranslate_from_sidecars(work)
    assert "re-translated from sidecars" in note
