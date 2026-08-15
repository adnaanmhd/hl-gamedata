"""Review-r5 regression tests (split-recovery / cutter / sweep): the r4
adopt-rowed-children recovery rule (#13), strict -p<digits> partial-dir
matching (#26), adopted-split shift propagation (#27), unreadable-manifest
no-touch (#38), rescinded-plan artifact discard on all four _fix_phase
branches (#14), cutter pre-clean of stale segment dirs (#20), and the
stray-manifest terminal sweep (#21)."""
import csv
import json
import os

import pytest

from pipeline import cutter, run as runmod
from pipeline.tests.test_fix_cut_gate import _make_session, needs_ffmpeg
from translator.v2 import V2_FRAME_COLS

SID = "2026-08-15T10-00-00Z_kamla_c_00000000000000e5"


def _seed(ledger, sid=SID, state="DISCOVERED"):
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path=f"kamla/Op/p@x.com/{sid}",
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video="", bytes_=1,
        state=state)


def _child(ledger, kid, parent, state="INGESTED"):
    ledger.insert_session(
        session_id=kid, game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path="kamla/Op/p@x.com/x",
        drive_ctime="2026", md5_video="", bytes_=0, state=state,
        parent_id=parent)


def _dir(cfg, name):
    """A work dir with a marker file — survival is observable."""
    d = cfg.work / name
    d.mkdir(parents=True)
    (d / "marker.txt").write_text("live")
    return d


def _fixing_parent_with_rowed_kids(cfg, ledger):
    """The review-r4 #0 kill window: a prior recovery finished its child
    inserts (rows + work dirs) but died before the SPLIT commit — parent
    still FIXING, manifest already gone."""
    _seed(ledger, SID, state="FIX_QUEUED")
    ledger.set_state(SID, "FIXING", "attempt 1")
    (cfg.work / SID).mkdir(parents=True)
    return [(_child(ledger, kid, SID) or _dir(cfg, kid))
            for kid in (f"{SID}-p1", f"{SID}-p2")]


# ------------------------------- adopt-rowed-children recovery (r5 #13)

def test_recover_split_adopts_rowed_children_without_manifest(
        cfg, ledger, monkeypatch):
    """review-r5 #13(a): rows exist + manifest gone + no rowless partials
    => adopt the rowed children as the completed split — re-deriving via
    REVALIDATING would re-cut and clobber the live children."""
    monkeypatch.setattr(runmod, "_validate_phase", lambda *a, **k: None)
    kid_dirs = _fixing_parent_with_rowed_kids(cfg, ledger)
    sink = set()
    kids = runmod._fix_phase(cfg, ledger, [SID], [], workers=1,
                             children_sink=sink)
    assert ledger.get(SID)["state"] == "SPLIT"
    assert set(kids) == {f"{SID}-p1", f"{SID}-p2"}
    assert sink == {f"{SID}-p1", f"{SID}-p2"}
    for d in kid_dirs:
        assert (d / "marker.txt").exists()    # live work untouched
    assert not (cfg.work / SID).exists()      # parent copy reclaimed


def test_recover_split_rowless_partial_blocks_adoption(cfg, ledger,
                                                       monkeypatch):
    """review-r5 #13(b): an extra ROWLESS {sid}-p3 dir means a NEWER
    interrupted cut — do NOT adopt; the rowless partial is wiped, rowed
    dirs stay, and the parent re-derives via REVALIDATING."""
    monkeypatch.setattr(runmod, "_validate_phase", lambda *a, **k: None)
    kid_dirs = _fixing_parent_with_rowed_kids(cfg, ledger)
    p3 = _dir(cfg, f"{SID}-p3")               # rowless: newer aborted cut
    kids = runmod._fix_phase(cfg, ledger, [SID], [], workers=1,
                             children_sink=set())
    assert ledger.get(SID)["state"] == "REVALIDATING"     # never SPLIT
    assert not p3.exists()                    # rowless partial wiped
    assert ledger.get(f"{SID}-p3") is None    # not adopted
    for d in kid_dirs:
        assert (d / "marker.txt").exists()    # rowed dirs kept
    assert kids == []


# --------------------------- strict -p<digits> partial matching (r5 #26)

def test_rowed_grandchild_dir_survives_split_recovery(cfg, ledger,
                                                      monkeypatch):
    """review-r5 #26 (uncommitted fix 5): {sid}-p1-p1 (a child's own split
    segment) and {sid}-p1-sub match the loose {sid}-p[0-9]* glob but are
    NOT direct partials of sid — the strict -p<digits> fullmatch must
    neither count them as rowless (blocking adoption) nor wipe them."""
    monkeypatch.setattr(runmod, "_validate_phase", lambda *a, **k: None)
    _fixing_parent_with_rowed_kids(cfg, ledger)
    grandkid = f"{SID}-p1-p1"                 # rowed under child p1
    _child(ledger, grandkid, f"{SID}-p1")
    gdir = _dir(cfg, grandkid)
    ndir = _dir(cfg, f"{SID}-p1-sub")         # nested-looking, rowless
    sink = set()
    kids = runmod._fix_phase(cfg, ledger, [SID], [], workers=1,
                             children_sink=sink)
    assert ledger.get(SID)["state"] == "SPLIT"            # not blocked
    assert (gdir / "marker.txt").exists()     # grandchild work intact
    assert (ndir / "marker.txt").exists()     # non-partial dir intact
    assert set(kids) == {f"{SID}-p1", f"{SID}-p2"}
    assert grandkid not in sink               # never the parent's child


def test_discard_split_artifacts_spares_grandchild_dirs(cfg, ledger,
                                                        monkeypatch):
    """review-r5 #26 (uncommitted fix 5): the rescinded-plan discard wipes
    only DIRECT {sid}-p<digits> partials — a rowed grandchild's dir is
    another session's live work and must survive; the direct rowless
    partial and the manifest still go."""
    monkeypatch.setattr(runmod, "_validate_phase", lambda *a, **k: None)
    monkeypatch.setattr(runmod.fix, "plan_fixes",
                        lambda *a, **k: {"steps": [],
                                         "unfixable": ["CNT_SHORT"]})
    _seed(ledger, SID, state="FIX_QUEUED")
    (cfg.work / SID).mkdir(parents=True)
    _child(ledger, f"{SID}-p1", SID, state="SPLIT")       # split further
    grandkid = f"{SID}-p1-p1"
    _child(ledger, grandkid, f"{SID}-p1")
    gdir = _dir(cfg, grandkid)
    stale = _dir(cfg, f"{SID}-p2")            # rowless direct partial
    manifest = cfg.work / f"{SID}.split-manifest.json"
    manifest.write_text(json.dumps(
        {"parent": SID, "segments": [f"{SID}-p2"], "dropped": 0}))
    runmod._fix_phase(cfg, ledger, [SID], [], workers=1,
                      children_sink=set())
    assert ledger.get(SID)["state"] == "REJECTED"
    assert not stale.exists() and not manifest.exists()   # discard worked
    assert (gdir / "marker.txt").exists()     # grandchild spared


# ------------------------------ adopted-split shift propagation (r5 #27)

def test_adopted_split_propagates_shift_record_to_children(cfg, ledger,
                                                           monkeypatch):
    """review-r5 #27 (uncommitted fix 7): the adopt branch must propagate
    the parent's translation_report shift entry to the children like the
    live cut path does — else shift-corrected adoptees spuriously fail
    qa's raw recheck and burn their fix budget."""
    monkeypatch.setattr(runmod, "_validate_phase", lambda *a, **k: None)
    _fixing_parent_with_rowed_kids(cfg, ledger)
    report = cfg.work / "translation_report.json"
    report.write_text(json.dumps({SID: {"shift_us": 41000}}))
    runmod._fix_phase(cfg, ledger, [SID], [], workers=1,
                      children_sink=set())
    assert ledger.get(SID)["state"] == "SPLIT"
    data = json.loads(report.read_text())
    assert data[f"{SID}-p1"] == {"shift_us": 41000}
    assert data[f"{SID}-p2"] == {"shift_us": 41000}


# ------------------------------- unreadable manifest no-touch (r5 #38)

def test_recover_split_unreadable_manifest_touches_nothing(cfg, ledger):
    """review-r5 #38 (uncommitted fix 6): a manifest that EXISTS but fails
    to read (transient I/O fault, here chmod 000) is NOT "killed mid-cut"
    — recovery must return without wiping dirs or adopting rows, so the
    next run can retry against the intact cut."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root: chmod 000 cannot block the read")
    _seed(ledger, SID, state="FIX_QUEUED")
    ledger.set_state(SID, "FIXING", "attempt 1")
    (cfg.work / SID).mkdir(parents=True)
    _child(ledger, f"{SID}-p1", SID)          # insert loop got this far
    d1 = _dir(cfg, f"{SID}-p1")
    d2 = _dir(cfg, f"{SID}-p2")               # listed, completed, rowless
    manifest = cfg.work / f"{SID}.split-manifest.json"
    manifest.write_text(json.dumps(
        {"parent": SID, "segments": [f"{SID}-p1", f"{SID}-p2"],
         "dropped": 0}))
    manifest.chmod(0o000)
    try:
        manifest.read_text()
        manifest.chmod(0o600)
        pytest.skip("mode 000 still readable on this filesystem")
    except PermissionError:
        pass
    done, kids = runmod._recover_split(cfg, ledger, SID, ledger.get(SID))
    assert done is False
    assert kids == [f"{SID}-p1"]              # only what already had rows
    assert (d1 / "marker.txt").exists()       # nothing wiped
    assert (d2 / "marker.txt").exists()
    manifest.chmod(0o600)                     # restore for tmp cleanup
    assert manifest.exists()                  # manifest kept for retry
    assert ledger.get(f"{SID}-p2") is None    # no adoption either
    assert ledger.get(SID)["state"] == "FIXING"


# --------------------------- rescinded-plan artifact discard (r5 #14)

@pytest.mark.parametrize("plan,apply_out,end_state", [
    pytest.param({"steps": [], "unfixable": ["CNT_SHORT"]},
                 None, "REJECTED", id="unfixable"),
    pytest.param({"steps": [], "unfixable": []},
                 None, "REJECTED", id="no-steps"),
    pytest.param({"steps": [("FIX_KEY_HYGIENE", {})], "unfixable": []},
                 {"applied": [], "children": None,
                  "error": "FIX_KEY_HYGIENE: boom"},
                 "REVALIDATING", id="fix-error"),
    pytest.param({"steps": [("FIX_CUT_SEGMENTS",
                             {"cut": [[100.0, 110.0]]})], "unfixable": []},
                 {"applied": [], "error": None,
                  "children": {"segments": [], "dropped": [
                      {"t0": 0, "t1": 10, "why": "under minimum"}]}},
                 "REJECTED", id="empty-split"),
])
def test_rescinded_plan_discards_stale_cut_artifacts(
        cfg, ledger, monkeypatch, plan, apply_out, end_state):
    """review-r5 #14: every branch that rescinds a plan (unfixable /
    no-steps / fix-error / empty-split-reject) must discard stale cut
    leftovers — a surviving manifest + segment dir would be adopted as a
    completed split by a later FIXING crash."""
    monkeypatch.setattr(runmod, "_validate_phase", lambda *a, **k: None)
    monkeypatch.setattr(runmod.fix, "plan_fixes", lambda *a, **k: plan)
    if apply_out is not None:
        monkeypatch.setattr(runmod.fix, "apply_fixes",
                            lambda *a, **k: apply_out)
    _seed(ledger, SID, state="FIX_QUEUED")
    (cfg.work / SID).mkdir(parents=True)
    stale = _dir(cfg, f"{SID}-p1")            # a prior attempt's cut
    manifest = cfg.work / f"{SID}.split-manifest.json"
    manifest.write_text(json.dumps(
        {"parent": SID, "segments": [f"{SID}-p1"], "dropped": 0}))
    kids = runmod._fix_phase(cfg, ledger, [SID], [], workers=1,
                             children_sink=set())
    assert ledger.get(SID)["state"] == end_state          # never SPLIT
    assert not stale.exists()
    assert not manifest.exists()
    assert kids == [] and ledger.get(f"{SID}-p1") is None


# ------------------------------------- cutter pre-clean (r5 #20)

@needs_ffmpeg
def test_cutter_precleans_stale_segment_dir(tmp_path):
    """review-r5 #20: a stale {sid}-pN dir (rescinded plan that escaped
    the run-layer discard) must never merge its files into the fresh cut
    — the cutter wipes it before writing this attempt's segment."""
    d = _make_session(tmp_path, seconds=80)
    sid = json.loads((d / "session.json").read_text())["session_id"]
    out = tmp_path / "out"
    stale = out / f"{sid}-p1"
    stale.mkdir(parents=True)
    (stale / "orphan.bin").write_bytes(b"stale sidecar")
    (stale / "frames.csv").write_text("frame_id\n0\n")    # old boundaries
    res = cutter.cut_segments(d, [(0.0, 80.0)], out)
    assert [g["id"] for g in res["segments"]] == [f"{sid}-p1"]
    assert not (stale / "orphan.bin").exists()            # stale gone
    with (stale / "frames.csv").open(newline="") as f:    # fresh csv only
        assert next(csv.reader(f)) == V2_FRAME_COLS
    assert (stale / "video.mp4").exists()


# ----------------------------- terminal-work manifest sweep (r5 #21)

def test_sweep_terminal_work_reclaims_stray_split_manifest(cfg, ledger):
    """review-r5 #21: a split-manifest left beside terminal work (parent
    already SPLIT) is unlinked by the sweep; a live sid's manifest — the
    input recovery still needs — survives, as does an unknown sid's."""
    _seed(ledger, SID, state="SPLIT")
    live = SID.replace("e5", "a5")
    _seed(ledger, live, state="FIXING")
    stray = cfg.work / f"{SID}.split-manifest.json"
    stray.write_text("{}")
    keep = cfg.work / f"{live}.split-manifest.json"
    keep.write_text("{}")
    orphan = cfg.work / "not-a-known-sid.split-manifest.json"
    orphan.write_text("{}")
    runmod._sweep_terminal_work(cfg, ledger)
    assert not stray.exists()                 # terminal parent: reclaimed
    assert keep.exists()                      # mid-pipeline: kept
    assert orphan.exists()                    # rowless: left alone
