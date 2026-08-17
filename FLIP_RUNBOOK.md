# Canary → flip → endgame runbook (kickoff steps 5–8, operational detail)

Companion to `PIPELINE_CONTINUOUS_DESIGN.md`; exact command sequences the
continuous-pipeline session executes. Everything here was decided during the
build/review loop — do not improvise past it.

## Step 5 — canary (VM, alongside the still-running rebuild)

Run from the SIDE checkout `~/hl-gamedata-continuous-test` (the live
`~/hl-gamedata` stays untouched until flip — the rebuild's spawn workers
re-import from it).

1. Canary knob overrides, edited **in the side checkout's `config.py` only**
   (never the repo): `CONT_SCAN_INTERVAL_S = 60`, `CONT_MEDIA_CAP_SESSIONS
   = 4` (bounds intake from the live scan so the canary doesn't gorge on
   the rebuild's backlog or its Gemini quota), `CONT_DAILY_REPORTS = False`
   (a canary must never touch payment surfaces).
2. Environment: `HL_PIPELINE_HOME=~/hl-pipeline-test`,
   `HL_PIPELINE_TEST_MODE=1` (TEST-prefixed Telegram),
   `--dest-prefix=_pipeline_test` (Drive II test folder only).
3. Seeds: 2+ sessions copied into `~/hl-pipeline-test/work` with ledger rows
   INGESTED (real media from the live work dir is fine to COPY — never move)
   plus the live read-only Drive scan for discovery realism (media cap 4
   bounds what it pulls).
4. Green criteria: autoscale observed moving (pool line changes in logs with
   reasons); digest fires (TEST-prefixed); ≥1 session end-to-end DELIVERED
   into `_pipeline_test/` with verified remote bytes; scan/dup/heal paths
   error-free against the live listing.
5. **Kill matrix (3 legs, REAL kill -9):** during download, during
   validation, during upload. After each: relaunch, assert from the ledger —
   exact resume (state-partition pickup), no double-DELIVERED (events
   oracle: one DELIVERED event per sid), hours recorded once, no stub
   `session.rrd` staged or uploaded.
6. Teardown: stop the canary, `deliver.cleanup_test_folder` purges
   `_pipeline_test/`, remove `~/hl-pipeline-test`. Nothing in the real
   `~/hl-pipeline` may be touched by any canary step (forbidden by kickoff).

## Step 6 — the flip (Telegram announce before and after)

a. `sudo systemctl stop hl-recal-watch` (watcher first — its ENDED message
   would race the announcements; the repo copy now debounces, but the
   RUNNING process is the old single-sample one), then
   `sudo systemctl stop hl-recal-rebuild`. Progress persists in the ledger.
   Expect a stale `run.lock` — the tools and driver pid-reclaim it.
b. RAM re-check (`free -g` — last read 5 GB used of 125; c2d-highcpu-56 has
   112 GB), then: stop VM → `gcloud compute instances set-machine-type
   hl-pipeline-vm --zone=asia-south1-a --machine-type=c2d-highcpu-56` →
   start → `gcloud compute config-ssh --project=hl-gamedata-pipeline`
   (ephemeral IP moves) → full suite green on the VM.
c. **Pre-deploy config edit on the Mac (committed): `CONT_DAILY_REPORTS =
   False`.** This is the payment-endgame interlock — with every rebuild-era
   root unstamped, one 14:00 IST daily send would misattribute the whole
   cohort and deadlock the regen (r-loop 1 blocker). Then deploy HEAD to
   `~/hl-gamedata` (rsync incl. tolerance patches `a4f93de`), re-touch rrd
   stubs, `bash tools/vm_setup.sh` (installs hl-continuous units; does NOT
   arm anything).
d. `tools/recal_refix_reset.py` dry-run → review the JSON plan → `--yes`.
   The tool now ACQUIRES the run lock (stale-reclaim included). Any rclone
   moveto failure aborts pre-DB (rc=3) — reconcile manually before retry.
e. `bash tools/vm_setup.sh --enable-continuous` (arms hl-continuous.service
   + hl-backup.timer; verifies `is-enabled`). Watch the first hour: 429
   rate in `~/hl-pipeline/logs/vlm-pressure.jsonl`, autoscale decisions in
   journald, disk, first digest.

## Step 7 — payment endgame (as soon as the pre-hi16 cohort is terminal)

The digest's stuck/backlog lines tell when. Then, in order:

1. `sudo systemctl stop hl-continuous` (clean stop releases the lock;
   SIGKILL leaves a stale one — both fine, the tools reclaim).
2. `tools/recal_regen_sheets.py` (preview) → sanity-read both sheets →
   `--send`. Final invariant: anchor == `2026-08-16T05:32:50+00:00`.
3. Flip `CONT_DAILY_REPORTS = True` (commit on Mac) → rsync deploy →
   `sudo systemctl start hl-continuous`. Normal dailies resume from the
   regen-written anchor (contiguous windows preserved by construction).
4. Update `NOTE_FOR_D3.md`; purge old sheet copies from the GCS mirror
   after replacements verify.

## Step 8 — tree verify + LAST destructive act

Driver stopped again (the verify tool also takes the lock) →
`tools/recal_verify_tree.py` CLEAN (or every defect explained + fixed) →
delete `superseded-prerecal/` + `superseded-refix-*/` → restart driver.

## Rollback (any point after the flip)

`sudo systemctl disable --now hl-continuous.service` → set
`PIPELINE_CONTINUOUS = False` + deploy → `sudo systemctl enable --now
hl-pipeline.timer` (Persistent=true fires a catch-up tick immediately).
The batch driver regroups its own open-batch rows — which is why the
continuous driver never touches the batches table.
