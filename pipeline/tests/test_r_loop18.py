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
