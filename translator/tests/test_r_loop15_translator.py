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
