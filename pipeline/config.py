"""Configuration: paths, secrets, and every numeric gate from plan §5.

Secrets live OUTSIDE the repo in ~/.config/hl-gamedata/secrets.env and are
never logged or committed. Working data lives OUTSIDE the repo in
~/hl-pipeline/ (override with HL_PIPELINE_HOME for tests).
Threshold changes are human-intervention-only (plan §13): edit here, logged
via git.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SECRETS_PATH = Path.home() / ".config" / "hl-gamedata" / "secrets.env"

# Phase-1 scope (R1): these two games, nothing else.
GAMES = ("kamla", "outer_wilds")
GAME_LABELS = {"kamla": "Kamla", "outer_wilds": "OW"}   # §14 message labels
VENDOR = "humynlabs"

# Required files per session folder in Drive I (R3+R4). rrd files are ignored
# entirely (R17) — never downloaded, regenerated at packaging.
REQUIRED_FILES = ("video.mp4", "frames.csv", "session.json",
                  "inputs.jsonl", "metadata.json")

# --- §5 numeric thresholds (one table, one place) ---------------------------
MIN_CLIP_S = 70.0                 # hard, also per split segment
SESSION_SOFT_MAX_S = 30 * 60.0    # >30 min accepted with note (R16)
MIN_DISTINCT_ACTIONS = 3          # per session AND per split segment (R14)
# Mid-clip non-gameplay keep-vs-cut bar (Adnaan 2026-08-17, split-cascade
# rulings R2+R3, relayed via the sister session). The test was
# `span <= 2.0 AND span <= 0.2% of clip`; the fractional half
# (KEEP_GATE_MAX_FRAC, now DELETED — R2) was a ratchet that TIGHTENED as
# clips got shorter: parent avg 1134s allowed 2.3s, child avg 342s allowed
# 0.68s, so a blip the parent deliberately KEPT became a cut-trigger in its
# own child purely because the child was shorter. Splitting was therefore
# self-perpetuating — 320 roots -> 600 children -> 145 grandchildren, with
# 109 depth-1 children themselves SPLIT, and the rebuild ran ~28h against a
# planned 7-8h at net queue drain ~= zero. The test is now ABSOLUTE ONLY:
# span <= this -> keep (gate if inputs inside), else cut.
# SUPERSESSION: this replaces the "keep+gate if <=2s contiguous AND <=0.2%
# of clip" ruling recorded in PIPELINE_IMPLEMENTATION_PLAN.md §"round-3"
# and attributed to Adnaan. That constraint was verified NOT to come from
# the Odyssey spec, so this is Adnaan superseding his own prior ruling.
# SCOPE: CNT_MID_NONGAMEPLAY only. Edge trimming (CNT_EDGE_NONGAMEPLAY)
# is UNCHANGED and still trims non-gameplay touching clip head/tail at any
# length. R1 reuses this SAME constant as the scanner path's cut bar —
# one threshold, not two.
KEEP_GATE_MAX_S = 5.0
# Scanner static-window minimum length (Adnaan 2026-08-17, ruling R1).
# Until now this was a bare inline literal in validate.py — the only
# load-bearing threshold in the pipeline with no provenance: absent from
# this file, unmentioned in the plan, pinned by no test. It STAYS 0.8; the
# initial instruction to raise it to 5s is SUPERSEDED. Reason it must stay
# low: this path exists to catch freezes SHORTER than the VLM sweep's 4s
# sampling interval, so that inputs occurring inside them can be GATED. A
# 5s floor would find nothing at all. It is safe at 0.8 because R1 forbids
# a window this short from proposing a cut (see KEEP_GATE_MAX_S above) —
# short finds are gating-only and create no child rows, so the 40-candidate
# cap in validate.py is now a pure cost bound and can no longer drive a
# cascade.
SCANNER_STATIC_MIN_S = 0.8
AFK_MIN_S = 30.0                  # >30s zero input + near-static = AFK
STILLNESS_FROZEN_BELOW = 0.40     # window motion / live-gameplay baseline
# Dead-black whole-clip gate — recalibrated 2026-08-16 (Adnaan): Kamla's
# legitimate dark scenes sit at mean luma 7-16 on the scanner's 160x90
# gray downscale, so the old near-black(<16, >50%) rule mass-false-
# positived (all 122 black-frozen ledger rows measured 50-76%, none >=90%);
# only the true capture-failure signature (uniform dead-black video) may
# reject. Frac tightened 0.50 -> 0.995 same evening (Adnaan, pre-rebuild
# relaunch; nothing was judged under 0.50). Partial blackouts are the
# mid-clip machinery's job (static windows -> VLM -> gate/split), not this
# whole-clip gate's. The frozen-motion arm (baseline<0.3) is dropped
# outright — near-static video stays advisory-only via video_active
# (INP_KEYS_MISSING interplay in validate.py).
DEAD_BLACK_LUMA_BELOW = 5.0       # a frame is dead-black: mean luma < this
DEAD_BLACK_REJECT_FRAC = 0.995    # reject when >= this frac of frames dead
# FIX_GATE_WINDOW blanks this many frames BEYOND the detected window on
# each side (Adnaan 2026-08-16): scanner re-detection jitter of +-1 frame
# between fix and recheck resurrected INP_FROZEN_ACTIONS forever (the
# 08-16 fix-failed loop, 5 of 10 rows) — padding makes the gate cover any
# re-drawn boundary. Content cost: ~66ms of input blanked beside a pause.
GATE_PAD_FRAMES = 2
DROPS_WARN_PCT = 1.0              # irregular intervals: <=1% pass
DROPS_REJECT_PCT = 5.0            # >5% reject; 1-5% deliver+warn
LAG_TARGET_MS = 50.0              # controls<->video: <=50 pass
LAG_HARD_MS = 150.0               # >150 constant -> fix+re-verify
FRAME_SYNC_MS = 100.0             # per-row timestamp vs real PTS
# VLM game-identity tripwire (unanimity only — sibling insight #7).
# GATING IS OFF for Phase 1: Adnaan ruled 2026-08-14 ~21:50 IST (recorded in
# project memory local-vlm-fallback-benchmark, postdating the plan's 19:47
# freeze) that VLM game identification is NOT required — votes are logged
# report-only. The R1 label-scope reject (non-Kamla/OW session labels) is a
# separate rule and stays. Flip to True to restore the plan-§5 behavior.
VLM_GAME_TRIPWIRE_GATES = False
TRIPWIRE_MIN_VOTES = 8
TRIPWIRE_MIN_VOTE_FRAC = 0.90     # of named guesses
TRIPWIRE_MIN_FRAME_FRAC = 0.50    # of sampled frames
# Payment-report window offset (Adnaan 08-15, settling restate-vs-offset
# as OFFSET): the reporting window ends this many hours BEFORE send time
# so every cohort in it has settled by generation — one authoritative
# sheet per day, no restating. EMPIRICAL, not a principle: measured
# upload->final-outcome lag over 25 settled roots was median 1.76 h /
# p90 2.77 h / worst non-startup 3.39 h; 4.0 clears every observed case
# with margin. Re-measure and retune when worker count or batch size
# changes. Pending cells now signal something genuinely stuck.
REPORT_OFFSET_H = 4.0
BATCH_SIZE = 10                   # R5
FIX_RETRIES = 2                   # R2: 2 fix passes then reject
RRD_SAMPLE_FRAC = 0.20            # R17: random 20% per game per day
DISK_LOW_WATER_GB = 100           # F7
INCOMPLETE_ESCALATE_H = 48        # F8
PACE_ALARM_FACTOR = 1.15          # §11.3
LAGGING_GAME_PRIORITY_GAP = 0.10  # F4: >10% pace gap -> priority
TARGET_HOURS_PER_GAME = 500.0     # R10 — delivered post-trim clip hours
COLLECT_TARGET_HOURS = 600.0      # R16 — collection buffer target
LEDGER_BACKUP_KEEP = 14           # §8

# Deadline: 2026-08-24 23:59 IST (§14 pace math). IST = UTC+5:30, no DST.
IST = timezone(timedelta(hours=5, minutes=30))
DEADLINE_IST = datetime(2026, 8, 24, 23, 59, tzinfo=IST)
DAILY_REPORT_HOUR_IST = 14        # R12

# Gemini 429 policy (§13): per-call backoff then session-level HOLD_VLM.
VLM_BACKOFF_BASE_S = 2.0
VLM_BACKOFF_MAX_S = 60.0
VLM_MAX_TRIES = 5

# Endpoint failover (R21): genlang <-> Vertex express. §7.6 smoke matrix
# from the VM (2026-08-15): vertex answered 403 API_KEY_SERVICE_BLOCKED for
# BOTH keys — vertex is dead for the active keys, so the flag STAYS False
# (the one matrix outcome where §7.6 keeps it off). Re-run the matrix and
# flip here if Adnaan unblocks aiplatform.googleapis.com on the keys.
VLM_FAILOVER_ENABLED = False

# R23 quota ladder: complete rung-model list on GEMINI_API_KEY, top rung
# first (the model of record). Below the last entry comes the prev-key rung
# (GEMINI_API_KEY_PREV at the rung-0 model), then HOLD_VLM. Rungs are sticky
# for the rest of the run; every run restarts at rung 0. All three ids
# VERIFIED by §7.6 generateContent probes on both keys (2026-08-15): the
# plan's assumed "gemini-3.1-pro" does not exist (404) — the live pro id is
# gemini-3.1-pro-preview.
VLM_MODEL_LADDER = ("gemini-3.7-flash", "gemini-3.5-flash",
                    "gemini-3.1-pro-preview")

# Overlap driver (R20): False = byte-identical lockstep fallback.
PIPELINE_OVERLAP = True
MAX_BATCHES_IN_FLIGHT = 3         # R5 amendment: <=3 batches (~30 sessions)

# --- Continuous driver (Adnaan rulings 2026-08-17; PIPELINE_CONTINUOUS_DESIGN.md)
# Gates `python -m pipeline run-continuous`. False is the ROLLBACK interlock:
# with it False a lingering/re-enabled hl-continuous.service refuses to start,
# so re-arming hl-pipeline.timer can never produce two drivers on one ledger.
PIPELINE_CONTINUOUS = True
CONT_SCAN_INTERVAL_S = 300        # ruling 2: poll Drive I every 5 min
CONT_MEDIA_CAP_SESSIONS = 40      # ruling 2: ~40 sessions local, ledger-counted
CONT_HOLD_RETRY_MIN = 30          # ruling 6: HOLD_VLM one retry / 30 min, forever
CONT_DIGEST_INTERVAL_H = 3.0      # ruling 4: Telegram digest cadence
# Autoscale band (ruling 3): validation-runner concurrency, bounded below by
# the suggested floor and above by cores-12 (headroom for D/U/S/H, ffmpeg
# side-processes and the OS). Computed at import so the resize to
# c2d-highcpu-56 lifts the ceiling without a config edit.
CONT_POOL_MIN = 8
CONT_POOL_MAX = max(8, (os.cpu_count() or 16) - 12)
CONT_AUTOSCALE_INTERVAL_S = 60
CONT_CPU_HIGH = 85.0              # no up-steps above this CPU%
CONT_CPU_CRIT = 95.0              # two consecutive intervals above -> step down
CONT_STEP_UP = 2
CONT_STEP_DOWN = 4                # 429 backpressure steps down harder than up
CONT_BACKPRESSURE_WINDOW_S = 600  # trailing window for 429-pressure counting
CONT_BACKPRESSURE_429_PER_MIN = 1.0
# Sticky-rung scope redefined for a forever-process (R23 amendment, ruling
# "sticky until a quiet period"): the rung resets to 0 after this many
# minutes with zero 429-pressure events and zero worker-reported climbs.
CONT_RUNG_QUIET_RESET_MIN = 60
CONT_ALERT_DEDUP_MIN = 60         # TTL dedup: recurring conditions re-alert
CONT_DOWNLOAD_WORKERS = 1         # serial D preserves F3 ctime-order intake
CONT_UPLOAD_WORKERS = 1           # serial U preserves the R17 15%-floor read
CONT_STUCK_H = 6.0                # digest stuck-list threshold
CONT_DOWNLOAD_RETRY_MIN = 5.0     # transient download failure cooldown
CONT_UPLOAD_RETRY_MIN = 10.0      # upload failure cooldown
CONT_RUNNER_CRASH_RETRY_MIN = 5.0  # session-runner crash cooldown
# Payment-endgame interlock: while False the driver sends NO daily payment
# or folder-issues reports (digest/alerts unaffected). The flip deploys
# False so a 14:00 IST send can never stamp the unstamped rebuild cohort
# before recal_regen_sheets.py regenerates the 08-15/08-16 sheets; set
# True (+ redeploy + restart) right after the regen --send completes.
CONT_DAILY_REPORTS = True
CONT_DISPATCH_IDLE_S = 2.0        # dispatcher poll when nothing eligible
CONT_DRAIN_GRACE_S = 600          # SIGTERM: wait this long for runners
# QUARANTINED is the one terminal state with no wipe: deliver_session and
# finalize_rejected own the DELIVERED/REJECTED wipes and the hourly sweep
# covers SPLIT/DUPLICATE, so quarantined media was held forever AND was
# invisible to the ~40-session cap. Twenty 3 GB sessions = ~90 GB the cap
# could not see, so intake stopped on the disk low-water instead, with no
# path back (r-loop 3). The media is now counted against the cap and
# reclaimed after this many hours — the DOSSIER is the evidence of record,
# and Drive I still holds the original (R6 read-only forever), so the local
# copy is re-downloadable, not unique.
CONT_QUARANTINE_RECLAIM_H = 48
# Shared translation_report.json lock (validate._locked_report_update).
# INVARIANT: WAIT > STALE. Up to CONT_POOL_MAX workers race this file and a
# waiter that runs out of patience writes UNLOCKED, which is the r1 #8 lost
# update. Patience used to be max(5s, CONT_POOL_MAX seconds) = 44s against a
# 120s staleness threshold, so for a DEAD holder — the one case that cannot
# resolve itself — every waiter gave up before the breaker could ever fire
# (r-loop 3). The guarded section is milliseconds, so 20s is already a very
# generous "this holder is dead" bar and 30s of patience always outlasts it.
REPORT_LOCK_STALE_S = 20.0
REPORT_LOCK_WAIT_S = 30.0

# Ledger states (§6 state machine)
STATES = (
    "DISCOVERED", "INCOMPLETE", "DOWNLOADING", "INGESTED", "VALIDATING",
    "READY", "FIX_QUEUED", "FIXING", "REVALIDATING", "REJECTED",
    "QUARANTINED", "DUPLICATE", "HOLD_VLM", "PACKAGED", "UPLOADED",
    "DELIVERED", "SPLIT",
)


def load_secrets(path: Path = SECRETS_PATH) -> dict[str, str]:
    """Parse KEY=VALUE lines. Values are secrets: never print or log them."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass
class Config:
    home: Path
    repo_root: Path = REPO_ROOT
    remote_collect: str = "drive-collect:"
    remote_deliver: str = "drive-deliver:"
    workers: int = 4                       # §7.5 benchmark sets this
    test_mode: bool = False                # TEST-prefixed Telegram, few msgs
    secrets: dict[str, str] = field(default_factory=dict)

    # -- derived paths (all outside the repo) --
    @property
    def work(self) -> Path: return self.home / "work"
    @property
    def dossiers(self) -> Path: return self.home / "dossiers"
    @property
    def reports_dir(self) -> Path: return self.home / "reports"
    @property
    def logs(self) -> Path: return self.home / "logs"
    @property
    def stage(self) -> Path: return self.home / "delivery-stage"
    @property
    def ledger_path(self) -> Path: return self.home / "ledger.db"
    @property
    def backups(self) -> Path: return self.home / "backups"
    @property
    def lock_dir(self) -> Path: return self.home / "run.lock"

    @property
    def gemini_key(self) -> str: return self.secrets.get("GEMINI_API_KEY", "")
    @property
    def gemini_key_prev(self) -> str:
        # R23 last-resort rung; empty string = rung not armed
        return self.secrets.get("GEMINI_API_KEY_PREV", "")
    @property
    def gemini_model(self) -> str:
        return self.secrets.get("GEMINI_MODEL", "gemini-3.7-flash")
    @property
    def tg_token(self) -> str: return self.secrets.get("TELEGRAM_BOT_TOKEN", "")
    @property
    def tg_chat(self) -> str: return self.secrets.get("TELEGRAM_CHAT_ID", "")

    def ensure_dirs(self) -> None:
        for p in (self.home, self.work, self.dossiers, self.reports_dir,
                  self.logs, self.stage, self.backups):
            p.mkdir(parents=True, exist_ok=True)


def load(home: Path | None = None, *, secrets_path: Path = SECRETS_PATH,
         test_mode: bool | None = None) -> Config:
    home = Path(home or os.environ.get("HL_PIPELINE_HOME",
                                       Path.home() / "hl-pipeline"))
    cfg = Config(home=home, secrets=load_secrets(secrets_path))
    w = os.environ.get("HL_PIPELINE_WORKERS")
    if w and w.isdigit():
        cfg.workers = int(w)
    if test_mode is not None:
        cfg.test_mode = test_mode
    elif os.environ.get("HL_PIPELINE_TEST_MODE", "").lower() in (
            "1", "true", "yes"):
        cfg.test_mode = True
    return cfg
