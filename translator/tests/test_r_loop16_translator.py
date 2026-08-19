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


# ------- r16 #5 (J6, RULED): the comma key ships as the named token
# ------- 'Comma'


def test_comma_key_display_round_trip():
    """r16 #5 (J6, RULED 2026-08-19 option A) unit: the comma key is
    NAMED like Space/Enter — key_display(',') == 'Comma', the inverse
    round-trips, and a keybind literal written as 'Comma' binds the
    raw ',' events via the alias."""
    from translator.keys import normalize_literal
    from translator.v2 import key_canonical, key_display
    assert key_display(",") == "Comma"
    assert key_canonical("Comma") == ","
    assert normalize_literal("Comma") == ","
    assert normalize_literal(",") == ","


@needs_ffmpeg
def test_comma_key_bind_ships_the_named_token(tmp_path):
    """r16 #5 (J6): a comma-bind player's presses terminal-rejected
    exactly like the r15 #4 ';' case one arm over — the checker's
    comma arm flags the bare ',' token the writer itself emits, and
    hygiene no-ops (proven end-to-end by the finder). The delivery now
    carries 'Comma' (cased, no comma character in the cell): the
    grammar passes and the action rides."""
    d = _bundle(tmp_path, {"session_id": "commabind",
                           "game": {"name": "Kamla"},
                           "recording": dict(_GOOD_REC)})
    (d / "keybind.json").write_text(json.dumps(
        {"interact": ",", "move_up": "w"}))
    evs = []
    for k, t0, t1 in ((",", 0.20, 0.50), ("w", 0.10, 0.40)):
        evs.append({"t": int(t0 * 1e6), "type": "key", "key": k,
                    "action": "down"})
        evs.append({"t": int(t1 * 1e6), "type": "key", "key": k,
                    "action": "up"})
    (d / "inputs.jsonl").write_text(
        "\n".join(json.dumps(e) for e in sorted(evs, key=lambda e: e["t"])))
    rep = v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    out_dir = Path(rep["out_dir"])
    (out_dir / "session.rrd").touch()
    r = v2.check_session_v2(out_dir)
    assert not any("non-v2 key tokens" in i for i in r.issues), r.issues
    rows = _delivered_rows(out_dir)
    hits = [x for x in rows
            if "Comma" in (x["input_keys"] or "").split("|")]
    assert hits, "the comma presses must ride as the named token"
    assert all("interact" in (x["input_actions"] or "").split("|")
               for x in hits)
    assert not any("," in (x["input_keys"] or "") for x in rows), \
        "no raw comma character inside a delivered input_keys cell"


@needs_ffmpeg
def test_glued_token_still_fails_the_comma_arm(tmp_path):
    """J6 control (§2 rule 3): the comma arm's real target — a
    glued/malformed multi-char token containing a comma — still FAILs
    the grammar; only the NAMED comma key was rescued."""
    import csv
    d = _bundle(tmp_path, {"session_id": "gluedtok",
                           "game": {"name": "Kamla"},
                           "recording": dict(_GOOD_REC)})
    rep = v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    out_dir = Path(rep["out_dir"])
    (out_dir / "session.rrd").touch()
    with (out_dir / "frames.csv").open(newline="") as f:
        rdr = csv.reader(f)
        header = next(rdr)
        body = list(rdr)
    ki, ai = header.index("input_keys"), header.index("input_actions")
    body[0][ki], body[0][ai] = "W,A", "move_up"
    with (out_dir / "frames.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)
    r = v2.check_session_v2(out_dir)
    assert any("non-v2 key tokens" in i and "W,A" in i
               for i in r.issues), r.issues


def _delivered_rows(out_dir):
    import csv
    with (Path(out_dir) / "frames.csv").open(newline="") as f:
        return list(csv.DictReader(f))


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
