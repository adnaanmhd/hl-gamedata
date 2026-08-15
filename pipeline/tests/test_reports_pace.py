"""§18 step-5 acceptance: batch + daily messages byte-match the §14
templates on fixture data; pace math unit tests."""
import json
from datetime import date, datetime

from pipeline import config as C
from pipeline import pace, reports
from pipeline.pace import PaceStatus
from pipeline.reports import BatchStats, DailyStats

IST = C.IST

BATCH_TEMPLATE = """🎮 batch #23 ✓ 16:42 · 41m
sessions: 8/10 delivered (3 auto-fixed) · 2 rejected (no mouse motion · <70s)
hours: +2.9 → Kamla 121.6/500 · OW 98.3/500 (Σ 219.9/1000)
queue: 34 sessions pending · 6 incomplete
⚠️ PACE need 111 h/day · trailing 55 → ~90 min/player/day required"""

DAILY_TEMPLATE = """💰 daily — Aug 17
delivered today +41.2 h from 96 sessions · 11 sessions rejected
totals: Kamla 121.6/500 · OW 98.3/500 · Σ 219.9/1000
collected: Kamla 156/600 · OW 131/600 · 7 days left
⚠️ pace: need 111 h/day (trailing 55) — projected finish Aug 31
rejects: no-mouse · <70s · notifications · wrong-game · dup
integrity: 1 cross-player duplicate (kept earlier upload)
📎 payment sheet attached"""


def _alarm_pace():
    p = PaceStatus()
    p.need_total = 111.0
    p.trailing_24h = 55.0
    p.min_per_player_day = 90.0
    p.projected_finish = date(2026, 8, 31)
    p.alarm = True
    return p


def test_batch_message_byte_matches_template():
    b = BatchStats(
        batch_no=23,
        finished_ist=datetime(2026, 8, 17, 16, 42, tzinfo=IST),
        duration_min=41, delivered=8, total=10, auto_fixed=3, rejected=2,
        reject_labels=["no mouse motion", "<70s"], hours_delta=2.9,
        hours_kamla=121.6, hours_ow=98.3, pending=34, incomplete=6)
    assert reports.build_batch_message(b, _alarm_pace()) == BATCH_TEMPLATE


def test_batch_message_healthy_is_four_lines():
    b = BatchStats(
        batch_no=5, finished_ist=datetime(2026, 8, 15, 9, 0, tzinfo=IST),
        duration_min=12, delivered=10, total=10, auto_fixed=0, rejected=0,
        hours_delta=3.4, hours_kamla=10.0, hours_ow=12.0, pending=0,
        incomplete=0)
    ok_pace = PaceStatus()
    ok_pace.alarm = False
    msg = reports.build_batch_message(b, ok_pace)
    assert len(msg.splitlines()) == 4
    assert "auto-fixed" not in msg and "(" not in msg.splitlines()[1]


def test_daily_message_byte_matches_template():
    d = DailyStats(
        day_ist=datetime(2026, 8, 17, 14, 0, tzinfo=IST),
        delivered_hours_today=41.2, delivered_sessions_today=96,
        rejected_sessions_today=11, hours_kamla=121.6, hours_ow=98.3,
        collected_kamla=156.0, collected_ow=131.0, days_left=7,
        reject_counts=[("no-mouse", 4), ("<70s", 3), ("notifications", 2),
                       ("wrong-game", 1), ("dup", 1)],
        integrity_lines=["1 cross-player duplicate (kept earlier upload)"])
    assert reports.build_daily_message(d, _alarm_pace()) == DAILY_TEMPLATE


def test_daily_message_quiet_day_omits_conditionals():
    d = DailyStats(
        day_ist=datetime(2026, 8, 18, 14, 0, tzinfo=IST),
        delivered_hours_today=50.0, delivered_sessions_today=100,
        rejected_sessions_today=0, hours_kamla=200.0, hours_ow=210.0,
        collected_kamla=250.0, collected_ow=260.0, days_left=6)
    quiet = PaceStatus()
    quiet.alarm = False
    msg = reports.build_daily_message(d, quiet)
    assert "rejects:" not in msg and "integrity:" not in msg \
        and "pace:" not in msg
    assert msg.endswith("📎 payment sheet attached")


def test_reason_labels():
    assert reports.reason_label("INP_MOTION_MISSING") == "no mouse motion"
    assert reports.reason_label("INP_MOTION_MISSING", daily=True) == "no-mouse"
    assert reports.reason_label("SOME_NEW_CODE") == "some-new-code"
    # unfixable codes must never fall through to the raw-code fallback
    assert reports.reason_label("QA_FAIL_UNMAPPED", daily=True) == \
        "qa-unmapped"
    assert reports.reason_label("INT_PATH", daily=True) == "bad-path"


# ------------------------------- unfixable-only reject labels (08-15)

def _r(code, blocking=True, fixable=False):
    return {"code": code, "blocking": blocking, "fixable": fixable}


def test_session_labels_filter_by_stored_fixable_field():
    """The three conditionally-fixable codes vary PER INSTANCE — the
    stored field decides, never a code-name list."""
    reasons = [_r("CNT_MID_NONGAMEPLAY", fixable=True),    # passenger
               _r("INP_MOTION_MISSING", fixable=False),
               _r("SYN_TS_NOT_PTS", fixable=True),         # passenger
               _r("CNT_DROPS", blocking=False)]            # advisory
    assert reports.session_reject_labels(reasons, daily=True) == ["no-mouse"]
    # same code, unfixable instance -> surfaces
    reasons2 = [_r("CNT_MID_NONGAMEPLAY", fixable=False)]
    assert reports.session_reject_labels(reasons2, daily=True) == \
        ["non-gameplay"]


def test_session_labels_all_fixable_emits_fix_failed_marker():
    reasons = [_r("SYN_TS_NOT_PTS", fixable=True),
               _r("CNT_NOTIF_EDGE", fixable=True)]
    assert reports.session_reject_labels(reasons) == ["fix-failed"]
    assert reports.session_reject_labels([]) == ["fix-failed"]


def test_session_labels_dedupe_shared_nicknames():
    reasons = [_r("CNT_NOTIF_MID"), _r("CNT_NOTIF_EDGE")]
    assert reports.session_reject_labels(reasons, daily=True) == \
        ["notifications"]


def test_ordered_labels_exhaustive_count_desc_tie_alpha_no_counts():
    per_session = [["no-mouse"], ["no-mouse"], ["<70s", "no-mouse"],
                   ["dup"], ["chat-pii"], ["tamper"], ["black-frozen"]]
    out = reports.ordered_reject_labels(per_session)
    # no [:3] cap: every distinct label present
    assert out[0] == "no-mouse"                    # count 3
    assert out[1] == "<70s"                        # count 1, alpha ties…
    assert out == ["no-mouse", "<70s", "black-frozen", "chat-pii", "dup",
                   "tamper"]
    assert not any("×" in lbl for lbl in out)


def test_sheet_cohort_attributes_late_outcome_to_upload_window(tmp_path):
    """v4 cohort rule: a session UPLOADED in window 1 but REJECTED much
    later (and with updated_at bumped later still) appears in window 1's
    sheet and in no other — outcome timing and timestamp bumps are
    irrelevant by construction."""
    from pipeline.ledger import Ledger
    led = Ledger(tmp_path / "l.db")
    sid = "2026-08-15T00-00-00Z_kamla_c_00000000000000dd"
    led.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path="kamla/Op/p@x.com/x",
        drive_ctime="2026-08-15T00:01:00.000Z", md5_video="m", bytes_=1,
        state="DISCOVERED")
    led.set_reasons(sid, [_r("INP_MOTION_MISSING")], 3)
    led.set_state(sid, "REJECTED")
    # outcome recorded far later + dossier write bumps updated_at
    led.db.execute("UPDATE sessions SET updated_at='2099-01-01T00:00:00' "
                   "WHERE session_id=?", (sid,))
    led.db.commit()
    upload_window = reports.build_sheet_rows(
        led, datetime.now(C.IST),
        bounds=("2026-08-15T00:00:00+00:00", "2026-08-16T00:00:00+00:00"))
    assert upload_window[0]["kamla_rejection_reasons"] == "no-mouse"
    later_window = reports.build_sheet_rows(
        led, datetime.now(C.IST),
        bounds=("2026-08-16T00:00:00+00:00", "2026-08-17T00:00:00+00:00"))
    assert later_window == []                 # never re-counted elsewhere
    led.close()


def test_sheet_reject_reasons_unfixable_only_and_game_bucketed(cfg,
                                                               tmp_path):
    """End-to-end through build_sheet_rows: passengers hidden, all-fixable
    reject shows the bare marker, and an OW rejection lands in the OW
    column (synthetic — no real OW data exists yet)."""
    from pipeline.ledger import Ledger
    led = Ledger(tmp_path / "l.db")
    rows = [
        ("s1", "kamla", [_r("INP_MOTION_MISSING"),
                         _r("SYN_TS_NOT_PTS", fixable=True)]),  # passenger
        ("s2", "kamla", [_r("CNT_CHAT_PII", fixable=True)]),    # all-fixable
        ("s3", "outer_wilds", [_r("CNT_SHORT")]),               # OW bucket
    ]
    for sid, game, reasons in rows:
        full = f"2026-08-15T00-00-0{sid[-1]}Z_{game}_c_{ord(sid[-1]):016x}"
        led.insert_session(
            session_id=full, game=game, operator_email="Op",
            player_email="p@x.com", drive_path=f"{game}/Op/p@x.com/x",
            drive_ctime="2026-08-15T00:01:00.000Z",
            md5_video=sid, bytes_=1, state="DISCOVERED")
        led.set_reasons(full, reasons, 3)
        led.set_state(full, "REJECTED")
    sheet = reports.build_sheet_rows(
        led, datetime.now(C.IST),
        bounds=("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00"))
    led.close()
    assert len(sheet) == 1                        # one (operator, player)
    kcell = sheet[0]["kamla_rejection_reasons"]
    ocell = sheet[0]["ow_rejection_reasons"]
    assert "no-mouse" in kcell and "fix-failed" in kcell
    assert "syn-ts-not-pts" not in kcell and "chat-pii" not in kcell
    assert "×" not in kcell
    assert ocell == "<70s"                        # OW reject, OW column


def test_sheet_uploaded_hours_parents_only_on_drive_ctime(tmp_path):
    """*_hrs_uploaded sums the PROBED video duration of PARENT sessions
    windowed on the real Drive upload time — children would double-count
    the parent's footage, and created_at is discovery time (d3 gotchas)."""
    from pipeline.ledger import Ledger
    led = Ledger(tmp_path / "l.db")
    par = "2026-08-15T01-00-00Z_kamla_c_00000000000000f2"
    led.insert_session(
        session_id=par, game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path="kamla/Op/p@x.com/x",
        drive_ctime="2026-08-15T01:02:03.413Z",     # RFC3339 millis Z
        md5_video="m", bytes_=1, state="DISCOVERED")
    led.update(par, duration_raw_s=3600.0)
    led.insert_session(
        session_id=f"{par}-p1", game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path="kamla/Op/p@x.com/x",
        drive_ctime="2026-08-15T01:02:03.413Z", md5_video="", bytes_=0,
        state="INGESTED", parent_id=par)
    led.update(f"{par}-p1", duration_raw_s=1800.0)  # child: must NOT count
    # blank drive_ctime parent: falls back to created_at, still counted
    par2 = "2026-08-15T02-00-00Z_kamla_c_00000000000000f3"
    led.insert_session(
        session_id=par2, game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path="kamla/Op/p@x.com/y",
        drive_ctime="", md5_video="m2", bytes_=1, state="DISCOVERED")
    led.update(par2, duration_raw_s=1800.0)
    sheet = reports.build_sheet_rows(
        led, datetime.now(C.IST),
        bounds=("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00"))
    led.close()
    assert len(sheet) == 1
    assert sheet[0]["kamla_hrs_uploaded"] == 1.5   # 1.0 parent + 0.5 blank
    assert sheet[0]["ow_hrs_uploaded"] == 0.0
    # cohort pending: both DISCOVERED roots (1.0 + 0.5) + INGESTED child
    # (0.5) are still in flight
    assert sheet[0]["kamla_pending_hrs"] == 2.0


def test_sheet_totals_sum_rounded_parts_across_both_games(tmp_path):
    """v3 totals: a both-games player's totals equal the sum of the
    ROUNDED game columns (the visible numbers must add up on a payment
    document). Synthetic — no real OW data exists yet."""
    from pipeline.ledger import Ledger
    led = Ledger(tmp_path / "l.db")
    specs = [("kamla", "f5", 445.0, 300.0),        # 0.1236h up, 0.0833h acc
             ("outer_wilds", "f6", 470.0, 500.0)]  # 0.1306h up, 0.1389h acc
    for game, tag, raw_s, del_s in specs:
        sid = f"2026-08-15T04-00-0{tag[-1]}Z_{game}_c_{ord(tag[-1]):016x}"
        led.insert_session(
            session_id=sid, game=game, operator_email="Op",
            player_email="both@x.com", drive_path=f"{game}/Op/both@x.com/x",
            drive_ctime="2026-08-15T04:01:00.000Z", md5_video=tag,
            bytes_=1, state="DISCOVERED")
        led.update(sid, duration_raw_s=raw_s)
        for st in ("INGESTED", "VALIDATING", "READY", "PACKAGED",
                   "UPLOADED"):
            led.set_state(sid, st)
        led.update(sid, duration_delivered_s=del_s,
                   delivered_at="2026-08-15T05:00:00+00:00")
        led.set_state(sid, "DELIVERED")
    sheet = reports.build_sheet_rows(
        led, datetime.now(C.IST),
        bounds=("2026-08-15T00:00:00+00:00", "2026-08-16T00:00:00+00:00"))
    led.close()
    assert len(sheet) == 1
    r = sheet[0]
    # rounded parts: 0.12 + 0.13 = 0.25 (raw sum would round to 0.25 too,
    # but the CONTRACT is parts-then-total; assert the exact identity)
    assert r["total_uploaded_hours"] == round(
        r["kamla_hrs_uploaded"] + r["ow_hrs_uploaded"], 2)
    assert r["total_delivered_hours"] == round(
        r["kamla_accepted_hrs"] + r["ow_accepted_hrs"], 2)
    assert r["kamla_hrs_uploaded"] == 0.12 and r["ow_hrs_uploaded"] == 0.13
    assert r["total_uploaded_hours"] == 0.25
    assert r["kamla_accepted_hrs"] == 0.08 and r["ow_accepted_hrs"] == 0.14
    assert r["total_delivered_hours"] == 0.22


def test_sheet_cohort_walks_depth2_tree_split_carries_nothing(tmp_path):
    """v4 trap: split trees reach depth 2 in the live ledger — a one-level
    join drops the grandchildren's hours. Root SPLIT -> p1 SPLIT ->
    p1-p1 DELIVERED; p2 REJECTED; p3 VALIDATING (pending). SPLIT nodes
    contribute nothing themselves."""
    from pipeline.ledger import Ledger
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-15T06-00-00Z_kamla_c_00000000000000f7"

    def put(sid, state, parent=None, raw=None, delivered=None,
            reasons=None):
        led.insert_session(
            session_id=sid, game="kamla", operator_email="Op",
            player_email="deep@x.com", drive_path="kamla/Op/deep@x.com/x",
            drive_ctime="2026-08-15T06:01:00.000Z", md5_video=sid[-4:],
            bytes_=1, state="DISCOVERED", parent_id=parent)
        if raw:
            led.update(sid, duration_raw_s=raw)
        if reasons is not None:
            led.set_reasons(sid, reasons, 3)
        if delivered:
            led.update(sid, duration_delivered_s=delivered,
                       delivered_at="2026-08-15T09:00:00+00:00")
        led.set_state(sid, state)

    put(root, "SPLIT", raw=7200.0)                     # contributes nothing
    put(f"{root}-p1", "SPLIT", parent=root, raw=3600.0)   # nothing either
    put(f"{root}-p1-p1", "DELIVERED", parent=f"{root}-p1",
        raw=1500.0, delivered=1440.0)                  # depth 2!
    put(f"{root}-p2", "REJECTED", parent=root,
        reasons=[{"code": "CNT_BLACK_FROZEN", "blocking": True,
                  "fixable": False, "params": {}, "evidence": "e"}])
    put(f"{root}-p3", "VALIDATING", parent=root, raw=1080.0)
    sheet = reports.build_sheet_rows(
        led, datetime.now(C.IST),
        bounds=("2026-08-15T00:00:00+00:00", "2026-08-16T00:00:00+00:00"))
    led.close()
    assert len(sheet) == 1
    r = sheet[0]
    assert r["kamla_hrs_uploaded"] == 2.0     # root raw only
    assert r["kamla_accepted_hrs"] == 0.4     # depth-2 grandchild found
    assert r["kamla_pending_hrs"] == 0.3      # VALIDATING child only
    assert r["kamla_rejection_reasons"] == "black-frozen"
    assert r["total_delivered_hours"] == 0.4


def test_sheet_suppresses_no_activity_rows(tmp_path):
    from pipeline.ledger import Ledger
    led = Ledger(tmp_path / "l.db")
    sid = "2026-08-15T03-00-00Z_kamla_c_00000000000000f4"
    led.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email="idle@x.com", drive_path="kamla/Op/idle@x.com/x",
        drive_ctime="2026-01-01T00:00:00.000Z",     # outside window
        md5_video="m", bytes_=1, state="DISCOVERED")
    led.update(sid, duration_raw_s=3600.0)
    sheet = reports.build_sheet_rows(
        led, datetime.now(C.IST),
        bounds=("2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00"))
    led.close()
    assert sheet == []                             # suppressed


def test_reject_detail_three_renderings(cfg, tmp_path):
    """The MD Reject detail section: (i) mixed fixable/unfixable shows the
    unfixable code, (ii) all-fixable shows fix-failed, (iii) unparseable
    reasons shows unreadable-reasons — never a false fix-failed."""
    from pipeline.ledger import Ledger
    led = Ledger(tmp_path / "l.db")
    cases = [
        ("a1", json.dumps([_r("CNT_BLACK_FROZEN"),
                           _r("CNT_MID_NONGAMEPLAY", fixable=True)])),
        ("a2", json.dumps([_r("SYN_TS_NOT_PTS", fixable=True)])),
        ("a3", "{corrupt!!"),
    ]
    for tag, rj in cases:
        sid = f"2026-08-15T00-00-0{tag[-1]}Z_kamla_c_{ord(tag[-1]):016x}"
        led.insert_session(
            session_id=sid, game="kamla", operator_email="Op",
            player_email="p@x.com", drive_path="kamla/Op/p@x.com/x",
            drive_ctime="2026-08-15T00:01:00.000Z", md5_video=tag,
            bytes_=1, state="DISCOVERED")
        led.set_state(sid, "REJECTED")
        led.db.execute("UPDATE sessions SET reasons_json=? "
                       "WHERE session_id=?", (rj, sid))
        led.db.commit()
    _csv, md_path = reports.write_payment_sheet(
        cfg, led, datetime.now(C.IST),
        bounds=("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00"))
    led.close()
    text = md_path.read_text()
    lines = {ln.split("`")[1][-2:]: ln for ln in text.splitlines()
             if ln.startswith("- `")}
    assert "CNT_BLACK_FROZEN" in lines["31"]
    assert "CNT_MID_NONGAMEPLAY" not in lines["31"]
    assert "fix-failed" in lines["32"]
    assert "unreadable-reasons" in lines["33"]
    assert "fix-failed" not in lines["33"]


# ------------------------------------------------------------------ pace

def test_pace_math_and_alarm():
    now = datetime(2026, 8, 17, 23, 59, tzinfo=IST)   # exactly 7 days left
    p = pace.compute({"kamla": 150.0, "outer_wilds": 150.0}, 40.0, now,
                     n_players=150)
    assert abs(p.days_left - 7.0) < 0.01
    assert abs(p.need_per_game["kamla"] - 50.0) < 0.1
    assert abs(p.need_total - 100.0) < 0.2
    assert p.alarm                                    # 100 > 40*1.15
    assert p.projected_finish and \
        p.projected_finish > C.DEADLINE_IST.date()
    assert abs(p.min_per_player_day - 40.0) < 0.2     # 100h*60/150


def test_pace_no_alarm_when_on_track():
    now = datetime(2026, 8, 17, 23, 59, tzinfo=IST)
    p = pace.compute({"kamla": 400.0, "outer_wilds": 400.0}, 50.0, now)
    assert abs(p.need_total - 200.0 / 7.0) < 0.2      # ~28.6 h/day
    assert not p.alarm
    assert p.projected_finish <= C.DEADLINE_IST.date()


def test_pace_zero_trailing_alarms():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=IST)
    p = pace.compute({"kamla": 0.0, "outer_wilds": 0.0}, 0.0, now)
    assert p.alarm and p.projected_finish is None


def test_pace_done_never_alarms():
    now = datetime(2026, 8, 20, 12, 0, tzinfo=IST)
    p = pace.compute({"kamla": 500.0, "outer_wilds": 500.0}, 0.0, now)
    assert not p.alarm and p.need_total == 0.0
