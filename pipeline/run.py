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
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as C
from . import deliver, fix, ingest, pace, reports, telegram
from .ledger import Ledger

RESUMABLE = ("DOWNLOADING", "INGESTED", "VALIDATING", "FIX_QUEUED",
             "FIXING", "REVALIDATING", "READY", "PACKAGED", "UPLOADED")


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
    from .validate import validate_session
    try:
        res = validate_session(
            Path(args["work_dir"]), Path(args["dossier_dir"]),
            payload=args["payload"], expected_game=args["expected_game"],
            gemini_key=args["gemini_key"], gemini_model=args["gemini_model"])
        return {"sid": args["sid"], "bin": res.bin,
                "hold_vlm": res.hold_vlm, "reasons": res.reasons,
                "advisories": res.advisories,
                "engine_verdict": res.engine_verdict}
    except Exception as e:
        import traceback
        return {"sid": args["sid"], "error": f"{type(e).__name__}: {e}",
                "tb": traceback.format_exc()[-1500:]}


# ------------------------------------------------------------ run phases

def _alert(cfg: C.Config, text: str, sent: list[str]) -> None:
    """Telegram alert; failures are logged, never fatal. Deduped per run."""
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
                     "gemini_model": cfg.gemini_model})
    if not jobs:
        return
    results: list[dict] = []
    if workers > 1 and len(jobs) > 1:
        try:
            with concurrent.futures.ProcessPoolExecutor(
                    max_workers=workers) as ex:
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
                            max_workers=1) as ex1:
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
        if "error" in r:
            ledger.set_state(sid, "QUARANTINED",
                             f"validation crashed: {r['error']}")
            _alert(cfg, f"validation crashed on {sid}: {r['error']}", alerts)
            continue
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


def _fix_phase(cfg, ledger, sids, alerts, *, workers: int) -> list[str]:
    """Fix FIX_QUEUED sessions (≤2 attempts each, R2); returns new child
    session ids created by splits."""
    children_created: list[str] = []
    for _pass in range(C.FIX_RETRIES):
        todo = [s for s in sids
                if (row := ledger.get(s))
                and row["state"] in ("FIX_QUEUED", "FIXING")]
        if not todo:
            break
        for sid in todo:
            row = ledger.get(sid)
            if row["state"] == "FIXING":
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
    batch_no = ledger.start_batch()
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
        ok=dstats["upload_failures"] == 0)
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
        telegram.send_document(cfg, csv_path, caption="payment sheet")
    except telegram.TelegramError as e:
        print(f"[daily-report-undelivered] {e}", file=sys.stderr)
        return False
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return True


def run(cfg: C.Config, *, max_batches: int = 50,
        dest_prefix: str = C.VENDOR, send_telegram: bool = True) -> int:
    if not acquire_lock(cfg):
        print("run lock held — exiting")
        return 0
    caff = None
    alerts: list[str] = []
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
        except RuntimeError as e:
            _alert(cfg, f"Drive scan failed: {e}", alerts)

        attempted: set[str] = set()
        for _ in range(max_batches):
            # each session gets at most ONE pass per run — a stuck
            # PACKAGED/HOLD_VLM set must not spin the loop for hours while
            # holding the run lock (review-2 #9); the next launchd tick is
            # the retry cadence (§13)
            resume = [r["session_id"] for r in ledger.by_state(*RESUMABLE)
                      if r["session_id"] not in attempted]
            batch = resume or [s for s in ingest.next_batch(ledger)
                               if s not in attempted]
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
