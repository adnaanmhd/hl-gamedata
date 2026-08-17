"""Continuous driver (PIPELINE_CONTINUOUS_DESIGN.md; Adnaan rulings
2026-08-17) — `python -m pipeline run-continuous`.

Always-on replacement for the batch overlap driver: per-session flow, 5-min
Drive polls, adaptive validation-pool autoscale with Gemini-429 backpressure,
per-session fix scheduling (FIX_QUEUED re-enters immediately), HOLD_VLM
retried every 30 min forever, and a 3-h Telegram digest emitted from inside
the driver. The batch driver (run.py) stays dormant-intact as rollback.

THE LEDGER IS THE QUEUE. Dispatchers re-derive all work from
`ledger.by_state(...)`; the only in-memory state is the ownership set
(single-owner-per-session — the precondition every mutating path in
fix/deliver assumes) and per-session cooldowns. Both are ALLOWED to be lost
on kill -9: restart rebuilds ownership empty and retries cooled sessions
once immediately, which is exactly "resume from the ledger".

Reuses the batch driver's per-session machinery verbatim (imported, not
copied): `_validate_worker`, `_recover_split`, `_discard_split_artifacts`,
the startup/hourly sweeps, and the daily-report triggers with their
stamps→anchor→marker ordering.
"""
from __future__ import annotations

import concurrent.futures
import json
import multiprocessing
import os
import shutil
import signal
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as C
from . import deliver, fix, ingest, reports, telegram
from .ledger import Ledger
from . import run as runmod

# States with local media (or a claim on imminent local media): the ~40
# session cap (ruling 2) counts these from the ledger, so it is resume-exact.
LOCAL_STATES = ("DOWNLOADING", "INGESTED", "VALIDATING", "FIX_QUEUED",
                "FIXING", "REVALIDATING", "READY", "PACKAGED", "UPLOADED",
                "HOLD_VLM")
# V-domain states for queue-depth (autoscale input): work the pool has or
# will have. HOLD_VLM is excluded — held sessions wait on a clock, not a slot.
V_DEPTH_STATES = ("INGESTED", "VALIDATING", "FIX_QUEUED", "FIXING",
                  "REVALIDATING")

# Test seams for _validate_one: the real path runs _WORKER_FN in a fresh
# single-job spawn subprocess (monkeypatches cannot cross that boundary);
# tests set _POOL_DISABLED=True to run the (possibly faked) worker inline.
_WORKER_FN = runmod._validate_worker
_POOL_DISABLED = False


# ------------------------------------------------------------ primitives

class ResizableGate:
    """Bounds concurrent session runners. Autoscale = set_target(); raising
    wakes waiters, lowering lets active drain below — running sessions are
    never interrupted (why validation uses single-job pools: there is no
    ProcessPoolExecutor to resize)."""

    def __init__(self, target: int):
        self._cond = threading.Condition()
        self._target = max(1, int(target))
        self._active = 0

    @property
    def target(self) -> int:
        with self._cond:
            return self._target

    @property
    def active(self) -> int:
        with self._cond:
            return self._active

    def set_target(self, n: int) -> None:
        with self._cond:
            self._target = max(1, int(n))
            self._cond.notify_all()

    def acquire(self, stop: threading.Event, poll_s: float = 0.5) -> bool:
        """Block until a slot is free; False when `stop` fires first."""
        with self._cond:
            while self._active >= self._target:
                if stop.is_set():
                    return False
                self._cond.wait(poll_s)
            if stop.is_set():
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._cond:
            self._active -= 1
            self._cond.notify_all()


class Ownership:
    """Single-owner-per-session. claim() is the only admission ticket to any
    lane; release() on lane exit. In-memory only — see module docstring."""

    def __init__(self):
        self._lock = threading.Lock()
        self._owned: set[str] = set()

    def claim(self, sid: str) -> bool:
        with self._lock:
            if sid in self._owned:
                return False
            self._owned.add(sid)
            return True

    def release(self, sid: str) -> None:
        with self._lock:
            self._owned.discard(sid)

    def snapshot(self) -> set[str]:
        with self._lock:
            return set(self._owned)

    def any(self) -> bool:
        with self._lock:
            return bool(self._owned)


class Cooldowns:
    """Per-session retry pacing — the continuous analogue of the batch
    driver's once-per-run `attempted` set. monotonic-clock based; injectable
    for tests."""

    def __init__(self, mono_fn=time.monotonic):
        self._lock = threading.Lock()
        self._until: dict[str, float] = {}
        self._mono = mono_fn

    def set(self, sid: str, seconds: float) -> None:
        with self._lock:
            self._until[sid] = self._mono() + seconds

    def ready(self, sid: str) -> bool:
        with self._lock:
            t = self._until.get(sid)
            if t is None:
                return True
            if self._mono() >= t:
                del self._until[sid]
                return True
            return False

    def blocked(self) -> set[str]:
        with self._lock:
            now = self._mono()
            expired = [s for s, t in self._until.items() if now >= t]
            for s in expired:
                del self._until[s]
            return set(self._until)


class AlertBook:
    """⚠️ alerts with TTL dedup: a forever-process must RE-raise persisting
    conditions (the batch driver's per-run list would silence a disk-low
    alert for the service's whole life) and must not grow unboundedly."""

    def __init__(self, cfg: C.Config, ttl_s: float, mono_fn=time.monotonic):
        self.cfg = cfg
        self._ttl = ttl_s
        self._mono = mono_fn
        self._lock = threading.Lock()
        self._sent: dict[str, float] = {}

    def alert(self, text: str) -> None:
        with self._lock:
            now = self._mono()
            for k in [k for k, t in self._sent.items()
                      if now - t > self._ttl]:
                del self._sent[k]
            last = self._sent.get(text)
            if last is not None and now - last < self._ttl:
                return
            self._sent[text] = now
        try:
            telegram.send_message(self.cfg, f"⚠️ {text}")
        except telegram.TelegramError as e:
            print(f"[alert-undelivered] {text} ({e})", file=sys.stderr)


def autoscale_decision(*, target: int, active: int, queue_depth: int,
                       cpu_pct: float | None, p429_per_min: float,
                       rung_climb: bool, cpu_crit_streak: bool,
                       lo: int, hi: int) -> tuple[int, str]:
    """Pure control law (design §4) — unit-testable without a clock.
    Returns (new_target, reason). Rules in priority order; band-clamped."""
    def clamp(n: int) -> int:
        return max(lo, min(hi, n))
    if p429_per_min >= C.CONT_BACKPRESSURE_429_PER_MIN or rung_climb:
        why = (f"429 backpressure ({p429_per_min:.1f}/min"
               + (", rung climb" if rung_climb else "") + ")")
        return clamp(target - C.CONT_STEP_DOWN), why
    if cpu_pct is not None and cpu_pct > C.CONT_CPU_CRIT and cpu_crit_streak:
        return clamp(target - 2), f"cpu {cpu_pct:.0f}% sustained"
    if (cpu_pct is not None and cpu_pct < C.CONT_CPU_HIGH
            and queue_depth > active):
        return clamp(target + C.CONT_STEP_UP), \
            f"queue {queue_depth} > active {active}, cpu {cpu_pct:.0f}%"
    return clamp(target), "hold"


# --------------------------------------------------------------- driver

@dataclass
class _Clocks:
    mono: object = time.monotonic
    now: object = time.time                       # epoch (pressure file ts)
    utcnow: object = None                         # -> datetime (aware)

    def __post_init__(self):
        if self.utcnow is None:
            self.utcnow = lambda: datetime.now(timezone.utc)


class ContinuousDriver:
    def __init__(self, cfg: C.Config, *, dest_prefix: str = C.VENDOR,
                 send_telegram: bool = True, clocks: _Clocks | None = None):
        self.cfg = cfg
        self.dest_prefix = dest_prefix
        self.send_telegram = send_telegram
        self.clk = clocks or _Clocks()
        self.stop = threading.Event()
        self.own = Ownership()
        self.cool = Cooldowns(self.clk.mono)
        self.gate = ResizableGate(C.CONT_POOL_MIN)
        self.alerts = AlertBook(cfg, C.CONT_ALERT_DEDUP_MIN * 60,
                                self.clk.mono)
        self.intake_lock = threading.Lock()       # scan pass vs D pick+claim
        self.pressure_path = cfg.logs / "vlm-pressure.jsonl"
        # R23 rung, continuous scope: sticky until a quiet period (§4)
        self._rung_lock = threading.Lock()
        self._rung = 0
        self._last_pressure_ep = 0.0              # epoch of newest 429 event
        self._climb_ep = 0.0                      # epoch of last rung climb
        self._pressure_pos = 0                    # file read offset
        self._pressure_recent: list[float] = []   # event epochs in window
        self._cpu_prev: tuple[int, int] | None = None
        self._cpu_crit_prev = False
        self._counts: dict[str, int] = {}         # H-thread state snapshot
        self._scan_passes = 0
        self.threads: list[threading.Thread] = []
        self.runner_pool: concurrent.futures.ThreadPoolExecutor | None = None

    # ------------------------------------------------------------- rung
    def current_rung(self) -> int:
        with self._rung_lock:
            return self._rung

    def absorb_rung(self, r: int) -> None:
        with self._rung_lock:
            if r > self._rung:
                self._rung = r
                self._climb_ep = self.clk.now()
                print(f"[rung] climbed to {r} (sticky until "
                      f"{C.CONT_RUNG_QUIET_RESET_MIN} quiet min)")

    def _maybe_reset_rung(self) -> None:
        with self._rung_lock:
            if self._rung == 0:
                return
            quiet_since = max(self._last_pressure_ep, self._climb_ep)
            if (self.clk.now() - quiet_since
                    > C.CONT_RUNG_QUIET_RESET_MIN * 60):
                print(f"[rung] quiet {C.CONT_RUNG_QUIET_RESET_MIN} min — "
                      f"reset {self._rung} -> 0 (model of record restored)")
                self._rung = 0

    # -------------------------------------------------------- S: scanner
    def _scan_thread(self) -> None:
        led = Ledger(self.cfg.ledger_path)
        try:
            while not self.stop.is_set():
                try:
                    with self.intake_lock:
                        res = ingest.scan(self.cfg, led)
                    for path, why in res.quarantined:
                        print(f"[quarantined] {path}: {why}")
                    for f in res.integrity_flags:
                        print(f"[integrity] {f}")
                    for sid in res.dup_cross:
                        try:
                            deliver.finalize_rejected(self.cfg, led, sid)
                        except Exception as e:
                            print(f"[finalize-failed] {sid}: {e}",
                                  file=sys.stderr)
                except Exception as e:
                    # same degradation as run(): scan trouble alerts, never
                    # stops the backlog draining
                    self.alerts.alert(f"Drive scan failed: {e}")
                self._scan_passes += 1
                self.stop.wait(C.CONT_SCAN_INTERVAL_S)
        finally:
            led.close()

    # ------------------------------------------------------- D: download
    def _local_count(self, led: Ledger) -> int:
        counts = led.counts_by_state()
        return sum(counts.get(s, 0) for s in LOCAL_STATES)

    def _pick_download(self, led: Ledger) -> str | None:
        if deliver.disk_free_gb(self.cfg.home) < C.DISK_LOW_WATER_GB:
            self.alerts.alert(f"disk under {C.DISK_LOW_WATER_GB} GB free — "
                              f"downloads paused (F7)")
            return None
        with self.intake_lock:
            exclude = self.own.snapshot() | self.cool.blocked()
            # kill-resume first: DOWNLOADING rows are already inside the
            # media cap (LOCAL_STATES counts them) and rclone re-downloads
            # idempotently — without this they orphan forever (r-loop 1)
            for r in led.by_state("DOWNLOADING"):
                sid = r["session_id"]
                if sid not in exclude and self.own.claim(sid):
                    return sid
            if self._local_count(led) >= C.CONT_MEDIA_CAP_SESSIONS:
                return None                # cap gates NEW intake only
            sids = ingest.next_batch(led, size=1, exclude=exclude)
            if not sids:
                return None
            if self.own.claim(sids[0]):
                # commit DOWNLOADING INSIDE the intake lock: a scan pass
                # must never see this sid as still-clobberable DISCOVERED
                # while D proceeds (cross-dup un-pick race, r-loop 1)
                led.set_state(sids[0], "DOWNLOADING", "claimed by D")
                return sids[0]
            return None

    def _download_one(self, led: Ledger, sid: str) -> None:
        """Per-sid body of run._download_phase with cooldowns replacing
        'retry next run' (routing preserved verbatim)."""
        row = led.get(sid)
        if row is None or row["state"] not in ("DISCOVERED", "DOWNLOADING"):
            return
        try:
            ingest.download(self.cfg, led, sid)
        except ingest.DownloadError as e:
            msg = str(e)
            kind = getattr(e, "kind", "transient")
            if kind == "zip_incomplete":
                led.set_state(sid, "DISCOVERED",
                              f"zip payload incomplete/unreadable — "
                              f"retrying: {msg}"[:300])
                led.incomplete_seen(row["drive_path"],
                                    [ingest.ZIP_PARTS_MARKER])
                self.cool.set(sid, C.CONT_DOWNLOAD_RETRY_MIN * 60)
            elif kind == "quarantine":
                led.set_state(sid, "QUARANTINED", msg[:300])
                self.alerts.alert(f"download quarantined {sid}: {e}")
            else:
                led.set_state(sid, "DISCOVERED",
                              f"download failed — retrying: {msg}"[:300])
                self.cool.set(sid, C.CONT_DOWNLOAD_RETRY_MIN * 60)
                self.alerts.alert(f"download failed for {sid} "
                                  f"(will retry): {e}")
        except (OSError, sqlite3.OperationalError) as e:
            led.set_state(sid, "DISCOVERED",
                          f"download failed (host-level) — retrying: "
                          f"{type(e).__name__}: {e}"[:300])
            self.cool.set(sid, C.CONT_DOWNLOAD_RETRY_MIN * 60)
            self.alerts.alert(f"download hit host-level error for {sid} "
                              f"(will retry): {type(e).__name__}: {e}")
        except Exception as e:
            led.set_state(sid, "QUARANTINED",
                          f"download crashed: {type(e).__name__}: "
                          f"{e}"[:300])
            self.alerts.alert(f"download crashed for {sid}: "
                              f"{type(e).__name__}: {e}")

    def _lane_loop(self, name: str, body) -> None:
        """Run body(led) per iteration under a guard that can NEVER kill
        the lane: an always-on process turns a dead thread into permanent
        loss of that lane while H keeps digesting healthily (r-loop 1
        blocker — the batch driver's 30-min process exit was the backstop
        this process no longer has). On any escape: alert (TTL-deduped),
        reopen the ledger connection (an OperationalError may have a
        poisoned transaction behind it), pause one idle interval, go on."""
        led = None
        try:
            while not self.stop.is_set():
                try:
                    if led is None:
                        led = Ledger(self.cfg.ledger_path)
                    body(led)
                except Exception as e:
                    self.alerts.alert(
                        f"{name} lane iteration failed (lane continues): "
                        f"{type(e).__name__}: {e}")
                    if led is not None:
                        try:
                            led.close()
                        except Exception:
                            pass
                        led = None
                    self.stop.wait(C.CONT_DISPATCH_IDLE_S)
        finally:
            if led is not None:
                led.close()

    def _download_thread(self) -> None:
        def body(led: Ledger) -> None:
            sid = self._pick_download(led)
            if sid is None:
                self.stop.wait(C.CONT_DISPATCH_IDLE_S)
                return
            try:
                self._download_one(led, sid)
            finally:
                self.own.release(sid)
        self._lane_loop("download", body)

    # ------------------------------------------------- V: session runners
    def _pick_v(self, led: Ledger) -> str | None:
        """Priority: (1) FIX_QUEUED — immediacy ruling, includes U's
        gate-fail hand-backs; (2) crash-resume triage states; (3) HOLD_VLM
        whose 30-min cooldown expired — BEFORE fresh intake, or a steady
        INGESTED stream starves held sessions indefinitely (ruling 6 /
        review-r4 #9, re-found by r-loop 1; the 30-min cooldown bounds how
        much dispatch HOLD can consume); (4) fresh INGESTED, FIFO."""
        for states in (("FIX_QUEUED",),
                       ("FIXING", "REVALIDATING", "VALIDATING"),
                       ("HOLD_VLM",),
                       ("INGESTED",)):
            for r in led.by_state(*states):
                sid = r["session_id"]
                if not self.cool.ready(sid):
                    continue
                if self.own.claim(sid):
                    return sid
        return None

    def _v_dispatcher(self) -> None:
        def body(led: Ledger) -> None:
            if not self.gate.acquire(self.stop):
                return                             # stopping
            handed_off = False
            sid = None
            try:
                sid = self._pick_v(led)
                if sid is None:
                    self.stop.wait(C.CONT_DISPATCH_IDLE_S)
                    return
                self.runner_pool.submit(self._session_runner, sid)
                handed_off = True                  # runner owns slot+claim
            finally:
                if not handed_off:
                    self.gate.release()
                    if sid is not None:
                        self.own.release(sid)
        self._lane_loop("validation dispatcher", body)

    def _session_runner(self, sid: str) -> None:
        """Drive ONE session through the whole V domain, holding its gate
        slot until it leaves — validate → fix → revalidate cycles happen
        here, immediately, which is what makes the parked-fix-tail class
        structurally impossible."""
        led = None
        try:
            # ctor INSIDE the try: a Ledger() failure here previously
            # leaked the gate slot + ownership forever (the pool swallows
            # the exception — r-loop 1)
            led = Ledger(self.cfg.ledger_path)
            while not self.stop.is_set():
                row = led.get(sid)
                if row is None:
                    return
                st = row["state"]
                if st == "FIXING":
                    if not self._fixing_triage(led, sid, row):
                        return
                    continue
                if st == "FIX_QUEUED":
                    if not self._fix_one(led, sid):
                        return
                    continue
                if st in ("INGESTED", "VALIDATING", "REVALIDATING",
                          "HOLD_VLM"):
                    new = self._validate_one(led, sid, row)
                    if new == "HOLD_VLM":
                        self.cool.set(sid, C.CONT_HOLD_RETRY_MIN * 60)
                        return
                    if new == "FIX_QUEUED":
                        continue
                    return          # READY / REJECTED / QUARANTINED
                return              # left the V domain some other way
        except Exception as e:
            self.alerts.alert(f"session runner crashed on {sid}: "
                              f"{type(e).__name__}: {e}")
            # crash cooldown: the state is unchanged (priority-2 triage
            # class), so without this the dispatcher re-claims instantly
            # and a persistent pre-subprocess fault hot-spins events
            self.cool.set(sid, C.CONT_RUNNER_CRASH_RETRY_MIN * 60)
        finally:
            if led is not None:
                led.close()
            self.own.release(sid)
            self.gate.release()

    def _drop_shift_entry(self, sid: str) -> None:
        """Design §7: terminal wipe drops the sid's entry in the shared
        work-root translation_report.json (DELIVERED/REJECTED entries are
        dropped by deliver.py's wipe sites; SPLIT parents here — children
        received their own copies at cut time via _propagate_shift_record)."""
        try:
            from .validate import _locked_report_remove
            _locked_report_remove(
                self.cfg.work / "translation_report.json", sid)
        except Exception as e:
            print(f"[shift-drop-failed] {sid}: {e}", file=sys.stderr)

    def _finalize_reject(self, led: Ledger, sid: str) -> None:
        """Per-session terminal hook (replaces the batch close-out sweep)."""
        try:
            deliver.finalize_rejected(self.cfg, led, sid)
        except Exception as e:
            print(f"[finalize-failed] {sid}: {e}", file=sys.stderr)

    def _validate_one(self, led: Ledger, sid: str, row) -> str | None:
        """One full Phase-II validation in a fresh single-job spawn
        subprocess (run._validate_worker unchanged). Fresh interpreter per
        session: native-crash isolation without wave retries, resizing
        without pool generations, no long-lived-worker staleness."""
        work = self.cfg.work / sid
        if not work.exists():
            led.set_state(sid, "QUARANTINED", "work copy missing")
            return "QUARANTINED"
        led.set_state(sid, "VALIDATING")
        job = {"sid": sid, "work_dir": str(work),
               "dossier_dir": str(self.cfg.dossiers / sid),
               "payload": ingest.sniff_payload(work),
               "expected_game": row["game"] or None,
               "gemini_key": self.cfg.gemini_key,
               "gemini_model": self.cfg.gemini_model,
               "vlm_rung": self.current_rung(),
               "pressure_path": str(self.pressure_path)}
        if _POOL_DISABLED:
            res = _WORKER_FN(job)          # test hook: in-process worker
        else:
            ctx = multiprocessing.get_context("spawn")
            try:
                with concurrent.futures.ProcessPoolExecutor(
                        max_workers=1, mp_context=ctx) as ex:
                    res = list(ex.map(_WORKER_FN, [job]))[0]
            except concurrent.futures.process.BrokenProcessPool:
                res = {"sid": sid,
                       "error": "validation worker died (native crash "
                                "decoding this session)"}
        # TRUE climbs only: the worker echoes max(injected, climbed)
        # (run.py:129), so comparing against the CURRENT driver rung let a
        # stale in-flight job resurrect the rung right after a quiet-period
        # reset and re-stamp _climb_ep forever (r-loop 1)
        if int(res.get("vlm_rung", 0)) > job["vlm_rung"]:
            self.absorb_rung(int(res["vlm_rung"]))
        if "error" in res:
            led.set_state(sid, "QUARANTINED",
                          f"validation crashed: {res['error']}")
            self.alerts.alert(f"validation crashed on {sid}: "
                              f"{res['error']}")
            return "QUARANTINED"
        led.set_reasons(sid, res["reasons"], res["bin"])
        if res["hold_vlm"]:
            led.set_state(sid, "HOLD_VLM",
                          "VLM sweep unfinished — never pass "
                          "unlooked-at (F5)")
            return "HOLD_VLM"
        if res["bin"] == 1:
            led.set_state(sid, "READY")
            return "READY"
        if res["bin"] == 2:
            led.set_state(sid, "FIX_QUEUED")
            return "FIX_QUEUED"
        led.set_state(sid, "REJECTED",
                      ",".join(x["code"] for x in res["reasons"]
                               if x["blocking"]))
        self._finalize_reject(led, sid)
        return "REJECTED"

    def _fixing_triage(self, led: Ledger, sid: str, row) -> bool:
        """Mid-fix crash triage — run._recover_split semantics verbatim
        (adopt a COMPLETE cut before any re-verdict; else REVALIDATING).
        Returns True to continue the runner loop, False on domain exit."""
        done, kid_ids = runmod._recover_split(self.cfg, led, sid, row)
        if done:
            try:
                fix._propagate_shift_record(
                    self.cfg.work / sid,
                    [self.cfg.work / k for k in kid_ids
                     if (self.cfg.work / k).is_dir()])
            except Exception as e:
                print(f"[shift-propagate-failed] {sid}: {e}",
                      file=sys.stderr)
            led.set_state(sid, "SPLIT",
                          f"{len(kid_ids)} segments (completed after "
                          f"mid-split crash)")
            shutil.rmtree(self.cfg.work / sid, ignore_errors=True)
            (self.cfg.work / f"{sid}.split-manifest.json").unlink(
                missing_ok=True)          # only after the SPLIT commit
            self._drop_shift_entry(sid)   # children got their own copies
            return False                  # children are INGESTED rows now
        led.set_state(sid, "REVALIDATING",
                      "mid-fix crash — re-deriving fix plan")
        return True

    def _fix_one(self, led: Ledger, sid: str) -> bool:
        """ONE fix attempt — the per-sid body of run._fix_phase without the
        batch pass loop. Budget accounting preserved verbatim: charge only
        at the FIXING transition; 2 attempts then reject (R2). Returns True
        when the runner should continue (REVALIDATING), False on exit."""
        row = led.get(sid)
        if row is None or row["state"] != "FIX_QUEUED":
            return False
        if row["fix_attempts"] >= C.FIX_RETRIES:
            led.set_state(sid, "REJECTED", "fix retries exhausted (R2)")
            self._finalize_reject(led, sid)
            return False
        led.update(sid, fix_attempts=row["fix_attempts"] + 1)
        led.set_state(sid, "FIXING", f"attempt {row['fix_attempts'] + 1}")
        reasons = json.loads(row["reasons_json"] or "[]")
        work = self.cfg.work / sid
        has_raw = (work / "raw" / "inputs.jsonl").exists()
        plan = fix.plan_fixes(reasons, game=row["game"], has_raw=has_raw)
        if plan["unfixable"]:
            runmod._discard_split_artifacts(self.cfg, led, sid)
            led.set_state(sid, "REJECTED", f"unfixable: {plan['unfixable']}")
            self._finalize_reject(led, sid)
            return False
        if not plan["steps"]:
            runmod._discard_split_artifacts(self.cfg, led, sid)
            led.set_state(sid, "REJECTED",
                          "no applicable fix for blocking reasons")
            self._finalize_reject(led, sid)
            return False
        reroute = next((p for f_, p in plan["steps"]
                        if f_ == "FIX_REROUTE_GAME"), None)
        game = row["game"]
        if reroute and reroute.get("actual") in C.GAMES:
            game = reroute["actual"]
            led.update(sid, game=game)
        out = fix.apply_fixes(work, plan, game=game,
                              dossier_dir=self.cfg.dossiers / sid,
                              split_root=self.cfg.work)
        if out["error"]:
            runmod._discard_split_artifacts(self.cfg, led, sid)
            led.set_state(sid, "REVALIDATING",
                          f"fix failed: {out['error']}"[:300])
            return True
        if out["children"] is not None and not out["children"]["segments"]:
            runmod._discard_split_artifacts(self.cfg, led, sid)
            led.set_state(sid, "REJECTED",
                          "split produced no >=70s segment "
                          f"(dropped {len(out['children']['dropped'])})")
            self._finalize_reject(led, sid)
            return False
        if out["children"] is not None:
            for seg in out["children"]["segments"]:
                if led.get(seg["id"]) is None:
                    led.insert_session(
                        session_id=seg["id"], game=game,
                        operator_email=row["operator_email"],
                        player_email=row["player_email"],
                        drive_path=row["drive_path"],
                        drive_ctime=row["drive_ctime"],
                        md5_video="", bytes_=0, state="INGESTED",
                        parent_id=sid,
                        detail=f"split segment {seg['t0']}-{seg['t1']}s")
                    led.update(seg["id"], duration_raw_s=seg["duration_s"])
            # children are plain INGESTED rows; the V dispatcher — the ONLY
            # claimant — picks each exactly once. No children_sink needed:
            # the double-membership class died with batches.
            led.set_state(sid, "SPLIT",
                          f"{len(out['children']['segments'])} segments"
                          + (f"; dropped {len(out['children']['dropped'])}"
                             if out["children"]["dropped"] else ""))
            shutil.rmtree(work, ignore_errors=True)
            (self.cfg.work / f"{sid}.split-manifest.json").unlink(
                missing_ok=True)
            self._drop_shift_entry(sid)   # children got their own copies
            return False
        led.set_state(sid, "REVALIDATING", "fixes applied")
        return True

    # --------------------------------------------------------- U: upload
    def _pick_upload(self, led: Ledger) -> str | None:
        for r in led.by_state("READY", "PACKAGED", "UPLOADED"):
            sid = r["session_id"]
            if not self.cool.ready(sid):
                continue
            if self.own.claim(sid):
                return sid
        return None

    def _deliver_one(self, led: Ledger, sid: str) -> None:
        """Per-sid body of run._deliver_phase; gate-fail hand-back goes to
        FIX_QUEUED which V picks IMMEDIATELY (priority 1) instead of next
        tick. Budget accounting preserved (no increment on requeue)."""
        from .validate import map_gate_failures
        row = led.get(sid)
        if not row or row["state"] not in ("READY", "PACKAGED", "UPLOADED"):
            return
        try:
            out = deliver.deliver_session(self.cfg, led, sid,
                                          dest_prefix=self.dest_prefix)
        except Exception as e:
            led.set_state(sid, "QUARANTINED",
                          f"delivery crashed: {type(e).__name__}: "
                          f"{e}"[:300])
            self.alerts.alert(f"delivery crashed for {sid}: "
                              f"{type(e).__name__}: {e}")
            return
        if out.status == "delivered":
            return
        if out.status == "failed_gate":
            r = led.get(sid)
            has_raw = (self.cfg.work / sid / "raw" / "inputs.jsonl").exists()
            reasons = map_gate_failures(out.gate_fails or [],
                                        has_raw=has_raw)
            if reasons:
                led.set_reasons(sid, reasons,
                                3 if any(not x["fixable"] for x in reasons
                                         if x["blocking"]) else 2)
            if r["fix_attempts"] >= C.FIX_RETRIES:
                led.set_state(sid, "REJECTED",
                              f"final gate: {out.detail}"[:300])
                self._finalize_reject(led, sid)
            else:
                led.set_state(sid, "FIX_QUEUED",
                              f"final gate: {out.detail}"[:300])
            return
        # upload failure: state untouched (deliver_session left it
        # READY/PACKAGED/UPLOADED); cooldown + alert, retried forever
        self.cool.set(sid, C.CONT_UPLOAD_RETRY_MIN * 60)
        self.alerts.alert(f"upload failed for {sid}: {out.detail}")

    def _upload_thread(self) -> None:
        def body(led: Ledger) -> None:
            sid = self._pick_upload(led)
            if sid is None:
                self.stop.wait(C.CONT_DISPATCH_IDLE_S)
                return
            try:
                self._deliver_one(led, sid)
            finally:
                self.own.release(sid)
        self._lane_loop("upload", body)

    # ------------------------------------------------- H: housekeeping
    def _cpu_pct(self) -> float | None:
        try:
            parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
            vals = [int(x) for x in parts]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals)
            prev, self._cpu_prev = self._cpu_prev, (idle, total)
            if prev is None or total <= prev[1]:
                return None
            dtotal = total - prev[1]
            return 100.0 * (1 - (idle - prev[0]) / dtotal) if dtotal else None
        except (OSError, ValueError, IndexError):
            try:
                return min(100.0, 100.0 * os.getloadavg()[0]
                           / (os.cpu_count() or 1))
            except OSError:
                return None

    def _read_pressure(self) -> None:
        """Incrementally ingest vlm-pressure.jsonl (429/5xx events appended
        by validation subprocesses); maintain the trailing window and the
        newest-event epoch. Truncate when huge — a momentary window
        undercount beats unbounded growth (writers keep appending via
        O_APPEND, so truncation never corrupts a line)."""
        try:
            size = self.pressure_path.stat().st_size
        except OSError:
            return
        if size < self._pressure_pos:
            self._pressure_pos = 0                 # truncated/rotated
        if size > self._pressure_pos:
            try:
                with open(self.pressure_path) as f:
                    f.seek(self._pressure_pos)
                    chunk = f.read()
                    self._pressure_pos = f.tell()
            except OSError:
                return
            for line in chunk.splitlines():
                try:
                    ev = json.loads(line)
                    ts = float(ev["ts"])
                except (json.JSONDecodeError, KeyError, TypeError,
                        ValueError):
                    continue
                if int(ev.get("status", 0)) == 429:
                    self._pressure_recent.append(ts)
                self._last_pressure_ep = max(self._last_pressure_ep, ts)
        cutoff = self.clk.now() - C.CONT_BACKPRESSURE_WINDOW_S
        self._pressure_recent = [t for t in self._pressure_recent
                                 if t >= cutoff]
        if size > 10 * 1024 * 1024:
            try:
                os.truncate(self.pressure_path, 0)
                self._pressure_pos = 0
            except OSError:
                pass

    def _autoscale_tick(self) -> None:
        self._read_pressure()
        self._maybe_reset_rung()
        cpu = self._cpu_pct()
        led_counts = self._counts
        depth = sum(led_counts.get(s, 0) for s in V_DEPTH_STATES)
        p429_per_min = (len(self._pressure_recent)
                        / (C.CONT_BACKPRESSURE_WINDOW_S / 60.0))
        climb = (self.clk.now() - self._climb_ep
                 < C.CONT_BACKPRESSURE_WINDOW_S) if self._climb_ep else False
        crit_now = cpu is not None and cpu > C.CONT_CPU_CRIT
        new, why = autoscale_decision(
            target=self.gate.target, active=self.gate.active,
            queue_depth=depth, cpu_pct=cpu, p429_per_min=p429_per_min,
            rung_climb=climb, cpu_crit_streak=self._cpu_crit_prev and crit_now,
            lo=C.CONT_POOL_MIN, hi=C.CONT_POOL_MAX)
        self._cpu_crit_prev = crit_now
        if new != self.gate.target:
            print(f"[autoscale] {self.gate.target} -> {new} ({why}; "
                  f"cpu {cpu if cpu is None else round(cpu)}%, "
                  f"depth {depth}, 429s/min {p429_per_min:.2f})")
            self.gate.set_target(new)

    def _digest_window(self) -> tuple[str, str] | None:
        anchor = self.cfg.reports_dir / ".last_digest"
        hi_dt = self.clk.utcnow()
        hi = hi_dt.isoformat(timespec="seconds")
        try:
            lo = anchor.read_text().strip()
        except OSError:
            lo = (hi_dt - timedelta(
                hours=C.CONT_DIGEST_INTERVAL_H)).isoformat(timespec="seconds")
            return lo, hi                        # first digest: send now
        try:
            lo_dt = datetime.fromisoformat(lo)
        except ValueError:
            lo_dt = hi_dt - timedelta(hours=C.CONT_DIGEST_INTERVAL_H)
            lo = lo_dt.isoformat(timespec="seconds")
        if hi_dt - lo_dt < timedelta(hours=C.CONT_DIGEST_INTERVAL_H):
            return None
        return lo, hi

    def _fallback_count(self, led: Ledger, lo: str, hi: str) -> int:
        """R23 'N on fallback model' over the window's verdicts, from the
        dossiers of record (crash-proof — run._batch_fallback_count
        pattern, windowed via the immutable events audit)."""
        sids = [r["session_id"] for r in led.db.execute(
            "SELECT DISTINCT session_id FROM events WHERE ts>=? AND ts<? "
            "AND to_state IN ('READY','FIX_QUEUED','REJECTED','HOLD_VLM')",
            (lo, hi)).fetchall()]
        return runmod._batch_fallback_count(self.cfg, sids)

    def _stuck_lines(self, led: Ledger) -> tuple[list[str], int]:
        """Stuck = non-terminal, unchanged > CONT_STUCK_H. DISCOVERED is
        excluded (cap-throttled intake is normal, not stuck — the digest's
        undownloaded count already shows it). HOLD_VLM ages from the FIRST
        HOLD event: each 30-min retry refreshes updated_at, which would
        otherwise hide a permanently-held session forever (r-loop 1)."""
        now = self.clk.utcnow()
        cut = (now - timedelta(hours=C.CONT_STUCK_H)).isoformat(
            timespec="seconds")
        rows = led.db.execute(
            "SELECT session_id, state, updated_at FROM sessions WHERE "
            "state NOT IN ('DELIVERED','REJECTED','SPLIT','DUPLICATE',"
            "'QUARANTINED','DISCOVERED','HOLD_VLM') AND updated_at<? "
            "ORDER BY updated_at", (cut,)).fetchall()
        held = led.db.execute(
            "SELECT s.session_id, s.state, "
            "(SELECT MIN(ts) FROM events e WHERE e.session_id=s.session_id"
            " AND e.to_state='HOLD_VLM') first_hold "
            "FROM sessions s WHERE s.state='HOLD_VLM'").fetchall()
        stuck = [(r["session_id"], r["state"], r["updated_at"])
                 for r in rows]
        for r in held:
            if r["first_hold"] and r["first_hold"] < cut:
                stuck.append((r["session_id"], "HOLD_VLM", r["first_hold"]))
        out = []
        for sid, state, since in stuck[:5]:
            try:
                age_h = (now - datetime.fromisoformat(since)
                         ).total_seconds() / 3600.0
            except (ValueError, TypeError):
                age_h = 0.0
            out.append(f"{sid} ({state} {age_h:.1f}h)")
        return out, len(stuck)

    def _send_digest(self, led: Ledger) -> None:
        win = self._digest_window()
        if win is None:
            return
        lo, hi = win
        try:
            lo_dt = datetime.fromisoformat(lo)
            hi_dt = datetime.fromisoformat(hi)
            window_h = (hi_dt - lo_dt).total_seconds() / 3600.0
        except ValueError:
            window_h = C.CONT_DIGEST_INTERVAL_H
        drow = led.db.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(duration_delivered_s),0) s "
            "FROM sessions WHERE state='DELIVERED' AND delivered_at>=? "
            "AND delivered_at<?", (lo, hi)).fetchone()
        rej_rows = led.db.execute(
            f"SELECT reasons_json FROM sessions WHERE state='REJECTED' AND "
            f"{reports.REJECT_TS}>=? AND {reports.REJECT_TS}<?",
            (lo, hi)).fetchall()
        quar_n = led.db.execute(
            "SELECT COUNT(DISTINCT session_id) n FROM events WHERE "
            "to_state='QUARANTINED' AND ts>=? AND ts<?",
            (lo, hi)).fetchone()["n"]
        label_lists = []
        for r in rej_rows:
            try:
                label_lists.append(reports.session_reject_labels(
                    json.loads(r["reasons_json"] or "[]"), daily=True))
            except json.JSONDecodeError:
                label_lists.append([reports.UNREADABLE_MARKER])
        counts = self._counts
        stuck, stuck_total = self._stuck_lines(led)
        now_ist = self.clk.utcnow().astimezone(C.IST)
        d = reports.DigestStats(
            now_ist=now_ist, window_h=window_h,
            delivered_n=drow["n"], delivered_hours=drow["s"] / 3600.0,
            rejected_n=len(rej_rows),
            reject_labels=reports.ordered_reject_labels(label_lists),
            hours_kamla=led.delivered_hours("kamla"),
            hours_ow=led.delivered_hours("outer_wilds"),
            backlog_undownloaded=counts.get("DISCOVERED", 0),
            backlog_inflight=sum(counts.get(s, 0) for s in (
                "DOWNLOADING", "INGESTED", "VALIDATING", "READY",
                "PACKAGED", "UPLOADED")),
            backlog_fix=sum(counts.get(s, 0) for s in (
                "FIX_QUEUED", "FIXING", "REVALIDATING")),
            backlog_hold=counts.get("HOLD_VLM", 0),
            incomplete=len(led.incomplete_list()),
            quarantined_n=quar_n,
            on_fallback=self._fallback_count(led, lo, hi),
            pool_target=self.gate.target, pool_active=self.gate.active,
            vlm_rung=self.current_rung(),
            stuck=stuck, stuck_total=stuck_total,
            past_deadline=now_ist > C.DEADLINE_IST)
        p = None if d.past_deadline else runmod._pace_now(led)
        msg = reports.build_digest_message(d, p)
        try:
            telegram.send_message(self.cfg, msg)
        except telegram.TelegramError as e:
            print(f"[digest-undelivered] {e}", file=sys.stderr)
            return                    # anchor unwritten -> retried next tick
        # anchor AFTER send: a kill duplicates a digest, never loses one
        (self.cfg.reports_dir / ".last_digest").write_text(hi)

    def _housekeeping_thread(self) -> None:
        led = Ledger(self.cfg.ledger_path)
        next_scale = 0.0
        next_sweep = 0.0
        self._counts = led.counts_by_state()
        try:
            while not self.stop.is_set():
                now = self.clk.mono()
                try:
                    self._counts = led.counts_by_state()
                    if now >= next_scale:
                        self._autoscale_tick()
                        next_scale = now + C.CONT_AUTOSCALE_INTERVAL_S
                    if self.send_telegram:
                        self._send_digest(led)
                        # CONT_DAILY_REPORTS is the payment-endgame
                        # interlock: with every rebuild-era root unstamped
                        # (recal_rebuild_reset nulled uploaded_reported_at),
                        # one daily send's late-arrival guard would pull the
                        # WHOLE cohort into one day's sheet, stamp it, and
                        # both misattribute the hours and deadlock
                        # recal_regen_sheets' stray-stamp gate (r-loop 1).
                        # The flip deploys False; True again after regen.
                        if C.CONT_DAILY_REPORTS:
                            runmod.send_daily_report_if_due(self.cfg, led)
                            runmod.send_folder_issues_if_due(self.cfg, led)
                    if now >= next_sweep:
                        led.backup_daily(self.cfg.backups,
                                         keep=C.LEDGER_BACKUP_KEEP)
                        runmod._finalize_orphan_rejects(self.cfg, led)
                        runmod._sweep_terminal_work(self.cfg, led)
                        # fresh dedup list per sweep: the ceiling alert
                        # re-fires hourly while the condition persists —
                        # run._alert's per-list dedup only spans this call
                        runmod._upload_ceiling_alert(self.cfg, led, [])
                        next_sweep = now + 3600
                except Exception as e:
                    self.alerts.alert(f"housekeeping duties failed "
                                      f"(driver continues): "
                                      f"{type(e).__name__}: {e}")
                self.stop.wait(20)
        finally:
            led.close()

    # ------------------------------------------------------------ launch
    def start(self) -> None:
        self.runner_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=C.CONT_POOL_MAX, thread_name_prefix="hl-runner")
        specs = [("hl-S", self._scan_thread)]
        specs += [("hl-D%d" % i, self._download_thread)
                  for i in range(C.CONT_DOWNLOAD_WORKERS)]
        specs += [("hl-Vdisp", self._v_dispatcher)]
        specs += [("hl-U%d" % i, self._upload_thread)
                  for i in range(C.CONT_UPLOAD_WORKERS)]
        specs += [("hl-H", self._housekeeping_thread)]
        for name, fn in specs:
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self.threads.append(t)

    def idle(self, led: Ledger) -> bool:
        """No owned sessions AND nothing currently eligible in any lane AND
        at least one scan pass done. Cooling sessions are not eligible —
        an --until-idle run exits and leaves them for the next run, exactly
        like the batch driver's attempted-set semantics."""
        if self._scan_passes < 1 or self.own.any():
            return False
        blocked = self.cool.blocked()
        counts = led.counts_by_state()
        for states in (("DISCOVERED",), ("INGESTED", "VALIDATING",
                       "FIX_QUEUED", "FIXING", "REVALIDATING"),
                       ("READY", "PACKAGED", "UPLOADED"), ("DOWNLOADING",),
                       ("HOLD_VLM",)):
            if not any(counts.get(s, 0) for s in states):
                continue
            for r in led.by_state(*states):
                if r["session_id"] not in blocked:
                    return False
        return True

    def shutdown(self) -> bool:
        """Returns True when every thread stopped inside the grace window.
        False = threads may still be writing the ledger; the caller must
        NOT release the run lock (a second driver could then start against
        live writes — r-loop 1); the stale lock is pid-reclaimed by the
        next starter once this process is truly dead."""
        self.stop.set()
        deadline = self.clk.mono() + C.CONT_DRAIN_GRACE_S
        for t in self.threads:
            t.join(timeout=max(0.1, deadline - self.clk.mono()))
        if self.runner_pool is not None:
            self.runner_pool.shutdown(wait=False, cancel_futures=True)
        alive = [t.name for t in self.threads if t.is_alive()]
        if alive:
            print(f"[shutdown] threads still alive after grace: {alive} — "
                  f"exiting anyway (kill-safe by design); run lock kept "
                  f"for pid-reclaim", file=sys.stderr)
        return not alive


def run_continuous(cfg: C.Config, *, dest_prefix: str = C.VENDOR,
                   until_idle: bool = False, max_wall_s: float | None = None,
                   send_telegram: bool = True,
                   clocks: _Clocks | None = None,
                   install_signals: bool = True) -> int:
    """Entry point. Returns 0 on clean stop, 1 when the run lock is held
    (misconfiguration: two drivers armed — systemd's restart loop + start
    limit turns that into a Telegram alert), 2 when the config flag is off
    (rollback interlock)."""
    if not C.PIPELINE_CONTINUOUS:
        print("PIPELINE_CONTINUOUS is False (rollback interlock) — refusing "
              "to start", file=sys.stderr)
        return 2
    if not runmod.acquire_lock(cfg):
        print("run lock held — another driver is live; refusing to start",
              file=sys.stderr)
        return 1
    clean_stop = True
    drv = ContinuousDriver(cfg, dest_prefix=dest_prefix,
                           send_telegram=send_telegram, clocks=clocks)
    if install_signals:
        for s in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(s, lambda *_: drv.stop.set())
            except ValueError:
                pass                       # not the main thread (tests)
    try:
        cfg.ensure_dirs()
        led = Ledger(cfg.ledger_path)
        try:
            led.backup_daily(cfg.backups, keep=C.LEDGER_BACKUP_KEEP)
        except Exception as e:
            drv.alerts.alert(f"start-of-run ledger backup failed (driver "
                             f"continues): {type(e).__name__}: {e}")
        # startup recovery: kill-window leak sweeps (batches untouched —
        # open batch rows are the dormant batch driver's rollback state)
        try:
            runmod._finalize_orphan_rejects(cfg, led)
            runmod._sweep_terminal_work(cfg, led)
        except Exception as e:
            drv.alerts.alert(f"startup sweep failed (driver continues): "
                             f"{type(e).__name__}: {e}")
        counts = led.counts_by_state()
        if send_telegram:
            try:
                telegram.send_message(
                    cfg, "🟢 continuous driver started — " + " ".join(
                        f"{k}:{v}" for k, v in sorted(counts.items())))
            except telegram.TelegramError as e:
                print(f"[startup-msg-undelivered] {e}", file=sys.stderr)
        print(f"[continuous] started: pool {C.CONT_POOL_MIN}.."
              f"{C.CONT_POOL_MAX}, scan {C.CONT_SCAN_INTERVAL_S}s, "
              f"cap {C.CONT_MEDIA_CAP_SESSIONS}, states {dict(counts)}")
        clean_stop = False               # threads about to go live
        drv.start()
        t0 = drv.clk.mono()
        idle_streak = 0
        while not drv.stop.is_set():
            drv.stop.wait(0.5)
            if max_wall_s is not None and drv.clk.mono() - t0 > max_wall_s:
                print("[continuous] max_wall_s reached — stopping")
                break
            if until_idle:
                # require consecutive idle reads: a session can be between
                # release-by-one-lane and claim-by-the-next
                idle_streak = idle_streak + 1 if drv.idle(led) else 0
                if idle_streak >= 3:
                    print("[continuous] idle — stopping (--until-idle)")
                    break
        clean_stop = drv.shutdown()
        led.close()
        return 0
    finally:
        if not clean_stop and drv.threads:
            # exception path after start(): still attempt a graceful stop
            clean_stop = drv.shutdown()
        # never release the lock over live writer threads: leave it stale
        # for pid-based reclaim once this process actually dies
        if clean_stop:
            runmod.release_lock(cfg)
