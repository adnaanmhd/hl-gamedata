"""r-loop 15 fixes (I1/I5, R8_IMPLEMENTATION_PLAN §3) — translator side.

Each test cites the iteration-15 finding it pins (r15 #N, findings of
record in R15_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at ce26148 (session scratchpad), per plan §1/§3.
"""
from __future__ import annotations

import json
from pathlib import Path

# v2 accessed by attribute so the fail-first scratch run fails PER TEST
from translator import v2
from translator.tests.test_r_loop9_translator import (_GOOD_REC, _bundle,
                                                      needs_ffmpeg)


# ------- r15 #4 (I1): qa-v2 exempts caseless key tokens


def _symbol_bundle(tmp_path):
    """A real Kamla bundle whose keybind.json binds ';' (a lefty/non-QWERTY
    interact) plus 'w' — the exact population r15 #4 proved is terminally
    rejected by the checker's own writer's output."""
    d = _bundle(tmp_path, {"session_id": "symbolbind",
                           "game": {"name": "Kamla"},
                           "recording": dict(_GOOD_REC)})
    (d / "keybind.json").write_text(json.dumps(
        {"interact": ";", "move_up": "w"}))
    evs = []
    for k, t0, t1 in ((";", 0.20, 0.50), ("w", 0.10, 0.40)):
        evs.append({"t": int(t0 * 1e6), "type": "key", "key": k,
                    "action": "down"})
        evs.append({"t": int(t1 * 1e6), "type": "key", "key": k,
                    "action": "up"})
    (d / "inputs.jsonl").write_text(
        "\n".join(json.dumps(e) for e in sorted(evs, key=lambda e: e["t"])))
    return d


def _delivered_rows(out_dir: Path):
    import csv
    with (Path(out_dir) / "frames.csv").open(newline="") as f:
        return list(csv.DictReader(f))


@needs_ffmpeg
def test_symbol_key_bind_passes_the_v2_token_grammar(tmp_path):
    """r15 #4 (I1, RULED 2026-08-19): the checker flagged every caseless
    symbol key its own writer emits (key_display(';') == ';'), mapping to
    INP_TOKEN_CASE -> FIX_KEY_HYGIENE, which re-tokenizes through the SAME
    key_display — a provably no-op fix loop, terminal reject, every
    session from that player unpaid. Symbol keys are real gameplay data
    and stay in the delivery."""
    d = _symbol_bundle(tmp_path)
    rep = v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    out_dir = Path(rep["out_dir"])
    (out_dir / "session.rrd").touch()      # ingest's stub; checker needs it
    r = v2.check_session_v2(out_dir)
    assert not any("non-v2 key tokens" in i for i in r.issues), r.issues
    rows = _delivered_rows(out_dir)
    semi = [x for x in rows if ";" in (x["input_keys"] or "").split("|")]
    assert semi, "the ';' presses must ride the delivered rows"
    assert all("interact" in (x["input_actions"] or "").split("|")
               for x in semi), \
        "the symbol key's action must ride with it"


# ------- r15 #5 (I2): the writer strips action-less combo halves


def _combo_bundle(tmp_path):
    """A Kamla bundle with a {modifier, key} combo bind (interact =
    Ctrl+E), a single bind (move_up = w), a bare-'e' press, a
    modifier-lead, and a full Ctrl+E chord — every arm of the r15 #5
    discriminator in one clip. No mouse_raw events: the retranslate
    route's always-on lag corrector spuriously 'corrects' synthetic
    clips (testsrc2's global motion correlates with anything) and
    shifts every event off the video."""
    d = _bundle(tmp_path, {"session_id": "combobind",
                           "game": {"name": "Kamla"},
                           "recording": dict(_GOOD_REC)})
    (d / "keybind.json").write_text(json.dumps(
        {"interact": {"modifier": "ctrl", "key": "e"}, "move_up": "w"}))
    evs = [
        {"t": int(0.10e6), "type": "key", "key": "w", "action": "down"},
        {"t": int(0.90e6), "type": "key", "key": "w", "action": "up"},
        # bare combo half: must strip (r15 #5)
        {"t": int(0.20e6), "type": "key", "key": "e", "action": "down"},
        {"t": int(0.35e6), "type": "key", "key": "e", "action": "up"},
        # modifier lead, then the full chord
        {"t": int(0.50e6), "type": "key", "key": "ctrl", "action": "down"},
        {"t": int(0.55e6), "type": "key", "key": "e", "action": "down"},
        {"t": int(0.70e6), "type": "key", "key": "e", "action": "up"},
        {"t": int(0.75e6), "type": "key", "key": "ctrl", "action": "up"},
    ]
    (d / "inputs.jsonl").write_text(
        "\n".join(json.dumps(e) for e in sorted(evs, key=lambda e: e["t"])))
    return d


def _assert_combo_invariant(rows):
    """The r15 #5 delivery invariant + both §2-rule-3 splits on one CSV."""
    for x in rows:
        assert not (x["input_keys"] and not x["input_actions"]), \
            f"keys with null actions shipped: {x['input_keys']!r}"
    e_rows = [x for x in rows
              if "E" in (x["input_keys"] or "").split("|")]
    assert e_rows, "the full chord's frames must keep the combo key"
    for x in e_rows:
        assert "Ctrl" in x["input_keys"].split("|"), \
            "a kept E must ride with its modifier (bare halves strip)"
        assert "interact" in (x["input_actions"] or "").split("|")
    w_rows = [x for x in rows
              if "W" in (x["input_keys"] or "").split("|")]
    assert w_rows and all(
        "move_up" in (x["input_actions"] or "").split("|")
        for x in w_rows), \
        "plain single binds must be unaffected (motion never strips keys)"


@needs_ffmpeg
def test_combo_half_pressed_alone_is_stripped_at_the_writer(tmp_path):
    """r15 #5 (I2, RULED 2026-08-19): bound_literals includes every alt
    of a {modifier, key} combo group, so both halves are 'bound' and
    _v2_rows kept them — but resolve_actions fires only when ALL groups
    are held, so a bare half shipped keys with null actions and the
    checker FAILed the keys-have-actions invariant through BOTH fix
    routes (terminal reject, player unpaid). A combo half pressed alone
    is now stripped-and-counted exactly like an unbound key."""
    d = _combo_bundle(tmp_path)
    rep = v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    out_dir = Path(rep["out_dir"])
    (out_dir / "session.rrd").touch()
    _assert_combo_invariant(_delivered_rows(out_dir))
    assert rep["stripped_keys"].get("e", 0) >= 1, rep["stripped_keys"]
    assert rep["stripped_keys"].get("ctrl", 0) >= 1, \
        "the modifier-lead frames strip the lone modifier too"
    r = v2.check_session_v2(out_dir)
    assert not any("null input_actions" in i for i in r.issues), r.issues


# ------- r15 #8 (I5): safe_session_id bounds the id's byte length


@needs_ffmpeg
def test_overlength_session_id_falls_back_to_folder_name_e2e(tmp_path):
    """r15 #8 (I5, H7's remaining sibling): a >NAME_MAX session_id
    passed safe_session_id and crashed every join's mkdir with OSError
    errno 63 — which apply_fixes' classifier calls HOST, so the row
    parked FIX_QUEUED and retried forever (never terminal, never the
    designed fallback) with an hourly alert blaming the host; the same
    crash killed both G7 operator tools mid-batch. Garbage ids are
    DESIGNED to degrade to the bundle-folder-name fallback; over-length
    ones now do."""
    from translator.tests.test_r_loop9_translator import _bundle as _b
    d = _b(tmp_path, {"session_id": "x" * 300,
                      "game": {"name": "Kamla"},
                      "recording": dict(_GOOD_REC)})
    out_root = tmp_path / "out"
    rep = v2.translate_bundle_v2(d, out_root, make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    out_dir = Path(rep["out_dir"]).resolve()
    assert out_dir.is_relative_to(out_root.resolve()), out_dir
    assert rep["session"] == "bundle", \
        "over-length ids fall back to the folder name, like NUL ones"


def test_safe_session_id_length_bound_boundary(tmp_path):
    """I5 unit pin at the shared decision point (all five join sites):
    the boundary both ways (200 bytes kept, 201 falls back), BYTE
    semantics (150 two-byte chars = 300 bytes falls back although
    len() is 150), and the clean-id control."""
    from translator.translate import safe_session_id
    assert safe_session_id("x" * 200, tmp_path / "b") == "x" * 200
    assert safe_session_id("x" * 201, tmp_path / "b") == "b"
    assert safe_session_id("é" * 150, tmp_path / "b") == "b"
    assert safe_session_id("ok-id_1.2", tmp_path / "b") == "ok-id_1.2"


@needs_ffmpeg
def test_lowercase_letter_and_snake_tokens_still_fail_the_grammar(tmp_path):
    """I1 control (§2 rule 3, the other side of the exemption): tokens
    that HAVE case still flag — a genuinely lowercase letter and a
    multi-char snake_case token (whose upper differs) both FAIL."""
    d = _symbol_bundle(tmp_path)
    rep = v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    out_dir = Path(rep["out_dir"])
    (out_dir / "session.rrd").touch()
    import csv
    with (out_dir / "frames.csv").open(newline="") as f:
        rdr = csv.reader(f)
        header = next(rdr)
        body = list(rdr)
    ki = header.index("input_keys")
    ai = header.index("input_actions")
    body[0][ki], body[0][ai] = "e", "interact"
    body[1][ki], body[1][ai] = "left_shift", "interact"
    with (out_dir / "frames.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)
    r = v2.check_session_v2(out_dir)
    bad = [i for i in r.issues if "non-v2 key tokens" in i]
    assert bad, "cased tokens must still FAIL the grammar"
    assert "'e'" in bad[0] and "'left_shift'" in bad[0], bad
