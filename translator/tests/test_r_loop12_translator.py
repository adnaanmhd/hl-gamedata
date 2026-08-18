"""r-loop 12 — translator side (#10: OverflowError arms).

Fail-first proofs run in a scratch copy of the pre-fix tree at 11af5a0
(session scratchpad), per plan §1.
"""
from __future__ import annotations

import csv

import pytest

from translator.tests.test_r_loop9_translator import needs_ffmpeg


@needs_ffmpeg
def test_bigint_timestamp_cell_degrades_to_a_typed_fail(tmp_path):
    """int() parses arbitrary-precision timestamp cells and the
    frame-sync compare's bigint-minus-float converts to float — a
    '9'*400 cell raised OverflowError OUT of check_session_v2,
    destroying the typed non-monotonic FAIL it had already recorded
    (degrade, never crash)."""
    from pipeline.tests.test_fix_cut_gate import _make_session
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80, name="bigts")
    with (d / "frames.csv").open(newline="") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    ti = header.index("timestamp_ms")
    body[10][ti] = "9" * 400
    with (d / "frames.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)
    r = check_session_v2(d)          # must not raise
    assert r.status == "FAIL", r.issues


# ------- r12 #15: the F9 v1-JOIN pinned end to end (the helper test
# ------- alone let a call-site revert survive with the gate green)

@needs_ffmpeg
def test_v1_translate_bundle_traversal_stays_inside_out(tmp_path):
    from pathlib import Path

    from translator import translate as v1mod
    from translator.tests.test_r_loop9_translator import _GOOD_REC, _bundle
    d = _bundle(tmp_path, {"session_id": "../../../../ESCAPED_v1",
                           "game": {"name": "Kamla"},
                           "recording": dict(_GOOD_REC)})
    out_root = tmp_path / "out"
    rep = v1mod.translate_bundle(d, out_root, do_trim=False,
                                 make_rrd=False)
    out_dir = Path(rep["out_dir"]).resolve()
    assert out_dir.is_relative_to(out_root.resolve()), out_dir
    assert rep["session"] == "bundle"
    assert not (tmp_path / "ESCAPED_v1").exists()
    assert not (tmp_path.parent / "ESCAPED_v1").exists()
