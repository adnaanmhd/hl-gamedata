#!/usr/bin/env python3
"""Selective fix-failed re-run reset — v2 after the 08-16 deep review
(16 confirmed findings; this file addresses the refix cluster):

- Fix-failed rows resolve to the TOP-LEVEL recording root by walking
  parent_id to NULL (splits nest — grandchildren exist in the live
  ledger; one-hop resolution reset a mid-level SPLIT child, which then
  degenerated to DUPLICATE on re-download and silently lost the footage).
- Teardown removes the ENTIRE subtree (recursive), not direct children
  (orphan grandchild rows got adopted later with stale state).
- DELIVERED/UPLOADED/PACKAGED descendants get Drive II compensation
  BEFORE any DB change: their remote dirs (authoritative path = latest
  UPLOADED event detail 'verified at <path>') are rclone-moved into
  superseded-refix-<stamp>/ at the drive-deliver: top level — otherwise
  the re-run re-delivers the same content under a new date folder and
  the client sees it twice, with the first copy orphaned. Local staging
  leftovers for subtree sids are wiped (staged_date pinning).
- All rclone moves happen first; any hard move failure aborts before the
  ledger is touched.

Run ON THE VM with the pipeline paused (no run.lock). --yes to execute.
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path.home() / "hl-gamedata"))
from pipeline import config as C                     # noqa: E402
from pipeline.ledger import Ledger                   # noqa: E402
from pipeline.validate import _locked_report_remove  # noqa: E402

REMOTE = "drive-deliver:"
DELIVERED_ISH = ("PACKAGED", "UPLOADED", "DELIVERED")


def top_root(ledger: Ledger, sid: str) -> str:
    for _ in range(10):
        row = ledger.get(sid)
        if row is None or not row["parent_id"]:
            return sid
        sid = row["parent_id"]
    raise RuntimeError(f"parent chain too deep at {sid}")


def subtree(ledger: Ledger, root: str) -> list[str]:
    """All descendants of root (BFS), root NOT included."""
    out: list[str] = []
    frontier = [root]
    while frontier:
        nxt: list[str] = []
        for p in frontier:
            for r in ledger.db.execute(
                    "SELECT session_id FROM sessions WHERE parent_id=?",
                    (p,)):
                out.append(r["session_id"])
                nxt.append(r["session_id"])
        frontier = nxt
    return out


def remote_dir_of(ledger: Ledger, sid: str) -> str | None:
    """Authoritative delivered path: latest UPLOADED event detail."""
    row = ledger.db.execute(
        "SELECT detail FROM events WHERE session_id=? AND "
        "to_state='UPLOADED' AND detail LIKE 'verified at %' "
        "ORDER BY ts DESC LIMIT 1", (sid,)).fetchone()
    if row is None:
        return None
    return row["detail"][len("verified at "):].strip()


def rclone(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(["rclone"] + args, capture_output=True, text=True,
                       timeout=300)
    return p.returncode, (p.stderr or p.stdout)[-300:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    cfg = C.load()
    if cfg.lock_dir.exists():
        print(f"ABORT: {cfg.lock_dir} exists — pipeline not paused")
        return 2
    ledger = Ledger(cfg.ledger_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    fix_failed = []
    for r in ledger.db.execute(
            "SELECT session_id, parent_id, reasons_json, "
            "COALESCE(duration_raw_s,0) d FROM sessions "
            "WHERE state='REJECTED'"):
        try:
            reasons = json.loads(r["reasons_json"] or "[]")
        except json.JSONDecodeError:
            continue
        blocking = [x for x in reasons if x.get("blocking")]
        if blocking and all(x.get("fixable") for x in blocking):
            fix_failed.append(r)

    roots = sorted({top_root(ledger, r["session_id"]) for r in fix_failed})
    roots = [s for s in roots
             if (row := ledger.get(s)) is not None
             and row["state"] not in ("QUARANTINED", "DUPLICATE")]
    hours = sum(r["d"] for r in fix_failed) / 3600.0

    # plan the Drive II compensation before any action
    plan = []          # (root, [subtree sids], [(sid, remote_dir)])
    for root in roots:
        kids = subtree(ledger, root)
        moves = []
        for sid in [root] + kids:
            row = ledger.get(sid)
            if row is not None and row["state"] in DELIVERED_ISH:
                rd = remote_dir_of(ledger, sid)
                if rd:
                    moves.append((sid, rd))
                else:
                    print(f"WARN: {sid} state={row['state']} but no "
                          f"UPLOADED event — verify manually")
        plan.append((root, kids, moves))
    print(json.dumps({
        "fix_failed_rows": len(fix_failed),
        "fix_failed_hours": round(hours, 2),
        "roots": [p[0] for p in plan],
        "subtree_rows": sum(len(p[1]) for p in plan),
        "drive_moves": [m for p in plan for m in
                        [f"{s} -> {d}" for s, d in p[2]]],
    }, indent=1))
    if not args.yes:
        print("dry run only (no --yes) — nothing changed")
        return 0
    if not roots:
        print("nothing to reset")
        return 0

    # 1) Drive II moves FIRST — abort before DB on any hard failure
    moved = []
    for root, kids, moves in plan:
        for sid, rd in moves:
            if not rd.startswith("humynlabs/"):
                print(f"skip move {sid}: path {rd!r} not under humynlabs/ "
                      f"(pre-rebuild path, already superseded)")
                continue
            rc, _ = rclone(["lsf", f"{REMOTE}{rd}"])
            if rc != 0:
                print(f"skip move {sid}: remote dir absent ({rd})")
                continue
            dest = f"superseded-refix-{stamp}/{rd[len('humynlabs/'):]}"
            rc, err = rclone(["moveto", f"{REMOTE}{rd}", f"{REMOTE}{dest}"])
            if rc != 0:
                print(f"ABORT: rclone moveto failed for {sid}: {err}")
                print(f"moved so far (reconcile manually): {moved}")
                return 3
            moved.append(f"{rd} -> {dest}")

    # 2) filesystem + DB, per root
    reset = 0
    deleted = 0
    for root, kids, _moves in plan:
        row = ledger.get(root)
        for sid in [root] + kids:
            dossier = cfg.dossiers / sid
            if dossier.exists():
                payload = [f for f in dossier.iterdir()
                           if f.name != "history"]
                if payload:
                    dst = dossier / "history" / f"refix-{stamp}"
                    dst.mkdir(parents=True, exist_ok=True)
                    for f in payload:
                        shutil.move(str(f), dst / f.name)
            shutil.rmtree(cfg.work / sid, ignore_errors=True)
            shutil.rmtree(cfg.work / f"{sid}-analysis", ignore_errors=True)
            report = cfg.work / "translation_report.json"
            if report.exists():
                _locked_report_remove(report, sid)
            for stage_dir in cfg.stage.glob(f"*/*/*/{sid}"):
                shutil.rmtree(stage_dir, ignore_errors=True)
        ledger.db.execute(
            "UPDATE sessions SET state='DISCOVERED', bin=NULL,"
            " reasons_json='[]', fix_attempts=0, duration_delivered_s=NULL,"
            " rrd_sampled=0, delivered_at=NULL, uploaded_reported_at=NULL,"
            " updated_at=? WHERE session_id=?", (now, root))
        ledger.db.execute(
            "INSERT INTO events(session_id, ts, from_state, to_state,"
            " detail) VALUES(?,?,?,?,?)",
            (root, now, row["state"], "DISCOVERED",
             f"fix-failed selective re-run under 08-16 tolerances; "
             f"{len(kids)} subtree rows torn down; delivered segments "
             f"moved to superseded-refix-{stamp}/"))
        for sid in kids:
            ledger.db.execute("DELETE FROM sessions WHERE session_id=?",
                              (sid,))
        reset += 1
        deleted += len(kids)
    ledger.db.commit()
    print(json.dumps({"roots_reset": reset, "subtree_rows_deleted": deleted,
                      "drive_dirs_moved": moved,
                      "superseded_refix_prefix":
                          f"superseded-refix-{stamp}/",
                      "states": ledger.counts_by_state()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
