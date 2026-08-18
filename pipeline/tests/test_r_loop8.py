"""r-loop 8 — the confirmed findings (C1-C9).

Three blockers (the retranslate guard killing split children; the host
carve-out re-running partially-applied plans; the daily send's missing
durable counted record), a four-finding seal-semantics cluster, ops-surface
majors, and the STR_SJ_INVALID no-op rewrite. The recurring theme is the
same as loops 4/6/7: most of the serious ones are regressions from earlier
fixes' own blind spots.
"""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import config as C
from pipeline import continuous as cont
from pipeline import fix as fixmod
from pipeline import run as runmod

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")


def _R(code, fixable=True, **params):
    return {"code": code, "blocking": True, "fixable": fixable,
            "params": params, "evidence": "e"}


def _seed_fix_queued(ledger, cfg, sid, reasons):
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path=f"kamla/Op/p@x.com/{sid}",
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video="m", bytes_=1,
        state="DISCOVERED")
    ledger.set_reasons(sid, reasons, 2)
    ledger.set_state(sid, "FIX_QUEUED")
    (cfg.work / sid).mkdir(parents=True, exist_ok=True)


# ---------------------- C1d: failed raw translate cleans up its temp tree

def test_failed_raw_translate_does_not_leak_the_temp_tree(tmp_path,
                                                          monkeypatch):
    """The `_translated/` cleanup was success-path-only, so each FAILED
    attempt left a video-sized temp copy inside the working copy — twice
    per session, against a media cap that counts sessions as its bytes
    bound."""
    work = tmp_path / "s"
    work.mkdir()

    def boom(bundle_dir, out_root, **kw):
        d = Path(out_root) / "humynlabs" / "d" / "g" / "s"
        d.mkdir(parents=True)
        (d / "video.mp4").write_bytes(b"v" * 4096)
        raise ValueError("metadata exploded mid-translate")

    monkeypatch.setattr(fixmod, "translate_bundle_v2", boom)
    out = fixmod.apply_fixes(work, {"steps": [("FIX_TRANSLATE_RAW", {})]},
                             game="kamla", dossier_dir=tmp_path / "d")
    assert out["error"]
    assert not (work / "_translated").exists(), \
        "a failed attempt must not leak the video-sized temp tree"
    log = json.loads((tmp_path / "d" / "fixlog.json").read_text())
    assert log[-1]["fixes"][0]["ok"] is False


# --------- C2 BLOCKER: the retranslate guard must not kill split children

def _sidecars(work: Path, started_at: datetime, events: list[dict]) -> None:
    raw = work / "raw"
    raw.mkdir(exist_ok=True)
    (raw / "metadata.json").write_text(json.dumps(
        {"recording": {"started_at_utc":
                       started_at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"},
         "game": {"name": "Kamla"}}))
    (raw / "inputs.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events))


def _created_at(work: Path) -> datetime:
    s = json.loads((work / "session.json").read_text())
    return datetime.fromisoformat(s["created_at_utc"].replace("Z", "+00:00"))


@needs_ffmpeg
def test_split_child_with_head_offset_beyond_its_length_retranslates(
        tmp_path):
    """cutter.py gives every split child created_at = parent_created +
    src_pts[i0] AND a copy of the parent's raw/ precisely so children can
    retranslate: head_s is the offset into the RAW recording, not into
    this clip. The r-loop-7 duration guard therefore terminally rejected
    every second-or-later segment on both attempts — good, already-cut
    footage refused for being exactly what a split child is."""
    from pipeline.tests.test_fix_cut_gate import _make_session
    work = _make_session(tmp_path, seconds=100, name="child")
    created = _created_at(work)
    started = created - timedelta(seconds=725.0)   # child of a long parent
    # keyboard-only events in the child's own band (sync/context quiet)
    evs = []
    for k, t0 in (("w", 726.0), ("a", 740.0), ("e", 755.0)):
        evs.append({"t": int(t0 * 1e6), "type": "key", "key": k,
                    "action": "down"})
        evs.append({"t": int((t0 + 2.0) * 1e6), "type": "key", "key": k,
                    "action": "up"})
    _sidecars(work, started, evs)

    note = fixmod.retranslate_from_sidecars(work)
    assert "re-translated from sidecars" in note
    with (work / "frames.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    keys = {k for r in rows for k in (r["input_keys"] or "").split("|") if k}
    assert {"W", "A", "E"} <= keys, keys


@needs_ffmpeg
def test_truly_bogus_stamps_still_refused_on_zero_events(tmp_path):
    """The original defence stays: stamps placing every sidecar event
    before the clip must fail attributably, never ship empty input
    columns."""
    from pipeline.tests.test_fix_cut_gate import _make_session
    work = _make_session(tmp_path, seconds=100, name="bogus")
    created = _created_at(work)
    started = created - timedelta(seconds=725.0)
    # all events fall before the head cut and none stays held across it
    evs = [{"t": int(10 * 1e6), "type": "key", "key": "w",
            "action": "down"},
           {"t": int(11 * 1e6), "type": "key", "key": "w", "action": "up"}]
    _sidecars(work, started, evs)
    with pytest.raises(fixmod.FixFailed) as e:
        fixmod.retranslate_from_sidecars(work)
    assert "zero events" in str(e.value)


# --------- C3 BLOCKER: host carve-out must not re-run an applied plan

_HOST_PARTIAL = {"applied": [{"fix": "FIX_RETRIM_HEAD", "ok": True},
                             {"fix": "FIX_SESSIONJSON_RECOMPUTE",
                              "ok": False}],
                 "children": None,
                 "error": "FIX_SESSIONJSON_RECOMPUTE: OSError: [Errno 28] "
                          "No space left on device",
                 "kind": "host"}
_HOST_NOTHING = {"applied": [{"fix": "FIX_RETRIM_HEAD", "ok": False}],
                 "children": None,
                 "error": "FIX_RETRIM_HEAD: OSError: [Errno 28] "
                          "No space left on device",
                 "kind": "host"}


def test_cont_host_error_after_applied_step_revalidates(cfg, ledger,
                                                        monkeypatch):
    """plan_fixes is pure: a FIX_QUEUED park with reasons untouched
    re-dispatches the IDENTICAL plan from step 0, re-running the
    already-succeeded destructive steps. A partially-applied host failure
    must re-derive from the half-fixed copy instead."""
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    sid = "2026-08-14T10-00-00Z_kamla_c_00000000000000c1"
    _seed_fix_queued(ledger, cfg, sid, [_R("STR_SENTINELS")])
    monkeypatch.setattr(fixmod, "apply_fixes",
                        lambda *a, **kw: dict(_HOST_PARTIAL))
    assert drv._fix_one(ledger, sid) is False
    row = ledger.get(sid)
    assert row["state"] == "REVALIDATING", row["state"]
    assert row["fix_attempts"] == 0, "the attempt must be refunded"


def test_cont_host_error_before_any_step_parks_fix_queued(cfg, ledger,
                                                          monkeypatch):
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    sid = "2026-08-14T10-00-00Z_kamla_c_00000000000000c2"
    _seed_fix_queued(ledger, cfg, sid, [_R("STR_SENTINELS")])
    monkeypatch.setattr(fixmod, "apply_fixes",
                        lambda *a, **kw: dict(_HOST_NOTHING))
    assert drv._fix_one(ledger, sid) is False
    row = ledger.get(sid)
    assert row["state"] == "FIX_QUEUED", row["state"]
    assert row["fix_attempts"] == 0


def test_batch_host_error_routing_mirrors_the_continuous_driver(
        cfg, ledger, monkeypatch):
    """The dormant rollback driver takes the same split — its pass loop
    could even re-trim within a single run."""
    monkeypatch.setattr(runmod, "_validate_phase", lambda *a, **kw: None)
    sid_p = "2026-08-14T10-00-00Z_kamla_c_00000000000000b1"
    sid_n = "2026-08-14T10-00-00Z_kamla_c_00000000000000b2"
    _seed_fix_queued(ledger, cfg, sid_p, [_R("STR_SENTINELS")])
    _seed_fix_queued(ledger, cfg, sid_n, [_R("STR_SENTINELS")])
    outs = {sid_p: _HOST_PARTIAL, sid_n: _HOST_NOTHING}
    monkeypatch.setattr(
        fixmod, "apply_fixes",
        lambda work, *a, **kw: dict(outs[Path(work).name]))
    runmod._fix_phase(cfg, ledger, [sid_p, sid_n], [], workers=1)
    rp, rn = ledger.get(sid_p), ledger.get(sid_n)
    assert rp["state"] == "REVALIDATING", rp["state"]
    assert rp["fix_attempts"] == 0
    assert rn["state"] == "FIX_QUEUED", rn["state"]
    assert rn["fix_attempts"] == 0


def test_host_error_mid_plan_never_reruns_the_destructive_step(
        cfg, ledger, monkeypatch, tmp_path):
    """The money shot: retrim succeeded, then the recompute hit ENOSPC.
    Pre-fix the row went back to FIX_QUEUED with reasons untouched and the
    next pick re-ran FIX_RETRIM_HEAD on the already-trimmed video —
    tools/retrim_v2_session.py probes the CURRENT video and removes
    head_s again on every call (measured 300s→175s over five passes)."""
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    sid = "2026-08-14T10-00-00Z_kamla_c_00000000000000c3"
    _seed_fix_queued(ledger, cfg, sid,
                     [_R("CNT_EDGE_NONGAMEPLAY", edge="head",
                         cut_at_s=12.5)])
    counter = tmp_path / "retrims"
    counter.write_text("0")

    def fake_dispatch(fix_id, params, work, game, split_root):
        if fix_id == "FIX_RETRIM_HEAD":
            counter.write_text(str(int(counter.read_text()) + 1))
            return "retrimmed"
        if fix_id == "FIX_SESSIONJSON_RECOMPUTE":
            raise OSError(28, "No space left on device")
        return "ok"

    monkeypatch.setattr(fixmod, "_dispatch", fake_dispatch)
    assert drv._fix_one(ledger, sid) is False
    row = ledger.get(sid)
    assert row["state"] == "REVALIDATING", row["state"]
    assert row["fix_attempts"] == 0
    assert counter.read_text() == "1"
    # a second pick must be a no-op — the row left FIX_QUEUED
    assert drv._fix_one(ledger, sid) is False
    assert counter.read_text() == "1", \
        "the destructive step must not run twice"
    assert ledger.get(sid)["state"] == "REVALIDATING"


# ------------------------------- C4: the three flip-time ops surfaces

def _ev(led, sid, to_state, when):
    led.db.execute(
        "INSERT INTO events(session_id, ts, from_state, to_state, detail)"
        " VALUES(?,?,?,?,?)",
        (sid, when.isoformat(timespec="seconds"), "", to_state, ""))


def test_stuck_list_sees_the_host_error_retry_loops(cfg, ledger):
    """C4a: the V-lane cooldown retry (state stays VALIDATING) and the C3
    carve-out (FIX_QUEUED<->FIXING) re-stamp updated_at every ~5 min, so
    `updated_at < cut` could never fire for exactly the rows most likely
    to be stuck — invisible on the ONLY ops surface the flip leaves live.
    Aged from the stint start in the events audit instead."""
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    drv = cont.ContinuousDriver(cfg, clocks=cont._Clocks(utcnow=lambda: now))
    fresh = now.isoformat(timespec="seconds")

    def seed(sid, state, stint_start_h, step_min=90):
        _seed_fix_queued(ledger, cfg, sid, [_R("STR_SENTINELS")])
        ledger.db.execute("DELETE FROM events WHERE session_id=?", (sid,))
        _ev(ledger, sid, "INGESTED",
            now - timedelta(hours=stint_start_h + 1))
        t = now - timedelta(hours=stint_start_h)
        states = (["FIX_QUEUED", "FIXING"] if state != "VALIDATING"
                  else ["VALIDATING"])
        i = 0
        while t < now:
            _ev(ledger, sid, states[i % len(states)], t)
            t += timedelta(minutes=step_min)
            i += 1
        ledger.db.execute(
            "UPDATE sessions SET state=?, updated_at=? WHERE session_id=?",
            (state, fresh, sid))
        ledger.db.commit()

    ping = "2026-08-14T10-00-00Z_kamla_c_00000000000000e1"
    seed(ping, "FIX_QUEUED", 20)          # 20h FIXING<->FIX_QUEUED loop
    val = "2026-08-14T10-00-00Z_kamla_c_00000000000000e2"
    seed(val, "VALIDATING", 20)           # 20h VALIDATING self-refresh
    okc = "2026-08-14T10-00-00Z_kamla_c_00000000000000e3"
    seed(okc, "FIXING", 10 / 60.0, step_min=5)   # normal fix, 10 min in

    lines, total = drv._stuck_lines(ledger)
    joined = " ".join(lines)
    assert ping in joined, lines
    assert val in joined, lines
    assert okc not in joined, lines


def test_digest_retry_is_bounded_when_telegram_is_down(cfg, ledger,
                                                       monkeypatch):
    """C4b: the digest duty ran on every ~20s H tick; through a Telegram
    outage that meant ~180 full digest rebuilds+sends an hour — during
    the incident when Telegram is the operator's only view."""
    from pipeline import reports as repmod
    from pipeline import telegram as tgmod
    mono = [10_000.0]
    drv = cont.ContinuousDriver(
        cfg, send_telegram=True,
        clocks=cont._Clocks(mono=lambda: mono[0]))
    monkeypatch.setattr(runmod, "send_daily_report_if_due",
                        lambda *a, **k: False)
    monkeypatch.setattr(runmod, "send_folder_issues_if_due",
                        lambda *a, **k: False)
    builds = []
    real_build = repmod.build_digest_message

    def spy_build(d, p):
        builds.append(1)
        return real_build(d, p)
    monkeypatch.setattr(repmod, "build_digest_message", spy_build)

    def down(cfg_, text):
        raise tgmod.TelegramError("down")
    monkeypatch.setattr(tgmod, "send_message", down)

    for _ in range(6):                       # 2 min of ticks, one window
        drv._house_tick(ledger)
        mono[0] += 20.0
    assert len(builds) == 1, \
        f"{len(builds)} digest builds inside one retry window"
    mono[0] += C.CONT_DIGEST_RETRY_S
    drv._house_tick(ledger)
    assert len(builds) == 2                  # retried after the window


def test_alertbook_failed_send_does_not_consume_the_ttl(cfg, monkeypatch):
    """C4c: the stamp landed before the send, so a failed send consumed
    the whole TTL and a persisting condition went silent for
    CONT_ALERT_DEDUP_MIN. Failed sends retract the stamp; successful ones
    still dedupe."""
    from pipeline import telegram as tgmod
    now = [0.0]
    book = cont.AlertBook(cfg, ttl_s=100, mono_fn=lambda: now[0])
    calls = []

    def send(cfg_, text):
        calls.append(text)
        if len(calls) == 1:
            raise tgmod.TelegramError("down")
    monkeypatch.setattr(tgmod, "send_message", send)
    book.alert("disk low")
    now[0] += 10
    book.alert("disk low")               # within TTL, first send FAILED
    assert len(calls) == 2, "a failed send must not consume the TTL"
    now[0] += 10
    book.alert("disk low")               # second send SUCCEEDED — deduped
    assert len(calls) == 2


# --------------- C5 BLOCKER: the daily send's durable counted record

def _daily_seed(cfg, ledger, monkeypatch, docs=None):
    """A countable DELIVERED root in the send window + counting telegram
    stubs. Returns (send_time, sid, csv_path, day)."""
    from pipeline.tests.test_review_r5_driver import (_mk_delivered_root,
                                                      _send_time,
                                                      _window_hi)
    send = _send_time()
    hi = _window_hi(send)
    sid = "2026-08-15T05-00-00Z_kamla_c_00000000000000c5"
    _mk_delivered_root(ledger, sid, hi - timedelta(hours=2))
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda c, t: None)
    monkeypatch.setattr(
        runmod.telegram, "send_document",
        lambda c, p, caption="": (docs.append(Path(p).read_bytes())
                                  if docs is not None else None))
    day = send.strftime("%Y-%m-%d")
    return send, sid, cfg.reports_dir / day / f"payment-{day}.csv", day


def test_daily_partial_stamp_crash_resumes_never_regenerates(
        cfg, ledger, monkeypatch):
    """One `database is locked` inside the accepted-stamp loop left the
    marker absent with the uploads already stamped; the retry then
    REGENERATED, and post-stamp build_sheet_rows excludes every stamped
    root — a smaller (even header-only) sheet overwrote payment-<day>.csv
    and was sent as the payment document."""
    import sqlite3
    from pipeline import reports
    from pipeline.tests.test_review_r5_driver import _send_time
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    calls = {"n": 0}
    real = reports.mark_accepted_reported

    def flaky(led_, sids):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(led_, sids)
    monkeypatch.setattr(reports, "mark_accepted_reported", flaky)
    with pytest.raises(sqlite3.OperationalError):
        runmod.send_daily_report_if_due(cfg, ledger, send)
    first = csv_path.read_bytes()
    assert b"p@x.com" in first                # the root was on the sheet
    assert not (cfg.reports_dir / day / ".sent").exists()

    # the retry (fault cleared) must RESUME, not regenerate
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=15)) is True
    assert csv_path.read_bytes() == first, \
        "the resent sheet must be byte-identical, never a regeneration"
    assert docs and docs[-1] == first          # document re-sent, same bytes
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["accepted_reported_at"]
    from pipeline.tests.test_review_r5_driver import _window_hi
    assert (cfg.reports_dir / ".last_daily_sent").read_text() == \
        _window_hi(send).isoformat(timespec="seconds")
    assert (cfg.reports_dir / day / ".sent").exists()


def test_daily_post_stamp_kill_resends_the_identical_csv(cfg, ledger,
                                                         monkeypatch):
    """All stamps landed, marker missing (kill between anchor and marker):
    the retry re-sends the CSV on disk — pre-fix it regenerated a
    header-only sheet because every root was already stamped."""
    from pipeline import reports
    from pipeline.tests.test_review_r5_driver import _send_time
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    first = csv_path.read_bytes()
    (cfg.reports_dir / day / ".sent").unlink()
    builds = []
    monkeypatch.setattr(
        reports, "build_sheet_rows",
        lambda *a, **k: builds.append(1) or [])
    assert runmod.send_daily_report_if_due(
        cfg, ledger, _send_time(hour=15)) is True
    assert builds == [], "the resume path must never rebuild the sheet"
    assert csv_path.read_bytes() == first
    assert docs[-1] == first


# --------------- C7: the gate record is per-WINDOW, not just per-entry

def test_sibling_with_genuine_zero_keys_still_rejects(tmp_path):
    """One FIX_GATE_WINDOW step carries ALL windows with ONE aggregate
    inventory. A segment containing a window that destroyed NOTHING
    (blanked rows were already empty) inherited the OTHER segment's
    destroyed key frames, and validate downgraded its GENUINE
    INP_KEYS_MISSING to an advisory — a mouse-only segment shipped under
    a locked delivery bar on a false statement about it."""
    from pipeline import gate
    from pipeline import validate
    from pipeline.tests.test_r_loop7 import make_gate_csv
    work = tmp_path / "S"
    work.mkdir()
    # inputs only inside the FIRST window; the second window's rows are
    # already empty, so its share of the destroyed inventory is zero
    make_gate_csv(work, inputs={40: ("E", "interact"),
                                41: ("E", "interact"),
                                42: ("E", "interact")})
    note = gate.gate_windows(work, [(40.0, 42.0), (300.0, 302.0)])
    applied = [{"fix": "FIX_GATE_WINDOW", "ok": True,
                "params": {"windows": [[40.0, 42.0], [300.0, 302.0]]},
                "note": note}]
    parent = tmp_path / "dossiers" / "P"
    parent.mkdir(parents=True)
    fixmod._propagate_gate_record(
        parent, tmp_path / "dossiers", applied,
        [{"id": "P-p1", "t0": 0.0, "t1": 100.0},
         {"id": "P-p2", "t0": 200.0, "t1": 400.0}])
    g2 = validate._gate_destroyed(tmp_path / "dossiers" / "P-p2")
    assert g2["key_frames"] == 0, g2

    rep = {"duration_s": 100.0, "qa_issues": [], "vlm": {},
           "inventory": {"rows": 400, "distinct_actions": 3,
                         "actions": {"a": 1, "b": 1, "c": 1},
                         "key_frames": 0, "motion_frames": 50,
                         "btn_frames": 5, "irregular_pct": 0.0}}
    aux = {"has_raw": False, "vlm_required": False, "gate_destroyed": g2}
    res = validate.map_reasons(rep, aux)
    assert any(r["code"] == "INP_KEYS_MISSING" for r in res.reasons), \
        "a genuine zero-keys segment must still reject — pre-fix the " \
        "sibling's inherited key frames downgraded it to an advisory"


def test_legacy_gate_entries_still_propagate_whole(tmp_path):
    """Entries without per_window (older fixlogs) keep the r-loop-7
    whole-entry behaviour — never dropped, never narrowed."""
    parent = tmp_path / "dossiers" / "Q"
    parent.mkdir(parents=True)
    applied = [{"fix": "FIX_GATE_WINDOW", "ok": True,
                "params": {"windows": [[40.0, 42.0]]},
                "note": {"windows": [[38.0, 44.0]],
                         "destroyed": {"actions": ["interact"],
                                       "key_frames": 7}}}]
    fixmod._propagate_gate_record(
        parent, tmp_path / "dossiers", applied,
        [{"id": "Q-p1", "t0": 0.0, "t1": 100.0},
         {"id": "Q-p2", "t0": 200.0, "t1": 400.0}])
    from pipeline.validate import _gate_destroyed
    assert _gate_destroyed(tmp_path / "dossiers" / "Q-p1") == \
        {"actions": ["interact"], "key_frames": 7}
    assert not (tmp_path / "dossiers" / "Q-p2" / "fixlog.json").exists()


# ------------ C9: the interlock stays pinned under the fixture regime

def test_daily_interlock_pinned_under_the_fixture_regime(cfg, ledger,
                                                         monkeypatch,
                                                         capsys):
    """conftest's autouse fixture forces CONT_DAILY_REPORTS=True so the
    gate is knob-independent — which means the suppression itself needs
    an explicit False-monkeypatch test, or deleting the interlock would
    leave the suite green."""
    monkeypatch.setattr(C, "CONT_DAILY_REPORTS", False)
    at_2pm = datetime(2026, 8, 18, 14, 30, tzinfo=C.IST)
    assert runmod.send_daily_report_if_due(cfg, ledger, at_2pm) is False
    assert "suppressed — CONT_DAILY_REPORTS=False" in \
        capsys.readouterr().err
    assert not (cfg.reports_dir / "2026-08-18" / ".sent").exists()


# ---------------- C8: the STR_SJ_INVALID rewrite validates what it keeps

def _sj_round_trip(tmp_path, field, bad):
    """corrupt one field -> checker FAIL -> map -> plan -> apply -> checker.
    Returns the AFTER result."""
    from pipeline import validate
    from pipeline.tests.test_fix_cut_gate import _make_session
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80)
    s = json.loads((d / "session.json").read_text())
    s[field] = bad(s[field]) if callable(bad) else bad
    (d / "session.json").write_text(json.dumps(s))
    before = check_session_v2(d)
    assert before.status == "FAIL", "the corruption must trip the checker"
    reasons: list = []
    validate._map_qa_issues(before.issues, reasons, has_raw=False)
    assert any(r["code"] == "STR_SJ_INVALID" for r in reasons), reasons
    plan = fixmod.plan_fixes(reasons, game="kamla", has_raw=False)
    out = fixmod.apply_fixes(d, plan, game="kamla",
                             dossier_dir=tmp_path / "dossier")
    assert out["error"] is None, out
    return check_session_v2(d)


@needs_ffmpeg
@pytest.mark.parametrize("field,bad", [
    ("platform", "Windows"),
    ("localization", "english"),
    ("input_mouse_convention", {"maps_to": "camera_look_velocity"}),
    ("input_mouse_convention",
     {"maps_to": "camera_look_velocity", "dx_positive": "up",
      "dx_negative": "down", "dy_positive": "left",
      "dy_negative": "right"}),
    ("input_mouse_convention",
     {"maps_to": "look", "dx_positive": "right", "dx_negative": "left",
      "dy_positive": "down", "dy_negative": "up"}),
    ("created_at_utc", lambda orig: orig[:19].replace("T", " ") + "+00:00"),
    ("created_at_utc", lambda orig: orig[:19] + "+0000"),
], ids=["bad_platform", "bad_localization", "conv_partial",
        "conv_bad_axes", "conv_bad_mapsto", "space_separated_ts",
        "plus0000_ts"])
def test_sj_invalid_rewrite_actually_repairs(tmp_path, field, bad):
    """The rewrite defaulted only ABSENT/FALSY fields while the checker
    rejects PRESENT-but-invalid values — each of these survived BOTH
    attempts into a fix-failed reject with three paid sweeps. Unmapping
    instead would reject sessions the rewrite CAN repair, so the rewrite
    validates-and-overwrites."""
    after = _sj_round_trip(tmp_path, field, bad)
    assert after.status != "FAIL", after.issues


@needs_ffmpeg
def test_sj_naive_ts_control_still_repairs(tmp_path):
    """Control: the naive-stamp repair predates r-loop 8 and must keep
    working through the same chain."""
    after = _sj_round_trip(tmp_path, "created_at_utc",
                           lambda orig: orig[:19])
    assert after.status != "FAIL", after.issues


# ------------- C6: seal semantics — tree_sealed_at, one meaning per mark

def test_daily_send_self_mark_is_not_a_tree_seal(tmp_path):
    """A REJECTED root whose labels a sheet counted gets its own
    accepted_reported_at — which the old code read as a WHOLE-TREE seal,
    locking its live child's future hours out of every sheet forever."""
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import (
        UNFIXABLE, W1, W2, W3, _put, _row, _sheet)
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_0000000000000sa1"
    _put(led, root, state="REJECTED", raw=3600.0, reasons=UNFIXABLE,
         player="seal@x.com")
    _put(led, f"{root}-p1", state="VALIDATING", parent=root, raw=1800.0,
         player="seal@x.com")
    s1 = _row(_sheet(led, W1), "seal@x.com")
    assert s1["kamla_rejection_reasons"] == "black-frozen"
    assert led.get(root)["accepted_reported_at"], "labels were counted"

    led.update(f"{root}-p1", duration_delivered_s=1700.0,
               delivered_at="2026-08-15T10:00:00+00:00")
    led.set_state(f"{root}-p1", "DELIVERED")
    s2 = _row(_sheet(led, W2), "seal@x.com")
    assert s2 is not None, \
        "pre-fix: the root's own mark sealed the child's hours out forever"
    assert s2["kamla_accepted_hrs"] == round(1700 / 3600.0, 2)
    assert _row(_sheet(led, W3), "seal@x.com") is None      # once
    led.close()


def test_late_root_with_hold_vlm_node_is_not_deferred(tmp_path, capsys):
    """Post-split the late-arrival settle-deferral was pure loss: HOLD_VLM
    re-enters itself every 30 min, so 'settled' could be never and the
    whole tree reached NO sheet at all. Counted immediately now, loudly;
    each node's hours land via its own mark."""
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import (W2, W3, _put, _row,
                                                      _sheet)
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_0000000000000sb1"
    _put(led, root, state="SPLIT", raw=3600.0, player="hold@x.com")
    _put(led, f"{root}-p1", state="HOLD_VLM", parent=root, raw=1800.0,
         player="hold@x.com")
    _put(led, f"{root}-p2", state="DELIVERED", parent=root, raw=1800.0,
         delivered=1700.0, player="hold@x.com")
    # W1 never generated: the root arrives LATE at W2's generation
    s2 = _row(_sheet(led, W2), "hold@x.com")
    assert s2 is not None, "pre-fix: deferred to no sheet at all"
    assert s2["kamla_hrs_uploaded"] == 1.0
    assert s2["kamla_accepted_hrs"] == round(1700 / 3600.0, 2)
    assert "tree still in flight" in capsys.readouterr().err

    led.update(f"{root}-p1", duration_delivered_s=1600.0,
               delivered_at="2026-08-16T10:00:00+00:00")
    led.set_state(f"{root}-p1", "DELIVERED")
    s3 = _row(_sheet(led, W3), "hold@x.com")
    assert s3 is not None and s3["kamla_accepted_hrs"] == \
        round(1600 / 3600.0, 2)
    s4 = _row(_sheet(led, ("2026-08-17T06:45:22+00:00",
                           "2026-08-18T06:45:22+00:00")), "hold@x.com")
    assert s4 is None
    led.close()


def test_refix_mixed_tree_proceeds_with_paid_piece_memory(cfg, monkeypatch,
                                                          capsys):
    """REWRITTEN under ruling C (Adnaan 2026-08-18 at D0; supersedes the
    r-loop-8 skipped_mixed refusal — its reason, 'no per-node fidelity
    survives teardown', is gone): a mixed tree PROCEEDS. The paid piece
    is remembered; the previously-unpaid delivered node re-delivers and
    its hours land exactly once; the paid piece's re-delivery never
    counts again."""
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import (FIXABLE, W2, W3,
                                                      _put, _refix, _row,
                                                      _sheet)
    led = Ledger(cfg.ledger_path)
    m = "2026-08-14T09-00-00Z_kamla_c_0000000000000sm1"
    try:
        _put(led, m, state="SPLIT", raw=3600.0, player="mixed@x.com")
        _put(led, f"{m}-p1", state="DELIVERED", parent=m, raw=1800.0,
             delivered=1700.0, player="mixed@x.com")
        led.update(f"{m}-p1",
                   accepted_reported_at="2026-08-15T00:00:00+00:00")
        _put(led, f"{m}-p2", state="DELIVERED", parent=m, raw=1800.0,
             delivered=1600.0, player="mixed@x.com")     # unpaid
        _put(led, f"{m}-p3", state="REJECTED", parent=m, raw=100.0,
             player="mixed@x.com", reasons=FIXABLE)
        led.update(m, uploaded_reported_at="2026-08-15T00:00:00+00:00")
    finally:
        led.close()

    _refix(cfg, monkeypatch)
    out = capsys.readouterr().out
    assert '"skipped_mixed": []' in out
    assert "REFUSED" not in out

    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(m)["state"] == "DISCOVERED"     # torn down
        assert led.get(f"{m}-p2") is None              # subtree deleted
        assert led.paid_pieces_for(m) == {f"{m}-p1": 1700.0}
        assert led.get(m)["tree_sealed_at"] is None
        # the re-run re-delivers BOTH pieces (deterministic ids)
        _put(led, f"{m}-p1", state="DELIVERED", parent=m, raw=1800.0,
             delivered=1700.0, player="mixed@x.com")   # paid: excluded
        _put(led, f"{m}-p2", state="DELIVERED", parent=m, raw=1800.0,
             delivered=1600.0, player="mixed@x.com")   # unpaid: payable
        led.set_state(m, "SPLIT")
        s2 = _row(_sheet(led, W2), "mixed@x.com")
        assert s2 is not None
        assert s2["kamla_accepted_hrs"] == round(1600 / 3600.0, 2), \
            "exactly the previously-unpaid piece's hours — no more, no less"
        assert _row(_sheet(led, W3), "mixed@x.com") is None, "and only once"
    finally:
        led.close()


def test_refix_fully_paid_tree_never_recounted_end_to_end(cfg,
                                                          monkeypatch):
    """REWRITTEN under ruling C (Adnaan 2026-08-18 at D0; r-loop 9 #18):
    the fully-paid tree PROCEEDS (no seal). End-to-end through the
    sheet: the re-delivered same-id/same-seconds paid piece is never
    counted again, AND the recovered fix-failed sibling's hours reach a
    sheet exactly once — the money the refix path exists to recover,
    which the r-loop-8 seal swallowed forever."""
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import (FIXABLE, W2, W3,
                                                      _put, _refix, _row,
                                                      _sheet)
    led = Ledger(cfg.ledger_path)
    root = "2026-08-14T09-00-00Z_kamla_c_0000000000000sf1"
    try:
        _put(led, root, state="SPLIT", raw=3600.0, player="fp@x.com")
        _put(led, f"{root}-p1", state="DELIVERED", parent=root, raw=1800.0,
             delivered=1700.0, player="fp@x.com")
        led.update(f"{root}-p1",
                   accepted_reported_at="2026-08-15T00:00:00+00:00")
        _put(led, f"{root}-p2", state="REJECTED", parent=root, raw=1800.0,
             player="fp@x.com", reasons=FIXABLE)
        led.update(root, uploaded_reported_at="2026-08-15T00:00:00+00:00")
    finally:
        led.close()

    _refix(cfg, monkeypatch)

    led = Ledger(cfg.ledger_path)
    try:
        row = led.get(root)
        assert row["tree_sealed_at"] is None, "ruling C: never seal"
        assert row["accepted_reported_at"] is None
        assert led.paid_pieces_for(root) == {f"{root}-p1": 1700.0}
        # the re-run re-delivers the SAME paid piece and RECOVERS p2
        _put(led, f"{root}-p1", state="DELIVERED", parent=root,
             raw=1800.0, delivered=1700.0, player="fp@x.com")
        _put(led, f"{root}-p2", state="DELIVERED", parent=root,
             raw=1800.0, delivered=1500.0, player="fp@x.com")
        led.set_state(root, "SPLIT")
        s2 = _row(_sheet(led, W2), "fp@x.com")
        assert s2 is not None, \
            "the recovered hours must reach a sheet (the #18 loss)"
        assert s2["kamla_accepted_hrs"] == round(1500 / 3600.0, 2), \
            "recovered p2 counted; re-delivered paid p1 excluded"
        assert _row(_sheet(led, W3), "fp@x.com") is None, "exactly once"
    finally:
        led.close()


def test_supersede_clears_the_tree_seal(tmp_path):
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import _put
    led = Ledger(tmp_path / "l.db")
    sid = "2026-08-14T09-00-00Z_kamla_c_0000000000000ss1"
    _put(led, sid, state="DELIVERED", raw=3600.0, delivered=3400.0)
    led.update(sid, tree_sealed_at="2026-08-15T00:00:00+00:00")
    led.supersede(sid, new_md5="zz", new_bytes=2,
                  new_ctime="2026-08-16T00:00:00.000Z",
                  dossier_root=tmp_path / "dossiers")
    assert led.get(sid)["tree_sealed_at"] is None
    led.close()


def test_rebuild_reset_clears_the_tree_seal(cfg, monkeypatch):
    import sys as _sys

    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import _load, _put
    reset = _load("recal_rebuild_reset")
    led = Ledger(cfg.ledger_path)
    sid = "2026-08-14T09-00-00Z_kamla_c_0000000000000sr1"
    try:
        _put(led, sid, state="DELIVERED", raw=3600.0, delivered=3400.0)
        led.update(sid, tree_sealed_at="2026-08-15T00:00:00+00:00")
    finally:
        led.close()
    parachute = cfg.home / "parachute.db"
    parachute.write_bytes(b"x" * 2048)
    monkeypatch.setenv("HL_PIPELINE_HOME", str(cfg.home))
    monkeypatch.setattr(_sys, "argv",
                        ["recal_rebuild_reset.py", "--yes",
                         "--backup", str(parachute)])
    assert reset.main() == 0
    led = Ledger(cfg.ledger_path)
    try:
        assert led.get(sid)["tree_sealed_at"] is None
    finally:
        led.close()


def test_quarantine_heal_clears_the_tree_seal(cfg, ledger, monkeypatch):
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    sid = "2026-08-14T10-00-00Z_kamla_c_0123456789abcdef"
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="op@x.com",
        player_email="p1@x.com", drive_path="kamla/BADPATH",
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video="old",
        bytes_=1, state="DISCOVERED")
    ledger.set_state(sid, "QUARANTINED")
    ledger.update(sid, tree_sealed_at="2026-08-15T00:00:00+00:00")
    entries = make_session_entries(sid=sid)
    monkeypatch.setattr(ingest, "list_drive", lambda _c: entries)
    ingest.scan(cfg, ledger, entries)
    row = ledger.get(sid)
    assert row["state"] == "DISCOVERED"
    assert row["tree_sealed_at"] is None


def test_daily_unreadable_record_refuses_loudly(cfg, ledger, monkeypatch,
                                                capsys):
    """A torn record is an unknown: regenerating post-stamp could ship a
    shrunken sheet, so the send refuses and says so — a human reconciles."""
    from pipeline.tests.test_review_r5_driver import _send_time
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_bytes(b"SENTINEL-SHEET")
    (cfg.reports_dir / day / ".daily-counted.json").write_text("{torn")
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is False
    assert csv_path.read_bytes() == b"SENTINEL-SHEET"
    assert not (cfg.reports_dir / day / ".sent").exists()
    assert "REFUSING to regenerate" in capsys.readouterr().err
