#!/usr/bin/env python3
"""One-shot ledger reset for the 08-16 black-frozen recalibration rebuild
(REVALIDATION_KICKOFF_PROMPT step 4). Runs ON THE VM with
PYTHONPATH=~/hl-gamedata. Refuses to act while run.lock exists or without
--yes. The ledger backup (parachute) must be taken BEFORE this runs —
pass its path via --backup so the script can verify it exists.

Resets every non-QUARANTINED/non-DUPLICATE ROOT to DISCOVERED with
supersede-style column resets (upload identity + events audit preserved,
duration_raw_s KEPT), tears down SPLIT child rows (cutter re-derives;
their events rows are kept — audit is append-only), archives existing
dossiers to history/prerecal-<stamp>/, wipes work/<sid> dirs and clears
translation_report.json entries via the locked helper.
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path.home() / "hl-gamedata"))
from pipeline import config as C                     # noqa: E402
from pipeline.ledger import Ledger                   # noqa: E402
from pipeline.validate import _locked_report_remove  # noqa: E402

KEEP_STATES = ("QUARANTINED", "DUPLICATE")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True,
                    help="path of the already-taken ledger backup")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    cfg = C.load()
    if cfg.lock_dir.exists():
        print(f"ABORT: {cfg.lock_dir} exists — a run is (or looks) alive")
        return 2
    bk = Path(args.backup)
    if not bk.exists() or bk.stat().st_size < 1024:
        print(f"ABORT: backup {bk} missing/empty — take the parachute first")
        return 2
    ledger = Ledger(cfg.ledger_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    bad_children = ledger.db.execute(
        "SELECT COUNT(*) n FROM sessions WHERE parent_id IS NOT NULL "
        "AND state IN (?,?)", KEEP_STATES).fetchone()["n"]
    if bad_children:
        print(f"ABORT: {bad_children} child row(s) in KEEP states — "
              f"design assumed none; re-decide before deleting children")
        return 2

    roots = ledger.db.execute(
        "SELECT session_id, state FROM sessions WHERE parent_id IS NULL "
        "AND state NOT IN (?,?)", KEEP_STATES).fetchall()
    children = ledger.db.execute(
        "SELECT session_id FROM sessions WHERE parent_id IS NOT NULL"
    ).fetchall()
    before = ledger.counts_by_state()
    print(json.dumps({"before": before, "roots_to_reset": len(roots),
                      "children_to_delete": len(children)}, indent=1))
    if not args.yes:
        print("dry run only (no --yes) — nothing changed")
        return 0

    all_sids = [r["session_id"] for r in roots] + \
               [c["session_id"] for c in children]

    # dossier archive (supersede pattern) BEFORE the DB flip
    archived = 0
    for sid in all_sids:
        dossier = cfg.dossiers / sid
        if not dossier.exists():
            continue
        payload = [f for f in dossier.iterdir() if f.name != "history"]
        if not payload:
            continue
        dst = dossier / "history" / f"prerecal-{stamp}"
        dst.mkdir(parents=True, exist_ok=True)
        for f in payload:
            shutil.move(str(f), dst / f.name)
        archived += 1

    cur = ledger.db
    for r in roots:
        sid = r["session_id"]
        cur.execute(
            "UPDATE sessions SET state='DISCOVERED', bin=NULL,"
            " reasons_json='[]', fix_attempts=0, duration_delivered_s=NULL,"
            " rrd_sampled=0, delivered_at=NULL, uploaded_reported_at=NULL,"
            " accepted_reported_at=NULL,"
            " updated_at=? WHERE session_id=?", (now, sid))
        cur.execute(
            "INSERT INTO events(session_id, ts, from_state, to_state,"
            " detail) VALUES(?,?,?,?,?)",
            (sid, now, r["state"], "DISCOVERED",
             "recal rebuild reset — REVALIDATION_KICKOFF_PROMPT step 4"))
    cur.execute("DELETE FROM sessions WHERE parent_id IS NOT NULL")
    ledger.db.commit()

    # transient media + shared translation report
    report = cfg.work / "translation_report.json"
    cleared = 0
    for sid in all_sids:
        wd = cfg.work / sid
        if wd.exists():
            shutil.rmtree(wd, ignore_errors=True)
        if report.exists():
            _locked_report_remove(report, sid)
            cleared += 1
    leftover_stage = list(cfg.stage.rglob("session.json"))

    after = ledger.counts_by_state()
    print(json.dumps({
        "after": after, "reset_roots": len(roots),
        "deleted_children": len(children), "dossiers_archived": archived,
        "report_entries_cleared": cleared,
        "stage_leftovers": [str(p.parent) for p in leftover_stage],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
