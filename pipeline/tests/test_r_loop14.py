"""r-loop 14 fixes (H1–H9, R8_IMPLEMENTATION_PLAN §3) — pipeline side.

Each test cites the iteration-14 finding it pins (r14 #N, findings of
record in R14_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 5f7015b (session scratchpad), per plan §1/§3.
"""
from __future__ import annotations

import json
import time
from datetime import timedelta, timezone
from types import SimpleNamespace

from pipeline import run as runmod
from pipeline.tests.test_r_loop8 import needs_ffmpeg


# ------- r14 #2≡#3 (H1): counted_at captured BEFORE the sheet's row read

def test_adjudication_during_the_sheet_build_skips_the_stamp(
        cfg, ledger, monkeypatch, capsys):
    """r14 #2≡#3 (H1): the count-time anchor used to be captured AFTER
    write_payment_sheet returned, so a ZIP_ADJ_CHANGED adjudication
    landing during the build (row read done, counted_at not yet taken)
    was neither "before the row read" nor ">= counted_at" — the stamp
    landed silently and the re-upload's hours reached no sheet, ever.
    With the anchor captured pre-build, the whole build window is
    covered by the `>=` arm."""
    from pipeline import ingest, reports
    from pipeline.tests.test_r_loop8 import _daily_seed
    docs: list[bytes] = []
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch,
                                           docs=docs)
    ledger.update(sid, md5_video="")     # zip class: unknowable at count
    real_build = reports.write_payment_sheet
    done = {"x": False}

    def adjudicate_mid_build(*a, **kw):
        out = real_build(*a, **kw)
        if not done["x"]:
            done["x"] = True
            # the download-time deferral's effects (the r12 test idiom
            # for ingest's clear + marker + backfill), landing AFTER
            # the row read but BEFORE the pre-H1 post-build capture
            ledger.update(sid, md5_video="e" * 32, duration_raw_s=None,
                          uploaded_reported_at=None,
                          accepted_reported_at=None)
            ledger.set_state(
                sid, ledger.get(sid)["state"],
                f"{ingest.ZIP_ADJ_CHANGED} (md5  -> {'e' * 32})")
            # roll the wall clock into the NEXT second so a POST-build
            # counted_at capture (the pre-H1 shape) provably postdates
            # the marker — the exact blind window r14 #2 proved
            time.sleep(1.1)
        return out
    monkeypatch.setattr(reports, "write_payment_sheet",
                        adjudicate_mid_build)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    row = ledger.get(sid)
    assert not row["uploaded_reported_at"] and \
        not row["accepted_reported_at"], \
        "a mid-build adjudication must NOT be stamped from the old sheet"
    assert "SKIPPED" in capsys.readouterr().err, "…and must say so"
    # the F6 probe refill restores the duration; the corrected
    # re-upload's hours then reach exactly ONE later sheet
    ledger.update(sid, duration_raw_s=7200.0)
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=1)) is True
    assert b"p@x.com" in docs[-1], \
        "the re-upload's hours must reach the next sheet"
    assert runmod.send_daily_report_if_due(
        cfg, ledger, send + timedelta(days=2)) is True
    assert b"p@x.com" not in docs[-1], "…and exactly once"


def test_pre_build_adjudication_leaves_a_real_md5_for_the_cas_arm(
        cfg, ledger, monkeypatch, capsys):
    """r14 #2 control (H1, the other side of the guard): an adjudication
    strictly BEFORE the sheet build backfills the real md5, so the row
    read snapshots a REAL hash — the recorded-'' arm is never in play,
    the CAS arm governs, and the stamp lands on the bytes the sheet
    actually counted (no over-skip from the earlier anchor)."""
    from pipeline import ingest
    from pipeline.tests.test_r_loop8 import _daily_seed
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch)
    # the deferral ran to completion pre-count: real-md5 backfill +
    # durable marker (the probe refill already restored the duration)
    ledger.update(sid, md5_video="e" * 32)
    ledger.set_state(sid, ledger.get(sid)["state"],
                     f"{ingest.ZIP_ADJ_CHANGED} (md5  -> {'e' * 32})")
    capsys.readouterr()
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["accepted_reported_at"], \
        "the sheet counted the NEW bytes — the CAS stamp must land"
    assert "SKIPPED" not in capsys.readouterr().err


def test_record_at_and_stamp_anchor_are_the_identical_string(
        cfg, ledger, monkeypatch):
    """H1 pin: the durable record's "at" and both stamp calls'
    counted_at are ONE capture. Under a ticking clock (every now()
    call differs by a full second) any re-capture in the chain would
    desynchronize the resume path's replay from the fresh path's skip
    decisions — the identical-string property G1 relied on and H1
    keeps."""
    from pipeline import reports
    from pipeline.tests.test_r_loop8 import _daily_seed
    send, sid, csv_path, day = _daily_seed(cfg, ledger, monkeypatch)
    base = {"t": send.astimezone(timezone.utc)}

    def ticking_now(tz=None):
        base["t"] += timedelta(seconds=1)
        return base["t"].astimezone(tz) if tz else base["t"]
    monkeypatch.setattr(runmod, "datetime",
                        SimpleNamespace(now=ticking_now))
    seen: dict[str, str | None] = {}
    real_up = reports.mark_uploads_reported
    real_acc = reports.mark_accepted_reported

    def up(led, lo, hi, sids=None, md5s=None, counted_at=None):
        seen["up"] = counted_at
        return real_up(led, lo, hi, sids=sids, md5s=md5s,
                       counted_at=counted_at)

    def acc(led, sids, md5s=None, counted_at=None):
        seen["acc"] = counted_at
        return real_acc(led, sids, md5s=md5s, counted_at=counted_at)
    monkeypatch.setattr(reports, "mark_uploads_reported", up)
    monkeypatch.setattr(reports, "mark_accepted_reported", acc)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    rec = json.loads(
        (cfg.reports_dir / day / ".daily-counted.json").read_text())
    assert rec["at"] == seen["up"] == seen["acc"], \
        "the record and both stamps must carry the IDENTICAL anchor"


# ------- r14 #1≡#6 (H2): retranslate session branch anchors its
# ------- fallback on the ledger slug

def _h2_bundle(tmp_path, name, game_block, keybind=None):
    """A real bundle whose raw metadata game block is caller-controlled
    (the r-loop 9/3 degraded-provenance class). Events press w/a/e/q —
    w/a/e are kamla-built-in bound, q only via a custom keybind."""
    from pipeline.tests.test_fix_cut_gate import _make_session
    from pipeline.tests.test_r_loop8 import _created_at
    work = _make_session(tmp_path, seconds=100, name=name)
    started = _created_at(work)
    evs = []
    for k, t0 in (("w", 10.0), ("a", 30.0), ("e", 50.0), ("q", 70.0)):
        evs.append({"t": int(t0 * 1e6), "type": "key", "key": k,
                    "action": "down"})
        evs.append({"t": int((t0 + 2.0) * 1e6), "type": "key", "key": k,
                    "action": "up"})
    raw = work / "raw"
    raw.mkdir(exist_ok=True)
    (raw / "metadata.json").write_text(json.dumps(
        {"recording": {"started_at_utc":
                       started.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"},
         "game": game_block}))
    (raw / "inputs.jsonl").write_text(
        "\n".join(json.dumps(e) for e in evs))
    if keybind is not None:
        (raw / "keybind.json").write_text(json.dumps(keybind))
    return work


def _letter_rows(work, letter):
    import csv
    with (work / "frames.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    keys = {k for r in rows
            for k in (r["input_keys"] or "").split("|") if k}
    actions = {a for r in rows
               if letter in (r["input_keys"] or "").split("|")
               for a in (r["input_actions"] or "").split("|") if a}
    return keys, actions


def _retranslate(work, tmp_path, game, dossier):
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop13 import _fixable
    plan = fixmod.plan_fixes([_fixable("SYN_TS_NOT_PTS")],
                             game=game, has_raw=True)
    assert [f for f, _ in plan["steps"]][0] == "FIX_RETRANSLATE"
    assert not plan["steps"][0][1].get("rerouted")
    return fixmod.apply_fixes(work, plan, game=game,
                              dossier_dir=tmp_path / dossier)


@needs_ffmpeg
def test_retranslate_degraded_metadata_falls_back_to_the_ledger_slug(
        tmp_path):
    """r14 #1≡#6 (H2): the non-reroute session branch anchored its
    built-in fallback on the PLAYER-TYPED chain — a numeric game name
    (the r-loop 9/3 provenance class), no keybind.json → resolve_keybind
    returned {} and 100% of key presses were stripped silently, then
    terminally rejected as INP_KEYS_MISSING + CNT_ACTIONS_FEW. The
    fallback now anchors on the ledger slug _dispatch holds."""
    work = _h2_bundle(tmp_path, "h2degraded", {"name": 12345})
    out = _retranslate(work, tmp_path, "kamla", "d1")
    assert not out["error"], out
    keys, e_actions = _letter_rows(work, "E")
    assert {"W", "A", "E"} <= keys, \
        f"built-in-bound presses must survive the retranslate: {keys}"
    assert "interact" in e_actions, \
        "the ledger game's built-in must resolve the actions"


@needs_ffmpeg
def test_retranslate_degraded_metadata_custom_keybind_still_wins(
        tmp_path):
    """H2 control (r13 #4 intent intact): with the same degraded
    metadata but a usable raw/keybind.json, the session's OWN bind
    governs — the ledger anchor is the FALLBACK, never an override."""
    work = _h2_bundle(tmp_path, "h2custom", {"name": 12345},
                      keybind={"interact": "q", "move_up": "w"})
    out = _retranslate(work, tmp_path, "kamla", "d2")
    assert not out["error"], out
    keys, q_actions = _letter_rows(work, "Q")
    assert "Q" in keys, \
        "the custom bind's press must survive under degraded metadata"
    assert "interact" in q_actions


@needs_ffmpeg
def test_retranslate_wrong_game_metadata_loses_to_the_ledger_slug(
        tmp_path):
    """r14 #6 second flavor (H2, §2 rule 3 — the discriminator split
    the other way): metadata NAMING the other in-scope game used to
    re-bin under the WRONG game's built-in. The ledger slug governs:
    literal e resolves to kamla's 'interact', never outer_wilds'
    'general_confirm'/'general_primary_interact'."""
    work = _h2_bundle(tmp_path, "h2wronggame", {"name": "Outer Wilds"})
    out = _retranslate(work, tmp_path, "kamla", "d3")
    assert not out["error"], out
    keys, e_actions = _letter_rows(work, "E")
    assert "E" in keys
    assert "interact" in e_actions, \
        f"the LEDGER game's semantics must govern: {e_actions}"
    assert not {"general_confirm", "general_primary_interact"} \
        & e_actions, "the metadata game's semantics must NOT leak in"


# ------- r14 #10 (H3): rebuild-reset discards split manifests +
# ------- rowless segment dirs

def test_rebuild_reset_discards_split_manifests_and_rowless_dirs(
        cfg, monkeypatch):
    """r14 #10 (H3): the teardown wiped only work/<sid> — a kill in the
    cutter's manifest-to-child-insert window (manifest + segment dirs on
    disk, zero child rows) carried the pre-recalibration cut through the
    reset; the re-run's crash triage then ADOPTED the VOID gen-1 cut
    (_recover_split -> complete=True over the stale segments) and the
    dirs leaked unreclaimably. The teardown now discards the split
    artifacts and the -analysis dir, as the refix sibling always did."""
    from pipeline.ledger import Ledger
    from pipeline.tests.test_payment_split_r6 import _put
    from pipeline.tests.test_r_loop13 import _rebuild_tool
    reset, parachute, _sys = _rebuild_tool(cfg, monkeypatch)
    led = Ledger(cfg.ledger_path)
    root = "2026-08-14T09-00-00Z_kamla_c_0000000000000h30"
    _put(led, root, state="FIXING", raw=3600.0, player="h3@x.com")
    led.close()
    work = cfg.work
    (work / root).mkdir(parents=True)
    for n in (1, 2):
        seg = work / f"{root}-p{n}"
        seg.mkdir(parents=True)
        (seg / "video.mp4").write_bytes(b"x" * 64)
    (work / f"{root}-analysis").mkdir()
    (work / f"{root}.split-manifest.json").write_text(json.dumps(
        {"segments": [f"{root}-p1", f"{root}-p2"]}))
    monkeypatch.setattr(_sys, "argv", ["recal_rebuild_reset.py", "--yes",
                                       "--backup", str(parachute)])
    assert reset.main() == 0
    assert not (work / f"{root}.split-manifest.json").exists(), \
        "the VOID cut's manifest must not survive the reset"
    assert not (work / f"{root}-p1").exists() and \
        not (work / f"{root}-p2").exists(), \
        "rowless segment dirs must not survive the reset"
    assert not (work / f"{root}-analysis").exists()
    # the re-run reaches FIXING again: crash triage must RE-DERIVE,
    # never adopt the pre-recalibration cut
    led = Ledger(cfg.ledger_path)
    try:
        led.set_state(root, "FIXING")
        complete, kids = runmod._recover_split(cfg, led, root,
                                               led.get(root))
        assert complete is False and kids == [], \
            "no stale adoption: the re-run must re-derive under the " \
            "new rules"
    finally:
        led.close()


# ------- r14 #4 (H4): stable alert dedup — rclone stderr normalized
# ------- at the choke point

def _fake_rclone_run(calls):
    """A subprocess.run stand-in producing production-shaped rclone
    stderr whose wall-clock prefix differs on every attempt (verified
    live against rclone v1.75.0 in R14_FINDINGS #4)."""
    import subprocess

    def fake_run(argv, **kw):
        calls["n"] += 1
        ts = f"2026/08/19 12:{calls['n']:02d}:07"
        return subprocess.CompletedProcess(
            argv, 3, "",
            f"{ts} ERROR : dir not found\n"
            f"{ts} ERROR : Attempt 3/3 failed with 1 errors")
    return fake_run


def test_rclone_alert_text_dedups_across_attempts(cfg, monkeypatch):
    """r14 #4 (H4): AlertBook dedups on the literal message text, and
    every rclone-backed failure alert embedded per-attempt-timestamped
    stderr — the 60-min TTL never fired and one failing session alerted
    every retry (12/h, measured) instead of the designed 1 per TTL
    (accepted item 11). The choke-point normalization restores the
    contract for all three embedders at once."""
    from pipeline import continuous as cont
    from pipeline import ingest
    sent: list[str] = []
    monkeypatch.setattr(cont.telegram, "send_message",
                        lambda c, t: sent.append(t))
    clock = {"t": 0.0}
    book = cont.AlertBook(cfg, 3600.0, mono_fn=lambda: clock["t"])
    calls = {"n": 0}
    monkeypatch.setattr(ingest.subprocess, "run", _fake_rclone_run(calls))
    for _ in range(12):              # one failing attempt every 5 min
        p = ingest.run_rclone(["copy", "src", "dst"])
        # the exact download-lane text shape: continuous._download_one
        # wrapping ingest.download's DownloadError
        book.alert(f"download failed for sid-x (will retry): "
                   f"rclone copy failed x3: {p.stderr.strip()[:300]}")
        clock["t"] += 300.0
    assert len(sent) == 1, \
        f"designed cadence is 1 send per 60-min TTL, got {len(sent)}"


def test_run_rclone_strips_the_stderr_timestamp_prefix(monkeypatch):
    """H4 unit: the choke point strips the leading wall-clock prefix
    from every stderr line; rc/stdout and non-prefixed lines ride
    through untouched."""
    import subprocess

    from pipeline import ingest

    def fake_run(argv, **kw):
        return subprocess.CompletedProcess(
            argv, 3, "listing-output",
            "2026/08/19 12:47:07 ERROR : boom\n"
            "2026/08/19 12:47:08 ERROR : Attempt 3/3 failed\n"
            "plain tail line")
    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    p = ingest.run_rclone(["lsjson", "remote:x"])
    assert p.stderr == ("ERROR : boom\n"
                        "ERROR : Attempt 3/3 failed\n"
                        "plain tail line")
    assert p.returncode == 3 and p.stdout == "listing-output"


def test_run_rclone_timeout_text_is_stable(monkeypatch):
    """H4 control: the synthetic timeout branch was already stable —
    it must come through byte-identical (the one alert text that
    dedup'd correctly pre-fix)."""
    import subprocess

    from pipeline import ingest

    def raise_timeout(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 5)
    monkeypatch.setattr(ingest.subprocess, "run", raise_timeout)
    p = ingest.run_rclone(["copy", "x", "y"], timeout_s=5)
    assert p.returncode == 124 and p.stderr == "timed out after 5s"


# ------- r14 #5 (H5): vanished-folder arm for DISCOVERED rows

_H5_SID = "2026-08-14T10-00-00Z_kamla_c_00000000000000a5"


def _h5_discovered(cfg, ledger):
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    ingest.scan(cfg, ledger,
                entries=make_session_entries(sid=_H5_SID, md5="h5-md5"))
    row = ledger.get(_H5_SID)
    assert row is not None and row["state"] == "DISCOVERED"
    return row


def test_vanished_discovered_row_leaves_intake(cfg, ledger, capsys):
    """r14 #5 (H5): a DISCOVERED row whose Drive folder was deleted
    retried forever — no prune arm covered the state, the empty work
    dir holds no media for the reclaim, and the digest's undownloaded
    backlog stayed permanently inflated. A healthy same-game listing
    missing the path now quarantines it, loudly, with NO INT_PATH
    reason (off the chase list)."""
    from pipeline import ingest, reports
    from pipeline.tests.conftest import make_session_entries
    _h5_discovered(cfg, ledger)
    capsys.readouterr()
    # the folder is deleted; the kamla tree still lists healthy content
    other = make_session_entries(
        sid="2026-08-14T11-00-00Z_kamla_c_00000000000000b6", md5="h6-md5")
    ingest.scan(cfg, ledger, entries=other)
    row = ledger.get(_H5_SID)
    assert row["state"] == "QUARANTINED"
    last = ledger.db.execute(
        "SELECT from_state, to_state, detail FROM events "
        "WHERE session_id=? ORDER BY id", (_H5_SID,)).fetchall()[-1]
    assert (last["from_state"], last["to_state"]) == \
        ("DISCOVERED", "QUARANTINED"), "a genuine transition (rule 5)"
    assert "folder gone from Drive I" in last["detail"]
    assert row["reasons_json"] in ("[]", None, ""), \
        "no INT_PATH reason — must stay off the folder-issues chase list"
    assert reports.build_folder_issues(ledger) == []
    assert "[vanished-discovered]" in capsys.readouterr().err, \
        "one loud line per pruned row"


def test_vanished_discovered_guard_needs_healthy_tree(cfg, ledger):
    """H5 guard controls (§2 rule 4, both sides): an empty/erroring
    listing, a listing where only the OTHER game's tree parsed, and a
    listing that still carries the folder must all leave the row
    untouched."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    _h5_discovered(cfg, ledger)
    ingest.scan(cfg, ledger, entries=[])                    # (a) empty
    assert ledger.get(_H5_SID)["state"] == "DISCOVERED"
    ingest.scan(cfg, ledger, entries=make_session_entries(  # (b) other game
        game="outer_wilds",
        sid="2026-08-14T10-00-00Z_outer_wilds_c_00000000000000f0",
        md5="ow-md5"))
    assert ledger.get(_H5_SID)["state"] == "DISCOVERED"
    ingest.scan(cfg, ledger,                                # (c) still listed
                entries=make_session_entries(sid=_H5_SID, md5="h5-md5"))
    assert ledger.get(_H5_SID)["state"] == "DISCOVERED"


def test_vanished_discovered_with_local_media_still_pruned(cfg, ledger):
    """H5: the trigger is the listing-derived STATE evidence, never
    local media — a DISCOVERED row that happens to hold bytes in work/
    is pruned by the same arm (the reclaim path is separate and only
    ever covered media-holding dirs anyway)."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    _h5_discovered(cfg, ledger)
    wd = cfg.work / _H5_SID
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "video.mp4").write_bytes(b"x" * 1024)
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid="2026-08-14T11-00-00Z_kamla_c_00000000000000b6", md5="h6-md5"))
    assert ledger.get(_H5_SID)["state"] == "QUARANTINED"


def test_vanished_discovered_reappearing_folder_heals(cfg, ledger):
    """H5 self-heal control: if the same sid later reappears at a clean
    path, the existing quarantined-path heal re-registers it — the arm
    must not create an unhealable dead end."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    _h5_discovered(cfg, ledger)
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid="2026-08-14T11-00-00Z_kamla_c_00000000000000b6", md5="h6-md5"))
    assert ledger.get(_H5_SID)["state"] == "QUARANTINED"
    # the same sid reappears at a CLEAN (different) path — the operator
    # re-uploads the folder under the corrected tree
    ingest.scan(cfg, ledger,
                entries=make_session_entries(op="op2@x.com", sid=_H5_SID,
                                             md5="h5-md5"))
    row = ledger.get(_H5_SID)
    assert row["state"] == "DISCOVERED", \
        "a reappearing folder must re-register via the heal branch"
    assert "op2@x.com" in row["drive_path"]
