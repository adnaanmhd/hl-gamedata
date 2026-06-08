"""CLI:  python -m translator <command> ...

  translate   <bundle_dir> [bundle_dir ...] --out <dir>     raw bundle -> delivery (5s trim)
  reprocess   <session_dir> [session_dir ...]               rebuild delivered session in place
  qa          <session_dir> [session_dir ...]               validate against spec + guidelines
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import qa
from .translate import reprocess_session, translate_bundle


def _add_common(p):
    p.add_argument("--no-rrd", action="store_true", help="write rrd_creation.py but don't run rerun")
    p.add_argument("--rrd-python", default=None, help="python interpreter with rerun installed")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="translator")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pt = sub.add_parser("translate", help="raw capture bundle -> delivery bundle")
    pt.add_argument("bundles", nargs="+", type=Path)
    pt.add_argument("--out", required=True, type=Path)
    pt.add_argument("--no-trim", action="store_true", help="skip the implicit 5s head/tail trim")
    _add_common(pt)

    pr = sub.add_parser("reprocess", help="rebuild an existing delivered session in place")
    pr.add_argument("sessions", nargs="+", type=Path)
    _add_common(pr)

    pq = sub.add_parser("qa", help="validate session(s)")
    pq.add_argument("sessions", nargs="+", type=Path)

    args = ap.parse_args(argv)

    if args.cmd == "translate":
        for b in args.bundles:
            res = translate_bundle(b, args.out, do_trim=not args.no_trim,
                                   make_rrd=not args.no_rrd, rrd_python=args.rrd_python)
            print(f"✓ {res['session']}  frames={res['frames']}  dq={res['data_quality']}")
            for w in res["warnings"]:
                print(f"    ⚠ {w}")
        return 0

    if args.cmd == "reprocess":
        for s in args.sessions:
            res = reprocess_session(s, make_rrd=not args.no_rrd, rrd_python=args.rrd_python)
            print(f"✓ {res['session']}  frames={res['frames']}  dq={res['data_quality']}")
        return 0

    if args.cmd == "qa":
        worst = 0
        rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
        for s in args.sessions:
            r = qa.check_session(s)
            icon = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}[r.status]
            print(f"{icon} {r.status}  {r.session}  frames={r.info.get('frames')}")
            for i in r.issues:
                print(f"    {i}")
            worst = max(worst, rank[r.status])
        return 0 if worst < 2 else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
