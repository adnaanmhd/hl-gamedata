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
import sqlite3
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


def _utc(ts) -> datetime | None:
    """Parseable ISO stamp -> aware datetime (naive reads as UTC);
    anything else -> None rather than a raise: every value routed here
    comes from a player-supplied file, and a bare KeyError/TypeError/
    ValueError escaped apply_fixes as an untyped crash (r-loop 7).
    Shared by has_raw_sidecars (the plan gate) and
    retranslate_from_sidecars (the consumer) so "usable" means the same
    thing at both sites (r19 #2)."""
    if not isinstance(ts, str):
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def has_raw_sidecars(work: Path) -> bool:
    """BOTH sidecars, USABLE — the condition retranslate_from_sidecars
    actually needs (it opens and parses raw/metadata.json
    unconditionally).

    validate.validate_session already required both; the two drivers
    tested only inputs.jsonl, so a zip upload missing metadata.json was
    planned a FIX_RETRANSLATE that raised FileNotFoundError on both
    attempts and was REJECTED "fix retries exhausted" — while every code
    in that group has a working CSV-level fallback. This is also the
    settled gray-zone rule ("sidecars missing: raw-needing fixes fall
    back to CSV-level") being silently violated (r-loop 7).

    PRESENT-but-unparseable metadata.json is the same failure as missing
    (r18 #4 — the r-loop-7 shape's open half): existence alone planned a
    FIX_RETRANSLATE that crashed JSONDecodeError on both attempts,
    superseding the CSV-level repairs that would have delivered the
    session. inputs.jsonl needs no parse test here — load_events reads
    it with errors='replace' line-tolerantly (r-loop 4).

    SEMANTICALLY-unusable metadata is the same failure again (r19 #2):
    the dict test alone admitted metadata whose recording.started_at_utc
    is absent or junk, and retranslate_from_sidecars hard-requires that
    stamp to derive the head offset — a typed FixFailed on both
    attempts, still superseding the CSV-level repairs that would have
    delivered the session, and nothing in the pipeline can ever repair
    raw/metadata.json between attempts. "Usable" therefore requires the
    one field the consumer cannot run without, judged by the consumer's
    own parse (_utc)."""
    raw = Path(work) / "raw"
    if not ((raw / "inputs.jsonl").exists()
            and (raw / "metadata.json").exists()):
        return False
    try:
        meta = json.loads((raw / "metadata.json").read_text(
            encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(meta, dict):
        return False
    rec = meta.get("recording")
    return isinstance(rec, dict) and _utc(rec.get("started_at_utc")) is not None


def _read_session_json(work: Path) -> dict:
    """session.json as a dict, or {} when it is unreadable/not an object.

    Player-supplied and reachable in every broken shape the checker FAILs
    on. Returning {} lets FIX_SESSIONJSON_REWRITE rebuild it from video +
    CSV ground truth instead of raising inside the fix that exists to
    repair it (r-loop 7)."""
    try:
        s = json.loads((work / "session.json").read_text(
            encoding="utf-8", errors="replace"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return s if isinstance(s, dict) else {}


# --------------------------------------------------------------- fix plan

def plan_fixes(reasons: list[dict], *, game: str, has_raw: bool) -> dict:
    """Reason codes -> ordered fix plan (§10 fix-chain column).

    Returns {"steps": [(fix_id, params)], "unfixable": [codes]}. A queued
    RETRANSLATE clears the CSV-level fixes it supersedes."""
    steps: list[tuple[str, dict]] = []
    unfixable: list[str] = []
    retranslate = False
    sj_rewrite = False
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
        elif code == "STR_SJ_INVALID":
            # NEVER route this through the retranslate, sidecars or not
            # (r-loop 7). retranslate_from_sidecars READS session.json for
            # the head offset (`created_at_utc` minus the raw metadata's
            # `started_at_utc`), so the fix depends on the very artifact
            # the FAIL says is broken: it raised identically on both
            # attempts and the session was REJECTED "fix retries
            # exhausted" under the bare fix-failed marker, while the
            # no-sidecar plan cleared the same FAIL in ONE attempt. Having
            # the required raw sidecars made a session strictly worse off.
            # FIX_SESSIONJSON_REWRITE rebuilds session.json from video +
            # CSV ground truth and is emitted BEFORE any retranslate, so
            # a session that needs both gets its precondition repaired
            # first instead of crashing on it.
            sj_rewrite = True
        elif code in ("STR_HEADER_BAD", "STR_CAMERA_NONNULL",
                      "STR_SENTINELS", "STR_TS_TAIL"):
            if has_raw:
                retranslate = True
            else:
                csv_fixes.append({
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
    if sj_rewrite:
        # BEFORE the retranslate, and NOT part of csv_fixes, so the
        # supersede below cannot drop the step the retranslate depends on
        steps.append(("FIX_SESSIONJSON_REWRITE", {}))
    if retranslate and has_raw:
        # the plan carries the reroute fact (r13 #4/G2): _dispatch
        # applies the built-in-keybind override ONLY when this is True
        steps.append(("FIX_RETRANSLATE", {"rerouted": reroute}))
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
    # structural surgery FIRST in the cut-less path too (r-loop 12 #6):
    # the rule was enforced only on cut/gate/retrim-bearing plans, so a
    # plain-CSV plan emitted hygiene/context ahead of FIX_ROWS_SURGERY —
    # and fix_actions_context hard-fails on any rows/video mismatch
    # (its track has one label per VIDEO frame), so [CONTEXT, ROWS]
    # burned both attempts on a FixFailed whose cure was one step later
    # in the same plan: a wrongful terminal reject. (For gate plans this
    # call was already here — gate.py asserts a v2 header, review-r4
    # #23; the gate STEP itself is still appended after the csv writers
    # below.) Idempotent via pre_emitted.
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
    kind = None
    persisted = 0     # applied[:persisted] already written to the fixlog
    for fix_id, params in plan["steps"]:
        try:
            if fix_id == "FIX_CUT_SEGMENTS" and applied[persisted:]:
                # DURABLE-BEFORE-THE-CUT (r-loop 10 #1): the gate blanked
                # frames.csv durably two steps ago, but its destroyed-
                # inventory record lived only in this process until the
                # single post-loop fixlog write — so a kill anywhere in
                # the cut dispatch lost the record forever: the adoption
                # paths read only the parent fixlog (applied=[]) and the
                # REVALIDATING route reads _gate_destroyed from the same
                # empty fixlog, terminally rejecting the child/parent for
                # a deficit the pipeline itself created. Persist the
                # attempt-so-far before the cut can start; the post-loop
                # append writes only the remainder (exactly-once).
                _append_fixlog(dossier_dir, applied[persisted:])
                persisted = len(applied)
            note = _dispatch(fix_id, params, work_dir, game,
                             split_root or work_dir.parent)
            if fix_id == "FIX_CUT_SEGMENTS":
                children = note
                applied.append({"fix": fix_id, "params": _jsonable(params),
                                "ok": True, "note": note})
                # the gate ran on the parent's timeline just above; its
                # record has to follow the footage into the children
                # (r-loop 6). Only the not-yet-persisted tail rides in
                # `applied` here — the pre-cut entries are found via the
                # parent fixlog walk, so nothing is seen twice.
                _propagate_gate_record(dossier_dir, dossier_dir.parent,
                                       applied[persisted:],
                                       note.get("segments") or [])
                break                     # children re-enter Phase II
            applied.append({"fix": fix_id, "params": _jsonable(params),
                            "ok": True, "note": note})
        except Exception as e:
            error = f"{fix_id}: {type(e).__name__}: {e}"
            # HOST-level vs SESSION-level, the same split run._validate_worker
            # makes (run.py:179). The fix lane was the last one without it:
            # every failure burned an attempt, so one disk-full or
            # wedged-ffmpeg episode spent BOTH attempts back to back and
            # terminally rejected the session — under the bare fix-failed
            # marker, because the stored reasons are all still fixable, so
            # an infrastructure failure was reported to the player as a
            # fault in their footage (r-loop 7 BLOCKER).
            #
            # CalledProcessError is deliberately NOT host: ffmpeg exiting
            # non-zero is usually the SESSION's bytes being undecodable,
            # and treating that as a host condition would retry a
            # genuinely broken clip forever instead of rejecting it.
            kind = "host" if isinstance(
                e, (OSError, MemoryError, sqlite3.OperationalError,
                    subprocess.TimeoutExpired)) else "session"
            applied.append({"fix": fix_id, "params": _jsonable(params),
                            "ok": False, "note": str(e)[:300]})
            break
    if applied[persisted:]:
        _append_fixlog(dossier_dir, applied[persisted:])
    return {"applied": applied, "children": children, "error": error,
            "kind": kind}


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
        # override ONLY on reroute plans (review-2 #5: the raw metadata
        # is exactly what the mismatch falsified — the built-in for the
        # corrected game governs). `game` is the ledger slug, which
        # ingest scoping keeps ALWAYS in C.GAMES, so the old bare
        # `game in C.GAMES` test was vacuously true and the built-in
        # silently overrode the session's own raw/keybind.json on EVERY
        # production retranslate — the F4 doctrine's third instance
        # (r13 #4/G2). The plan carries the reroute fact; both drivers
        # resolve `game` to the corrected slug before apply_fixes.
        return retranslate_from_sidecars(
            work, game_override=game
            if params.get("rerouted") and game in C.GAMES else None,
            ledger_game=game if game in C.GAMES else None)
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
        # make_rrd=False: in the fix path out_dir IS the work dir, and
        # NOTHING reads that rrd -- v2.py only checks session.rrd EXISTS
        # (which is why ingest.download touches a 0-byte stub), and
        # deliver.stage_session regenerates it inside the stage dir. The
        # render embeds the whole clip via rr.AssetVideo and logs 5
        # entries per frame, so it came out roughly VIDEO-SIZED: minutes
        # inside the runner's gate slot, and ~2x that session's bytes on
        # disk against a cap (CONT_MEDIA_CAP_SESSIONS) that counts
        # SESSIONS as its bytes bound (r-loop 5).
        return tool.retrim(work, params["head_s"], work, make_rrd=False)
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

def _propagate_gate_record(parent_dossier: Path, dossier_root: Path,
                           applied: list[dict], segments: list[dict]) -> None:
    """Split children must inherit the parent's GATE record.

    Since r-loop 5 a cut-bearing plan gates BEFORE it cuts, so the
    parent's rows are blanked and cutter copies the blanked rows into
    every child verbatim. But the destroyed-inventory record lives in the
    PARENT's dossier, while each child is validated against its own fresh
    dossier — so validate._gate_destroyed saw nothing and the child took
    the wrongful CNT_ACTIONS_FEW / INP_KEYS_MISSING reject the record
    exists to prevent (r-loop 6). Same shape as _propagate_shift_record
    below, and for the same reason: state established on the parent's
    timeline has to follow the footage.
    """
    # Walk EARLIER attempts (parent fixlog) then the CURRENT attempt in
    # order, so every gate entry can be brought onto the CURRENT parent
    # clock first: stored spans are on the clock AT GATE TIME, and every
    # ok FIX_RETRIM_HEAD applied AFTER a gate entry shifted the parent's
    # surviving rows earlier by its actual cut — comparing stale spans
    # against post-trim segment bounds withheld the record from the
    # segment that contains the blanked rows and handed it to the sibling
    # (r-loop 9 #11).
    ordered: list[dict] = []
    try:
        log = json.loads((parent_dossier / "fixlog.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        log = []
    if isinstance(log, list):
        for rec in log:
            if isinstance(rec, dict):
                ordered.extend(e for e in (rec.get("fixes") or [])
                               if isinstance(e, dict))
    ordered.extend(e for e in applied if isinstance(e, dict))
    gate_entries = []
    for i, e in enumerate(ordered):
        if e.get("fix") != "FIX_GATE_WINDOW" or not e.get("ok"):
            continue
        off = sum(_retrim_cut(later) for later in ordered[i + 1:]
                  if later.get("fix") == "FIX_RETRIM_HEAD"
                  and later.get("ok"))
        gate_entries.append(_rebase_gate_entry(e, off))
    if not gate_entries:
        return
    for seg in segments:
        sid = seg.get("id")
        if not sid:
            continue
        # ONLY the segment that actually contains the gated window
        # (r-loop 7). cutter._cut_loop gives each child its own row slice
        # (`rows[i0:i0 + m]`), so the blanked rows land in exactly ONE
        # segment — the docstring's "cutter copies the blanked rows into
        # every child verbatim" is false. Handing the record to a sibling
        # whose rows were never touched let validate._gate_destroyed
        # downgrade that sibling's GENUINE CNT_ACTIONS_FEW /
        # INP_KEYS_MISSING to an advisory, so a segment with 2 distinct
        # actions and zero key frames shipped — violating two locked
        # delivery bars — under two operator advisories that were false
        # statements about it. And per WINDOW, not just per entry
        # (r-loop 8): one FIX_GATE_WINDOW step carries ALL windows with
        # ONE aggregate inventory, so with frozen windows in two
        # different segments BOTH inherited the FULL inventory and a
        # sibling's genuine deficit was downgraded by inventory destroyed
        # elsewhere. Entries with per_window notes hand each segment only
        # its overlapping windows' share; legacy entries keep the
        # whole-entry behaviour.
        mine = _entries_for_segment(gate_entries, seg.get("t0"),
                                    seg.get("t1"), parent_dossier.name)
        if not mine:
            continue
        # rebase every span into the CHILD's clock: its row 0 sits at
        # parent-clock t0 (cutter used src_pts[i0]; t0 is its rounded
        # twin — the <=1ms skew is absorbed by the pad-widened spans).
        # Without this a level-2 split compared child-clock bounds
        # against parent-clock spans and dropped the record from ALL
        # grandchildren (r-loop 9 #20). t0 unknown -> unadjusted.
        try:
            child_off = float(seg.get("t0"))
        except (TypeError, ValueError):
            child_off = 0.0
        if child_off:
            mine = [_rebase_gate_entry(e, child_off) for e in mine]
        # OSError propagates: apply_fixes' except classifies it HOST and
        # the r-loop-8 carve-out discards the rescinded cut and
        # re-derives — the old `except OSError: pass` silently shipped a
        # child without its record on ENOSPC, the exact silent-drop this
        # function's doctrine forbids (r-loop 9 #14).
        _append_fixlog(dossier_root / sid, mine)


def _retrim_cut(entry: dict) -> float:
    """The ACTUAL head cut an ok FIX_RETRIM_HEAD removed: the tool's note
    records head_cut_s (keyframe-snapped); the requested params.head_s is
    only the fallback for older/torn notes (r-loop 9 #11)."""
    note = entry.get("note") if isinstance(entry.get("note"), dict) else {}
    v = note.get("head_cut_s")
    if v is None:
        v = (entry.get("params") or {}).get("head_s")
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rebase_gate_entry(entry: dict, off: float) -> dict:
    """A fresh copy of a gate entry with every readable [a, b] span
    shifted earlier by `off` seconds (clamped at 0) — used both to bring
    stored spans onto the CURRENT parent clock (subtract later retrim
    cuts) and to move selected entries onto a CHILD's clock (subtract the
    segment's t0). Unreadable spans are copied unadjusted: never drop,
    never guess (r-loop 9 #11/#20). Always copies — the caller's entries
    may still be written to the parent fixlog afterwards."""
    e = json.loads(json.dumps(entry, default=str))
    if not off:
        return e

    def shift(spans):
        if not isinstance(spans, list):
            return spans
        out = []
        for w in spans:
            try:
                a, b = float(w[0]), float(w[1])
                out.append([max(a - off, 0.0), max(b - off, 0.0)])
            except (TypeError, ValueError, IndexError, KeyError):
                out.append(w)
        return out

    params = e.get("params")
    if isinstance(params, dict) and "windows" in params:
        params["windows"] = shift(params.get("windows"))
    note = e.get("note")
    if isinstance(note, dict):
        if "windows" in note:
            note["windows"] = shift(note.get("windows"))
        pw = note.get("per_window")
        if isinstance(pw, list):
            for w in pw:
                if isinstance(w, dict):
                    if "windows" in w:
                        w["windows"] = shift(w.get("windows"))
                    if w.get("requested") is not None:
                        s = shift([w.get("requested")])
                        w["requested"] = s[0]
    return e


def _spans_touch(spans, t0, t1) -> bool:
    """Does ANY [a, b] span overlap [t0, t1)? Empty/unknown/unreadable
    spans count as touching — a record must never be dropped silently,
    only withheld from a segment proved not to contain it."""
    if not spans:
        return True
    for w in spans:
        try:
            a, b = float(w[0]), float(w[1])
        except (TypeError, ValueError, IndexError, KeyError):
            return True
        if a < float(t1) and b > float(t0):
            return True
    return False


def _gate_entry_touches(entry: dict, t0, t1) -> bool:
    """Does a gate entry's blanked span overlap [t0, t1) on the parent
    clock? Tested against the note's ACTUALLY-blanked spans (pads
    included, r-loop 8) when present, falling back to the requested
    params. Unknown or unreadable bounds propagate."""
    if t0 is None or t1 is None:
        return True
    note = entry.get("note") if isinstance(entry.get("note"), dict) else {}
    windows = note.get("windows") or \
        (entry.get("params") or {}).get("windows") or []
    return _spans_touch(windows, t0, t1)


def _entries_for_segment(gate_entries: list[dict], t0, t1,
                         parent: str) -> list[dict]:
    """The gate entries (or synthetic per-window shares) one segment
    inherits (r-loop 8). An entry whose note carries `per_window` is
    narrowed to the windows whose APPLIED spans (fallback: requested)
    overlap [t0, t1): none -> the entry is withheld; some -> a SYNTHETIC
    entry with only their union inventory. Anything unreadable, and every
    legacy entry without per_window, propagates whole (never drop
    silently)."""
    out: list[dict] = []
    for e in gate_entries:
        note = e.get("note") if isinstance(e.get("note"), dict) else {}
        pw = note.get("per_window")
        if not (isinstance(pw, list) and pw) or t0 is None or t1 is None:
            if _gate_entry_touches(e, t0, t1):
                out.append(e)
            continue
        selected = []
        unreadable = False
        for w in pw:
            if not isinstance(w, dict):
                unreadable = True
                break
            spans = w.get("windows") or [w.get("requested")]
            if _spans_touch(spans, t0, t1):
                selected.append(w)
        if unreadable:
            out.append(e)               # never drop what we cannot read
            continue
        if not selected:
            continue
        acts = sorted({a for w in selected
                       for a in ((w.get("destroyed") or {}).get("actions")
                                 or []) if isinstance(a, str)})
        keys = 0
        for w in selected:
            try:
                keys += int((w.get("destroyed") or {}).get("key_frames")
                            or 0)
            except (TypeError, ValueError):
                pass
        out.append({"fix": "FIX_GATE_WINDOW", "ok": True,
                    "params": {"windows": [w.get("requested")
                                           for w in selected]},
                    "note": {"windows": [s for w in selected
                                         for s in (w.get("windows") or [])],
                             "destroyed": {"actions": acts,
                                           "key_frames": keys},
                             "propagated_from": parent}})
    return out


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
    # try/finally: the cleanup was success-path-only, so every FAILED
    # attempt leaked a video-sized `_translated/` tree inside the working
    # copy — twice per session against a media cap that counts sessions
    # as its bytes bound (r-loop 8)
    try:
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
        # the applied sync shift must survive the temp-out cleanup — qa's
        # raw recomputation reads it from the work root (review finding #10)
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
        return f"translated raw bundle: {res['data_quality']}"
    finally:
        shutil.rmtree(out, ignore_errors=True)


def retranslate_from_sidecars(work: Path, *,
                              game_override: str | None = None,
                              ledger_game: str | None = None) -> str:
    """FIX_RETRANSLATE for a v2 upload with raw sidecars (R3): re-bin the
    events onto the DELIVERED video (no re-trim — the head offset comes from
    metadata started_at vs session created_at), re-run lag correction and
    context gating, rewrite frames.csv. The universal strong fix.

    `game_override` is the REROUTED game: the raw metadata is exactly what
    the mismatch falsified, so on reroute the built-in keybind for the
    corrected game governs, never the metadata-derived one (review-2 #5).

    `ledger_game` anchors the NON-reroute branch (r-loop 14 #1≡#6): the
    player-typed chain (metadata game name / game_name / game_title) is
    degradable to numeric/absent/wrong-game garbage, and anchoring the
    built-in fallback and the slug on it let resolve_keybind return {} —
    stripping 100% of key presses into an unfixable terminal reject — or
    re-bin under the WRONG game's built-in. The ledger slug _dispatch
    holds governs instead, exactly as the F4 siblings (fix_key_hygiene,
    fix_actions_context) already do; the session's own raw/keybind.json
    still wins when usable (r13 #4 intent intact)."""
    from translator.v2 import GAME_TITLES
    raw = work / "raw"
    # see translator/v2.py: player-typed free text, non-UTF-8 reachable.
    # has_raw_sidecars gates planning on this file PARSING (r18 #4), so
    # a crash here should be unreachable — belt-and-braces: any residual
    # path (direct callers, a race on the file) stays a typed, named
    # failure instead of a bare JSONDecodeError
    try:
        meta = json.loads((raw / "metadata.json").read_text(
            encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as e:
        raise FixFailed(f"raw/metadata.json unreadable "
                        f"({type(e).__name__}: {e})") from e
    s = _read_session_json(work)
    game_info = meta.get("game", {}) if isinstance(meta, dict) else {}
    if not isinstance(game_info, dict):
        game_info = {}
    exe_name = game_info.get("exe_name")
    if not isinstance(exe_name, str):
        # same provenance and crash as translate_bundle_v2's guard: a
        # numeric exe_name reached game_key_from_name's re.sub (r-loop 9)
        exe_name = None
    if game_override:
        slug = game_override
        game_name = GAME_TITLES.get(slug, slug)
    else:
        game_name = game_info.get("name") or meta.get("game_name") \
            or s.get("game_title")
        # ledger slug first (r-loop 14 #1≡#6): every downstream consumer
        # of this slug (the KEYBIND_PATCHES update, the context-gating
        # test, fix_sessionjson_recompute) keys on the game the LEDGER
        # holds, never on the player-typed chain
        slug = ledger_game or game_key_from_name(game_name or "", exe_name) \
            or "unknown_game"

    # the module-level _utc (shared with has_raw_sidecars, r19 #2); a
    # truthy non-dict "recording" block used to crash the old
    # `(meta.get("recording") or {}).get(...)` with an untyped
    # AttributeError — same player-file class, same degrade
    rec = meta.get("recording") if isinstance(meta, dict) else None
    started = _utc(rec.get("started_at_utc") if isinstance(rec, dict)
                   else None)
    created = _utc(s.get("created_at_utc") if isinstance(s, dict) else None)
    if started is None or created is None:
        raise FixFailed(
            "cannot derive the head offset: raw metadata started_at_utc "
            f"({'ok' if started else 'unusable'}) / session.json "
            f"created_at_utc ({'ok' if created else 'unusable'}) — "
            "session.json must be repaired before a re-translate")
    head_s = max((created - started).total_seconds(), 0.0)

    info = V.probe(work / "video.mp4")
    pts = V.frame_pts(work / "video.mp4")
    raw_events = load_events(raw / "inputs.jsonl")
    if game_override:
        keybind = dict(KEYBINDS.get(slug, {}))
    else:
        # the fallback anchor is the ledger slug (r-loop 14 #1≡#6): the
        # session's own keybind.json still wins when usable, and
        # resolve_keybind's internal parsed-but-unusable fallback lands
        # on the RIGHT built-in once anchored here
        keybind = resolve_keybind(keybind_path=raw / "keybind.json",
                                  game_name=ledger_game or game_name,
                                  exe_name=exe_name)
    keybind.update(KEYBIND_PATCHES.get(slug, {}))
    rules = build_resolver(keybind)
    bound = bound_literals(keybind)

    carried: list = []
    events = trimmod.rebase_events(raw_events, head_s, info.duration_s,
                                   carried_out=carried)
    # OUTPUT-based bogus-stamp defence (r-loop 8 BLOCKER — replaces the
    # r-loop-7 `head_s > duration_s` guard). Split children LEGITIMATELY
    # have head_s far beyond their own length: cutter.py stamps every
    # child created_at = parent_created + src_pts[i0] and copies raw/
    # precisely so children can retranslate, so head_s is the offset into
    # the RAW recording, not into this clip — the duration test wrongly
    # terminal-rejected every second-or-later segment on both attempts.
    # What the old guard actually defended against was shipping a
    # frames.csv with empty input columns off stamps that do not describe
    # this video; test THAT directly. Carried-only counts as zero: with a
    # bogus head every unmatched 'down' in the sidecar (keys held when
    # capture stopped) is re-pressed at t=0, so a plain non-empty test
    # would fabricate a full-clip hold of those keys — a legitimate split
    # child always retains in-band events (r-loop 9).
    if raw_events and len(events) == len(carried):
        raise FixFailed(
            f"head offset {head_s:.1f}s leaves zero events beyond "
            f"{len(carried)} held-key carries from a "
            f"non-empty sidecar — session.json created_at_utc and raw "
            f"metadata started_at_utc do not describe this video; "
            f"refusing to re-bin")
    rows, stats = bin_session(events, info, keybind, rules, bound,
                              frame_pts_us=pts)

    shift_us = 0
    note = ""
    if sync.available() and stats.has_mouse_motion:
        try:
            mdx, mdy = sync.motion_track(work / "video.mp4")
        except Exception as e:
            # opencv open/decode failure: skip lag correction with a
            # trail instead of burning the fix attempt (r-loop 10 #10)
            note = (f"lag correction skipped (video not decodable by "
                    f"opencv: {type(e).__name__})")
            mdx = None
    else:
        mdx = None
    if mdx is not None:
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
    v2rows = _v2_rows(rows, bound, strip, rules)
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
    kbp = work / "raw" / "keybind.json"
    if kbp.exists():
        # the session's own keybind.json is AUTHORITATIVE (r-loop 11
        # #4/#11): judging `bound` against the built-ins alone made the
        # r10 unbound strip delete every custom-bound key press (and the
        # action re-resolution erase its actions) — silent delivered-data
        # corruption that then passed the checker cleanly. Resolve
        # exactly as retranslate_from_sidecars does; resolve_keybind
        # falls back to the built-in itself when the file is unusable.
        kb = resolve_keybind(keybind_path=kbp, game_name=game,
                             exe_name=None)
    else:
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
        if bound:
            # mirror _v2_rows' delivery invariant (r-loop 10 #9): an
            # unbound key resolves no action, so keeping it re-fires the
            # keys-without-null-actions FAIL this fix is planned for —
            # a no-op loop that burned the budget into a wrongful reject.
            # No-keybind sessions keep every token (actions cannot be
            # re-resolved there anyway).
            unbound = {t for t in kset if t not in bound}
            stripped += len(unbound)
            kset -= unbound
        # buttons canonicalize through the FULL vocabulary (r-loop 10 #7):
        # the exact-name round-trip passed foreign tokens ('left',
        # 'Mouse4', 'LMB') through verbatim, so the checker's non-v2-token
        # FAIL re-fired identically after the "fix". Unmappable tokens are
        # dropped (and counted) so the set test can never re-fire.
        btns = []
        for b in (r[bi] or "").split("|"):
            if not b:
                continue
            canon = _BTN_DISPLAY_INV.get(b) \
                or K.MOUSE_BUTTONS.get(b.lower()) \
                or (b.lower() if b.lower() in _BTN_DISPLAY else None)
            if canon:
                btns.append(canon)
            else:
                stripped += 1
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
            credited: set[str] = set()
            acts, _ = resolve_actions(kset | set(btns), motion, rules,
                                      credited_out=credited)
            if bound:
                # mirror _v2_rows' credited-token rule (r15 #5): a
                # {modifier, key} combo half held alone is bound but
                # satisfies no rule, so keeping it re-fires the
                # keys-without-actions FAIL this fix is planned for —
                # the bound sibling of the unbound strip above. Buttons
                # keep today's behavior (the checker's invariant covers
                # keys only). Stripping after the resolve is exact: an
                # uncredited token contributed to no satisfied rule, so
                # the resolved actions cannot change.
                uncredited = {t for t in kset if t not in credited}
                stripped += len(uncredited)
                kset -= uncredited
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
    kbp = work / "raw" / "keybind.json"
    if kbp.exists():
        # the session's own keybind.json is AUTHORITATIVE here exactly as
        # in fix_key_hygiene (F4) — this step REWRITES input_actions for
        # every row, so resolving with the built-ins re-labeled every
        # custom-bound session (interact presses shipped as the built-in
        # semantic, checker-green) and undid hygiene's correct resolution
        # one step later in the same plan (r-loop 12 #5/#8).
        kb = resolve_keybind(keybind_path=kbp, game_name=game,
                             exe_name=None)
    else:
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
    try:
        mdx, mdy = sync.motion_track(work / "video.mp4")
    except (OSError, MemoryError, sqlite3.OperationalError,
            subprocess.TimeoutExpired):
        # host classes propagate to apply_fixes' classifier untouched
        # (attempt refunded, cooldown) — the r10 guard below re-typed
        # them as session-kind FixFailed and burned the attempt on an
        # infrastructure failure (r-loop 11 #3)
        raise
    except Exception as e:
        # typed, attributable failure instead of an untyped ValueError
        # burning the attempt (r-loop 10 #10)
        raise FixFailed(f"lag shift cannot measure: video not decodable "
                        f"by opencv ({type(e).__name__})")

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
    """Judged by the checker's OWN _FLOAT_RE (r-loop 10 #8): the old
    ''/'0'/no-dot heuristic left dotted-but-nonconformant cells ('.5',
    '1.', '+1.0', '1.2e3') verbatim — the FAIL re-fired identically on
    both attempts — and crashed with an uncaught ValueError on dotless
    non-numeric cells ('abc'). Importing the regex keeps fix and checker
    from drifting, the same pattern fix_sessionjson_recompute adopted for
    the SJ enums in r-loop 8."""
    import math

    from translator.v2 import _FLOAT_RE
    header, rows = _read_csv(work)
    dxi = header.index("input_mouse_dx")
    dyi = header.index("input_mouse_dy")

    def _parse(v) -> float:
        # unparseable/non-finite degrades to 0.0, like translator/v2's
        # _num_cell — a junk cell is not motion
        try:
            x = float(v)
        except (TypeError, ValueError):
            return 0.0
        return x if math.isfinite(x) else 0.0

    has_motion = any(_parse(r[dxi]) != 0.0 or _parse(r[dyi]) != 0.0
                     for r in rows)
    fixed = 0
    for r in rows:
        for i in (dxi, dyi):
            v = r[i]
            if has_motion:
                if not _FLOAT_RE.match(v or ""):
                    r[i] = f"{_parse(v):.1f}"
                    fixed += 1
            else:
                if v != "":
                    r[i] = ""
                    fixed += 1
    _write_csv(work, header, rows)
    return f"sentinels normalized in {fixed} cells " \
           f"({'float 0.0' if has_motion else 'blank — no mouse capture'})"


def _conv_valid(conv) -> bool:
    """Replicates check_session_v2's acceptance of input_mouse_convention
    (translator/v2._check_session_json). The rewrite must overwrite
    anything the checker would FAIL — it used to default only
    ABSENT/FALSY fields, so a PRESENT-but-invalid convention survived
    both attempts into a fix-failed reject (r-loop 8)."""
    from translator.v2 import _CAMERA_MAPS, _MAPS_TO
    if not isinstance(conv, dict):
        return False
    need = ("maps_to", "dx_positive", "dx_negative",
            "dy_positive", "dy_negative")
    if any(k not in conv for k in need):
        return False
    m = conv["maps_to"]
    if not isinstance(m, str) or m not in _MAPS_TO:
        return False
    if m == "other" and not conv.get("maps_to_other"):
        return False
    if m in _CAMERA_MAPS:
        if any(not isinstance(conv[k], str) for k in need[1:]):
            return False
        if conv["dx_positive"] not in {"right", "left"} or \
                conv["dx_negative"] not in {"right", "left"}:
            return False
        if conv["dy_positive"] not in {"down", "up"} or \
                conv["dy_negative"] not in {"down", "up"}:
            return False
        return True
    return all(conv[k] == "not_applicable" for k in need[1:])


def fix_sessionjson_recompute(work: Path, game: str) -> str:
    """Recompute session.json from video + CSV ground truth (the final
    consistency pass of every fix chain).

    VALIDATES what it keeps (r-loop 8): the rewrite used to default only
    ABSENT/FALSY fields while the checker rejects PRESENT-but-invalid
    values — platform 'Windows', localization 'english', a partial or
    wrong-axis input_mouse_convention, an aware-but-nonconforming
    created_at_utc all survived both attempts into a fix-failed reject
    with three paid sweeps. The checker's own enums/regexes are the
    acceptance tests here, so the two can never drift apart silently."""
    from translator.v2 import (GAME_TITLES, LOCALIZATIONS, _LOC_RE,
                               _PLATFORMS, _TS_RE)
    info = V.probe(work / "video.mp4")
    # unreadable / non-object session.json starts from {} and is rebuilt
    # from ground truth — this IS the fix for a broken session.json, so it
    # must not crash on one (r-loop 7)
    s = _read_session_json(work)
    created_raw = s.get("created_at_utc")
    parsed_ok = True
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        if created.tzinfo is None:
            # a naive stamp is UTC by contract — repair it in place so
            # .astimezone below can't shift it by the host's offset
            created = created.replace(tzinfo=timezone.utc)
    except (AttributeError, ValueError):
        created = datetime.now(timezone.utc)
        parsed_ok = False
    # re-emit canonically whenever the ORIGINAL string is not
    # checker-conformant: covers the naive case (already handled above)
    # AND the aware-nonconforming ones — '+0000' offsets and space
    # separators parse fine but fail _TS_RE, so they used to be kept
    # verbatim and re-FAILed identically (r-loop 8). The checker's
    # acceptance is regex AND parse, so a regex-shaped but unparseable
    # stamp (hour 25, month 13) must re-emit too — kept verbatim it
    # re-FAILed 'timestamps unparseable' on both attempts (r-loop 9 #13)
    if not parsed_ok or not isinstance(created_raw, str) \
            or not _TS_RE.match(created_raw):
        s["created_at_utc"] = created.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f") + "Z"
    ended = created + timedelta(seconds=info.duration_s)
    slug = game if game in C.GAMES else \
        (game_key_from_name(s.get("game_title", "")) or game)
    plat = s.get("platform")
    if not (isinstance(plat, str) and plat in _PLATFORMS):
        plat = "PC"
    loc = s.get("localization")
    if not (isinstance(loc, str) and _LOC_RE.match(loc)):
        loc = LOCALIZATIONS.get(slug, "en-US")
    s.update(
        vendor_name=C.VENDOR,
        game_title=GAME_TITLES.get(slug, s.get("game_title", slug)),
        ended_at_utc=ended.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f") + "Z",
        duration_ms=round(info.duration_s * 1000.0),
        duration_seconds=round(info.duration_s, 3),
        fps=info.fps, frame_count=info.frame_count,
        record_width_px=info.width, record_height_px=info.height,
        platform=plat,
        localization=loc)
    s.setdefault("session_id", work.name)
    s.setdefault("screen_width_px", info.width)
    s.setdefault("screen_height_px", info.height)
    if not _conv_valid(s.get("input_mouse_convention")):
        s["input_mouse_convention"] = dict(MOUSE_CONVENTION)
    tmp = work / "session.json.tmp"
    tmp.write_text(json.dumps(s, indent=2))
    tmp.replace(work / "session.json")
    return "session.json recomputed from video+CSV ground truth"


def _v1_sidecar_started(work: Path) -> datetime | None:
    """The v1 work dir's usable raw started_at_utc, else None — the
    has_raw_sidecars usability rule, at both locations fix_v1_to_v2 can
    see them in: the work ROOT on a first run (this fix moves the
    sidecars into raw/ only at its end) and raw/ on a re-entrant run
    (the K2 two-location rule). Non-None means the head-offset contract
    (created − started == binning head) will be LIVE for this session's
    raw verify and any later retranslate. Each file is located
    INDEPENDENTLY: a crash between this fix's own per-file moves leaves
    the pair split across root and raw/, and the re-entrant run's move
    reunites them — a pair-per-location test would read that split as
    no-sidecars and fabricate the stamp the reunited contract then
    judges."""
    def _find(name: str) -> Path | None:
        for base in (work, work / "raw"):
            if (base / name).exists():
                return base / name
        return None

    mp, ip = _find("metadata.json"), _find("inputs.jsonl")
    if mp is None or ip is None:
        return None
    try:
        meta = json.loads(mp.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    rec = meta.get("recording") if isinstance(meta, dict) else None
    return _utc(rec.get("started_at_utc") if isinstance(rec, dict)
                else None)


def fix_v1_to_v2(work: Path, game: str) -> str:
    """ARR_V1_FORMAT: mechanical v1→v2 (playbook §6 — actions are already
    resolved; raws not needed).

    Every read of the player-typed v1 payload DEGRADES instead of
    crashing (r18 #1≡#2≡#3≡#5 — the class K3 fixed at _active and its
    sweep NOTED here): this route's crashes are unrescuable by design,
    because the checker early-returns on key_binding.json before the
    CSV is ever scanned, so no STR_SENTINELS (or any other repair) can
    ever precede this step — an uncaught ValueError burned both
    attempts into a wrongful terminal reject of a repairable session.
    A corrupt session.json is reachable here too: sniff types the
    payload v1 on key_binding.json alone, without parsing it.

    Degrading must never FABRICATE the head-offset contract (r19 #1,
    the iteration-19 BLOCKER): wherever usable raw sidecars exist,
    created_at_utc − started_at_utc IS the binning head offset —
    _verify_against_raw re-bins by it and retranslate_from_sidecars
    derives head_s from it — so a made-up value there ships a silently
    desynced retranslate or manufactures a wrongful terminal reject.
    On that route an unusable stamp is RECOVERED from ground truth
    (started_at + head_cut) and an unusable head cut is a typed
    refusal; only the no-sidecar route keeps the pure degrade arms,
    where nothing downstream consumes the difference."""
    s = _read_session_json(work)
    if "canonical" not in s and "game_title" in s:
        # already a flat v2 session that merely carries a stray
        # key_binding.json — the correct fix is deleting the file, never
        # running the v1 conversion over v2 display-case keys (review-2 #8)
        (work / "key_binding.json").unlink(missing_ok=True)
        return "stray key_binding.json deleted (session was already v2)"
    canonical = s.get("canonical", {})
    if not isinstance(canonical, dict):
        # a non-dict canonical block would crash every .get below —
        # degrade to {}: session_id falls back to the folder name and
        # fix_sessionjson_recompute rebuilds the rest from ground truth
        canonical = {}
    header, rows = _read_csv(work)
    col = {c: i for i, c in enumerate(header)}
    need = ["frame_id", "timestamp_ms", "input_keys", "input_actions",
            "input_mouse_buttons", "input_mouse_dx", "input_mouse_dy"]
    if not all(c in col for c in need):
        raise FixFailed("v1 frames.csv missing input columns")
    for r in rows:
        # r19 #3 (M3): one ragged row (truncated write, an unquoted
        # newline) raised IndexError past every guard on this
        # checker-blind route — a missing cell degrades to '', the
        # empty value every read below already handles ('' keys split
        # to none, _parse_motion('') is 0.0)
        if len(r) < len(header):
            r += [""] * (len(header) - len(r))
    slug = game or game_key_from_name(canonical.get("game", "")) or ""

    # ---- r19 #1 (BLOCKER) / #4≡#6 / #10 (M1): resolve the delivered
    # stamp BEFORE anything is written, so a refusal leaves the work dir
    # byte-identical for attempt 2. The stamp and the head cut parse
    # SEPARATELY; what a degraded value may become depends on whether
    # the raw-sidecar head-offset contract is live (see docstring). An
    # ABSENT trim/head_cut_s field is the documented v1-optional shape
    # (a payload that never recorded a cut, head 0.0 is TRUE); a
    # PRESENT-but-junk one is destroyed evidence and must not silently
    # read as 0.0. OverflowError rides every guard here — JSON-legal
    # bigints, Infinity and '1e999' raise it past (TypeError,
    # ValueError) in the float parse, the timedelta build AND the
    # datetime addition (r19 #4≡#6).
    import math
    ca = canonical.get("created_at_utc")
    trim_meta = canonical.get("trim")
    stamp = None
    if ca:
        try:
            stamp = datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                # a naive v1 stamp is UTC by contract — repair it in
                # place so .astimezone below can't shift it by the
                # host's offset (r15 #7: the sole omission of the
                # sibling guard; the qa checker that would flag naive
                # stamps never runs before ARR_V1_FORMAT routes here)
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            stamp = None
    head_cut, head_usable = 0.0, True
    if trim_meta is not None and not isinstance(trim_meta, dict):
        head_usable = False
    elif isinstance(trim_meta, dict):
        try:
            head_cut = float(trim_meta.get("head_cut_s") or 0.0)
            head_usable = math.isfinite(head_cut)
        except (TypeError, ValueError, OverflowError):
            head_usable = False
    created_out, recovered = None, False
    if stamp is not None and head_usable:
        try:
            created_out = stamp + timedelta(seconds=head_cut)
        except OverflowError:
            head_usable = False
    if created_out is None:
        started = _v1_sidecar_started(work)
        if started is not None:
            # the contract is LIVE: recover from ground truth or refuse
            # typed — never fabricate (r19 #1)
            if head_usable:
                try:
                    created_out = started + timedelta(seconds=head_cut)
                    recovered = True
                except OverflowError:
                    head_usable = False
            if not head_usable:
                raise FixFailed(
                    "canonical.trim head_cut_s unusable while usable raw "
                    "sidecars are attached — a fabricated head offset "
                    "would silently desync or wrongly reject the "
                    "raw-sidecar verify/retranslate (r19 #1); repair "
                    "session.json's canonical.trim before converting")
        elif stamp is not None:
            # junk head cut with no live contract: keep the good stamp
            # (head 0.0) rather than discarding it for a bad neighbor
            # (r19 #10); an unusable stamp stays omitted for
            # fix_sessionjson_recompute to synthesize
            created_out = stamp

    # the session's own keybind.json is AUTHORITATIVE (the F4 doctrine's
    # fourth instance — r17 #2): judging `bound` against the built-ins
    # alone deleted every custom-bound key press from the converted rows
    # while their v1-resolved actions shipped verbatim on the same rows
    # with an empty input_keys cell — checker-GREEN, because the
    # keys-have-actions test is one-directional (keys_no_action), so the
    # corruption passed silently and no retranslate was ever planned. At
    # this point the sidecars are still at the work ROOT (this function
    # moves them into raw/ below); accept the raw/ location too so a
    # re-entrant run resolves identically. The delivered key_binding.json
    # is DELIBERATELY not a fallback (deviation recorded in plan §0): the
    # inversion sniff is biased against flipping, and a mis-flip empties
    # the keyboard column (the r-loop-4 catastrophic class).
    kbp = work / "keybind.json"
    if not kbp.exists():
        kbp = work / "raw" / "keybind.json"
    if kbp.exists():
        kb = resolve_keybind(keybind_path=kbp, game_name=slug,
                             exe_name=None)
    else:
        kb = dict(KEYBINDS.get(slug, {}))
    kb.update(KEYBIND_PATCHES.get(slug, {}))
    bound = bound_literals(kb) if kb else frozenset()

    out = []
    from translator.binner import C2W_COLS, CAMERA_COLS
    from translator.v2 import CAMERA_EXTRA_COLS
    cam_null = [""] * (len(C2W_COLS) + len(CAMERA_COLS)
                       + len(CAMERA_EXTRA_COLS))

    def _parse_motion(v) -> float:
        # unparseable/non-finite degrades to 0.0, exactly like
        # fix_sentinels' _parse and translator/v2's _num_cell — a junk
        # cell is not motion (r18 #1≡#2≡#3≡#5: the bare float here
        # crashed both attempts on one '1,5' cell, and on this route
        # no sentinel repair can ever run first)
        try:
            x = float(v or 0)
        except (TypeError, ValueError):
            return 0.0
        return x if math.isfinite(x) else 0.0

    # has_motion judges PARSED values (fix_sentinels' own has_motion
    # rule): the old string test counted junk and '0.0' cells as
    # motion, so a junk-only column fabricated an all-zero motion
    # track instead of the blank no-capture form
    has_motion = any(_parse_motion(r[col["input_mouse_dx"]]) != 0.0
                     or _parse_motion(r[col["input_mouse_dy"]]) != 0.0
                     for r in rows)
    for r in rows:
        keys = [t for t in (r[col["input_keys"]] or "").split("|") if t]
        # canonicalize before the bound test — bound_literals is lowercase
        # canonical while v1 files may carry either case (review-2 #8)
        kept = [key_display(key_canonical(t)) for t in keys
                if not bound or key_canonical(t) in bound]
        # same full-vocabulary canonicalization as fix_key_hygiene
        # (r-loop 10 #7): v1 files carry raw-event forms ('left') that the
        # exact-name map passed through into a checker FAIL; unmappable
        # tokens are dropped so the set test can never re-fire
        from translator.keys import MOUSE_BUTTONS as _MB
        btns = []
        for b in (r[col["input_mouse_buttons"]] or "").split("|"):
            if not b:
                continue
            canon = _BTN_DISPLAY_INV.get(b) \
                or (b if b in _BTN_DISPLAY else None) \
                or _MB.get(b.lower()) \
                or (b.lower() if b.lower() in _BTN_DISPLAY else None)
            if canon:
                btns.append(_BTN_DISPLAY[canon])
        dx, dy = r[col["input_mouse_dx"]], r[col["input_mouse_dy"]]
        if has_motion:
            dx = f"{_parse_motion(dx):.1f}"
            dy = f"{_parse_motion(dy):.1f}"
        else:
            dx = dy = ""
        out.append([r[col["frame_id"]], r[col["timestamp_ms"]]] + cam_null
                   + ["|".join(kept), r[col["input_actions"]],
                      "|".join(btns), dx, dy])
    _write_csv(work, V2_FRAME_COLS, out)

    new_s = {"session_id": canonical.get("session_id", work.name)}
    # created_out was resolved (or refused) BEFORE any write above —
    # r19 #1/#4≡#6/#10 (M1); None = omitted, fix_sessionjson_recompute
    # below synthesizes a canonical stamp from ground truth (its
    # designed r-loop 7/8 job)
    if created_out is not None:
        new_s["created_at_utc"] = created_out.astimezone(
            timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
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
    note = f"converted v1 -> v2 ({len(out)} rows)"
    if recovered:
        # attributable in the fixlog: the delivered stamp came from raw
        # ground truth, not from the (unusable) canonical block (r19 #1)
        note += "; created_at_utc recovered from raw started_at_utc + head_cut"
    return note
