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
from .v2 import check_session_v2, translate_bundle_v2


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

    pt2 = sub.add_parser("translate-v2", help="raw capture bundle -> spec v2 delivery")
    pt2.add_argument("bundles", nargs="+", type=Path)
    pt2.add_argument("--out", required=True, type=Path)
    pt2.add_argument("--head-s", type=float, default=5.0,
                     help="head trim seconds (default 5; tail is always 5)")
    pt2.add_argument("--no-lag-correct", action="store_true",
                     help="skip the controls-to-video sync measurement/correction")
    _add_common(pt2)

    pq2 = sub.add_parser("qa-v2", help="validate v2 session(s) against spec §1.5")
    pq2.add_argument("sessions", nargs="+", type=Path)
    pq2.add_argument("--raw-root", type=Path, default=None,
                     help="root of raw bundles; sessions are matched by "
                          "metadata.json session_id for the off-by-one recheck")

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

    if args.cmd == "translate-v2":
        for b in args.bundles:
            res = translate_bundle_v2(b, args.out, head_s=args.head_s,
                                      make_rrd=not args.no_rrd,
                                      rrd_python=args.rrd_python,
                                      lag_correct=not args.no_lag_correct)
            print(f"✓ {res['session']}  frames={res['frames']}  "
                  f"trim={res['trim']['head_cut_s']:.1f}s head  "
                  f"dq={res['data_quality']}")
            if res["sync"].get("measured"):
                print(f"    sync: lag {res['sync']['measured_lag_ms']:+.1f}ms "
                      f"→ shift {res['sync']['applied_shift_ms']:+.1f}ms "
                      f"→ residual {res['sync'].get('residual_lag_ms', res['sync']['measured_lag_ms']):+.1f}ms "
                      f"[{res['sync'].get('status', '?')}]")
            if res["stripped_keys"]:
                print(f"    stripped unbound keys (frames): {res['stripped_keys']}")
            for w in res["warnings"]:
                print(f"    ⚠ {w}")
        return 0

    if args.cmd == "qa-v2":
        import json as _json
        raw_by_sid = {}
        if args.raw_root:
            for m in Path(args.raw_root).glob("*/*/metadata.json"):
                try:
                    raw_by_sid[_json.loads(m.read_text()).get("session_id")] = m.parent
                except Exception:
                    pass
        worst = 0
        rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
        for s in args.sessions:
            raw = raw_by_sid.get(Path(s).name)
            r = check_session_v2(s, raw_bundle=raw)
            icon = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}[r.status]
            print(f"{icon} {r.status}  {r.session}")
            for i in r.issues:
                print(f"    {i}")
            worst = max(worst, rank[r.status])
        return 0 if worst < 2 else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
