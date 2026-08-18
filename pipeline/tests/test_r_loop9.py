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
