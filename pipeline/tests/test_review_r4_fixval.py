"""Review-r4 regression tests: fix-plan header ordering (#23), aux-note
advisories (#18/#22), locked report stale-break/delete (#38/#45, #7),
vlm key scrub on InvalidURL (#25), cutter manifest protocol (#12)."""
import http.client
import json
import os
import time as _time

import pytest

from pipeline import cutter, fix, validate, vlm
from pipeline.tests.test_fix_cut_gate import _make_session, _r, needs_ffmpeg
from pipeline.tests.test_validate_mapper import aux, rep
from pipeline.validate import map_reasons

# ------------------------------------------- FIX_HEADER_REWRITE ordering


def test_header_rewrite_precedes_gate_window():
    # review-r4 #23: gate.py hard-asserts a v2 header — a plan reaching
    # FIX_GATE_WINDOW first would error every attempt and burn the budget
    plan = fix.plan_fixes(
        [_r("STR_HEADER_BAD"),
         _r("INP_FROZEN_ACTIONS", params={"t0": 60.0, "t1": 61.5})],
        game="outer_wilds", has_raw=False)
    ids = [s[0] for s in plan["steps"]]
    assert ids.count("FIX_HEADER_REWRITE") == 1
    assert ids.index("FIX_HEADER_REWRITE") < ids.index("FIX_GATE_WINDOW")


def test_header_rewrite_precedes_cut_segments():
    # review-r4 #23: cutter asserts the v2 header too
    plan = fix.plan_fixes(
        [_r("STR_HEADER_BAD"),
         _r("CNT_MID_NONGAMEPLAY", params={"cut": [100.0, 110.0]})],
        game="kamla", has_raw=False)
    ids = [s[0] for s in plan["steps"]]
    assert ids.count("FIX_HEADER_REWRITE") == 1
    assert ids.index("FIX_HEADER_REWRITE") < ids.index("FIX_CUT_SEGMENTS")


def test_header_rewrite_precedes_retrim_head_exactly_once():
    # review-r4 #23: retrim tool asserts the v2 header; the pre-emit must
    # not double-plan the rewrite via the later csv loop
    plan = fix.plan_fixes(
        [_r("STR_HEADER_BAD"),
         _r("CNT_EDGE_NONGAMEPLAY", params={"edge": "head",
                                            "cut_at_s": 12.5})],
        game="kamla", has_raw=False)
    ids = [s[0] for s in plan["steps"]]
    assert ids.count("FIX_HEADER_REWRITE") == 1
    assert ids.index("FIX_HEADER_REWRITE") < ids.index("FIX_RETRIM_HEAD")


# --------------------------------------------- aux notes -> advisories


def test_aux_notes_surface_as_advisories():
    # review-r4 #18/#22: aux["notes"] was write-only — it must reach the
    # MapResult advisories (and thus verdict.json)
    notes = ["scanner failed: cv2 exploded",
             "static-window cap hit: 12 windows not sent to VLM"]
    res = map_reasons(rep(), aux(notes=notes), "kamla")
    for n in notes:
        assert n in res.advisories


# ------------------------------------- locked report update/remove


def test_locked_update_breaks_stale_lock_and_merges(tmp_path):
    # review-r4 #38/#45: a lock orphaned >120s is broken by rename-aside,
    # the update still lands, and neither lock nor grave survives
    report = tmp_path / "translation_report.json"
    report.write_text(json.dumps({"other": {"shift_us": 1}}))
    lock = tmp_path / "translation_report.json.lock"
    lock.mkdir()
    past = _time.time() - 200
    os.utime(lock, (past, past))
    validate._locked_report_update(report, "sess-x", {"shift_us": 33333})
    data = json.loads(report.read_text())
    assert data["sess-x"] == {"shift_us": 33333}
    assert data["other"] == {"shift_us": 1}          # merged, not clobbered
    assert not lock.exists()
    assert not list(tmp_path.glob("*.stale-*"))      # grave cleaned up


def test_locked_remove_deletes_only_target(tmp_path):
    # review-r4 #7: supersede must drop ONLY the old sid's shift record
    report = tmp_path / "translation_report.json"
    report.write_text(json.dumps({"sess-gone": {"shift_us": 5},
                                  "other": {"shift_us": 1}}))
    validate._locked_report_remove(report, "sess-gone")
    data = json.loads(report.read_text())
    assert "sess-gone" not in data
    assert data["other"] == {"shift_us": 1}


def test_locked_remove_missing_file_is_noop(tmp_path):
    # review-r4 #7: removing from a nonexistent report must not create it
    report = tmp_path / "translation_report.json"
    validate._locked_report_remove(report, "sess-x")
    assert not report.exists()
    assert not (tmp_path / "translation_report.json.lock").exists()


# --------------------------------------------- vlm key scrub (#25)


def test_invalid_url_error_scrubs_key(monkeypatch):
    # review-r4 #25: http.client.InvalidURL embeds the full request URL
    # (?key=...) in its message; the split key must never reach VLMError
    def bad_post(url, headers, body, timeout_s=180):
        raise http.client.InvalidURL(
            "URL can't contain control characters. "
            "'/v1/publishers/google/models/x:generateContent"
            "?key=sekritAAA part2' (found at least ' ')")

    monkeypatch.setattr(vlm, "_post", bad_post)
    monkeypatch.setattr(vlm.time, "sleep", lambda s: None)   # fast retries
    with pytest.raises(vlm.VLMError) as ei:
        vlm.generate("sekritAAA part2", "gemini-3.7-flash", [{"text": "x"}])
    msg = str(ei.value)
    assert "sekritAAA" not in msg
    assert "key=***" in msg


# ------------------------------------ cutter manifest protocol (#12)


@needs_ffmpeg
def test_cut_success_writes_manifest_listing_segments(tmp_path):
    # review-r4 #12: the manifest marks the cut COMPLETE and names exactly
    # the produced segment ids
    d = _make_session(tmp_path, seconds=80)
    out = tmp_path / "out"
    res = cutter.cut_segments(d, [(0.0, 80.0)], out)
    assert len(res["segments"]) >= 1
    sid = json.loads((d / "session.json").read_text())["session_id"]
    man = out / f"{sid}.split-manifest.json"
    assert man.exists()
    data = json.loads(man.read_text())
    assert data["segments"] == [g["id"] for g in res["segments"]]


@needs_ffmpeg
def test_cut_failure_leaves_no_partials_and_no_manifest(tmp_path,
                                                        monkeypatch):
    # review-r4 #12: a mid-cut failure must wipe this attempt's segment
    # dirs and never write the manifest (else recovery adopts a bogus split)
    d = _make_session(tmp_path, seconds=80)
    out = tmp_path / "out"
    real_fp = cutter.V.frame_pts
    calls = {"n": 0}

    def fp(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_fp(path)          # source read succeeds
        raise RuntimeError("segment PTS read exploded")

    monkeypatch.setattr(cutter.V, "frame_pts", fp)
    with pytest.raises(RuntimeError):
        cutter.cut_segments(d, [(0.0, 80.0)], out)
    assert calls["n"] >= 2                # failed on a SEGMENT read
    sid = json.loads((d / "session.json").read_text())["session_id"]
    assert not list(out.glob(f"{sid}-p*"))
    assert not (out / f"{sid}.split-manifest.json").exists()
