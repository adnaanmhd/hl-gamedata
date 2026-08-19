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
b. **Resize E2 → C2D.** Pre-flight was VERIFIED against the project's own API
   on 2026-08-17 — do not re-derive it from docs, but do re-check quota if
   anything else was created in the region since:

   | Gate | Verified state |
   |---|---|
   | `c2d-highcpu-56` in `asia-south1-a` | offered, 56 vCPU / 114688 MB, not deprecated (also in `-b`, `-c`) |
   | `C2D_CPUS` quota, asia-south1 | limit 100, usage 0 → 56 fits |
   | aggregate `CPUS` quota | limit 100, usage 32 → 56 after the move, fits |
   | `minCpuPlatform` | **unset** — an Intel pin would have blocked the move to AMD EPYC outright |
   | boot disk | `pd-balanced` 250 GB, C2D-compatible; SSD_TOTAL_GB 250/500, unchanged |
   | `onHostMaintenance` | `MIGRATE`; C2D supports live migration, no change needed |

   **RAM check — do NOT read `free -g` naively.** The E2 balloon makes that
   number meaningless: it is a point-in-time reading of host confiscation
   that does not travel to C2D. Measured 2026-08-17 22:51 IST, the balloon
   was fully deflated (`balloon_inflate == balloon_deflate == 552697465`,
   net 0 GiB) and `free -g` read 2 GiB used / 101 free — while cumulative
   inflation over 27 h of uptime was **2.06 TiB**. So the same command
   returns anything between ~2 and ~94 GiB used depending on when you run
   it. Read the balloon instead:

   ```
   awk '/balloon_inflate/{i=$2} /balloon_deflate/{d=$2} END {printf "%.1f GiB\n", (i-d)*4096/1073741824}' /proc/vmstat
   ```

   Our real footprint is ~3.5 GiB anonymous + page cache, against 112 GiB on
   C2D — ample even at `CONT_POOL_MAX` = 56−12 = 44 workers (~11.6 GiB).

   Then: stop VM → `gcloud compute instances set-machine-type
   hl-pipeline-vm --zone=asia-south1-a --machine-type=c2d-highcpu-56` →
   start → `gcloud compute config-ssh --project=hl-gamedata-pipeline`
   (ephemeral IP moves) → full suite green on the VM → confirm the balloon
   is gone (`lsmod | grep balloon` empty, and the awk above unavailable/0).

   **Run the suite through the gate, never as a bare `pytest; echo $?`:**

   ```
   SUITE_FLOOR=745 bash tools/run_suite.sh \
       --with numpy==2.4.6 --with opencv-python-headless==5.0.0.93 \
       --with rerun-sdk==0.36.0
   ```

   The gate is valid with `CONT_DAILY_REPORTS` at EITHER value — the suite
   is knob-independent via a conftest autouse fixture (r-loop 8: 6c ships
   False committed, and 11 send-path tests went red on this exact pinned
   invocation run against that tree). The suppression itself is pinned by
   an explicit False-monkeypatch test.

   "Green" cannot mean "exit status 0". `run_continuous` ends its finally
   with `os._exit(0)` when it owns the process, and ~12 tests call it
   in-process, so a regression of the `install_signals` guard kills the
   interpreter mid-suite: **measured at 140 of 449 tests run, no summary
   line, exit 0** (r-loop 6). No pytest hook can defend against that —
   `os._exit` skips every hook — so the gate checks, from the parent, that
   a summary line exists AND that the pass count clears the floor. Raise
   `SUITE_FLOOR` as the suite grows; never lower it to make a run green.

   **Expected failure branch — zone capacity.** Availability and quota are
   settled, but neither rules out a stockout: `start` can return
   `ZONE_RESOURCE_POOL_EXHAUSTED`, and it can only surface once the VM is
   already stopped. This is not an error to debug live. Immediate undo:

   ```
   gcloud compute instances set-machine-type hl-pipeline-vm \
       --zone=asia-south1-a --machine-type=e2-standard-32
   gcloud compute instances start hl-pipeline-vm --zone=asia-south1-a
   gcloud compute config-ssh --project=hl-gamedata-pipeline
   ```

   Then continue the flip on E2 — the resize fixes the balloon (throttle
   cause A) and nothing else; R1–R3 fix the split cascade (cause B) and are
   independent of it. Neither is a prerequisite for the other. Retry the
   resize later, or use `-b`/`-c` (both offer the type) if a zone move is
   worth the disk snapshot+recreate; do not block the flip on it.
c. **Pre-deploy config edit on the Mac (committed): `CONT_DAILY_REPORTS =
   False`.** This is the payment-endgame interlock — with every rebuild-era
   root unstamped, one 14:00 IST daily send would misattribute the whole
   cohort and deadlock the regen (r-loop 1 blocker). It is a GAP-CLOSER,
   never policy: the flag returns to True IMMEDIATELY after step 7.2's
   regen `--send` completes (step 7.3). Then deploy HEAD to
   `~/hl-gamedata` (rsync), re-touch rrd stubs, `bash tools/vm_setup.sh`
   (installs hl-continuous units; does NOT arm anything).

   **The deploy set is FOUR things, and all four must be in it** (R4: none
   of them applies until this moment, by design — the running rebuild keeps
   judging under the old checkers so the refix population stays coherent;
   matches FLIP_HANDOVER §2):
   1. the continuous driver,
   2. the `a4f93de` fix-failed tolerance patches,
   3. **the split-cascade rulings R1–R3** (`KEEP_GATE_MAX_S` 5.0,
      `KEEP_GATE_MAX_FRAC` deleted, `SCANNER_STATIC_MIN_S` named),
   4. **ALL r-loop fix sets** (review loops 1–8 — everything committed on
      the flip branch since the design freeze).

   Verify on the VM after rsync, before arming — cheaper than discovering it
   from throughput a day later. If any check fails, the rsync did not ship
   what was tested — stop and fix before arming:
   ```
   cd ~/hl-gamedata
   grep -n "KEEP_GATE_MAX_S\|SCANNER_STATIC_MIN_S\|KEEP_GATE_MAX_FRAC" pipeline/config.py
   #   -> KEEP_GATE_MAX_S = 5.0, SCANNER_STATIC_MIN_S = 0.8, NO KEEP_GATE_MAX_FRAC
   grep -c "accepted_reported_at" pipeline/reports.py pipeline/ledger.py   # non-zero both
   grep -n "read_counted_record\|write_counted_record" tools/recal_regen_sheets.py  # both
   grep -n "first_pts_abs" translator/trim.py                              # trim-clock fix
   # r-loop 8 markers:
   grep -n "daily-counted.json" pipeline/run.py                            # C5 durable record
   grep -n "tree_sealed_at" pipeline/ledger.py pipeline/reports.py tools/recal_refix_reset.py
   grep -n "per_window" pipeline/gate.py pipeline/fix.py                   # C7
   grep -n "CONT_DIGEST_RETRY_S" pipeline/config.py pipeline/continuous.py # C4
   # r-loop 9 markers:
   grep -n "paid_pieces" pipeline/ledger.py pipeline/reports.py tools/recal_refix_reset.py  # D7 ruling C
   grep -n "pending_daily_send" pipeline/reports.py tools/recal_refix_reset.py tools/recal_rebuild_reset.py  # D7b interlock
   grep -n "carried_out" translator/trim.py pipeline/fix.py           # D1c
   ```
d. `tools/recal_refix_reset.py` dry-run → review the JSON plan → `--yes`.
   The tool now ACQUIRES the run lock (stale-reclaim included). Any rclone
   moveto OR lsf failure (rc other than 3/4) aborts pre-DB (rc=3) —
   reconcile manually before retry. It also refuses (rc=2) while any
   daily send is pending resume (r-loop 9 #7). Review
   `paid_pieces_to_record` / `skipped_sealed` in the plan JSON (ruling
   C, Adnaan 08-18: paid and mixed trees proceed with per-piece
   payment memory; `skipped_mixed`/`sealed_roots` stay [] — at the
   flip both are expected empty, zero payment stamps exist).
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
   `sudo systemctl start hl-continuous`. Do this IMMEDIATELY after the
   regen `--send` completes — the False was a gap-closer for the regen,
   never policy (Adnaan 08-18). Normal dailies resume from the
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
