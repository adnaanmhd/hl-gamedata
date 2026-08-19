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


# ------- r15 #5 (I2): both fix routes restore the keys-have-actions
# ------- invariant on a combo-bind session


def _combo_work(tmp_path):
    """Translate the r15 #5 combo bundle and shape it as a v2 working
    copy with raw/ sidecars (the retranslate/hygiene input form)."""
    from translator import v2
    from translator.tests.test_r_loop15_translator import _combo_bundle
    d = _combo_bundle(tmp_path)
    rep = v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    work = tmp_path / "work"
    shutil.copytree(Path(rep["out_dir"]), work)
    (work / "session.rrd").touch()
    raw = work / "raw"
    raw.mkdir(exist_ok=True)
    for name in ("inputs.jsonl", "metadata.json", "keybind.json"):
        shutil.copy2(d / name, raw / name)
    return work


def _csv_dict_rows(work):
    import csv
    with (Path(work) / "frames.csv").open(newline="") as f:
        return list(csv.DictReader(f))


@needs_ffmpeg
def test_retranslate_route_restores_the_combo_invariant(tmp_path):
    """r15 #5 (I2) route (a): FIX_RETRANSLATE re-bins from sidecars via
    _v2_rows, which pre-fix reproduced the bare-half rows identically —
    the FAIL re-fired on both attempts and the session was terminally
    rejected. The re-bin now strips uncredited combo halves."""
    from translator import v2
    from translator.tests.test_r_loop15_translator import \
        _assert_combo_invariant
    from pipeline.tests.test_r_loop14 import _retranslate
    work = _combo_work(tmp_path)
    out = _retranslate(work, tmp_path, "kamla", "d-i2a")
    assert not out["error"], out
    _assert_combo_invariant(_csv_dict_rows(work))
    r = v2.check_session_v2(work)
    assert not any("null input_actions" in i for i in r.issues), r.issues


@needs_ffmpeg
def test_hygiene_route_strips_the_actionless_combo_half(tmp_path):
    """r15 #5 (I2) route (b): FIX_KEY_HYGIENE stripped only UNBOUND
    tokens (the r10 #9 rule), and a combo half is bound — 'stripped 0',
    identical FAIL, terminal reject. The mirror now strips uncredited
    tokens; an injected pre-fix-shaped bare-E row comes out clean while
    the genuine chord rows keep E+Ctrl+interact (the §2-rule-4 proceed
    side on the same run)."""
    import csv
    from translator import v2
    from pipeline import fix as fixmod
    from translator.tests.test_r_loop15_translator import \
        _assert_combo_invariant
    work = _combo_work(tmp_path)
    with (work / "frames.csv").open(newline="") as f:
        rdr = csv.reader(f)
        header = next(rdr)
        body = list(rdr)
    ki, ai = header.index("input_keys"), header.index("input_actions")
    assert body[0][ki] == "", "row 0 predates the first press"
    body[0][ki], body[0][ai] = "E", ""       # the pre-I2 delivered shape
    with (work / "frames.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)
    fixmod.fix_key_hygiene(work, "kamla")
    rows = _csv_dict_rows(work)
    assert rows[0]["input_keys"] == "" and rows[0]["input_actions"] == "", \
        "the bare combo half must be stripped, not shipped action-less"
    _assert_combo_invariant(rows)
    r = v2.check_session_v2(work)
    assert not any("null input_actions" in i for i in r.issues), r.issues


# ------- r15 #7 (I4): fix_v1_to_v2 guards a naive created_at_utc


def _v1_work(tmp_path, created_at, name):
    """A v1-shaped working copy (7-col frames.csv + canonical block)
    over a real video — the ARR_V1_FORMAT input class."""
    import csv as _csv

    from pipeline.tests.test_fix_cut_gate import _make_session
    d = _make_session(tmp_path, seconds=80, name=name)
    with (d / "frames.csv").open(newline="") as f:
        rows = list(_csv.reader(f))
    header, body = rows[0], rows[1:]
    col = {c: i for i, c in enumerate(header)}
    v1_header = ["frame_id", "timestamp_ms", "input_keys",
                 "input_actions", "input_mouse_buttons",
                 "input_mouse_dx", "input_mouse_dy"]
    with (d / "frames.csv").open("w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(v1_header)
        w.writerows([[r[col[c]] for c in v1_header] for r in body])
    (d / "session.json").write_text(json.dumps(
        {"canonical": {"session_id": name, "game": "Kamla",
                       "created_at_utc": created_at}}))
    return d


@needs_ffmpeg
def test_v1_naive_created_at_is_not_shifted_by_the_host_offset(tmp_path):
    """r15 #7 (I4): fix_v1_to_v2 parsed the v1 canonical created_at_utc
    and wrote created.astimezone(utc) — for a NAIVE stamp (no tz
    suffix, a real HumynCapture provenance class) astimezone interprets
    HOST-LOCAL time and shifted the written stamp by the host's UTC
    offset (−5h30m on an IST host): silent delivered-metadata
    corruption on the no-sidecar branch, a manufactured wrongful
    terminal reject on the sidecar branch. Every sibling site already
    guarded this; fix_v1_to_v2 was the sole omission — and the qa
    checker that would flag naive stamps never runs before
    ARR_V1_FORMAT routes here. The TZ is forced IN-TEST so the pin
    fails pre-fix on every host, not just an IST one."""
    import os
    import time

    from pipeline import fix as fixmod
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()
    try:
        work = _v1_work(tmp_path, "2026-08-10T15:34:03", "v1naive")
        note = fixmod.fix_v1_to_v2(work, "kamla")
        assert "converted v1 -> v2" in note
        s = json.loads((work / "session.json").read_text())
        assert s["created_at_utc"] == "2026-08-10T15:34:03.000000Z", \
            f"naive-UTC wall clock must survive byte-identical: " \
            f"{s['created_at_utc']}"
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


@needs_ffmpeg
def test_v1_aware_created_at_control_unchanged(tmp_path):
    """I4 control (§2 rule 4, the proceed side): an aware stamp
    converts exactly as before — the guard touches only naive input."""
    from pipeline import fix as fixmod
    work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", "v1aware")
    fixmod.fix_v1_to_v2(work, "kamla")
    s = json.loads((work / "session.json").read_text())
    assert s["created_at_utc"] == "2026-08-10T15:34:03.000000Z"
