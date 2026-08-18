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
