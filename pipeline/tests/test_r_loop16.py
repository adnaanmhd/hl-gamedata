"""r-loop 16 fixes (J-set, R8_IMPLEMENTATION_PLAN §0) — pipeline/tools
side.

Each test cites the iteration-16 finding it pins (r16 #N, findings of
record in R16_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 4dc37b4 (session scratchpad); pin-only tests use
the mutation-proof pattern with the finders' EXACT mutants, per plan
§1/§4.
"""
from __future__ import annotations

import json
import os
import time

from pipeline.tests.test_r_loop8 import needs_ffmpeg


# ------- r16 #3 (J2): the INP_OSKEYS trigger judges the session binding


def _os_map(os_keys, bound):
    from pipeline import validate
    return validate.map_reasons(
        {"duration_s": 100.0, "vlm": {"samples": [1]},
         "inventory": {"os_keys": os_keys, "distinct_actions": 5,
                       "key_frames": 10, "motion_frames": 5,
                       "btn_frames": 2, "rows": 100}},
        {"probed_duration_s": 100.0, "has_raw": True,
         "bound_literals": bound, "notifs": [], "chats": []})


def test_oskeys_trigger_skips_bound_keys():
    """r16 #3 (J2): the trigger fired on the engine's pattern-only
    os_keys count, but the planned FIX_KEY_HYGIENE deliberately KEEPS
    bound OS/F-keys (the locked strip-unless-bound rule) — a provable
    no-op loop: two burned attempts, wrongful terminal reject of a
    spec-conformant delivery, three paid sweeps. A bound OS-pattern
    key now surfaces as an advisory, never a blocking reason."""
    res = _os_map({"Insert": 8}, frozenset({"insert"}))
    assert not any(r["code"] == "INP_OSKEYS" for r in res.reasons), \
        [r["code"] for r in res.reasons]
    assert any("BOUND" in a and "Insert" in a for a in res.advisories), \
        res.advisories


def test_oskeys_trigger_unbound_control_still_fires():
    """J2 control (§2 rule 4, the proceed side): unbound OS-key
    pollution keeps today's blocking reason — hygiene really does
    clear it."""
    res = _os_map({"CapsLock": 3}, frozenset({"insert"}))
    hits = [r for r in res.reasons if r["code"] == "INP_OSKEYS"]
    assert len(hits) == 1 and hits[0]["params"]["keys"] == {"CapsLock": 3}


def test_oskeys_trigger_mixed_filters_per_token():
    """J2 mixed case (kills the over-filter mutant): one bound + one
    unbound OS-pattern key in the same session — the reason lists ONLY
    the unbound token, the advisory only the bound one."""
    res = _os_map({"Insert": 8, "CapsLock": 3}, frozenset({"insert"}))
    hits = [r for r in res.reasons if r["code"] == "INP_OSKEYS"]
    assert len(hits) == 1 and hits[0]["params"]["keys"] == {"CapsLock": 3}
    assert any("Insert" in a and "CapsLock" not in a
               for a in res.advisories), res.advisories


def test_oskeys_trigger_without_aux_keeps_todays_behavior():
    """J2 back-compat guard: a caller that never resolved the binding
    (legacy aux, tests) keeps the pre-fix all-flagged behavior — the
    conservative direction."""
    from pipeline import validate
    res = validate.map_reasons(
        {"duration_s": 100.0, "vlm": {"samples": [1]},
         "inventory": {"os_keys": {"Insert": 8}, "distinct_actions": 5,
                       "key_frames": 10, "motion_frames": 5,
                       "btn_frames": 2, "rows": 100}},
        {"probed_duration_s": 100.0, "has_raw": True,
         "notifs": [], "chats": []})
    assert any(r["code"] == "INP_OSKEYS" for r in res.reasons)


def test_session_bound_literals_resolves_like_hygiene(tmp_path):
    """J2 unit: the helper resolves exactly as fix_key_hygiene does —
    the session's own raw/keybind.json when present, the ledger game's
    built-in otherwise."""
    from pipeline.validate import _session_bound_literals
    work = tmp_path / "w"
    (work / "raw").mkdir(parents=True)
    (work / "raw" / "keybind.json").write_text(
        json.dumps({"interact": "insert", "move_up": "w"}))
    bound = _session_bound_literals(work, "kamla")
    assert "insert" in bound and "w" in bound
    work2 = tmp_path / "w2"
    work2.mkdir()
    builtin = _session_bound_literals(work2, "kamla")
    assert "insert" not in builtin and "e" in builtin


@needs_ffmpeg
def test_oskeys_trigger_bound_aware_end_to_end(tmp_path):
    """J2 wiring pinned END TO END (the r15 #6 lesson: pin where the
    behavior is live — deleting the validate_session aux wiring must
    not leave the suite green): a real session whose CSV carries
    Insert presses and whose raw/keybind.json binds insert passes
    validation without INP_OSKEYS; the identical session WITHOUT the
    keybind takes today's blocking reason."""
    import csv

    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.validate import validate_session

    def _inject(d):
        with (d / "frames.csv").open(newline="") as f:
            rows = list(csv.reader(f))
        header, body = rows[0], rows[1:]
        ki = header.index("input_keys")
        ai = header.index("input_actions")
        for i in range(10, 18):
            body[i][ki] = "Insert"
            body[i][ai] = "interact"
        with (d / "frames.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(body)

    d = _make_session(tmp_path, seconds=80, name="oskbound")
    _inject(d)
    (d / "raw").mkdir()
    (d / "raw" / "keybind.json").write_text(
        json.dumps({"interact": "insert", "move_up": "w",
                    "move_left": "a", "move_down": "s"}))
    res = validate_session(d, tmp_path / "dossier", skip_vlm=True,
                           expected_game="kamla")
    assert not any(r["code"] == "INP_OSKEYS" for r in res.reasons), \
        [r["code"] for r in res.reasons]
    assert any("BOUND" in a for a in res.advisories), res.advisories

    d2 = _make_session(tmp_path, seconds=80, name="oskunbound")
    _inject(d2)
    res2 = validate_session(d2, tmp_path / "dossier2", skip_vlm=True,
                            expected_game="kamla")
    assert any(r["code"] == "INP_OSKEYS" for r in res2.reasons), \
        [r["code"] for r in res2.reasons]


# ------- r16 #2 (J5, RULED): the daily resume scan fails CLOSED


def test_daily_scan_listing_failure_refuses_the_tick(
        cfg, ledger, monkeypatch, capsys):
    """r16 #2 (J5, RULED Adnaan 2026-08-19: fail CLOSED): the
    day-agnostic resume scan's inline try/except read a transient
    listing OSError as 'no pending days' — the fresh path then ran
    past an older pending day, re-counted its unstamped roots as late
    arrivals, and the pending day's later resume re-sent the SAME
    hours on its own sheet: two sent payment documents, silent
    double-pay (finder-proven by execution). The harm needs the
    ASYMMETRIC transient — the regen guard's listing succeeds, the
    scan's fails (the EMFILE class) — reproduced here at the shared
    helper seam: call 1 real, call 2 None. The tick must refuse
    loudly, send nothing, and stamp nothing."""
    from pipeline import reports
    from pipeline import run as runmod
    from pipeline.tests.test_r_loop8 import _daily_seed
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    real = reports._report_day_dirs
    calls = {"n": 0}

    def flaky(c):
        calls["n"] += 1
        return real(c) if calls["n"] == 1 else None
    monkeypatch.setattr(reports, "_report_day_dirs", flaky)
    capsys.readouterr()
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is False, \
        "an unlistable reports dir must refuse the tick, never guess"
    assert calls["n"] == 2, \
        "the scan must consult the shared fail-closed helper"
    assert "unlistable" in capsys.readouterr().err
    assert docs == [] and not csv_path.exists(), \
        "nothing may be built or sent on the refused tick"
    row = ledger.get(sid)
    assert not row["uploaded_reported_at"] and \
        not row["accepted_reported_at"], "…and nothing stamped"
    # transient doctrine: the next healthy tick proceeds normally
    monkeypatch.setattr(reports, "_report_day_dirs", real)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    assert csv_path.exists()


def test_daily_first_ever_send_with_no_reports_dir_proceeds(
        cfg, ledger, monkeypatch):
    """J5 control (§2 rule 4, the proceed side): a genuinely MISSING
    reports dir (first-ever send) is nothing-pending, not a refusal —
    the shared helper returns [] for it and the fresh path runs."""
    import shutil

    from pipeline import run as runmod
    from pipeline.tests.test_r_loop8 import _daily_seed
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch)
    shutil.rmtree(cfg.reports_dir)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    assert csv_path.exists()


# ------- r16 #6 (J3, tests-only): the fix_sync remap credited-strip pin


def test_fix_sync_remap_strips_uncredited_combo_half():
    """r16 #6 (J3, tests-only): I2's fourth writer site — the
    fix_sync_from_v1 remap's kset &= credited — was exercised by no
    test: reverting it to the exact pre-I2 shape passed the FULL
    arming gate at 765/761 (finder-proven), while a combo-bind v1
    delivery would ship keys with null actions. Mutation-proofed with
    that EXACT revert in a fixed-tree scratch copy (session
    scratchpad): it fails this pin. The chord and single-bind controls
    ride the same call (§2 rule 3)."""
    from pipeline.tests.test_payment_split_r6 import _load
    from translator.keybind import build_resolver
    tool = _load("fix_sync_from_v1")
    rules = build_resolver({"interact": {"modifier": "ctrl", "key": "e"},
                            "move_up": "w"})
    pts2 = [0, 33333, 66667]
    rows, uncovered = tool.remap(
        [["e"], ["ctrl", "e"], ["w"]], [[], [], []],
        [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], pts2, 0, rules)
    assert uncovered == 0
    assert rows[0][0] == [] and rows[0][1] == [], \
        f"the bare combo half must be stripped: {rows[0]}"
    assert rows[1][0] == ["ctrl", "e"] and "interact" in rows[1][1]
    assert rows[2][0] == ["w"] and rows[2][1] == ["move_up"]
    for keys, actions, _btns, _dx, _dy in rows:
        assert not (keys and not actions), rows


# ------- r16 #7 (J4, tests-only): the retrim naive-created_at guard pin


def _retrim_session(tmp_path, created_at, name):
    from pipeline.tests.test_fix_cut_gate import _make_session
    d = _make_session(tmp_path, seconds=80, name=name)
    s = json.loads((d / "session.json").read_text())
    s["created_at_utc"] = created_at
    (d / "session.json").write_text(json.dumps(s))
    return d


@needs_ffmpeg
def test_retrim_tool_guards_a_naive_created_at(tmp_path):
    """r16 #7 (J4, tests-only): I4's sweep half — the naive-stamp
    guard in tools/retrim_v2_session.py — was exercised by no test
    (every retrim test builds AWARE stamps): deleting the two guard
    lines passed the FULL arming gate at 765/761 (finder-proven) while
    a direct operator retrim of a naive-stamped legacy delivery on the
    IST Mac wrote created_at_utc 5h30m early. Mutation-proofed with
    that EXACT deletion in a fixed-tree scratch copy (session
    scratchpad): it fails this pin. TZ forced in-test so the pin fails
    on every host."""
    from pipeline.tests.test_payment_split_r6 import _load
    tool = _load("retrim_v2_session")
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()
    try:
        d = _retrim_session(tmp_path, "2026-08-10T15:34:03", "rtznaive")
        tool.retrim(d, 3.0, d, make_rrd=False)
        s = json.loads((d / "session.json").read_text())
        assert s["created_at_utc"].startswith("2026-08-10T15:34:"), \
            f"naive-UTC wall clock must survive (+snapped head cut " \
            f"only): {s['created_at_utc']}"
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


@needs_ffmpeg
def test_retrim_tool_aware_created_at_control(tmp_path):
    """J4 control: an aware stamp retrims exactly as before."""
    from pipeline.tests.test_payment_split_r6 import _load
    tool = _load("retrim_v2_session")
    d = _retrim_session(tmp_path, "2026-08-10T15:34:03Z", "rtzaware")
    tool.retrim(d, 3.0, d, make_rrd=False)
    s = json.loads((d / "session.json").read_text())
    assert s["created_at_utc"].startswith("2026-08-10T15:34:")
