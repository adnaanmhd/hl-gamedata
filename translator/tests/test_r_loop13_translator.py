"""r-loop 13 — translator side (G4, r13 #6: OverflowError arms
completed in the checker).

Fail-first proofs run in a scratch copy of the pre-fix tree at b69fee1
(session scratchpad), per plan §1/§3.
"""
from __future__ import annotations

import csv
import json

import pytest

from translator.tests.test_r_loop9_translator import needs_ffmpeg


@needs_ffmpeg
@pytest.mark.parametrize("field", ["duration_ms", "duration_seconds",
                                   "fps", "frame_count"])
def test_bigint_session_field_degrades_to_a_typed_fail(tmp_path, field):
    """r13 #6: json.loads parses an unbounded integer literal to a
    Python bigint, and _check_session_json's arithmetic
    (duration_ms/1000.0, fps*duration_seconds) raised OverflowError
    past the (TypeError, ValueError) tuple — the checker crashed and
    the session went terminally QUARANTINED 'validation crashed'
    instead of a typed, fixable FAIL. The r12 #10 arm guarded only the
    frame-sync max() and inventory(), which these triggers never
    reach."""
    from pipeline.tests.test_fix_cut_gate import _make_session
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80, name=f"big_{field}")
    s = json.loads((d / "session.json").read_text())
    s[field] = 10 ** 400
    (d / "session.json").write_text(json.dumps(s))
    r = check_session_v2(d)          # must not raise
    assert r.status == "FAIL", r.issues


@needs_ffmpeg
def test_all_bigint_timestamp_column_degrades_to_a_typed_fail(tmp_path):
    """r13 #6, second trigger: with every timestamp_ms cell a bigint,
    the spacing median is itself a bigint and `0.2 * med` raised with
    no guard — before the guarded frame-sync arm was ever reached."""
    from pipeline.tests.test_fix_cut_gate import _make_session
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80, name="bigcol")
    with (d / "frames.csv").open(newline="") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    ti = header.index("timestamp_ms")
    for i, row in enumerate(body):
        row[ti] = str((i + 1) * 10 ** 400)   # monotonic, bigint spacing
    with (d / "frames.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)
    r = check_session_v2(d)          # must not raise
    assert r.status == "FAIL", r.issues


@needs_ffmpeg
def test_bigint_duration_with_raw_sidecars_skips_the_raw_recheck(
        tmp_path):
    """r13 #6 sweep: _verify_against_raw computes
    float(s['duration_seconds'])*1e6 and its except tuple lacked
    OverflowError — a bigint claim on a session WITH raw sidecars
    crashed the checker there even after _check_session_json was
    fixed. It must degrade to the documented warn-skip.

    De-vacuoused by r14 #12 (H9b): check_session_v2 calls
    _verify_against_raw ONLY when the caller passes raw_bundle (there
    is no auto-detection) — the original bare call never reached site
    4 at all, and reverting BOTH site-4 arms ran the full suite green.
    Now drives the production call shape (validate.py/analyze_sample
    pass raw_bundle) and pins the specific degrade line.
    Mutation-proofed with the finder's exact revert in a HEAD scratch
    copy."""
    from datetime import timedelta

    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.tests.test_r_loop8 import _created_at, _sidecars
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80, name="bigraw")
    _sidecars(d, _created_at(d) - timedelta(seconds=0), [])
    s = json.loads((d / "session.json").read_text())
    s["duration_seconds"] = 10 ** 400
    (d / "session.json").write_text(json.dumps(s))
    r = check_session_v2(d, raw_bundle=d / "raw")     # must not raise
    assert r.status == "FAIL", r.issues
    assert any("raw verification skipped" in i and "OverflowError" in i
               for i in r.issues), \
        f"site 4 must degrade to the documented warn-skip: {r.issues}"


@needs_ffmpeg
def test_bigint_event_t_with_inf_duration_skips_the_event(tmp_path):
    """r14 #12 (H9b), the per-event sibling arm: an inf-class duration
    claim (JSON 1e999) corrupts end_us, so a bigint mouse_raw t passes
    the window test and int(e['t'] - head_us) overflows — the guarded
    arm skips the garbage event instead of crashing the checker.
    Mutation-proofed alongside the tuple arm (both site-4 halves must
    be live for these two tests to pass)."""
    from datetime import timedelta

    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.tests.test_r_loop8 import _created_at, _sidecars
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80, name="bigevt")
    _sidecars(d, _created_at(d) - timedelta(seconds=0),
              [{"t": 10 ** 400, "type": "mouse_raw", "dx": 1, "dy": 1}])
    s = json.loads((d / "session.json").read_text())
    s["duration_seconds"] = float("inf")     # the JSON 1e999 class
    (d / "session.json").write_text(json.dumps(s))
    r = check_session_v2(d, raw_bundle=d / "raw")     # must not raise
    assert r.status in ("PASS", "FAIL", "WARN"), r.issues
