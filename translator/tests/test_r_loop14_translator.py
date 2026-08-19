"""r-loop 14 fixes (H7/H8, R8_IMPLEMENTATION_PLAN §3) — translator/tools
side.

Each test cites the iteration-14 finding it pins (r14 #N, findings of
record in R14_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 5f7015b (session scratchpad), per plan §1/§3.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# v2 accessed by attribute, and safe_session_id imported inside its
# test, so the fail-first scratch run fails PER TEST (r9 pattern)
from translator import v2
from translator.tests.test_r_loop9_translator import (_GOOD_REC, _bundle,
                                                      needs_ffmpeg)


# ------- r14 #8 (H7): safe_session_id rejects control characters

@needs_ffmpeg
@pytest.mark.parametrize("sid", ["abc\x00def", "ab\tcd"],
                         ids=["nul", "tab"])
def test_control_char_session_id_falls_back_to_folder_name(tmp_path, sid):
    """r14 #8 (H7): an embedded NUL passed safe_session_id and crashed
    every join's resolve()/mkdir with an untyped ValueError — burning
    both fix attempts into a terminal 'fix retries exhausted' reject
    (raw path) or crashing the G7 operator tools mid-batch. Garbage ids
    are DESIGNED to degrade to the bundle-folder-name fallback; control
    characters now do."""
    d = _bundle(tmp_path, {"session_id": sid,
                           "game": {"name": "Kamla"},
                           "recording": dict(_GOOD_REC)})
    out_root = tmp_path / "out"
    rep = v2.translate_bundle_v2(d, out_root, make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    out_dir = Path(rep["out_dir"]).resolve()
    assert out_dir.is_relative_to(out_root.resolve()), out_dir
    assert rep["session"] == "bundle", \
        "control-char ids fall back to the folder name, like non-str ones"


def test_safe_session_id_rejects_control_characters(tmp_path):
    """H7 unit pin at the shared decision point (all five join sites),
    with the clean-id control alongside."""
    from translator.translate import safe_session_id
    assert safe_session_id("abc\x00def", tmp_path / "b") == "b"
    assert safe_session_id("ab\tcd", tmp_path / "b") == "b"
    assert safe_session_id("ab\ncd", tmp_path / "b") == "b"
    assert safe_session_id("ok-id_1.2", tmp_path / "b") == "ok-id_1.2"


# ------- r14 #9 (H8): analyze_sample verdicts judge the probed duration

def test_analyze_short_gate_judges_the_probed_duration():
    """r14 #9 (H8): build_verdict issued the re-record verdict from the
    player-typed duration claim while the probed truth sat in
    a.video_probe — a corrupt-small claim (59.9 claimed, 600s probed,
    the inverse of the r12 #9 ms-in-seconds class) told the operator to
    re-record good footage that needs only a session.json rewrite."""
    from tools.analyze_sample import SessionAnalysis, build_verdict
    a = SessionAnalysis("s", "o")
    a.duration_s = 59.9                       # corrupt-small claim
    a.video_probe = {"duration_s": 600.0}     # probed truth
    build_verdict(a, vlm_ran=False)
    assert not any("70s delivery minimum" in m
                   for m in a.verdict_reasons), a.verdict_reasons
    assert not a.verdict.startswith("re-record")


def test_analyze_short_gate_claim_is_only_the_fallback():
    """H8 control (the other side): with no probe result the claim
    still gates — the fallback stays."""
    from tools.analyze_sample import SessionAnalysis, build_verdict
    a = SessionAnalysis("s", "o")
    a.duration_s = 59.9
    a.video_probe = {}
    build_verdict(a, vlm_ran=False)
    assert any("70s delivery minimum" in m for m in a.verdict_reasons)
    assert a.verdict.startswith("re-record")


def test_analyze_tail_window_judged_on_probed_end():
    """r14 #9 (H8): a corrupt-LARGE claim made every genuine tail
    window read as MID-CLIP ('no mid-clip cut tool exists … decide a
    trim strategy or re-record') instead of the tail-edge retrim
    advice — the operator acts on the wrong coaching."""
    from tools.analyze_sample import SessionAnalysis, build_verdict
    a = SessionAnalysis("s", "o")
    a.duration_s = 300000.0                   # ms-in-seconds corruption
    a.video_probe = {"duration_s": 300.0}
    a.vlm = {"windows": [{"labels": ["menu"], "t0": 294.0, "t1": 300.0,
                          "gating": True, "n_samples": 2,
                          "tier": "high", "inputs": {}}],
             "samples": [1]}
    build_verdict(a, vlm_ran=True)
    msgs = a.verdict_reasons
    assert any("at clip END" in m for m in msgs), msgs
    assert not any("mid-clip" in m for m in msgs), msgs
