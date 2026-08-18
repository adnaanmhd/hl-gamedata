"""Phase III — the reason→fix registry and canonical fix order (plan §11).

Canonical order (later steps depend on earlier): REMUX → REROUTE →
RETRANSLATE if queued (supersedes and clears most CSV-level fixes) →
RETRIM/CUT → GATE → CONTEXT/HYGIENE → mechanical rewrites → session.json
recompute. rrd happens at packaging (R17). All fixes operate on the LOCAL
working copy — Drive I originals never change (R6). Every applied fix is
appended to the dossier fixlog.json — the audit trail behind payment.

A cut ends the parent's pass: the segments re-enter Phase II as child
sessions (ids -pN) with their own bounded fix budget; the parent becomes
SPLIT.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from translator import rrd as rrdmod
from translator import sync
from translator import trim as trimmod
from translator import video as V
from translator.binner import bin_session
from translator.keybind import (bound_literals, build_resolver,
                                resolve_actions)
from translator.keybinds import KEYBINDS, game_key_from_name
from translator.translate import load_events, resolve_keybind
from translator.v2 import (KEYBIND_PATCHES, MOUSE_CONVENTION, V2_FRAME_COLS,
                           _BTN_DISPLAY, _BTN_DISPLAY_INV, _v2_rows,
                           key_canonical, key_display, translate_bundle_v2)
from translator import context as ctxmod

from . import config as C
from . import cutter, gate

_RETRIM = None


def _load_tool(name: str, filename: str):
    path = C.REPO_ROOT / "tools" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _retrim_tool():
    global _RETRIM
    if _RETRIM is None:
        _RETRIM = _load_tool("hl_retrim", "retrim_v2_session.py")
    return _RETRIM


class FixFailed(Exception):
    pass


# --------------------------------------------------------------- fix plan

def plan_fixes(reasons: list[dict], *, game: str, has_raw: bool) -> dict:
    """Reason codes -> ordered fix plan (§10 fix-chain column).

    Returns {"steps": [(fix_id, params)], "unfixable": [codes]}. A queued
    RETRANSLATE clears the CSV-level fixes it supersedes."""
    steps: list[tuple[str, dict]] = []
    unfixable: list[str] = []
    retranslate = False
    cut_windows: list[tuple[float, float]] = []
    gate_windows: list[tuple[float, float]] = []
    head_cut: float | None = None
    # FIX_RETRIM_HEAD is emitted late (after hygiene and the gate) — see
    # the head_cut branch below for why the ordering is load-bearing
    head_step: tuple[str, dict] | None = None
    tail_cut: float | None = None
    csv_fixes: list[tuple[str, dict]] = []
    remux = reroute = v1 = raw_only = context_fix = False
    reroute_actual = ""

    for r in reasons:
        if not r.get("blocking"):
            continue
        code, p = r["code"], r.get("params") or {}
        if not r.get("fixable"):
            unfixable.append(code)
            continue
        if code == "STR_VIDEO_UNREADABLE":
            remux = True
        elif code == "ARR_V1_FORMAT":
            v1 = True
        elif code == "ARR_RAW_ONLY":
            raw_only = True
        elif code == "STR_GAME_MISMATCH":
            reroute = True
            reroute_actual = p.get("actual", "")
        elif code in ("SYN_TS_NOT_PTS", "SYN_LAG_CONST", "SYN_DRIFT",
                      "INP_KEYS_NO_ACTION", "QA_FAIL_UNMAPPED",
                      "STR_ROWS_MISMATCH", "STR_TS_NONMONO"):
            if has_raw:
                retranslate = True
            elif code == "SYN_TS_NOT_PTS":
                csv_fixes.append(("FIX_TSREPAIR_PTS", {}))
            elif code == "SYN_LAG_CONST":
                csv_fixes.append(("FIX_LAGSHIFT_CSV", p))
            elif code == "INP_KEYS_NO_ACTION":
                csv_fixes.append(("FIX_KEY_HYGIENE", {}))
            elif code == "STR_ROWS_MISMATCH":
                csv_fixes.append(("FIX_ROWS_SURGERY", {}))
            elif code == "STR_TS_NONMONO":
                csv_fixes.append(("FIX_TSREPAIR_PTS", {}))
            else:
                unfixable.append(code)
        elif code == "INP_FANOUT":
            if game == "outer_wilds":
                context_fix = True
            elif has_raw:
                retranslate = True
            else:
                unfixable.append(code)
        elif code in ("INP_OSKEYS", "INP_TOKEN_CASE", "INP_BLEED"):
            csv_fixes.append(("FIX_KEY_HYGIENE", {}))
        elif code == "INP_FROZEN_ACTIONS":
            gate_windows.append((p["t0"], p["t1"]))
        elif code in ("CNT_MID_NONGAMEPLAY", "CNT_AFK"):
            if "cut" in p:
                cut_windows.append(tuple(p["cut"]))
            else:
                unfixable.append(code)
        elif code == "CNT_EDGE_NONGAMEPLAY":
            if p.get("edge") == "head":
                head_cut = max(head_cut or 0.0, p["cut_at_s"])
            else:
                tail_cut = min(tail_cut or 1e9, p["cut_at_s"])
        elif code in ("CNT_NOTIF_EDGE", "CNT_CHAT_PII"):
            t = p.get("t", 0.0)
            if p.get("edge") == "head" or t <= 3.0:
                head_cut = max(head_cut or 0.0, t + 1.0)
            else:
                tail_cut = min(tail_cut or 1e9, t - 1.0)
        elif code in ("STR_SJ_INVALID", "STR_HEADER_BAD",
                      "STR_CAMERA_NONNULL", "STR_SENTINELS", "STR_TS_TAIL"):
            if has_raw:
                retranslate = True
            else:
                csv_fixes.append({
                    "STR_SJ_INVALID": ("FIX_SESSIONJSON_REWRITE", {}),
                    "STR_HEADER_BAD": ("FIX_HEADER_REWRITE", {}),
                    "STR_CAMERA_NONNULL": ("FIX_CAMERA_NULL", {}),
                    "STR_SENTINELS": ("FIX_SENTINELS", {}),
                    "STR_TS_TAIL": ("FIX_TSREPAIR_PTS", {}),
                }[code])
        else:
            unfixable.append(code)

    if remux:
        steps.append(("FIX_REMUX", {}))
    if v1:
        steps.append(("FIX_V1_TO_V2", {}))
    if raw_only:
        steps.append(("FIX_TRANSLATE_RAW", {}))
    if reroute:
        steps.append(("FIX_REROUTE_GAME", {"actual": reroute_actual}))
        retranslate = has_raw or retranslate
    if retranslate and has_raw:
        steps.append(("FIX_RETRANSLATE", {}))
        csv_fixes = []                    # superseded by the re-translate
    pre_emitted: set[tuple[str, str]] = set()

    def _pre_cut_csv_fixes():
        """Structural CSV surgery must precede a cut, gate, or retrim.
        Header rewrite first of all: gate.py, cutter.py and the retrim
        tool hard-assert a v2 header, so a plan that reaches them before
        FIX_HEADER_REWRITE errors on its first step every attempt and
        burns the fix budget — wrongful reject (review-r4 #23). Then rows
        before timestamps: the cutter maps CSV rows onto video frames and
        needs them aligned first (the row delta is what tsrepair
        hard-fails on)."""
        for want in ("FIX_HEADER_REWRITE", "FIX_ROWS_SURGERY",
                     "FIX_TSREPAIR_PTS"):
            for fid, p in csv_fixes:
                if fid == want and (fid, json.dumps(p)) not in pre_emitted:
                    pre_emitted.add((fid, json.dumps(p)))
                    steps.append((fid, p))

    if cut_windows or (head_cut and tail_cut):
        cuts = list(cut_windows)
        if head_cut:
            cuts.append((0.0, head_cut))
        if tail_cut:
            cuts.append((tail_cut, 1e9))
        _pre_cut_csv_fixes()
        # Gate BEFORE the cut, never instead of it (r-loop 5). Both cut
        # exits used to return here with gate_windows silently discarded,
        # and nothing carried the window forward: the r-loop-4 sidecar was
        # deliberately reverted, children are inserted with
        # reasons_json="[]", and cutter.py:173 copies the parent's rows
        # through verbatim -- so a segment could ship semantic actions
        # recorded during a CONFIRMED freeze, which is the client complaint
        # the gate exists to prevent. Re-deriving it in the child is not
        # equivalent: it costs another paid Gemini sweep and one of the
        # child's two attempts, and it can MISS -- the VLM samples every 4s,
        # so a 3s freeze can fall between samples, _map_windows needs
        # tier=='high', and _build_aux drops scanner statics near the edges.
        # Gating here is always correct: it blanks in place on the parent's
        # own timeline, where the window was measured, and the cutter copies
        # the blanked rows into every child. Safe to run before the cut
        # because these exits emit no hygiene/context step -- the fixes that
        # re-derive input_actions and would undo it -- only the structural
        # repairs _pre_cut_csv_fixes() just emitted, which the gate needs.
        if gate_windows:
            steps.append(("FIX_GATE_WINDOW",
                          {"windows": sorted(gate_windows)}))
        steps.append(("FIX_CUT_SEGMENTS", {"cut": sorted(cuts)}))
        return {"steps": steps, "unfixable": sorted(set(unfixable))}
    if head_cut:
        # the retrim tool asserts a v2 header too (review-r4 #23)
        _pre_cut_csv_fixes()
        # DEFERRED, not dropped (r-loop 4). The retrim is emitted at the end
        # so the final order is: structural -> hygiene/context/CSV writers
        # -> GATE_WINDOW -> RETRIM_HEAD -> SESSIONJSON_RECOMPUTE.
        #   * gate after hygiene, because hygiene re-derives input_actions
        #     and would undo it (r-loop 3);
        #   * gate BEFORE the retrim, because gate windows carry PRE-trim
        #     timestamps — correct at the moment the gate runs — and the
        #     retrim only slices head rows and rebases what survives, never
        #     re-deriving actions;
        #   * sessionjson last, because the retrim rewrites the video.
        # Dropping the gate (the old behaviour) cost a whole fix attempt:
        # the reason survived untouched into revalidation and attempt 2 was
        # spent gating it, leaving nothing for any third reason — REJECTED
        # "fix retries exhausted", an unpaid player, surfaced to ops as a
        # bare fix-failed marker. R3 made the collision common rather than
        # rare: a 2-5s mid-clip window used to be a CUT, which MERGES with
        # the head trim into one FIX_CUT_SEGMENTS step; as a gate it cannot.
        head_step = ("FIX_RETRIM_HEAD", {"head_s": head_cut})
    if tail_cut:
        _pre_cut_csv_fixes()
        # same as the exit above (r-loop 5)
        if gate_windows:
            steps.append(("FIX_GATE_WINDOW",
                          {"windows": sorted(gate_windows)}))
        steps.append(("FIX_CUT_SEGMENTS", {"cut": [(tail_cut, 1e9)]}))
        return {"steps": steps, "unfixable": sorted(set(unfixable))}
    if gate_windows:
        # gate.py asserts a v2 header — structural surgery first
        # (review-r4 #23). The gate STEP itself is appended after the csv
        # writers below; this only orders the structural repairs ahead of it.
        _pre_cut_csv_fixes()
    # hygiene before context (canonical CONTEXT/HYGIENE slot); seen is
    # seeded with the pre-gate/-retrim emissions so the csv loop below
    # cannot plan them a second time (review-r4 #23)
    seen = set(pre_emitted)
    hygiene_planned = False
    for fid, p in csv_fixes:
        if fid == "FIX_KEY_HYGIENE" and fid in seen:
            continue
        seen.add(fid)
        if fid == "FIX_KEY_HYGIENE":
            hygiene_planned = True
            steps.append((fid, p))
    # hygiene re-resolves actions WITHOUT context — on OW that re-fans-out
    # multi-bound keys, so context gating must always follow it there
    if (context_fix or (hygiene_planned and game == "outer_wilds")) \
            and not retranslate:
        steps.append(("FIX_ACTIONS_CONTEXT", {}))
    # row/timestamp surgery order matters: the row-count delta is the very
    # thing tsrepair hard-fails on (review-2 #14)
    _CSV_ORDER = {"FIX_ROWS_SURGERY": 0, "FIX_TSREPAIR_PTS": 1}
    for fid, p in sorted((fp for fp in csv_fixes
                          if fp[0] != "FIX_KEY_HYGIENE"),
                         key=lambda fp: _CSV_ORDER.get(fp[0], 2)):
        if (fid, json.dumps(p)) not in seen:
            seen.add((fid, json.dumps(p)))
            steps.append((fid, p))
    # FIX_GATE_WINDOW goes LAST among the frames.csv writers (r-loop 3).
    # It only BLANKS input_keys/input_actions, so it is safe last — while
    # every step above re-derives those same columns and silently undid it:
    # fix_key_hygiene re-resolves actions for every row from keys|buttons
    # plus the motion flags, and resolve_actions fires motion-bound
    # semantics (kamla `look: mouse`) from dx/dy alone, which the gate
    # deliberately leaves as captured. So a gated window came back with
    # input_actions='look' on every frame that still had mouse motion, in
    # the SAME pass that gated it; revalidation re-raised
    # INP_FROZEN_ACTIONS, attempt 2 was spent re-gating, and any other
    # surviving reason then rejected a deliverable session. Verified by
    # running gate_windows + fix_key_hygiene on a synthetic v2 frames.csv:
    # 20/20 gated rows came back with actions repopulated.
    # FIX_ACTIONS_CONTEXT and FIX_LAGSHIFT_CSV rewrite/displace the same
    # columns and are ordered above for the same reason.
    if gate_windows:
        steps.append(("FIX_GATE_WINDOW", {"windows": sorted(gate_windows)}))
    if head_step is not None:
        steps.append(head_step)
    if steps:
        steps.append(("FIX_SESSIONJSON_RECOMPUTE", {}))
    return {"steps": steps, "unfixable": sorted(set(unfixable))}


# ------------------------------------------------------------ application

def apply_fixes(work_dir: Path, plan: dict, *, game: str,
                dossier_dir: Path, split_root: Path | None = None) -> dict:
    """Run the planned fixes on the working copy. Returns
    {"applied": [...], "children": [...] or None, "error": str or None}."""
    work_dir = Path(work_dir)
    dossier_dir = Path(dossier_dir)
    applied: list[dict] = []
    children = None
    error = None
    for fix_id, params in plan["steps"]:
        try:
            note = _dispatch(fix_id, params, work_dir, game,
                             split_root or work_dir.parent)
            if fix_id == "FIX_CUT_SEGMENTS":
                children = note
                applied.append({"fix": fix_id, "params": _jsonable(params),
                                "ok": True, "note": note})
                break                     # children re-enter Phase II
            applied.append({"fix": fix_id, "params": _jsonable(params),
                            "ok": True, "note": note})
        except Exception as e:
            error = f"{fix_id}: {type(e).__name__}: {e}"
            applied.append({"fix": fix_id, "params": _jsonable(params),
                            "ok": False, "note": str(e)[:300]})
            break
    _append_fixlog(dossier_dir, applied)
    return {"applied": applied, "children": children, "error": error}


def _jsonable(p):
    return json.loads(json.dumps(p, default=str))


def _append_fixlog(dossier_dir: Path, entries: list[dict]) -> None:
    dossier_dir.mkdir(parents=True, exist_ok=True)
    path = dossier_dir / "fixlog.json"
    try:
        log = json.loads(path.read_text())
    except FileNotFoundError:
        log = []
    except json.JSONDecodeError:
        # a torn file is EVIDENCE LOSS, not a fresh start: rename it aside
        # so the loss is visible rather than silently overwritten. This is
        # design §13's "dossier evidence + fixlog" — the artifact a payment
        # dispute is adjudicated against (r-loop 4).
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            path.replace(path.with_name(f"fixlog.json.corrupt-{stamp}"))
            print(f"[fixlog-corrupt] {dossier_dir.name}: unreadable log "
                  f"preserved as fixlog.json.corrupt-{stamp}")
        except OSError:
            pass
        log = []
    log.append({"ts": datetime.now(timezone.utc).isoformat(
        timespec="seconds"), "fixes": entries})
    # ATOMIC, like every other artifact writer here (frames.csv,
    # session.json, translation_report.json, the split manifest, the digest
    # anchor): kill -9 is the designed-for path, and a torn write here
    # discarded the ENTIRE fix history, including which frames the pipeline
    # itself blanked and why.
    tmp = path.with_name(f"fixlog.json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(log, indent=1))
    tmp.replace(path)


def _dispatch(fix_id: str, params: dict, work: Path, game: str,
              split_root: Path):
    if fix_id == "FIX_REMUX":
        return fix_remux(work)
    if fix_id == "FIX_V1_TO_V2":
        return fix_v1_to_v2(work, game)
    if fix_id == "FIX_TRANSLATE_RAW":
        return fix_translate_raw(work)
    if fix_id == "FIX_REROUTE_GAME":
        return f"rerouted to {params.get('actual')} (ledger updates game; " \
               f"re-translate under the correct keybind follows)"
    if fix_id == "FIX_RETRANSLATE":
        return retranslate_from_sidecars(
            work, game_override=game if game in C.GAMES else None)
    if fix_id == "FIX_CUT_SEGMENTS":
        # the PROBED duration, not session.json's (which may be the very
        # thing that's wrong) — a stale short duration would silently
        # truncate everything past it
        dur = V.probe(work / "video.mp4").duration_s
        keep = cutter.complement_windows(
            [(a, min(b, dur)) for a, b in params["cut"]], dur)
        res = cutter.cut_segments(work, keep, split_root)
        _propagate_shift_record(work, [Path(s["dir"])
                                       for s in res["segments"]])
        return res
    if fix_id == "FIX_RETRIM_HEAD":
        tool = _retrim_tool()
        return tool.retrim(work, params["head_s"], work)
    if fix_id == "FIX_GATE_WINDOW":
        return gate.gate_windows(work, params["windows"])
    if fix_id == "FIX_KEY_HYGIENE":
        return fix_key_hygiene(work, game)
    if fix_id == "FIX_ACTIONS_CONTEXT":
        return fix_actions_context(work, game)
    if fix_id == "FIX_LAGSHIFT_CSV":
        return fix_lagshift_csv(work)
    if fix_id == "FIX_TSREPAIR_PTS":
        return fix_tsrepair_pts(work)
    if fix_id == "FIX_ROWS_SURGERY":
        return fix_rows_surgery(work)
    if fix_id == "FIX_HEADER_REWRITE":
        return fix_header_rewrite(work)
    if fix_id == "FIX_CAMERA_NULL":
        return fix_camera_null(work)
    if fix_id == "FIX_SENTINELS":
        return fix_sentinels(work)
    if fix_id in ("FIX_SESSIONJSON_REWRITE", "FIX_SESSIONJSON_RECOMPUTE"):
        return fix_sessionjson_recompute(work, game)
    raise FixFailed(f"unknown fix id {fix_id}")


# ------------------------------------------------------- implementations

def _propagate_shift_record(parent: Path, children: list[Path]) -> None:
    """qa's raw recomputation reads the applied sync shift from
    translation_report.json keyed by SESSION DIR NAME — split children
    must inherit the parent's entry or every shift-corrected child fails
    the recheck spuriously (review finding #10)."""
    report_path = parent.parent / "translation_report.json"
    try:
        report = json.loads(report_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return
    entry = report.get(parent.name)
    if not entry:
        return
    # locked + atomic like every other writer of this SHARED file — up to
    # 8 validation workers race it (review-r3 #8)
    from .validate import _locked_report_update
    for child in children:
        if child.name not in report:
            _locked_report_update(report_path, child.name, dict(entry))


def _read_csv(work: Path) -> tuple[list[str], list[list[str]]]:
    with (work / "frames.csv").open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        return header, list(reader)


def _write_csv(work: Path, header: list[str], rows: list[list[str]]) -> None:
    tmp = work / "frames.csv.tmp"
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(work / "frames.csv")          # atomic (§13)


def fix_remux(work: Path) -> str:
    src = work / "video.mp4"
    tmp = work / "video.remux.mp4"
    p = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-map", "0",
         "-c", "copy", "-movflags", "+faststart", str(tmp)],
        capture_output=True, text=True, timeout=1800)
    if p.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise FixFailed(f"remux failed: {p.stderr.strip()[:200]}")
    try:
        V.probe(tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise FixFailed(f"remuxed file still unreadable: {e}")
    tmp.replace(src)
    return "remuxed (stream copy)"


def fix_translate_raw(work: Path) -> str:
    """ARR_RAW_ONLY: the folder is a raw bundle — full translate-v2 (with
    the implicit 5 s trim) into a temp out, then adopt the delivery files."""
    out = work / "_translated"
    res = translate_bundle_v2(work, out, make_rrd=False)
    src = Path(res["out_dir"])
    for name in ("video.mp4", "frames.csv", "session.json",
                 "rrd_creation.py"):
        shutil.copy2(src / name, work / name)
    (work / "session.rrd").touch()
    raw = work / "raw"
    raw.mkdir(exist_ok=True)
    for name in ("inputs.jsonl", "metadata.json", "keybind.json"):
        if (work / name).exists():
            shutil.move(str(work / name), raw / name)
    # the applied sync shift must survive the temp-out cleanup — qa's raw
    # recomputation reads it from the work root (review finding #10)
    try:
        rep = json.loads((out / "translation_report.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        rep = {}
    entry = rep.get(json.loads((work / "session.json").read_text())
                    .get("session_id")) or rep.get(src.name)
    if entry:
        # locked + atomic (review-r3 #8)
        from .validate import _locked_report_update
        _locked_report_update(work.parent / "translation_report.json",
                              work.name, entry)
    shutil.rmtree(out, ignore_errors=True)
    return f"translated raw bundle: {res['data_quality']}"


def retranslate_from_sidecars(work: Path, *,
                              game_override: str | None = None) -> str:
    """FIX_RETRANSLATE for a v2 upload with raw sidecars (R3): re-bin the
    events onto the DELIVERED video (no re-trim — the head offset comes from
    metadata started_at vs session created_at), re-run lag correction and
    context gating, rewrite frames.csv. The universal strong fix.

    `game_override` is the REROUTED game: the raw metadata is exactly what
    the mismatch falsified, so on reroute the built-in keybind for the
    corrected game governs, never the metadata-derived one (review-2 #5)."""
    from translator.v2 import GAME_TITLES
    raw = work / "raw"
    meta = json.loads((raw / "metadata.json").read_text())
    s = json.loads((work / "session.json").read_text())
    game_info = meta.get("game", {})
    if game_override:
        slug = game_override
        game_name = GAME_TITLES.get(slug, slug)
    else:
        game_name = game_info.get("name") or meta.get("game_name") \
            or s.get("game_title")
        slug = game_key_from_name(game_name or "",
                                  game_info.get("exe_name")) \
            or "unknown_game"

    def _utc(ts: str) -> datetime:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    started = _utc(meta["recording"]["started_at_utc"])
    created = _utc(s["created_at_utc"])
    head_s = max((created - started).total_seconds(), 0.0)

    info = V.probe(work / "video.mp4")
    pts = V.frame_pts(work / "video.mp4")
    raw_events = load_events(raw / "inputs.jsonl")
    if game_override:
        keybind = dict(KEYBINDS.get(slug, {}))
    else:
        keybind = resolve_keybind(keybind_path=raw / "keybind.json",
                                  game_name=game_name,
                                  exe_name=game_info.get("exe_name"))
    keybind.update(KEYBIND_PATCHES.get(slug, {}))
    rules = build_resolver(keybind)
    bound = bound_literals(keybind)

    events = trimmod.rebase_events(raw_events, head_s, info.duration_s)
    rows, stats = bin_session(events, info, keybind, rules, bound,
                              frame_pts_us=pts)

    shift_us = 0
    note = ""
    if sync.available() and stats.has_mouse_motion:
        mdx, mdy = sync.motion_track(work / "video.mp4")
        conv = {"input_mouse_convention": MOUSE_CONVENTION}

        def measure(rs):
            adx, ady = sync.input_track_from_rows(
                [r[-2] for r in rs], [r[-1] for r in rs], conv)
            return sync.estimate_lag(mdx, mdy, adx, ady)

        est = measure(rows)
        measurable = (est.active_fraction >= sync.MIN_ACTIVE_FRACTION
                      and abs(est.correlation) >= sync.MIN_ABS_CORRELATION)
        for _ in range(3):
            if not (measurable
                    and abs(est.lag_ms(info.fps)) > sync.TARGET_ABS_LAG_MS):
                break
            shift_us += round(-est.lag_frames / info.fps * 1_000_000)
            shifted = [dict(e, t=e["t"] + shift_us) for e in raw_events]
            events = trimmod.rebase_events(shifted, head_s, info.duration_s)
            rows, stats = bin_session(events, info, keybind, rules, bound,
                                      frame_pts_us=pts)
            est = measure(rows)
        note = (f"lag {est.lag_ms(info.fps):+.1f}ms after "
                f"{shift_us / 1000:+.1f}ms shift")

    if slug in ctxmod.CONTEXT_GAMES and ctxmod.available():
        track = ctxmod.classify_video(work / "video.mp4", info.fps, slug)
        if len(track) == len(rows):
            from translator.v2 import apply_context_to_rows
            apply_context_to_rows(rows, track, slug, rules, info.fps)
        else:
            note += " | context track length mismatch — gating skipped"

    strip: dict = {}
    v2rows = _v2_rows(rows, bound, strip)
    _write_csv(work, V2_FRAME_COLS, v2rows)
    fix_sessionjson_recompute(work, slug)

    # locked + atomic (review-r3 #8)
    from .validate import _locked_report_update
    _locked_report_update(work.parent / "translation_report.json",
                          work.name,
                          {"shift_us": shift_us, "retranslated": True})
    return f"re-translated from sidecars ({stats.n_frames} frames; {note}; " \
           f"stripped {sum(strip.values())} unbound key presses)"


def fix_key_hygiene(work: Path, game: str) -> str:
    """CSV-level key normalization via translator/keys.py: v2 token case,
    OS-key/control-byte strip, L+R bleed drop-spurious-side, then re-resolve
    actions from the surviving keys (spec §1.5.5 coupling)."""
    from translator import keys as K
    kb = dict(KEYBINDS.get(game, {}))
    kb.update(KEYBIND_PATCHES.get(game, {}))
    rules = build_resolver(kb) if kb else None
    bound = bound_literals(kb) if kb else frozenset()
    header, rows = _read_csv(work)
    col = {c: i for i, c in enumerate(header)}
    ki, ai, bi = col["input_keys"], col["input_actions"], \
        col["input_mouse_buttons"]
    dxi, dyi = col["input_mouse_dx"], col["input_mouse_dy"]
    stripped = 0
    bleed = 0
    for r in rows:
        toks = [key_canonical(t) for t in (r[ki] or "").split("|") if t]
        kept = []
        for t in toks:
            nt = K.normalize_event_key(t, bound=bound, aggressive=True)
            if nt:
                kept.append(nt)
            else:
                stripped += 1
        kset = set(kept)
        for left, right in K.MODIFIER_PAIRS:
            if left in kset and right in kset:
                bleed += 1
                lb, rb = left in bound, right in bound
                kset.discard(right if lb or not rb else left)
        btns = [_BTN_DISPLAY_INV.get(b, b)
                for b in (r[bi] or "").split("|") if b]
        if rules is not None:
            # numeric-zero test, not a string sentinel test — "0"/"-0.0"
            # cells are motionless and must not fire look actions
            # (review-2 #7; matches translator/v2.py's own _active)
            def _moving(v: str) -> bool:
                if v in ("", None):
                    return False
                try:
                    return float(v) != 0.0
                except ValueError:
                    return False
            motion = (_moving(r[dxi]), _moving(r[dyi]))
            acts, _ = resolve_actions(kset | set(btns), motion, rules)
            r[ai] = "|".join(acts)
        r[ki] = "|".join(key_display(t) for t in sorted(kset))
        r[bi] = "|".join(_BTN_DISPLAY.get(b, b) for b in btns)
    _write_csv(work, header, rows)
    return f"hygiene: stripped {stripped} tokens, resolved {bleed} bleed " \
           f"frames, actions re-resolved"


def fix_actions_context(work: Path, game: str) -> str:
    """FIX_ACTIONS_CONTEXT — Outer Wilds only (context tables exist only for
    OW; a no-op elsewhere by design). Reuses tools/fix_actions_from_v2.py's
    core via the translator primitives, editing the working copy in place."""
    if game not in ctxmod.CONTEXT_GAMES:
        return "no-op: no context table for this game"
    if not ctxmod.available():
        raise FixFailed("context gating needs numpy + opencv")
    s = json.loads((work / "session.json").read_text())
    header, v2rows = _read_csv(work)
    assert header == V2_FRAME_COLS
    rows = []
    for x in v2rows:
        head, (keys, actions, btns, dx, dy) = x[:-5], x[-5:]
        ck = "|".join(sorted(key_canonical(t)
                             for t in keys.split("|") if t))
        cb = "|".join(sorted(_BTN_DISPLAY_INV.get(b, b)
                             for b in btns.split("|") if b))
        rows.append(head + [ck, actions, cb, dx, dy])
    track = ctxmod.classify_video(work / "video.mp4", s["fps"], game)
    if len(track) != len(rows):
        raise FixFailed(f"context track {len(track)} != {len(rows)} rows")
    kb = dict(KEYBINDS[game])
    kb.update(KEYBIND_PATCHES.get(game, {}))
    from translator.v2 import apply_context_to_rows
    summary = apply_context_to_rows(rows, track, game, build_resolver(kb),
                                    s["fps"])
    out = []
    for x in rows:
        head, (keys, actions, btns, dx, dy) = x[:-5], x[-5:]
        dk = "|".join(key_display(t) for t in keys.split("|") if t)
        db = "|".join(_BTN_DISPLAY.get(b, b) for b in btns.split("|") if b)
        out.append(head + [dk, actions, db, dx, dy])
    _write_csv(work, V2_FRAME_COLS, out)
    return f"context gating: {summary['frames_changed']} frames changed, " \
           f"dead strips {summary['dead_press_strips']}"


def shift_input_rows(rows: list[list[str]], k: int, idx: list[int],
                     empty_val: dict[int, str]) -> list[list[str]]:
    """Move the input columns k rows later (k>0) or earlier (k<0); rows
    shifted in from beyond either boundary get the empty value (disclosed —
    no source data exists there)."""
    src = [list(r) for r in rows]
    out = [list(r) for r in rows]
    n = len(rows)
    for i in range(n):
        j = i - k
        for cidx in idx:
            out[i][cidx] = src[j][cidx] if 0 <= j < n else empty_val[cidx]
    return out


def fix_lagshift_csv(work: Path) -> str:
    """FIX_LAGSHIFT_CSV (no sidecars): shift all input columns by
    round(lag_ms / frame_interval_ms) rows, re-measure, iterate ≤3
    (port of the fix_sync_from_v1.py shift pattern, plan §11)."""
    if not sync.available():
        raise FixFailed("lag shift needs numpy + opencv to re-measure")
    s = json.loads((work / "session.json").read_text())
    fps = float(s["fps"])
    header, rows = _read_csv(work)
    col = {c: i for i, c in enumerate(header)}
    idx = [col[c] for c in ("input_keys", "input_actions",
                            "input_mouse_buttons", "input_mouse_dx",
                            "input_mouse_dy")]
    mdx, mdy = sync.motion_track(work / "video.mp4")

    def measure(rs):
        adx, ady = sync.input_track_from_rows(
            [r[col["input_mouse_dx"]] for r in rs],
            [r[col["input_mouse_dy"]] for r in rs], s)
        return sync.estimate_lag(mdx, mdy, adx, ady)

    est = measure(rows)
    if est.active_fraction < sync.MIN_ACTIVE_FRACTION or \
            abs(est.correlation) < sync.MIN_ABS_CORRELATION:
        raise FixFailed("lag not measurable — cannot shift safely")
    total = 0
    blank = {col["input_mouse_dx"]: "0.0", col["input_mouse_dy"]: "0.0"}
    has_motion = any(r[col["input_mouse_dx"]] not in ("",) for r in rows)
    empty_val = {i: ("0.0" if i in blank and has_motion else "")
                 for i in idx}
    for _ in range(3):
        if abs(est.lag_ms(fps)) <= sync.TARGET_ABS_LAG_MS:
            break
        k = -est.lag_frames
        if k == 0:
            break
        total += k
        rows = shift_input_rows(rows, k, idx, empty_val)
        est = measure(rows)
    if abs(est.lag_ms(fps)) > sync.MAX_ABS_LAG_MS:
        raise FixFailed(
            f"lag still {est.lag_ms(fps):+.1f}ms after shifting "
            f"{total} rows — drifting, not constant (SYN_DRIFT)")
    _write_csv(work, header, rows)
    return f"shifted input columns by {total} rows; residual " \
           f"{est.lag_ms(fps):+.1f}ms (corr {est.correlation:+.2f})"


def fix_tsrepair_pts(work: Path) -> str:
    """FIX_TSREPAIR_PTS: rewrite timestamp_ms from real per-frame PTS."""
    pts = V.frame_pts(work / "video.mp4")
    header, rows = _read_csv(work)
    if not pts or len(pts) != len(rows):
        raise FixFailed(f"PTS count {len(pts)} != rows {len(rows)}")
    ti = header.index("timestamp_ms")
    for i, r in enumerate(rows):
        r[ti] = str(int(round(pts[i] / 1000.0)))
    _write_csv(work, header, rows)
    return f"timestamps rewritten from PTS for {len(rows)} rows"


def fix_rows_surgery(work: Path) -> str:
    """STR_ROWS_MISMATCH without sidecars: |Δ| ≤ 2 tail surgery — truncate
    extra CSV rows, or append empty-input rows on real PTS timestamps
    (no events recorded there; nothing is fabricated)."""
    info = V.probe(work / "video.mp4")
    pts = V.frame_pts(work / "video.mp4")
    header, rows = _read_csv(work)
    delta = len(rows) - info.frame_count
    if abs(delta) > 2:
        raise FixFailed(f"row delta {delta} beyond tail surgery (|Δ|≤2); "
                        f"needs sidecars for a re-translate")
    if delta > 0:
        rows = rows[:info.frame_count]
    elif delta < 0:
        if not pts or len(pts) != info.frame_count:
            raise FixFailed("PTS unreadable — cannot extend tail")
        width = len(header)
        ti = header.index("timestamp_ms")
        fi = header.index("frame_id")
        for i in range(len(rows), info.frame_count):
            r = [""] * width
            r[fi] = str(i)
            r[ti] = str(int(round(pts[i] / 1000.0)))
            dxc = header.index("input_mouse_dx")
            dyc = header.index("input_mouse_dy")
            if rows and rows[-1][dxc] != "":
                r[dxc] = r[dyc] = "0.0"
            rows.append(r)
    _write_csv(work, header, rows)
    return f"row surgery: delta {delta} resolved to {len(rows)} rows"


def fix_header_rewrite(work: Path) -> str:
    header, rows = _read_csv(work)
    if header == V2_FRAME_COLS:
        return "header already v2"
    old = {c: i for i, c in enumerate(header)}
    out = []
    for r in rows:
        out.append([r[old[c]] if c in old and old[c] < len(r) else ""
                    for c in V2_FRAME_COLS])
    _write_csv(work, V2_FRAME_COLS, out)
    return f"header rewritten ({len(header)} -> {len(V2_FRAME_COLS)} cols)"


def fix_camera_null(work: Path) -> str:
    header, rows = _read_csv(work)
    keep = {"frame_id", "timestamp_ms", "input_keys", "input_actions",
            "input_mouse_buttons", "input_mouse_dx", "input_mouse_dy"}
    idxs = [i for i, c in enumerate(header) if c not in keep]
    n = 0
    for r in rows:
        for i in idxs:
            if r[i] != "":
                r[i] = ""
                n += 1
    _write_csv(work, header, rows)
    return f"nulled {n} camera cells"


def fix_sentinels(work: Path) -> str:
    header, rows = _read_csv(work)
    dxi = header.index("input_mouse_dx")
    dyi = header.index("input_mouse_dy")
    has_motion = any(r[dxi] not in ("", "0", "0.0") or
                     r[dyi] not in ("", "0", "0.0") for r in rows)
    fixed = 0
    for r in rows:
        for i in (dxi, dyi):
            v = r[i]
            if has_motion:
                if v in ("", "0"):
                    r[i] = "0.0"
                    fixed += 1
                elif "." not in v:
                    r[i] = f"{float(v):.1f}"
                    fixed += 1
            else:
                if v != "":
                    r[i] = ""
                    fixed += 1
    _write_csv(work, header, rows)
    return f"sentinels normalized in {fixed} cells " \
           f"({'float 0.0' if has_motion else 'blank — no mouse capture'})"


def fix_sessionjson_recompute(work: Path, game: str) -> str:
    """Recompute session.json from video + CSV ground truth (the final
    consistency pass of every fix chain)."""
    from translator.v2 import GAME_TITLES, LOCALIZATIONS
    info = V.probe(work / "video.mp4")
    s = json.loads((work / "session.json").read_text())
    created_raw = s.get("created_at_utc")
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        if created.tzinfo is None:
            # a naive stamp is UTC by contract — repair it in place so
            # .astimezone below can't shift it by the host's offset
            created = created.replace(tzinfo=timezone.utc)
            s["created_at_utc"] = created.strftime(
                "%Y-%m-%dT%H:%M:%S.%f") + "Z"
    except (AttributeError, ValueError):
        created = datetime.now(timezone.utc)
        s["created_at_utc"] = created.strftime(
            "%Y-%m-%dT%H:%M:%S.%f") + "Z"
    ended = created + timedelta(seconds=info.duration_s)
    slug = game if game in C.GAMES else \
        (game_key_from_name(s.get("game_title", "")) or game)
    s.update(
        vendor_name=C.VENDOR,
        game_title=GAME_TITLES.get(slug, s.get("game_title", slug)),
        ended_at_utc=ended.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f") + "Z",
        duration_ms=round(info.duration_s * 1000.0),
        duration_seconds=round(info.duration_s, 3),
        fps=info.fps, frame_count=info.frame_count,
        record_width_px=info.width, record_height_px=info.height,
        platform=s.get("platform") or "PC",
        localization=s.get("localization")
        or LOCALIZATIONS.get(slug, "en-US"))
    s.setdefault("session_id", work.name)
    s.setdefault("screen_width_px", info.width)
    s.setdefault("screen_height_px", info.height)
    conv = s.get("input_mouse_convention")
    if not isinstance(conv, dict) or "maps_to" not in conv:
        s["input_mouse_convention"] = dict(MOUSE_CONVENTION)
    tmp = work / "session.json.tmp"
    tmp.write_text(json.dumps(s, indent=2))
    tmp.replace(work / "session.json")
    return "session.json recomputed from video+CSV ground truth"


def fix_v1_to_v2(work: Path, game: str) -> str:
    """ARR_V1_FORMAT: mechanical v1→v2 (playbook §6 — actions are already
    resolved; raws not needed)."""
    s = json.loads((work / "session.json").read_text())
    if "canonical" not in s and "game_title" in s:
        # already a flat v2 session that merely carries a stray
        # key_binding.json — the correct fix is deleting the file, never
        # running the v1 conversion over v2 display-case keys (review-2 #8)
        (work / "key_binding.json").unlink(missing_ok=True)
        return "stray key_binding.json deleted (session was already v2)"
    canonical = s.get("canonical", {})
    header, rows = _read_csv(work)
    col = {c: i for i, c in enumerate(header)}
    need = ["frame_id", "timestamp_ms", "input_keys", "input_actions",
            "input_mouse_buttons", "input_mouse_dx", "input_mouse_dy"]
    if not all(c in col for c in need):
        raise FixFailed("v1 frames.csv missing input columns")
    slug = game or game_key_from_name(canonical.get("game", "")) or ""
    kb = dict(KEYBINDS.get(slug, {}))
    kb.update(KEYBIND_PATCHES.get(slug, {}))
    bound = bound_literals(kb) if kb else frozenset()

    out = []
    from translator.binner import C2W_COLS, CAMERA_COLS
    from translator.v2 import CAMERA_EXTRA_COLS
    cam_null = [""] * (len(C2W_COLS) + len(CAMERA_COLS)
                       + len(CAMERA_EXTRA_COLS))
    has_motion = any(r[col["input_mouse_dx"]] not in ("", "0")
                     or r[col["input_mouse_dy"]] not in ("", "0")
                     for r in rows)
    for r in rows:
        keys = [t for t in (r[col["input_keys"]] or "").split("|") if t]
        # canonicalize before the bound test — bound_literals is lowercase
        # canonical while v1 files may carry either case (review-2 #8)
        kept = [key_display(key_canonical(t)) for t in keys
                if not bound or key_canonical(t) in bound]
        btns = [_BTN_DISPLAY.get(b, b)
                for b in (r[col["input_mouse_buttons"]] or "").split("|")
                if b]
        dx, dy = r[col["input_mouse_dx"]], r[col["input_mouse_dy"]]
        if has_motion:
            dx = f"{float(dx or 0):.1f}"
            dy = f"{float(dy or 0):.1f}"
        else:
            dx = dy = ""
        out.append([r[col["frame_id"]], r[col["timestamp_ms"]]] + cam_null
                   + ["|".join(kept), r[col["input_actions"]],
                      "|".join(btns), dx, dy])
    _write_csv(work, V2_FRAME_COLS, out)

    new_s = {"session_id": canonical.get("session_id", work.name)}
    ca = canonical.get("created_at_utc")
    trim_meta = canonical.get("trim") or {}
    if ca:
        created = datetime.fromisoformat(ca.replace("Z", "+00:00"))
        created += timedelta(seconds=trim_meta.get("head_cut_s") or 0.0)
        new_s["created_at_utc"] = created.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f") + "Z"
    (work / "session.json").write_text(json.dumps(new_s, indent=2))
    fix_sessionjson_recompute(work, slug)
    (work / "key_binding.json").unlink(missing_ok=True)
    # sidecars move to raw/ like every other v2 working copy — left at the
    # root they make the engine's sniffer read the session as a raw bundle
    # and dead-end the whole v1 recovery path (review finding #11)
    raw = work / "raw"
    raw.mkdir(exist_ok=True)
    for name in ("inputs.jsonl", "metadata.json", "keybind.json"):
        if (work / name).exists():
            shutil.move(str(work / name), raw / name)
    if not (work / "rrd_creation.py").exists():
        rrdmod.write_script(work)
    (work / "session.rrd").touch()
    return f"converted v1 -> v2 ({len(out)} rows)"
