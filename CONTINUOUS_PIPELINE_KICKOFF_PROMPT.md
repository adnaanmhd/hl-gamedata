# Kickoff — continuous pipeline rewrite → canary → flip (+resize) → finish backlog → payment endgame

You are starting a fresh session in `/Users/adnaan/Documents/hl-projects/hl-gamedata`
(the repo moved here 2026-08-17 under the hl-projects ruling — old `~/Documents/hl-gamedata`
paths in older docs are stale). The pipeline runs on the GCP VM `hl-pipeline-vm`
(asia-south1-a, project `hl-gamedata-pipeline`, ssh alias
`hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline`; if ssh fails after any VM stop/start,
re-run `gcloud compute config-ssh --project=hl-gamedata-pipeline` — the ephemeral IP moves).
Read `PIPELINE_IMPLEMENTATION_PLAN.md` §4–§6 and `PIPELINE_ARCHITECTURE.md` before code.
Suite baseline: **323 passed on Mac AND VM** at HEAD (includes `a4f93de` tolerance patches +
endgame tooling and `38ff9e0` permanent-workers commit).

## Why this session exists (Adnaan's rulings, 2026-08-17 afternoon — locked, do NOT re-ask)

The batch architecture is retired. Adnaan ruled:

1. **No more batches.** Continuous flow: keep downloading whatever lands in Drive I and
   push each session straight into processing. Batch size, ≤3-in-flight slots, per-batch
   fix loops, per-batch Telegram toplines — all gone.
2. **Continuous download**: poll Drive I every **5 minutes**; local media cap **~40
   sessions** (disk low-water 100 GB stays, F7); wipe-after-terminal per session
   (R5's storage discipline survives as a per-session bound, not a batch).
3. **Max out workers — adaptive autoscale.** Scale the validation pool automatically
   (suggested band ~8 → cores−12, tune freely) on CPU% + queue depth, **with Gemini-429
   backpressure**: sustained 429-ladder activity steps the pool DOWN before models degrade.
   Knobs config-visible.
4. **Telegram digest every 3 hours, emitted from inside the driver** (survives restarts).
   It REPLACES per-batch toplines. Content: delivered/rejected counts + hours since last
   digest; cumulative per-game delivered hours vs the 500 h targets; backlog depth
   (undownloaded / in-flight / fix / HOLD_VLM); pace vs Aug-24; fallback-model count;
   anything stuck. The 14:00 IST daily payment report, folder-issues report, and
   real-time alerts (disk/crash/quarantine/upload-failure) are UNCHANGED.
5. **Per-session fix scheduling** (replaces batch-scoped loops): a FIX_QUEUED session
   re-enters the pool immediately, R2's 2-attempts-then-reject unchanged. The "parked
   fix-tail" class (children born after their batch's loop closed) must become
   structurally impossible.
6. **HOLD_VLM: one retry per 30 minutes, forever** (preserves F5's per-run cadence).
7. **Scheduler: always-on systemd service** (Restart=always, crash alert via the
   existing alert unit pattern), replacing the 30-min timer. Keep the OLD timer+batch
   driver code intact and dormant this phase — **rollback = stop the continuous service,
   re-enable `hl-pipeline.timer`**. Deleting batch code is post-Phase-1 cleanup.
8. **VM resize AT THE FLIP: e2-standard-32 → c2d-highcpu-56** ($1.385/h ≈ $33.3/day
   [web, asia-south1]; C2D quota already open at 100 vCPUs; N2D is quota-0 — Adnaan may
   file the free N2D request separately for a later 64-core step-up; Intel N2 rejected).
   **Gate: read actual RAM usage on the VM first** (c2d-highcpu-56 has 112 GB vs current
   128; expected fine, verify, don't assume). One stop→resize→start covers the flip.
9. **Canary then flip, and the flip does NOT wait for the rebuild backlog to drain.**
   The recal rebuild currently running keeps grinding untouched while you build. When
   your canary is green: stop the rebuild (progress is fully preserved in the ledger —
   kill-resume is proven; only in-flight work is redone), resize, deploy, run the refix
   reset, and relaunch on the continuous driver, which inherits the ledger mid-backlog
   and finishes everything — remaining downloads, the parked fix-tail, the selective
   refix set — under the new tolerances. Zero idle time.
10. **This session owns EVERYTHING from the canary onward**, including the payment
    endgame (below). The prior session (the recal-rebuild session) keeps its monitors
    running and watches until your final report lands — do not kill its watchers, do not
    expect it to act. Its 45-min pulse monitor and drain watcher poll read-only over ssh;
    they are harmless to you.

## Ground rules (unchanged, load-bearing)

- Machine-wide CLAUDE.md: verify before claiming; read whole sources; mark `[assumption]`.
- Commits path-scoped per green step; NEVER push; never touch the Obsidian vault.
- Secrets: `~/.config/hl-gamedata/secrets.env` (Mac + VM). Never print/log/commit keys.
- **Drive I (`drive-collect:`) read-only forever (R6).**
- Full suite after every step, Mac AND VM for code that ships:
  `PYTHONPATH=. uv run --with pytest --with numpy --with opencv-python-headless --with rerun-sdk pytest pipeline/tests translator/tests -q`
  (VM variant pins numpy==2.4.6 opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0.)
- Deploy: `rsync -a --delete --exclude 'out/' --exclude '__pycache__/' --exclude '*.rrd' ./ hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline:hl-gamedata/`
  then re-touch rrd stubs (see REVALIDATION_KICKOFF_PROMPT.md for the loop).
- Review protocol (Adnaan's ruling 08-17, supersedes the default Q17 shape — exact
  terms in step 4): adversarial review→fix loop, ≤5 iterations, EVERY iteration runs
  the full composition; leftovers after 5 go to Adnaan; then independent REAL e2e
  verification. Plus a REAL kill matrix on the canary (kill -9 during download,
  validation, upload → exact resume, no double-DELIVERED, hours once, no stub rrd).
- Invariants that survive the rewrite untouched: F5 (nothing passes unlooked-at;
  VLM failure → HOLD_VLM), R23 ladder semantics (sticky rungs — redefine "sticky scope"
  sensibly for a continuous process: sticky until a quiet period, document it), duplicate
  rule (md5 accept-earliest-ctime — mind scan-ordering under 5-min polls), R7 delivery
  layout + 5 spec files, R17 rrd 20%/game/day, R11/R12 payment semantics, stamps→anchor→
  marker ordering, per-session state machine + events audit (extend states only if
  unavoidable), qa-v2 final gate, dossier evidence.

## Current state you inherit (verified ~15:45 IST 08-17 — RE-QUERY, it drifts)

- **Recal rebuild live** on the VM: transient unit `hl-recal-rebuild` (driver pid ~9757,
  `run --quiet`, 10 workers, 32 vCPU). Backlog at last read: ~100 undownloaded recordings
  (~190 GB ≈ 36 fh) + ~30 fh in flight; ~92-row parked fix-tail (mostly SYN_TS_NOT_PTS —
  the strict-checker class the tolerance patches address); ~275 DELIVERED rows so far;
  rejects single-digit (the 99.5% dead-black rule holds). `hl-pipeline.timer` DISABLED;
  `hl-backup.timer` armed (03:00 IST).
- **Tolerance patches are COMMITTED but NOT DEPLOYED** (`a4f93de`): gate row-pad ±2,
  dx/dy run≥3-or->0.5% tolerance. Deploy at flip, never before (the running rebuild must
  keep judging under the old checkers so the refix population is coherent).
- **One-shot tools (committed, deep-review-hardened, unexecuted):**
  - `tools/recal_refix_reset.py` — run at flip (dry-run first, then `--yes`): resolves
    fix-failed rows to TOP-level roots, tears down full subtrees, **moves delivered
    siblings' Drive II dirs to `superseded-refix-<stamp>/`** (paths from UPLOADED
    events), then resets. Any rclone move failure aborts before DB changes.
  - `tools/recal_regen_sheets.py` — the payment endgame. PREVIEW first (side-effect-free),
    then `--send`. Cohort-scoped gate: only non-terminal trees whose ROOT upload time
    < 2026-08-16T05:32:50Z block. Windows of record are inside the file. Resume records
    (`.regen-v2-done` / `.regen-v2-counted.json`) make re-runs safe; send-before-stamp;
    final invariant anchor == 2026-08-16T05:32:50+00:00.
  - `tools/recal_verify_tree.py` — Drive II tree↔ledger verifier (events-based paths,
    exact per-dir file sets, rrd-aware). Must be CLEAN (or every defect explained and
    fixed) before any deletion.
- **Drive II (`drive-deliver:`)**: `humynlabs/` holds ONLY rebuild-era uploads.
  Pre-rebuild trees live at `superseded-prerecal/{08-15-2026,08-16-2026}` (466+394
  objects, byte-verified). After the refix pass there may also be `superseded-refix-*/`.
  **Deleting `superseded-prerecal/` + `superseded-refix-*/` is the LAST destructive act**,
  only after `recal_verify_tree` is clean and sheets are sent. Client has pulled nothing.
- **Payment state**: pre-rebuild sheets for 08-15/08-16 are VOID (Adnaan's ruling; a
  start message already told Telegram). The ledger parachute
  (`~/hl-pipeline/backups/pre-recal-rebuild-20260816T140745Z.db` + GCS copy) and
  `prerecal-snapshot.json` (same dir + GCS; also on the Mac at the old session's
  scratchpad) hold the pre-rebuild per-player hours for the delta table.
  `reject-reasons-pre-rebuild.json` (repo root, committed) is the step-8 comparison
  baseline (138 rows / 26.3 h / 113 recordings, black-frozen on 126). Note the drifted
  kill-time snapshot too: 149 rows / 28.42 h / 126 recordings, black-frozen SOLE-reason
  partition = 26.24 h of 28.42. Present both, sourced.
- **hl-pipeline.service** (installed + template) already carries HL_PIPELINE_WORKERS=10;
  that unit stays as the dormant ROLLBACK path.

## Steps, in order

1. **Design doc first** (committed): the continuous driver's concurrency model, state
   ownership, autoscale law (CPU + queue + 429 backpressure), disk bound, dup-ordering
   under polling, HOLD_VLM/fix scheduling, digest, resume semantics (kill at ANY instant
   resumes exactly from the ledger — this is the load-bearing property), rollback lever.
2. **Implement** behind a config flag (`PIPELINE_CONTINUOUS=True` style), old driver
   dormant-intact. Unit files: `hl-continuous.service` (+ digest inside), alert wiring.
3. **Tests** (suite green both hosts) incl. a REAL pool test and a
   `python -m pipeline run-continuous` smoke with ≥2 seeded sessions (spawn semantics —
   pytest cannot see `__main__` re-import failures; see plan §6).
4. **Adversarial review → fix loop (Adnaan's ruling, 08-17 — exact terms):**
   - **Maximum 5 iterations.** EVERY iteration (all five, no lighter late rounds) runs
     the full composition: **deep FULL-CODEBASE review** (pipeline/ + translator/ +
     tools/, not just the diff) **+ delta review of everything changed since loop
     start + adversarial hunting for bugs/issues introduced by the loop's own
     changes and fixes**. Multi-agent lanes with findings adversarially verified
     (2-vote refute discipline) before they count.
   - Fix confirmed findings each iteration, suite green both hosts, commit
     path-scoped per iteration (r-loop message style, cite this prompt).
   - The loop EXITS EARLY only when an iteration ends with zero confirmed findings
     and nothing left to fix. **Anything verified-but-unfixed still standing after
     iteration 5 is highlighted to Adnaan, severity-ordered, before proceeding.**
   - Only after the loop exits clean (or Adnaan acknowledges the leftovers): run the
     **independent REAL end-to-end verification** — a fresh agent that wrote and
     reviewed none of this code, exercising the actual system (real VLM calls, real
     Drive II `_pipeline_test/` uploads purged after, real kill/resume) and reporting
     whether everything works as expected; its verdict is relayed VERBATIM and a
     BLOCKED-with-error never becomes a pass. This is in ADDITION to the step-10
     production verifier at the very end.
5. **Canary**: `HL_PIPELINE_HOME=~/hl-pipeline-test`, test-mode Telegram, Drive II
   `_pipeline_test/` only (purge after via `deliver.cleanup_test_folder`), seeded
   sessions + the live (read-only) Drive scan; 3-leg kill matrix; autoscale observed
   moving; digest fires. Deleting/cleaning ANYTHING in the real pipeline home from the
   canary is forbidden.
6. **THE FLIP** (announce on Telegram before and after):
   a. Stop `hl-recal-rebuild` (progress persists; expect stale `run.lock` — remove after
      verifying its pid is dead).
   b. RAM check, then resize: stop VM → `set-machine-type c2d-highcpu-56` → start →
      `config-ssh` refresh → suite green on VM.
   c. Deploy HEAD (tolerances included), re-touch rrd stubs.
   d. `tools/recal_refix_reset.py` dry-run → review its plan output → `--yes`.
   e. Launch `hl-continuous.service`. It finishes the backlog + refix set + fix-tail
      under the new tolerances at 56 cores. Watch the first hour closely (429 rate,
      autoscale behavior, disk).
7. **Payment endgame** (as soon as the pre-hi16 cohort is terminal — the digest's
   stuck-list tells you; chase HOLD_VLM/zip-stall cohort rows to terminal first):
   `recal_regen_sheets.py` preview → sanity-read both sheets → `--send`. Update
   `NOTE_FOR_D3.md` (payment-sheet artifacts + the obsolete repo-root
   `payment-2026-08-15.csv` reference — that file is Adnaan's, flag don't delete).
   Purge old sheet copies from the GCS mirror after replacements verify.
8. **Tree verify + deletion**: `recal_verify_tree.py` clean (reconcile any stale dirs —
   expected class: pre-refix child dirs superseded by re-derived child sets) → delete
   `superseded-prerecal/` and `superseded-refix-*/` (LAST destructive act).
9. **Reject-reason table**: exhaustive reason×count (+hours) over the final ledger vs
   BOTH baselines (committed file + kill-time snapshot). Decision input for Adnaan —
   present, don't act.
10. **Independent live verifier** (fresh agent, never one that wrote/reviewed this code;
    verdict relayed VERBATIM): suite both hosts; continuous-driver kill-resume spot
    check; ledger consistency (every non-quarantine/dup row terminal, hours counted
    once, no stub rrd staged); Drive II ↔ ledger exact match + superseded trees GONE;
    sheets exist for both days + anchor/markers/stamps coherent + old sheets purged;
    digest firing on schedule; autoscale + 429 backpressure observed; secrets sweep
    (counts only); `_pipeline_test/` purged.
11. **Final report** (verdict-first): before/after state table; per-player delivered-
    hours delta (payment impact vs the parachute snapshot); reason×count table;
    sheets sent; Drive II repopulation + deletion proof; continuous-driver throughput
    measured (unique-collected fh/day — count the split tax honestly: batch-era
    measurement was 1.76×) vs the ~119 fh/day Aug-24 pace; verifier verdict verbatim;
    open items carried forward: Gemini billing tier unverified (vault OPEN
    CONTRADICTION), credential rotation deferred (Adnaan 08-16), vertex failover dark,
    `_seed_shift_record` exact-equality can burn one fix attempt (minor),
    torn verdict.json in nightly DR mirror (cosmetic), 4 stale open batches pre-reset
    (bookkeeping wart), lagging-game ordering note, and the CONTESTED 1–1 review finding
    on `video_active` dark-footage interplay (mechanism confirmed, delivery-impact
    refuted via the ≥3-actions gate; Adnaan may want a ruling).

## Numbers of record (for context, all measured this weekend)

- Dead-black recalibration: luma<5, reject ≥99.5% (Adnaan; two evidence videos measure
  13.4% / 6.7%). Old rule mass-false-positived 26.24 h (sole-reason partition).
- Throughput: 16 vCPU/8w = 26.3 val-min per PROCESSED fh; 32 vCPU/10w = 12.0 processed /
  **21.2 per UNIQUE collected fh (split tax 1.76×) ≈ 68 fh/day**; mission pace needs
  ~119 fh/day; c2d-highcpu-56 + continuous + autoscale is the answer to that gap.
- Yield: pre-rebuild 41.4% → rebuild-to-date ~96% on settled footage.

Begin with step 1. Verify every line number and count cited here against the current
tree and live ledger first — they drift.
