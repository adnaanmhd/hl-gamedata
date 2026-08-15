import json

from pipeline.ledger import Ledger


def _add(led, sid, state="DISCOVERED", **kw):
    args = dict(session_id=sid, game="kamla", operator_email="op@x.com",
                player_email="p@x.com", drive_path=f"kamla/op@x.com/p@x.com/{sid}",
                drive_ctime="2026-08-14T10:00:00.000Z",
                md5_video="m" + sid[-4:], bytes_=10, state=state)
    args.update(kw)
    led.insert_session(**args)


def test_transitions_append_events(ledger):
    _add(ledger, "s1")
    ledger.set_state("s1", "DOWNLOADING")
    ledger.set_state("s1", "INGESTED", "payload=v2")
    ev = ledger.db.execute(
        "SELECT from_state, to_state, detail FROM events WHERE session_id='s1'"
        " ORDER BY id").fetchall()
    assert [(e["from_state"], e["to_state"]) for e in ev] == [
        ("", "DISCOVERED"), ("DISCOVERED", "DOWNLOADING"),
        ("DOWNLOADING", "INGESTED")]
    assert ev[-1]["detail"] == "payload=v2"


def test_update_rejects_unknown_and_state(ledger):
    _add(ledger, "s1")
    try:
        ledger.update("s1", state="READY")
        assert False, "state via update() must be refused"
    except AssertionError:
        pass


def test_delivered_hours_and_rollup(ledger):
    _add(ledger, "a1", state="DELIVERED", game="kamla")
    ledger.update("a1", duration_delivered_s=3600.0,
                  delivered_at="2026-08-14T10:00:00+00:00")
    _add(ledger, "a2", state="DELIVERED", game="outer_wilds",
         player_email="p2@x.com")
    ledger.update("a2", duration_delivered_s=1800.0,
                  delivered_at="2026-08-14T11:00:00+00:00")
    _add(ledger, "a3", state="REJECTED", game="kamla")
    assert ledger.delivered_hours() == 1.5
    assert ledger.delivered_hours("kamla") == 1.0
    roll = ledger.player_rollup()
    by_player = {(r["game"], r["player_email"]): r for r in roll}
    assert by_player[("kamla", "p@x.com")]["delivered"] == 1
    assert by_player[("kamla", "p@x.com")]["rejected"] == 1
    assert by_player[("outer_wilds", "p2@x.com")]["hours"] == 0.5


def test_split_children_not_double_counted_in_rollup(ledger):
    _add(ledger, "parent", state="SPLIT")
    _add(ledger, "parent-p1", state="DELIVERED", parent_id="parent")
    ledger.update("parent-p1", duration_delivered_s=3600.0,
                  delivered_at="2026-08-14T10:00:00+00:00")
    # hours count the child; rollup session counts only parents
    assert ledger.delivered_hours() == 1.0
    roll = ledger.player_rollup()
    assert sum(r["uploaded"] for r in roll) == 1     # the parent row only


def test_supersede_resets_and_archives(ledger, cfg):
    _add(ledger, "s1", state="REJECTED")
    ledger.set_reasons("s1", [{"code": "CNT_SHORT", "blocking": True,
                               "fixable": False, "params": {},
                               "evidence": "50s"}], 3)
    ledger.update("s1", fix_attempts=2, duration_raw_s=50.0)
    d = cfg.dossiers / "s1"
    d.mkdir(parents=True)
    (d / "verdict.json").write_text("{}")
    ledger.supersede("s1", new_md5="newmd5", new_bytes=222,
                     new_ctime="2026-08-15T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    row = ledger.get("s1")
    assert row["state"] == "DISCOVERED"
    assert row["fix_attempts"] == 0
    assert row["md5_video"] == "newmd5"
    assert row["reasons_json"] == "[]"
    assert not (d / "verdict.json").exists()
    hist = list((d / "history").glob("superseded-*/verdict.json"))
    assert len(hist) == 1


def test_incomplete_lifecycle(ledger):
    ledger.incomplete_seen("kamla/o/p/s", ["inputs.jsonl"])
    ledger.incomplete_seen("kamla/o/p/s", ["inputs.jsonl", "metadata.json"])
    rows = ledger.incomplete_list()
    assert len(rows) == 1
    assert json.loads(rows[0]["missing_json"]) == ["inputs.jsonl",
                                                   "metadata.json"]
    ledger.incomplete_resolved("kamla/o/p/s")
    assert ledger.incomplete_list() == []


def test_backup_refreshes_todays_file(ledger, cfg):
    """One file per UTC day, REFRESHED each call (review-r3 #36) — the GCS
    sync must never mirror a stale snapshot; prune keeps newest."""
    p1 = ledger.backup_daily(cfg.backups)
    assert p1 is not None and p1.exists()
    ledger.insert_session(
        session_id="fresh", game="kamla", operator_email="o",
        player_email="p@x.com", drive_path="kamla/o/p/fresh",
        drive_ctime="2026", md5_video="f", bytes_=1, state="DISCOVERED")
    p2 = ledger.backup_daily(cfg.backups)
    assert p2 is not None and p2 == p1        # same file, refreshed
    import sqlite3 as _s
    db = _s.connect(p2)
    n = db.execute("SELECT COUNT(*) FROM sessions "
                   "WHERE session_id='fresh'").fetchone()[0]
    db.close()
    assert n == 1                              # today's copy is current
    assert not list(cfg.backups.glob(".ledger-*.tmp"))
