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
    lines.append("📎 payment sheet attached")
    return "\n".join(lines)


# ------------------------------------------------------------ sheet (F8)

# Schema per Adnaan (08-15, via the d3 session; respec same day): one row
# per (operator, player) spanning both games — three kamla_/ow_ pairs
# after the identity columns. No game column, no counts, no cumulative.
# v4 (08-15): COHORT accounting + the pending pair. A player's footage is
# judged in the window it was UPLOADED in — accepted/pending/rejected are
# attributed back to the root upload's window regardless of when the
# outcome happened (delivery-time keying made rows internally
# incomparable: "1.29 uploaded / 0.44 accepted" read as rejection when it
# was just the pipeline's 1-2 h latency). Total column names are Adnaan's
# EXACT strings — "hours" vs "hrs" and "delivered" vs "accepted"
# deliberately NOT normalized (flagged; one-line change here if he
# harmonizes).
SHEET_COLS = ["date", "operator", "player_email",
              "kamla_hrs_uploaded", "ow_hrs_uploaded",
              "kamla_accepted_hrs", "ow_accepted_hrs",
              "kamla_pending_hrs", "ow_pending_hrs",
              "kamla_rejection_reasons", "ow_rejection_reasons",
              "total_uploaded_hours", "total_delivered_hours"]


def _day_bounds_utc(day_ist: datetime) -> tuple[str, str]:
    start_ist = day_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_ist.astimezone(timezone.utc)
    return (start.isoformat(timespec="seconds"),
            (start + timedelta(days=1)).isoformat(timespec="seconds"))


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


def build_sheet_rows(ledger: Ledger, day_ist: datetime,
                     bounds: tuple[str, str] | None = None) -> list[dict]:
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
    money (R11)."""
    lo, hi = bounds or _day_bounds_utc(day_ist)
    lo_dt, hi_dt = _parse_ts(lo), _parse_ts(hi)
    day = day_ist.strftime("%Y-%m-%d")
    rows = ledger.db.execute(
        "SELECT session_id, game, operator_email, player_email, parent_id,"
        " state, drive_ctime, created_at, duration_raw_s,"
        " duration_delivered_s, reasons_json"
        " FROM sessions WHERE player_email != ''").fetchall()
    children: dict[str, list] = {}
    for r in rows:
        if r["parent_id"]:
            children.setdefault(r["parent_id"], []).append(r)
    per_key: dict[tuple[str, str], dict] = {}

    def bucket(r):
        k = (r["operator_email"] or "", r["player_email"])
        return per_key.setdefault(k, {
            "kamla_hrs_uploaded": 0.0, "ow_hrs_uploaded": 0.0,
            "kamla_accepted_hrs": 0.0, "ow_accepted_hrs": 0.0,
            "kamla_pending_hrs": 0.0, "ow_pending_hrs": 0.0,
            "kamla_rej": [], "ow_rej": []})

    game_col = {"kamla": "kamla", "outer_wilds": "ow"}
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
        if up is None or lo_dt is None or hi_dt is None \
                or not (lo_dt <= up < hi_dt):
            continue
        g_root = game_col.get(root["game"] or "")
        if g_root is not None:
            bucket(root)[f"{g_root}_hrs_uploaded"] += \
                (root["duration_raw_s"] or 0.0) / 3600.0
        # recursive walk of the whole tree (root included)
        stack = [root]
        while stack:
            n = stack.pop()
            stack.extend(children.get(n["session_id"], []))
            g = game_col.get(n["game"] or "")
            if g is None:
                continue     # never dump an unknown game into a column
            if n["state"] == "DELIVERED":
                bucket(n)[f"{g}_accepted_hrs"] += \
                    (n["duration_delivered_s"] or 0.0) / 3600.0
            elif n["state"] == "REJECTED":
                try:
                    labels = session_reject_labels(
                        json.loads(n["reasons_json"] or "[]"), daily=True)
                except json.JSONDecodeError:
                    labels = [UNREADABLE_MARKER]
                bucket(n)[f"{g}_rej"].append(labels)
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
            "kamla_pending_hrs": round(b["kamla_pending_hrs"], 2),
            "ow_pending_hrs": round(b["ow_pending_hrs"], 2),
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
        # suppress no-activity rows (the old sheet listed every player
        # ever seen); pending counts as activity
        if any(row[c] > 0.0 for c in
               ("kamla_hrs_uploaded", "ow_hrs_uploaded",
                "kamla_accepted_hrs", "ow_accepted_hrs",
                "kamla_pending_hrs", "ow_pending_hrs")) \
                or row["kamla_rejection_reasons"] \
                or row["ow_rejection_reasons"]:
            out.append(row)
    return out


def write_payment_sheet(cfg: C.Config, ledger: Ledger, day_ist: datetime,
                        bounds: tuple[str, str] | None = None
                        ) -> tuple[Path, Path]:
    """CSV + MD twin under ~/hl-pipeline/reports/YYYY-MM-DD/ (F8)."""
    day = day_ist.strftime("%Y-%m-%d")
    out = cfg.reports_dir / day
    out.mkdir(parents=True, exist_ok=True)
    rows = build_sheet_rows(ledger, day_ist, bounds)
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
                "kamla_pending_hrs", "ow_pending_hrs",
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

    lo, hi = bounds or _day_bounds_utc(day_ist)
    rejects = ledger.db.execute(
        f"SELECT session_id, reasons_json, dossier_path FROM sessions "
        f"WHERE state='REJECTED' AND {REJECT_TS}>=? AND {REJECT_TS}<? "
        f"ORDER BY session_id", (lo, hi)).fetchall()
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
