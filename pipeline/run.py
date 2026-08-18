"""Orchestrator (plan §6/§7.6/§13) — `python -m pipeline run`.

launchd fires this every 30 min; it exits instantly if the run lock is held
(stale locks from crashes are reclaimed by pid check). Each batch is
wrapped in `caffeinate -i`; the queue drains, then the process exits.
Every phase is driven purely by ledger state, so a kill at any point
resumes exactly: downloads re-run (rclone is idempotent), validation
re-runs, uploads re-verify, and hours are recorded once at the DELIVERED
transition.
"""
from __future__ import annotations

import concurrent.futures
import json
import multiprocessing
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as C
from . import deliver, fix, ingest, pace, reports, telegram
from .ledger import Ledger

RESUMABLE = ("DOWNLOADING", "INGESTED", "VALIDATING", "FIX_QUEUED",
             "FIXING", "REVALIDATING", "READY", "PACKAGED", "UPLOADED")

# States that mean "still mid-pipeline this run" — a batch whose session
# sits in one of these after U's pass stays OPEN and carries to the next
# run via the start-written batches.summary_json (plan §6).
_MID_PIPELINE = set(RESUMABLE)

# R23 run-level VLM state, owned by the parent process: the run's current
# sticky rung (injected into every validation job, max of worker reports
# kept — survives spawn pool generations) and, per session, whether the
# LAST verdict used a laddered-down model. Reset at every run() entry.
_VLM_RUN_STATE: dict = {"rung": 0, "fallback": {}}


def _reset_vlm_run_state() -> None:
    _VLM_RUN_STATE["rung"] = 0
    _VLM_RUN_STATE["fallback"] = {}


# ------------------------------------------------------------------ lock

def _pid_is_pipeline(pid: int) -> bool:
    """Is `pid` alive AND actually a lock-legitimate process? os.kill(pid,0)
    alone treats a RECYCLED pid as a live run and skips every tick forever
    (review-r3 #26). On Linux /proc gives the cmdline; elsewhere fall
    back to liveness only.

    `recal_` counts too: the flip tools acquire this same lock (r-loop 1),
    but they run as `python tools/recal_*.py`, whose cmdline carries
    neither "pipeline" nor "pytest" — so a starting driver judged a LIVE
    tool's lock stale and reclaimed it, leaving the mutex one-way
    (r-loop 2 blocker)."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True                      # no /proc (macOS): liveness only
    return (b"pipeline" in cmdline or b"pytest" in cmdline
            or b"recal_" in cmdline)


def _reclaim_stale_lock(cfg: C.Config) -> None:
    """Atomic reclaim: RENAME the stale lock aside, then delete the
    renamed dir. rmtree-then-retry let two starters both remove and both
    acquire — B's rmtree could delete the lock A had JUST re-created
    (review-r4 #2/#36/#39). Only one renamer ever wins os.rename; the
    loser's FileNotFoundError is the sign someone else reclaimed."""
    grave = cfg.lock_dir.with_name(f"run.lock.stale-{os.getpid()}")
    try:
        os.rename(cfg.lock_dir, grave)
    except OSError:
        return                       # someone else reclaimed (or it's live)
    shutil.rmtree(grave, ignore_errors=True)


def acquire_lock(cfg: C.Config) -> bool:
    for _ in range(2):
        try:
            cfg.lock_dir.mkdir(parents=True)
            (cfg.lock_dir / "pid").write_text(str(os.getpid()))
            return True
        except FileExistsError:
            try:
                pid = int((cfg.lock_dir / "pid").read_text())
                if _pid_is_pipeline(pid):
                    return False              # live run holds it
                _reclaim_stale_lock(cfg)
            except (ValueError, FileNotFoundError):
                # pid file missing/garbled. Reclaiming here could steal a
                # JUST-mkdir'd winner's lock (review-r3 #4) — only reclaim
                # when the dir is old enough that no live winner can be
                # mid-write; else yield to the next tick.
                try:
                    age = time.time() - cfg.lock_dir.stat().st_mtime
                except OSError:
                    age = 0.0
                if age > 2 * 3600:
                    _reclaim_stale_lock(cfg)
                else:
                    return False
    return False


def release_lock(cfg: C.Config) -> None:
    """Remove the lock ONLY while we still hold it. Without the pid check a
    process whose lock was reclaimed (or replaced by a newer holder) would
    rmtree the NEW holder's lock on its way out, disarming the mutex
    entirely (r-loop 2)."""
    try:
        holder = int((cfg.lock_dir / "pid").read_text())
    except (OSError, ValueError):
        holder = os.getpid()          # unreadable: fall back to old behavior
    if holder != os.getpid():
        print(f"[lock] not releasing — held by pid {holder}, not us",
              file=sys.stderr)
        return
    shutil.rmtree(cfg.lock_dir, ignore_errors=True)


# ------------------------------------------------------- worker function

def _validate_worker(args: dict) -> dict:
    """Runs in a subprocess: full Phase-II validation of one session."""
    from . import vlm as vlmmod
    from .validate import validate_session
    # R23 run-level stickiness: start at the parent-injected rung (spawn
    # workers are fresh interpreters — module state alone would reset every
    # pool generation) and report the ending rung back for the parent's max.
    # max(), not assignment: pool processes serve many jobs, and a rung
    # climbed on an earlier job in this generation must not be clobbered
    # back to the batch-start value (review-r1 #11).
    vlmmod._rung = max(vlmmod._rung, int(args.get("vlm_rung", 0)))
    # continuous-driver 429 backpressure channel — optional, additive: the
    # batch driver never passes it and gets today's behavior unchanged
    if args.get("pressure_path"):
        vlmmod._pressure_path = args["pressure_path"]
    try:
        res = validate_session(
            Path(args["work_dir"]), Path(args["dossier_dir"]),
            payload=args["payload"], expected_game=args["expected_game"],
            gemini_key=args["gemini_key"], gemini_model=args["gemini_model"])
        return {"sid": args["sid"], "bin": res.bin,
                "hold_vlm": res.hold_vlm, "reasons": res.reasons,
                "advisories": res.advisories,
                "engine_verdict": res.engine_verdict,
                "vlm_rung": vlmmod._rung,
                "vlm_fallback": any(
                    m.get("rung", 0) > 0
                    for m in (res.metrics or {}).get("models_used", []))}
    except Exception as e:
        import traceback
        # Tag HOST-level failures so the caller can tell "this machine is
        # having a bad minute" from "this session's bytes crash the
        # decoder" (r-loop 6). The V lane was the only one of the three
        # without this split: _download_one catches (OSError,
        # sqlite3.OperationalError) and cools down, _deliver_one catches
        # those plus TimeoutExpired and cools down, and _deliver_one's own
        # comment records that a bare `except Exception` there once
        # converted the whole delivery backlog to QUARANTINED. QUARANTINED
        # is TERMINAL with no automatic re-entry, so a full disk or an
        # ENOMEM during a sweep terminally rejected every session that
        # happened to be validating.
        kind = "host" if isinstance(
            e, (OSError, MemoryError, sqlite3.OperationalError)) else "crash"
        return {"sid": args["sid"], "error": f"{type(e).__name__}: {e}",
                "kind": kind,
                "tb": traceback.format_exc()[-1500:],
                "vlm_rung": vlmmod._rung}


# ------------------------------------------------------------ run phases

_ALERT_LOCK = threading.Lock()


def _alert(cfg: C.Config, text: str, sent: list[str]) -> None:
    """Telegram alert; failures are logged, never fatal. Deduped per run.
    The lock makes check-then-append atomic across D/V/U threads."""
    with _ALERT_LOCK:
        if text in sent:
            return
        sent.append(text)
    try:
        telegram.send_message(cfg, f"⚠️ {text}")
    except telegram.TelegramError as e:
        print(f"[alert-undelivered] {text} ({e})", file=sys.stderr)


def _download_phase(cfg, ledger, sids, alerts) -> None:
    for sid in sids:
        row = ledger.get(sid)
        if row["state"] not in ("DISCOVERED", "DOWNLOADING"):
            continue
        if deliver.disk_free_gb(cfg.home) < C.DISK_LOW_WATER_GB:
            _alert(cfg, f"disk under {C.DISK_LOW_WATER_GB} GB free — "
                        f"downloads paused (F7)", alerts)
            break
        try:
            ingest.download(cfg, ledger, sid)
        except ingest.DownloadError as e:
            msg = str(e)
            kind = getattr(e, "kind", "transient")
            if kind == "zip_incomplete":
                # a multi-part zip still mid-upload reassembles as garbage;
                # the missing parts appear later — this must stay retryable,
                # never terminal (review-2 #10)
                ledger.set_state(sid, "DISCOVERED",
                                 f"zip payload incomplete/unreadable — "
                                 f"retrying next run: {msg}"[:300])
                ledger.incomplete_seen(row["drive_path"],
                                       [ingest.ZIP_PARTS_MARKER])
            elif kind == "quarantine":
                # bad checksum, permanently unusable archive, garbage
                # payload: retrying can never succeed (review-r2 #0/#10)
                ledger.set_state(sid, "QUARANTINED", msg[:300])
                _alert(cfg, f"download quarantined {sid}: {e}", alerts)
            else:
                # transient transfer failure: back to the queue, alert once
                ledger.set_state(sid, "DISCOVERED",
                                 f"download failed — retrying next run: "
                                 f"{msg}"[:300])
                _alert(cfg, f"download failed for {sid} (will retry): {e}",
                       alerts)
        except (OSError, sqlite3.OperationalError) as e:
            # host-level trouble (disk full, I/O hiccup, a ledger write
            # waiting out a WAL checkpoint past busy_timeout) anywhere in
            # the download path is TRANSIENT — quarantining here made a
            # full disk permanently kill good sessions (review-r4 #3/#17;
            # sqlite busy added by review-r5 #25)
            ledger.set_state(sid, "DISCOVERED",
                             f"download failed (host-level) — retrying "
                             f"next run: {type(e).__name__}: {e}"[:300])
            _alert(cfg, f"download hit host-level error for {sid} "
                        f"(will retry): {type(e).__name__}: {e}", alerts)
        except Exception as e:
            # nothing a raw exception could mean is retry-fixable, and
            # letting it escape killed the whole D thread for the run
            # (review-r2 #0 second half): quarantine THIS session, alert,
            # keep downloading the rest
            ledger.set_state(sid, "QUARANTINED",
                             f"download crashed: {type(e).__name__}: "
                             f"{e}"[:300])
            _alert(cfg, f"download crashed for {sid}: "
                        f"{type(e).__name__}: {e}", alerts)


def _validate_phase(cfg, ledger, sids, alerts, *, workers: int) -> None:
    jobs = []
    for sid in sids:
        row = ledger.get(sid)
        if row["state"] not in ("INGESTED", "VALIDATING", "HOLD_VLM",
                                "REVALIDATING"):
            continue
        work = cfg.work / sid
        if not work.exists():
            ledger.set_state(sid, "QUARANTINED", "work copy missing")
            continue
        ledger.set_state(sid, "VALIDATING")
        jobs.append({"sid": sid, "work_dir": str(work),
                     "dossier_dir": str(cfg.dossiers / sid),
                     "payload": ingest.sniff_payload(work),
                     "expected_game": row["game"] or None,
                     "gemini_key": cfg.gemini_key,
                     "gemini_model": cfg.gemini_model,
                     "vlm_rung": _VLM_RUN_STATE["rung"]})
    if not jobs:
        return
    # spawn, not the platform default: fork on Linux would clone a
    # multi-threaded parent holding sqlite/stdio locks and intermittently
    # deadlock children (plan §6); macOS already spawns, so Mac tests
    # cannot surface the fork wedge — the guard in __main__.py makes
    # spawn's main-module re-import safe
    ctx = multiprocessing.get_context("spawn")
    results: list[dict] = []
    # workers>1 alone decides pool use — a single job must STILL run in a
    # subprocess in production, or one poisoned session's native crash
    # (cv2/ffmpeg segfault) kills the orchestrator itself and crash-loops
    # every tick with no quarantine (review-r2 #6); workers=1 stays inline
    # (tests, debugging)
    if workers > 1:
        try:
            with concurrent.futures.ProcessPoolExecutor(
                    max_workers=min(workers, len(jobs)),
                    mp_context=ctx) as ex:
                results = list(ex.map(_validate_worker, jobs))
        except concurrent.futures.process.BrokenProcessPool:
            # a native crash (cv2/ffmpeg segfault, OOM kill) took the pool
            # down — retry each job in its own single-worker pool so only
            # the killer session quarantines, not the whole batch, and the
            # run never wedges (review-2 #4)
            results = []
            for j in jobs:
                try:
                    with concurrent.futures.ProcessPoolExecutor(
                            max_workers=1, mp_context=ctx) as ex1:
                        results.append(
                            list(ex1.map(_validate_worker, [j]))[0])
                except concurrent.futures.process.BrokenProcessPool:
                    results.append(
                        {"sid": j["sid"],
                         "error": "validation worker died (native crash "
                                  "decoding this session)"})
    else:
        for j in jobs:
            results.append(_validate_worker(j))
    for r in results:
        sid = r["sid"]
        if "vlm_rung" in r:
            # parent keeps the max — the run's rung survives pool
            # generations (R23 run-level stickiness)
            _VLM_RUN_STATE["rung"] = max(_VLM_RUN_STATE["rung"],
                                         int(r["vlm_rung"]))
        if "error" in r:
            ledger.set_state(sid, "QUARANTINED",
                             f"validation crashed: {r['error']}")
            _alert(cfg, f"validation crashed on {sid}: {r['error']}", alerts)
            continue
        _VLM_RUN_STATE["fallback"][sid] = bool(r.get("vlm_fallback"))
        ledger.set_reasons(sid, r["reasons"], r["bin"])
        if r["hold_vlm"]:
            ledger.set_state(sid, "HOLD_VLM",
                             "VLM sweep unfinished — never pass "
                             "unlooked-at (F5)")
        elif r["bin"] == 1:
            ledger.set_state(sid, "READY")
        elif r["bin"] == 2:
            ledger.set_state(sid, "FIX_QUEUED")
        else:
            ledger.set_state(sid, "REJECTED",
                             ",".join(x["code"] for x in r["reasons"]
                                      if x["blocking"]))


def _partial_dirs(cfg, sid: str) -> list[Path]:
    """Segment dirs that belong DIRECTLY to `sid` — {sid}-p<digits> only.
    The bare {sid}-p[0-9]* glob also matched grandchildren ({sid}-p1-p1),
    whose rows the parent_id=sid query does not see: a live grandchild's
    work dir was classified as a rowless partial and wiped
    (review-r5 #26)."""
    pat = re.compile(re.escape(sid) + r"-p\d+$")
    return [d for d in cfg.work.glob(f"{sid}-p[0-9]*")
            if d.is_dir() and pat.fullmatch(d.name)]


def _discard_split_artifacts(cfg, ledger, sid: str) -> None:
    """Remove a rescinded cut's leftovers — rowless segment dirs and the
    manifest. Without this, a fix path that errored or rejected AFTER a
    completed cut left a truthful-looking manifest behind, and a later
    FIXING crash adopted the stale split from a rescinded plan
    (review-r4 #5/#19). Rowed children are live work and are kept."""
    have_rows = {k["session_id"] for k in ledger.db.execute(
        "SELECT session_id FROM sessions WHERE parent_id=?",
        (sid,)).fetchall()}
    for d in _partial_dirs(cfg, sid):
        if d.name not in have_rows:
            shutil.rmtree(d, ignore_errors=True)
    (cfg.work / f"{sid}.split-manifest.json").unlink(missing_ok=True)


def _recover_split(cfg, ledger, sid: str, row) -> tuple[bool, list[str]]:
    """Mid-fix crash triage for a FIXING parent (review-r3 #1/#5): the
    cutter's manifest marks a COMPLETE cut. Adopt the split ONLY when the
    manifest exists and every listed segment has a work dir or ledger row
    — inserting rows for manifest-listed dirs the kill orphaned. Anything
    else (no manifest = killed mid-cut; missing segments; stray non-
    manifest dirs) is wiped and the parent re-derives via REVALIDATING.
    Returns (complete, kid_ids)."""
    manifest_path = cfg.work / f"{sid}.split-manifest.json"
    have_rows = {k["session_id"] for k in ledger.db.execute(
        "SELECT session_id FROM sessions WHERE parent_id=?",
        (sid,)).fetchall()}
    manifest_ids: list[str] = []
    if manifest_path.exists():
        try:
            manifest_ids = list(json.loads(
                manifest_path.read_text()).get("segments") or [])
        except json.JSONDecodeError:
            manifest_ids = []          # torn content: treat as absent
        except OSError:
            # the manifest EXISTS but could not be read — a transient
            # I/O fault is indistinguishable from killed-mid-cut only if
            # we conflate them: wiping here destroyed a COMPLETE cut's
            # segment dirs and let the re-cut clobber a live rowed child
            # (review-r5 #38). Touch nothing; retry next run.
            return False, sorted(have_rows)
    complete = bool(manifest_ids) and all(
        (cfg.work / seg_id).is_dir() or seg_id in have_rows
        for seg_id in manifest_ids)
    if not complete and not manifest_ids and have_rows:
        # rows exist but the manifest is gone: rows are only ever inserted
        # after a manifest-complete cut, so this is the kill window between
        # a prior recovery finishing its inserts and the SPLIT commit —
        # adopt the rowed children as the split; re-cutting here would
        # clobber live children (review-r4 #0). Guard: no rowless partial
        # dirs may exist (those would mean a NEWER interrupted cut).
        rowless = [d for d in _partial_dirs(cfg, sid)
                   if d.name not in have_rows]
        if not rowless:
            return True, sorted(have_rows)
    if not complete:
        # wipe rowless partials so a LATER crash can't adopt them; rowed
        # children are real work and stay
        for seg_dir in _partial_dirs(cfg, sid):
            if seg_dir.name not in have_rows:
                shutil.rmtree(seg_dir, ignore_errors=True)
        manifest_path.unlink(missing_ok=True)
        # if some children already have rows, the earlier attempt got far
        # enough that they are valid segments — but the parent must NOT
        # complete as SPLIT on a subset; REVALIDATING re-derives the plan
        return False, sorted(have_rows)
    for seg_id in manifest_ids:
        if seg_id in have_rows:
            continue
        dur = None
        try:
            dur = float(json.loads(
                (cfg.work / seg_id / "session.json").read_text()
            ).get("duration_seconds") or 0) or None
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        ledger.insert_session(
            session_id=seg_id, game=row["game"],
            operator_email=row["operator_email"],
            player_email=row["player_email"],
            drive_path=row["drive_path"], drive_ctime=row["drive_ctime"],
            md5_video="", bytes_=0, state="INGESTED", parent_id=sid,
            detail="segment recovered after mid-split crash (manifest)")
        if dur:
            ledger.update(seg_id, duration_raw_s=dur)
    # the manifest is unlinked by the CALLER after the SPLIT commit — an
    # unlink here left a kill window (rows in, manifest gone, parent still
    # FIXING) that re-derived the cut and clobbered live children
    # (review-r4 #0/#4/#20)
    return True, sorted(set(manifest_ids) | have_rows)


def _fix_phase(cfg, ledger, sids, alerts, *, workers: int,
               children_sink=None) -> list[str]:
    """Fix FIX_QUEUED sessions (≤2 attempts each, R2); returns new child
    session ids created by splits. `children_sink` (a set) is told about
    every child the moment its ledger row exists, so the driver's
    `attempted` set can never race D into claiming a child this run
    (review-r1 #10)."""
    children_created: list[str] = []
    for _pass in range(C.FIX_RETRIES):
        # children get their own fix passes within the run — scanning only
        # the original sids left bin-2 children stranded in FIX_QUEUED and
        # their batch open (review-r1 #18/#21)
        todo = [s for s in sids + children_created
                if (row := ledger.get(s))
                and row["state"] in ("FIX_QUEUED", "FIXING")]
        if not todo:
            break
        for sid in todo:
            row = ledger.get(sid)
            if row["state"] == "FIXING":
                done, kid_ids = _recover_split(cfg, ledger, sid, row)
                if done:
                    # crash landed after the COMPLETE cut (manifest
                    # present, every segment accounted for) but before the
                    # parent's SPLIT transition: re-validating the parent
                    # could re-verdict it deliverable and ship the whole
                    # video ON TOP of its segments (review-r1 #4) —
                    # complete the split instead
                    for kid in kid_ids:
                        # never double-register: a resumed child may
                        # already ride in this batch's sids (review-r2 #19)
                        if kid not in children_created and kid not in sids:
                            children_created.append(kid)
                        if children_sink is not None:
                            children_sink.add(kid)
                    # the normal cut path propagates the parent's shift
                    # record to children via fix.py; the adopted path
                    # skipped it, so adopted children of a shift-corrected
                    # parent spuriously failed qa's raw recomputation and
                    # burned their fix budget (review-r5 #27). Best-effort:
                    # the record may legitimately not exist.
                    try:
                        fix._propagate_shift_record(
                            cfg.work / sid,
                            [cfg.work / k for k in kid_ids
                             if (cfg.work / k).is_dir()])
                    except Exception as e:
                        print(f"[shift-propagate-failed] {sid}: {e}",
                              file=sys.stderr)
                    ledger.set_state(sid, "SPLIT",
                                     f"{len(kid_ids)} segments (completed "
                                     f"after mid-split crash)")
                    shutil.rmtree(cfg.work / sid, ignore_errors=True)
                    (cfg.work / f"{sid}.split-manifest.json").unlink(
                        missing_ok=True)      # only after the SPLIT commit
                    continue
                # crash mid-fix: the copy may be half-fixed and the stored
                # plan stale — re-running RETRIM/CUT on it would trim real
                # gameplay twice (review finding #6). Re-validate instead:
                # the fresh verdict re-derives exactly what still needs
                # fixing on the CURRENT copy.
                ledger.set_state(sid, "REVALIDATING",
                                 "mid-fix crash — re-deriving fix plan")
                continue
            if row["fix_attempts"] >= C.FIX_RETRIES:
                ledger.set_state(sid, "REJECTED",
                                 "fix retries exhausted (R2)")
                continue
            ledger.update(sid, fix_attempts=row["fix_attempts"] + 1)
            ledger.set_state(sid, "FIXING",
                             f"attempt {row['fix_attempts'] + 1}")
            reasons = json.loads(row["reasons_json"] or "[]")
            work = cfg.work / sid
            has_raw = fix.has_raw_sidecars(work)
            # Resolve the reroute target BEFORE planning (r-loop 5).
            # row["game"] is the DRIVE-FOLDER game -- the very value
            # STR_GAME_MISMATCH says is wrong -- and plan_fixes branches on
            # `game` in two places: INP_FANOUT (fixable only for outer_wilds
            # or when has_raw) and the hygiene->context coupling. Planning
            # with the wrong game classified INP_FANOUT as UNFIXABLE and
            # rejected the session before the reroute was ever applied,
            # although FIX_REROUTE_GAME + FIX_ACTIONS_CONTEXT is exactly the
            # designed fix. The milder variant planned hygiene with no
            # context step after it, so hygiene re-fanned-out the multi-bound
            # OW keys and INP_FANOUT came back on revalidation, burning the
            # second attempt and a second paid sweep.
            game = row["game"]
            _mm = next((r_ for r_ in reasons
                        if r_.get("code") == "STR_GAME_MISMATCH"),
                       None)
            if _mm and (_mm.get("params") or {}).get("actual") \
                    in C.GAMES:
                game = _mm["params"]["actual"]
            plan = fix.plan_fixes(reasons, game=game,
                                  has_raw=has_raw)
            if plan["unfixable"]:
                _discard_split_artifacts(cfg, ledger, sid)
                ledger.set_state(sid, "REJECTED",
                                 f"unfixable: {plan['unfixable']}")
                continue
            if not plan["steps"]:
                _discard_split_artifacts(cfg, ledger, sid)
                ledger.set_state(sid, "REJECTED",
                                 "no applicable fix for blocking reasons")
                continue
            # a reroute changes which keybind/title every later fix and
            # re-validation must use — apply it to the ledger FIRST
            reroute = next((p for f, p in plan["steps"]
                            if f == "FIX_REROUTE_GAME"), None)
            if reroute and reroute.get("actual") in C.GAMES:
                game = reroute["actual"]
            if game != row["game"]:
                ledger.update(sid, game=game)
            out = fix.apply_fixes(work, plan, game=game,
                                  dossier_dir=cfg.dossiers / sid,
                                  split_root=cfg.work)
            if out["error"]:
                # partially-applied plan: never re-run it blind — go back
                # through validation to re-derive from the current copy.
                # Any cut artifacts from THIS plan are rescinded with it
                # (review-r4 #5/#19)
                _discard_split_artifacts(cfg, ledger, sid)
                if out.get("kind") == "host":
                    # host-level, not the session: refund the attempt.
                    # Same carve-out the continuous driver applies; the
                    # rollback path must not re-expose the r-loop-7
                    # blocker (a disk-full episode burning both attempts
                    # and rejecting under fix-failed). But park FIX_QUEUED
                    # only when NOTHING was applied (r-loop 8 BLOCKER):
                    # plan_fixes is pure, so a re-pick re-dispatches the
                    # IDENTICAL plan from step 0 including already-
                    # succeeded destructive steps (retrim removes head_s
                    # again on every call — and this pass loop would
                    # re-trim within a single run). A partially-applied
                    # plan routes through REVALIDATING to re-derive from
                    # the half-fixed copy (review finding #6).
                    ledger.update(sid, fix_attempts=row["fix_attempts"])
                    if not any(a.get("ok")
                               for a in (out.get("applied") or [])):
                        ledger.set_state(
                            sid, "FIX_QUEUED",
                            f"host-level fix failure before any step "
                            f"applied — retrying: {out['error']}"[:300])
                    else:
                        ledger.set_state(
                            sid, "REVALIDATING",
                            f"host-level fix failure after applied "
                            f"step(s) — re-deriving from the current "
                            f"copy: {out['error']}"[:300])
                    _alert(cfg, f"fix hit a host-level error on {sid} "
                                f"(will retry): {out['error']}", alerts)
                    continue
                ledger.set_state(sid, "REVALIDATING",
                                 f"fix failed: {out['error']}"[:300])
                continue
            if out["children"] is not None and \
                    not out["children"]["segments"]:
                # no ≥70 s segment survived the cut — §5: reject
                _discard_split_artifacts(cfg, ledger, sid)
                ledger.set_state(sid, "REJECTED",
                                 "split produced no >=70s segment "
                                 f"(dropped {len(out['children']['dropped'])})")
                continue
            if out["children"] is not None:
                for seg in out["children"]["segments"]:
                    if ledger.get(seg["id"]) is None:
                        ledger.insert_session(
                            session_id=seg["id"], game=game,
                            operator_email=row["operator_email"],
                            player_email=row["player_email"],
                            drive_path=row["drive_path"],
                            drive_ctime=row["drive_ctime"],
                            md5_video="", bytes_=0, state="INGESTED",
                            parent_id=sid,
                            detail=f"split segment {seg['t0']}-{seg['t1']}s")
                        ledger.update(seg["id"],
                                      duration_raw_s=seg["duration_s"])
                    if seg["id"] not in children_created \
                            and seg["id"] not in sids:
                        children_created.append(seg["id"])
                    if children_sink is not None:
                        children_sink.add(seg["id"])
                ledger.set_state(sid, "SPLIT",
                                 f"{len(out['children']['segments'])} "
                                 f"segments"
                                 + (f"; dropped "
                                    f"{len(out['children']['dropped'])}"
                                    if out["children"]["dropped"] else ""))
                shutil.rmtree(work, ignore_errors=True)
                (cfg.work / f"{sid}.split-manifest.json").unlink(
                    missing_ok=True)
                continue
            ledger.set_state(sid, "REVALIDATING", "fixes applied")
        # re-validate everything the fixes touched (full Phase II re-run);
        # dict.fromkeys dedupes while keeping order (review-r2 #19)
        revalidate = [s for s in dict.fromkeys(sids + children_created)
                      if (row := ledger.get(s))
                      and row["state"] in ("REVALIDATING", "INGESTED")]
        _validate_phase(cfg, ledger, revalidate, alerts, workers=workers)
    # anything still FIX_QUEUED after the bounded loop → reject w/ residuals
    for sid in dict.fromkeys(sids + children_created):
        row = ledger.get(sid)
        if row and row["state"] == "FIX_QUEUED" \
                and row["fix_attempts"] >= C.FIX_RETRIES:
            ledger.set_state(sid, "REJECTED", "fix retries exhausted (R2)")
    return children_created


def _deliver_phase(cfg, ledger, sids, alerts,
                   dest_prefix: str = C.VENDOR) -> dict:
    from .validate import map_gate_failures
    stats = {"delivered": 0, "hours": 0.0, "upload_failures": 0}
    for sid in sids:
        row = ledger.get(sid)
        if not row or row["state"] not in ("READY", "PACKAGED", "UPLOADED"):
            continue
        try:
            out = deliver.deliver_session(cfg, ledger, sid,
                                          dest_prefix=dest_prefix)
        except (OSError, sqlite3.OperationalError,
                subprocess.TimeoutExpired) as e:
            # host-level trouble (ENOSPC from the multi-GB stage copy, a
            # locked/full ledger, an rrd render past its 1800s timeout) is
            # TRANSIENT and must not be terminal — deliver_session is
            # state-guarded and resumes. Mirrors the continuous driver's
            # _deliver_one so the rollback path behaves identically
            # (r-loop 3).
            stats["upload_failures"] += 1
            _alert(cfg, f"delivery deferred for {sid} (transient): "
                        f"{type(e).__name__}: {e}", alerts)
            continue
        except Exception as e:
            # staging/rrd/qa crash: quarantining preserves the session for
            # a human instead of re-crashing every launchd tick (review
            # finding #3); Drive I original is untouched either way
            ledger.set_state(sid, "QUARANTINED",
                             f"delivery crashed: {type(e).__name__}: "
                             f"{e}"[:300])
            _alert(cfg, f"delivery crashed for {sid}: "
                        f"{type(e).__name__}: {e}", alerts)
            continue
        if out.status == "delivered":
            stats["delivered"] += 1
            stats["hours"] += out.hours
        elif out.status == "failed_gate":
            r = ledger.get(sid)
            # the gate failures BECOME the reasons, so the fix pass has a
            # real plan to work from (review finding #2)
            has_raw = fix.has_raw_sidecars(cfg.work / sid)
            reasons = map_gate_failures(out.gate_fails or [],
                                        has_raw=has_raw)
            if reasons:
                ledger.set_reasons(sid, reasons,
                                   3 if any(not x["fixable"]
                                            for x in reasons
                                            if x["blocking"]) else 2)
            # no attempt increment here — the fix phase charges its own
            # budget when it runs; incrementing on requeue made the fix
            # dead-on-arrival at the budget check (review-2 #11)
            if r["fix_attempts"] >= C.FIX_RETRIES:
                ledger.set_state(sid, "REJECTED",
                                 f"final gate: {out.detail}"[:300])
            else:
                ledger.set_state(sid, "FIX_QUEUED",
                                 f"final gate: {out.detail}"[:300])
        else:
            stats["upload_failures"] += 1
            _alert(cfg, f"upload failed for {sid}: {out.detail}", alerts)
    return stats


def process_batch(cfg: C.Config, ledger: Ledger, sids: list[str], *,
                  alerts: list[str],
                  dest_prefix: str = C.VENDOR) -> reports.BatchStats:
    batch_no = ledger.start_batch(sessions=sids)
    t0 = datetime.now(timezone.utc)
    _download_phase(cfg, ledger, sids, alerts)
    _validate_phase(cfg, ledger, sids, alerts, workers=cfg.workers)
    kids = _fix_phase(cfg, ledger, sids, alerts, workers=cfg.workers)
    all_sids = list(dict.fromkeys(sids + kids))
    dstats = _deliver_phase(cfg, ledger, all_sids, alerts,
                            dest_prefix=dest_prefix)

    rejected, fixed = 0, 0
    reject_label_lists: list[list[str]] = []
    for sid in all_sids:
        row = ledger.get(sid)
        if not row:
            continue
        if row["state"] == "REJECTED":
            rejected += 1
            deliver.finalize_rejected(cfg, ledger, sid)
            try:
                # unfixable-only per the stored fixable field; fix-failed
                # marker for all-fixable rejects (Adnaan 08-15);
                # unparseable reasons are an UNKNOWN, never fix-failed
                labels = reports.session_reject_labels(
                    json.loads(row["reasons_json"] or "[]"))
            except json.JSONDecodeError:
                labels = [reports.UNREADABLE_MARKER]
            reject_label_lists.append(labels)
        elif row["state"] == "DELIVERED" and row["fix_attempts"] > 0:
            fixed += 1
    labels = reports.ordered_reject_labels(reject_label_lists)

    counts = ledger.counts_by_state()
    hours = {g: ledger.delivered_hours(g) for g in C.GAMES}
    now_ist = datetime.now(C.IST)
    b = reports.BatchStats(
        batch_no=batch_no, finished_ist=now_ist,
        duration_min=max(round((datetime.now(timezone.utc) - t0)
                               .total_seconds() / 60), 1),
        delivered=dstats["delivered"],
        total=len([s for s in all_sids if ledger.get(s)]),
        auto_fixed=fixed, rejected=rejected, reject_labels=labels,
        hours_delta=dstats["hours"],
        hours_kamla=hours["kamla"], hours_ow=hours["outer_wilds"],
        pending=counts.get("DISCOVERED", 0),
        incomplete=len(ledger.incomplete_list()),
        ok=dstats["upload_failures"] == 0,
        on_fallback=sum(1 for s in all_sids
                        if _VLM_RUN_STATE["fallback"].get(s)))
    ledger.finish_batch(batch_no, {
        "delivered": b.delivered, "rejected": b.rejected,
        "hours_delta": b.hours_delta, "auto_fixed": b.auto_fixed,
        "sessions": all_sids})
    return b


def _pace_now(ledger: Ledger) -> pace.PaceStatus:
    n_players = ledger.db.execute(
        "SELECT COUNT(DISTINCT player_email) n FROM sessions "
        "WHERE player_email != ''").fetchone()["n"] or 150
    return pace.compute(
        {g: ledger.delivered_hours(g) for g in C.GAMES},
        ledger.delivered_last_24h(), datetime.now(C.IST),
        n_players=n_players)


def send_daily_report_if_due(cfg: C.Config, ledger: Ledger,
                             now_ist: datetime | None = None) -> bool:
    # payment-endgame interlock, enforced HERE so it binds every caller —
    # the continuous driver, the dormant batch driver reached by a
    # rollback (its unit passes no --quiet), and the manual daily-report
    # command alike. Guarding only the continuous call site left the
    # rollback path free to stamp the unstamped rebuild cohort into one
    # day's sheet and deadlock the regen (r-loop 2 blocker).
    if not C.CONT_DAILY_REPORTS:
        print("[daily] suppressed — CONT_DAILY_REPORTS=False "
              "(payment-endgame interlock)", file=sys.stderr)
        return False
    now_ist = now_ist or datetime.now(C.IST)
    if now_ist.hour < C.DAILY_REPORT_HOUR_IST:
        return False
    day = now_ist.strftime("%Y-%m-%d")
    marker = cfg.reports_dir / day / ".sent"
    if marker.exists():
        return False
    # the reporting window ENDS REPORT_OFFSET_H before send time (Adnaan
    # 08-15: OFFSET over restate) so every cohort in it has settled at
    # generation, and RUNS FROM the previous window's end. A fixed
    # trailing-24h from a drifting send time leaves gaps (send 14:29 then
    # 14:01 → 28 min of deliveries in no report) or overlaps
    # (review-r3 #24); the persisted anchor makes windows contiguous.
    # Fallback for the first send ever: 24 h ending at the offset edge.
    hi_dt = now_ist.astimezone(timezone.utc) \
        - timedelta(hours=C.REPORT_OFFSET_H)
    hi = hi_dt.isoformat(timespec="seconds")
    anchor = cfg.reports_dir / ".last_daily_sent"
    lo = (hi_dt - timedelta(hours=24)).isoformat(timespec="seconds")
    window_clamped = False
    try:
        stored = anchor.read_text().strip()
        # sanity: never widen beyond 48 h (host clock jumps, long outage)
        floor_ = (hi_dt - timedelta(hours=48)).isoformat(timespec="seconds")
        if floor_ <= stored < hi:
            lo = stored
        elif stored and stored < floor_:
            # An anchor older than 48 h is silently replaced by a trailing
            # 24 h — which is exactly the state the payment endgame leaves
            # behind: recal_regen_sheets writes the anchor at
            # 2026-08-16T05:32:50Z and CONT_DAILY_REPORTS stays False until
            # the regen completes, so if dailies resume more than 48 h later
            # this message's counters cover 24 h while the SHEET attached to
            # it still counts the older roots through the late-arrival
            # guard. The hours are conserved either way, but the headline
            # and its own attachment disagreed with nothing saying so
            # (r-loop 3). Say so.
            window_clamped = True
    except (OSError, ValueError):
        pass
    row = ledger.db.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(duration_delivered_s),0) s "
        "FROM sessions WHERE state='DELIVERED' AND delivered_at>=? AND "
        "delivered_at<?", (lo, hi)).fetchone()
    # windowed on the REJECTED transition ts (immutable events row), not
    # updated_at — finalize_rejected bumps updated_at after the report is
    # sent, which re-counted the session in the NEXT day's window
    rej_rows = ledger.db.execute(
        f"SELECT reasons_json FROM sessions WHERE state='REJECTED' AND "
        f"{reports.REJECT_TS}>=? AND {reports.REJECT_TS}<?",
        (lo, hi)).fetchall()
    # unfixable-only per stored fixable fields, ALL labels per session,
    # fix-failed marker for all-fixable rejects (Adnaan 08-15); the count
    # orders the line but is not printed
    counts: dict[str, int] = {}
    for r in rej_rows:
        try:
            labels = reports.session_reject_labels(
                json.loads(r["reasons_json"] or "[]"), daily=True)
        except json.JSONDecodeError:
            labels = [reports.UNREADABLE_MARKER]   # unknown ≠ fix-failed
        for lbl in labels:
            counts[lbl] = counts.get(lbl, 0) + 1
    dups = ledger.db.execute(
        f"SELECT COUNT(*) n FROM sessions WHERE state='REJECTED' AND "
        f"{reports.REJECT_TS}>=? AND {reports.REJECT_TS}<? AND "
        f"reasons_json LIKE '%INT_DUP_CROSS%'", (lo, hi)).fetchone()["n"]
    p = _pace_now(ledger)
    d = reports.DailyStats(
        day_ist=now_ist,
        delivered_hours_today=row["s"] / 3600.0,
        delivered_sessions_today=row["n"],
        rejected_sessions_today=len(rej_rows),
        hours_kamla=ledger.delivered_hours("kamla"),
        hours_ow=ledger.delivered_hours("outer_wilds"),
        collected_kamla=ledger.collected_hours("kamla"),
        collected_ow=ledger.collected_hours("outer_wilds"),
        days_left=max(int((C.DEADLINE_IST - now_ist).total_seconds()
                          // 86400), 0),
        reject_counts=sorted(counts.items(),
                             key=lambda kv: (-kv[1], kv[0])),
        integrity_lines=([f"{dups} cross-player duplicate"
                          f"{'s' if dups > 1 else ''} (kept earlier upload)"]
                         if dups else []),
        # the folder-issues heartbeat (Adnaan via d3, 08-15) — a snapshot
        # count at payment-send time; the list itself follows in the
        # folder-issues message minutes later
        folder_issues=len(reports.build_folder_issues(ledger)))
    counted: list[str] = []
    accepted: list[str] = []
    csv_path, _md = reports.write_payment_sheet(cfg, ledger, now_ist,
                                                bounds=(lo, hi),
                                                counted_out=counted,
                                                accepted_out=accepted)
    msg = reports.build_daily_message(d, p)
    if window_clamped:
        msg += ("\n\n⚠️ Window note: the stored anchor was older than 48 h "
                "(driver paused — payment endgame), so the counters above "
                "cover the trailing 24 h only. The ATTACHED SHEET is "
                "authoritative: it still credits the older cohort through "
                "the late-arrival guard, so its totals are legitimately "
                "larger than this headline.")
    try:
        telegram.send_message(cfg, msg)
    except telegram.TelegramError as e:
        print(f"[daily-report-undelivered] {e}", file=sys.stderr)
        return False
    # ordering is load-bearing: STAMPS first, then the anchor, then the
    # marker. Anchor-before-stamps left a kill window where the next tick
    # regenerated the NEXT window and every unstamped root of the
    # just-reported one re-entered as a late arrival — a full window of
    # payment hours counted twice (review-r5 #39, BLOCKER). With stamps
    # first, a kill anywhere in this sequence errs toward an identical or
    # smaller resent sheet — never toward double-counted hours. The
    # stamps are exactly what THIS sheet counted (review-r5 #3).
    stamped = reports.mark_uploads_reported(ledger, lo, hi, sids=counted)
    if stamped:
        print(f"[daily] stamped {stamped} root upload(s) as reported")
    # the accepted-side mark rides the SAME pre-anchor position for the
    # same reason: stamped-then-killed errs toward a smaller resent sheet,
    # never toward hours paid twice (RULED split, Adnaan 2026-08-18)
    acc_stamped = reports.mark_accepted_reported(ledger, accepted)
    if acc_stamped:
        print(f"[daily] stamped {acc_stamped} node(s) as accepted-reported")
    anchor.write_text(hi)          # next report's window starts here
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    try:
        telegram.send_document(cfg, csv_path, caption="payment sheet")
    except telegram.TelegramError as e:
        print(f"[daily-sheet-undelivered] {e} — sheet remains at "
              f"{csv_path}", file=sys.stderr)
        # the message channel just worked, so an alert has a good chance
        # even when the attachment path fails (review-r2 #40/#46)
        try:
            telegram.send_message(
                cfg, f"⚠️ payment sheet attachment failed to send — "
                     f"file is on the VM at {csv_path}")
        except telegram.TelegramError:
            pass
    return True


def send_folder_issues_if_due(cfg: C.Config, ledger: Ledger,
                              now_ist: datetime | None = None) -> bool:
    """Second daily report (Adnaan via d3, 08-15): incomplete uploads +
    badly-named folders. A live snapshot, NOT window-based — no offset, no
    anchor, no cohort logic (different question from the payment sheet);
    same trigger hour, own marker, SEPARATE message + CSV so chase-work
    forwards without the payment sheet. An empty snapshot sends nothing
    (an empty forward is noise) but still writes the marker."""
    if not C.CONT_DAILY_REPORTS:
        return False                  # rides the payment interlock (r-loop 2)
    now_ist = now_ist or datetime.now(C.IST)
    if now_ist.hour < C.DAILY_REPORT_HOUR_IST:
        return False
    day = now_ist.strftime("%Y-%m-%d")
    marker = cfg.reports_dir / day / ".issues-sent"
    if marker.exists():
        return False
    # the payment message must have gone out first — its heartbeat says
    # "see NEXT message", and a failed payment send with a successful
    # issues send would leave next tick's payment message pointing at a
    # message that already arrived (folder-issues review #5)
    if not (cfg.reports_dir / day / ".sent").exists():
        return False
    csv_path, rows = reports.write_folder_issues_csv(cfg, ledger, now_ist)
    if not rows:
        print("[folder-issues] none outstanding — not sent")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return False
    msg = reports.build_folder_issues_message(rows, now_ist)
    try:
        telegram.send_message(cfg, msg)
    except telegram.TelegramError as e:
        # marker unwritten — retried next tick; a duplicate message is the
        # cheap failure mode (same doctrine as the payment report)
        print(f"[folder-issues-undelivered] {e}", file=sys.stderr)
        return False
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    try:
        telegram.send_document(cfg, csv_path, caption="folder issues")
    except telegram.TelegramError as e:
        print(f"[folder-issues-csv-undelivered] {e} — file at {csv_path}",
              file=sys.stderr)
        try:
            telegram.send_message(
                cfg, f"⚠️ folder-issues csv failed to send — file is on "
                     f"the VM at {csv_path}")
        except telegram.TelegramError:
            pass
    return True


def _batch_fallback_count(cfg: C.Config, sids: list[str]) -> int:
    """R23 'N on fallback model' from the dossiers of record: the run-level
    _VLM_RUN_STATE map is empty for crash-resumed batches, which silently
    dropped the flag from their batch message (review-r2 #32)."""
    n = 0
    for sid in sids:
        try:
            v = json.loads(
                (cfg.dossiers / sid / "verdict.json").read_text())
            if any(m.get("rung", 0) > 0
                   for m in (v.get("metrics") or {}).get("models_used", [])):
                n += 1
        except (OSError, json.JSONDecodeError):
            continue
    return n


def _finalize_orphan_rejects(cfg: C.Config, ledger: Ledger) -> None:
    """REJECTED sessions whose run died before U's finalize pass kept their
    work dirs forever and never got a coaching dossier (review-r2 #8/#23).
    Triggers on a leftover work dir, a leftover -analysis dir (a crash
    between finalize's two rmtrees leaks it, review-r3 #46), OR a missing
    dossier_path (rejects that never had a work dir would otherwise never
    get their coaching dossier, review-r3 #27). Idempotent."""
    for r in ledger.by_state("REJECTED"):
        sid = r["session_id"]
        if (cfg.work / sid).exists() \
                or (cfg.work / f"{sid}-analysis").exists() \
                or not r["dossier_path"]:
            try:
                deliver.finalize_rejected(cfg, ledger, sid)
            except Exception as e:
                print(f"[finalize-failed] {sid}: {e}", file=sys.stderr)


# ------------------------------------------------- overlap driver (R20)

@dataclass
class _Batch:
    """One batch's descriptor as it moves D -> V -> U (plan §6)."""
    no: int
    sids: list[str]
    t0_utc: datetime
    slot: bool = False        # True when D took a flight-semaphore slot
    dl_s: float = 0.0
    val_s: float = 0.0
    up_s: float = 0.0
    on_fallback: int = 0      # R23 count for this batch's verdicts


def _partition_resume(ledger: Ledger) -> tuple[list[_Batch], list[_Batch],
                                               list[_Batch]]:
    """Group RESUMABLE rows back into their original batches (open batches'
    start-written summary_json; split children ride with their parent),
    FIFO-regroup any strays into new batches, and route each batch to its
    most-upstream owner: D (still downloading), V (validating/fixing), or
    U (delivering only). A kill at any instant resumes exactly (§13)."""
    rows = ledger.by_state(*RESUMABLE)
    if not rows:
        return [], [], []
    state = {r["session_id"]: r["state"] for r in rows}
    parent = {r["session_id"]: r["parent_id"] for r in rows}
    assigned: dict[str, int] = {}
    groups: dict[int, list[str]] = {}
    # members some FINISHED batch already reported: carrying them back
    # into a still-open batch counted their delivery/hours in TWO batch
    # messages — e.g. a HOLD_VLM member that delivered via the guaranteed
    # hold batch while its original batch stayed open (review-r5 #22)
    already_reported: set[str] = set()
    for fb in ledger.db.execute(
            "SELECT summary_json FROM batches WHERE finished IS NOT NULL"
    ).fetchall():
        try:
            already_reported.update(
                json.loads(fb["summary_json"] or "{}").get("sessions") or [])
        except json.JSONDecodeError:
            pass
    for b in ledger.open_batches():
        try:
            sids = (json.loads(b["summary_json"] or "{}")
                    .get("sessions")) or []
        except json.JSONDecodeError:
            sids = []
        for s in sids:
            if s in assigned:
                continue
            if s in state:
                assigned[s] = b["batch_no"]
                groups.setdefault(b["batch_no"], []).append(s)
            else:
                r = ledger.get(s)
                if r is not None and s not in already_reported \
                        and r["state"] in (
                        "DELIVERED", "REJECTED", "SPLIT", "DUPLICATE",
                        "QUARANTINED"):
                    # TERMINAL members (e.g. DELIVERED before the kill)
                    # stay in the membership: dropping them under-reported
                    # the closing batch message (review-r3 #43/#25).
                    # DISCOVERED/HOLD_VLM members deliberately do NOT ride
                    # — carrying them marked them `attempted`, deferring
                    # their re-intake a full tick and closing their batch
                    # around them (review-r4 #35); they re-enter via
                    # next_batch / the hold branch instead.
                    assigned[s] = b["batch_no"]
                    groups.setdefault(b["batch_no"], []).append(s)
    for s, p in parent.items():
        if s not in assigned and p in assigned:
            assigned[s] = assigned[p]
            groups[assigned[p]].append(s)
    strays = [s for s in state if s not in assigned]
    for i in range(0, len(strays), C.BATCH_SIZE):
        chunk = strays[i:i + C.BATCH_SIZE]
        groups[ledger.start_batch(sessions=chunk)] = chunk
    d_q: list[_Batch] = []
    v_q: list[_Batch] = []
    u_q: list[_Batch] = []
    v_states = {"INGESTED", "VALIDATING", "FIX_QUEUED", "FIXING",
                "REVALIDATING"}
    for no in sorted(groups):
        b = _Batch(no=no, sids=groups[no],
                   t0_utc=datetime.now(timezone.utc))
        sts = {state[s] for s in b.sids if s in state}
        if "DOWNLOADING" in sts:
            d_q.append(b)
        elif sts & v_states:
            v_q.append(b)
        else:
            u_q.append(b)
    return d_q, v_q, u_q


def _overlapped_run(cfg: C.Config, ledger: Ledger, alerts: list[str], *,
                    max_batches: int, dest_prefix: str,
                    send_telegram: bool) -> None:
    """Three threads at batch granularity (R20): D downloads batch N+1
    while V (this thread) validates batch N and U uploads batch N−1.
    Handoff via two queues of _Batch descriptors; ≤MAX_BATCHES_IN_FLIGHT
    new batches local at once; every thread has its OWN Ledger connection;
    writes stay phase-scoped exactly as in lockstep (§6)."""
    q_dv: queue.Queue = queue.Queue()
    q_vu: queue.Queue = queue.Queue()
    stop = threading.Event()
    flight = threading.Semaphore(C.MAX_BATCHES_IN_FLIGHT)
    attempted: set[str] = set()

    def _release(b: _Batch) -> None:
        """Idempotent flight-slot release. EVERY path a batch can take —
        delivered, left open, thread crash, shutdown drain — must end
        here exactly once; a leaked slot wedges D in acquire() forever
        while the process holds the run lock (review-r1 #0/#1/#2)."""
        if b.slot:
            b.slot = False
            flight.release()

    d_resume, v_resume, u_resume = _partition_resume(ledger)
    for b in d_resume + v_resume + u_resume:
        attempted.update(b.sids)

    def d_thread() -> None:
        # constructor inside the try: a Ledger() failure here used to kill
        # the thread before the finally existed, so q_dv never got its
        # poison None and V blocked forever holding the run lock
        # (review-r2 #1)
        dl = None
        try:
            dl = Ledger(cfg.ledger_path)
            for b in d_resume:
                if stop.is_set():
                    q_dv.put(b)     # V decides; never strand a batch
                    continue
                t0 = time.monotonic()
                _download_phase(cfg, dl, b.sids, alerts)
                b.dl_s = time.monotonic() - t0
                print(f"[dl b{b.no}] resumed {len(b.sids)} session(s) "
                      f"{b.dl_s / 60:.1f}m")
                q_dv.put(b)
            # HOLD_VLM retries get ONE guaranteed batch per run BEFORE new
            # intake — the old only-when-idle branch starved held sessions
            # indefinitely while fresh uploads kept arriving (review-r4
            # #9); F5 promises a retry every run, not "when quiet"
            held = [r["session_id"] for r in dl.by_state("HOLD_VLM")
                    if r["session_id"] not in attempted][:C.BATCH_SIZE]
            if held and not stop.is_set():
                got = flight.acquire(timeout=1.0)
                while not got and not stop.is_set():
                    got = flight.acquire(timeout=1.0)
                if got:
                    if stop.is_set():
                        flight.release()
                    else:
                        attempted.update(held)
                        q_dv.put(_Batch(
                            no=dl.start_batch(sessions=held), sids=held,
                            t0_utc=datetime.now(timezone.utc), slot=True))
            started = 0
            while not stop.is_set() and started < max_batches:
                if deliver.disk_free_gb(cfg.home) < C.DISK_LOW_WATER_GB:
                    _alert(cfg, f"disk under {C.DISK_LOW_WATER_GB} GB free "
                                f"— downloads paused (F7)", alerts)
                    break               # V/U keep draining and wiping
                # stop-aware acquire: a timeout-less acquire cannot be
                # woken by stop.set() and is the deadlock half of the
                # leaked-slot wedge (review-r1 #0)
                got = flight.acquire(timeout=1.0)
                while not got and not stop.is_set():
                    got = flight.acquire(timeout=1.0)
                if stop.is_set():
                    if got:
                        flight.release()
                    break
                slot_held = True
                try:
                    sids = [s for s in ingest.next_batch(
                                dl, exclude=attempted)]
                    hold_retry = False
                    if not sids:
                        # nothing new to download: HOLD_VLM retries to V
                        sids = [r["session_id"]
                                for r in dl.by_state("HOLD_VLM")
                                if r["session_id"] not in attempted]
                        sids = sids[:C.BATCH_SIZE]
                        hold_retry = True
                    if not sids:
                        flight.release()
                        slot_held = False
                        break
                    attempted.update(sids)
                    b = _Batch(no=dl.start_batch(sessions=sids), sids=sids,
                               t0_utc=datetime.now(timezone.utc), slot=True)
                    started += 1
                    if not hold_retry:
                        t0 = time.monotonic()
                        _download_phase(cfg, dl, sids, alerts)
                        b.dl_s = time.monotonic() - t0
                        print(f"[dl b{b.no}] {len(sids)} session(s) "
                              f"{b.dl_s / 60:.1f}m")
                    slot_held = False       # ownership rides with b.slot
                    q_dv.put(b)
                except Exception:
                    if slot_held:
                        flight.release()
                    raise
        except Exception as e:
            _alert(cfg, f"download thread crashed: {type(e).__name__}: {e}",
                   alerts)
        finally:
            if dl is not None:
                dl.close()
            q_dv.put(None)

    def u_process(ul: Ledger, b: _Batch) -> None:
        t0 = time.monotonic()
        dstats = _deliver_phase(cfg, ul, b.sids, alerts,
                                dest_prefix=dest_prefix)
        b.up_s = time.monotonic() - t0
        rejected, fixed = 0, 0
        reject_label_lists: list[list[str]] = []
        for sid in b.sids:
            row = ul.get(sid)
            if not row:
                continue
            if row["state"] == "REJECTED":
                rejected += 1
                deliver.finalize_rejected(cfg, ul, sid)
                try:
                    # unfixable-only + fix-failed marker (Adnaan 08-15);
                    # unparseable reasons are an UNKNOWN, never fix-failed
                    labels = reports.session_reject_labels(
                        json.loads(row["reasons_json"] or "[]"))
                except json.JSONDecodeError:
                    labels = [reports.UNREADABLE_MARKER]
                reject_label_lists.append(labels)
            elif row["state"] == "DELIVERED" and row["fix_attempts"] > 0:
                fixed += 1
        labels = reports.ordered_reject_labels(reject_label_lists)
        # the R22 tuning gauge: what bound this batch
        print(f"[batch b{b.no}] stages dl {b.dl_s / 60:.1f}m · "
              f"val {b.val_s / 60:.1f}m · up {b.up_s / 60:.1f}m")
        still_open = [s for s in b.sids
                      if (r := ul.get(s)) and r["state"] in _MID_PIPELINE]
        if still_open:
            # gate-failed session handed back to V's domain — NEXT run
            # (attempted-set semantics); the batch stays open and its
            # message fires on the run that finishes it (§6). The flight
            # slot is released by u_thread's finally, never here.
            print(f"[up b{b.no}] batch left open — "
                  f"{len(still_open)} session(s) mid-pipeline "
                  f"(e.g. {still_open[0]})")
            return
        counts = ul.counts_by_state()
        hours = {g: ul.delivered_hours(g) for g in C.GAMES}
        # delivered/hours from the LEDGER ROWS, not this run's dstats: a
        # batch that completes across runs (gate hand-back, crash resume)
        # must report every member it ever delivered, not just the closing
        # run's (review-r2 #27)
        del_rows = [r for s in b.sids
                    if (r := ul.get(s)) and r["state"] == "DELIVERED"]
        stats = reports.BatchStats(
            batch_no=b.no, finished_ist=datetime.now(C.IST),
            duration_min=max(round((datetime.now(timezone.utc) - b.t0_utc)
                                   .total_seconds() / 60), 1),
            delivered=len(del_rows),
            total=len([s for s in b.sids if ul.get(s)]),
            auto_fixed=fixed, rejected=rejected, reject_labels=labels,
            hours_delta=sum(r["duration_delivered_s"] or 0
                            for r in del_rows) / 3600.0,
            hours_kamla=hours["kamla"], hours_ow=hours["outer_wilds"],
            pending=counts.get("DISCOVERED", 0),
            incomplete=len(ul.incomplete_list()),
            ok=dstats["upload_failures"] == 0,
            on_fallback=max(b.on_fallback,
                            _batch_fallback_count(cfg, b.sids)))
        ul.finish_batch(b.no, {
            "delivered": stats.delivered, "rejected": stats.rejected,
            "hours_delta": stats.hours_delta,
            "auto_fixed": stats.auto_fixed, "sessions": b.sids})
        if send_telegram and (stats.delivered or stats.rejected):
            try:
                telegram.send_message(
                    cfg, reports.build_batch_message(stats, _pace_now(ul)))
            except telegram.TelegramError as e:
                print(f"[batch-msg-undelivered] {e}", file=sys.stderr)

    def u_thread() -> None:
        """One crash must cost one batch, not the thread: a dead U leaves
        every queued batch's flight slot unreleased and wedges D, then V,
        then the whole run — while it still holds the run lock
        (review-r1 #1). Hence per-batch guard + slot release in finally."""
        ul = None
        try:
            ul = Ledger(cfg.ledger_path)
            for b in u_resume:
                try:
                    u_process(ul, b)
                except Exception as e:
                    _alert(cfg, f"upload failed for batch {b.no} — will "
                                f"resume next run: {type(e).__name__}: {e}",
                           alerts)
                finally:
                    _release(b)
            while True:
                b = q_vu.get()
                if b is None:
                    break
                try:
                    u_process(ul, b)
                except Exception as e:
                    _alert(cfg, f"upload failed for batch {b.no} — will "
                                f"resume next run: {type(e).__name__}: {e}",
                           alerts)
                finally:
                    _release(b)
        except Exception as e:
            # only the Ledger constructor can reach here (per-batch guards
            # above). A dead U must NOT stop draining q_vu: V keeps
            # feeding slotted batches, and with 3 in flight their leaked
            # slots would wedge D in acquire while V waits on q_dv —
            # deadlock with the run lock held (review-r3 #0). Keep
            # consuming and releasing until the poison pill; sessions
            # keep their states and deliver next run.
            _alert(cfg, f"upload thread failed to start — batches will "
                        f"resume next run: {type(e).__name__}: {e}", alerts)
            while True:
                b = q_vu.get()
                if b is None:
                    break
                _release(b)
        finally:
            if ul is not None:
                ul.close()

    dthr = threading.Thread(target=d_thread, name="hl-D", daemon=True)
    uthr = threading.Thread(target=u_thread, name="hl-U", daemon=True)
    dthr.start()
    uthr.start()

    def v_process(b: _Batch) -> None:
        t0 = time.monotonic()
        _validate_phase(cfg, ledger, b.sids, alerts, workers=cfg.workers)
        # children_sink=attempted: a child that lands HOLD_VLM mid-fix must
        # already be in `attempted` when D's hold-retry branch looks, or
        # the same session rides two live batches (review-r1 #10)
        kids = _fix_phase(cfg, ledger, b.sids, alerts, workers=cfg.workers,
                          children_sink=attempted)
        b.sids = list(dict.fromkeys(b.sids + kids))
        attempted.update(kids)
        b.val_s = time.monotonic() - t0
        b.on_fallback = sum(1 for s in b.sids
                            if _VLM_RUN_STATE["fallback"].get(s))
        print(f"[val b{b.no}] {len(b.sids)} session(s) "
              f"{b.val_s / 60:.1f}m")
        q_vu.put(b)

    try:
        for b in v_resume:
            try:
                v_process(b)
            except Exception as e:
                # one batch's failure must not kill the run: sessions keep
                # their ledger states and resume next run; the slot dies
                # here instead of wedging D (review-r1 #2)
                _alert(cfg, f"validation failed for batch {b.no} — will "
                            f"resume next run: {type(e).__name__}: {e}",
                       alerts)
                _release(b)
        while True:
            item = q_dv.get()
            if item is None:
                break
            try:
                v_process(item)
            except Exception as e:
                _alert(cfg, f"validation failed for batch {item.no} — will "
                            f"resume next run: {type(e).__name__}: {e}",
                       alerts)
                _release(item)
            # periodic duties INSIDE the drain loop: a multi-hour backlog
            # run must not sail past 14:00 IST or leave the nightly GCS
            # sync mirroring a stale backup (§6). Guarded: a reporting or
            # backup hiccup must not abort the whole overlapped run
            # (review-r2 #26/#45)
            try:
                if send_telegram:
                    send_daily_report_if_due(cfg, ledger)
                    send_folder_issues_if_due(cfg, ledger)
                ledger.backup_daily(cfg.backups, keep=C.LEDGER_BACKUP_KEEP)
            except Exception as e:
                _alert(cfg, f"periodic duties failed (run continues): "
                            f"{type(e).__name__}: {e}", alerts)
    finally:
        stop.set()
        # drain anything D queued that V never consumed — their slots must
        # die here or dthr.join() below waits on a wedged acquire forever
        try:
            while True:
                leftover = q_dv.get_nowait()
                if leftover is not None:
                    _release(leftover)
        except queue.Empty:
            pass
        q_vu.put(None)
        # U's join is UNBOUNDED: it always terminates once poisoned (every
        # u_process does bounded rclone work), and a timeout here would
        # kill the process mid-upload on every heavy run's tail batch —
        # any single >10-min upload could then never deliver (review-r2
        # #20). D's join keeps a timeout: its acquire is stop-aware, so
        # only a wedged rclone download holds it, bounded by rclone's own
        # timeout.
        uthr.join()
        dthr.join(timeout=900)
        # D may have queued one more batch mid-shutdown, and a U that died
        # at startup leaves slotted batches in q_vu — sweep both now that
        # the threads are down (review-r2 #1)
        for q in (q_dv, q_vu):
            try:
                while True:
                    leftover = q.get_nowait()
                    if leftover is not None:
                        _release(leftover)
            except queue.Empty:
                pass
        if dthr.is_alive():
            # daemon thread dies with the process; the run lock is released
            # by run()'s finally — next tick starts clean (review-r1 #6)
            _alert(cfg, "download thread failed to stop within 15 min — "
                        "exiting run; state resumes next tick", alerts)


def _close_stale_batches(ledger: Ledger) -> None:
    """Finish any open batch with no member still mid-pipeline. Kill-time
    regrouping (children became strays, members went terminal in successor
    batches) and non-RESUMABLE parking (HOLD_VLM/DISCOVERED) both orphan
    open batches rows forever otherwise (review-r1 #19/#23; observed live
    as batch_no=2 on go-live day). Bookkeeping close only — no Telegram
    message; the sessions' hours were reported by the batches that actually
    delivered them."""
    for b in ledger.open_batches():
        try:
            sids = (json.loads(b["summary_json"] or "{}")
                    .get("sessions")) or []
        except json.JSONDecodeError:
            sids = []
        rows = [ledger.get(s) for s in sids]
        if any(r and r["state"] in _MID_PIPELINE for r in rows):
            continue
        ledger.finish_batch(b["batch_no"], {
            "delivered": sum(1 for r in rows
                             if r and r["state"] == "DELIVERED"),
            "rejected": sum(1 for r in rows
                            if r and r["state"] == "REJECTED"),
            "sessions": sids, "closed_by": "stale-batch-sweep"})


def _iso_age_h(ts_str) -> float:
    """Hours since an ISO stamp. Returns 0.0 when it is missing or
    unparseable -- an unreadable timestamp must never authorise deleting
    media (same contract as _terminal_age_h)."""
    try:
        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def _terminal_age_h(row) -> float:
    """Hours since a ledger row last changed. Returns 0.0 when the stamp is
    missing or unparseable — an unreadable timestamp must never be what
    authorises deleting media."""
    try:
        ts = datetime.fromisoformat(
            str(row["updated_at"]).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def _sweep_terminal_work(cfg: C.Config, ledger: Ledger) -> None:
    """A kill between the DELIVERED commit and the local wipe leaks work/,
    -analysis/ and stage dirs forever — resumed runs skip DELIVERED
    sessions, so nothing ever reclaimed them (review-r1 #22). REJECTED is
    deliberately excluded: finalize_rejected owns that wipe."""
    if cfg.work.exists():
        for p in cfg.work.iterdir():
            if p.name.endswith(".split-manifest.json"):
                # stray manifest whose parent already went terminal
                parent = ledger.get(p.name[:-len(".split-manifest.json")])
                if parent and parent["state"] in ("SPLIT", "REJECTED",
                                                  "DELIVERED"):
                    p.unlink(missing_ok=True)
                continue
            if not p.is_dir():
                continue
            sid = p.name[:-len("-analysis")] \
                if p.name.endswith("-analysis") else p.name
            row = ledger.get(sid)
            if row and row["state"] == "QUARANTINED" \
                    and _terminal_age_h(row) >= C.CONT_QUARANTINE_RECLAIM_H:
                # QUARANTINED had NO reclaim arm at all, so its media was
                # held forever while being invisible to the media cap —
                # the disk filled and intake stopped with no way back
                # (r-loop 3). The dossier is the evidence of record and
                # Drive I keeps the original, so reclaiming the local copy
                # after a triage window loses nothing recoverable.
                shutil.rmtree(p, ignore_errors=True)
                deliver._drop_shift_entry(cfg, sid)
                continue
            if row and row["state"] == "DISCOVERED":
                # A DISCOVERED row holding media is a download that keeps
                # failing (_download_one returns the row here on every
                # transient and every zip_incomplete, and ingest.download
                # leaves what rclone already transferred behind). Nothing
                # reclaimed it, so an abandoned multi-part zip parked
                # gigabytes forever -- invisible to the media cap until
                # r-loop 5 taught _local_count to count it, and then
                # capable of stopping intake outright with no way back.
                # Reclaiming loses no data: Drive I keeps the original and
                # rclone re-downloads idempotently (the same property that
                # makes DOWNLOADING kill-resume safe). Age from the last
                # completed download -- updated_at and the DISCOVERED stint
                # are both re-stamped by the 5-min retry.
                # age from the first FAILURE, not first sight on Drive
                # (r-loop 6). The INGESTED anchor r-loop 5 used can never
                # exist for these rows: INGESTED is written only at the END
                # of a SUCCESSFUL download, and a row only returns to
                # DISCOVERED holding media from a FAILED one -- so COALESCE
                # yielded '' and MIN() returned the ingest.scan insert, i.e.
                # the moment the folder was first seen on Drive. With intake
                # cap-throttled and one serial download worker, a session
                # routinely waits >12h between discovery and its first
                # attempt, so the documented grace was effectively ZERO: the
                # next hourly sweep deleted a partial multi-part transfer
                # that rclone --checksum would otherwise have resumed.
                # Anchoring on the first DISCOVERED event AFTER a DOWNLOADING
                # event is exact, and MIN (not MAX) means the 5-min retry
                # bounce cannot reset it. No DOWNLOADING event -> no age ->
                # never reclaimed, which is right: nothing was downloaded.
                first_disc = ledger.db.execute(
                    "SELECT MIN(ts) t FROM events WHERE session_id=? "
                    "AND to_state='DISCOVERED' AND ts > (SELECT MIN(ts) "
                    "FROM events WHERE session_id=? AND "
                    "to_state='DOWNLOADING')",
                    (sid, sid)).fetchone()
                t = first_disc["t"] if first_disc else None
                if t and _iso_age_h(t) >= C.CONT_DISCOVERED_RECLAIM_H:
                    shutil.rmtree(p, ignore_errors=True)
                    deliver._drop_shift_entry(cfg, sid)
                continue
            if row and row["state"] in ("DELIVERED", "SPLIT", "DUPLICATE"):
                shutil.rmtree(p, ignore_errors=True)
                # the sid's shift record dies with its media: the live
                # paths drop it after their terminal commit, but a kill
                # BETWEEN commit and wipe left the entry behind forever
                # (DELIVERED/SPLIT have no other reclaim path — REJECTED
                # gets one via finalize_rejected; r-loop 2)
                deliver._drop_shift_entry(cfg, sid)
    if cfg.stage.exists():
        for pattern in ("*/*/*", "*/*/*/*"):
            for sdir in cfg.stage.glob(pattern):
                if not sdir.is_dir():
                    continue
                row = ledger.get(sdir.name)
                if row and row["state"] == "DELIVERED":
                    shutil.rmtree(sdir, ignore_errors=True)
                elif row and row["state"] == "QUARANTINED" \
                        and _terminal_age_h(row) >= \
                        C.CONT_QUARANTINE_RECLAIM_H:
                    # a delivery that crashed mid-stage leaks the staged
                    # copy as well as the work dir (r-loop 3)
                    shutil.rmtree(sdir, ignore_errors=True)


def _upload_ceiling_alert(cfg: C.Config, ledger: Ledger,
                          alerts: list[str]) -> None:
    """§5/§13: alert when a rolling 24 h's deliveries approach the external
    750 GB/user/day Drive cap (alert line at 600 GB). Approximation, stated:
    `bytes` is the Drive-I source size; delivered volume adds the ~20%
    rrd sample and small overheads — the 1.25 factor covers it
    (review-r1 #28)."""
    lo = (datetime.now(timezone.utc)
          - timedelta(hours=24)).isoformat(timespec="seconds")
    # split children carry bytes=0 (their media was cut locally), so a
    # bytes-only sum goes dark on split-heavy days (review-r2 #31): use
    # bytes when present, else the §15 ~3.13 GB per delivered
    # footage-hour estimate
    rows = ledger.db.execute(
        "SELECT bytes, duration_delivered_s FROM sessions "
        "WHERE state='DELIVERED' AND delivered_at>=?", (lo,)).fetchall()
    approx_gb = sum(
        (r["bytes"] * 1.25 / (1024 ** 3)) if r["bytes"]
        else ((r["duration_delivered_s"] or 0) / 3600.0 * 3.13)
        for r in rows)
    if approx_gb > 600:
        _alert(cfg, f"Drive upload ceiling: ~{approx_gb:.0f} GB delivered "
                    f"in the last 24 h — the 750 GB/day SA cap is close "
                    f"(§5); consider a second delivery SA", alerts)


def run(cfg: C.Config, *, max_batches: int = 50,
        dest_prefix: str = C.VENDOR, send_telegram: bool = True) -> int:
    # Mirror of the continuous side's rollback interlock (r-loop 5).
    # PIPELINE_CONTINUOUS was read in exactly ONE place — run_continuous —
    # so the flag was a one-way interlock: it stopped the continuous unit
    # when False, but nothing stopped the BATCH driver when True. The run
    # lock only guarantees the two never run at the same instant; it does
    # not stop the batch driver taking over during a continuous restart
    # window. An operator rolling forward the obvious way (set the flag,
    # deploy, `systemctl start hl-continuous`) without re-running
    # vm_setup --enable-continuous left BOTH armed: the next *:0/30 tick
    # wins run.lock, hl-pipeline.service is Type=oneshot with
    # TimeoutStartSec=infinity so a backlog run holds it for hours,
    # run_continuous returns 1 on each restart, 5 attempts in ~50s burn
    # StartLimitBurst, and the unit enters 'failed' with an OnFailure
    # alert naming the wrong cause — while production silently runs on
    # the batch driver, writing batch rows onto the continuous ledger.
    # Return 0, not 1: a timer tick that correctly declines is not a
    # failure and must not alert.
    if C.PIPELINE_CONTINUOUS:
        print("PIPELINE_CONTINUOUS is True — the continuous driver owns "
              "this pipeline; batch run refusing to start", file=sys.stderr)
        return 0
    if not acquire_lock(cfg):
        print("run lock held — exiting")
        return 0
    caff = None
    alerts: list[str] = []
    _reset_vlm_run_state()                 # every run restarts at rung 0
    try:
        cfg.ensure_dirs()
        ledger = Ledger(cfg.ledger_path)
        try:
            ledger.backup_daily(cfg.backups, keep=C.LEDGER_BACKUP_KEEP)
        except Exception as e:
            # an unguarded backup crash here aborted the ENTIRE tick —
            # no scan, no uploads, no sweeps, no alert — and repeated
            # every 30 min while the cause (ENOSPC, torn tmp) persisted
            # (review-r5 #4); the drain-loop call was already guarded
            _alert(cfg, f"start-of-run ledger backup failed (run "
                        f"continues): {type(e).__name__}: {e}", alerts)
        try:
            res = ingest.scan(cfg, ledger)
            for path, why in res.quarantined:
                print(f"[quarantined] {path}: {why}")
            if res.integrity_flags:
                for f in res.integrity_flags:
                    print(f"[integrity] {f}")
            for sid in res.dup_cross:
                # scan-time cross-dup rejects get their dossier + coaching
                # + local wipe like every other reject (review-r1 #24)
                try:
                    deliver.finalize_rejected(cfg, ledger, sid)
                except Exception as e:
                    print(f"[finalize-failed] {sid}: {e}", file=sys.stderr)
        except Exception as e:
            # any scan crash — not just rclone's RuntimeError: rc=0 garbage
            # JSON (JSONDecodeError) and malformed listing entries (KeyError)
            # must also degrade to an alert, never abort the run before the
            # READY/PACKAGED backlog drains (review-r4 #29). Exception never
            # catches KeyboardInterrupt/SystemExit (BaseException).
            _alert(cfg, f"Drive scan failed: {e}", alerts)

        if C.PIPELINE_OVERLAP:
            try:
                caff = subprocess.Popen(["caffeinate", "-i"])
            except FileNotFoundError:
                caff = None                # Linux VM: host never sleeps (R8)
            _overlapped_run(cfg, ledger, alerts, max_batches=max_batches,
                            dest_prefix=dest_prefix,
                            send_telegram=send_telegram)
            _close_stale_batches(ledger)
            _finalize_orphan_rejects(cfg, ledger)
            _sweep_terminal_work(cfg, ledger)
            _upload_ceiling_alert(cfg, ledger, alerts)
            held = ledger.counts_by_state().get("HOLD_VLM", 0)
            if held:
                _alert(cfg, f"{held} session(s) still HOLD_VLM at end of "
                            f"run — VLM sweep repeatedly failing (§13)",
                       alerts)
            if send_telegram:
                send_daily_report_if_due(cfg, ledger)
                # the overlap path is PRODUCTION: an idle tick never enters
                # the drain-loop duties (poison pill breaks first), so this
                # end-of-run site is the only guaranteed daily fire — the
                # issues message was wired only into lockstep and the
                # heartbeat's "see next message" pointed at nothing on
                # quiet days (folder-issues review #1)
                send_folder_issues_if_due(cfg, ledger)
            ledger.close()
            return 0

        attempted: set[str] = set()
        for _ in range(max_batches):
            # each session gets at most ONE pass per run — a stuck
            # PACKAGED/HOLD_VLM set must not spin the loop for hours while
            # holding the run lock (review-2 #9); the next launchd tick is
            # the retry cadence (§13)
            resume = [r["session_id"] for r in ledger.by_state(*RESUMABLE)
                      if r["session_id"] not in attempted]
            batch = resume or ingest.next_batch(ledger, exclude=attempted)
            if not batch:
                batch = [r["session_id"]
                         for r in ledger.by_state("HOLD_VLM")
                         if r["session_id"] not in attempted]
            if not batch:
                break
            batch = batch[:C.BATCH_SIZE]
            attempted.update(batch)
            try:
                caff = subprocess.Popen(["caffeinate", "-i"])
            except FileNotFoundError:
                caff = None
            try:
                b = process_batch(cfg, ledger, batch, alerts=alerts,
                                  dest_prefix=dest_prefix)
            finally:
                if caff:
                    caff.terminate()
                    caff = None
            if send_telegram and (b.delivered or b.rejected):
                try:
                    telegram.send_message(
                        cfg, reports.build_batch_message(
                            b, _pace_now(ledger)))
                except telegram.TelegramError as e:
                    print(f"[batch-msg-undelivered] {e}", file=sys.stderr)
        _close_stale_batches(ledger)
        _finalize_orphan_rejects(cfg, ledger)
        _sweep_terminal_work(cfg, ledger)
        _upload_ceiling_alert(cfg, ledger, alerts)
        held = ledger.counts_by_state().get("HOLD_VLM", 0)
        if held:
            _alert(cfg, f"{held} session(s) still HOLD_VLM at end of run — "
                        f"VLM sweep repeatedly failing (§13)", alerts)
        if send_telegram:
            send_daily_report_if_due(cfg, ledger)
            send_folder_issues_if_due(cfg, ledger)
        ledger.close()
        return 0
    finally:
        if caff:
            caff.terminate()
        release_lock(cfg)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "run"
    cfg = C.load()
    if cmd == "run":
        # --quiet: rebuild/maintenance runs (08-16 recal rebuild) — no
        # per-batch toplines, no daily/folder-issues generation (anchor,
        # markers and stamps untouched); _alert() and backup_daily stay
        # live. Normal timer units never pass it.
        quiet = "--quiet" in argv[1:]
        if quiet:
            print("[run] --quiet: telegram toplines + daily reports "
                  "suppressed for this run")
        return run(cfg, send_telegram=not quiet)
    if cmd == "run-continuous":
        # the always-on driver (Adnaan rulings 08-17). Lazy import: the
        # dormant batch path must not pay for (or break on) the module.
        from . import continuous
        # STRICT parsing. The default destination is the REAL client tree on
        # Drive II, and the old prefix-match silently fell back to it for any
        # spelling that was not the exact literal `--dest-prefix=`: a space
        # instead of `=`, an underscore, a trailing typo. The canary is the
        # one place an operator hand-types this under flip-time pressure,
        # and getting it wrong would upload test sessions into the
        # production client tree — where the canary teardown, which purges
        # only `_pipeline_test`, would never clean them, and where the real
        # ledger has no rows for them, so recal_verify_tree would later
        # brand them unexplained and deletable (r-loop 3).
        dest = C.VENDOR
        until_idle = quiet_flag = False
        for a in argv[1:]:
            if a.startswith("--dest-prefix="):
                dest = a.split("=", 1)[1]
                if not dest.strip():
                    print("--dest-prefix= requires a non-empty value "
                          "(empty would deliver to the Drive II ROOT)")
                    return 2
            elif a == "--until-idle":
                until_idle = True
            elif a == "--quiet":
                quiet_flag = True
            else:
                print(f"unknown argument {a!r} for run-continuous "
                      f"(--dest-prefix=NAME | --until-idle | --quiet). "
                      f"Refusing rather than defaulting to the real "
                      f"delivery prefix {C.VENDOR!r}.")
                return 2
        return continuous.run_continuous(
            cfg, dest_prefix=dest, until_idle=until_idle,
            send_telegram=not quiet_flag)
    if cmd == "status":
        ledger = Ledger(cfg.ledger_path)
        print(json.dumps({
            "states": ledger.counts_by_state(),
            "hours": {g: round(ledger.delivered_hours(g), 2)
                      for g in C.GAMES},
            "incomplete": len(ledger.incomplete_list())}, indent=1))
        return 0
    if cmd == "daily-report":
        ledger = Ledger(cfg.ledger_path)
        sent = send_daily_report_if_due(cfg, ledger)
        issues = send_folder_issues_if_due(cfg, ledger)
        print(f"daily report sent: {sent}; folder issues sent: {issues}")
        return 0
    print(f"unknown command {cmd!r} "
          f"(run | run-continuous | status | daily-report)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
