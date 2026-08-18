"""r-loop 5 regression tests."""
from __future__ import annotations

import time

from pipeline import config as C
from pipeline import continuous as cont
from pipeline import ingest
from pipeline import run as runmod


def test_unclean_drain_returns_when_not_owning_the_process(cfg, monkeypatch):
    """r-loop 5: run_continuous is a LIBRARY call. When the drain grace
    expires with a lane still alive, the r-loop-4 fast path left by
    os._exit(0) — which, in-process, terminates the pytest interpreter
    with status 0: the suite stops mid-run and the shell reads success.
    Reproduced before this fix: pytest collected 2 tests, ran one, never
    ran a guaranteed failure, printed no summary and exited 0.

    install_signals is the ownership flag (only a process owner installs
    handlers; every test passes False), so a non-owning caller must
    RETURN normally instead. Before the fix this test could not fail —
    it killed the interpreter and took the rest of the suite with it.
    """
    monkeypatch.setattr(C, "CONT_DRAIN_GRACE_S", 0.5)

    def slow_list(_cfg):
        time.sleep(4)          # outlives the drain grace
        return []
    monkeypatch.setattr(ingest, "list_drive", slow_list)

    rc = cont.run_continuous(cfg, until_idle=True, send_telegram=False,
                             install_signals=False, max_wall_s=1.5)
    # reaching this line at all is the assertion: the interpreter lived
    assert rc == 0


# ------------------------------------ BLOCKER: DISCOVERED media is invisible

def _seed_disc(ledger, sid, *, state="DISCOVERED"):
    ledger.insert_session(session_id=sid, game="kamla",
                          operator_email="op@x.com", player_email="p@x.com",
                          drive_path="kamla/op@x.com/p@x.com/" + sid,
                          drive_ctime="2026-08-14T10:00:00.000Z",
                          md5_video="a" * 32, bytes_=10, state=state)


def _age_discovered_event(ledger, sid, hours):
    """Backdate the DISCOVERED event so the reclaim/stuck age is measured
    from the audit, not from updated_at."""
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc)
          - timedelta(hours=hours)).isoformat(timespec="seconds")
    ledger.db.execute("DELETE FROM events WHERE session_id=?", (sid,))
    ledger.db.execute(
        "INSERT INTO events(session_id, from_state, to_state, ts, detail) "
        "VALUES(?,?,?,?,?)", (sid, "DOWNLOADING", "DISCOVERED", ts, "fail"))
    ledger.db.commit()


def test_local_count_sees_media_held_by_a_DISCOVERED_row(cfg, ledger):
    """r-loop 5 blocker: _download_one returns a row to DISCOVERED on every
    transient/zip_incomplete failure while ingest.download leaves what
    rclone already transferred in work/<sid>. LOCAL_STATES excludes
    DISCOVERED, so gigabytes were invisible to the ~40-session cap: the
    disk filled, _pick_download refused on the F7 low-water check, and cap
    pressure stayed silent so nothing named the cause. Same class as the
    QUARANTINED leak fixed in r-loop 3/4."""
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    _seed_disc(ledger, "s-nomedia")
    assert drv._local_count(ledger) == 0        # no work dir -> not counted

    _seed_disc(ledger, "s-media")
    (cfg.work / "s-media").mkdir(parents=True)
    assert drv._local_count(ledger) == 1

    # sid and sid-analysis are ONE session, not two
    (cfg.work / "s-media-analysis").mkdir(parents=True)
    assert drv._local_count(ledger) == 1


def test_sweep_reclaims_stale_DISCOVERED_media_but_spares_fresh(cfg, ledger):
    """The cap alone would stop intake with no way back (the r-loop-3
    QUARANTINED lesson), so aged media must be reclaimable. Age comes from
    the events audit: the 5-min retry bounces DISCOVERED->DOWNLOADING->
    DISCOVERED forever, re-stamping updated_at every time."""
    for sid, age in (("s-old", C.CONT_DISCOVERED_RECLAIM_H + 1),
                     ("s-new", 1)):
        _seed_disc(ledger, sid)
        (cfg.work / sid).mkdir(parents=True)
        (cfg.work / sid / "video.mp4").write_bytes(b"x")
        _age_discovered_event(ledger, sid, age)

    runmod._sweep_terminal_work(cfg, ledger)

    assert not (cfg.work / "s-old").exists(), \
        "stale DISCOVERED media must be reclaimed (rclone re-downloads)"
    assert (cfg.work / "s-new").exists(), \
        "a download failing for an hour must keep its partial transfer"
    # the row itself survives — only the bytes are reclaimed
    assert ledger.get("s-old")["state"] == "DISCOVERED"


def test_stuck_list_names_a_DISCOVERED_row_that_holds_media(cfg, ledger):
    """_stuck_lines excludes DISCOVERED by design (it is the unbounded
    'seen on Drive' population), which left the ONE failure that fills the
    disk with no ops surface at all — and at the flip the 3h digest is the
    only surface, since CONT_DAILY_REPORTS ships False."""
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    _seed_disc(ledger, "s-quiet")               # no media: stays invisible
    _age_discovered_event(ledger, "s-quiet", C.CONT_STUCK_H + 5)

    _seed_disc(ledger, "s-loud")
    (cfg.work / "s-loud").mkdir(parents=True)
    _age_discovered_event(ledger, "s-loud", C.CONT_STUCK_H + 5)

    lines, _n = drv._stuck_lines(ledger)
    text = " ".join(lines)
    assert "s-loud" in text
    assert "s-quiet" not in text
