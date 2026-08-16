#!/usr/bin/env python3
"""Step-6 payment-sheet regeneration under the post-rebuild verdicts
(REVALIDATION_KICKOFF_PROMPT step 6). Runs ON THE VM with
PYTHONPATH=~/hl-gamedata, ONLY after every non-QUARANTINED/DUPLICATE root
is terminal (DELIVERED/REJECTED/SPLIT with terminal children) — the
caller verifies that first.

Original window boundaries of record (recovered 08-16, sources: GCS
backup of reports/.last_daily_sent + live anchor, cross-checked against
.sent marker mtimes):
  08-15 sheet: (2026-08-14T06:45:22+00:00, 2026-08-15T06:45:22+00:00]
  08-16 sheet: (2026-08-15T06:45:22+00:00, 2026-08-16T05:32:50+00:00]

Per day, in chronological order, mirroring the production r5 discipline
stamps -> anchor -> marker -> send (crash anywhere re-runs to an
identical-or-smaller sheet, never double-counts):
  write_payment_sheet(bounds) -> mark_uploads_reported(sids=counted)
  -> anchor=hi -> marker touch -> Telegram "SUPERSEDES ..." + attachment.
Sheets overwrite the old files in place (same path = purge of old VM
copies); GCS mirror re-sync + NOTE_FOR_D3.md happen outside this script.
"""
import json
import sys
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


def main() -> int:
    send = "--send" in sys.argv[1:]
    cfg = C.load()
    ledger = Ledger(cfg.ledger_path)
    nonterminal = ledger.db.execute(
        "SELECT COUNT(*) n FROM sessions WHERE state NOT IN "
        "('DELIVERED','REJECTED','SPLIT','QUARANTINED','DUPLICATE')"
    ).fetchone()["n"]
    if nonterminal:
        print(f"ABORT: {nonterminal} session(s) not terminal — "
              f"run the driver to completion first")
        return 2
    anchor = cfg.reports_dir / ".last_daily_sent"
    out = []
    for day, lo, hi in WINDOWS:
        day_dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=C.IST)
        counted: list[str] = []
        csv_path, md_path = reports.write_payment_sheet(
            cfg, ledger, day_dt, bounds=(lo, hi), counted_out=counted)
        stamped = reports.mark_uploads_reported(ledger, lo, hi,
                                                sids=counted)
        anchor.write_text(hi)
        marker = cfg.reports_dir / day / ".sent"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        caption = (f"SUPERSEDES {day} sheet — methodology v2 "
                   f"(black-frozen recalibration). Old sheet is VOID for "
                   f"payment; pay per this one.")
        if send:
            telegram.send_message(cfg, f"📋 {caption}")
            telegram.send_document(cfg, csv_path, caption=caption)
        out.append({"day": day, "lo": lo, "hi": hi, "counted": len(counted),
                    "stamped": stamped, "csv": str(csv_path),
                    "sent": send})
    print(json.dumps({"regenerated": out,
                      "final_anchor": anchor.read_text()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
