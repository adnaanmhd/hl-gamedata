"""CONT_UPLOAD_WORKERS 1 -> 4 (Adnaan 2026-08-20, relayed through the
sibling session to the flip session): the single serial U lane delivered
~40 sessions/h against ~100 verdicts/h in the first production hour, parked
40+ READY rows, and — because READY rows hold local media — filled the
media cap and choked intake.

The serial lane existed to protect the §1.4 15%-floor read in
deliver.deliver_session: read today's DELIVERED count, decide whether to
force an rrd. With N lanes the read is STALE, not merely racy — sibling
lanes decide minutes before any of them reaches DELIVERED, so they all saw
the same count and all forced (CPU minutes each) or none did (floor dip).
The fix: a lane-wide lock around read -> decide -> record, with the count
including in-flight decisions (PACKAGED/UPLOADED rows, READY rows already
marked sampled) and the decision recorded BEFORE the slow generation.

The first two tests were proven RED on the pre-fix tree in a scratch copy
outside the repo.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from pipeline import config as C
from pipeline import deliver, ingest
from pipeline.tests.conftest import make_session_entries

SID_A = "2026-08-14T10-00-00Z_kamla_c_00000000000000a1"
SID_B = "2026-08-14T11-00-00Z_kamla_c_00000000000000b2"


def _ready(cfg, ledger, sid):
    ingest.scan(cfg, ledger, entries=make_session_entries(sid=sid, md5=sid[-4:]))
    ledger.set_state(sid, "READY")
    work = cfg.work / sid
    work.mkdir(parents=True, exist_ok=True)
    (work / "video.mp4").write_bytes(b"vv")
    (work / "frames.csv").write_text("frame_id\n0\n")
    (work / "session.json").write_text(json.dumps(
        {"session_id": sid, "duration_seconds": 90.0}))
    (work / "session.rrd").touch()
    (work / "rrd_creation.py").write_text("# script")


def _no_draw(monkeypatch):
    """Force the deterministic 20% draw to 'not sampled' so only the floor
    can sample; make generation instant and observable."""
    monkeypatch.setattr(deliver, "rrd_sampled", lambda *a, **k: False)
    gens = []

    def fake_generate(stage_dir, timeout_s=0):
        gens.append(stage_dir.name)
        (stage_dir / "session.rrd").write_bytes(b"rrd")
    monkeypatch.setattr(deliver.rrdmod, "write_script",
                        lambda d: (d / "rrd_creation.py").write_text("#"))
    monkeypatch.setattr(deliver.rrdmod, "generate", fake_generate)
    return gens


def _stop_after_decision(monkeypatch):
    """Run deliver_session only up to the floor decision: final_gate raises
    a sentinel so nothing is uploaded and the row stays READY with its
    recorded decision — exactly the in-flight shape a sibling lane sees."""
    class Stop(Exception):
        pass

    def boom(stage_dir, sampled):
        raise Stop()
    monkeypatch.setattr(deliver, "final_gate", boom)
    return Stop


def test_floor_counts_sibling_lanes_in_flight_decisions(cfg, ledger,
                                                        monkeypatch):
    """Fresh day, zero delivered: lane 1 (A) is forced by the floor and is
    still in flight (READY, rrd_sampled=1, rrd generating). Lane 2 (B)
    decides next. Pre-fix B read DELIVERED-only -> n=0,s=0 -> forced a
    SECOND rrd. Post-fix B counts A -> n=1,s=1 >= need=ceil(15%*2)=1 ->
    not forced."""
    gens = _no_draw(monkeypatch)
    Stop = _stop_after_decision(monkeypatch)
    _ready(cfg, ledger, SID_A)
    _ready(cfg, ledger, SID_B)
    for sid in (SID_A, SID_B):
        try:
            deliver.deliver_session(cfg, ledger, sid)
        except Stop:
            pass
    assert ledger.get(SID_A)["rrd_sampled"] == 1, "A must be floor-forced"
    assert ledger.get(SID_B)["rrd_sampled"] == 0, \
        "B must see A's in-flight sampled decision and not force again"
    assert gens == [SID_A]


def test_floor_still_forces_when_in_flight_siblings_are_unsampled(
        cfg, ledger, monkeypatch):
    """Other side: an in-flight sibling that was NOT sampled (PACKAGED,
    rrd_sampled=0) raises n without raising s, so the floor forces the
    next lane. Pre-fix the PACKAGED row was invisible (n=0) -> also forced
    — so this pins the denominator, not the direction."""
    gens = _no_draw(monkeypatch)
    Stop = _stop_after_decision(monkeypatch)
    # one DELIVERED-today sampled filler and five in-flight unsampled
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ledger.insert_session(session_id="f-del", game="kamla",
                          operator_email="o", player_email="p",
                          drive_path="kamla/o/p/f-del",
                          drive_ctime="2026-08-14T00:00:00.000Z",
                          md5_video="fd", bytes_=1, state="DELIVERED")
    ledger.update("f-del", rrd_sampled=1, duration_delivered_s=1.0,
                  delivered_at=now)
    for i in range(5):
        sid = f"f-pk{i}"
        ledger.insert_session(session_id=sid, game="kamla",
                              operator_email="o", player_email="p",
                              drive_path=f"kamla/o/p/{sid}",
                              drive_ctime="2026-08-14T00:00:00.000Z",
                              md5_video=f"p{i}", bytes_=1, state="PACKAGED")
    # n=6 (+1 for this one = 7) -> need=ceil(1.05)=2 > s=1 -> force
    _ready(cfg, ledger, SID_B)
    try:
        deliver.deliver_session(cfg, ledger, SID_B)
    except Stop:
        pass
    assert ledger.get(SID_B)["rrd_sampled"] == 1
    assert gens == [SID_B]


def test_floor_decision_is_serialized_by_the_lane_lock(cfg, ledger,
                                                       monkeypatch):
    """Mutation-proof pin: the read+decide+record runs under
    deliver._FLOOR_LOCK (a bare lock that nothing acquires would let two
    lanes interleave between read and record)."""
    _no_draw(monkeypatch)
    Stop = _stop_after_decision(monkeypatch)
    held = []
    real = threading.Lock()

    class Spy:
        def __enter__(self):
            real.acquire(); held.append("in")
        def __exit__(self, *a):
            held.append("out"); real.release()
    monkeypatch.setattr(deliver, "_FLOOR_LOCK", Spy())
    orig_update = ledger.update

    first = []

    def update(sid, **f):
        if "rrd_sampled" in f and sid == SID_A and not first:
            first.append(True)      # the RECORD: later updates are idempotent
            assert real.locked() and held and held[-1] == "in", \
                "rrd_sampled must first be recorded while the floor lock is held"
        return orig_update(sid, **f)
    monkeypatch.setattr(ledger, "update", update)
    _ready(cfg, ledger, SID_A)
    try:
        deliver.deliver_session(cfg, ledger, SID_A)
    except Stop:
        pass
    assert held == ["in", "out"]
    assert ledger.get(SID_A)["rrd_sampled"] == 1


def test_upload_lane_count_is_four_and_download_stays_serial():
    """Pin the ruled knobs: 4 U lanes (the binding wall in hour one), D
    stays serial (F3 createdTime-order intake), media cap 80."""
    assert C.CONT_UPLOAD_WORKERS == 4
    assert C.CONT_DOWNLOAD_WORKERS == 1
    assert C.CONT_MEDIA_CAP_SESSIONS == 80
