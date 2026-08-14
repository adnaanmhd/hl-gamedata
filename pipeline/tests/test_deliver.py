import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import config as C
from pipeline import deliver, ingest

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")


def test_rrd_sampling_deterministic_and_near_20pct():
    a = deliver.rrd_sampled("s1", "kamla", "08-15-2026")
    assert a == deliver.rrd_sampled("s1", "kamla", "08-15-2026")
    n = sum(deliver.rrd_sampled(f"s{i}", "kamla", "08-15-2026")
            for i in range(2000))
    assert 300 < n < 500          # ~20% of 2000


def _fake_session(cfg, ledger, sid, game="kamla"):
    from pipeline.tests.conftest import make_session_entries
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=sid, game=game, md5="m"))
    ledger.set_state(sid, "READY")
    work = cfg.work / sid
    work.mkdir(parents=True, exist_ok=True)
    (work / "video.mp4").write_bytes(b"vv")
    (work / "frames.csv").write_text("frame_id\n0\n")
    (work / "session.json").write_text(json.dumps(
        {"session_id": sid, "duration_seconds": 90.0}))
    (work / "session.rrd").touch()          # stub — must never ship
    (work / "rrd_creation.py").write_text("# script")
    return work


SID = "2026-08-14T10-00-00Z_kamla_c_0123456789abcdef"


def test_deliver_session_full_flow(cfg, ledger, monkeypatch):
    work = _fake_session(cfg, ledger, SID)
    uploaded = {}

    def fake_rclone(args, **kw):
        if args[0] == "copy":
            src = Path(args[-2])
            uploaded["files"] = {
                f.name: {"Size": f.stat().st_size,
                         "Hashes": {"md5": hashlib.md5(
                             f.read_bytes()).hexdigest()}}
                for f in src.iterdir() if f.is_file()}
            uploaded["dest"] = args[-1]
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "lsjson":
            out = [dict(Name=n, IsDir=False, **v)
                   for n, v in uploaded.get("files", {}).items()]
            return subprocess.CompletedProcess(args, 0, json.dumps(out), "")
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(deliver, "run_rclone", fake_rclone)
    monkeypatch.setattr(deliver, "final_gate", lambda d, s: (True, []))
    monkeypatch.setattr(deliver, "rrd_sampled", lambda *a, **k: False)
    out = deliver.deliver_session(cfg, ledger, SID)
    assert out.status == "delivered"
    assert abs(out.hours - 0.025) < 1e-6
    row = ledger.get(SID)
    assert row["state"] == "DELIVERED"
    assert row["duration_delivered_s"] == 90.0
    assert row["rrd_sampled"] == 0
    assert not work.exists()                       # wiped post-verify only
    # non-sampled sessions ship the 3 spec files, never the stub rrd
    assert set(uploaded["files"]) == set(deliver.SPEC_FILES)
    assert uploaded["dest"].startswith(
        f"drive-deliver:humynlabs/")


def test_deliver_verify_failure_keeps_local_media(cfg, ledger, monkeypatch):
    work = _fake_session(cfg, ledger, SID)

    def fake_rclone(args, **kw):
        if args[0] == "copy":
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "lsjson":
            return subprocess.CompletedProcess(args, 0, "[]", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(deliver, "run_rclone", fake_rclone)
    monkeypatch.setattr(deliver, "final_gate", lambda d, s: (True, []))
    monkeypatch.setattr(deliver, "rrd_sampled", lambda *a, **k: False)
    out = deliver.deliver_session(cfg, ledger, SID)
    assert out.status == "failed_upload"
    assert "missing on Drive II" in out.detail
    row = ledger.get(SID)
    assert row["state"] == "PACKAGED"              # resumable, not DELIVERED
    assert row["duration_delivered_s"] is None
    assert work.exists()                           # nothing deleted


def test_deliver_failed_gate_routes_back(cfg, ledger, monkeypatch):
    _fake_session(cfg, ledger, SID)
    monkeypatch.setattr(deliver, "rrd_sampled", lambda *a, **k: False)
    monkeypatch.setattr(deliver, "final_gate",
                        lambda d, s: (False, ["FAIL: broken"]))
    out = deliver.deliver_session(cfg, ledger, SID)
    assert out.status == "failed_gate"
    assert ledger.get(SID)["state"] == "READY"     # unchanged; Phase III


@needs_ffmpeg
def test_final_gate_waives_rrd_only_for_unsampled(tmp_path):
    from pipeline.tests.test_fix_cut_gate import _make_session
    d = _make_session(tmp_path, seconds=80)
    (d / "session.rrd").unlink()
    (d / "rrd_creation.py").unlink()
    ok, fails = deliver.final_gate(d, sampled=False)
    assert ok, fails                       # waived BY FILENAME
    ok2, fails2 = deliver.final_gate(d, sampled=True)
    assert not ok2                         # sampled sessions need the pair
    # a real failure must never hide behind the waiver
    (d / "frames.csv").write_text("bad,header\n1,2\n")
    ok3, fails3 = deliver.final_gate(d, sampled=False)
    assert not ok3 and any("header" in f for f in fails3)


def test_finalize_rejected_writes_coaching_and_wipes(cfg, ledger):
    work = _fake_session(cfg, ledger, SID)
    ledger.set_state(SID, "REJECTED")
    ledger.set_reasons(SID, [
        {"code": "CNT_SHORT", "blocking": True, "fixable": False,
         "params": {}, "evidence": "clip 50s"},
        {"code": "CNT_NOTIF_MID", "blocking": True, "fixable": False,
         "params": {}, "evidence": "toast at 60s"}], 3)
    deliver.finalize_rejected(cfg, ledger, SID)
    text = (cfg.dossiers / SID / "coaching.md").read_text()
    assert "CNT_SHORT" in text and "Do Not Disturb" in text
    assert not work.exists()
