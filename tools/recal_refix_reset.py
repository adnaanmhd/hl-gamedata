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


def discard_split_artifacts(work: Path, sid: str) -> None:
    """Remove a sid's split manifest and rowless segment dirs.

    Per-sid teardown wiped `work/<sid>` and `work/<sid>-analysis` but left
    `work/<sid>.split-manifest.json` and the `work/<sid>-p<N>` segment dirs
    a kill-mid-cut leaves behind (r-loop 3). Because the rows are DELETEd
    immediately after, `run._sweep_terminal_work` can never reclaim them
    either — both its work-dir and its manifest branches look the sid up in
    the ledger and skip when it is gone. Segment videos are hundreds of MB
    and would sit in work/ permanently, counting against the 100 GB
    low-water that pauses ALL intake. Worse, cutter segment ids are
    deterministic (`<sid>-p<n>`), so a re-split under R1-R3 recreates the
    same names and a stale manifest could be adopted as a COMPLETED cut
    over half-written segments — the rescinded-manifest class review-r4
    #5/#19 closed everywhere else.

    The `-p<digits>` test is deliberate: it must not eat an unrelated
    directory that merely starts with the same prefix.
    """
    (work / f"{sid}.split-manifest.json").unlink(missing_ok=True)
    for seg in work.glob(f"{sid}-p*"):
        if seg.is_dir() and seg.name[len(sid) + 2:].isdigit():
            shutil.rmtree(seg, ignore_errors=True)


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
    """HOLDS the run lock for the tool's whole duration (r-loop 1): the
    old bare existence check left a TOCTOU where systemd Restart=always
    could start the continuous driver mid-reset. acquire_lock also
    reclaims a stale lock from a killed driver — exactly the flip state
    (kickoff 6a expects one after stopping hl-recal-rebuild)."""
    from pipeline.run import acquire_lock, release_lock
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--allow-reported", action="store_true",
                    help="proceed even for roots already counted on a sent "
                         "payment sheet (their uploaded_reported_at stamp is "
                         "preserved either way, so nothing is double-paid — "
                         "but the sheet of record and Drive II will disagree "
                         "until you reconcile)")
    args = ap.parse_args()
    cfg = C.load()
    if not acquire_lock(cfg):
        print("ABORT: run lock held — stop the driver "
              "(hl-continuous.service / hl-pipeline.timer) first")
        return 2
    try:
        return _locked_main(cfg, args)
    finally:
        release_lock(cfg)


def _locked_main(cfg, args) -> int:
    # PENDING-DAILY interlock (r-loop 9 #7): a durable .daily-counted.json
    # without its settled markers means an interrupted send WILL resume.
    # Tearing rows down under it makes that resume send a STALE sheet
    # crediting deleted rows, and the re-run's deterministic same-id
    # children then get counted a second time. Same doctrine as the
    # run-lock check: refuse, loudly, before any Drive/DB action.
    from pipeline.reports import pending_daily_send
    pending_day = pending_daily_send(cfg)
    if pending_day:
        print(json.dumps({
            "ABORT": "a daily send is pending resume",
            "day": pending_day,
            "why": ("reports/<day>/.daily-counted.json exists without its "
                    "settled .sent+doc_sent markers — the resume would "
                    "credit rows this tool is about to delete"),
            "how": ("let the driver finish the resume (or reconcile the "
                    "day by hand), then re-run"),
        }, indent=1))
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
    # RULING C (Adnaan 2026-08-18 at D0; r-loop 9 #1/#18): per-piece
    # payment memory replaces the r-loop-8 seal-or-refuse logic. Paid and
    # even MIXED trees PROCEED — the tool records WHICH pieces were paid
    # (ledger.paid_pieces, durable, never auto-deleted) before teardown,
    # and build_sheet_rows excludes exactly those pieces' re-delivered
    # hours while the recovered fix-failed hours stay payable. The old
    # skipped_mixed refusal is superseded (its reason — no per-node
    # fidelity survives teardown — is gone); the key stays in the output
    # JSON, now always [], for schema stability. The ONLY refusal left is
    # an already-SEALED root (r-loop-8-era tree_sealed_at): per-piece
    # fidelity never existed for old seals, the seal is NEVER overwritten
    # (#1), and a human reconciles. Production today has zero seals and
    # post-D7 nothing writes new ones.
    skipped_mixed: list[dict] = []          # superseded; kept for schema
    skipped_sealed: list[dict] = []
    kept_plan = []
    for root, kids, moves in plan:
        rootrow = ledger.get(root)
        # (sid, seconds, seg-detail) per accepted-counted DELIVERED node
        paid = []
        for s in [root] + kids:
            n = ledger.get(s)
            if n is None or n["state"] != "DELIVERED" \
                    or not n["accepted_reported_at"]:
                continue
            segrow = ledger.db.execute(
                "SELECT detail FROM events WHERE session_id=? AND "
                "detail LIKE 'split segment %' ORDER BY ts LIMIT 1",
                (s,)).fetchone()
            paid.append((s, n["duration_delivered_s"],
                         segrow["detail"] if segrow else None))
        if rootrow is not None and rootrow["tree_sealed_at"]:
            skipped_sealed.append({
                "root": root, "tree_sealed_at": rootrow["tree_sealed_at"],
                "paid_nodes": [s for s, _, _ in paid]})
            print(f"REFUSED (sealed tree): {root} carries an r-loop-8-era "
                  f"whole-tree seal ({rootrow['tree_sealed_at']}) — "
                  f"per-piece payment fidelity never existed for it; "
                  f"reconcile by hand, the seal is preserved; other "
                  f"roots proceed")
            continue
        kept_plan.append((root, kids, moves, paid))
    plan = kept_plan
    # Already-REPORTED roots need a human, not an automatic re-run. Their
    # hours are on a sheet that has been sent (and may have been paid); the
    # re-run re-delivers the same footage, and since the stamp is now
    # preserved those hours will simply not be re-counted — which is right
    # for payment, but the operator must know the sheet and the tree will
    # no longer agree until they reconcile. Refuse by default rather than
    # deciding it silently (r-loop 4).
    stamped = [p[0] for p in plan
               if (row := ledger.get(p[0])) is not None
               and row["uploaded_reported_at"]]
    if stamped and not args.allow_reported:
        print(json.dumps({
            "ABORT": "roots already counted on a sent payment sheet",
            "stamped_roots": stamped,
            "why": ("their uploaded hours are already reported; re-running "
                    "them re-delivers the same footage. The stamp is "
                    "preserved so nothing is double-paid, but the sheet of "
                    "record and Drive II will disagree until reconciled."),
            "how": "re-run with --allow-reported once you have reconciled",
        }, indent=1))
        return 2
    print(json.dumps({
        "fix_failed_rows": len(fix_failed),
        "fix_failed_hours": round(hours, 2),
        "roots": [p[0] for p in plan],
        "already_reported_roots": stamped,
        "skipped_mixed": skipped_mixed,
        "skipped_sealed": skipped_sealed,
        "paid_pieces_to_record": [
            {"root": p[0],
             "pieces": [{"sid": s, "seconds": secs}
                        for s, secs, _seg in p[3]]}
            for p in plan if p[3]],
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
    for root, kids, moves, _paid in plan:
        for sid, rd in moves:
            if not rd.startswith("humynlabs/"):
                print(f"skip move {sid}: path {rd!r} not under humynlabs/ "
                      f"(pre-rebuild path, already superseded)")
                continue
            rc, err = rclone(["lsf", f"{REMOTE}{rd}"])
            if rc in (3, 4):
                # rclone: 3/4 = directory/file not found — genuinely absent
                print(f"skip move {sid}: remote dir absent ({rd})")
                continue
            if rc != 0:
                # any OTHER non-zero rc (network outage = 1) is FAILURE,
                # not absence: skipping the compensating moveto here let
                # the teardown proceed and the re-run re-delivered a
                # duplicate to the client (r-loop 9 #19). Abort pre-DB,
                # exactly like a failed moveto.
                print(f"ABORT: rclone lsf failed for {sid} "
                      f"(rc={rc}, not an absence): {err}")
                print(f"moved so far (reconcile manually): {moved}")
                return 3
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
    sealed_roots: list = []     # always [] since ruling C — schema stability
    paid_recorded: list = []
    for root, kids, _moves, paid_nodes in plan:
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
            discard_split_artifacts(cfg.work, sid)
            report = cfg.work / "translation_report.json"
            if report.exists():
                _locked_report_remove(report, sid)
            for stage_dir in cfg.stage.glob(f"*/*/*/{sid}"):
                shutil.rmtree(stage_dir, ignore_errors=True)
        # uploaded_reported_at is DELIBERATELY preserved (r-loop 4 blocker).
        # Clearing it re-opens an already-reported cohort: the video
        # identity is unchanged (same drive_path, same md5, same
        # duration_raw_s), so these are not new uploaded hours — and that
        # stamp is the only thing stopping build_sheet_rows' LATE-ARRIVAL
        # guard from counting the root a second time. It was harmless only
        # while recal_rebuild_reset had already nulled the whole cohort;
        # once normal dailies resume (FLIP_RUNBOOK 7.3) every root carries
        # a stamp, and un-stamping would pay the same footage on two sheets
        # that never reference each other — with step 8 deleting
        # superseded-refix-*/ so the first copy is gone from Drive II.
        # (ledger.supersede clears it correctly, because there the md5 is
        # new and the hours genuinely are new.)
        # PER-PIECE PAYMENT MEMORY, not a seal (RULING C, Adnaan
        # 2026-08-18 at D0; r-loop 9 #1/#18). The r-loop-8 whole-tree seal
        # was wrong in both directions: it swallowed the recovered
        # fix-failed hours of a paid tree forever (#18), and a second pass
        # over a sealed tree recomputed paid=[] and NULLed the seal,
        # re-opening already-paid footage for a second payment (#1).
        # Instead, each accepted-counted DELIVERED piece is recorded in
        # ledger.paid_pieces (INSERT OR IGNORE — first record wins;
        # durable across passes, never auto-deleted) and build_sheet_rows
        # excludes exactly those pieces' re-delivered hours. The root's
        # own accepted_reported_at is cleared: it means only "this root
        # node's own count", and the re-run's root may itself deliver
        # genuinely new hours (if the root NODE was itself paid, its
        # memory row excludes the same-seconds re-delivery). A REJECTED
        # node carrying an accepted mark had its LABELS counted, not
        # hours — it is not a paid piece. tree_sealed_at is never written
        # here any more; the column and its reports-side honor logic stay
        # as defense-in-depth for historical rows.
        for psid, secs, seg in paid_nodes:
            ledger.record_paid_piece(root, psid, secs, seg)
        if paid_nodes:
            paid_recorded.append({
                "root": root,
                "pieces": [{"sid": s, "seconds": secs}
                           for s, secs, _seg in paid_nodes]})
        ledger.db.execute(
            "UPDATE sessions SET state='DISCOVERED', bin=NULL,"
            " reasons_json='[]', fix_attempts=0, duration_delivered_s=NULL,"
            " rrd_sampled=0, delivered_at=NULL, accepted_reported_at=NULL,"
            " tree_sealed_at=NULL, updated_at=? WHERE session_id=?",
            (now, root))
        paid_note = ""
        if paid_nodes:
            paid_note = ("; paid pieces remembered: "
                         + json.dumps([{"sid": s, "seconds": secs}
                                       for s, secs, _seg in paid_nodes]))
        ledger.db.execute(
            "INSERT INTO events(session_id, ts, from_state, to_state,"
            " detail) VALUES(?,?,?,?,?)",
            (root, now, row["state"], "DISCOVERED",
             f"fix-failed selective re-run under 08-16 tolerances; "
             f"{len(kids)} subtree rows torn down; delivered segments "
             f"moved to superseded-refix-{stamp}/" + paid_note))
        for sid in kids:
            ledger.db.execute("DELETE FROM sessions WHERE session_id=?",
                              (sid,))
        reset += 1
        deleted += len(kids)
    ledger.db.commit()
    print(json.dumps({"roots_reset": reset, "subtree_rows_deleted": deleted,
                      # ruling C: no whole-tree seals are written any more
                      # (sealed_roots stays [] for schema stability); the
                      # recorded pieces' re-delivered hours are excluded
                      # by build_sheet_rows via the paid_pieces memory,
                      # while recovered fix-failed hours stay payable
                      "sealed_roots": sealed_roots,
                      "paid_pieces_recorded": paid_recorded,
                      "skipped_mixed": skipped_mixed,
                      "skipped_sealed": skipped_sealed,
                      "drive_dirs_moved": moved,
                      "superseded_refix_prefix":
                          f"superseded-refix-{stamp}/",
                      "states": ledger.counts_by_state()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
