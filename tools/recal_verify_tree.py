#!/usr/bin/env python3
"""Drive II tree <-> ledger verifier (built for the 08-16 endgame; the
deep review confirmed the planned verify step had no tool and that the
ONLY authoritative remote-path record is the events table — delivered_at
dates are wrong for cross-midnight sessions, sid-level checks miss stale
extra files).

Checks, all read-only:
  1. Every DELIVERED row has a latest UPLOADED event 'verified at <path>'
     and that exact dir exists remotely.
  2. Per-dir file-name sets match EXACTLY what should have shipped:
     deliver.SPEC_FILES, plus deliver.RRD_FILES iff the row's
     rrd_sampled=1. Extra files (the stale-rrd class) and missing files
     are both defects.
  3. Every remote dir under humynlabs/ maps back to a DELIVERED row
     (stale dirs from superseded/re-derived sessions are listed for
     cleanup).
  4. No rows stuck PACKAGED/UPLOADED; no DELIVERED row with a
     0-duration or NULL delivered_at.
Content checksums were verified at upload time by deliver.upload_and_verify
(size+md5 per staged file); this tool verifies structure, completeness
and exclusivity of the tree.

Run ON THE VM. Exit 0 = clean, 1 = defects listed.
"""
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path.home() / "hl-gamedata"))
from pipeline import config as C          # noqa: E402
from pipeline import deliver              # noqa: E402
from pipeline.ledger import Ledger        # noqa: E402

REMOTE = "drive-deliver:"


def main() -> int:
    """HOLDS the run lock (r-loop 1): run against a LIVE driver, any
    session delivered between the ledger snapshot and the 30-min remote
    listing would be branded 'STALE remote dir' — a class the endgame's
    step-8 cleanup treats as deletable. Pause the driver first; the lock
    makes that mandatory instead of aspirational."""
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
    ledger = Ledger(cfg.ledger_path)
    defects: list[str] = []
    notes: list[str] = []

    delivered = ledger.db.execute(
        "SELECT session_id, game, rrd_sampled, duration_delivered_s, "
        "delivered_at FROM sessions WHERE state='DELIVERED'").fetchall()
    stuck = ledger.db.execute(
        "SELECT session_id, state FROM sessions WHERE state IN "
        "('PACKAGED','UPLOADED')").fetchall()
    for r in stuck:
        defects.append(f"stuck mid-delivery: {r['session_id']} "
                       f"({r['state']})")

    expected: dict[str, dict] = {}
    for r in delivered:
        if not r["delivered_at"]:
            defects.append(f"{r['session_id']}: DELIVERED without "
                           f"delivered_at")
        if not r["duration_delivered_s"]:
            defects.append(f"{r['session_id']}: DELIVERED with empty "
                           f"duration_delivered_s")
        ev = ledger.db.execute(
            "SELECT detail FROM events WHERE session_id=? AND "
            "to_state='UPLOADED' AND detail LIKE 'verified at %' "
            "ORDER BY ts DESC LIMIT 1", (r["session_id"],)).fetchone()
        if ev is None:
            defects.append(f"{r['session_id']}: DELIVERED but no "
                           f"UPLOADED event with a path")
            continue
        rd = ev["detail"][len("verified at "):].strip()
        want = set(deliver.SPEC_FILES)
        if r["rrd_sampled"]:
            want |= set(deliver.RRD_FILES)
        expected[rd] = {"sid": r["session_id"], "files": want}

    # one recursive listing of the live tree
    p = subprocess.run(
        ["rclone", "lsf", "-R", "--files-only", f"{REMOTE}humynlabs"],
        capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        print(f"ABORT: rclone lsf failed: {p.stderr[-300:]}")
        return 1
    actual: dict[str, set] = defaultdict(set)
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        d, _, name = line.rpartition("/")
        actual[f"humynlabs/{d}"].add(name)

    for rd, meta in sorted(expected.items()):
        if rd not in actual:
            defects.append(f"MISSING remote dir: {rd} ({meta['sid']})")
            continue
        have = actual[rd]
        missing = meta["files"] - have
        extra = have - meta["files"]
        if missing:
            defects.append(f"{rd}: missing {sorted(missing)}")
        if extra:
            defects.append(f"{rd}: EXTRA files {sorted(extra)} "
                           f"(stale-rrd class — must be removed)")
    for rd in sorted(set(actual) - set(expected)):
        defects.append(f"STALE remote dir (no DELIVERED row): {rd}")

    notes.append(f"delivered rows: {len(delivered)}; remote dirs: "
                 f"{len(actual)}; expected dirs: {len(expected)}")
    rrd_n = sum(1 for r in delivered if r["rrd_sampled"])
    notes.append(f"rrd_sampled: {rrd_n}/{len(delivered)} "
                 f"({rrd_n / max(len(delivered), 1):.0%})")
    print(json.dumps({"defects": defects, "notes": notes,
                      "clean": not defects}, indent=1))
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
