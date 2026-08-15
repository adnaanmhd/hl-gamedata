"""§18 step-5 acceptance: batch + daily messages byte-match the §14
templates on fixture data; pace math unit tests."""
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


def test_sheet_reject_reasons_unfixable_only(cfg, tmp_path):
    """End-to-end through build_sheet_rows: passengers hidden, all-fixable
    reject shows the bare marker."""
    from datetime import timezone as _tz
    from pipeline.ledger import Ledger
    led = Ledger(tmp_path / "l.db")
    now = datetime.now(_tz.utc).isoformat(timespec="seconds")
    rows = [
        ("s1", [_r("INP_MOTION_MISSING"),
                _r("SYN_TS_NOT_PTS", fixable=True)]),   # passenger hidden
        ("s2", [_r("CNT_CHAT_PII", fixable=True)]),     # all-fixable
    ]
    for sid, reasons in rows:
        led.insert_session(
            session_id=f"2026-08-15T00-00-0{sid[-1]}Z_kamla_c_"
                       f"{ord(sid[-1]):016x}",
            game="kamla", operator_email="Op", player_email="p@x.com",
            drive_path="kamla/Op/p@x.com/x", drive_ctime="2026",
            md5_video=sid, bytes_=1, state="DISCOVERED")
        full = f"2026-08-15T00-00-0{sid[-1]}Z_kamla_c_{ord(sid[-1]):016x}"
        led.set_reasons(full, reasons, 3)
        led.set_state(full, "REJECTED")
    sheet = reports.build_sheet_rows(
        led, datetime.now(C.IST),
        bounds=("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00"))
    led.close()
    cell = sheet[0]["top_reject_reasons"]
    assert "no-mouse" in cell and "fix-failed" in cell
    assert "syn-ts-not-pts" not in cell and "chat-pii" not in cell
    assert "×" not in cell


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
