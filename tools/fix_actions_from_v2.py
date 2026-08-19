"""Re-resolve input_actions of a delivered v2 session with context gating.

The June-2026 raw bundles are lost, but this fix needs no raw data: the
delivered frames.csv already carries per-frame held keys / buttons / dx / dy,
and the per-frame game context comes from the delivered video
(translator/context.py). Only the input_actions column changes — plus
input_keys rows where a key was pressed in a context where it does nothing
in-game ("unbound in this context" strip, per the locked v2 rule). Video,
timestamps, dx/dy and session.json are carried over untouched, so the shipped
sync/off-by-one results still hold. session.rrd is regenerated.

Usage:
  PYTHONPATH=. uv run --with numpy --with opencv-python-headless \
      --with rerun-sdk python tools/fix_actions_from_v2.py \
      <session_dir> ... --out <root> [--overrides <json>] [--date MM-DD-YYYY]

Output layout (spec v2): <root>/humynlabs/<date, upload UTC>/<game>/<session>/

Overrides JSON, keyed by session_id:
  {"<session_id>": {
      "ambiguous": {"<run_start_frame>": "<action>"},   # suit Space runs
      "context":   {"<frame>|<a>-<b>": "<context>"}}}   # boundary presses
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translator import context as ctxmod                     # noqa: E402
from translator import rrd                                   # noqa: E402
from translator.keybind import build_resolver                # noqa: E402
from translator.keybinds import KEYBINDS, game_key_from_name # noqa: E402
from translator.translate import (resolve_keybind,           # noqa: E402
                                  safe_session_id)
from translator.v2 import (KEYBIND_PATCHES, V2_FRAME_COLS,   # noqa: E402
                           apply_context_to_rows, key_canonical, key_display,
                           _BTN_DISPLAY, _BTN_DISPLAY_INV)

VENDOR = "humynlabs"


def fix_session(session_dir: Path, out_root: Path, date: str,
                overrides: dict) -> dict:
    session_dir = Path(session_dir)
    s = json.loads((session_dir / "session.json").read_text())
    # player-typed session.json: constrain the id to ONE safe path
    # component + the same containment assert the v1/v2 translate joins
    # carry (F9 sibling missed until r13 #7)
    sid = safe_session_id(s.get("session_id"), session_dir)
    slug = game_key_from_name(s.get("game_title", "")) or "unknown_game"
    out_dir = out_root / VENDOR / date / slug / sid
    assert out_dir.resolve().is_relative_to(Path(out_root).resolve()), \
        f"{sid}: out_dir escapes the output root"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in ("video.mp4", "session.json", "rrd_creation.py"):
        shutil.copy2(session_dir / name, out_dir / name)

    with (session_dir / "frames.csv").open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        v2rows = list(reader)
    assert header == V2_FRAME_COLS, f"{sid}: unexpected frames.csv header"

    summary = {"session": sid, "out_dir": str(out_dir), "frames": len(v2rows)}
    if slug in ctxmod.CONTEXT_GAMES:
        # v2 display tokens -> canonical, so the translator resolver applies
        rows = []
        for x in v2rows:
            head, (keys, actions, btns, dx, dy) = x[:-5], x[-5:]
            ck = "|".join(sorted(key_canonical(t) for t in keys.split("|") if t))
            cb = "|".join(sorted(_BTN_DISPLAY_INV.get(b, b)
                                 for b in btns.split("|") if b))
            rows.append(head + [ck, actions, cb, dx, dy])

        ctx_track = ctxmod.classify_video(session_dir / "video.mp4", s["fps"], slug)
        assert len(ctx_track) == len(rows), \
            f"{sid}: context track {len(ctx_track)} != {len(rows)} rows"
        ov = overrides.get(sid, {})
        for spec, ctx in (ov.get("context") or {}).items():
            a, _, b = spec.partition("-")
            for i in range(int(a), int(b or a) + 1):
                ctx_track[i] = ctx
        ambig = {int(k): v for k, v in (ov.get("ambiguous") or {}).items()}

        kbp = session_dir / "raw" / "keybind.json"
        if kbp.exists():
            # the session's own keybind is authoritative (r-loop 12
            # #5/#8, mirroring pipeline/fix.py's F4'd sites)
            kb = resolve_keybind(keybind_path=kbp, game_name=slug,
                                 exe_name=None)
        else:
            kb = dict(KEYBINDS[slug])
        kb.update(KEYBIND_PATCHES.get(slug, {}))
        summary["context"] = apply_context_to_rows(
            rows, ctx_track, slug, build_resolver(kb), s["fps"],
            ambig_overrides=ambig or None)
        summary["contexts"] = {c: ctx_track.count(c) for c in sorted(set(ctx_track))}

        out = []
        for x in rows:
            head, (keys, actions, btns, dx, dy) = x[:-5], x[-5:]
            dk = "|".join(key_display(t) for t in keys.split("|") if t)
            db = "|".join(_BTN_DISPLAY.get(b, b) for b in btns.split("|") if b)
            out.append(head + [dk, actions, db, dx, dy])
        v2rows = out
        with (out_dir / "frames.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(V2_FRAME_COLS)
            w.writerows(v2rows)
        rrd.generate(out_dir)
    else:
        # no context table for this game (e.g. Kamla, 1:1 binds): deliverables
        # are content-identical — copy rather than regenerate
        shutil.copy2(session_dir / "frames.csv", out_dir / "frames.csv")
        shutil.copy2(session_dir / "session.rrd", out_dir / "session.rrd")
        summary["context"] = "n/a (no multi-bound keys; files copied)"
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="+", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--overrides", type=Path, default=None)
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%m-%d-%Y"))
    args = ap.parse_args(argv)
    overrides = (json.loads(args.overrides.read_text())
                 if args.overrides else {})

    report_src = None
    for sess in args.sessions:
        res = fix_session(sess, args.out, args.date, overrides)
        print(json.dumps(res, indent=2))
        # carry the sync-correction record so qa-v2 finds the applied shift
        for parent in list(Path(sess).resolve().parents)[:4]:
            p = parent / "translation_report.json"
            if p.is_file():
                report_src = p
                break
    if report_src:
        report = json.loads(report_src.read_text())
        dst = args.out / "translation_report.json"
        try:
            existing = json.loads(dst.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            existing = {}
        existing.update(report)
        dst.write_text(json.dumps(existing, indent=2))
        print(f"carried translation_report.json -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
