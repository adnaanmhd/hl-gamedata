#!/usr/bin/env python3
"""Correct controls-to-video sync for delivered v2 sessions WITHOUT raw inputs.

Recovery path for the 07-17-2026 delivery: the raw bundles (inputs.jsonl +
metadata.json) no longer exist, so the normal translate-v2 lag correction
(shift raw events, re-bin) can't run. What survives per session:

  - the delivered v2 session (video.mp4 + frames.csv on real PTS), and
  - the v1 delivery (recorded_samples/…): binned frames.csv on real PTS over a
    DIFFERENT trim window of the same raw encode. NOTE: the v1 and v2 CSVs can
    disagree on event→frame attribution (Kamla's v2 carried a ~2.3s offset the
    v1 did not) — which is why the shift is always DERIVED BY MEASUREMENT on
    the merged source, never assumed from the baseline.

Both videos are lossless stream-copies of one raw encode, so their frames
correspond 1:1 where the windows overlap. This tool:

  1. aligns the two frame grids exactly (PTS-delta fingerprint, verified over
     the full overlap),
  2. merges the two binned input tracks in v2 frame-index space (v1 preferred
     where present — it extends past v2's tail after the shift; v2 fills the
     head v1 doesn't cover),
  3. measures the controls-to-video lag with the client's algorithm
     (translator.sync) and remaps inputs by the measured constant:
       - held keys / buttons: backward-map (dest frame takes the source frame
         containing its shifted window start) — held-state continuity is kept,
       - mouse dx/dy: forward-map source→dest sums — total displacement is
         conserved, nothing is duplicated or fabricated,
       - input_actions: re-resolved per dest frame from keys+buttons+motion,
  4. re-measures until |lag| <= 50 ms target (max 3 iterations),
  5. writes the corrected session (video/session.json unchanged) + updates
     translation_report.json at the output root.

Limit of the method: the first `shift` of the corrected timeline (~2.3s Kamla,
~0.2s OW) has no surviving source data → empty inputs there, disclosed in the
report. Precision is ±1 frame (binned sources), same granularity as the
measurement itself.

Usage:
  PYTHONPATH=. uv run --with numpy --with opencv-python-headless \
      --with rerun-sdk python tools/fix_sync_from_v1.py \
      --v1 <v1_session_dir> --v2 <v2_session_dir> --out-root <root> [--date MM-DD-YYYY]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translator import rrd, sync
from translator import video as V
from translator.keybind import bound_literals, build_resolver, resolve_actions
from translator.keybinds import game_key_from_name
from translator.translate import VENDOR, resolve_keybind
from translator.v2 import (_BTN_DISPLAY, _KEY_DISPLAY, KEYBIND_PATCHES,
                           V2_FRAME_COLS, key_display)

_KEY_INTERNAL = {v: k for k, v in _KEY_DISPLAY.items()}
_BTN_INTERNAL = {v: k for k, v in _BTN_DISPLAY.items()}


def key_internal(tok: str) -> str:
    if tok in _KEY_INTERNAL:
        return _KEY_INTERNAL[tok]
    if len(tok) == 1:
        return tok.lower()
    return "_".join(p.lower() for p in
                    re.findall(r"[A-Z][a-z0-9]*|[0-9]+", tok)) or tok.lower()


def read_input_cols(path: Path) -> dict:
    out = {"keys": [], "btns": [], "dx": [], "dy": []}
    with path.open(newline="") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            out["keys"].append([t for t in (row["input_keys"] or "").split("|") if t])
            out["btns"].append([t for t in (row["input_mouse_buttons"] or "").split("|") if t])
            out["dx"].append(float(row["input_mouse_dx"]) if row["input_mouse_dx"] else 0.0)
            out["dy"].append(float(row["input_mouse_dy"]) if row["input_mouse_dy"] else 0.0)
    return out


def align_offset(pts1: list[int], pts2: list[int],
                 tol_us: int = 1000) -> tuple[int, int, int]:
    """Find o such that v1 frame (j+o) is the same raw frame as v2 frame j.

    Matches the inter-frame PTS-delta fingerprint (the dropped-frame gap
    pattern is unique), then verifies ABSOLUTE offset consistency over the
    overlap. The trailing frames of a re-trimmed copy can differ (end-cut
    rounding rewrites the last packet or two), so verification finds the
    longest consistent prefix and returns it as the trusted range.

    Returns (o, j_lo, j_hi): v1 data is trusted for v2 frames j in [j_lo, j_hi).
    """
    d1 = [pts1[i + 1] - pts1[i] for i in range(len(pts1) - 1)]
    d2 = [pts2[j + 1] - pts2[j] for j in range(len(pts2) - 1)]
    probe = 400
    winners = []
    for o in range(-(len(pts2) - 50), len(pts1) - 50):
        j0 = max(0, -o)
        i0 = j0 + o
        m = min(probe, len(d1) - i0, len(d2) - j0)
        if m < 50:
            continue
        if all(abs(d1[i0 + k] - d2[j0 + k]) <= tol_us for k in range(m)):
            winners.append(o)
    full = []
    for o in winners:
        j_lo = max(0, -o)
        j_max = min(len(pts2), len(pts1) - o)
        base = pts2[j_lo] - pts1[j_lo + o]
        j_hi = j_lo
        while j_hi < j_max and abs((pts2[j_hi] - pts1[j_hi + o]) - base) <= tol_us:
            j_hi += 1
        if (j_hi - j_lo) >= 0.99 * (j_max - j_lo):
            full.append((o, j_lo, j_hi, j_max))
    if len(full) != 1:
        raise SystemExit(f"grid alignment ambiguous/failed: candidates={full} "
                         f"(probe winners={winners[:5]}…)")
    o, j_lo, j_hi, j_max = full[0]
    tail_drop = j_max - j_hi
    print(f"  grid alignment: v1_frame = v2_frame + {o}; v1 trusted for v2 "
          f"frames [{j_lo}, {j_hi}) "
          f"({tail_drop} trailing frames differ → v2 source there)")
    return o, j_lo, j_hi


def remap(src_keys, src_btns, src_dx, src_dy, pts2, shift_us, rules):
    """Apply the constant shift on the v2 frame grid; returns per-frame
    (keys, actions, btns, dx, dy) plus count of frames with no source."""
    n = len(pts2)
    keys_d: list[list[str]] = []
    btns_d: list[list[str]] = []
    dx_d = [0.0] * n
    dy_d = [0.0] * n
    uncovered = 0
    for j in range(n):
        t_back = pts2[j] - shift_us
        i = bisect_right(pts2, t_back) - 1
        if 0 <= i < n and src_keys[i] is not None:
            keys_d.append(src_keys[i])
            btns_d.append(src_btns[i])
        else:
            keys_d.append([])
            btns_d.append([])
            uncovered += 1
    for i in range(n):
        if src_keys[i] is None:
            continue
        j = bisect_right(pts2, pts2[i] + shift_us) - 1
        if 0 <= j < n:
            dx_d[j] += src_dx[i]
            dy_d[j] += src_dy[i]
    rows = []
    for j in range(n):
        kset = set(keys_d[j])
        bset = set(btns_d[j])
        moving = bool(dx_d[j] or dy_d[j])
        actions = resolve_actions(kset | bset, moving, rules) if rules else []
        rows.append((sorted(kset), actions, sorted(bset), dx_d[j], dy_d[j]))
    return rows, uncovered


def fix_session(v1_dir: Path, v2_dir: Path, out_root: Path, date: str) -> dict:
    s = json.loads((v2_dir / "session.json").read_text())
    session_id = s["session_id"]
    slug = game_key_from_name(s["game_title"]) or "unknown_game"
    fps = s["fps"]
    print(f"== {session_id} ({s['game_title']})")

    pts1 = V.frame_pts(v1_dir / "video.mp4")
    pts2 = V.frame_pts(v2_dir / "video.mp4")
    v1 = read_input_cols(v1_dir / "frames.csv")
    v2 = read_input_cols(v2_dir / "frames.csv")
    if len(pts1) != len(v1["dx"]) or len(pts2) != len(v2["dx"]):
        raise SystemExit(f"row/frame mismatch: v1 {len(v1['dx'])}/{len(pts1)}, "
                         f"v2 {len(v2['dx'])}/{len(pts2)}")
    o, j_lo, j_hi = align_offset(pts1, pts2)

    # merged source in v2 index space; v1 preferred where trusted, v2 fills
    # what v1 doesn't reach. v1 tokens are internal lowercase already; v2
    # display tokens are converted back to internal.
    n2 = len(pts2)
    src_keys, src_btns, src_dx, src_dy = [], [], [], []
    used_v1 = used_v2 = 0
    for j in range(n2):
        i = j + o
        if j_lo <= j < j_hi:
            src_keys.append(v1["keys"][i])
            src_btns.append(v1["btns"][i])
            src_dx.append(v1["dx"][i])
            src_dy.append(v1["dy"][i])
            used_v1 += 1
        else:
            src_keys.append([key_internal(t) for t in v2["keys"][j]])
            src_btns.append([_BTN_INTERNAL.get(t, key_internal(t))
                             for t in v2["btns"][j]])
            src_dx.append(v2["dx"][j])
            src_dy.append(v2["dy"][j])
            used_v2 += 1
    print(f"  merged source: {used_v1} frames from v1, {used_v2} from v2")
    # cross-check: v1 and v2 binned the same events — dx sums must agree on
    # the overlap (their input columns are independent derivations).
    diff = sum(1 for j in range(j_lo, j_hi)
               if v1["dx"][j + o] != v2["dx"][j] or v1["dy"][j + o] != v2["dy"][j])
    if diff:
        print(f"  note: v1/v2 CSVs disagree on {diff} overlap frames' dx/dy — "
              f"the two deliveries attributed events differently; the v1 side "
              f"wins in the merge and the shift is measured on the result")

    keybind = resolve_keybind(keybind_path=v1_dir / "keybind.json",
                              game_name=s["game_title"], exe_name=None)
    keybind.update(KEYBIND_PATCHES.get(slug, {}))
    rules = build_resolver(keybind)
    bound = bound_literals(keybind)

    print("  measuring baseline lag (optical flow)…")
    mdx, mdy = sync.motion_track(v2_dir / "video.mp4")

    def measure(dx_cells, dy_cells):
        adx, ady = sync.input_track_from_rows(dx_cells, dy_cells, s)
        return sync.estimate_lag(mdx, mdy, adx, ady)

    est0 = measure(v2["dx"], v2["dy"])
    print(f"  baseline: lag {est0.lag_ms(fps):+.1f}ms  corr "
          f"{est0.correlation:+.3f}  active {est0.active_fraction:.1%}")
    measurable = (est0.active_fraction >= sync.MIN_ACTIVE_FRACTION
                  and abs(est0.correlation) >= sync.MIN_ABS_CORRELATION)
    if not measurable:
        raise SystemExit("baseline lag not measurable — refusing to correct blindly")

    shift_us = 0
    est = est0
    rows = None
    uncovered = 0
    for _ in range(3):
        if abs(est.lag_ms(fps)) <= sync.TARGET_ABS_LAG_MS:
            break
        shift_us += round(-est.lag_frames / fps * 1_000_000)
        rows, uncovered = remap(src_keys, src_btns, src_dx, src_dy, pts2,
                                shift_us, rules)
        est = measure([r[3] for r in rows], [r[4] for r in rows])
        print(f"  shift {shift_us / 1000:+.1f}ms → residual lag "
              f"{est.lag_ms(fps):+.1f}ms  corr {est.correlation:+.3f}")
    if rows is None:
        rows, uncovered = remap(src_keys, src_btns, src_dx, src_dy, pts2,
                                0, rules)

    status, msg = sync.verdict(est, fps)
    out_dir = out_root / VENDOR / date / slug / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    extra_null = [""] * (len(V2_FRAME_COLS) - 2 - 5)
    with (out_dir / "frames.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(V2_FRAME_COLS)
        for j, (keys, actions, btns, dx, dy) in enumerate(rows):
            kept = [key_display(k) for k in keys if k in bound]
            btn_disp = [_BTN_DISPLAY.get(b, key_display(b)) for b in btns]
            w.writerow([j, int(round(pts2[j] / 1000.0))] + extra_null
                       + ["|".join(kept), "|".join(actions),
                          "|".join(btn_disp), f"{dx:.1f}", f"{dy:.1f}"])

    for name in ("video.mp4", "session.json"):
        if not (out_dir / name).exists():
            subprocess.run(["cp", "-c", str(v2_dir / name), str(out_dir / name)],
                           check=True)
    rrd.generate(out_dir)

    report_path = out_root / "translation_report.json"
    try:
        report = json.loads(report_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        report = {}
    entry = {
        "method": "v1+v2 binned remap (raw inputs.jsonl unavailable)",
        "measured": True,
        "measured_lag_ms": round(est0.lag_ms(fps), 1),
        "measured_correlation": round(est0.correlation, 4),
        "active_fraction": round(est0.active_fraction, 4),
        "applied_shift_ms": round(shift_us / 1000.0, 1),
        "residual_lag_ms": round(est.lag_ms(fps), 1),
        "residual_correlation": round(est.correlation, 4),
        "status": status,
        "shift_us": shift_us,
        "head_frames_without_source": uncovered,
        "out_dir": str(out_dir),
        "fixed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    report[session_id] = entry
    report_path.write_text(json.dumps(report, indent=2))
    print(f"  [{status}] {msg}")
    print(f"  wrote {out_dir}  (head frames without source data: {uncovered})")
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", type=Path, required=True, action="append",
                    help="v1 delivered session dir (repeatable)")
    ap.add_argument("--v2", type=Path, required=True, action="append",
                    help="matching v2 delivered session dir (same order)")
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%m-%d-%Y"))
    args = ap.parse_args()
    if len(args.v1) != len(args.v2):
        ap.error("--v1/--v2 must pair up")
    for v1_dir, v2_dir in zip(args.v1, args.v2):
        fix_session(v1_dir, v2_dir, args.out_root, args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
