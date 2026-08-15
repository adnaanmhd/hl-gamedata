"""Report formatting (plan §14) — per-batch Telegram topline, daily payment
message, and the payment sheet (CSV + human MD twin).

The message builders are pure formatters over computed values so the §18
byte-match acceptance can pin them exactly. Counts are sessions; hours are
always labelled; money never appears (R11).
"""
from __future__ import annotations

import csv
import json
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
}


def reason_label(code: str, daily: bool = False) -> str:
    pair = REASON_LABELS.get(code)
    if pair:
        return pair[1] if daily else pair[0]
    return code.lower().replace("_", "-")


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
        lines.append("rejects: " + " · ".join(
            f"{label} ×{n}" for label, n in d.reject_counts))
    for line in d.integrity_lines:
        lines.append(f"integrity: {line}")
    lines.append("📎 payment sheet attached")
    return "\n".join(lines)


# ------------------------------------------------------------ sheet (F8)

SHEET_COLS = ["date", "game", "operator_email", "player_email",
              "sessions_uploaded", "delivered", "rejected",
              "delivered_hours", "cumulative_hours", "top_reject_reasons"]


def _day_bounds_utc(day_ist: datetime) -> tuple[str, str]:
    start_ist = day_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_ist.astimezone(timezone.utc)
    return (start.isoformat(timespec="seconds"),
            (start + timedelta(days=1)).isoformat(timespec="seconds"))


def build_sheet_rows(ledger: Ledger, day_ist: datetime,
                     bounds: tuple[str, str] | None = None) -> list[dict]:
    """Per-player rows for the day's payment sheet. Hours only, no money
    (R11). Split children roll up under the parent's player. `bounds` is
    the (lo, hi) UTC ISO window — the caller passes the trailing-24h
    window so nothing delivered after send time ever vanishes."""
    lo, hi = bounds or _day_bounds_utc(day_ist)
    day = day_ist.strftime("%Y-%m-%d")
    q = """
    SELECT game, operator_email, player_email,
      SUM(CASE WHEN parent_id IS NULL AND created_at>=? AND created_at<?
          THEN 1 ELSE 0 END) uploaded_today,
      SUM(CASE WHEN state='DELIVERED' AND delivered_at>=? AND
          delivered_at<? THEN 1 ELSE 0 END) delivered_today,
      SUM(CASE WHEN state='REJECTED' AND updated_at>=? AND updated_at<?
          THEN 1 ELSE 0 END) rejected_today,
      COALESCE(SUM(CASE WHEN state='DELIVERED' AND delivered_at>=? AND
          delivered_at<? THEN duration_delivered_s END),0)/3600.0 h_today,
      COALESCE(SUM(CASE WHEN state='DELIVERED'
          THEN duration_delivered_s END),0)/3600.0 h_total
    FROM sessions
    WHERE player_email != '' AND state NOT IN ('DUPLICATE','QUARANTINED')
    GROUP BY game, operator_email, player_email
    ORDER BY game, operator_email, player_email
    """
    args = [lo, hi] * 4
    rows = []
    for r in ledger.db.execute(q, args).fetchall():
        rr = ledger.db.execute(
            "SELECT reasons_json FROM sessions WHERE game=? AND "
            "player_email=? AND state='REJECTED' AND updated_at>=? AND "
            "updated_at<?",
            (r["game"], r["player_email"], lo, hi)).fetchall()
        counts: dict[str, int] = {}
        for x in rr:
            try:
                for reason in json.loads(x["reasons_json"] or "[]"):
                    if reason.get("blocking"):
                        lbl = reason_label(reason["code"], daily=True)
                        counts[lbl] = counts.get(lbl, 0) + 1
            except json.JSONDecodeError:
                continue
        top = " ".join(f"{k}×{v}" for k, v in
                       sorted(counts.items(), key=lambda kv: -kv[1])[:3])
        rows.append({
            "date": day, "game": r["game"],
            "operator_email": r["operator_email"],
            "player_email": r["player_email"],
            "sessions_uploaded": r["uploaded_today"],
            "delivered": r["delivered_today"],
            "rejected": r["rejected_today"],
            "delivered_hours": round(r["h_today"], 2),
            "cumulative_hours": round(r["h_total"], 2),
            "top_reject_reasons": top})
    return rows


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

    ops: dict[tuple[str, str], dict] = {}
    for r in rows:
        k = (r["game"], r["operator_email"])
        o = ops.setdefault(k, {"delivered": 0, "rejected": 0, "h": 0.0,
                               "cum": 0.0})
        o["delivered"] += r["delivered"]
        o["rejected"] += r["rejected"]
        o["h"] += r["delivered_hours"]
        o["cum"] += r["cumulative_hours"]
    md += ["", "## Per-operator rollup", "",
           "| game | operator | delivered | rejected | hours today | "
           "cumulative |", "|---|---|---|---|---|---|"]
    for (g, op), o in sorted(ops.items()):
        md.append(f"| {g} | {op} | {o['delivered']} | {o['rejected']} | "
                  f"{o['h']:.2f} | {o['cum']:.2f} |")

    lo, hi = bounds or _day_bounds_utc(day_ist)
    rejects = ledger.db.execute(
        "SELECT session_id, reasons_json, dossier_path FROM sessions "
        "WHERE state='REJECTED' AND updated_at>=? AND updated_at<? "
        "ORDER BY session_id", (lo, hi)).fetchall()
    md += ["", "## Reject detail", ""]
    if rejects:
        for r in rejects:
            try:
                codes = [x["code"] for x in
                         json.loads(r["reasons_json"] or "[]")
                         if x.get("blocking")]
            except json.JSONDecodeError:
                codes = []
            md.append(f"- `{r['session_id']}`: {', '.join(codes) or '—'} "
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
