"""Tests added by adversarial-review round 5 — ingest heals + ledger +
payment sheet: quarantine-heal slot reset, player-gated move heal,
path-derived quarantine ids, vanished bad-path clearing, supersede stamp
clear, backup tmp hygiene, and the stamped-root / never-downloaded-reject
sheet rules."""
import types
from datetime import datetime, timezone

import pytest

from pipeline import config as C
from pipeline import ingest, reports
from pipeline import ledger as ledgermod
from pipeline.tests.conftest import make_session_entries


def _zip_entries(sid, op="Op", player="p@x.com",
                 ctime="2026-08-15T09:00:00.000Z"):
    """A zip-payload session folder: no video.mp4 in the listing, so the
    Drive-side video md5 is unknowable ('')."""
    base = f"kamla/{op}/{player}/{sid}"
    return [{"Path": f"{base}/session-001.zip", "Name": "session-001.zip",
             "IsDir": False, "Size": 777, "ModTime": ctime, "Hashes": {}}]


def _junk_entries(path, name="frames.csv"):
    """A file-bearing dir at a non-session depth -> path quarantine."""
    return [{"Path": f"{path}/{name}", "Name": name, "IsDir": False,
             "Size": 5, "ModTime": "2026-08-15T10:00:00.000Z", "Hashes": {}}]


def _int_path_reason(evidence):
    return [{"code": "INT_PATH", "blocking": True, "fixable": False,
             "params": {}, "evidence": evidence}]


def _mk_root(led, sid, ctime, raw=None, state="DISCOVERED",
             player="p@x.com"):
    led.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email=player, drive_path=f"kamla/Op/{player}/{sid}",
        drive_ctime=ctime, md5_video=sid[-4:], bytes_=1, state=state)
    if raw is not None:
        led.update(sid, duration_raw_s=raw)


def _sheet_and_mark(led, bounds):
    """Generation + stamping exactly as production wires them (r5 #3).
    BOTH marks since the ruled uploaded/accepted split (Adnaan 08-18)."""
    counted: list = []
    accepted: list = []
    rows = reports.build_sheet_rows(
        led, datetime.now(C.IST), bounds=bounds, counted_out=counted,
        accepted_out=accepted)
    reports.mark_uploads_reported(led, *bounds, sids=counted)
    reports.mark_accepted_reported(led, accepted)
    return rows


# --------------------- quarantine heal resets the slot (r5 #23/#9, fix 15)

def test_quarantine_heal_resets_slot_like_supersede(cfg, ledger):
    """r5 #23/#9 (fix 15): a heal is a fresh-upload event — the burned
    fix_attempts, stale durations, rrd_sampled, delivered_at and the old
    sheet's uploaded_reported_at must all reset like supersede; inherited,
    the corrected re-upload was insta-rejected 'fix retries exhausted' and
    its hours never reached another payment sheet."""
    sid = "2026-08-14T10-00-00Z_kamla_c_00000000000000e9"
    # player p1@x.com matches the heal listing's builder default: the
    # scenario is an OPERATOR-folder rename (player unchanged) — the old
    # p@x.com seed accidentally made it cross-player, which the r17 #1
    # identity guard rightly refuses (different bytes, different player)
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Old Name",
        player_email="p1@x.com", drive_path=f"kamla/Old Name/p1@x.com/{sid}",
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video="oldmd5",
        bytes_=5, state="READY")
    # both fix attempts burned, footage probed/delivered, sheet stamped —
    # then delivery crashes and quarantines the row
    ledger.update(sid, fix_attempts=2, duration_raw_s=900.0,
                  duration_delivered_s=880.0, rrd_sampled=1,
                  delivered_at="2026-08-14T12:00:00+00:00",
                  uploaded_reported_at="2026-08-14T13:00:00+00:00")
    ledger.set_reasons(sid, _int_path_reason("stale"), 3)
    ledger.set_state(sid, "QUARANTINED", "delivery crashed")
    # operator re-uploads corrected bytes under a renamed operator folder
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        op="New Name", sid=sid, md5="newmd5"))
    row = ledger.get(sid)
    assert row["state"] == "DISCOVERED"
    assert row["drive_path"] == f"kamla/New Name/p1@x.com/{sid}"
    assert row["md5_video"] == "newmd5"
    assert row["fix_attempts"] == 0            # NOT 'retries exhausted'
    assert row["duration_raw_s"] is None       # old bytes never re-counted
    assert row["duration_delivered_s"] is None
    assert row["rrd_sampled"] == 0
    assert row["delivered_at"] is None
    assert row["uploaded_reported_at"] is None  # late guard re-armed
    assert row["reasons_json"] == "[]" and row["bin"] is None
    assert sid in res.discovered
    assert any("healed" in f for f in res.integrity_flags)


# ---------------- move heal with unknown md5: player gate (r5 #41, fix 16)

def test_move_heal_md5_unknown_operator_rename_still_heals(cfg, ledger):
    """r5 #41 (fix 16) motivating case: with no byte identity anywhere
    (zip payloads, md5 '' on both sides) an operator-folder rename keeps
    the PLAYER segment — the move heal must still fire."""
    sid = "2026-08-14T10-00-00Z_kamla_c_0000000000000f21"
    old = f"kamla/Old Name/p@x.com/{sid}"
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Old Name",
        player_email="p@x.com", drive_path=old,
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video="", bytes_=777,
        state="DISCOVERED")
    res = ingest.scan(cfg, ledger,
                      entries=_zip_entries(sid, op="New Name"))
    row = ledger.get(sid)
    assert row["drive_path"] == f"kamla/New Name/p@x.com/{sid}"
    assert row["operator_email"] == "New Name"
    assert row["state"] == "DISCOVERED"
    assert any("drive folder moved" in f for f in res.integrity_flags)


def test_move_heal_md5_unknown_cross_player_stays_collision(cfg, ledger):
    """r5 #41 (fix 16): a same-sid folder in ANOTHER player's tree with
    md5 unknown must NOT heal — re-pointing would flip payment
    attribution and deliver unverifiable bytes. Stays a collision."""
    sid = "2026-08-14T10-00-00Z_kamla_c_0000000000000f22"
    old = f"kamla/Op/a@x.com/{sid}"
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email="a@x.com", drive_path=old,
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video="", bytes_=777,
        state="DISCOVERED")
    res = ingest.scan(cfg, ledger,
                      entries=_zip_entries(sid, player="b@x.com"))
    row = ledger.get(sid)
    assert row["drive_path"] == old                    # NOT re-pointed
    assert row["player_email"] == "a@x.com"            # payee unchanged
    assert any("session-id collision" in f for f in res.integrity_flags)
    assert not any("drive folder moved" in f for f in res.integrity_flags)


# ------------- path-derived ids for junk-dir quarantines (r5 #2, fix 17)

def test_same_basename_junk_dirs_get_distinct_ledger_rows(cfg, ledger):
    """r5 #2 (fix 17): two players' junk subfolders both named 'out' must
    BOTH get ledger rows (path-derived sid name~md5[:8]) — bare-basename
    ids collapsed the second misupload onto the first's PK row and it
    vanished from the chase list."""
    path_a = ("kamla/Op/a@x.com/"
              "2026-08-15T10-00-00Z_kamla_c_00000000000000a1/out")
    path_b = ("kamla/Op/b@x.com/"
              "2026-08-15T11-00-00Z_kamla_c_00000000000000b1/out")
    entries = _junk_entries(path_a) + _junk_entries(path_b)
    ingest.scan(cfg, ledger, entries=entries)
    rows = ledger.by_state("QUARANTINED")
    assert sorted(r["drive_path"] for r in rows) == sorted([path_a, path_b])
    assert all(r["session_id"].startswith("out~") for r in rows)
    assert ledger.get("out") is None              # no bare-basename row
    # both misuploads reach the chase list, attributed to their own paths
    issues = reports.build_folder_issues(ledger)
    assert sorted(r["drive_path"] for r in issues) == \
        sorted([path_a, path_b])
    # deterministic ids: a rescan finds its own rows, inserts nothing new
    ingest.scan(cfg, ledger, entries=entries)
    assert len(ledger.by_state("QUARANTINED")) == 2


def test_legacy_bare_name_quarantine_row_respected(cfg, ledger):
    """r5 #2 (fix 17): a pre-existing legacy bare-name row for the SAME
    path is respected — no duplicate path-derived insert for it."""
    path = ("kamla/Op/a@x.com/"
            "2026-08-15T10-00-00Z_kamla_c_00000000000000a2/out")
    ledger.insert_session(
        session_id="out", game="", operator_email="", player_email="",
        drive_path=path, drive_ctime="", md5_video="", bytes_=0,
        state="QUARANTINED", detail="path depth 5 != 4")
    ledger.set_reasons("out", _int_path_reason("path depth 5 != 4"), 3)
    ingest.scan(cfg, ledger, entries=_junk_entries(path))
    rows = [r for r in ledger.by_state("QUARANTINED")
            if r["drive_path"] == path]
    assert len(rows) == 1
    assert rows[0]["session_id"] == "out"         # the legacy row, alone


# ----------------- vanished bad-path rows leave the chase (r5 #1, fix 18)

def test_vanished_bad_path_cleared_with_audit_event(cfg, ledger):
    """r5 #1 (fix 18): a QUARANTINED INT_PATH row whose folder is GONE
    from a healthy listing drops off the folder-issues chase list —
    reasons cleared, same-state audit event; the row itself stays."""
    ingest.scan(cfg, ledger, entries=_junk_entries("kamla/stray_junk"))
    sid = ledger.by_state("QUARANTINED")[0]["session_id"]
    assert "INT_PATH" in ledger.get(sid)["reasons_json"]
    # operator deleted/renamed the folder; kamla tree lists healthy
    ingest.scan(cfg, ledger, entries=make_session_entries())
    row = ledger.get(sid)
    assert row["state"] == "QUARANTINED"          # terminal state kept
    assert row["reasons_json"] == "[]"            # chase key cleared
    last = ledger.db.execute(
        "SELECT from_state, to_state, detail FROM events "
        "WHERE session_id=? ORDER BY id", (sid,)).fetchall()[-1]
    assert (last["from_state"], last["to_state"]) == \
        ("QUARANTINED", "QUARANTINED")            # audit, not a transition
    assert "no longer on Drive" in last["detail"]
    assert reports.build_folder_issues(ledger) == []


def test_vanished_bad_path_guard_needs_healthy_tree(cfg, ledger):
    """r5 #1 (fix 18) guard: an empty listing, another game's tree, or the
    folder STILL being listed must never clear the reasons — only a
    healthy same-game listing missing the folder counts as fixed."""
    stray = "kamla/stray_junk"
    ingest.scan(cfg, ledger, entries=_junk_entries(stray))
    sid = ledger.by_state("QUARANTINED")[0]["session_id"]
    # (a) empty/erroring listing clears NOTHING
    ingest.scan(cfg, ledger, entries=[])
    assert "INT_PATH" in ledger.get(sid)["reasons_json"]
    # (b) a listing where only the OTHER game's tree parsed
    ingest.scan(cfg, ledger, entries=make_session_entries(
        game="outer_wilds",
        sid="2026-08-14T10-00-00Z_outer_wilds_c_00000000000000f0",
        md5="ow-md5"))
    assert "INT_PATH" in ledger.get(sid)["reasons_json"]
    # (c) healthy kamla listing with the bad folder STILL present
    ingest.scan(cfg, ledger,
                entries=make_session_entries(md5="k-md5")
                + _junk_entries(stray))
    assert "INT_PATH" in ledger.get(sid)["reasons_json"]


# ------------------- supersede clears the sheet stamp (r5 #7, fix 20)

def test_supersede_clears_uploaded_reported_at(cfg, ledger):
    """r5 #7 (fix 20): the stamp belonged to the OLD upload's sheet —
    inherited, it blocked the corrected re-upload's hours from the
    late-arrival guard on every future sheet."""
    sid = "2026-08-14T10-00-00Z_kamla_c_0000000000000f30"
    _mk_root(ledger, sid, "2026-08-14T10:00:00.000Z", raw=900.0)
    ledger.set_state(sid, "REJECTED")
    ledger.update(sid, uploaded_reported_at="2026-08-14T13:00:00+00:00")
    ledger.supersede(sid, new_md5="newmd5", new_bytes=2,
                     new_ctime="2026-08-15T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    assert ledger.get(sid)["uploaded_reported_at"] is None


# ---------------------- backup tmp hygiene (r5 #4/#28, fix 21)

def test_backup_daily_precleans_stale_torn_tmps(cfg, ledger):
    """r5 #4/#28 (fix 21): torn .ledger-*.db.tmp leftovers from a
    mid-backup kill poisoned the SAME day's later attempts (backup onto a
    torn file raises DatabaseError) and were mirrored into the DR bucket
    forever — backup_daily must pre-clean them and still succeed."""
    cfg.backups.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (cfg.backups / f".ledger-{today}.db.tmp").write_bytes(b"torn same-day")
    (cfg.backups / ".ledger-2026-08-01.db.tmp").write_bytes(b"torn old")
    dst = ledger.backup_daily(cfg.backups)
    assert dst is not None and dst.exists()
    assert list(cfg.backups.glob(".ledger-*.db.tmp")) == []
    import sqlite3
    db = sqlite3.connect(dst)                     # a REAL snapshot, not torn
    db.execute("SELECT COUNT(*) FROM sessions").fetchone()
    db.close()


def test_backup_daily_failure_unlinks_tmp_before_reraise(cfg, ledger,
                                                         monkeypatch):
    """r5 #4/#28 (fix 21): on failure — BaseException included, a
    mid-backup KeyboardInterrupt is the motivating kill — the tmp is
    unlinked before the re-raise, so one crash can't orphan a torn file."""
    def torn_connect(path):
        ingest.Path(path).write_bytes(b"torn")    # tmp exists at the crash
        raise KeyboardInterrupt("kill mid-backup (simulated)")
    monkeypatch.setattr(ledgermod, "sqlite3",
                        types.SimpleNamespace(connect=torn_connect))
    with pytest.raises(KeyboardInterrupt):
        ledger.backup_daily(cfg.backups)
    assert list(cfg.backups.glob(".ledger-*.db.tmp")) == []


# ------------- stamped in-window roots never re-count (r5 #33/#43, fix 13a)

def test_stamped_in_window_root_not_recounted_on_window_rewind(ledger):
    """r5 #33/#43 (fix 13a): in_window requires the root be UNSTAMPED —
    an anchor-loss/rewind re-opening an already-reported interval must
    yield a smaller sheet, never a second count of the same hours."""
    sid = "2026-08-15T10-00-00Z_kamla_c_0000000000000fc1"
    _mk_root(ledger, sid, "2026-08-15T10:00:00.000Z", raw=3600.0)
    w = ("2026-08-15T06:45:22+00:00", "2026-08-16T06:45:22+00:00")
    s1 = _sheet_and_mark(ledger, w)
    assert len(s1) == 1 and s1[0]["kamla_hrs_uploaded"] == 1.0
    # anchor lost -> the exact same interval regenerates: nothing doubles
    assert reports.build_sheet_rows(ledger, datetime.now(C.IST),
                                    bounds=w) == []


# ------- never-downloaded reject reaches the sheet once (r5 #12, fix 13b)

def test_never_downloaded_reject_late_labels_exactly_once(ledger):
    """r5 #12 (fix 13b): a terminal REJECTED root that never downloads
    (scan-time cross-dup, duration_raw_s NULL forever) whose ctime
    predates the window still reaches the sheet as a late arrival — its
    dup label appears for the player, then the stamp keeps it off every
    later sheet (exactly once across two sheets)."""
    sid = "2026-08-14T00-00-00Z_kamla_c_0000000000000fc2"
    w1 = ("2026-08-14T06:45:22+00:00", "2026-08-15T06:45:22+00:00")
    w2 = ("2026-08-15T06:45:22+00:00", "2026-08-16T06:45:22+00:00")
    w3 = ("2026-08-16T06:45:22+00:00", "2026-08-17T06:45:22+00:00")
    # window 1 generated before the folder completed: no row yet
    assert _sheet_and_mark(ledger, w1) == []
    # folder completes late, is rejected AT SCAN as a cross-dup: no
    # download ever runs, duration_raw_s stays NULL, ctime is pre-w2
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email="dup@x.com", drive_path=f"kamla/Op/dup@x.com/{sid}",
        drive_ctime="2026-08-14T00:01:00.000Z", md5_video="d", bytes_=1,
        state="REJECTED", detail="cross-identity duplicate")
    ledger.set_reasons(sid, [
        {"code": "INT_DUP_CROSS", "blocking": True, "fixable": False,
         "params": {}, "evidence": "video md5 identical to other"}], 3)
    s2 = _sheet_and_mark(ledger, w2)
    assert len(s2) == 1
    assert s2[0]["kamla_rejection_reasons"] == "dup"   # attribution lands
    assert s2[0]["kamla_hrs_uploaded"] == 0.0          # no probed hours
    # stamped by the w2 send: never again
    assert _sheet_and_mark(ledger, w3) == []
