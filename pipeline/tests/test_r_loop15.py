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


# ------- r15 #1≡#2≡#3≡#10 (I7): H5 gone-is-gone RULING + rename
# ------- coaching


def test_vanished_same_path_restore_stays_quarantined_by_ruling(
        cfg, ledger):
    """r15 #1≡#2≡#3≡#10 (I7, RULED Adnaan 2026-08-19: 'if the folder
    is gone, it's gone'): a SAME-path reappearance (Drive trash
    restore, identical re-upload — same path, same md5, same ctime) is
    DELIBERATELY terminal: no heal, no listing counters, no event
    churn. This pins the RULING against a future well-meaning
    same-path heal; the different-path heal control lives in
    test_r_loop14 (test_vanished_discovered_reappearing_folder_heals)
    and stays green untouched. The correction path is a re-upload
    under a NEW folder name, which mints a new session id — both
    dedupe sites exclude QUARANTINED rows, so the dead row cannot
    block or dup-reject the renamed copy."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    from pipeline.tests.test_r_loop14 import _H5_SID, _h5_discovered
    _h5_discovered(cfg, ledger)
    other = make_session_entries(
        sid="2026-08-14T11-00-00Z_kamla_c_00000000000000b6", md5="h6-md5")
    ingest.scan(cfg, ledger, entries=other)
    assert ledger.get(_H5_SID)["state"] == "QUARANTINED"
    n_events = ledger.db.execute(
        "SELECT COUNT(*) c FROM events WHERE session_id=?",
        (_H5_SID,)).fetchone()["c"]
    # the folder comes back at the IDENTICAL path — twice
    for _ in range(2):
        res = ingest.scan(cfg, ledger,
                          entries=make_session_entries(sid=_H5_SID,
                                                       md5="h5-md5"))
        assert ledger.get(_H5_SID)["state"] == "QUARANTINED", \
            "same-path restore is terminal BY RULING"
        assert res.discovered == [] and res.superseded == [] \
            and res.integrity_flags == [], res
    after = ledger.db.execute(
        "SELECT COUNT(*) c FROM events WHERE session_id=?",
        (_H5_SID,)).fetchone()["c"]
    assert after == n_events, "silently: no event churn on restores"


def test_vanished_detail_and_loud_line_carry_the_rename_coaching(
        cfg, ledger, capsys):
    """I7 coaching string (fail-first at ce26148: absent there): since
    the same-path restore is terminal by ruling, the ONE ops surface —
    the quarantine event detail and the [vanished-discovered] stderr
    line — must tell the operator the correction that actually works:
    re-upload under a NEW folder name."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    from pipeline.tests.test_r_loop14 import _H5_SID, _h5_discovered
    _h5_discovered(cfg, ledger)
    capsys.readouterr()
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid="2026-08-14T11-00-00Z_kamla_c_00000000000000b6", md5="h6-md5"))
    last = ledger.db.execute(
        "SELECT detail FROM events WHERE session_id=? ORDER BY id",
        (_H5_SID,)).fetchall()[-1]
    assert "folder gone from Drive I" in last["detail"]
    assert "re-upload under a NEW folder name" in last["detail"], \
        last["detail"]
    assert len(last["detail"]) <= 300, "under the event detail cap"
    err = capsys.readouterr().err
    assert "[vanished-discovered]" in err
    assert "re-upload under a NEW folder name" in err


def test_coached_rename_reupload_enters_despite_dead_quarantined_row(
        cfg, ledger):
    """r17 #6 (K6, tests-only): entry 70's load-bearing precondition —
    'both dedupe sites exclude QUARANTINED rows, so the dead row never
    blocks or dup-rejects the renamed copy' — existed only as
    docstring prose: deleting the QUARANTINED exclusion from the
    scan-time dedupe passed the FULL arming gate at 782/778
    (finder-proven), while every player following the pipeline's own
    printed coaching got the re-upload terminally parked as DUPLICATE
    of a row that can never deliver (the same-player arm fires BEFORE
    the adjudicated-loser strip). Scan-time site: a NEW sid at a
    different path, SAME md5 + same player as the dead row, must land
    DISCOVERED with no dup verdict anywhere. Mutation-proofed with
    the finder's EXACT deletion in a fixed-tree scratch copy (session
    scratchpad): it fails this pin."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    from pipeline.tests.test_r_loop14 import _H5_SID, _h5_discovered
    _h5_discovered(cfg, ledger)
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid="2026-08-14T11-00-00Z_kamla_c_00000000000000b6", md5="h6-md5"))
    assert ledger.get(_H5_SID)["state"] == "QUARANTINED"
    # the coached correction: same footage re-uploaded under a NEW
    # folder name -> a NEW session id at a different path
    new_sid = "2026-08-14T12-00-00Z_kamla_c_00000000000000c6"
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        op="op2@x.com", sid=new_sid, md5="h5-md5",
        ctime="2026-08-14T12:00:00.000Z"))
    row = ledger.get(new_sid)
    assert row["state"] == "DISCOVERED", \
        "the coached re-upload must enter intake, not park as DUPLICATE"
    assert new_sid in res.discovered
    assert res.duplicates == [] and res.dup_cross == []
    assert "INT_DUP_CROSS" not in (row["reasons_json"] or "")
    assert ledger.get(_H5_SID)["state"] == "QUARANTINED", \
        "the dead row stays dead (gone-is-gone)"


def test_coached_rename_reupload_survives_download_dedupe_too(
        cfg, ledger, monkeypatch):
    """K6 download-time twin (the ruling names BOTH sites): a
    zip-payload re-upload carries no Drive-side md5, so the
    download-time backfill dedupe is the only judge — removing
    QUARANTINED from ITS exclusion tuple parks the coached correction
    at download instead (same-player arm, work dir wiped). The dead
    row's md5 is the REAL hash of the re-uploaded bytes here, exactly
    the byte-identical rename-re-upload the coaching promises works.
    The finder's exact tuple mutant fails this pin (fixed-tree
    scratch proof)."""
    import hashlib
    import subprocess

    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    from pipeline.tests.test_r_loop14 import _H5_SID
    payload = b"h5-rename-bytes"
    real_md5 = hashlib.md5(payload).hexdigest()
    ingest.scan(cfg, ledger,
                entries=make_session_entries(sid=_H5_SID, md5=real_md5))
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid="2026-08-14T11-00-00Z_kamla_c_00000000000000b6", md5="h6-md5"))
    assert ledger.get(_H5_SID)["state"] == "QUARANTINED"
    new_sid = "2026-08-14T12-00-00Z_kamla_c_00000000000000c6"
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        op="op2@x.com", sid=new_sid, files=["bundle.zip"],
        ctime="2026-08-14T12:00:00.000Z"))
    assert new_sid in res.discovered

    def fake_rclone(args, **kw):
        d = None
        for a in args:
            if str(cfg.work) in str(a):
                d = ingest.Path(a)
        d.mkdir(parents=True, exist_ok=True)
        (d / "video.mp4").write_bytes(payload)
        (d / "frames.csv").write_text("frame_id\n")
        (d / "session.json").write_text('{"game_title": "Kamla"}')
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(ingest, "run_rclone", fake_rclone)
    monkeypatch.setattr(ingest, "_probe_duration", lambda v: 123.0)
    kind = ingest.download(cfg, ledger, new_sid)
    assert kind != "duplicate", \
        "the coached re-upload must not dup-park at the download dedupe"
    row = ledger.get(new_sid)
    assert row["state"] == "INGESTED"
    assert "INT_DUP_CROSS" not in (row["reasons_json"] or "")
    assert ledger.get(_H5_SID)["state"] == "QUARANTINED"
