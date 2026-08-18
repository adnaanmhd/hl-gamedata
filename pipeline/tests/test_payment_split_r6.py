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
    must never be able to pull its root back onto every future sheet
    either (the re-entry test filters exactly like the tree walk)."""
    led = Ledger(tmp_path / "l.db")
    root = "2026-08-14T09-00-00Z_kamla_c_00000000000000f1"
    _put(led, root, state="SPLIT", raw=3600.0, player="unk@x.com")
    _put(led, f"{root}-p1", state="DELIVERED", parent=root, raw=1800.0,
         delivered=1700.0, player="unk@x.com", game="xonotic")
    _sheet(led, W1)                       # root stamped uploaded
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


def test_refix_reset_mirrors_the_uploaded_stamp(cfg, monkeypatch):
    """recal_refix_reset: an UNREPORTED root is cleared so its re-run's
    hours can be paid; an already-reported root is SEALED so the same
    footage is not counted twice after its Drive II copy is replaced."""
    refix = _load("recal_refix_reset")
    led = Ledger(cfg.ledger_path)
    fixable = [{"code": "SYN_TS_NOT_PTS", "blocking": True,
                "fixable": True, "params": {}, "evidence": "e"}]
    for tag, stamped in (("g1", False), ("g2", True)):
        sid = f"2026-08-14T09-00-0{tag[-1]}Z_kamla_c_00000000000000{tag}"
        _put(led, sid, state="REJECTED", raw=3600.0, player=f"{tag}@x.com",
             reasons=fixable)
        led.update(sid, accepted_reported_at="2026-08-15T00:00:00+00:00")
        if stamped:
            led.update(sid, uploaded_reported_at="2026-08-15T00:00:00+00:00")
    led.close()

    monkeypatch.setattr(refix, "rclone", lambda args: (0, ""))

    class _Args:
        yes = True
        allow_reported = True
    assert refix._locked_main(cfg, _Args) == 0

    led = Ledger(cfg.ledger_path)
    try:
        got = {r["player_email"]: r["accepted_reported_at"] for r in
               led.db.execute("SELECT player_email, accepted_reported_at "
                              "FROM sessions")}
        assert got["g1@x.com"] is None, \
            "unreported root must re-open for payment"
        assert got["g2@x.com"], "already-reported root must stay sealed"
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
