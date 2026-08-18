"""r-loop 11 — translator side (F9: #12).

Player-supplied metadata.json session_id was joined into the output path
unsanitized: '../../../../ESCAPED_dir' wrote all four delivery files
OUTSIDE out/ — path traversal on the pipeline VM. The r-loop-9 guard
closed only the non-str crash. Fail-first proofs run in a scratch copy
of the pre-fix tree at 1500d95 (session scratchpad), per plan §1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# v2 accessed by attribute, and safe_session_id imported inside its
# test, so the fail-first scratch run fails PER TEST (r9 pattern)
from translator import v2
from translator.tests.test_r_loop9_translator import (_GOOD_REC, _bundle,
                                                      needs_ffmpeg)


@needs_ffmpeg
@pytest.mark.parametrize("sid", ["../../../../ESCAPED_dir",
                                 "a/b", "a\\b", ".."],
                         ids=["traversal", "slash", "backslash", "dotdot"])
def test_traversal_session_id_stays_inside_out(tmp_path, sid):
    d = _bundle(tmp_path, {"session_id": sid,
                           "game": {"name": "Kamla"},
                           "recording": dict(_GOOD_REC)})
    out_root = tmp_path / "out"
    rep = v2.translate_bundle_v2(d, out_root, make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    out_dir = Path(rep["out_dir"]).resolve()
    assert out_dir.is_relative_to(out_root.resolve()), out_dir
    assert rep["session"] == "bundle", \
        "unsafe ids fall back to the folder name, like non-str ones"
    assert not (tmp_path.parent / "ESCAPED_dir").exists()
    assert not (tmp_path / "ESCAPED_dir").exists()


@needs_ffmpeg
def test_clean_session_id_is_kept(tmp_path):
    """Control: ordinary ids (dots and dashes included) are untouched."""
    d = _bundle(tmp_path, {"session_id": "2026-08-14T10-00-00Z_kamla.v1",
                           "game": {"name": "Kamla"},
                           "recording": dict(_GOOD_REC)})
    rep = v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    assert rep["session"] == "2026-08-14T10-00-00Z_kamla.v1"


def test_v1_translate_join_is_sanitized(tmp_path):
    """The v1 translate_bundle has the identical unsanitized join — pin
    the shared guard directly (the v1 path needs no ffmpeg for this)."""
    from translator.translate import safe_session_id
    assert safe_session_id("../../../evil", tmp_path / "b") == "b"
    assert safe_session_id(12345, tmp_path / "b") == "b"
    assert safe_session_id("ok-id_1.2", tmp_path / "b") == "ok-id_1.2"
