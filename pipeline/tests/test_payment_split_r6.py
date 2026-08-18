"""The RULED uploaded/accepted mark split (Adnaan, 2026-08-18).

`uploaded_reported_at` used to do two jobs — "uploaded hours counted,
never again" AND "this root is finished, never look again". The second is
what lost the money: `build_sheet_rows` stamps a root as soon as it is
`r_countable` (raw probed, right after download), so a root whose split
children were still validating recorded `accepted_hrs = 0` and was then
invisible to BOTH the in-window and late-arrival guards forever. Every
hour those children later delivered to the client was paid to nobody.
Measured on the 08-18 rebuild dump: 135 of 309 countable roots (43.7%)
were countable-but-unsettled, holding 16.84 h in unsettled nodes.

The fix splits the mark in two. These tests pin both halves AND the two
invariants the obvious fixes broke (see the same-named tests in
test_reports_pace.py, which must keep passing unchanged).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

from pipeline import config as C
from pipeline import reports
from pipeline.ledger import Ledger

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    """Same loader the other flip-tool tests use (test_recal_tools_r3)."""
    spec = importlib.util.spec_from_file_location(
        f"_tool_{name}", REPO / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

W1 = ("2026-08-14T06:45:22+00:00", "2026-08-15T06:45:22+00:00")
W2 = ("2026-08-15T06:45:22+00:00", "2026-08-16T06:45:22+00:00")
W3 = ("2026-08-16T06:45:22+00:00", "2026-08-17T06:45:22+00:00")

UNFIXABLE = [{"code": "CNT_BLACK_FROZEN", "blocking": True,
              "fixable": False, "params": {}, "evidence": "e"}]


def _put(led, sid, *, state, parent=None, ctime="2026-08-14T09:00:00.000Z",
         raw=None, delivered=None, reasons=None, player="split@x.com",
         game="kamla"):
    led.insert_session(
        session_id=sid, game=game, operator_email="Op",
        player_email=player, drive_path=f"{game}/Op/{player}/x",
        drive_ctime=ctime, md5_video=sid[-6:], bytes_=1,
        state="DISCOVERED", parent_id=parent)
    if raw is not None:
        led.update(sid, duration_raw_s=raw)
    if reasons is not None:
        led.set_reasons(sid, reasons, 3)
    if delivered is not None:
        led.update(sid, duration_delivered_s=delivered,
                   delivered_at="2026-08-15T09:00:00+00:00")
    if state != "DISCOVERED":
        led.set_state(sid, state)


def _sheet(led, bounds):
    """Generate + stamp BOTH marks, exactly as the send site wires them."""
    counted: list[str] = []
    accepted: list[str] = []
    rows = reports.build_sheet_rows(
        led, datetime.now(C.IST), bounds=bounds,
        counted_out=counted, accepted_out=accepted)
    reports.mark_uploads_reported(led, *bounds, sids=counted)
    reports.mark_accepted_reported(led, accepted)
    return rows


def _row(rows, player="split@x.com"):
    hit = [r for r in rows if r["player_email"] == player]
    assert len(hit) <= 1, f"duplicate rows for {player}: {rows}"
    return hit[0] if hit else None


# ------------------------------------------------------------ THE BUG

def test_stranded_hours_reach_a_later_sheet_exactly_once(tmp_path):
    """THE ruled bug. Root probed (countable) and SPLIT while its children
    are still validating -> sheet 1 counts uploaded hours and stamps. The
    children then deliver. Before the fix the root was sealed out of every
    later sheet and those hours were paid to nobody; now they land on the
    next sheet, once, with uploaded 0."""
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_00000000000000a1"
    _put(led, root, state="SPLIT", raw=3600.0)
    _put(led, f"{root}-p1", state="VALIDATING", parent=root, raw=1800.0)
    _put(led, f"{root}-p2", state="VALIDATING", parent=root, raw=1700.0)

    s1 = _row(_sheet(led, W1))
    assert s1 is not None
    assert s1["kamla_hrs_uploaded"] == 1.0     # uploaded hours counted...
    assert s1["kamla_accepted_hrs"] == 0.0     # ...accepted not yet known
    assert led.get(root)["uploaded_reported_at"]
    assert led.get(root)["accepted_reported_at"] is None

    # the children settle after the stamp
    led.update(f"{root}-p1", duration_delivered_s=1700.0,
               delivered_at="2026-08-15T10:00:00+00:00")
    led.set_state(f"{root}-p1", "DELIVERED")
    led.update(f"{root}-p2", duration_delivered_s=1600.0,
               delivered_at="2026-08-15T11:00:00+00:00")
    led.set_state(f"{root}-p2", "DELIVERED")

    s2 = _row(_sheet(led, W2))
    assert s2 is not None, "stranded hours never reached any sheet"
    assert s2["kamla_accepted_hrs"] == round(3300 / 3600.0, 2)   # 0.92
    assert s2["total_delivered_hours"] == round(3300 / 3600.0, 2)
    # uploaded is 0 on the re-entry — Adnaan accepted this reading
    assert s2["kamla_hrs_uploaded"] == 0.0
    assert s2["total_uploaded_hours"] == 0.0

    assert _row(_sheet(led, W3)) is None       # and never again
    led.close()


def test_unsplit_root_delivering_after_its_stamp_is_still_paid(tmp_path):
    """Same bug without a split: the root itself is probed and stamped
    while still VALIDATING, and delivers afterwards."""
    led = Ledger(tmp_path / "l.db")
    sid = "2026-08-14T09-00-00Z_kamla_c_00000000000000a2"
    _put(led, sid, state="VALIDATING", raw=3600.0, player="solo@x.com")
    s1 = _row(_sheet(led, W1), "solo@x.com")
    assert s1["kamla_hrs_uploaded"] == 1.0 and s1["kamla_accepted_hrs"] == 0.0

    led.update(sid, duration_delivered_s=3400.0,
               delivered_at="2026-08-15T10:00:00+00:00")
    led.set_state(sid, "DELIVERED")
    s2 = _row(_sheet(led, W2), "solo@x.com")
    assert s2 is not None and s2["kamla_accepted_hrs"] == 0.94
    assert s2["kamla_hrs_uploaded"] == 0.0
    assert _row(_sheet(led, W3), "solo@x.com") is None
    led.close()


def test_late_rejecting_child_labels_reach_a_sheet_once(tmp_path):
    """Reject labels are on the accepted side of the split too: a child
    that rejects after its root was stamped must still name its reason on
    the player's row — once."""
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_00000000000000a3"
    _put(led, root, state="SPLIT", raw=3600.0, player="rej@x.com")
    _put(led, f"{root}-p1", state="VALIDATING", parent=root, raw=1800.0,
         player="rej@x.com")
    s1 = _row(_sheet(led, W1), "rej@x.com")
    assert s1["kamla_rejection_reasons"] == ""

    led.set_reasons(f"{root}-p1", UNFIXABLE, 3)
    led.set_state(f"{root}-p1", "REJECTED")
    s2 = _row(_sheet(led, W2), "rej@x.com")
    assert s2 is not None
    assert s2["kamla_rejection_reasons"] == "black-frozen"
    assert _row(_sheet(led, W3), "rej@x.com") is None
    led.close()


def test_partial_tree_counts_incrementally_never_twice(tmp_path):
    """The mark is PER NODE, so a tree that settles in stages reports each
    stage once — the depth-2 partial-tree behaviour (a settled child's
    hours appear while a sibling is still in flight) is preserved."""
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_00000000000000a4"
    _put(led, root, state="SPLIT", raw=7200.0, player="inc@x.com")
    _put(led, f"{root}-p1", state="DELIVERED", parent=root, raw=3600.0,
         delivered=3600.0, player="inc@x.com")
    _put(led, f"{root}-p2", state="VALIDATING", parent=root, raw=3600.0,
         player="inc@x.com")
    s1 = _row(_sheet(led, W1), "inc@x.com")
    assert s1["kamla_accepted_hrs"] == 1.0      # settled child counted now

    led.update(f"{root}-p2", duration_delivered_s=1800.0,
               delivered_at="2026-08-15T10:00:00+00:00")
    led.set_state(f"{root}-p2", "DELIVERED")
    s2 = _row(_sheet(led, W2), "inc@x.com")
    assert s2 is not None
    assert s2["kamla_accepted_hrs"] == 0.5      # ONLY the new half
    assert _row(_sheet(led, W3), "inc@x.com") is None
    led.close()


# --------------------------------------------------- the two invariants

def test_accepted_hours_conservation_invariant(tmp_path):
    """The accepted-side mirror of d3's family-killer: accepted hours
    summed across ALL sheets equal duration_delivered_s over every
    DELIVERED node — nothing dropped, nothing doubled. This is the
    invariant BOTH rejected fixes broke, in opposite directions."""
    led = Ledger(tmp_path / "l.db")
    # a: settles inside its own window
    _put(led, "2026-08-14T09-00-00Z_kamla_c_00000000000000b1",
         state="DELIVERED", raw=3600.0, delivered=3400.0, player="a@x.com")
    # b: split, stamped mid-flight, children deliver in window 2
    b = "2026-08-14T10-00-00Z_kamla_c_00000000000000b2"
    _put(led, b, state="SPLIT", raw=3600.0,
         ctime="2026-08-14T10:00:00.000Z", player="b@x.com")
    _put(led, f"{b}-p1", state="VALIDATING", parent=b, raw=1800.0,
         player="b@x.com")
    # c: uploaded in window 2, delivers in window 2
    _put(led, "2026-08-15T09-00-00Z_kamla_c_00000000000000b3",
         state="DELIVERED", ctime="2026-08-15T09:00:00.000Z", raw=1800.0,
         delivered=1700.0, player="c@x.com")

    total = 0.0
    for i, w in enumerate((W1, W2, W3)):
        if i == 1:
            led.update(f"{b}-p1", duration_delivered_s=1750.0,
                       delivered_at="2026-08-15T10:00:00+00:00")
            led.set_state(f"{b}-p1", "DELIVERED")
        for r in _sheet(led, w):
            total += r["total_delivered_hours"]
    led.close()
    delivered_s = 3400 + 1750 + 1700
    assert total == round(3400 / 3600.0, 2) + round(1750 / 3600.0, 2) \
        + round(1700 / 3600.0, 2)
    assert abs(total - delivered_s / 3600.0) < 0.02      # rounding only


def test_uploaded_stamp_still_counts_uploads_exactly_once(tmp_path):
    """The uploaded side is untouched by the split: a root that re-enters
    for its accepted hours must NOT bring its uploaded hours back with it
    (that is the double-count the 'stamp later' fix caused)."""
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_00000000000000c1"
    _put(led, root, state="SPLIT", raw=3600.0)
    _put(led, f"{root}-p1", state="VALIDATING", parent=root, raw=1800.0)
    total_up = 0.0
    for i, w in enumerate((W1, W2, W3)):
        if i == 1:
            led.update(f"{root}-p1", duration_delivered_s=1700.0,
                       delivered_at="2026-08-15T10:00:00+00:00")
            led.set_state(f"{root}-p1", "DELIVERED")
        for r in _sheet(led, w):
            total_up += r["total_uploaded_hours"]
    led.close()
    assert total_up == 1.0        # once, in window 1, and never again


# --------------------------------------------------------- stamp hygiene

def test_supersede_clears_the_accepted_mark(tmp_path):
    """A re-upload under the same sid is genuinely new footage: an
    inherited accepted mark would seal its delivered hours out of every
    future sheet — the same bug in its other form."""
    led = Ledger(tmp_path / "l.db")
    sid = "2026-08-14T09-00-00Z_kamla_c_00000000000000d1"
    _put(led, sid, state="DELIVERED", raw=3600.0, delivered=3400.0)
    led.update(sid, accepted_reported_at="2026-08-15T00:00:00+00:00",
               uploaded_reported_at="2026-08-15T00:00:00+00:00")
    led.supersede(sid, new_md5="zz", new_bytes=2,
                  new_ctime="2026-08-16T00:00:00.000Z",
                  dossier_root=tmp_path / "dossiers")
    row = led.get(sid)
    assert row["accepted_reported_at"] is None
    assert row["uploaded_reported_at"] is None
    led.close()


def test_future_window_root_is_not_pulled_forward(tmp_path):
    """A root whose cohort window has not opened yet contributes nothing,
    accepted included — the re-entry is gated on `up < hi`."""
    led = Ledger(tmp_path / "l.db")
    sid = "2026-08-16T09-00-00Z_kamla_c_00000000000000e1"
    _put(led, sid, state="DELIVERED", ctime="2026-08-16T09:00:00.000Z",
         raw=3600.0, delivered=3400.0, player="future@x.com")
    led.update(sid, uploaded_reported_at="2026-08-14T00:00:00+00:00")
    assert _row(_sheet(led, W1), "future@x.com") is None
    led.close()


def test_unknown_game_node_cannot_re_enter_forever(tmp_path):
    """A node in a game with no sheet column can never be counted, so it
    must never pull its root back onto every future sheet either.

    Asserts the PREDICATE directly (r-loop 7): the old version only
    checked that no row appeared, which the row-suppression rule
    satisfies on its own — deleting the GAME_COL filter left the suite
    green. A mapped, already-counted sibling makes the row non-empty if
    the filter ever goes."""
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_00000000000000f1"
    _put(led, root, state="SPLIT", raw=3600.0, player="unk@x.com")
    _put(led, f"{root}-p1", state="DELIVERED", parent=root, raw=1800.0,
         delivered=1700.0, player="unk@x.com", game="xonotic")
    _put(led, f"{root}-p2", state="DELIVERED", parent=root, raw=1800.0,
         delivered=1700.0, player="unk@x.com")
    _sheet(led, W1)                       # root stamped; p2 counted
    assert led.get(f"{root}-p2")["accepted_reported_at"]

    rows = led.db.execute(
        "SELECT session_id, game, state, parent_id, accepted_reported_at"
        " FROM sessions").fetchall()
    children: dict = {}
    for r in rows:
        if r["parent_id"]:
            children.setdefault(r["parent_id"], []).append(r)
    root_row = next(r for r in rows if r["session_id"] == root)
    assert reports._tree_has_uncounted_accepted(root_row, children) is False

    assert _row(_sheet(led, W2), "unk@x.com") is None
    assert led.get(f"{root}-p1")["accepted_reported_at"] is None
    led.close()


def test_sheet_row_reject_labels_are_not_reprinted(tmp_path):
    """A reject counted on one sheet does not reappear on the next just
    because a sibling delivered later."""
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_00000000000000f2"
    _put(led, root, state="SPLIT", raw=3600.0, player="mix@x.com")
    _put(led, f"{root}-p1", state="REJECTED", parent=root, raw=1800.0,
         reasons=UNFIXABLE, player="mix@x.com")
    _put(led, f"{root}-p2", state="VALIDATING", parent=root, raw=1800.0,
         player="mix@x.com")
    s1 = _row(_sheet(led, W1), "mix@x.com")
    assert s1["kamla_rejection_reasons"] == "black-frozen"

    led.update(f"{root}-p2", duration_delivered_s=1700.0,
               delivered_at="2026-08-15T10:00:00+00:00")
    led.set_state(f"{root}-p2", "DELIVERED")
    s2 = _row(_sheet(led, W2), "mix@x.com")
    assert s2["kamla_accepted_hrs"] == round(1700 / 3600.0, 2)
    assert s2["kamla_rejection_reasons"] == ""      # not reprinted
    led.close()


def _refix(cfg, monkeypatch):
    refix = _load("recal_refix_reset")
    monkeypatch.setattr(refix, "rclone", lambda args: (0, ""))

    class _Args:
        yes = True
        allow_reported = True
    assert refix._locked_main(cfg, _Args) == 0


FIXABLE = [{"code": "SYN_TS_NOT_PTS", "blocking": True,
            "fixable": True, "params": {}, "evidence": "e"}]


def test_refix_seal_only_fires_where_hours_were_actually_counted(
        cfg, monkeypatch):
    """The seal exists for ONE job: this subtree is torn down and
    re-delivered, so hours already on a SENT sheet must not be counted
    twice. Keying it on the UPLOADED stamp was wrong (r-loop 7) — this
    tool selects fix-failed REJECTED trees, which contributed accepted_hrs
    0.00 to the sheet that stamped them, so the seal protected nothing and
    permanently blocked the re-run's genuinely new hours. That is the loss
    the split was written to close, on the very path that recovers the
    08-16 recalibration's false-positive rejects."""
    led = Ledger(cfg.ledger_path)
    try:
        # A: fix-failed root, uploaded-counted, NEVER paid an accepted hour
        a = "2026-08-14T09-00-00Z_kamla_c_0000000000000ga1"
        _put(led, a, state="REJECTED", raw=3600.0, player="unpaid@x.com",
             reasons=FIXABLE)
        led.update(a, uploaded_reported_at="2026-08-15T00:00:00+00:00",
                   accepted_reported_at="2026-08-15T00:00:00+00:00")
        # B: root whose DELIVERED child WAS counted — real double-pay risk
        b = "2026-08-14T09-00-00Z_kamla_c_0000000000000gb1"
        _put(led, b, state="SPLIT", raw=3600.0, player="paid@x.com")
        _put(led, f"{b}-p1", state="DELIVERED", parent=b, raw=1800.0,
             delivered=1700.0, player="paid@x.com")
        led.update(f"{b}-p1",
                   accepted_reported_at="2026-08-15T00:00:00+00:00")
        _put(led, f"{b}-p2", state="REJECTED", parent=b, raw=1800.0,
             player="paid@x.com", reasons=FIXABLE)
        led.update(b, uploaded_reported_at="2026-08-15T00:00:00+00:00")
        # C: root with a DELIVERED child that was NEVER counted
        c = "2026-08-14T09-00-00Z_kamla_c_0000000000000gc1"
        _put(led, c, state="SPLIT", raw=3600.0, player="uncounted@x.com")
        _put(led, f"{c}-p1", state="DELIVERED", parent=c, raw=1800.0,
             delivered=1700.0, player="uncounted@x.com")
        _put(led, f"{c}-p2", state="REJECTED", parent=c, raw=1800.0,
             player="uncounted@x.com", reasons=FIXABLE)
        led.update(c, uploaded_reported_at="2026-08-15T00:00:00+00:00")
    finally:
        led.close()

    _refix(cfg, monkeypatch)

    led = Ledger(cfg.ledger_path)
    try:
        got = {r["session_id"]: r["accepted_reported_at"] for r in
               led.db.execute("SELECT session_id, accepted_reported_at "
                              "FROM sessions")}
        assert got[a] is None, \
            "a tree that was never paid an accepted hour must re-open"
        assert got[b], \
            "a tree with counted DELIVERED hours must stay sealed"
        assert got[c] is None, \
            "an uncounted DELIVERED child is not a reason to seal"
    finally:
        led.close()


def test_refix_reopened_tree_is_paid_exactly_once(cfg, monkeypatch):
    """End of the chain: the re-run's delivered hours must actually reach
    a sheet, once. This is the property the wrong seal destroyed."""
    led = Ledger(cfg.ledger_path)
    root = "2026-08-14T09-00-00Z_kamla_c_0000000000000gd1"
    try:
        _put(led, root, state="REJECTED", raw=3600.0, player="recov@x.com",
             reasons=FIXABLE)
        led.update(root, uploaded_reported_at="2026-08-15T00:00:00+00:00",
                   accepted_reported_at="2026-08-15T00:00:00+00:00")
    finally:
        led.close()

    _refix(cfg, monkeypatch)

    led = Ledger(cfg.ledger_path)
    try:
        # the re-run delivers
        led.update(root, duration_delivered_s=3400.0,
                   delivered_at="2026-08-15T10:00:00+00:00")
        led.set_state(root, "DELIVERED")
        s2 = _row(_sheet(led, W2), "recov@x.com")
        assert s2 is not None, "recovered footage never reached a sheet"
        assert s2["kamla_accepted_hrs"] == 0.94
        assert _row(_sheet(led, W3), "recov@x.com") is None   # once
    finally:
        led.close()


def test_accepted_mark_is_written_by_the_daily_send_before_the_anchor(
        cfg, ledger, monkeypatch):
    """End of the wiring: the real send site stamps BOTH marks, and both
    BEFORE the anchor is written — a kill anywhere in the sequence errs
    toward a smaller resent sheet, never toward paid-twice hours."""
    import pipeline.run as runmod
    from datetime import timedelta, timezone
    send = datetime.now(C.IST).replace(hour=14, minute=7, second=3,
                                       microsecond=0)
    # inside the first-window seed [send-28h, send-4h) — otherwise the root
    # is not in-window and the sheet legitimately counts nothing
    ctime = (send.astimezone(timezone.utc) - timedelta(hours=10)
             ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    sid = "2026-08-14T09-00-00Z_kamla_c_0000000000000aa1"
    _put(ledger, sid, state="DELIVERED", ctime=ctime,
         raw=3600.0, delivered=3400.0, player="wire@x.com")
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda cfg_, text: None)
    monkeypatch.setattr(runmod.telegram, "send_document",
                        lambda cfg_, path, caption="": None)
    anchor = cfg.reports_dir / ".last_daily_sent"
    real_mark = reports.mark_accepted_reported
    seen: list[bool] = []

    def spy(led, sids):
        seen.append(anchor.exists())        # anchor must not exist yet
        return real_mark(led, sids)
    monkeypatch.setattr(reports, "mark_accepted_reported", spy)
    assert runmod.send_daily_report_if_due(cfg, ledger, send) is True
    assert seen == [False], "accepted stamp must precede the anchor write"
    assert ledger.get(sid)["accepted_reported_at"]
    assert ledger.get(sid)["uploaded_reported_at"]


# ------------------------------- r-loop 7: the gaps mutation testing found

def test_regen_resume_record_round_trips_through_the_real_writer(cfg):
    """BLOCKER (r-loop 7). The split gave the durable resume record a
    second half. Both write sites and the resume branch learned the new
    shape; the stray-stamp PRE-CHECK did not, and `set()` over a dict
    yields the KEY NAMES — so no real root id was ever in `recorded`,
    every cohort root the tool itself stamped read as stray, and the
    payment endgame hard-aborted with a false "interlock breached"
    diagnosis on every re-run, including the rc=3 telegram retry the tool
    tells the operator to perform. Tested against the REAL writer, never a
    hand-built shape."""
    regen = _load("recal_regen_sheets")
    day_dir = cfg.reports_dir / "2026-08-15"
    day_dir.mkdir(parents=True, exist_ok=True)
    rec = day_dir / ".regen-v2-counted.json"

    regen.write_counted_record(rec, ["root-a", "root-b"], ["root-a-p1"])
    counted, accepted = regen.read_counted_record(rec)
    assert counted == ["root-a", "root-b"], counted
    assert accepted == ["root-a-p1"], accepted
    # the pre-check compares ROOT ids against uploaded_reported_at
    assert set(counted) == {"root-a", "root-b"}
    assert "counted" not in set(counted) and "accepted" not in set(counted)

    # a pre-split bare-list record still loads
    rec.write_text(json.dumps(["root-c"]))
    assert regen.read_counted_record(rec) == (["root-c"], [])


def test_regen_rerun_after_a_send_does_not_false_abort(cfg, monkeypatch):
    """The end-to-end property: once a day has been sent and its roots
    stamped, re-running the tool must NOT read those roots as stray."""
    regen = _load("recal_regen_sheets")
    led = Ledger(cfg.ledger_path)
    sids = []
    try:
        for i in range(3):
            sid = f"2026-08-14T1{i}-00-00Z_kamla_c_0000000000000r{i}a"
            sids.append(sid)
            _put(led, sid, state="DELIVERED",
                 ctime=f"2026-08-14T1{i}:00:00.000Z", raw=3600.0,
                 delivered=3400.0, player="regen@x.com")
            # exactly what a completed --send leaves behind
            led.update(sid, uploaded_reported_at="2026-08-15T00:00:00+00:00",
                       accepted_reported_at="2026-08-15T00:00:00+00:00")
    finally:
        led.close()
    day_dir = cfg.reports_dir / "2026-08-15"
    day_dir.mkdir(parents=True, exist_ok=True)
    regen.write_counted_record(day_dir / ".regen-v2-counted.json",
                               sids, sids)
    (day_dir / ".regen-v2-done").write_text("done")

    monkeypatch.setattr(sys, "argv", ["recal_regen_sheets.py"])   # preview
    rc = regen._locked_main(cfg)
    assert rc == 0, "re-run false-aborted on roots the tool itself stamped"


def test_rebuild_reset_clears_the_accepted_mark(cfg, monkeypatch):
    """Untested until r-loop 7: deleting the reset left the suite green,
    and a stale root-level mark is read as a whole-tree SEAL."""
    reset = _load("recal_rebuild_reset")
    led = Ledger(cfg.ledger_path)
    sid = "2026-08-14T09-00-00Z_kamla_c_0000000000000rr1"
    try:
        _put(led, sid, state="DELIVERED", raw=3600.0, delivered=3400.0,
             player="rb@x.com")
        led.update(sid, uploaded_reported_at="2026-08-15T00:00:00+00:00",
                   accepted_reported_at="2026-08-15T00:00:00+00:00")
    finally:
        led.close()
    parachute = cfg.home / "parachute.db"
    parachute.write_bytes(b"x" * 2048)      # the tool requires >= 1 KiB
    # main() builds its own Config via C.load(); point that at the test
    # home so this can never reach a real ~/hl-pipeline
    monkeypatch.setenv("HL_PIPELINE_HOME", str(cfg.home))
    monkeypatch.setattr(sys, "argv",
                        ["recal_rebuild_reset.py", "--yes",
                         "--backup", str(parachute)])
    assert reset.main() == 0
    led = Ledger(cfg.ledger_path)
    try:
        row = led.get(sid)
        assert row["accepted_reported_at"] is None
        assert row["uploaded_reported_at"] is None
    finally:
        led.close()


def test_quarantine_heal_clears_the_accepted_mark(cfg, ledger, monkeypatch):
    """The path-heal is a FRESH-upload event and resets the slot exactly
    like supersede. Untested until r-loop 7; without it the healed
    re-upload's delivered hours are sealed out of every sheet."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    sid = "2026-08-14T10-00-00Z_kamla_c_0123456789abcdef"
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="op@x.com",
        player_email="p1@x.com", drive_path="kamla/BADPATH",
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video="old",
        bytes_=1, state="DISCOVERED")
    ledger.set_state(sid, "QUARANTINED")
    ledger.update(sid, uploaded_reported_at="2026-08-15T00:00:00+00:00",
                  accepted_reported_at="2026-08-15T00:00:00+00:00")
    entries = make_session_entries(sid=sid)
    monkeypatch.setattr(ingest, "list_drive", lambda _c: entries)
    ingest.scan(cfg, ledger, entries)
    row = ledger.get(sid)
    assert row["state"] == "DISCOVERED", row["state"]
    assert row["accepted_reported_at"] is None
    assert row["uploaded_reported_at"] is None

