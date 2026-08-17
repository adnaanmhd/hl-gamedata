# Continuous driver — design (step 1 of CONTINUOUS_PIPELINE_KICKOFF_PROMPT.md)

Replaces the batch overlap driver (R20) per Adnaan's 2026-08-17 rulings: no batches,
continuous per-session flow, 5-min Drive polls, adaptive autoscale with Gemini-429
backpressure, per-session fix scheduling, 3-h in-driver digest, always-on systemd
service. The batch driver stays dormant-intact as the rollback path.

Everything here was designed against the code as mapped 2026-08-17 (line refs at
HEAD `9912dd2`). Sources: full reads of `run.py`/`ledger.py`/`config.py`/
`__main__.py`; module maps of ingest/validate+vlm+scanner/fix+cutter+gate+deliver/
reports+pace+telegram/systemd+tools/tests.

---

## 0. Shape

One always-on process, `python -m pipeline run-continuous`, systemd unit
`hl-continuous.service` (Type=simple, Restart=always). New module
`pipeline/continuous.py`; `run.py` is touched only for (a) the `run-continuous`
command branch in `main()` and (b) one additive, optional key in
`_validate_worker`'s args dict (429 pressure channel, §5). Nothing else in the
batch driver changes — rollback stays byte-identical.

**The ledger IS the queue.** No in-memory queue is ever the source of truth.
Each lane's dispatcher polls `ledger.by_state(...)` (cheap; WAL; own connection
per thread) plus two in-memory-only overlays that are *allowed* to be lost on
kill:

- `owned` — set of sids currently held by some lane (claim-before-work,
  release-on-exit, one lock). Guarantees single-owner-per-session, which is the
  precondition every mutating path in fix/deliver assumes and the
  `set_state` read-modify-write pattern requires (ledger has no claim
  primitive).
- `cooldown[sid] → monotonic deadline` — per-session retry pacing (§6). Losing
  it on restart means one immediate retry, which is benign and matches "kill at
  any instant resumes exactly from the ledger".

## 1. Threads & lanes

| Thread | Count | Owns (ledger writes) |
|---|---|---|
| S — scanner | 1 | everything `ingest.scan` writes today (DISCOVERED inserts, dedupe/DUPLICATE, supersede, heals, quarantines, incomplete upserts) |
| D — download worker | `CONT_DOWNLOAD_WORKERS` = 1 | DISCOVERED/DOWNLOADING → INGESTED, re-queue/QUARANTINED on download outcomes (exact `_download_phase` routing, run.py:168-223) |
| V — dispatcher + session runners | dispatcher 1; runners bounded by the autoscale gate (§4) | VALIDATING, READY, FIX_QUEUED, FIXING, REVALIDATING, REJECTED, HOLD_VLM, SPLIT + child inserts |
| U — upload worker | `CONT_UPLOAD_WORKERS` = 1 | READY → PACKAGED → UPLOADED → DELIVERED; FIX_QUEUED/REJECTED on final-gate failure (exact `_deliver_phase` routing, run.py:575-623) |
| H — housekeeping | 1 | no session states; digest anchor, daily report (existing stamps→anchor→marker), backups, sweeps, autoscale target, alerts |

Writes stay phase-scoped exactly as in the batch driver; the `owned` set makes
the scoping airtight where the batch driver relied on batch membership.

- **S** ticks every `CONT_SCAN_INTERVAL_S = 300`. It reuses `ingest.scan`
  whole-listing granularity unchanged (all dedupe/heal/supersede adjudication
  stays in one place, ordering within a listing unchanged). Scan-time
  cross-dup rejects get `finalize_rejected` exactly as run.py:1439-1445 does.
- **S↔D interlock**: one `intake_lock` serializes a scan pass against D's
  pick-and-claim. Reason: scan's cross-dup un-pick clobbers only pre-download
  states (tested semantics, r2 #143-154); without the lock, D could claim a
  DISCOVERED row mid-scan and turn a legal un-pick into a race. The batch
  driver got this serialization for free (scan ran before threads started).
- **D** picks the next eligible DISCOVERED row FIFO by `(lagging-game priority,
  drive_ctime, sid)` — the `ingest.next_batch` ordering with size 1, minus the
  batch slice. Eligibility: not owned, not cooling down, media cap + disk
  low-water pass (§7). `ingest.download` unchanged. **Rows put back to
  DISCOVERED by `recal_refix_reset.py` re-enter here** — intake is
  ledger-driven, not discovery-driven, by construction.
- **V dispatcher** picks, in priority order: (1) FIX_QUEUED (immediacy ruling —
  includes U's gate-fail hand-backs), (2) FIXING/REVALIDATING/VALIDATING
  (crash-resume triage), (3) HOLD_VLM whose 30-min cooldown expired — BEFORE
  fresh intake, or a steady INGESTED stream starves held sessions (ruling 6 /
  review-r4 #9; the 30-min cooldown bounds HOLD's share of dispatch),
  (4) INGESTED FIFO. It acquires one autoscale-gate slot (blocking), claims
  the sid, and submits a **session runner**.
- **Every lane loop is iteration-guarded** (`_lane_loop`): an exception that
  escapes the per-sid handlers alerts (TTL-deduped), reopens the lane's
  ledger connection, pauses one idle interval, and continues. A dead lane in
  an always-on process is permanent loss with a healthy-looking heartbeat —
  the batch driver's 30-min process exit was the backstop this process no
  longer has (r-loop 1 blocker).
- **D resumes DOWNLOADING rows first** (they are already inside the media
  cap; rclone is idempotent), then picks fresh DISCOVERED. The
  DISCOVERED→DOWNLOADING transition commits INSIDE the intake lock, so a
  scan pass can never see a D-claimed sid as still-clobberable (cross-dup
  un-pick race).
- **A session runner** drives ONE session through the whole V domain in a loop,
  holding its slot until the session leaves the domain:
  `validate → {READY | HOLD_VLM | REJECTED | FIX_QUEUED}`;
  on FIX_QUEUED: budget check → FIXING (attempt charged exactly as
  run.py:480-486) → apply → `REVALIDATING → validate` again, or SPLIT
  (children inserted + wiped parent), or REJECTED. A FIXING row claimed at
  resume runs `_recover_split` triage FIRST (adopt-complete-split before any
  re-verdict — run.py:339-413 semantics, reused not rewritten).
  Exit actions: READY → release (U's dispatcher sees it); REJECTED →
  `finalize_rejected` at the transition (per-session terminal hook replacing
  the batch close-out sweep); HOLD_VLM → 30-min cooldown; SPLIT → children
  are claimed-and-queued in the same step that inserts their rows (the
  child-parking class and the two-live-batches class both become structurally
  impossible: there is no loop to close and no batch to ride).
  Validation itself runs in a **fresh single-job spawn subprocess** via the
  existing `_validate_worker` (one `ProcessPoolExecutor(max_workers=1,
  mp_context=spawn)` per job). Rationale: (a) preserves the
  workers>1-means-subprocess crash isolation rule (run.py:254-258) with a far
  simpler BrokenProcessPool story — a native crash costs exactly that session,
  no wave retry re-paying completed sweeps; (b) pool resizing becomes a
  non-problem (§4); (c) fresh interpreter per session kills the long-lived
  worker staleness class (engine/prev-key caches, cv2 leaks). Cost ≈ seconds
  of spawn+import per multi-minute session — negligible.
- **U** picks READY/PACKAGED/UPLOADED FIFO, runs `deliver_session` unchanged.
  Default 1 worker on purpose: keeps the R17 15%-floor read-then-decide
  serialized and the rrd/day accounting semantics exactly as tested. On
  failed_gate: reasons mapped and handed back as FIX_QUEUED (no budget charge,
  run.py:599-619 semantics) — V picks it up immediately (priority 1), not next
  tick. On upload failure: alert + 10-min cooldown, retried forever.
- **H** ticks every ~20 s and runs each duty at its own cadence, all in one
  thread so report/digest triggers are never concurrently invoked (the
  marker-check/marker-write races stay impossible): digest (§8), the UNCHANGED
  `send_daily_report_if_due` + `send_folder_issues_if_due` + `backup_daily`,
  `_finalize_orphan_rejects` + `_sweep_terminal_work` (hourly), upload-ceiling
  alert, autoscale controller (§4), stuck-session detection (§8).

Shutdown: SIGTERM sets a stop event; dispatchers stop picking; runners finish
their current step (bounded); process exits. Kill -9 at any instant is the
designed-for path, not the exception (§9).

## 2. Entry point, flag, lock

- `python -m pipeline run-continuous` → `continuous.run_continuous(cfg)`.
- `C.PIPELINE_CONTINUOUS = True` gates the command (refuses to start when
  False). **This flag is the rollback interlock**: rollback flips it False so
  a lingering/re-enabled service unit cannot fight the batch timer.
- The existing `run.lock` (acquire/reclaim logic unchanged, run.py:55-114)
  is held for the service's lifetime — mutual exclusion against the dormant
  batch driver and against `recal_refix_reset.py`'s live-pipeline abort check
  works unchanged. `_pid_is_pipeline` already matches the new process (same
  `python -m pipeline` cmdline).
- Test/canary knobs: `run_continuous(cfg, stop_event=…, until_idle=False,
  max_wall_s=None, now_fn=…)` — the bounded-run boundary the resume tests
  need (the suite's "run() twice" pattern becomes "run until idle, kill,
  run until idle again").

## 3. State machine

Unchanged. No new states, no schema migration. The batches table is **never
touched**: leftover open batch rows from the rebuild era stay as-is — they are
the dormant batch driver's state, and finishing them here would corrupt a
future rollback's regroup. (Reported once as bookkeeping in the final report.)

## 4. Autoscale

Concurrency quantity: number of concurrently active session runners
(≈ concurrent validation subprocesses; a runner's fix steps run inside its
slot, so ffmpeg-heavy fixes are accounted, unlike the batch driver).

Mechanism: a **ResizableGate** — target + active count + condition variable.
Dispatcher blocks in `acquire()` when `active ≥ target`; runners `release()`
on exit. Raising the target notifies waiters; lowering it simply lets active
drain below it — running sessions are never interrupted. (This is why
single-job pools matter: there is no ProcessPoolExecutor to resize.)

Controller (in H, every `CONT_AUTOSCALE_INTERVAL_S = 60`), pure function
`autoscale_decision(inputs) → target` so it unit-tests without a clock:

```
band            = [CONT_POOL_MIN=8, CONT_POOL_MAX=max(8, cpu_count()-12)]
inputs          = cpu_pct (Δ/proc/stat, loadavg/cores fallback),
                  queue_depth (eligible V-domain sids incl. running),
                  p429 (429 events in the trailing CONT_BACKPRESSURE_WINDOW_S=600),
                  rung_climb (any worker-reported rung > injected rung in window)
rules, in order:
  1. p429_rate ≥ CONT_BACKPRESSURE_429_PER_MIN=1.0  OR rung_climb
       → target -= CONT_STEP_DOWN=4   (floor band; also freezes rule 3)
  2. cpu_pct > 95 for two consecutive intervals
       → target -= 2
  3. cpu_pct < CONT_CPU_HIGH=85 AND queue_depth > active
       → target += CONT_STEP_UP=2    (cap band)
  else hold
```

Every decision is logged with its inputs. All knobs live in `config.py`
(config-visible per ruling 3).

**429 signal path** (does not exist today — vlm.py's backoff sleep is silent):
the 429/5xx branch of `_generate_once` (vlm.py:127-135) appends one small line
`{ts, model, endpoint_tag, rung, status}` to `cfg.logs/vlm-pressure.jsonl`
(O_APPEND, atomic for small writes; **tags only, never URLs/keys** — existing
secrets discipline). Workers learn the path via one optional key in the args
dict (`pressure_path`), set by `_validate_worker` into the vlm module —
additive; the batch driver simply doesn't pass it. H tails the file for the
trailing-window count. Rationale: file channel gives real-time backpressure
even while every worker is asleep mid-backoff (worker-result-only signaling
would be blind exactly when it matters); the durable per-session record stays
where it already is (dossier `models_used`).

**Sticky-rung scope, redefined for a continuous process** (ruling: "sticky
until a quiet period, document it"): driver-level rung state, injected into
every job at submit (dequeue-time, not plan-time — no staleness), max absorbed
from every result (today's semantics, tightened by per-future submission).
**Reset rule: the rung drops back to 0 after `CONT_RUNG_QUIET_RESET_MIN = 60`
minutes with zero pressure-file activity and zero worker-reported climbs.**
A climb is **reported > injected** for that job — never a comparison against
the driver's current rung: workers echo max(injected, climbed), so a stale
in-flight job finishing after a quiet-period reset must not resurrect the
rung or re-stamp the climb clock (r-loop 1). This is the continuous analogue
of "next run resets to 3.7": a run boundary meant "quota trouble is presumed
over"; sixty quiet minutes now carries that presumption. Reset is logged and
surfaces in the next digest. Driver restart also resets to rung 0 (today's
kill behavior, kept deliberately).

## 5. Fix & HOLD_VLM scheduling

- **FIX_QUEUED re-enters the pool immediately** — V-dispatcher priority 1,
  runner loops fix→revalidate in place. Budget: increment ONLY at the FIXING
  transition (run.py:480-486); gate-fail requeue never charges (run.py:611-614)
  — exact accounting preserved, so the deliver→gate-fail→fix ping-pong stays
  bounded by 2 attempts exactly as today.
- **HOLD_VLM: one retry per 30 min, forever** (`CONT_HOLD_RETRY_MIN = 30`),
  per-session in-memory cooldown. Restart retries immediately once — benign,
  documented. Each retry is a full re-validation (F5: the aux battery is
  re-derived, never shortcut). No guaranteed-batch machinery needed: HOLD
  retries can never be starved because they are dispatcher picks, not slot
  arbitration against fresh intake.
- Anti-spin generally: the batch driver's `attempted` set (once per run)
  becomes per-failure-class cooldowns: transient download 5 min, upload
  failure 10 min, session-runner crash 5 min, HOLD_VLM 30 min. Same
  retry-forever semantics as the 30-min tick, with explicit pacing.

## 6. Dup ordering under 5-min polls (documented semantics)

The md5 accept-earliest-`createdTime` rule (F3) is adjudicated inside
`ingest.scan` against the whole listing, unchanged. What polling changes is
the *window*: a later-ctime copy can be mid-pipeline (or delivered) minutes
before its earlier-ctime twin is uploaded. The existing, tested semantics
already cover this and are **kept without a settle delay**:

- both pre-download → earliest ctime wins, loser INT_DUP_CROSS;
- later copy already in flight/delivered when the earlier appears → the
  in-flight/shipped copy is kept and the deviation is flagged (r2 #143-154,
  r4_ingest triple-copy semantics — "accept earliest" degrades to "accept
  earliest, else flag" exactly as today).

A settle delay would trade mission throughput for dup purity on a rare event
(cross-player identical md5); rejected. Within any single poll the listing is
adjudicated in one pass, so ordering inside a poll is deterministic.

## 7. Disk & media bounds

- **Media cap**: D pauses intake while
  `count(states DOWNLOADING…UPLOADED + HOLD_VLM) ≥ CONT_MEDIA_CAP_SESSIONS = 40`
  (ledger-derived, resume-exact). Wipe-after-terminal is already in
  `deliver_session`/`finalize_rejected`; the hourly `_sweep_terminal_work` +
  `_finalize_orphan_rejects` sweeps reclaim kill-window leaks, so the cap
  cannot ratchet shut.
- **Disk low-water** (F7): unchanged 100 GB check before each download; V/U
  keep draining and wiping (gating intake only — pausing delivery would starve
  the reclaim path).
- Terminal wipe additionally calls `validate._locked_report_remove` for the
  sid (stale shift entries in the shared translation_report.json otherwise
  poison a re-upload's revalidation — mapped risk).

## 8. Digest & reporting

- **3-h digest** (replaces per-batch toplines): persisted anchor
  `cfg.reports_dir/.last_digest`; H sends when `now − anchor ≥ 3 h`, writes the
  anchor only after a successful send (duplicate-on-kill, never silent-loss —
  the daily-report doctrine; hours here are informational, so no stamping).
  Window queries are ledger-side: delivered via `delivered_at ∈ [anchor, hi)`,
  rejects via the immutable `REJECT_TS` events fragment. Content (ruling 4):
  delivered/rejected counts + hours in window · cumulative per-game hours vs
  500 h · backlog depth (undownloaded / in-flight / fix / HOLD_VLM) · pace vs
  Aug-24 · fallback-model count (dossier `models_used` over the window's
  verdict sids from the events table — crash-proof, `_batch_fallback_count`
  pattern) · stuck list (non-terminal sids unchanged > `CONT_STUCK_H = 6` h,
  HOLD_VLM ages, new quarantines) · current pool target/active + rung if ≠ 0.
  Sent even when idle (heartbeat). Pure formatter `reports.build_digest_message`
  + `DigestStats`, byte-pinnable like the batch/daily templates.
  Post-deadline guard: after `DEADLINE_IST` the pace line is replaced by a
  plain totals line (pace.compute degenerates past the deadline).
- **Daily payment + folder-issues**: UNCHANGED functions, called from H only
  (single-threaded trigger — concurrency race class closed). Stamps→anchor→
  marker ordering untouched. **`CONT_DAILY_REPORTS` is the payment-endgame
  interlock** (r-loop 1 blocker): every rebuild-era root is unstamped, so
  one post-flip daily send's late-arrival guard would pull the whole cohort
  into one day's sheet, stamp it, misattribute the hours AND deadlock
  `recal_regen_sheets`' stray-stamp gate. The flip deploys it False; set
  True (+ redeploy + restart) only after the regen `--send` completes.
- The digest's window line also counts new quarantines; the stuck list
  excludes DISCOVERED (cap-throttled intake is normal) and ages HOLD_VLM
  from its FIRST hold event (each 30-min retry refreshes `updated_at`).
- **Alerts**: same `⚠️` surfaces; dedup becomes TTL-based
  (`CONT_ALERT_DEDUP_MIN = 60`, pruned) — a forever-process must re-raise
  persisting conditions and must not grow an unbounded sent-list.
- `hl-recal-watch` (the interim VM watcher) is superseded by the digest and is
  stopped at the flip, per its own docstring.

## 9. Resume semantics (the load-bearing property)

Kill -9 at ANY instant resumes exactly because every decision re-derives from
the ledger:

- No queue holds state: dispatchers re-poll `by_state`; `owned`/cooldowns are
  in-memory-only overlays whose loss means at worst one immediate retry.
- Startup sequence: `acquire_lock` (stale-lock reclaim unchanged — required
  after any kill -9, mapped risk) → `_finalize_orphan_rejects` →
  `_sweep_terminal_work` → dispatchers start. No batch regroup exists;
  `_partition_resume`'s subtle carry rules dissolve with the batch table. The
  per-state routing IS the resume: DOWNLOADING re-downloads (rclone
  idempotent), VALIDATING re-validates, FIXING gets `_recover_split` triage
  (all five kill-window classes a–f from the map are inherited via reuse of
  the existing functions, not reimplemented), READY/PACKAGED/UPLOADED resume
  inside `deliver_session`'s own state guards, hours recorded once at the
  DELIVERED transition.
- Digest/daily anchors: written after send → duplicates possible, loss not.
- The kill matrix (canary, step 5): kill -9 during download, validation, and
  upload; assert exact resume, no double-DELIVERED (events-table oracle), hours
  once, no stub rrd staged.

## 10. systemd & rollback

`pipeline/systemd/hl-continuous.service.in` (+ `hl-continuous-alert.service.in`),
following the existing template idioms (placeholders, pinned uv deps,
PYTHONUNBUFFERED, network-online):

```
Type=simple
Restart=always
RestartSec=10
StartLimitIntervalSec=600
StartLimitBurst=5          # exhaustion → unit 'failed' → OnFailure fires
OnFailure=hl-continuous-alert.service
StandardOutput=journal     # no tee: the dated-file pattern freezes at start
ExecStart=… python -m pipeline run-continuous
```

Restart=always makes OnFailure fire only at start-limit exhaustion — the
StartLimit pair (absent from every existing unit) is what keeps "failure is
always Telegram-visible" true; the alert text says crash-looping, points at
`journalctl -u hl-continuous`. Additionally the driver itself sends a startup
Telegram line, so a crash-loop is visible as repeated startup lines even
before exhaustion.

`vm_setup.sh`: the two new units join the install list; `--enable-timers` is
retargeted to arm `hl-backup.timer` only (arming the batch tick becomes an
explicit manual act, so a post-flip re-provision can never resurrect two
drivers). `hl-backup.*` unchanged — the driver keeps writing into
`backups/dossiers/reports`, so R19 holds.

**Rollback lever** (one paragraph, executable): `sudo systemctl disable --now
hl-continuous.service` → set `PIPELINE_CONTINUOUS = False` (+ deploy) → `sudo
systemctl enable --now hl-pipeline.timer`. Note: Persistent=true fires a
catch-up tick immediately. The batch driver then regroups any open batches —
which is why this design never touches them.

## 11. Config deltas (all in `config.py`, human-edit-only)

`PIPELINE_CONTINUOUS=True` · `CONT_SCAN_INTERVAL_S=300` ·
`CONT_MEDIA_CAP_SESSIONS=40` · `CONT_HOLD_RETRY_MIN=30` ·
`CONT_DIGEST_INTERVAL_H=3` · `CONT_POOL_MIN=8` · `CONT_POOL_MAX` (cores−12,
floor 8) · `CONT_AUTOSCALE_INTERVAL_S=60` · `CONT_CPU_HIGH=85` ·
`CONT_STEP_UP=2` · `CONT_STEP_DOWN=4` · `CONT_BACKPRESSURE_WINDOW_S=600` ·
`CONT_BACKPRESSURE_429_PER_MIN=1.0` · `CONT_RUNG_QUIET_RESET_MIN=60` ·
`CONT_ALERT_DEDUP_MIN=60` · `CONT_DOWNLOAD_WORKERS=1` ·
`CONT_UPLOAD_WORKERS=1` · `CONT_STUCK_H=6`.

## 12. Small hardening deltas ridden along (each justified by always-on)

- `scanner.py`: the decode deadline is enforced on the READ loop itself
  (select + os.read; a stalled decoder blocks a naive read() forever and a
  wait()-timeout never fires while it does), and the ffmpeg child is killed
  on expiry — a leaked/wedged decoder accumulates forever in an always-on
  process.
- `validate.py`: `_locked_report_update` lock patience scales with the pool
  band (the ~5 s give-up-and-write-anyway fallback was sized for 8 workers;
  at ~44 it re-opens the lost-update window).
- `cutter.py`/`fix.py`/`translator/video.py`: every ffmpeg/ffprobe call is
  timeout-bounded — a wedged helper must surface as a fix failure, never pin
  a runner slot forever.
- `translator/v2.py`: malformed session.json / frames.csv yields FAIL
  verdicts, never checker crashes (a crash misclassifies the session as
  "validation crashed" → QUARANTINED instead of an actionable reject);
  `translate_bundle_v2`'s shared-report write is atomic tmp+replace.
- On an unclean stop (threads outlive the drain grace) the run lock is
  deliberately KEPT — pid-reclaim by the next starter is the safe path;
  releasing it over live writer threads would let a second driver run
  against concurrent writes.
- The flip-time tools (`recal_refix_reset`, `recal_regen_sheets`,
  `recal_verify_tree`) ACQUIRE the run lock for their whole duration —
  a bare existence check left a TOCTOU where systemd Restart=always could
  start the driver mid-tool. `recal_regen_sheets`' stray-stamp gate is
  cohort-scoped (post-hi16 roots stamped by normal dailies never block the
  endgame; a stamped COHORT root aborts loudly for human reconcile).
- Digest/daily windows use `[lo, hi)` with persisted `hi` → seconds-granular
  timestamps never double-count boundary rows.

## 13. Invariants explicitly preserved (checklist for review)

F5 hold-not-pass (unchanged code path) · R23 ladder order/arming + sticky-
until-quiet (§4) · dup accept-earliest + tested deviation paths (§6) · R7
layout + 5 spec files (deliver untouched) · R17 20%/game/day + 15% floor
(serialized U) · R11/R12 + stamps→anchor→marker (functions untouched, single
trigger thread) · per-session state machine + events audit (all transitions
via set_state under single-owner) · qa-v2 final gate on staged bytes ·
dossier evidence + fixlog · R6 Drive-I read-only · R2 2-attempts · 70 s/3-
actions/etc. thresholds (config untouched) · kill-anywhere resume (§9).
