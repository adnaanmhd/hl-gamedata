"""Intake game plan — Adnaan 2026-08-20 ruling, implemented by the flip
session (FLIP_EXEC_KICKOFF_PROMPT.md step 8): Kamla first, oldest-first,
until 500 DELIVERED Kamla hours sit on the delivery drive, then Kamla
intake STOPS (in-flight finishes, overshoot accepted) and OW continues.

Before this gate the shipped intake was F4 lagging-game BALANCING
(pipeline/ingest.py lagging_game): pure createdTime FIFO on a fresh
ledger, then OW-first the moment Kamla led by >10% — the opposite of the
ruling. Every test here was proven to FAIL against that unfixed tree in a
scratch copy outside the repo before the gate landed, except the two
"other side of the guard" pins marked as such.
"""
from __future__ import annotations

import pytest

from pipeline import config as C
from pipeline import continuous as cont
from pipeline import ingest
from pipeline.tests.conftest import make_session_entries

K_OLD = "2026-08-14T09-00-00Z_kamla_c_00000000000000a1"
K_NEW = "2026-08-14T11-00-00Z_kamla_c_00000000000000a2"
OW_OLDEST = "2026-08-14T08-00-00Z_outer_wilds_c_00000000000000b1"
OW_NEWEST = "2026-08-14T12-00-00Z_outer_wilds_c_00000000000000b2"


def _seed_queue(cfg, ledger):
    """Four DISCOVERED rows; by createdTime alone the order would be
    OW_OLDEST, K_OLD, K_NEW, OW_NEWEST."""
    entries = (make_session_entries(sid=OW_OLDEST, game="outer_wilds",
                                    md5="m0", ctime="2026-08-14T08:00:00.000Z")
               + make_session_entries(sid=K_OLD, md5="m1",
                                      ctime="2026-08-14T09:00:00.000Z")
               + make_session_entries(sid=K_NEW, md5="m2",
                                      ctime="2026-08-14T11:00:00.000Z")
               + make_session_entries(sid=OW_NEWEST, game="outer_wilds",
                                      md5="m3",
                                      ctime="2026-08-14T12:00:00.000Z"))
    ingest.scan(cfg, ledger, entries=entries)


def _deliver(ledger, sid, game, hours):
    ledger.insert_session(
        session_id=sid, game=game, operator_email="o@x.com",
        player_email="p@x.com", drive_path=f"{game}/o/p/{sid}",
        drive_ctime="2026-08-13T00:00:00.000Z", md5_video=sid[-8:] * 4,
        bytes_=1, state="DELIVERED")
    ledger.update(sid, duration_delivered_s=hours * 3600.0,
                  delivered_at="2026-08-14T00:00:00+00:00")


# ----------------------------------------------------------- priority

def test_kamla_sorts_first_on_a_fresh_ledger(cfg, ledger):
    """Fresh ledger, zero delivered hours: F4 gave pure cross-game FIFO
    (OW_OLDEST first). The ruling is Kamla first, oldest-first."""
    _seed_queue(cfg, ledger)
    assert ingest.next_batch(ledger) == [K_OLD, K_NEW, OW_OLDEST, OW_NEWEST]


def test_kamla_stays_first_while_it_leads(cfg, ledger):
    """F4 flipped priority to OW as soon as Kamla led by >10%; under the
    ruling Kamla keeps the head of the queue until its own stop bar."""
    _seed_queue(cfg, ledger)
    _deliver(ledger, "k-done-1", "kamla", 120.0)       # OW lags by 100%
    assert ingest.lagging_game(ledger) == "outer_wilds"  # F4 would flip...
    assert ingest.priority_game(ledger) == "kamla"       # ...the ruling wins
    assert ingest.next_batch(ledger) == [K_OLD, K_NEW, OW_OLDEST, OW_NEWEST]


def test_priority_none_restores_f4_balancing(cfg, ledger, monkeypatch):
    """Other side of the guard: with the forced priority unset, next_batch
    is exactly the pre-ruling F4 behaviour (pinned in test_ingest too)."""
    monkeypatch.setattr(C, "INTAKE_GAME_PRIORITY", None)
    _seed_queue(cfg, ledger)
    assert ingest.next_batch(ledger) == [OW_OLDEST, K_OLD, K_NEW, OW_NEWEST]
    _deliver(ledger, "k-done-1", "kamla", 120.0)
    assert ingest.priority_game(ledger) == "outer_wilds"
    assert ingest.next_batch(ledger)[0] == OW_OLDEST


# ---------------------------------------------------------------- stop

def test_kamla_intake_closes_at_exactly_the_bar(cfg, ledger):
    """SUM(duration_delivered_s) over DELIVERED kamla rows reaching 500 h
    closes Kamla's NEW intake: no Kamla row is picked, OW keeps flowing in
    createdTime order. A split child counts like any DELIVERED row (the
    ledger's delivered_hours does not filter parent_id — same figure the
    digest prints)."""
    _seed_queue(cfg, ledger)
    _deliver(ledger, "k-root", "kamla", 300.0)
    _deliver(ledger, "k-child", "kamla", 200.0)   # 300 + 200 = 500.0 exactly
    assert ledger.delivered_hours("kamla") == pytest.approx(500.0)
    assert ingest.closed_games(ledger) == frozenset({"kamla"})
    assert ingest.next_batch(ledger) == [OW_OLDEST, OW_NEWEST]


def test_kamla_stays_open_just_below_the_bar(cfg, ledger):
    """Other side of the guard: one second short of 500 h, Kamla is still
    open AND still first."""
    _seed_queue(cfg, ledger)
    _deliver(ledger, "k-root", "kamla", 500.0 - 1 / 3600.0)
    assert ingest.closed_games(ledger) == frozenset()
    assert ingest.next_batch(ledger) == [K_OLD, K_NEW, OW_OLDEST, OW_NEWEST]


def test_stop_counts_only_delivered_rows(cfg, ledger):
    """Hours on non-DELIVERED rows (SPLIT parents, UPLOADED-not-yet-
    DELIVERED, REJECTED) never move the stop: the ruling's measure is
    hours that sit DELIVERED on the drive."""
    _seed_queue(cfg, ledger)
    _deliver(ledger, "k-parent", "kamla", 600.0)
    ledger.set_state("k-parent", "SPLIT", "cut into children")
    assert ingest.closed_games(ledger) == frozenset()
    assert ingest.next_batch(ledger)[0] == K_OLD


def test_stop_is_per_game_and_knob_driven(cfg, ledger, monkeypatch):
    """Mutation-proof pin: the bar is read from INTAKE_GAME_STOP_HOURS at
    call time (not a literal 500), and a game without an entry never
    closes however many hours it has delivered."""
    monkeypatch.setattr(C, "INTAKE_GAME_STOP_HOURS",
                        {"kamla": 1.0, "outer_wilds": 2.0})
    _seed_queue(cfg, ledger)
    _deliver(ledger, "ow-done", "outer_wilds", 1.5)    # below OW's 2.0 bar
    _deliver(ledger, "k-done", "kamla", 1.0)           # at kamla's 1.0 bar
    assert ingest.closed_games(ledger) == frozenset({"kamla"})
    assert ingest.next_batch(ledger) == [OW_OLDEST, OW_NEWEST]
    _deliver(ledger, "ow-done-2", "outer_wilds", 0.5)  # now 2.0 -> closed
    assert ingest.closed_games(ledger) == frozenset({"kamla", "outer_wilds"})
    assert ingest.next_batch(ledger) == []
    monkeypatch.setattr(C, "INTAKE_GAME_STOP_HOURS", {})
    assert ingest.closed_games(ledger) == frozenset()
    assert len(ingest.next_batch(ledger)) == 4


def test_closed_priority_game_falls_back_to_f4(cfg, ledger, monkeypatch):
    """After Kamla closes, the forced priority no longer applies — the
    head of the queue is decided by F4 over the games still open (here
    the only open game). Pins that a closed game is never returned as
    the priority, which would otherwise sort nothing first."""
    monkeypatch.setattr(C, "INTAKE_GAME_STOP_HOURS", {"kamla": 1.0})
    _seed_queue(cfg, ledger)
    _deliver(ledger, "k-done", "kamla", 1.0)
    assert ingest.priority_game(ledger) == "outer_wilds"


# ------------------------------------------------ continuous driver

def test_driver_pick_respects_the_stop(cfg, ledger, monkeypatch):
    """The D lane's fresh pick goes through next_batch: with Kamla closed
    and only Kamla DISCOVERED, the driver picks nothing; add an OW row and
    it picks that one — never a closed-game row."""
    monkeypatch.setattr(C, "INTAKE_GAME_STOP_HOURS", {"kamla": 1.0})
    _seed_queue(cfg, ledger)
    for sid in (OW_OLDEST, OW_NEWEST):
        ledger.set_state(sid, "REJECTED", "out of the way")
    _deliver(ledger, "k-done", "kamla", 1.0)
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    assert drv._pick_download(ledger) is None
    ledger.set_state(OW_NEWEST, "DISCOVERED", "back")
    assert drv._pick_download(ledger) == OW_NEWEST
    assert ledger.get(OW_NEWEST)["state"] == "DOWNLOADING"
    assert ledger.get(K_OLD)["state"] == "DISCOVERED"


def test_in_flight_kamla_still_finishes_after_the_stop(cfg, ledger,
                                                       monkeypatch):
    """RULED: in-flight sessions finish. Two in-flight shapes must survive
    the stop: a DOWNLOADING row (kill-resume) and a DISCOVERED row that
    already holds local bytes inside the media cap (the r-loop 6 carve-out
    — excluding it there would re-open that intake stall). Neither is a
    NEW Kamla pick."""
    monkeypatch.setattr(C, "INTAKE_GAME_STOP_HOURS", {"kamla": 1.0})
    monkeypatch.setattr(C, "CONT_MEDIA_CAP_SESSIONS", 1)
    _seed_queue(cfg, ledger)
    _deliver(ledger, "k-done", "kamla", 1.0)
    assert ingest.closed_games(ledger) == frozenset({"kamla"})
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    # (a) DOWNLOADING kill-resume
    ledger.set_state(K_OLD, "DOWNLOADING", "claimed by D before the stop")
    assert drv._pick_download(ledger) == K_OLD
    drv.own.release(K_OLD)
    ledger.set_state(K_OLD, "INGESTED", "downloaded")
    # (b) DISCOVERED holding partial bytes, cap full (1 of 1 — K_NEW)
    (cfg.work / K_NEW).mkdir(parents=True)
    (cfg.work / K_NEW / "part001.zip").write_bytes(b"partial")
    (cfg.work / K_OLD).mkdir(parents=True)   # K_OLD's media counts too
    (cfg.work / K_OLD / "video.mp4").write_bytes(b"x")
    assert drv._local_count(ledger) >= C.CONT_MEDIA_CAP_SESSIONS
    assert drv._pick_download(ledger) == K_NEW
    assert ledger.get(K_NEW)["state"] == "DOWNLOADING"
