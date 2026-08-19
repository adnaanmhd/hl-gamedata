"""Tests added by adversarial-review round 4 — ingest half: md5-mismatch
quarantine kind, exclude-before-slice batching, pre-download move heal,
quarantine-heal work-dir wipe, supersede/heal shift-record removal, and
the reworked third-copy cross-dup rule."""
import json

import pytest

from pipeline import config as C
from pipeline import ingest
from pipeline.tests.conftest import make_session_entries

SID1 = "2026-08-14T10-00-00Z_kamla_c_0123456789abcdef"   # entries default

SID_MV = "2026-08-14T10-00-00Z_kamla_c_00000000000000d6"
OLD_PATH = f"kamla/Old Name/p@x.com/{SID_MV}"
NEW_PATH = f"kamla/New Name/p@x.com/{SID_MV}"

SID_A = "2026-08-14T12-00-00Z_kamla_c_00000000000000aa"  # latest ctime
SID_B = "2026-08-14T11-00-00Z_kamla_c_00000000000000ab"  # middle ctime
SID_C = "2026-08-14T09-00-00Z_kamla_c_00000000000000ac"  # earliest ctime


def _seed(ledger, sid, *, state="DISCOVERED", player="p@x.com", op="Op",
          md5="", ctime="2026-08-14T10:00:00.000Z", path=None):
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email=op,
        player_email=player,
        drive_path=path or f"kamla/{op}/{player}/{sid}",
        drive_ctime=ctime, md5_video=md5, bytes_=1, state=state)


# ------------------------------ md5-mismatch download kind (review-r4 #28)

def test_download_md5_mismatch_pins_quarantine_kind(cfg, ledger,
                                                    monkeypatch):
    """review-r4 #28: three failed checksum verifies must surface as
    DownloadError(kind="quarantine") — a bare/transient kind would send
    the corrupt copy back to DISCOVERED and retry it forever."""
    ingest.scan(cfg, ledger, entries=make_session_entries(md5="ff" * 16))
    attempts = []

    def fake_rclone(args, **kw):
        import subprocess
        for a in args:
            if str(cfg.work) in str(a):
                d = ingest.Path(a)
                d.mkdir(parents=True, exist_ok=True)
                (d / "video.mp4").write_bytes(b"never-the-right-bytes")
                attempts.append(1)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ingest, "run_rclone", fake_rclone)
    with pytest.raises(ingest.DownloadError) as ei:
        ingest.download(cfg, ledger, SID1)
    assert ei.value.kind == "quarantine"
    assert "md5 mismatch" in str(ei.value)
    assert len(attempts) == 3            # all three verify attempts burned


# -------------------------- exclude filtered BEFORE slice (review-r4 #15)

def test_next_batch_excludes_before_slice(cfg, ledger):
    """review-r4 #15: a head-of-queue clique of excluded (already
    attempted) sessions must not starve intake — exclusion applies before
    the size slice, so the NEXT sessions surface."""
    n = C.BATCH_SIZE
    sids = []
    for i in range(n + 3):
        sid = f"2026-08-14T{i:02d}-00-00Z_kamla_c_{i:016x}"
        _seed(ledger, sid, md5=f"m{i}",
              ctime=f"2026-08-14T{i:02d}:00:00.000Z")
        sids.append(sid)
    # size cap respected with nothing excluded (FIFO head)
    assert ingest.next_batch(ledger) == sids[:n]
    # entire head excluded -> the tail is batched, not an empty slice
    batch = ingest.next_batch(ledger, exclude=set(sids[:n]))
    assert batch == sids[n:]
    assert batch and len(batch) <= n


# ------------------------------- pre-download MOVE heal (review-r4 #6)

def test_scan_move_heal_repoints_predownload_row(cfg, ledger):
    """review-r4 #6: an operator-folder rename moves the whole subtree;
    a DISCOVERED row whose old path vanished from the listing (same video
    md5 at the new path) is re-pointed instead of retrying a dead path."""
    _seed(ledger, SID_MV, md5="mv-md5", path=OLD_PATH, op="Old Name")
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        op="New Name", player="p@x.com", sid=SID_MV, md5="mv-md5",
        ctime="2026-08-14T10:00:00.000Z"))
    row = ledger.get(SID_MV)
    assert row["drive_path"] == NEW_PATH
    assert row["state"] == "DISCOVERED"
    assert row["operator_email"] == "New Name"
    assert any("drive folder moved" in f for f in res.integrity_flags)
    assert SID_MV not in res.discovered        # re-point, not a discovery


def test_scan_copy_at_both_paths_stays_collision(cfg, ledger):
    """review-r4 #6 gate 1: a COPY leaves both paths listed — that is an
    identity collision, never a move heal."""
    _seed(ledger, SID_MV, md5="mv-md5", path=OLD_PATH, op="Old Name")
    entries = (make_session_entries(op="New Name", player="p@x.com",
                                    sid=SID_MV, md5="mv-md5")
               + make_session_entries(op="Old Name", player="p@x.com",
                                      sid=SID_MV, md5="mv-md5"))
    res = ingest.scan(cfg, ledger, entries=entries)
    row = ledger.get(SID_MV)
    assert row["drive_path"] == OLD_PATH               # NOT re-pointed
    assert row["state"] == "DISCOVERED"
    assert any("session-id collision" in f for f in res.integrity_flags)
    assert not any("drive folder moved" in f for f in res.integrity_flags)


def test_scan_move_heal_refuses_md5_mismatch(cfg, ledger):
    """review-r4 #6 gate 3: old path absent but different video bytes at
    the new path — different bytes are never a move; stays a collision."""
    _seed(ledger, SID_MV, md5="mv-md5", path=OLD_PATH, op="Old Name")
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        op="New Name", player="p@x.com", sid=SID_MV, md5="other-bytes"))
    row = ledger.get(SID_MV)
    assert row["drive_path"] == OLD_PATH               # NOT re-pointed
    assert row["md5_video"] == "mv-md5"                # untouched
    assert any("session-id collision" in f for f in res.integrity_flags)


# ------------------- quarantine heal wipes stale work (review-r4 #21, #7)

def test_quarantine_heal_wipes_stale_work_dirs_and_shift_record(cfg,
                                                                ledger):
    """review-r4 #21 (+ heal half of #7): healing a QUARANTINED path must
    wipe the stale work/<sid> and <sid>-analysis dirs — and the sid's
    entry in the shared translation report — so the fresh download can't
    merge with the old upload's leftovers."""
    sid_q = "2026-08-14T10-00-00Z_kamla_c_00000000000000e4"
    _seed(ledger, sid_q, state="QUARANTINED", path=f"kamla/badpath/{sid_q}")
    work = cfg.work / sid_q
    (work / "raw").mkdir(parents=True)
    (work / "raw" / "inputs.jsonl").write_text("stale sidecar")
    ana = cfg.work / f"{sid_q}-analysis"
    ana.mkdir(parents=True)
    (ana / "report.json").write_text("{}")
    report = cfg.work / "translation_report.json"
    report.write_text(json.dumps({sid_q: {"shift_us": -66700},
                                  "other": {"shift_us": 33350}}))
    res = ingest.scan(cfg, ledger,
                      entries=make_session_entries(sid=sid_q, md5="hh"))
    assert ledger.get(sid_q)["state"] == "DISCOVERED"
    assert any("healed" in f for f in res.integrity_flags)
    assert not work.exists()
    assert not ana.exists()
    data = json.loads(report.read_text())
    assert sid_q not in data
    assert data["other"] == {"shift_us": 33350}        # neighbors intact


# ------------------ supersede drops the shift record (review-r4 #7)

def test_supersede_drops_translation_report_entry(cfg, ledger):
    """review-r4 #7: superseding a rejected slot must drop the old
    upload's entry from work/translation_report.json — left behind, qa
    validates the replacement bytes against the OLD shift."""
    ingest.scan(cfg, ledger, entries=make_session_entries(md5="old"))
    ledger.set_state(SID1, "REJECTED")
    report = cfg.work / "translation_report.json"
    report.write_text(json.dumps({SID1: {"shift_us": -66700},
                                  "other": {"shift_us": 33350}}))
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        md5="new", ctime="2026-08-15T00:00:00.000Z"))
    assert res.superseded == [SID1]
    assert ledger.get(SID1)["state"] == "DISCOVERED"
    data = json.loads(report.read_text())
    assert SID1 not in data
    assert data["other"] == {"shift_us": 33350}        # neighbors intact


# --------------------------- third-copy cross-dup rule (review-r4 #37)

def test_third_copy_inflight_blocker_spares_discovered_sibling(cfg,
                                                               ledger):
    """review-r4 #37 (the finding's triple): with an in-flight copy A and
    a DISCOVERED copy B of the same bytes, an earlier third copy C is
    rejected naming A as the blocker (F3 deviation) — and B is NOT
    clobbered, because un-picking requires ALL copies clobberable."""
    _seed(ledger, SID_A, player="a@x.com", md5="x-dup",
          ctime="2026-08-14T12:00:00.000Z")
    ledger.set_state(SID_A, "VALIDATING")
    _seed(ledger, SID_B, player="b@x.com", md5="x-dup",
          ctime="2026-08-14T11:00:00.000Z")
    # B's folder rides in the listing at its registered path: scan sees
    # the FULL Drive listing in production, and the r14 #5 vanished-
    # folder arm rightly prunes any DISCOVERED row absent from a healthy
    # listing — this test pins the dup rule, not the prune
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=SID_C, player="c@x.com", md5="x-dup",
        ctime="2026-08-14T09:00:00.000Z")
        + make_session_entries(op="Op", player="b@x.com", sid=SID_B,
                               md5="x-dup",
                               ctime="2026-08-14T11:00:00.000Z"))
    assert ledger.get(SID_C)["state"] == "REJECTED"
    assert SID_C in res.dup_cross
    assert "INT_DUP_CROSS" in ledger.get(SID_C)["reasons_json"]
    # the flag names the copy actually in flight, not merely the earliest
    assert any("F3 deviation" in f and SID_A in f
               for f in res.integrity_flags)
    assert ledger.get(SID_A)["state"] == "VALIDATING"  # untouched
    assert ledger.get(SID_B)["state"] == "DISCOVERED"  # NOT clobbered


def test_earliest_new_copy_rejects_all_clobberable_copies(cfg, ledger):
    """review-r4 #37: when every existing copy is still pre-download, the
    earlier new copy un-picks ALL of them — leaving one behind would
    record a keeper that never delivers."""
    _seed(ledger, SID_A, player="a@x.com", md5="x-dup",
          ctime="2026-08-14T12:00:00.000Z")
    _seed(ledger, SID_B, player="b@x.com", md5="x-dup",
          ctime="2026-08-14T11:00:00.000Z")
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=SID_C, player="c@x.com", md5="x-dup",
        ctime="2026-08-14T09:00:00.000Z"))
    assert ledger.get(SID_A)["state"] == "REJECTED"
    assert ledger.get(SID_B)["state"] == "REJECTED"
    assert sorted(res.dup_cross) == sorted([SID_A, SID_B])
    assert ledger.get(SID_C)["state"] == "DISCOVERED"
    assert SID_C in res.discovered


def test_adjudicated_loser_neither_blocks_nor_rerejects(cfg, ledger):
    """review-r4 #37: a copy already adjudicated as a dup loser
    (REJECTED) is a spectator — it neither blocks the new copy nor gets
    re-rejected, even with an earlier createdTime."""
    _seed(ledger, SID_A, player="a@x.com", md5="x-dup", state="REJECTED",
          ctime="2026-08-14T08:00:00.000Z")
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=SID_C, player="c@x.com", md5="x-dup",
        ctime="2026-08-14T09:00:00.000Z"))
    assert ledger.get(SID_C)["state"] == "DISCOVERED"
    assert SID_C in res.discovered
    assert ledger.get(SID_A)["state"] == "REJECTED"    # not re-adjudicated
    assert res.dup_cross == []
