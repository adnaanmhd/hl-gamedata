"""r-loop 18 fixes (L-set, R8_IMPLEMENTATION_PLAN §0) — pipeline side.

Each test cites the iteration-18 finding it pins (r18 #N, findings of
record in R18_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 324ac8b (session scratchpad); pin-only tests use
the mutation-proof pattern with the finders' EXACT mutants, per plan
§0/§1. These fixes land UNREVIEWED per the RULED sequence (iteration
18 was the last pass) — the checkpoint report labels them honestly.
"""
from __future__ import annotations

import csv
import json

from pipeline.tests.test_r_loop8 import needs_ffmpeg


# ------- r18 #1≡#2≡#3≡#5 (L1): fix_v1_to_v2 degrades junk v1 cells


def _rewrite_v1(work, edit):
    with (work / "frames.csv").open(newline="") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    col = {c: i for i, c in enumerate(header)}
    edit(col, body)
    with (work / "frames.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)


def _v1_rows(work):
    with (work / "frames.csv").open(newline="") as f:
        return list(csv.DictReader(f))


@needs_ffmpeg
def test_v1_conversion_degrades_junk_motion_cells(tmp_path):
    """r18 #1≡#2≡#3≡#5 (L1): fix_v1_to_v2 ran a bare float() over v1
    dx/dy cells, so ONE junk cell (locale '1,5', 'abc' — the
    STR_SENTINELS population) crashed the conversion with a
    session-kind ValueError on both attempts — a wrongful terminal
    reject that NO other plan step can rescue, because the checker
    early-returns on key_binding.json before the CSV is ever scanned
    (no STR_SENTINELS can precede this step). The junk cell now
    degrades to 0.0 exactly like fix_sentinels' _parse; the poisoned
    rows keep their keys and v1 actions; numeric cells format as
    before (the same-call control, §2 rule 3)."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", "l1junk")

    def edit(col, body):
        body[10][col["input_mouse_dx"]] = "1,5"
        body[11][col["input_mouse_dy"]] = "abc"
    _rewrite_v1(work, edit)
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note
    rows = _v1_rows(work)
    assert rows[10]["input_mouse_dx"] == "0.0", \
        "a junk cell is not motion — it degrades to 0.0"
    assert rows[11]["input_mouse_dy"] == "0.0"
    assert rows[10]["input_keys"] and rows[10]["input_actions"], \
        "the poisoned row keeps its keys and v1 actions"
    assert rows[0]["input_mouse_dx"] == "7.0", \
        "numeric control: real motion formats exactly as before"


@needs_ffmpeg
def test_v1_conversion_junk_only_motion_ships_blank(tmp_path):
    """L1, the fabricated-track half: has_motion judged the raw STRING
    ('not in (\"\", \"0\")'), so a column whose only non-zero-looking
    cells are junk counted as captured motion — pre-fix that crashed
    the float; a wrong fix that only guarded the float would ship a
    fabricated all-zero motion track. has_motion now judges PARSED
    values (fix_sentinels' own rule): a junk-only column ships the
    blank no-capture form."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", "l1junkonly")

    def edit(col, body):
        for r in body:
            r[col["input_mouse_dx"]] = "0.0"
            r[col["input_mouse_dy"]] = "0"
        body[10][col["input_mouse_dx"]] = "1,5"
        body[11][col["input_mouse_dy"]] = "None"
    _rewrite_v1(work, edit)
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note
    rows = _v1_rows(work)
    assert all(r["input_mouse_dx"] == "" and r["input_mouse_dy"] == ""
               for r in rows), \
        "junk-only motion is no capture — blank, never a fabricated track"


@needs_ffmpeg
def test_v1_conversion_unusable_created_at_is_synthesized(tmp_path):
    """L1 sibling read (named by three of four finders): an unparseable
    canonical created_at_utc raised ValueError out of the same
    conversion. It is now OMITTED and fix_sessionjson_recompute —
    already called by this fix — synthesizes a canonical stamp from
    ground truth (its designed r-loop 7/8 job). The I4 naive/aware
    pins in test_r_loop15 are the proceed-side controls."""
    from translator.v2 import _TS_RE
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    work = _v1_work(tmp_path, "not-a-date", "l1badstamp")
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note
    s = json.loads((work / "session.json").read_text())
    assert _TS_RE.match(s["created_at_utc"]), \
        "recompute must synthesize a checker-conformant stamp"


@needs_ffmpeg
def test_v1_conversion_nondict_trim_and_canonical_degrade(tmp_path):
    """L1 sibling reads: a non-dict canonical trim block crashed the
    head-cut arithmetic (AttributeError — not even the host tuple),
    and a non-dict canonical would crash every .get. Both now degrade;
    a valid stamp beside a junk trim converts with the stamp intact."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", "l1badtrim")
    s = json.loads((work / "session.json").read_text())
    s["canonical"]["trim"] = "bogus"
    (work / "session.json").write_text(json.dumps(s))
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note
    out = json.loads((work / "session.json").read_text())
    assert out["created_at_utc"] == "2026-08-10T15:34:03.000000Z", \
        "a junk trim must not cost the valid stamp (head cut 0.0)"


@needs_ffmpeg
def test_v1_conversion_corrupt_session_json_degrades(tmp_path):
    """L1 sibling read: a corrupt session.json REACHES this route —
    sniff types the payload v1 on key_binding.json alone, without
    parsing session.json — and the bare json.loads crashed both
    attempts. The read now goes through _read_session_json ({} on
    unreadable), the conversion completes on the CSV ground truth and
    recompute rebuilds session.json."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", "l1badsj")
    (work / "session.json").write_text('{"canonical": {"truncated')
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note
    s = json.loads((work / "session.json").read_text())
    assert s.get("game_title"), "recompute rebuilt session.json"


# ------- r18 #4 (L2): unusable raw/metadata.json falls back to
# ------- CSV-level fixes


def _raw_dir(tmp_path, metadata_text):
    work = tmp_path / "l2work"
    (work / "raw").mkdir(parents=True)
    (work / "raw" / "inputs.jsonl").write_text('{"t": 0}\n')
    if metadata_text is not None:
        (work / "raw" / "metadata.json").write_text(metadata_text)
    return work


def test_unusable_metadata_plans_csv_level_not_retranslate(tmp_path):
    """r18 #4 (L2): has_raw_sidecars tested only EXISTENCE while
    retranslate_from_sidecars parses raw/metadata.json unconditionally
    — so a present-but-corrupt sidecar planned a FIX_RETRANSLATE that
    crashed JSONDecodeError (session-kind, no refund) on both attempts,
    SUPERSEDING the CSV-level repairs that would have delivered the
    session: having the sidecars made it strictly worse off, the exact
    r-loop-7 shape left open for this class. Unusable now equals
    missing — the settled gray-zone rule ('raw-needing fixes fall back
    to CSV-level') — so plan_fixes keeps FIX_TSREPAIR_PTS."""
    from pipeline import fix as fixmod
    for bad in ('{"recording": {"started_at', '[1, 2, 3]'):
        work = _raw_dir(tmp_path / bad[:4].strip('[{"'), bad)
        assert fixmod.has_raw_sidecars(work) is False, \
            f"unusable metadata must read as no-sidecars: {bad!r}"
        plan = fixmod.plan_fixes(
            [{"code": "SYN_TS_NOT_PTS", "blocking": True, "fixable": True,
              "params": {}, "evidence": "e"}],
            game="kamla", has_raw=fixmod.has_raw_sidecars(work))
        ids = [fid for fid, _p in plan["steps"]]
        assert "FIX_TSREPAIR_PTS" in ids and "FIX_RETRANSLATE" not in ids


def test_usable_metadata_still_plans_retranslate_control(tmp_path):
    """L2 control (§2 rule 4, the proceed side): both sidecars present
    and USABLE keep today's retranslate supersede; a missing file
    keeps reading False (the r-loop-7 pin's own ground). r19 #2 (M2)
    tightened "usable" to require a parseable started_at_utc — the
    control models a genuinely usable sidecar accordingly."""
    from pipeline import fix as fixmod
    work = _raw_dir(
        tmp_path,
        '{"recording": {"started_at_utc": "2026-08-12T08:33:31Z"}}')
    assert fixmod.has_raw_sidecars(work) is True
    plan = fixmod.plan_fixes(
        [{"code": "SYN_TS_NOT_PTS", "blocking": True, "fixable": True,
          "params": {}, "evidence": "e"}],
        game="kamla", has_raw=fixmod.has_raw_sidecars(work))
    ids = [fid for fid, _p in plan["steps"]]
    assert "FIX_RETRANSLATE" in ids and "FIX_TSREPAIR_PTS" not in ids
    assert fixmod.has_raw_sidecars(_raw_dir(tmp_path / "m", None)) is False


def test_retranslate_metadata_read_is_typed_belt_and_braces(tmp_path):
    """L2 belt-and-braces: any residual path into
    retranslate_from_sidecars with an unreadable metadata.json (direct
    callers, a race after planning) raises a typed FixFailed NAMING the
    file instead of a bare JSONDecodeError the classifier reads as an
    anonymous session crash."""
    import pytest

    from pipeline import fix as fixmod
    work = _raw_dir(tmp_path, '{"recording": {"started_at')
    with pytest.raises(fixmod.FixFailed) as ei:
        fixmod.retranslate_from_sidecars(work, ledger_game="kamla")
    assert "metadata.json unreadable" in str(ei.value)


# ------- r18 #6 (L3, tests-only): K2's keybind-fallback anchor pinned
# ------- where it is live


@needs_ffmpeg
def test_v1_unusable_keybind_falls_back_on_the_ledger_slug(tmp_path):
    """r18 #6 (L3, tests-only): every fix_v1_to_v2 test used a USABLE
    keybind or NO keybind file, and all used kamla — so K2's
    game_name=slug anchor and resolve_keybind's parsed-but-unusable
    fallback were no-ops across the whole cohort: mutating the anchor
    to game_name=None passed the FULL arming gate at 793/789
    (finder-proven, byte-identical summary) while every outer_wilds
    v1 conversion with an unusable keybind (VK codes, nulls — the
    r-loop-7 population) shipped ALL movement presses deleted with
    their actions orphaned, checker-green. The I3 recipe applied at
    this site: OW ledger slug + DEGRADED canonical metadata (kills the
    metadata-anchored restatement too, the r14 H2 regression shape) +
    a parsed-but-unusable keybind at the work root. KEYBIND_PATCHES
    (outer_wilds) is live in this cohort for the first time.
    Mutation-proofed with the finder's EXACT game_name=None mutant in
    a fixed-tree scratch copy (session scratchpad): it fails this
    pin."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", "l3owkb")
    s = json.loads((work / "session.json").read_text())
    s["canonical"]["game"] = 12345          # degraded, like r15 #6/I3
    (work / "session.json").write_text(json.dumps(s))
    (work / "keybind.json").write_text(json.dumps({"look_up": 12345}))
    note = fixmod.fix_v1_to_v2(work, "outer_wilds")
    assert "converted v1 -> v2" in note
    rows = _v1_rows(work)
    for key, action in (("W", "move_up"), ("A", "move_left"),
                        ("S", "move_down")):
        hit = [r for r in rows if action in r["input_actions"]]
        assert hit, f"the {action} rows must exist"
        assert all(key in r["input_keys"].split("|") for r in hit), \
            f"an unusable keybind must fall back to the LEDGER slug's " \
            f"built-in — {key} presses were deleted (orphan actions)"
