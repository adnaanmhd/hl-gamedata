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

Payment evidence (r13 #8, ruling C extended here): REFUSES rc=2 when
any in-scope root carries uploaded_reported_at or any DELIVERED node
carries accepted_reported_at, unless --allow-reported. Under the flag,
uploaded stamps are PRESERVED through the reset (nothing double-pays
via the late-arrival guard) and every accepted-stamped DELIVERED node
is recorded in ledger.paid_pieces BEFORE the child DELETE; the
accepted marks are then nulled — the memory carries the payment fact.
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
    """HOLDS the run lock for the tool's whole duration (the r-loop 1
    shape every sibling flip tool already has, adopted here in r-loop 12
    #3/#12): the bare existence check left a TOCTOU where systemd
    Restart=always / a timer tick could start the continuous driver
    mid-teardown — the blanket child DELETE and the work-dir wipes would
    then race live validation and cut work. acquire_lock also reclaims a
    stale lock from a killed driver, closing the false-refusal case for
    free."""
    from pipeline.run import acquire_lock, release_lock
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True,
                    help="path of the already-taken ledger backup")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--allow-reported", action="store_true",
                    help="proceed although payment stamps exist: uploaded "
                         "stamps are preserved and accepted-stamped "
                         "DELIVERED nodes are recorded as paid pieces "
                         "(ruling C) before the teardown")
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
    from pipeline.run import _discard_split_artifacts
    bk = Path(args.backup)
    if not bk.exists() or bk.stat().st_size < 1024:
        print(f"ABORT: backup {bk} missing/empty — take the parachute first")
        return 2
    # PENDING-SEND interlock (r-loop 9 #7) — same rationale and shape as
    # recal_refix_reset: never tear rows down under a resumable send.
    # Kind-specific diagnosis (r13 #9): the old daily-only text named a
    # file that does not exist and a remedy the driver can never
    # perform for regen-pending and wedged days.
    from pipeline.reports import (PENDING_SEND_GUIDANCE,
                                  pending_daily_send_detail)
    pending = pending_daily_send_detail(cfg)
    if pending:
        day, kind = pending
        why, how = PENDING_SEND_GUIDANCE[kind]
        print(json.dumps({
            "ABORT": "a daily/regen send is pending resume",
            "day": day, "kind": kind, "why": why, "how": how,
        }, indent=1))
        return 2
    ledger = Ledger(cfg.ledger_path)
    n_mem = ledger.db.execute(
        "SELECT COUNT(*) c FROM paid_pieces").fetchone()["c"]
    if n_mem:
        # paid_pieces is payment evidence and is deliberately PRESERVED
        # across the rebuild reset (ruling C, r-loop 9): a stale id
        # collision surfaces loudly on the sheet side rather than a
        # silent double-pay. Say so, so the operator knows it is there.
        print(f"NOTE: {n_mem} paid-piece memory row(s) are PRESERVED "
              f"across this reset (payment evidence is never auto-"
              f"deleted); ambiguous re-deliveries will surface loudly "
              f"on future sheets")
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
        "SELECT session_id, state, uploaded_reported_at FROM sessions "
        "WHERE parent_id IS NULL AND state NOT IN (?,?)",
        KEEP_STATES).fetchall()
    children = ledger.db.execute(
        "SELECT session_id FROM sessions WHERE parent_id IS NOT NULL"
    ).fetchall()

    # PAYMENT-EVIDENCE refusal + per-piece memory (r13 #8; extends
    # ruling C to this tool). The refix sibling's doctrine, mirrored:
    # already-REPORTED footage needs a human decision, not a silent
    # teardown — once dailies resume, build_sheet_rows re-counts every
    # un-stamped root via the late-arrival guard, so nulling the stamps
    # here pays the same footage on two sheets, and deleting an
    # accepted-stamped DELIVERED child destroys the only record that
    # its hours were already paid.
    stamped_roots = [r["session_id"] for r in roots
                     if r["uploaded_reported_at"]]
    acc_nodes = ledger.db.execute(
        "SELECT session_id, parent_id, duration_delivered_s FROM sessions"
        " WHERE state='DELIVERED' AND accepted_reported_at IS NOT NULL"
    ).fetchall()
    if (stamped_roots or acc_nodes) and not args.allow_reported:
        print(json.dumps({
            "ABORT": "payment stamps exist in the reset scope",
            "stamped_roots": stamped_roots,
            "accepted_stamped_delivered_nodes":
                [n["session_id"] for n in acc_nodes],
            "why": ("their hours are already counted on a sent payment "
                    "sheet; resetting/deleting them un-stamped would "
                    "re-count the same footage on a later sheet "
                    "(late-arrival guard) — a silent double-pay"),
            "how": ("re-run with --allow-reported once reconciled: "
                    "uploaded stamps are then preserved and each "
                    "accepted-stamped DELIVERED node is recorded as a "
                    "paid piece (ruling C) before the teardown"),
        }, indent=1))
        return 2
    # (sid, seconds, seg-detail, tree-root) per accepted-counted
    # DELIVERED node — root included; resolved to the TREE root because
    # build_sheet_rows keys paid_pieces on it, and -p1-p1 nesting makes
    # the immediate parent the wrong key (refix walks [root]+kids)
    parent_of = {r["session_id"]: r["parent_id"] for r in
                 ledger.db.execute(
                     "SELECT session_id, parent_id FROM sessions")}

    def _tree_root(sid: str) -> str:
        seen: set = set()
        while parent_of.get(sid) and sid not in seen:
            seen.add(sid)
            sid = parent_of[sid]
        return sid

    paid_to_record = []
    for n in acc_nodes:
        segrow = ledger.db.execute(
            "SELECT detail FROM events WHERE session_id=? AND "
            "detail LIKE 'split segment %' ORDER BY ts LIMIT 1",
            (n["session_id"],)).fetchone()
        paid_to_record.append((_tree_root(n["session_id"]),
                               n["session_id"],
                               n["duration_delivered_s"],
                               segrow["detail"] if segrow else None))

    before = ledger.counts_by_state()
    print(json.dumps({"before": before, "roots_to_reset": len(roots),
                      "children_to_delete": len(children),
                      "stamped_roots_preserved": len(stamped_roots),
                      "accepted_stamped_delivered_nodes": len(acc_nodes),
                      "paid_pieces_to_record": len(paid_to_record)},
                     indent=1))
    if not args.yes:
        print("dry run only (no --yes) — nothing changed")
        return 0

    all_sids = [r["session_id"] for r in roots] + \
               [c["session_id"] for c in children]

    # record the paid pieces BEFORE the child DELETE destroys the rows
    # (refix's recording block; INSERT OR IGNORE — first record wins)
    for root_id, psid, secs, seg in paid_to_record:
        ledger.record_paid_piece(root_id, psid, secs, seg)

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
        # uploaded_reported_at is DELIBERATELY preserved (r13 #8; the
        # refix comment's rationale verbatim: the video identity is
        # unchanged, so these are not new uploaded hours — the stamp is
        # the only thing stopping the late-arrival guard from counting
        # the root a second time; preserved stamps mean nothing is
        # double-paid). accepted_reported_at IS nulled — the paid-piece
        # memory recorded above now carries the payment fact per node
        # (ruling C). tree_sealed_at=NULL stays (r8 C6).
        cur.execute(
            "UPDATE sessions SET state='DISCOVERED', bin=NULL,"
            " reasons_json='[]', fix_attempts=0, duration_delivered_s=NULL,"
            " rrd_sampled=0, delivered_at=NULL,"
            " accepted_reported_at=NULL, tree_sealed_at=NULL,"
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
        # split manifests + rowless segment dirs + the analysis dir go
        # too (r14 #10): a kill in the cutter's manifest-to-child-insert
        # window leaves manifest + segment dirs with zero child rows;
        # carried through the reset, the re-run's crash triage ADOPTS
        # the pre-recalibration (VOID) cut over the stale gen-1
        # segments, and the dirs leak unreclaimably (_sweep_terminal_work
        # needs a SPLIT/REJECTED/DELIVERED parent or a rowed sid). The
        # child rows are already DELETEd above, so every segment dir of
        # every sid is rowless here — the shared discard wipes them all,
        # exactly as the refix sibling does in its teardown.
        _discard_split_artifacts(cfg, ledger, sid)
        shutil.rmtree(cfg.work / f"{sid}-analysis", ignore_errors=True)
        if report.exists():
            _locked_report_remove(report, sid)
            cleared += 1
    leftover_stage = list(cfg.stage.rglob("session.json"))

    after = ledger.counts_by_state()
    print(json.dumps({
        "after": after, "reset_roots": len(roots),
        "deleted_children": len(children), "dossiers_archived": archived,
        "report_entries_cleared": cleared,
        "stamped_roots_preserved": len(stamped_roots),
        "accepted_stamped_delivered_nodes": len(acc_nodes),
        "paid_pieces_recorded": len(paid_to_record),
        "stage_leftovers": [str(p.parent) for p in leftover_stage],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
