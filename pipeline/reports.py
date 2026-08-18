"""Report formatting (plan §14) — per-batch Telegram topline, daily payment
message, and the payment sheet (CSV + human MD twin).

The message builders are pure formatters over computed values so the §18
byte-match acceptance can pin them exactly. Counts are sessions; hours are
always labelled; money never appears (R11).
"""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as C
from .ledger import Ledger
from .pace import PaceStatus

# code -> (batch label, daily short label)
REASON_LABELS = {
    "INP_MOTION_MISSING": ("no mouse motion", "no-mouse"),
    "INP_BUTTONS_MISSING": ("no mouse buttons", "no-buttons"),
    "INP_KEYS_MISSING": ("no keyboard", "no-keys"),
    "CNT_SHORT": ("<70s", "<70s"),
    "CNT_NOTIF_MID": ("notifications", "notifications"),
    "CNT_NOTIF_EDGE": ("notifications", "notifications"),
    "CNT_WRONG_GAME": ("wrong game", "wrong-game"),
    "INT_DUP_CROSS": ("duplicate", "dup"),
    "CNT_ACTIONS_FEW": ("<3 actions", "<3-actions"),
    "CNT_DROPS": ("frame drops", "drops"),
    "CNT_MID_NONGAMEPLAY": ("non-gameplay", "non-gameplay"),
    "CNT_AFK": ("AFK", "afk"),
    "CNT_CHAT_PII": ("chat/PII", "chat-pii"),
    "CNT_BLACK_FROZEN": ("black/frozen video", "black-frozen"),
    "SYN_UNMEASURABLE_SUSPECT": ("unverifiable sync", "sync-suspect"),
    "INT_TAMPER": ("integrity", "tamper"),
    "QA_FAIL_UNMAPPED": ("unmapped QA failure", "qa-unmapped"),
    "INT_PATH": ("bad drive path", "bad-path"),
}

# Reject-window anchor: the REJECTED transition time from the immutable
# events audit — NOT sessions.updated_at, which finalize_rejected bumps
# again when it writes the dossier (deliver.py), pushing a session counted
# in one daily window into the NEXT window too (double-count; found via
# the d3 session's reproduction gotcha, 08-15).
REJECT_TS = ("(SELECT MAX(ts) FROM events WHERE "
             "events.session_id = sessions.session_id "
             "AND events.to_state = 'REJECTED')")

# Marker for sessions that reached REJECTED with only FIXABLE reasons
# stored ("fix retries exhausted", R2): with fixable reasons hidden from
# every reject surface, such a player's cell would go blank while
# `rejected` still counts them — the bare marker keeps the surfaces
# honest (Adnaan, 08-15; no ×N per the same ruling).
FIX_FAILED_MARKER = "fix-failed"

# A reasons_json that fails to PARSE is an unknown, not a retries-
# exhausted verdict — rendering it as fix-failed would dress an unknown
# up as a specific claim an operator may act on (d3 finding, 08-15).
UNREADABLE_MARKER = "unreadable-reasons"


def reason_label(code: str, daily: bool = False) -> str:
    pair = REASON_LABELS.get(code)
    if pair:
        return pair[1] if daily else pair[0]
    return code.lower().replace("_", "-")


def session_reject_labels(reasons: list[dict],
                          daily: bool = False) -> list[str]:
    """One rejected session's surface labels: blocking AND NOT fixable,
    judged per reason's OWN stored `fixable` field — three codes are
    conditionally fixable per instance (CNT_MID_NONGAMEPLAY, CNT_CHAT_PII,
    QA_FAIL_UNMAPPED), so a code-name list would misattribute them.
    Deduped (labels, not codes — NOTIF_MID/EDGE share a nickname). A
    session with no unfixable reason gets the bare FIX_FAILED_MARKER."""
    labels = sorted({reason_label(r["code"], daily) for r in reasons
                     if r.get("blocking") and not r.get("fixable")})
    return labels or [FIX_FAILED_MARKER]


def ordered_reject_labels(per_session_labels: list[list[str]]) -> list[str]:
    """Aggregate label lists across sessions: EVERY distinct label (no
    silent caps), ordered by occurrence count descending then
    alphabetically — the count decides order but is never printed
    (Adnaan, 08-15)."""
    counts: dict[str, int] = {}
    for labels in per_session_labels:
        for lbl in labels:
            counts[lbl] = counts.get(lbl, 0) + 1
    return [lbl for lbl, _ in
            sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


@dataclass
class BatchStats:
    batch_no: int
    finished_ist: datetime
    duration_min: int
    delivered: int
    total: int
    auto_fixed: int
    rejected: int
    reject_labels: list[str] = field(default_factory=list)
    hours_delta: float = 0.0
    hours_kamla: float = 0.0
    hours_ow: float = 0.0
    pending: int = 0
    incomplete: int = 0
    ok: bool = True
    on_fallback: int = 0     # R23: verdicts that used a laddered-down model


def build_batch_message(b: BatchStats, pace: PaceStatus | None) -> str:
    """§14 per-batch topline. Healthy batch = 4 lines; pace line only when
    the alarm fired."""
    icon = "✓" if b.ok else "⚠"
    lines = [f"🎮 batch #{b.batch_no} {icon} "
             f"{b.finished_ist.strftime('%H:%M')} · {b.duration_min}m"]
    s = f"sessions: {b.delivered}/{b.total} delivered"
    if b.auto_fixed:
        s += f" ({b.auto_fixed} auto-fixed)"
    s += f" · {b.rejected} rejected"
    if b.reject_labels:
        s += f" ({' · '.join(b.reject_labels)})"
    lines.append(s)
    total = b.hours_kamla + b.hours_ow
    lines.append(
        f"hours: +{b.hours_delta:.1f} → "
        f"Kamla {b.hours_kamla:.1f}/{C.TARGET_HOURS_PER_GAME:.0f} · "
        f"OW {b.hours_ow:.1f}/{C.TARGET_HOURS_PER_GAME:.0f} "
        f"(Σ {total:.1f}/{2 * C.TARGET_HOURS_PER_GAME:.0f})")
    lines.append(f"queue: {b.pending} sessions pending · "
                 f"{b.incomplete} incomplete")
    if b.on_fallback:
        # R23-approved format addition (08-15): only when a verdict came
        # from a laddered-down model
        lines.append(f"{b.on_fallback} on fallback model")
    if pace is not None and pace.alarm:
        lines.append(
            f"⚠️ PACE need {pace.need_total:.0f} h/day · trailing "
            f"{pace.trailing_24h:.0f} → ~{pace.min_per_player_day:.0f} "
            f"min/player/day required")
    return "\n".join(lines)


@dataclass
class DigestStats:
    """3-h continuous digest (replaces the per-batch topline — Adnaan
    ruling 4, 2026-08-17). All window figures are LEDGER-derived over
    [anchor, hi) so a kill never loses or doubles them."""
    now_ist: datetime
    window_h: float
    delivered_n: int
    delivered_hours: float
    rejected_n: int
    reject_labels: list[str] = field(default_factory=list)
    hours_kamla: float = 0.0
    hours_ow: float = 0.0
    backlog_undownloaded: int = 0
    backlog_inflight: int = 0
    backlog_fix: int = 0
    backlog_hold: int = 0
    incomplete: int = 0
    quarantined_n: int = 0        # new quarantines in the window
    on_fallback: int = 0          # R23 flagged verdicts in the window
    pool_target: int = 0
    pool_active: int = 0
    vlm_rung: int = 0
    stuck: list[str] = field(default_factory=list)   # pre-formatted lines
    stuck_total: int = 0
    past_deadline: bool = False


def build_digest_message(d: DigestStats, pace: PaceStatus | None) -> str:
    """Pure formatter (byte-pinnable like the batch/daily templates).
    Sent every CONT_DIGEST_INTERVAL_H even when idle — the heartbeat."""
    total = d.hours_kamla + d.hours_ow
    lines = [f"📡 digest {d.now_ist.strftime('%H:%M')} · "
             f"last {d.window_h:.1f}h"]
    w = (f"window: {d.delivered_n} delivered "
         f"(+{d.delivered_hours:.1f} h) · {d.rejected_n} rejected")
    if d.reject_labels:
        w += f" ({' · '.join(d.reject_labels)})"
    if d.quarantined_n:
        w += f" · {d.quarantined_n} quarantined"
    lines.append(w)
    lines.append(
        f"totals: Kamla {d.hours_kamla:.1f}/{C.TARGET_HOURS_PER_GAME:.0f} · "
        f"OW {d.hours_ow:.1f}/{C.TARGET_HOURS_PER_GAME:.0f} "
        f"(Σ {total:.1f}/{2 * C.TARGET_HOURS_PER_GAME:.0f})")
    lines.append(
        f"backlog: {d.backlog_undownloaded} undownloaded · "
        f"{d.backlog_inflight} in-flight · {d.backlog_fix} fix · "
        f"{d.backlog_hold} hold · {d.incomplete} incomplete")
    pool = f"pool: {d.pool_active}/{d.pool_target} active"
    if d.vlm_rung:
        pool += f" · rung {d.vlm_rung}"
    lines.append(pool)
    if d.on_fallback:
        lines.append(f"{d.on_fallback} on fallback model")
    if d.past_deadline:
        lines.append("deadline passed — pace line retired")
    elif pace is not None and pace.alarm:
        lines.append(
            f"⚠️ PACE need {pace.need_total:.0f} h/day · trailing "
            f"{pace.trailing_24h:.0f} → ~{pace.min_per_player_day:.0f} "
            f"min/player/day required")
    if d.stuck:
        s = "stuck: " + " · ".join(d.stuck)
        if d.stuck_total > len(d.stuck):
            s += f" (+{d.stuck_total - len(d.stuck)} more)"
        lines.append(s)
    return "\n".join(lines)


@dataclass
class DailyStats:
    day_ist: datetime
    delivered_hours_today: float
    delivered_sessions_today: int
    rejected_sessions_today: int
    hours_kamla: float
    hours_ow: float
    collected_kamla: float
    collected_ow: float
    days_left: int
    reject_counts: list[tuple[str, int]] = field(default_factory=list)
    integrity_lines: list[str] = field(default_factory=list)
    folder_issues: int = 0     # count only — the list rides its own message


def build_daily_message(d: DailyStats, pace: PaceStatus | None) -> str:
    total = d.hours_kamla + d.hours_ow
    lines = [
        f"💰 daily — {d.day_ist.strftime('%b')} {d.day_ist.day}",
        f"delivered today +{d.delivered_hours_today:.1f} h from "
        f"{d.delivered_sessions_today} sessions · "
        f"{d.rejected_sessions_today} sessions rejected",
        f"totals: Kamla {d.hours_kamla:.1f}/{C.TARGET_HOURS_PER_GAME:.0f} · "
        f"OW {d.hours_ow:.1f}/{C.TARGET_HOURS_PER_GAME:.0f} · "
        f"Σ {total:.1f}/{2 * C.TARGET_HOURS_PER_GAME:.0f}",
        f"collected: Kamla {d.collected_kamla:.0f}/"
        f"{C.COLLECT_TARGET_HOURS:.0f} · OW {d.collected_ow:.0f}/"
        f"{C.COLLECT_TARGET_HOURS:.0f} · {d.days_left} days left",
    ]
    if pace is not None and pace.alarm:
        proj = (f"{pace.projected_finish.strftime('%b')} "
                f"{pace.projected_finish.day}"
                if pace.projected_finish else "never at current rate")
        lines.append(f"⚠️ pace: need {pace.need_total:.0f} h/day (trailing "
                     f"{pace.trailing_24h:.0f}) — projected finish {proj}")
    if d.reject_counts:
        # labels only — the count orders but is not printed (Adnaan 08-15)
        lines.append("rejects: " + " · ".join(
            label for label, _n in d.reject_counts))
    for line in d.integrity_lines:
        lines.append(f"integrity: {line}")
    # ALWAYS present (Adnaan via d3, 08-15): the daily heartbeat for the
    # folder-issues report — pure silence made a crashed issues job look
    # exactly like a clean day. Count only, never the list: the payment
    # message stays forwardable without chase-work riding along.
    lines.append("folder issues: 0" if not d.folder_issues else
                 f"folder issues: {d.folder_issues} — see next message")
    lines.append("📎 payment sheet attached")
    return "\n".join(lines)


# ------------------------------------------------------------ sheet (F8)

# Schema per Adnaan (08-15, via the d3 session; respec same day): one row
# per (operator, player) spanning both games — three kamla_/ow_ pairs
# after the identity columns. No game column, no counts, no cumulative.
# v4 (08-15): COHORT accounting. A player's footage is judged in the
# window it was UPLOADED in — accepted/rejected (and internal pending)
# are attributed back to the root upload's window regardless of when the
# outcome happened (delivery-time keying made rows internally
# incomparable: "1.29 uploaded / 0.44 accepted" read as rejection when it
# was just the pipeline's 1-2 h latency). The pending PAIR was added and
# then REMOVED by Adnaan the same day — pending is still computed and a
# non-zero cohort logs loudly (with the 4 h offset it is zero on every
# healthy run; a stalled session silently understating accepted hours is
# exactly the failure this schema chases). Total column names are
# Adnaan's EXACT strings — "hours" vs "hrs" and "delivered" vs
# "accepted" deliberately NOT normalized (flagged; one-line change here
# if he harmonizes).
SHEET_COLS = ["date", "operator", "player_email",
              "kamla_hrs_uploaded", "ow_hrs_uploaded",
              "kamla_accepted_hrs", "ow_accepted_hrs",
              "kamla_rejection_reasons", "ow_rejection_reasons",
              "total_uploaded_hours", "total_delivered_hours"]


def _day_bounds_utc(day_ist: datetime) -> tuple[str, str]:
    start_ist = day_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_ist.astimezone(timezone.utc)
    return (start.isoformat(timespec="seconds"),
            (start + timedelta(days=1)).isoformat(timespec="seconds"))


def r_countable(root) -> bool:
    """A root's uploaded-hours are countable once the video was probed."""
    return root["duration_raw_s"] is not None


def mark_uploads_reported(ledger: Ledger, lo: str, hi: str,
                          sids: list[str] | None = None) -> int:
    """Stamp uploaded_reported_at on every root the just-generated sheet
    counted. The stamp is what stops a late arrival being counted twice.
    Returns the number stamped.

    `sids` is the EXACT counted set the sheet built (build_sheet_rows'
    counted_out) — always pass it in production: re-deriving here raced
    the D thread, and a root probed between generation and stamping got
    stamped without ever being counted — its hours vanished from every
    sheet (review-r5 #3). The re-derive below survives only as a fallback
    for callers without a counted list."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if sids is not None:
        for sid in sids:
            ledger.update(sid, uploaded_reported_at=now)
        return len(sids)
    lo_dt, hi_dt = _parse_ts(lo), _parse_ts(hi)
    if lo_dt is None or hi_dt is None:
        return 0
    n = 0
    for r in ledger.db.execute(
            "SELECT session_id, drive_ctime, created_at, duration_raw_s,"
            " uploaded_reported_at FROM sessions WHERE parent_id IS NULL"
            " AND player_email != '' AND uploaded_reported_at IS NULL"
            " AND state NOT IN ('DUPLICATE','QUARANTINED')").fetchall():
        if r["duration_raw_s"] is None:
            continue
        up = _parse_ts(r["drive_ctime"]) or _parse_ts(r["created_at"])
        if up is not None and up < hi_dt:      # in-window OR late arrival
            ledger.update(r["session_id"], uploaded_reported_at=now)
            n += 1
    return n


def mark_accepted_reported(ledger: Ledger, sids: list[str]) -> int:
    """Stamp accepted_reported_at on every NODE whose accepted hours (or
    reject labels) the just-generated sheet counted — build_sheet_rows'
    accepted_out. Returns the number stamped.

    This is the second half of the RULED split (Adnaan, 2026-08-18).
    `uploaded_reported_at` used to mean both "uploaded hours counted" and
    "this root is finished, never look again"; the second meaning is what
    lost the money — a root stamped while its children were still being
    validated could never re-enter a sheet, so hours that shipped to the
    client were never paid. Uploaded is still counted exactly once (d3's
    conservation invariant holds); accepted is now counted exactly once
    too, per node, whenever it lands.

    Deliberately has NO re-derive fallback: re-deriving the counted set
    raced the pipeline threads and stamped rows the sheet never counted
    (review-r5 #3). The caller passes what the sheet actually counted."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for sid in sids:
        ledger.update(sid, accepted_reported_at=now)
    return len(sids)


def _parse_ts(v: str | None) -> datetime | None:
    """Normalize the ledger's two timestamp dialects to aware datetimes:
    drive_ctime is RFC3339 with millis + Z ('…T11:08:34.413Z') while
    everything else is isoformat seconds +00:00 — a raw SQL string
    comparison between them is meaningless (d3 gotcha B, 08-15)."""
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


# game -> the sheet's column prefix. Module level so the accepted-side
# re-entry test below filters exactly like the tree walk does: a node in a
# game with no column can never be counted, so it must never be able to
# pull its root back onto every future sheet either.
GAME_COL = {"kamla": "kamla", "outer_wilds": "ow"}

def pending_daily_send(cfg) -> str | None:
    """The day name of any daily send whose durable record has not fully
    settled (missing .sent marker, or .sent without doc_sent), else None.
    The recal tools REFUSE to run while one exists (r-loop 9 #7): tearing
    rows down under a pending record makes the later resume send a STALE
    sheet crediting deleted rows, and the re-run's deterministic same-id
    children then get counted a second time."""
    try:
        day_dirs = sorted(p for p in Path(cfg.reports_dir).iterdir()
                          if p.is_dir())
    except OSError:
        return None
    for d in day_dirs:
        rec = d / ".daily-counted.json"
        if not rec.is_file():
            continue
        if not (d / ".sent").exists():
            return d.name
        try:
            if not json.loads(rec.read_text()).get("doc_sent"):
                return d.name
        except (OSError, json.JSONDecodeError):
            return d.name
    return None


# accepted-side terminal states: the only nodes that ever carry accepted
# hours or reject labels onto a sheet.
_ACCEPTED_STATES = ("DELIVERED", "REJECTED")


def _mem_reconcile_failures(root, children: dict, mem: dict) -> list[str]:
    """Paid-piece memory rows that fail to reconcile against the tree's
    DELIVERED nodes (r-loop 11 #2/#13/#20). Cutter ids are deterministic
    ({sid}-p1, {sid}-p1-p1), so an id-PRESENCE check proves nothing: any
    re-cut re-creates the recorded id over different footage. A memory
    row reconciles only when its id is a currently-DELIVERED node with
    the recorded seconds (±1.0s); an id that is absent, now
    SPLIT/REJECTED/pending, or seconds-mismatched proves the cut changed
    — which is the entire premise of the memory skip, so the whole
    tree's match is void."""
    by_id: dict[str, dict] = {}
    st = [root]
    while st:
        n = st.pop()
        by_id[n["session_id"]] = n
        st.extend(children.get(n["session_id"], []))
    out: list[str] = []
    for pid, secs in sorted(mem.items()):
        node = by_id.get(pid)
        if node is None:
            out.append(f"{pid} (absent)")
        elif node["state"] != "DELIVERED":
            out.append(f"{pid} (now {node['state']})")
        elif secs is None or \
                abs((node["duration_delivered_s"] or 0.0) - secs) > 1.0:
            out.append(f"{pid} (paid "
                       f"{secs if secs is not None else '?'}s, "
                       f"re-delivered "
                       f"{(node['duration_delivered_s'] or 0.0):.0f}s)")
    return out


def _tree_has_uncounted_accepted(root, children: dict,
                                 mem: dict | None = None) -> bool:
    """Does this root's tree hold a DELIVERED/REJECTED node whose accepted
    hours (or labels) no sheet has counted yet? Drives the accepted-side
    re-entry — see mark_accepted_reported. A DELIVERED node in the paid
    -piece memory is treated as counted (r-loop 9, ruling C): without
    this, a memory-skipped node (which never gets a stamp) would re-enter
    its root on every future sheet forever. The match is VOID whenever
    any memory row fails to reconcile against the tree's DELIVERED nodes
    (r-loop 10 #11, re-keyed r-loop 11 #2/#13/#20: cutter ids are
    deterministic, so id PRESENCE proves nothing — a recorded id absent,
    present as non-DELIVERED, or seconds-mismatched all prove the cut
    changed) — every unstamped node then keeps the root re-entering so
    the loud exclusion line repeats until a human reconciles."""
    orphaned = _mem_reconcile_failures(root, children, mem) if mem \
        else []
    stack = [root]
    while stack:
        n = stack.pop()
        stack.extend(children.get(n["session_id"], []))
        if n["state"] not in _ACCEPTED_STATES \
                or n["accepted_reported_at"] \
                or GAME_COL.get(n["game"] or "") is None:
            continue
        if n["state"] == "DELIVERED" and mem and not orphaned \
                and n["session_id"] in mem:
            secs = mem[n["session_id"]]
            cur = n["duration_delivered_s"] or 0.0
            if secs is not None and abs(cur - secs) <= 1.0:
                # a MATCHED paid piece counts as counted (pre-teardown)
                continue
            # an id COLLISION stays "uncounted" on purpose: the root
            # keeps re-entering so build_sheet_rows prints its AMBIGUOUS
            # line on every sheet until a human reconciles
        return True
    return False


def build_sheet_rows(ledger: Ledger, day_ist: datetime,
                     bounds: tuple[str, str] | None = None,
                     counted_out: list[str] | None = None,
                     accepted_out: list[str] | None = None) -> list[dict]:
    """One row per (operator, player), COHORT accounting (v4, 08-15):
    every ROOT upload (parent_id IS NULL, not DUPLICATE/QUARANTINED)
    whose drive_ctime falls in the window contributes — to that window —
    every hour derived from it, however late the outcome:

    - *_hrs_uploaded: the root's duration_raw_s (probed video.mp4).
      Windowed on the REAL upload time (created_at is discovery time and
      clusters poll batches); blank ctime falls back to created_at, logged.
    - *_accepted_hrs: SUM(duration_delivered_s) over DELIVERED nodes in
      the root's tree — walked RECURSIVELY (live trees reach depth 2;
      a one-level join drops hours). SPLIT nodes contribute nothing
      themselves: their children carry the hours.
    - *_pending_hrs: SUM(duration_raw_s) over tree nodes not yet
      DELIVERED/REJECTED/SPLIT — derived from STATE, never by
      subtraction (trim/cut loss would masquerade as work in progress).
    - *_rejection_reasons: unfixable labels from REJECTED tree nodes
      (stored fixable flag, deduped, count-ordered, no counts printed,
      fix-failed marker; unreadable-reasons on parse failure).
    - uploaded != accepted+pending+rejected by design: head/tail trim and
      dropped segments are legitimate loss.
    Rows with no activity in the window are suppressed. Hours only, no
    money (R11).

    TWO independent marks decide re-entry (RULED, Adnaan 2026-08-18):
    `uploaded_reported_at` on the root means only "uploaded hours counted";
    `accepted_reported_at` per NODE means "that node's accepted hours /
    labels counted". A root stamped while its children were still in
    flight therefore comes BACK on a later sheet carrying accepted hours
    with uploaded 0 — an intended reading, not a defect. Before the split
    one mark did both jobs and every such root was sealed out of every
    future sheet, so footage that shipped was never paid for."""
    lo, hi = bounds or _day_bounds_utc(day_ist)
    lo_dt, hi_dt = _parse_ts(lo), _parse_ts(hi)
    day = day_ist.strftime("%Y-%m-%d")
    rows = ledger.db.execute(
        "SELECT session_id, game, operator_email, player_email, parent_id,"
        " state, drive_ctime, created_at, duration_raw_s,"
        " duration_delivered_s, reasons_json, uploaded_reported_at,"
        " accepted_reported_at, tree_sealed_at"
        " FROM sessions WHERE player_email != ''").fetchall()
    children: dict[str, list] = {}
    for r in rows:
        if r["parent_id"]:
            children.setdefault(r["parent_id"], []).append(r)
    # per-piece payment memory (RULED C, Adnaan 2026-08-18; r-loop 9
    # #1/#18): pieces recal_refix_reset recorded as already-paid before
    # tearing their tree down. A re-delivered same-id piece matching its
    # record is skipped exactly like an accepted-stamped node; a matching
    # id with DIFFERENT seconds is an id COLLISION (the re-run cut
    # differently) — excluded LOUDLY, never auto-paid: a withheld hour is
    # hand-recoverable, a double-pay is not.
    paid_mem: dict[str, dict] = {}
    for r in ledger.db.execute(
            "SELECT root_id, session_id, seconds FROM paid_pieces"):
        paid_mem.setdefault(r["root_id"], {})[r["session_id"]] = \
            r["seconds"]
    per_key: dict[tuple[str, str], dict] = {}

    def bucket(r):
        k = (r["operator_email"] or "", r["player_email"])
        return per_key.setdefault(k, {
            "kamla_hrs_uploaded": 0.0, "ow_hrs_uploaded": 0.0,
            "kamla_accepted_hrs": 0.0, "ow_accepted_hrs": 0.0,
            "kamla_pending_hrs": 0.0, "ow_pending_hrs": 0.0,
            "kamla_rej": [], "ow_rej": []})

    game_col = GAME_COL
    for root in rows:
        if root["parent_id"] is not None or \
                root["state"] in ("DUPLICATE", "QUARANTINED"):
            continue
        up = _parse_ts(root["drive_ctime"])
        if up is None:
            up = _parse_ts(root["created_at"])
            if up is not None:
                print(f"[sheet] {root['session_id']}: blank/unparseable "
                      f"drive_ctime — windowing upload on created_at",
                      file=sys.stderr)
        if up is None or lo_dt is None or hi_dt is None:
            continue
        # a STAMPED root was already counted by some sheet — never again,
        # even if it lands in-window: a lost/rewound anchor file used to
        # re-open an already-reported interval and double-count every
        # cohort in it (review-r5 #33/#43); an identical-window resend
        # after a mid-sequence kill now yields a smaller sheet instead
        in_window = lo_dt <= up < hi_dt and not root["uploaded_reported_at"]
        # LATE-ARRIVAL GUARD (d3/review-r4): a root whose cohort window
        # has already been reported (up < lo) but which no sheet has
        # counted yet — folder completed after its MIN-file-ctime stamp,
        # or download finished after generation — joins the CURRENT
        # window instead of vanishing. Countable = raw probed, or a
        # terminal reject that never downloads (scan-time cross-dup):
        # its labels must still reach the player's row exactly once
        # (review-r5 #12). The send site stamps what THIS sheet counted.
        late = (not in_window and up < lo_dt
                and (r_countable(root) or root["state"] == "REJECTED")
                and not root["uploaded_reported_at"])
        # ACCEPTED-SIDE RE-ENTRY (RULED, Adnaan 2026-08-18). The uploaded
        # stamp means ONLY "uploaded hours counted". A root stamped while
        # its split children were still validating recorded accepted_hrs=0
        # and was then invisible to both guards above forever — the player
        # was paid nothing for footage that shipped to the client
        # (measured: 135 of 309 countable roots, 16.84 h, on the 08-18
        # rebuild dump). It now comes back carrying accepted hours only,
        # with uploaded 0. `up < hi_dt` keeps a root whose cohort window
        # has not opened yet out of this sheet. The whole-tree SEAL lives
        # in its OWN column, tree_sealed_at, written only by
        # recal_refix_reset when it tears down an already-paid tree
        # (r-loop 8): the root's accepted_reported_at means only "this
        # root NODE's own count" — reading it as a seal let an ordinary
        # daily send that counted a DELIVERED/REJECTED root's own
        # hours/labels lock its live children's future hours out forever.
        sealed = bool(root["tree_sealed_at"])
        accepted_due = (up < hi_dt and not sealed
                        and bool(root["uploaded_reported_at"])
                        and _tree_has_uncounted_accepted(
                            root, children,
                            paid_mem.get(root["session_id"])))
        # THIRD RE-ENTRY ARM (r-loop 11 #6): a root whose duration_raw_s
        # never landed (the download-time ffprobe is single-shot and
        # swallowed) is not countable, so it can never be uploaded-
        # stamped — once its window passed, `late` and `accepted_due`
        # were BOTH unreachable and its DELIVERED/REJECTED nodes' hours/
        # labels reached no sheet, silently, forever. Pay the accepted
        # side exactly like accepted_due; uploaded hours stay 0 — never
        # fabricate them from a NULL probe. The drivers' validate-time
        # backfill closes this for every root that still validates; this
        # arm is the residue for roots that never re-validate.
        uncountable_due = (not in_window and up < hi_dt and not sealed
                           and not root["uploaded_reported_at"]
                           and not (r_countable(root)
                                    or root["state"] == "REJECTED")
                           and _tree_has_uncounted_accepted(
                               root, children,
                               paid_mem.get(root["session_id"])))
        if uncountable_due:
            print(f"[sheet] UNCOUNTABLE root {root['session_id']} "
                  f"(duration_raw_s missing — swallowed probe): counting "
                  f"its settled tree nodes' accepted hours/labels; "
                  f"uploaded hours stay 0", file=sys.stderr)
        if not in_window and not late and not accepted_due \
                and not uncountable_due:
            continue
        if late:
            # POST-SPLIT shape (r-loop 8; kickoff §4d): a late root is
            # counted IMMEDIATELY, exactly like an in-window one —
            # uploaded hours now, accepted hours whenever each node
            # settles, via the per-node accepted marks (accepted_due on
            # later sheets). The old settle-check deferral (review-r5 #29)
            # predates the mark split — back then the stamp froze
            # accepted_hrs at whatever the sheet saw. Post-split it was
            # pure loss: an unsettled late tree reached NO sheet at all
            # while a HOLD_VLM node blocked it (HOLD re-enters itself
            # every 30 min, so "settled" could be never), though the
            # identical in-window tree was paid incrementally.
            stack_s = [root]
            unsettled = False
            while stack_s:
                n_ = stack_s.pop()
                stack_s.extend(children.get(n_["session_id"], []))
                if n_["state"] not in ("DELIVERED", "REJECTED", "SPLIT",
                                       "DUPLICATE", "QUARANTINED"):
                    unsettled = True
                    break
            if unsettled:
                print(f"[sheet] LATE ARRIVAL (tree still in flight — "
                      f"uploaded counted now; accepted hours follow on "
                      f"later sheets): {root['session_id']}",
                      file=sys.stderr)
            else:
                print(f"[sheet] LATE ARRIVAL: {root['session_id']} uploaded "
                      f"{root['drive_ctime'] or root['created_at']} — its "
                      f"window was already reported; counted in the "
                      f"current sheet (conservation)", file=sys.stderr)
        if (in_window or late) and counted_out is not None and \
                (r_countable(root) or root["state"] == "REJECTED"):
            # exactly what mark_uploads_reported must stamp: counted
            # roots only — an in-window root still awaiting its download
            # stays unstamped so the late guard can pick its hours up.
            # An accepted-only re-entry never lands here: its uploaded
            # hours were counted (and stamped) by an earlier sheet.
            counted_out.append(root["session_id"])
        if in_window or late:
            g_root = game_col.get(root["game"] or "")
            if g_root is not None:
                bucket(root)[f"{g_root}_hrs_uploaded"] += \
                    (root["duration_raw_s"] or 0.0) / 3600.0
        # recursive walk of the whole tree (root included)
        mem = paid_mem.get(root["session_id"]) or {}
        # ORPHANED memory (r-loop 10 #11, re-keyed r-loop 11 #2/#13/#20):
        # a paid piece that fails to reconcile against the tree's
        # DELIVERED nodes — id absent, id now SPLIT/REJECTED/pending, or
        # seconds-mismatched — means the re-run re-cut the same footage
        # (unsplit root, re-cut siblings, -p1-p1 nesting: the EXPECTED
        # refix outcomes). Deterministic cutter ids made a bare
        # id-PRESENCE check silently double-pay: any re-cut re-creates
        # R-p1, so the void never fired. On any failed reconcile, every
        # not-in-memory DELIVERED node of the tree is excluded LOUDLY,
        # and the ROOT prints one reconcile line per sheet even when no
        # such node exists (#20: all-matched trees re-entered silently
        # forever), until a human reconciles the memory rows.
        orphaned = _mem_reconcile_failures(root, children, mem) if mem \
            else []
        if orphaned:
            print(f"[sheet] ORPHANED paid-piece memory under "
                  f"{root['session_id']}: {'; '.join(orphaned)} — paid "
                  f"rows failed to reconcile against the tree's "
                  f"DELIVERED nodes; not-in-memory DELIVERED hours are "
                  f"withheld; reconcile by hand", file=sys.stderr)
        stack = [root]
        while stack:
            n = stack.pop()
            stack.extend(children.get(n["session_id"], []))
            g = game_col.get(n["game"] or "")
            if g is None:
                continue     # never dump an unknown game into a column
            if n["state"] == "DELIVERED":
                # counted once, ever: its own mark, or the root-level seal
                if sealed or n["accepted_reported_at"]:
                    continue
                if orphaned and n["session_id"] not in mem:
                    print(f"[sheet] ORPHANED paid-piece memory under "
                          f"{root['session_id']} ({', '.join(orphaned)}): "
                          f"re-delivered node {n['session_id']} "
                          f"({(n['duration_delivered_s'] or 0.0):.0f}s) "
                          f"may contain already-paid footage — NOT "
                          f"counted; reconcile by hand", file=sys.stderr)
                    continue
                if n["session_id"] in mem:
                    secs = mem[n["session_id"]]
                    cur = n["duration_delivered_s"] or 0.0
                    if secs is not None and abs(cur - secs) <= 1.0:
                        print(f"[sheet] paid-piece memory: "
                              f"{n['session_id']} ({cur:.0f}s) was paid "
                              f"before its refix teardown — not counted "
                              f"again", file=sys.stderr)
                    else:
                        print(f"[sheet] AMBIGUOUS re-delivered piece "
                              f"{n['session_id']}: paid memory "
                              f"{secs if secs is not None else '?'}s, "
                              f"re-delivered {cur:.0f}s — NOT counted; "
                              f"reconcile by hand", file=sys.stderr)
                    continue
                bucket(n)[f"{g}_accepted_hrs"] += \
                    (n["duration_delivered_s"] or 0.0) / 3600.0
                if accepted_out is not None:
                    accepted_out.append(n["session_id"])
            elif n["state"] == "REJECTED":
                if sealed or n["accepted_reported_at"]:
                    continue
                try:
                    labels = session_reject_labels(
                        json.loads(n["reasons_json"] or "[]"), daily=True)
                except json.JSONDecodeError:
                    labels = [UNREADABLE_MARKER]
                bucket(n)[f"{g}_rej"].append(labels)
                if accepted_out is not None:
                    accepted_out.append(n["session_id"])
            elif n["state"] != "SPLIT":
                # still in flight (SPLIT itself carries nothing — its
                # children hold the hours)
                bucket(n)[f"{g}_pending_hrs"] += \
                    (n["duration_raw_s"] or 0.0) / 3600.0

    out = []
    for (op, player), b in sorted(per_key.items()):
        row = {
            "date": day, "operator": op, "player_email": player,
            "kamla_hrs_uploaded": round(b["kamla_hrs_uploaded"], 2),
            "ow_hrs_uploaded": round(b["ow_hrs_uploaded"], 2),
            "kamla_accepted_hrs": round(b["kamla_accepted_hrs"], 2),
            "ow_accepted_hrs": round(b["ow_accepted_hrs"], 2),
            "kamla_rejection_reasons":
                " ".join(ordered_reject_labels(b["kamla_rej"])),
            "ow_rejection_reasons":
                " ".join(ordered_reject_labels(b["ow_rej"]))}
        # totals sum the ROUNDED parts: the visible columns must add up
        # exactly on a payment document (v3, Adnaan 08-15)
        row["total_uploaded_hours"] = round(
            row["kamla_hrs_uploaded"] + row["ow_hrs_uploaded"], 2)
        row["total_delivered_hours"] = round(
            row["kamla_accepted_hrs"] + row["ow_accepted_hrs"], 2)
        # pending has no column (removed by Adnaan 08-15) but must never
        # go silent: with the 4 h offset a non-zero pending cohort means a
        # session stalled past settlement and this row's accepted hours
        # are UNDERSTATED — log makes the shortfall attributable
        pending = round(b["kamla_pending_hrs"] + b["ow_pending_hrs"], 2)
        if pending > 0.0:
            print(f"[sheet] PENDING COHORT: {op}/{player} has "
                  f"{pending:.2f}h still in flight at generation — "
                  f"accepted hours understated on this sheet",
                  file=sys.stderr)
        # suppress no-activity rows (the old sheet listed every player
        # ever seen)
        if any(row[c] > 0.0 for c in
               ("kamla_hrs_uploaded", "ow_hrs_uploaded",
                "kamla_accepted_hrs", "ow_accepted_hrs")) \
                or row["kamla_rejection_reasons"] \
                or row["ow_rejection_reasons"]:
            out.append(row)
    return out


def write_payment_sheet(cfg: C.Config, ledger: Ledger, day_ist: datetime,
                        bounds: tuple[str, str] | None = None,
                        counted_out: list[str] | None = None,
                        accepted_out: list[str] | None = None
                        ) -> tuple[Path, Path]:
    """CSV + MD twin under ~/hl-pipeline/reports/YYYY-MM-DD/ (F8)."""
    day = day_ist.strftime("%Y-%m-%d")
    out = cfg.reports_dir / day
    out.mkdir(parents=True, exist_ok=True)
    # keep our own copy even when the caller wants one: the '## Reject
    # detail' section below is built from EXACTLY what this sheet counted
    accepted_here: list[str] = []
    rows = build_sheet_rows(ledger, day_ist, bounds, counted_out=counted_out,
                            accepted_out=accepted_here)
    if accepted_out is not None:
        accepted_out.extend(accepted_here)
    csv_path = out / f"payment-{day}.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLS)
        w.writeheader()
        w.writerows(rows)

    md = [f"# Payment sheet — {day}", "",
          "Hours only — no money figures (R11).", ""]
    md.append("| " + " | ".join(SHEET_COLS) + " |")
    md.append("|" + "|".join("---" for _ in SHEET_COLS) + "|")
    for r in rows:
        md.append("| " + " | ".join(str(r[c]) for c in SHEET_COLS) + " |")

    ops: dict[str, dict] = {}
    num_cols = ("kamla_hrs_uploaded", "ow_hrs_uploaded",
                "kamla_accepted_hrs", "ow_accepted_hrs",
                "total_uploaded_hours", "total_delivered_hours")
    for r in rows:
        o = ops.setdefault(r["operator"], {c: 0.0 for c in num_cols})
        for c in num_cols:
            o[c] += r[c]
    md += ["", "## Per-operator rollup", "",
           "| operator | " + " | ".join(num_cols) + " |",
           "|" + "|".join("---" for _ in range(len(num_cols) + 1)) + "|"]
    for op, o in sorted(ops.items()):
        md.append("| " + op + " | "
                  + " | ".join(f"{o[c]:.2f}" for c in num_cols) + " |")

    # The detail section must describe the SAME population as the columns
    # above it. It used to window on REJECTED-transition time while the
    # columns window on upload COHORT, so the two disagreed in both
    # directions: a reject whose root uploaded in an earlier window was
    # named in a player's *_rejection_reasons cell with no evidence line
    # under it, and a reject whose root uploaded later got an evidence
    # line for a row that is not on this sheet (r-loop 6). Building it
    # from the counted set makes the mismatch structurally impossible —
    # and with the accepted-side mark, each reject is evidenced exactly
    # once, on the sheet that named it. (recal_regen_sheets solved the
    # same mismatch its own way for the two flip-time sheets; its
    # rewrite_reject_section still overrides this one.)
    rejects = []
    if accepted_here:
        q = ",".join("?" for _ in accepted_here)
        rejects = ledger.db.execute(
            f"SELECT session_id, reasons_json, dossier_path FROM sessions "
            f"WHERE state='REJECTED' AND session_id IN ({q}) "
            f"ORDER BY session_id", accepted_here).fetchall()
    md += ["", "## Reject detail", ""]
    if rejects:
        for r in rejects:
            try:
                # unfixable-only like every other reject surface (5th site,
                # d3 08-15), deduped first-seen; RAW CODES on purpose —
                # this is the ops-facing detail, not a player surface
                codes = list(dict.fromkeys(
                    x["code"] for x in
                    json.loads(r["reasons_json"] or "[]")
                    if x.get("blocking") and not x.get("fixable")))
                label = ", ".join(codes) or FIX_FAILED_MARKER
            except json.JSONDecodeError:
                label = UNREADABLE_MARKER        # unknown, never fix-failed
            md.append(f"- `{r['session_id']}`: {label} "
                      f"(evidence: {r['dossier_path'] or 'dossier pending'})")
    else:
        md.append("- none")

    inc = ledger.incomplete_list()
    md += ["", "## Incomplete folders (retried every run; >48 h "
               "highlighted, F8)", ""]
    now = datetime.now(timezone.utc)
    if inc:
        for r in inc:
            age_h = (now - datetime.fromisoformat(r["first_seen"])
                     ).total_seconds() / 3600.0
            flag = " **⚠ >48 h — needs coaching**" \
                if age_h > C.INCOMPLETE_ESCALATE_H else ""
            md.append(f"- `{r['drive_path']}` — missing "
                      f"{r['missing_json']} · first seen "
                      f"{age_h:.0f} h ago{flag}")
    else:
        md.append("- none")

    md_path = out / f"payment-{day}.md"
    md_path.write_text("\n".join(md) + "\n")
    return csv_path, md_path


# ------------------------------------------------ folder issues (d3, 08-15)

# Second daily report (Adnaan via d3, 08-15): chase-work — session folders
# missing pipeline-REQUIRED files (both rrd files exempt by the same-day
# amendment) + every path-quarantined folder. A live SNAPSHOT, deliberately
# NOT window-based: no REPORT_OFFSET_H, no cohort logic; a folder reappears
# every day until fixed. Rides a SEPARATE Telegram message + CSV: the
# payment sheet gets forwarded to operators, chase-work must forward alone.
FOLDER_ISSUE_COLS = ["problem", "operator", "player_email", "folder",
                     "detail", "first_seen_by_pipeline", "age_hours",
                     "drive_path"]


def _issue_identity(drive_path: str) -> tuple[str, str, str]:
    """(operator, player_email, folder) when the path parses as
    game/operator/player/session; else blanks + the full path as the
    folder — a depth-2 stray has no operator segment, and printing a
    guess would misdirect the chase (d3 ruling)."""
    parts = Path(drive_path).parts
    if len(parts) == 4:
        return parts[1], parts[2], parts[3]
    return "", "", drive_path


def build_folder_issues(ledger: Ledger,
                        now: datetime | None = None) -> list[dict]:
    """Rows for the folder-issues report: incomplete uploads first, then
    bad paths; oldest first within each section. first_seen is when the
    PIPELINE first saw the folder (resets on a fresh ledger), NOT upload
    time — the column name says exactly that."""
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    for r in ledger.incomplete_list():          # already first_seen-ordered
        op, player, folder = _issue_identity(r["drive_path"])
        first = r["first_seen"]
        try:
            age = (now - datetime.fromisoformat(first)
                   ).total_seconds() / 3600.0
        except ValueError:
            # a malformed timestamp must never kill the PAYMENT report,
            # which calls this for its heartbeat (folder-issues review #8)
            age = 0.0
        try:
            missing = ", ".join(json.loads(r["missing_json"] or "[]"))
        except json.JSONDecodeError:
            missing = r["missing_json"] or ""
        rows.append({"problem": "incomplete_upload", "operator": op,
                     "player_email": player, "folder": folder,
                     "detail": missing, "first_seen_by_pipeline": first,
                     "age_hours": round(age, 1),
                     "drive_path": r["drive_path"]})
    bad: list[dict] = []
    for r in ledger.by_state("QUARANTINED"):
        try:
            reasons = json.loads(r["reasons_json"] or "[]")
        except json.JSONDecodeError:
            reasons = []
        path_reason = next((x for x in reasons
                            if x.get("code") == "INT_PATH"), None)
        if path_reason is None:
            # other quarantines are pipeline trouble, not folder-naming
            # chase-work — Adnaan's ruling covers the three scan path
            # reasons, all of which carry INT_PATH
            continue
        op, player, folder = _issue_identity(r["drive_path"] or "")
        first = r["created_at"] or ""
        try:
            age = (now - datetime.fromisoformat(first)
                   ).total_seconds() / 3600.0
        except ValueError:
            age = 0.0
        bad.append({"problem": "bad_path", "operator": op,
                    "player_email": player, "folder": folder,
                    "detail": path_reason.get("evidence", ""),
                    "first_seen_by_pipeline": first,
                    "age_hours": round(age, 1),
                    "drive_path": r["drive_path"] or ""})
    bad.sort(key=lambda r: r["first_seen_by_pipeline"])
    return rows + bad


def write_folder_issues_csv(cfg: C.Config, ledger: Ledger,
                            day_ist: datetime) -> tuple[Path, list[dict]]:
    """One CSV for both lists (the problem column tells them apart) under
    the day's reports dir."""
    day = day_ist.strftime("%Y-%m-%d")
    out = cfg.reports_dir / day
    out.mkdir(parents=True, exist_ok=True)
    rows = build_folder_issues(ledger)
    path = out / f"folder-issues-{day}.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FOLDER_ISSUE_COLS)
        w.writeheader()
        w.writerows(rows)
    return path, rows


def build_folder_issues_message(rows: list[dict],
                                day_ist: datetime) -> str:
    """Two sections, ≤10 lines each + an overflow count (no silent caps —
    the csv always carries everything). Degrades to counts-only when the
    text would break Telegram's 4096-char message cap: free-text operator
    names + full paths made a realistic 10+10 message overflow, and an
    over-limit send fails EVERY tick with the marker unwritten — the
    report would never arrive at all (folder-issues review #2)."""
    inc = [r for r in rows if r["problem"] == "incomplete_upload"]
    bad = [r for r in rows if r["problem"] == "bad_path"]
    header = f"🗂 folder issues — {day_ist.strftime('%b')} {day_ist.day}"

    def _fmt(r):
        who = " / ".join(x for x in (r["operator"], r["player_email"])
                         if x)
        # a stray's folder already IS the full path — don't print it twice
        # (folder-issues review #6)
        head = f"{who} / {r['folder']}" if who else r["folder"]
        return (f"- {head} — {r['detail']} · first seen "
                f"{r['age_hours']:.0f} h ago")

    lines = [header]
    lines.append(f"incomplete uploads ({len(inc)}):")
    lines.extend(_fmt(r) for r in inc[:10])
    if len(inc) > 10:
        lines.append(f"  … and {len(inc) - 10} more in the csv")
    if not inc:
        lines.append("- none")
    lines.append(f"badly-named / misplaced folders ({len(bad)}):")
    lines.extend(_fmt(r) for r in bad[:10])
    if len(bad) > 10:
        lines.append(f"  … and {len(bad) - 10} more in the csv")
    if not bad:
        lines.append("- none")
    lines.append("📎 folder-issues csv attached")
    msg = "\n".join(lines)
    if len(msg) > 3500:            # headroom under 4096 for the TEST prefix
        msg = "\n".join([
            header,
            f"incomplete uploads: {len(inc)} · badly-named / misplaced "
            f"folders: {len(bad)}",
            "list too long for a message — full detail in the attached csv",
            "📎 folder-issues csv attached"])
    return msg
