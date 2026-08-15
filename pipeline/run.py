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
import shutil
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

def acquire_lock(cfg: C.Config) -> bool:
    for _ in range(2):
        try:
            cfg.lock_dir.mkdir(parents=True)
            (cfg.lock_dir / "pid").write_text(str(os.getpid()))
            return True
        except FileExistsError:
            try:
                pid = int((cfg.lock_dir / "pid").read_text())
                os.kill(pid, 0)
                return False                      # live run holds it
            except (ValueError, FileNotFoundError):
                # the winner may be between mkdir and pid-write — give it
                # a beat before declaring the lock stale
                time.sleep(1.0)
                try:
                    pid = int((cfg.lock_dir / "pid").read_text())
                    os.kill(pid, 0)
                    return False
                except (ValueError, FileNotFoundError, ProcessLookupError,
                        PermissionError):
                    shutil.rmtree(cfg.lock_dir, ignore_errors=True)
            except (ProcessLookupError, PermissionError):
                shutil.rmtree(cfg.lock_dir, ignore_errors=True)   # stale
    return False


def release_lock(cfg: C.Config) -> None:
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
        return {"sid": args["sid"], "error": f"{type(e).__name__}: {e}",
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
            if "zip" in msg:
                # a multi-part zip still mid-upload reassembles as garbage;
                # the missing parts appear later — this must stay retryable,
                # never terminal (review-2 #10)
                ledger.set_state(sid, "DISCOVERED",
                                 f"zip payload incomplete/unreadable — "
                                 f"retrying next run: {msg}"[:300])
                ledger.incomplete_seen(row["drive_path"],
                                       ["zip parts incomplete"])
            elif "md5 mismatch" in msg:
                ledger.set_state(sid, "QUARANTINED", msg[:300])
                _alert(cfg, f"download quarantined {sid}: {e}", alerts)
            else:
                # transient transfer failure: back to the queue, alert once
                ledger.set_state(sid, "DISCOVERED",
                                 f"download failed — retrying next run: "
                                 f"{msg}"[:300])
                _alert(cfg, f"download failed for {sid} (will retry): {e}",
                       alerts)


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
    if workers > 1 and len(jobs) > 1:
        try:
            with concurrent.futures.ProcessPoolExecutor(
                    max_workers=workers, mp_context=ctx) as ex:
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
                kids = ledger.db.execute(
                    "SELECT session_id FROM sessions WHERE parent_id=?",
                    (sid,)).fetchall()
                if kids:
                    # crash landed between child insertion and the parent's
                    # SPLIT transition: re-validating the parent could
                    # re-verdict it deliverable and ship the whole video ON
                    # TOP of its segments (review-r1 #4) — complete the
                    # split instead, exactly as the pre-kill run would have
                    for k in kids:
                        if k["session_id"] not in children_created:
                            children_created.append(k["session_id"])
                        if children_sink is not None:
                            children_sink.add(k["session_id"])
                    ledger.set_state(sid, "SPLIT",
                                     f"{len(kids)} segments (completed "
                                     f"after mid-split crash)")
                    shutil.rmtree(cfg.work / sid, ignore_errors=True)
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
            has_raw = (work / "raw" / "inputs.jsonl").exists()
            plan = fix.plan_fixes(reasons, game=row["game"],
                                  has_raw=has_raw)
            if plan["unfixable"]:
                ledger.set_state(sid, "REJECTED",
                                 f"unfixable: {plan['unfixable']}")
                continue
            if not plan["steps"]:
                ledger.set_state(sid, "REJECTED",
                                 "no applicable fix for blocking reasons")
                continue
            # a reroute changes which keybind/title every later fix and
            # re-validation must use — apply it to the ledger FIRST
            reroute = next((p for f, p in plan["steps"]
                            if f == "FIX_REROUTE_GAME"), None)
            game = row["game"]
            if reroute and reroute.get("actual") in C.GAMES:
                game = reroute["actual"]
                ledger.update(sid, game=game)
            out = fix.apply_fixes(work, plan, game=game,
                                  dossier_dir=cfg.dossiers / sid,
                                  split_root=cfg.work)
            if out["error"]:
                # partially-applied plan: never re-run it blind — go back
                # through validation to re-derive from the current copy
                ledger.set_state(sid, "REVALIDATING",
                                 f"fix failed: {out['error']}"[:300])
                continue
            if out["children"] is not None and \
                    not out["children"]["segments"]:
                # no ≥70 s segment survived the cut — §5: reject
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
                continue
            ledger.set_state(sid, "REVALIDATING", "fixes applied")
        # re-validate everything the fixes touched (full Phase II re-run)
        revalidate = [s for s in sids + children_created
                      if (row := ledger.get(s))
                      and row["state"] in ("REVALIDATING", "INGESTED")]
        _validate_phase(cfg, ledger, revalidate, alerts, workers=workers)
    # anything still FIX_QUEUED after the bounded loop → reject w/ residuals
    for sid in sids + children_created:
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
            has_raw = (cfg.work / sid / "raw" / "inputs.jsonl").exists()
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
    all_sids = sids + kids
    dstats = _deliver_phase(cfg, ledger, all_sids, alerts,
                            dest_prefix=dest_prefix)

    rejected, labels, fixed = 0, [], 0
    for sid in all_sids:
        row = ledger.get(sid)
        if not row:
            continue
        if row["state"] == "REJECTED":
            rejected += 1
            deliver.finalize_rejected(cfg, ledger, sid)
            try:
                blocking = [x["code"] for x in
                            json.loads(row["reasons_json"] or "[]")
                            if x.get("blocking")]
            except json.JSONDecodeError:
                blocking = []
            if blocking:
                lbl = reports.reason_label(blocking[0])
                if lbl not in labels:
                    labels.append(lbl)
        elif row["state"] == "DELIVERED" and row["fix_attempts"] > 0:
            fixed += 1

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
    now_ist = now_ist or datetime.now(C.IST)
    if now_ist.hour < C.DAILY_REPORT_HOUR_IST:
        return False
    day = now_ist.strftime("%Y-%m-%d")
    marker = cfg.reports_dir / day / ".sent"
    if marker.exists():
        return False
    # the reporting window is the TRAILING 24h ending at send time — a
    # calendar-day window sent at 14:00 would permanently drop everything
    # delivered 14:00-24:00 from every report (review finding #15)
    hi_dt = now_ist.astimezone(timezone.utc)
    lo = (hi_dt - timedelta(hours=24)).isoformat(timespec="seconds")
    hi = hi_dt.isoformat(timespec="seconds")
    row = ledger.db.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(duration_delivered_s),0) s "
        "FROM sessions WHERE state='DELIVERED' AND delivered_at>=? AND "
        "delivered_at<?", (lo, hi)).fetchone()
    rej_rows = ledger.db.execute(
        "SELECT reasons_json FROM sessions WHERE state='REJECTED' AND "
        "updated_at>=? AND updated_at<?", (lo, hi)).fetchall()
    counts: dict[str, int] = {}
    for r in rej_rows:
        try:
            blocking = [x["code"] for x in
                        json.loads(r["reasons_json"] or "[]")
                        if x.get("blocking")]
        except json.JSONDecodeError:
            blocking = []
        if blocking:
            lbl = reports.reason_label(blocking[0], daily=True)
            counts[lbl] = counts.get(lbl, 0) + 1
    dups = ledger.db.execute(
        "SELECT COUNT(*) n FROM sessions WHERE state='REJECTED' AND "
        "updated_at>=? AND updated_at<? AND reasons_json LIKE "
        "'%INT_DUP_CROSS%'", (lo, hi)).fetchone()["n"]
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
        reject_counts=sorted(counts.items(), key=lambda kv: -kv[1]),
        integrity_lines=([f"{dups} cross-player duplicate"
                          f"{'s' if dups > 1 else ''} (kept earlier upload)"]
                         if dups else []))
    csv_path, _md = reports.write_payment_sheet(cfg, ledger, now_ist,
                                                bounds=(lo, hi))
    msg = reports.build_daily_message(d, p)
    try:
        telegram.send_message(cfg, msg)
    except telegram.TelegramError as e:
        print(f"[daily-report-undelivered] {e}", file=sys.stderr)
        return False
    # marker right after the MESSAGE lands: a sheet-send failure (or a kill
    # between the two sends) must not re-send the whole report next run
    # (review-r1 #20/#26); the sheet also sits on disk for the GCS sync
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    try:
        telegram.send_document(cfg, csv_path, caption="payment sheet")
    except telegram.TelegramError as e:
        print(f"[daily-sheet-undelivered] {e} — sheet remains at "
              f"{csv_path}", file=sys.stderr)
    return True


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
    for b in ledger.open_batches():
        try:
            sids = (json.loads(b["summary_json"] or "{}")
                    .get("sessions")) or []
        except json.JSONDecodeError:
            sids = []
        for s in sids:
            if s in state and s not in assigned:
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
        sts = {state[s] for s in b.sids}
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
        dl = Ledger(cfg.ledger_path)
        try:
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
            dl.close()
            q_dv.put(None)

    def u_process(ul: Ledger, b: _Batch) -> None:
        t0 = time.monotonic()
        dstats = _deliver_phase(cfg, ul, b.sids, alerts,
                                dest_prefix=dest_prefix)
        b.up_s = time.monotonic() - t0
        rejected, labels, fixed = 0, [], 0
        for sid in b.sids:
            row = ul.get(sid)
            if not row:
                continue
            if row["state"] == "REJECTED":
                rejected += 1
                deliver.finalize_rejected(cfg, ul, sid)
                try:
                    blocking = [x["code"] for x in
                                json.loads(row["reasons_json"] or "[]")
                                if x.get("blocking")]
                except json.JSONDecodeError:
                    blocking = []
                if blocking:
                    lbl = reports.reason_label(blocking[0])
                    if lbl not in labels:
                        labels.append(lbl)
            elif row["state"] == "DELIVERED" and row["fix_attempts"] > 0:
                fixed += 1
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
        stats = reports.BatchStats(
            batch_no=b.no, finished_ist=datetime.now(C.IST),
            duration_min=max(round((datetime.now(timezone.utc) - b.t0_utc)
                                   .total_seconds() / 60), 1),
            delivered=dstats["delivered"],
            total=len([s for s in b.sids if ul.get(s)]),
            auto_fixed=fixed, rejected=rejected, reject_labels=labels,
            hours_delta=dstats["hours"],
            hours_kamla=hours["kamla"], hours_ow=hours["outer_wilds"],
            pending=counts.get("DISCOVERED", 0),
            incomplete=len(ul.incomplete_list()),
            ok=dstats["upload_failures"] == 0,
            on_fallback=b.on_fallback)
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
        ul = Ledger(cfg.ledger_path)
        try:
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
        finally:
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
        b.sids = b.sids + kids
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
            # sync mirroring a stale backup (§6)
            if send_telegram:
                send_daily_report_if_due(cfg, ledger)
            ledger.backup_daily(cfg.backups, keep=C.LEDGER_BACKUP_KEEP)
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
        uthr.join(timeout=600)
        dthr.join(timeout=600)
        # D may have queued one more batch mid-shutdown (it was inside a
        # download when stop fired) — sweep again now that it has exited
        try:
            while True:
                leftover = q_dv.get_nowait()
                if leftover is not None:
                    _release(leftover)
        except queue.Empty:
            pass
        if uthr.is_alive() or dthr.is_alive():
            # daemon threads die with the process; the run lock is released
            # by run()'s finally — next tick starts clean (review-r1 #6)
            _alert(cfg, "driver thread failed to stop within 10 min — "
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


def _sweep_terminal_work(cfg: C.Config, ledger: Ledger) -> None:
    """A kill between the DELIVERED commit and the local wipe leaks work/,
    -analysis/ and stage dirs forever — resumed runs skip DELIVERED
    sessions, so nothing ever reclaimed them (review-r1 #22). REJECTED is
    deliberately excluded: finalize_rejected owns that wipe."""
    if cfg.work.exists():
        for p in cfg.work.iterdir():
            if not p.is_dir():
                continue
            sid = p.name[:-len("-analysis")] \
                if p.name.endswith("-analysis") else p.name
            row = ledger.get(sid)
            if row and row["state"] in ("DELIVERED", "SPLIT", "DUPLICATE"):
                shutil.rmtree(p, ignore_errors=True)
    if cfg.stage.exists():
        for pattern in ("*/*/*", "*/*/*/*"):
            for sdir in cfg.stage.glob(pattern):
                if not sdir.is_dir():
                    continue
                row = ledger.get(sdir.name)
                if row and row["state"] == "DELIVERED":
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
    row = ledger.db.execute(
        "SELECT COALESCE(SUM(bytes),0) b FROM sessions "
        "WHERE state='DELIVERED' AND delivered_at>=?", (lo,)).fetchone()
    approx_gb = row["b"] * 1.25 / (1024 ** 3)
    if approx_gb > 600:
        _alert(cfg, f"Drive upload ceiling: ~{approx_gb:.0f} GB delivered "
                    f"in the last 24 h — the 750 GB/day SA cap is close "
                    f"(§5); consider a second delivery SA", alerts)


def run(cfg: C.Config, *, max_batches: int = 50,
        dest_prefix: str = C.VENDOR, send_telegram: bool = True) -> int:
    if not acquire_lock(cfg):
        print("run lock held — exiting")
        return 0
    caff = None
    alerts: list[str] = []
    _reset_vlm_run_state()                 # every run restarts at rung 0
    try:
        cfg.ensure_dirs()
        ledger = Ledger(cfg.ledger_path)
        ledger.backup_daily(cfg.backups, keep=C.LEDGER_BACKUP_KEEP)
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
        except RuntimeError as e:
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
            _sweep_terminal_work(cfg, ledger)
            _upload_ceiling_alert(cfg, ledger, alerts)
            held = ledger.counts_by_state().get("HOLD_VLM", 0)
            if held:
                _alert(cfg, f"{held} session(s) still HOLD_VLM at end of "
                            f"run — VLM sweep repeatedly failing (§13)",
                       alerts)
            if send_telegram:
                send_daily_report_if_due(cfg, ledger)
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
        _sweep_terminal_work(cfg, ledger)
        _upload_ceiling_alert(cfg, ledger, alerts)
        held = ledger.counts_by_state().get("HOLD_VLM", 0)
        if held:
            _alert(cfg, f"{held} session(s) still HOLD_VLM at end of run — "
                        f"VLM sweep repeatedly failing (§13)", alerts)
        if send_telegram:
            send_daily_report_if_due(cfg, ledger)
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
        return run(cfg)
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
        print(f"daily report sent: {sent}")
        return 0
    print(f"unknown command {cmd!r} (run | status | daily-report)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
