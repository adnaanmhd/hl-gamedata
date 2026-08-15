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


def test_send_marker_guard_and_csv(cfg, ledger, monkeypatch):
    _seed_issue_rows(ledger)
    sent_msgs, sent_docs = [], []
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda cfg, text: sent_msgs.append(text))
    monkeypatch.setattr(runmod.telegram, "send_document",
                        lambda cfg, path, caption="": sent_docs.append(path))
    now = datetime(2026, 8, 15, C.DAILY_REPORT_HOUR_IST, 5, tzinfo=C.IST)
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
    now = datetime(2026, 8, 15, C.DAILY_REPORT_HOUR_IST, 5, tzinfo=C.IST)
    assert runmod.send_folder_issues_if_due(cfg, ledger, now_ist=now) is False
    assert not sent
    assert (cfg.reports_dir / "2026-08-15" / ".issues-sent").exists()


def test_message_send_failure_leaves_marker_unwritten(cfg, ledger,
                                                      monkeypatch):
    _seed_issue_rows(ledger)

    def _boom(cfg, text):
        raise runmod.telegram.TelegramError("down")

    monkeypatch.setattr(runmod.telegram, "send_message", _boom)
    now = datetime(2026, 8, 15, C.DAILY_REPORT_HOUR_IST, 5, tzinfo=C.IST)
    assert runmod.send_folder_issues_if_due(cfg, ledger, now_ist=now) is False
    # retried next tick: marker must NOT exist
    assert not (cfg.reports_dir / "2026-08-15" / ".issues-sent").exists()
