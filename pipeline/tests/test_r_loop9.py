"""r-loop 9 fixes (D1–D8) — pipeline side.

Each test cites the R9_FINDINGS.md number it pins. Fail-first proofs run in
a scratch copy of the pre-fix tree (session scratchpad), per plan §1.
"""
from __future__ import annotations

import csv
import json
from datetime import timedelta

import pytest

from pipeline import fix as fixmod
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
