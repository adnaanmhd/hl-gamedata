"""r-loop 16 fixes (J-set, R8_IMPLEMENTATION_PLAN §0) — translator side.

Each test cites the iteration-16 finding it pins (r16 #N, findings of
record in R16_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 4dc37b4 (session scratchpad), per plan §1/§4.
"""
from __future__ import annotations

import json
from pathlib import Path

# v2 accessed by attribute so the fail-first scratch run fails PER TEST
from translator import v2
from translator.tests.test_r_loop9_translator import (_GOOD_REC, _bundle,
                                                      needs_ffmpeg)


# ------- r16 #1≡#4 (J1): safe_session_id rejects unencodable ids


def test_lone_surrogate_session_id_falls_back(tmp_path):
    """r16 #1≡#4 (J1): the I5 byte-length clause measured the id with
    encode('utf-8', 'ignore'), which silently DROPS what utf-8 cannot
    encode — so a JSON-legal lone surrogate (json.loads accepts the
    \\ud800 escape; a real UTF-16-origin corruption class for a Windows
    capture tool) passed the shared decision point and every join then
    strict-encoded it: UnicodeEncodeError, a ValueError the fix lane
    classifies session — both attempts burned, wrongful terminal
    reject, and both G7 operator tools killed mid-batch. Unencodable
    ids now take the designed folder-name fallback."""
    from translator.translate import safe_session_id
    assert safe_session_id("abc\ud800def", tmp_path / "b") == "b"
    assert safe_session_id("\udfff" * 4, tmp_path / "b") == "b"
    # controls: encodable ids keep the I5 semantics exactly
    assert safe_session_id("ok-id_1.2", tmp_path / "b") == "ok-id_1.2"
    assert safe_session_id("x" * 200, tmp_path / "b") == "x" * 200
    assert safe_session_id("x" * 201, tmp_path / "b") == "b"


@needs_ffmpeg
def test_lone_surrogate_session_id_e2e_translates_under_fallback(tmp_path):
    """J1 e2e: the surrogate arrives through the production shape — an
    ASCII \\ud800 escape in metadata.json survives the errors='replace'
    read and json.loads — and the REAL v2 join must translate under
    the folder-name fallback instead of crashing UnicodeEncodeError at
    the out_dir resolve/mkdir."""
    d = _bundle(tmp_path, {"session_id": "abc\ud800def",
                           "game": {"name": "Kamla"},
                           "recording": dict(_GOOD_REC)})
    out_root = tmp_path / "out"
    rep = v2.translate_bundle_v2(d, out_root, make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    out_dir = Path(rep["out_dir"]).resolve()
    assert out_dir.is_relative_to(out_root.resolve()), out_dir
    assert rep["session"] == "bundle", \
        "unencodable ids fall back to the folder name, like NUL ones"
    s = json.loads((out_dir / "session.json").read_text())
    assert s["session_id"] == "bundle"
