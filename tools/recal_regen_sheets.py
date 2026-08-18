#!/usr/bin/env python3
"""Step-6 payment-sheet regeneration — v2 after the 08-16 deep review
(the regen cluster: empty-SUPERSEDES re-run blocker, anchor rewind,
global abort gate, cohort-blind reject detail, marker mtime evidence).

Design:
- PREVIEW (default, no --send): computes both days, writes preview-*.csv/
  .md beside the real sheets, prints totals. ZERO side effects — no
  stamps, no anchor, no markers, no real-file overwrite.
- SEND (--send), per day in order, with a durable resume record:
    skip day if reports/<day>/.regen-v2-done exists
    generate sheet + cohort-keyed reject detail -> real paths
    write .regen-v2-counted.json (the exact counted sids) BEFORE any
      side effect; a re-run after a crash reuses it verbatim — the sheet
      can never be regenerated post-stamp (the empty-sheet blocker)
    telegram message -> document (abort rc=3 BEFORE stamps on failure;
      re-run resends — duplicate message is the accepted cost)
    mark_uploads_reported(sids from the record) -> anchor=hi ->
      marker touch ONLY if missing (mtime evidence preserved) ->
      .regen-v2-done
- Abort gate is COHORT-SCOPED: only non-terminal trees whose TOP-ROOT
  upload time < hi16 block (162+ post-hi16 ride-alongs are structurally
  uncountable by these windows and must not block payment).
- Stray-stamp pre-check: any stamped root not accounted for by our own
  resume records aborts loudly (a stray daily send would silently empty
  the superseding sheets).
- Final invariant: anchor == HI16 once both days are done.

Windows of record (recovered 08-16; sources: GCS mirror of the anchor +
live anchor, cross-checked against .sent mtimes):
  08-15: (2026-08-14T06:45:22+00:00, 2026-08-15T06:45:22+00:00]
  08-16: (2026-08-15T06:45:22+00:00, 2026-08-16T05:32:50+00:00]
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / "hl-gamedata"))
from pipeline import config as C                    # noqa: E402
from pipeline import reports, telegram              # noqa: E402
from pipeline.ledger import Ledger                  # noqa: E402

WINDOWS = [
    ("2026-08-15", "2026-08-14T06:45:22+00:00", "2026-08-15T06:45:22+00:00"),
    ("2026-08-16", "2026-08-15T06:45:22+00:00", "2026-08-16T05:32:50+00:00"),
]
HI16 = WINDOWS[-1][2]
TERMINAL = ("DELIVERED", "REJECTED", "SPLIT", "QUARANTINED", "DUPLICATE")


def top_root_id(ledger, sid):
    """The top-level recording a row belongs to (itself if it is one).

    build_sheet_rows counts and labels ROOTS, so membership in its
    counted_out must be tested against the root, never against a -pN
    child's own id (r-loop 5).
    """
    for _ in range(10):
        row = ledger.get(sid)
        if row is None or not row["parent_id"]:
            return sid
        sid = row["parent_id"]
    return sid


def top_root_ctime(ledger, sid, cache):
    """Upload time of the top-level recording a row belongs to.
    created_at fallback for blank/unparseable drive_ctime mirrors
    build_sheet_rows' own windowing fallback — without it the gate and the
    reject detail go blind to a root the generated sheet includes
    (r-loop 1)."""
    seen = []
    for _ in range(10):
        if sid in cache:
            break
        row = ledger.get(sid)
        if row is None:
            cache[sid] = None
            break
        if not row["parent_id"]:
            cache[sid] = (reports._parse_ts(row["drive_ctime"])
                          or reports._parse_ts(row["created_at"]))
            break
        seen.append(sid)
        sid = row["parent_id"]
    for s in seen:
        cache[s] = cache.get(sid)
    return cache.get(sid)


def cohort_reject_detail(ledger, lo_dt, hi_dt, cache, counted=None):
    """Rejected rows of trees whose ROOT uploaded in [lo, hi), PLUS the
    late arrivals the sheet's own columns counted. The stock section
    windows on REJECTED-transition time, which is rebuild-time for every
    row now (verified) and would print '- none'.

    `counted` closes an r-loop-5 mismatch: build_sheet_rows has a
    LATE-ARRIVAL guard that deliberately counts unstamped countable roots
    with `up < lo_dt` into the current window — their hours AND their
    *_rejection_reasons labels both land in the sheet — while this
    function dropped them on a strict [lo, hi) test. The 08-15 sheet is
    the exposed one: it is the first sheet of record (no anchor, so lo is
    the 24h fallback), and recal_rebuild_reset nulled every pre-rebuild
    stamp, so every earlier root enters through that guard. The published,
    VOID-superseding payment document then named a reject class in a
    player's row with no matching evidence line — and if a pre-window
    reject was the only one that day, the section read "- none in this
    upload cohort" above a table showing unpaid hours.
    """
    counted = set(counted or ())
    lines = []
    for r in ledger.db.execute(
            "SELECT session_id, reasons_json, dossier_path FROM sessions "
            "WHERE state='REJECTED' ORDER BY session_id"):
        ct = top_root_ctime(ledger, r["session_id"], cache)
        in_window = ct is not None and lo_dt <= ct < hi_dt
        if not in_window and (not counted or top_root_id(
                ledger, r["session_id"]) not in counted):
            continue
        try:
            reasons = json.loads(r["reasons_json"] or "[]")
        except json.JSONDecodeError:
            reasons = []
        blocking = [x for x in reasons if x.get("blocking")]
        unfix = [x["code"] for x in blocking if not x.get("fixable")]
        label = " + ".join(dict.fromkeys(unfix)) if unfix else "fix-failed"
        # evidence must come from the reason that produced the LABEL. It
        # was taken unconditionally from blocking[0] — frequently a
        # *fixable* reason — so the superseding payment document read
        # "CNT_CHAT_PII — 7.2s static window at 412s", pairing an unfixable
        # code with an unrelated reason's evidence. R1-R3 make multi-reason
        # blocking sets the normal shape, since sub-5s spans now stay as
        # gate reasons on the parent instead of becoming child rows
        # (r-loop 4). This is the player-facing explanation of why hours
        # were not paid, and the record a dispute is argued from.
        src = next((x for x in blocking if not x.get("fixable")),
                   blocking[0] if blocking else None)
        ev = (src.get("evidence", "")[:90] if src else "")
        lines.append(f"- `{r['session_id']}`: {label}"
                     + (f" — {ev}" if ev else "")
                     + (f" · dossier: {r['dossier_path']}"
                        if r["dossier_path"] else ""))
    return lines or ["- none in this upload cohort"]


def rewrite_reject_section(md_path: Path, lines: list[str]) -> None:
    text = md_path.read_text()
    head = "## Reject detail"
    i = text.find(head)
    if i < 0:
        md_path.write_text(text + f"\n{head}\n\n" + "\n".join(lines) + "\n")
        return
    j = text.find("\n## ", i + len(head))
    tail = text[j:] if j >= 0 else "\n"
    body = (text[:i] + head + "\n\n(cohort view: rejected rows of "
            "recordings UPLOADED in this window — transition-time view is "
            "meaningless post-rebuild)\n\n" + "\n".join(lines) + "\n" + tail)
    md_path.write_text(body)


def main() -> int:
    """HOLDS the run lock for the tool's whole duration (r-loop 1): the
    old bare existence check left a TOCTOU where systemd Restart=always
    could start the continuous driver mid-regeneration. acquire_lock also
    reclaims a stale lock from a killed driver — exactly the flip state."""
    from pipeline.run import acquire_lock, release_lock
    cfg = C.load()
    if not acquire_lock(cfg):
        print("ABORT: run lock held — stop the driver "
              "(hl-continuous.service / hl-pipeline.timer) first")
        return 2
    try:
        return _locked_main(cfg)
    finally:
        release_lock(cfg)


def _locked_main(cfg) -> int:
    send = "--send" in sys.argv[1:]
    ledger = Ledger(cfg.ledger_path)
    cache: dict = {}
    hi16_dt = reports._parse_ts(HI16)

    # cohort-scoped terminality gate
    blockers = []
    ride_alongs = 0
    for r in ledger.db.execute(
            "SELECT session_id, state FROM sessions WHERE state NOT IN "
            "(?,?,?,?,?)", TERMINAL):
        ct = top_root_ctime(ledger, r["session_id"], cache)
        if ct is not None and ct < hi16_dt:
            blockers.append((r["session_id"], r["state"]))
        else:
            ride_alongs += 1
    if blockers:
        print("ABORT: cohort rows not terminal (run the driver until "
              "these land):")
        for sid, st in blockers[:20]:
            print(f"  {st:<12} {sid}")
        return 2
    if ride_alongs:
        print(f"note: {ride_alongs} non-terminal ride-along row(s) with "
              f"upload >= hi16 — structurally outside both windows, "
              f"ignored")

    # stray-stamp pre-check — COHORT-SCOPED (r-loop 1): only stamped roots
    # whose upload time falls inside our windows can empty the superseding
    # sheets. Post-hi16 roots are stamped by every NORMAL daily send once
    # continuous operation resumes — aborting on those deadlocked the
    # endgame forever. A stamped COHORT root, though, means a daily send
    # already counted (and misattributed) rebuild-cohort hours: that needs
    # a human reconcile, never an auto-unstamp.
    recorded: set = set()
    for day, _, _ in WINDOWS:
        p = cfg.reports_dir / day / ".regen-v2-counted.json"
        if p.exists():
            recorded |= set(json.loads(p.read_text()))
    stray = []
    residual = []
    for r in ledger.db.execute(
            "SELECT session_id, state FROM sessions WHERE parent_id IS NULL "
            "AND uploaded_reported_at IS NOT NULL"):
        if r["session_id"] in recorded:
            continue
        # recal_rebuild_reset DELIBERATELY preserves uploaded_reported_at
        # for QUARANTINED/DUPLICATE roots (its KEEP_STATES), so a root that
        # carried a PRE-rebuild stamp and is now in one of those states
        # still has it. Such stamps are reachable: build_sheet_rows stamps
        # every in-window countable root regardless of state, and only
        # skips DUPLICATE/QUARANTINED as evaluated AT GENERATION TIME — so
        # a root counted and stamped while still INGESTED/VALIDATING, then
        # quarantined by a validation crash, is exactly this shape. They
        # are structurally uncountable by build_sheet_rows now, so their
        # stamps cannot empty a superseding sheet. Aborting on them
        # hard-blocked the payment endgame with no override flag, and
        # named the wrong subsystem: the CONT_DAILY_REPORTS interlock was
        # never breached (r-loop 5).
        if r["state"] in ("QUARANTINED", "DUPLICATE"):
            residual.append(r["session_id"])
            continue
        ct = top_root_ctime(ledger, r["session_id"], cache)
        if ct is not None and ct >= hi16_dt:
            continue                     # post-cohort: normal daily stamp
        stray.append(r["session_id"])
    if residual:
        print(f"note: {len(residual)} pre-rebuild residual stamp(s) on "
              f"QUARANTINED/DUPLICATE root(s) ignored — recal_rebuild_reset "
              f"preserves those by design and build_sheet_rows cannot count "
              f"them: {', '.join(sorted(residual)[:5])}"
              + (" ..." if len(residual) > 5 else ""))
    if stray:
        print(f"ABORT: {len(stray)} COHORT root(s) already stamped outside "
              f"our resume records — a daily send counted rebuild-cohort "
              f"hours into the wrong sheet (CONT_DAILY_REPORTS interlock "
              f"breached?). Reconcile by hand before regenerating: "
              f"{stray[:10]}")
        return 2

    anchor = cfg.reports_dir / ".last_daily_sent"
    out = []
    # PREVIEW runs against a scratch COPY of the ledger and a scratch
    # reports dir (r-loop 3). Two defects, one cause — preview skipped the
    # stamping that --send does BETWEEN the two days:
    #   1. Day 2 double-counted day 1. With nothing stamped, every root the
    #      08-15 sheet counted was still `uploaded_reported_at IS NULL` when
    #      08-16 was built, so it re-entered through the LATE-ARRIVAL guard
    #      and was counted twice. Verified by simulation: preview gave
    #      08-16 totals of 3.0 h where --send gives 2.0 h.
    #   2. write_payment_sheet writes the REAL payment-<day>.csv/.md first
    #      and only then copied them to preview-*, so a preview overwrote
    #      the sheets of record (which hl-backup then mirrors to GCS) with
    #      the inflated numbers, and the old comment claiming the result was
    #      "identical to what --send would write" was false for day 2.
    # A scratch ledger lets the preview stamp exactly as --send does, so
    # the human gate in FLIP_RUNBOOK 7.2 reads the real thing.
    scratch_dir = None
    if not send:
        scratch_dir = Path(tempfile.mkdtemp(prefix="regen-preview-"))
        shadow_home = scratch_dir / "home"
        (shadow_home / "reports").mkdir(parents=True, exist_ok=True)
        src_db = ledger.db
        dst_path = scratch_dir / "ledger.db"
        dst = sqlite3.connect(dst_path)
        with dst:
            src_db.backup(dst)
        dst.close()
        real_cfg, real_ledger = cfg, ledger
        cfg = replace(cfg, home=shadow_home)
        ledger = Ledger(dst_path)
    # markers, resume records and the published preview files always live in
    # the REAL reports dir; only the generated sheets go to the shadow
    pub_reports = (real_cfg if not send else cfg).reports_dir
    for day, lo, hi in WINDOWS:
        day_dir = cfg.reports_dir / day
        pub_dir = pub_reports / day
        done = pub_dir / ".regen-v2-done"
        counted_file = pub_dir / ".regen-v2-counted.json"
        if done.exists():
            # skip in BOTH modes: post-stamp, build_sheet_rows excludes
            # every stamped root, so a "read-only" preview would rewrite
            # the real payment-<day>.csv/.md as an EMPTY sheet over the
            # sheets of record (r-loop 2 — the skip was send-only)
            out.append({"day": day, "skipped": "already sent (.regen-v2-done)"
                        + ("" if send else " — preview refuses to rewrite "
                                          "post-stamp sheets")})
            continue
        day_dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=C.IST)
        lo_dt, hi_dt = reports._parse_ts(lo), reports._parse_ts(hi)

        if send and counted_file.exists():
            counted = json.loads(counted_file.read_text())
            csv_path = day_dir / f"payment-{day}.csv"
            md_path = day_dir / f"payment-{day}.md"
            resumed = True
        else:
            counted = []
            csv_path, md_path = reports.write_payment_sheet(
                cfg, ledger, day_dt, bounds=(lo, hi), counted_out=counted)
            # pass the EXACT set the sheet counted, so the '## Reject
            # detail' section describes the same population as the columns
            # above it — late arrivals included (r-loop 5)
            rewrite_reject_section(
                md_path, cohort_reject_detail(ledger, lo_dt, hi_dt, cache,
                                              counted=counted))
            resumed = False
            if not send:
                # publish ONLY the preview twins into the real reports dir;
                # payment-<day>.csv/.md were written to the shadow and the
                # sheets of record are untouched
                pub_dir.mkdir(parents=True, exist_ok=True)
                pv_csv = pub_dir / f"preview-payment-{day}.csv"
                pv_md = pub_dir / f"preview-payment-{day}.md"
                pv_csv.write_bytes(csv_path.read_bytes())
                pv_md.write_bytes(md_path.read_bytes())
                # stamp the SHADOW ledger exactly as --send stamps the real
                # one, so the next day's sheet sees this day's roots as
                # already counted instead of re-counting them as late
                # arrivals
                reports.mark_uploads_reported(ledger, lo, hi, sids=counted)
                csv_path, md_path = pv_csv, pv_md
            else:
                tmp = counted_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(counted))
                tmp.replace(counted_file)

        if send:
            if not counted_file.exists():
                tmp = counted_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(counted))
                tmp.replace(counted_file)
            caption = (f"SUPERSEDES {day} sheet — methodology v2 "
                       f"(black-frozen recalibration). Old {day} sheet is "
                       f"VOID for payment; pay per this sheet.")
            try:
                telegram.send_message(cfg, f"📋 {caption}")
                telegram.send_document(cfg, csv_path, caption=caption)
            except telegram.TelegramError as e:
                print(f"ABORT rc=3 before stamping: telegram failed for "
                      f"{day}: {e} — re-run to resend (sheet + counted "
                      f"record are durable; duplicate message is the "
                      f"accepted cost)")
                return 3
            stamped = reports.mark_uploads_reported(ledger, lo, hi,
                                                    sids=counted)
            anchor.write_text(hi)
            marker = day_dir / ".sent"
            if not marker.exists():        # preserve mtime evidence
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
            done.write_text(datetime.now(C.IST).isoformat())
            out.append({"day": day, "lo": lo, "hi": hi,
                        "counted": len(counted), "stamped": stamped,
                        "resumed": resumed, "csv": str(csv_path)})
        else:
            out.append({"day": day, "lo": lo, "hi": hi,
                        "counted": len(counted), "preview": True,
                        "csv": str(csv_path)})

    result = {"mode": "send" if send else "preview", "days": out}
    if send:
        result["anchor"] = anchor.read_text().strip()
        result["anchor_ok"] = result["anchor"] == HI16
    else:
        ledger.close()
        cfg, ledger = real_cfg, real_ledger
        shutil.rmtree(scratch_dir, ignore_errors=True)
        result["note"] = ("preview built against a scratch ledger copy with "
                          "inter-day stamping applied — the sheets of record "
                          "were NOT written; read preview-payment-*.csv/.md")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
