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


# ------- r19 #1 (BLOCKER) / #4≡#6 / #10 (M1): fix_v1_to_v2 never
# ------- fabricates the head-offset contract


def _v1_with_sidecars(tmp_path, created_at, name, started_at,
                      trim, at_root=True):
    """A v1 work dir whose raw sidecars make the head-offset contract
    live — at the work ROOT (the first-run shape) or in raw/ (the
    re-entrant shape), per fix_v1_to_v2's two-location rule."""
    from pipeline.tests.test_r_loop15 import _v1_work
    work = _v1_work(tmp_path, created_at, name)
    s = json.loads((work / "session.json").read_text())
    if trim is not ...:
        s["canonical"]["trim"] = trim
    (work / "session.json").write_text(json.dumps(s))
    if at_root == "mixed":
        # the crash-between-moves split: the re-entrant run reunites
        # the pair in raw/, so the contract is live and the probe must
        # see it (kills the pair-per-location restatement)
        (work / "raw").mkdir(exist_ok=True)
        ibase, mbase = work / "raw", work
    else:
        base = work if at_root else work / "raw"
        base.mkdir(exist_ok=True)
        ibase = mbase = base
    (ibase / "inputs.jsonl").write_text('{"t": 0}\n')
    (mbase / "metadata.json").write_text(json.dumps(
        {"recording": {"started_at_utc": started_at}}))
    return work


@needs_ffmpeg
def test_v1_sidecar_route_refuses_unusable_trim(tmp_path):
    """r19 #1 (BLOCKER, M1 refuse side): with usable sidecars attached,
    a junk canonical.trim used to convert at head 0.0 with the stamp
    kept — created == started then made the raw verify falsely condemn
    the still-correct CSV and the planned retranslate re-bin every
    event one head-cut early: silent delivered desync. The conversion
    now REFUSES typed, BEFORE any write (attempt 2 sees the identical
    dir), for both junk-trim shapes."""
    import csv as _csv

    import pytest

    from pipeline import fix as fixmod
    for i, (trim, loc) in enumerate((("bogus", True),
                                     ({"head_cut_s": "abc"}, True),
                                     ("bogus", "mixed"))):
        work = _v1_with_sidecars(tmp_path, "2026-08-10T15:34:03Z",
                                 f"m1refuse{i}", "2026-08-10T15:33:55Z",
                                 trim, at_root=loc)
        with pytest.raises(fixmod.FixFailed) as e:
            fixmod.fix_v1_to_v2(work, "kamla")
        assert "canonical.trim" in str(e.value)
        with (work / "frames.csv").open(newline="") as f:
            header = next(_csv.reader(f))
        assert len(header) == 7, \
            "the refusal precedes every write — frames.csv is still v1"


@needs_ffmpeg
def test_v1_sidecar_route_recovers_unusable_stamp(tmp_path):
    """r19 #1 (M1 recovery arm): an unusable created_at_utc beside a
    USABLE trim and usable sidecars used to be omitted — recompute then
    synthesized a now-UTC stamp that poisoned the raw verify and made
    the retranslate refuse on both attempts (wrongful terminal reject
    of the exact class L1 set out to rescue). The stamp is now
    RECOVERED from ground truth: started_at_utc + head_cut."""
    from pipeline import fix as fixmod
    work = _v1_with_sidecars(tmp_path, "not-a-date", "m1recover",
                             "2026-08-10T15:33:55Z",
                             {"head_cut_s": 8.0}, at_root=False)
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note and "recovered" in note
    s = json.loads((work / "session.json").read_text())
    assert s["created_at_utc"] == "2026-08-10T15:34:03.000000Z", \
        "the delivered stamp is raw ground truth, not a synthesized now"


@needs_ffmpeg
def test_v1_sidecar_route_usable_stamp_control(tmp_path):
    """M1 proceed-side control (§2 rules 3/4): a parseable stamp beside
    a usable trim converts exactly as before — stamp + head_cut — with
    the sidecars attached and no refusal."""
    from pipeline import fix as fixmod
    work = _v1_with_sidecars(tmp_path, "2026-08-10T15:34:03Z",
                             "m1happy", "2026-08-10T15:33:58Z",
                             {"head_cut_s": 5.0}, at_root=True)
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note and "recovered" not in note
    s = json.loads((work / "session.json").read_text())
    assert s["created_at_utc"] == "2026-08-10T15:34:08.000000Z"


@needs_ffmpeg
def test_v1_overflow_head_cut_degrades_without_sidecars(tmp_path):
    """r19 #4≡#6 (M1): OverflowError escaped L1's (TypeError,
    ValueError) net — a JSON bigint, Infinity, '1e999' or a
    large-but-finite 1e18 head_cut_s crashed the float parse, the
    timedelta build or the datetime addition on both attempts. With no
    sidecars every overflow shape now degrades: the conversion
    completes and the parseable stamp is KEPT (head 0.0, r19 #10)."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    for i, junk in enumerate((10**400, float("inf"), "1e999", 1e18)):
        work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", f"m1ovf{i}")
        s = json.loads((work / "session.json").read_text())
        s["canonical"]["trim"] = {"head_cut_s": junk}
        (work / "session.json").write_text(json.dumps(s))
        note = fixmod.fix_v1_to_v2(work, "kamla")
        assert "converted v1 -> v2" in note, repr(junk)
        out = json.loads((work / "session.json").read_text())
        assert out["created_at_utc"] == "2026-08-10T15:34:03.000000Z", \
            repr(junk)


@needs_ffmpeg
def test_v1_junk_head_cut_value_keeps_valid_stamp(tmp_path):
    """r19 #10 (M1, no-sidecar arm): L1 parsed the stamp and the trim
    in ONE try block, so a junk head_cut_s VALUE ('5,0' — the same
    locale class as the L1 dx/dy fix) discarded the VALID parseable
    stamp and the delivered created_at_utc silently became processing
    wall-clock time. The head cut parse is now separate: the junk
    value degrades to 0.0 and the good stamp ships."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    for i, junk in enumerate(("5,0", "abc")):
        work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", f"m1jt{i}")
        s = json.loads((work / "session.json").read_text())
        s["canonical"]["trim"] = {"head_cut_s": junk}
        (work / "session.json").write_text(json.dumps(s))
        note = fixmod.fix_v1_to_v2(work, "kamla")
        assert "converted v1 -> v2" in note
        out = json.loads((work / "session.json").read_text())
        assert out["created_at_utc"] == "2026-08-10T15:34:03.000000Z", \
            "a junk trim value must not cost the valid stamp"
