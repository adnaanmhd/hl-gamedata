"""Folder-issues daily report (Adnaan via d3, 08-15): incomplete uploads +
path-quarantined folders — separate Telegram message + one CSV, marker-guarded
live snapshot. NOT window-based; no cohort/offset logic may leak in."""
import csv
from datetime import datetime

from pipeline import config as C
from pipeline import ingest
from pipeline import reports
from pipeline import run as runmod
from pipeline.tests.conftest import make_session_entries
import pytest


@pytest.fixture(autouse=True)
def _arm_the_batch_driver(monkeypatch):
    """run() now declines when PIPELINE_CONTINUOUS is True (r-loop 5): the
    flag used to be a ONE-WAY interlock that stopped the continuous unit
    when False but never stopped the batch driver when True, so a
    roll-forward could leave both armed and let a batch tick take over
    production. These tests exercise the (dormant) batch driver itself, so
    they arm it explicitly."""
    monkeypatch.setattr(C, "PIPELINE_CONTINUOUS", False)



def _seed_issue_rows(ledger):
    # the two live acceptance cases (d3, 08-15)
    ledger.incomplete_seen(
        "kamla/Rukaiya+Tanzeela/giveusheirloom@gmail.com/"
        "2026-08-15T11-06-45Z_kamla_c_e6e9d0031d37f425", ["video.mp4"])
    ledger.incomplete_seen(
        "kamla/Rukaiya+Tanzeela/harshitrameja3082005@gmail.com/"
        "2026-08-15T12-21-25Z_kamla_c_6cc275ddc3d5a420",
        ["video.mp4", "inputs.jsonl"])
    ledger.insert_session(
        session_id="exerising kamla", game="", operator_email="",
        player_email="",
        drive_path="kamla/Rukaiya+Tanzeela/harshitrameja3082005@gmail.com/"
                   "exerising kamla",
        drive_ctime="", md5_video="", bytes_=0, state="QUARANTINED",
        detail="session folder 'exerising kamla' doesn't match the id "
               "pattern")
    ledger.set_reasons("exerising kamla", [
        {"code": "INT_PATH", "blocking": True, "fixable": False,
         "params": {},
         "evidence": "session folder 'exerising kamla' doesn't match the "
                     "id pattern"}], 3)
    # a NON-path quarantine (download crashed) — must NOT appear on list 2
    ledger.insert_session(
        session_id="2026-08-15T09-00-00Z_kamla_c_0000000000000abc",
        game="kamla", operator_email="Op", player_email="p@x.com",
        drive_path="kamla/Op/p@x.com/"
                   "2026-08-15T09-00-00Z_kamla_c_0000000000000abc",
        drive_ctime="", md5_video="m", bytes_=1, state="QUARANTINED",
        detail="download crashed: KeyError: x")


def test_build_rows_content_and_order(cfg, ledger):
    _seed_issue_rows(ledger)
    rows = reports.build_folder_issues(ledger)
    assert [r["problem"] for r in rows] == \
        ["incomplete_upload", "incomplete_upload", "bad_path"]
    r1, r2, r3 = rows
    assert r1["operator"] == "Rukaiya+Tanzeela"
    assert r1["player_email"] == "giveusheirloom@gmail.com"
    assert r1["folder"] == "2026-08-15T11-06-45Z_kamla_c_e6e9d0031d37f425"
    assert r1["detail"] == "video.mp4"
    assert r2["detail"] == "video.mp4, inputs.jsonl"
    assert r3["folder"] == "exerising kamla"
    assert "doesn't match the id pattern" in r3["detail"]
    # the download-crashed quarantine is absent from both lists
    assert not any("0000000000000abc" in r["drive_path"] for r in rows)


def test_operator_not_derivable_prints_full_path(cfg, ledger):
    ledger.insert_session(
        session_id="stray", game="", operator_email="", player_email="",
        drive_path="kamla/stray", drive_ctime="", md5_video="", bytes_=0,
        state="QUARANTINED", detail="path depth 2 != 4")
    ledger.set_reasons("stray", [
        {"code": "INT_PATH", "blocking": True, "fixable": False,
         "params": {}, "evidence": "path depth 2 != 4"}], 3)
    rows = reports.build_folder_issues(ledger)
    assert rows[0]["operator"] == ""
    assert rows[0]["folder"] == "kamla/stray"     # full path, never a guess
    assert rows[0]["drive_path"] == "kamla/stray"


def test_rrd_only_missing_absent_from_both_lists(cfg, ledger):
    # the exact case the amendment reversed: a folder missing ONLY rrd
    # files is complete per REQUIRED_FILES — no incomplete row, no
    # quarantine, absent from both lists
    entries = make_session_entries(files=list(C.REQUIRED_FILES))
    res = ingest.scan(cfg, ledger, entries=entries)
    assert res.discovered and not res.incomplete and not res.quarantined
    assert reports.build_folder_issues(ledger) == []


def _payment_sent(cfg, day="2026-08-15"):
    """The issues message only follows a SENT payment message (review #5)."""
    d = cfg.reports_dir / day
    d.mkdir(parents=True, exist_ok=True)
    (d / ".sent").touch()


def test_send_marker_guard_and_csv(cfg, ledger, monkeypatch):
    _seed_issue_rows(ledger)
    sent_msgs, sent_docs = [], []
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda cfg, text: sent_msgs.append(text))
    monkeypatch.setattr(runmod.telegram, "send_document",
                        lambda cfg, path, caption="": sent_docs.append(path))
    now = datetime(2026, 8, 15, C.DAILY_REPORT_HOUR_IST, 5, tzinfo=C.IST)
    # before the payment message went out: never send (the heartbeat says
    # "see NEXT message" — the issues message must follow it)
    assert runmod.send_folder_issues_if_due(cfg, ledger, now_ist=now) is False
    assert not sent_msgs
    _payment_sent(cfg)
    assert runmod.send_folder_issues_if_due(cfg, ledger, now_ist=now) is True
    assert len(sent_msgs) == 1 and len(sent_docs) == 1
    assert "incomplete uploads (2):" in sent_msgs[0]
    assert "badly-named / misplaced folders (1):" in sent_msgs[0]
    with open(sent_docs[0], newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == reports.FOLDER_ISSUE_COLS
    assert len(rows) == 3
    # marker-guarded: second call sends nothing
    assert runmod.send_folder_issues_if_due(cfg, ledger, now_ist=now) is False
    assert len(sent_msgs) == 1


def test_before_hour_and_empty_day(cfg, ledger, monkeypatch):
    sent = []
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda cfg, text: sent.append(text))
    monkeypatch.setattr(runmod.telegram, "send_document",
                        lambda cfg, path, caption="": sent.append(path))
    early = datetime(2026, 8, 15, C.DAILY_REPORT_HOUR_IST - 1, 55,
                     tzinfo=C.IST)
    assert runmod.send_folder_issues_if_due(cfg, ledger,
                                            now_ist=early) is False
    # empty snapshot at the due hour: nothing sent, marker written so the
    # empty check doesn't re-run every tick
    _payment_sent(cfg)
    now = datetime(2026, 8, 15, C.DAILY_REPORT_HOUR_IST, 5, tzinfo=C.IST)
    assert runmod.send_folder_issues_if_due(cfg, ledger, now_ist=now) is False
    assert not sent
    assert (cfg.reports_dir / "2026-08-15" / ".issues-sent").exists()


def test_ghost_incomplete_rows_pruned_only_from_healthy_listing(
        cfg, ledger, capsys):
    # Adnaan via d3 (08-15): ghosts inflated the "N incomplete" counters;
    # prune — but ONLY off a successful, non-empty listing for that tree
    ghost = "kamla/Op Name/p@x.com/2026-08-15T08-00-00Z_kamla_c_00000000000000bb"
    ledger.incomplete_seen(ghost, ["video.mp4"])
    ow_row = ("outer_wilds/Op Name/p@x.com/"
              "2026-08-15T08-05-00Z_outer_wilds_c_00000000000000bc")
    ledger.incomplete_seen(ow_row, ["video.mp4"])

    # (a) empty listing prunes NOTHING (the guard)
    ingest.scan(cfg, ledger, entries=[])
    assert len(ledger.incomplete_list()) == 2

    # (b) kamla tree present but the ghost absent -> exactly the ghost is
    # pruned and logged; the outer_wilds row survives (its tree is absent
    # from this listing — same guard)
    entries = make_session_entries()          # a live, complete kamla session
    ingest.scan(cfg, ledger, entries=entries)
    left = [r["drive_path"] for r in ledger.incomplete_list()]
    assert left == [ow_row]
    err = capsys.readouterr().err
    assert "[incomplete-pruned]" in err and ghost in err

    # (c) a still-listed incomplete folder is NOT pruned
    inc_entries = make_session_entries(
        sid="2026-08-15T08-10-00Z_kamla_c_00000000000000bd",
        files=["frames.csv"])                 # listed, still incomplete
    ingest.scan(cfg, ledger, entries=inc_entries)
    paths = [r["drive_path"] for r in ledger.incomplete_list()]
    assert any("bd" in p for p in paths)      # tracked, not pruned


def test_daily_message_heartbeat_counts_folder_issues(cfg, ledger,
                                                      monkeypatch):
    # Adnaan via d3 (08-15): the payment message carries a COUNT heartbeat
    # so a crashed issues job never looks like a clean day
    _seed_issue_rows(ledger)
    sent = []
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda cfg, text: sent.append(text))
    monkeypatch.setattr(runmod.telegram, "send_document",
                        lambda cfg, path, caption="": None)
    now = datetime(2026, 8, 15, C.DAILY_REPORT_HOUR_IST, 5, tzinfo=C.IST)
    assert runmod.send_daily_report_if_due(cfg, ledger, now) is True
    daily = sent[0]
    assert "folder issues: 3 — see next message" in daily


def test_message_send_failure_leaves_marker_unwritten(cfg, ledger,
                                                      monkeypatch):
    _seed_issue_rows(ledger)

    def _boom(cfg, text):
        raise runmod.telegram.TelegramError("down")

    monkeypatch.setattr(runmod.telegram, "send_message", _boom)
    _payment_sent(cfg)
    now = datetime(2026, 8, 15, C.DAILY_REPORT_HOUR_IST, 5, tzinfo=C.IST)
    assert runmod.send_folder_issues_if_due(cfg, ledger, now_ist=now) is False
    # retried next tick: marker must NOT exist
    assert not (cfg.reports_dir / "2026-08-15" / ".issues-sent").exists()


def test_prune_guard_needs_parsed_content_not_bare_dirs(cfg, ledger):
    # review #4: the bare game-dir entry or a stray junk file must NOT
    # satisfy the tree-non-empty guard — only parsed session content does
    ghost = "kamla/Op/p@x.com/2026-08-15T08-20-00Z_kamla_c_00000000000000be"
    ledger.incomplete_seen(ghost, ["video.mp4"])
    ingest.scan(cfg, ledger, entries=[
        {"Path": "kamla", "Name": "kamla", "IsDir": True}])
    assert len(ledger.incomplete_list()) == 1     # bare dir: no prune
    ingest.scan(cfg, ledger, entries=[
        {"Path": "kamla/random_junk.txt", "Name": "random_junk.txt",
         "IsDir": False, "Size": 5, "ModTime": "2026-08-15T10:00:00.000Z"}])
    assert len(ledger.incomplete_list()) == 1     # junk file: no prune
    # real parsed content under the tree -> the ghost prunes
    ingest.scan(cfg, ledger, entries=make_session_entries())
    assert ledger.incomplete_list() == []


def test_heal_clears_stale_int_path_reasons(cfg, ledger):
    # review #3: healed-then-requarantined sessions must not resurface on
    # list 2 with evidence about a name that is already fixed
    sid = "2026-08-15T08-30-00Z_kamla_c_00000000000000bf"
    ledger.insert_session(
        session_id=sid, game="", operator_email="", player_email="",
        drive_path=f"kamla/Op/badplayer/{sid}", drive_ctime="",
        md5_video="", bytes_=0, state="QUARANTINED",
        detail="player folder 'badplayer' is not an email")
    ledger.set_reasons(sid, [
        {"code": "INT_PATH", "blocking": True, "fixable": False,
         "params": {},
         "evidence": "player folder 'badplayer' is not an email"}], 3)
    ingest.scan(cfg, ledger,
                entries=make_session_entries(sid=sid, player="ok@x.com"))
    assert ledger.get(sid)["state"] == "DISCOVERED"
    # later trouble re-quarantines for an unrelated reason
    ledger.set_state(sid, "QUARANTINED", "download crashed: KeyError: x")
    assert reports.build_folder_issues(ledger) == []


def test_overlong_message_degrades_to_counts(cfg, ledger):
    # review #2: an over-4096 message fails EVERY send with the marker
    # unwritten — degrade to counts + csv pointer instead
    op = "Very Long Operator Name With Many Words"
    for i in range(12):
        ledger.incomplete_seen(
            f"kamla/{op}/some.player.email{i:02d}@gmail.com/"
            f"2026-08-15T0{i % 10}-00-00Z_kamla_c_00000000000000{i:02x}",
            ["video.mp4", "inputs.jsonl", "metadata.json"])
    for i in range(12):
        path = (f"kamla/{op}/some.player.email{i:02d}@gmail.com/extra/"
                f"way/too/deep/2026-08-15T0{i % 10}-10-00Z_kamla_c_"
                f"00000000000001{i:02x}")
        sid = f"stray-{i}"
        ledger.insert_session(
            session_id=sid, game="", operator_email="", player_email="",
            drive_path=path, drive_ctime="", md5_video="", bytes_=0,
            state="QUARANTINED", detail="path depth 9 != 4")
        ledger.set_reasons(sid, [
            {"code": "INT_PATH", "blocking": True, "fixable": False,
             "params": {}, "evidence": "path depth 9 != 4 (want "
                                       "game/operator/player/session)"}], 3)
    rows = reports.build_folder_issues(ledger)
    msg = reports.build_folder_issues_message(
        rows, datetime(2026, 8, 15, 14, 0, tzinfo=C.IST))
    assert len(msg) <= 3500
    assert "incomplete uploads: 12" in msg
    assert "misplaced folders: 12" in msg
    assert "csv" in msg


def test_end_of_run_wiring_calls_issues_after_payment(cfg, monkeypatch):
    # review #1: the OVERLAP end-of-run (production's only guaranteed
    # daily fire on idle ticks) must call both sends, payment first —
    # the issues call was wired only into lockstep
    calls = []
    monkeypatch.setattr(runmod, "send_daily_report_if_due",
                        lambda cfg_, led, now_ist=None:
                        calls.append("payment") or True)
    monkeypatch.setattr(runmod, "send_folder_issues_if_due",
                        lambda cfg_, led, now_ist=None:
                        calls.append("issues") or True)
    monkeypatch.setattr(runmod.ingest, "scan",
                        lambda cfg_, led: ingest.ScanResult())
    for overlap in (False, True):
        calls.clear()
        monkeypatch.setattr(C, "PIPELINE_OVERLAP", overlap)
        assert runmod.run(cfg, send_telegram=True) == 0
        assert calls == ["payment", "issues"], (overlap, calls)
