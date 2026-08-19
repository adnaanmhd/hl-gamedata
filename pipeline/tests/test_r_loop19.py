"""r-loop 19 fixes (M-set, R8_IMPLEMENTATION_PLAN §0) — pipeline side.

Each test cites the iteration-19 finding it pins (r19 #N, findings of
record in R19_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 06ecd72 (session scratchpad); pin-only tests use
the mutation-proof pattern with the finders' EXACT mutants, per plan
§0/§1. The M fixes land landed-but-UNREVIEWED only if iteration 20 is
not run over them — per the 2026-08-20 ruling iteration 20 reviews
this set.
"""
from __future__ import annotations

import json

from pipeline.tests.test_r_loop8 import needs_ffmpeg  # noqa: F401


# ------- r19 #2 (M2): "usable" sidecars require what retranslate needs


def _raw_dir(tmp_path, metadata_text, name="m2work"):
    work = tmp_path / name
    (work / "raw").mkdir(parents=True)
    (work / "raw" / "inputs.jsonl").write_text('{"t": 0}\n')
    if metadata_text is not None:
        (work / "raw" / "metadata.json").write_text(metadata_text)
    return work


_SYNC_REASON = [{"code": "SYN_TS_NOT_PTS", "blocking": True,
                 "fixable": True, "params": {}, "evidence": "e"}]


def test_semantically_unusable_metadata_plans_csv_level(tmp_path):
    """r19 #2 (M2): the L2 gate required only a dict parse, but
    retranslate_from_sidecars hard-requires a parseable
    recording.started_at_utc to derive the head offset — dict metadata
    without one planned a FIX_RETRANSLATE that raised the same typed
    FixFailed on both attempts, superseding the CSV-level repair that
    would have delivered the session (nothing can repair
    raw/metadata.json between attempts). Every semantically-unusable
    shape now reads as no-sidecars and the CSV fallback plans. The
    junk-STRING stamp kills the presence-not-parseability mutant
    (§2 rule 4)."""
    from pipeline import fix as fixmod
    for i, meta in enumerate((
            '{"recording": {}}',
            '{"recording": {"started_at_utc": 12345}}',
            '{"recording": {"started_at_utc": "10/08/2026 15:34"}}',
            '{"recording": "junk"}',
            '{"game": {"name": "Kamla"}}',
    )):
        work = _raw_dir(tmp_path, meta, name=f"m2-{i}")
        assert fixmod.has_raw_sidecars(work) is False, meta
        plan = fixmod.plan_fixes(_SYNC_REASON, game="kamla",
                                 has_raw=fixmod.has_raw_sidecars(work))
        ids = [fid for fid, _p in plan["steps"]]
        assert "FIX_TSREPAIR_PTS" in ids and "FIX_RETRANSLATE" not in ids


def test_naive_started_at_still_reads_usable(tmp_path):
    """M2 proceed-side control (§2 rule 4): the gate judges usability
    with retranslate's OWN parse (_utc), under which a NAIVE stamp is
    UTC by contract — the gate must not refuse what the consumer
    accepts. The aware control lives in the updated L2/r-loop-7 pins."""
    from pipeline import fix as fixmod
    work = _raw_dir(
        tmp_path, '{"recording": {"started_at_utc": "2026-08-14T10:00:00"}}')
    assert fixmod.has_raw_sidecars(work) is True
    plan = fixmod.plan_fixes(_SYNC_REASON, game="kamla",
                             has_raw=fixmod.has_raw_sidecars(work))
    assert ("FIX_RETRANSLATE", {"rerouted": False}) in \
        [(f, p) for f, p in plan["steps"]]


def test_retranslate_nondict_recording_is_typed(tmp_path):
    """M2 sibling (same commit): retranslate_from_sidecars read the
    stamp via (meta.get("recording") or {}).get(...) — a truthy
    non-dict "recording" block crashed an untyped AttributeError (not
    even in the host tuple). It now degrades to the existing typed
    'cannot derive the head offset' FixFailed."""
    import pytest

    from pipeline import fix as fixmod
    work = _raw_dir(tmp_path, '{"recording": "junk"}', name="m2rec")
    (work / "session.json").write_text(
        json.dumps({"created_at_utc": "2026-08-14T10:00:05Z"}))
    with pytest.raises(fixmod.FixFailed) as e:
        fixmod.retranslate_from_sidecars(work)
    assert "head offset" in str(e.value)
