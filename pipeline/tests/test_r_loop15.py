"""r-loop 15 fixes (I1–I8, R8_IMPLEMENTATION_PLAN §3) — pipeline side.

Each test cites the iteration-15 finding it pins (r15 #N, findings of
record in R15_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at ce26148 (session scratchpad), per plan §1/§3.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from pipeline.tests.test_r_loop8 import needs_ffmpeg


# ------- r15 #4 (I1): hygiene is idempotent on a symbol-key session


@needs_ffmpeg
def test_key_hygiene_is_a_noop_on_a_symbol_key_session(tmp_path):
    """r15 #4 (I1): FIX_KEY_HYGIENE re-tokenizes through the same
    key_display the writer used, so on a symbol-bind session it strips
    nothing — and pre-fix the re-check FAILed identically, burning both
    attempts into a terminal reject. Post-fix the checker and writer
    agree by construction: hygiene strips 0 and the re-check is clean."""
    from pipeline import fix as fixmod
    from translator import v2
    from translator.tests.test_r_loop15_translator import _symbol_bundle
    d = _symbol_bundle(tmp_path)
    rep = v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    work = tmp_path / "work"
    shutil.copytree(Path(rep["out_dir"]), work)
    (work / "session.rrd").touch()
    raw = work / "raw"
    raw.mkdir(exist_ok=True)
    shutil.copy2(d / "keybind.json", raw / "keybind.json")
    note = fixmod.fix_key_hygiene(work, "kamla")
    assert "stripped 0 tokens" in note, note
    r = v2.check_session_v2(work)
    assert not any("non-v2 key tokens" in i for i in r.issues), r.issues
