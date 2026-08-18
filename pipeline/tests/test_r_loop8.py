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
