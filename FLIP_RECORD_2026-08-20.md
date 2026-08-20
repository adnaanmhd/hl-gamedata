# FLIP RECORD — 2026-08-20 (executed per `FLIP_EXEC_KICKOFF_PROMPT.md`)

## VERDICT: FLIP LIVE — `hl-continuous.service` armed 17:53:21 IST (12:23:21Z) on a fresh ledger, Drive II wiped, Kamla-first + 500 h stop gate deployed

Preconditions held: e2e verdict GREEN-WITH-FINDINGS accepted (`E2E_VERDICT.md`);
Adnaan's explicit go in this session ("I give my go for the flip").
Code deployed: **`2a26abc`** (HEAD `d7c35d7` + this session's two commits: `eb213ef` gate + `2a26abc` U lanes). Both host
gates green on it: **Mac 860/856**, **VM 860/856** (399 s on the new shape vs 697 s
on the old e2). Nothing pushed.

## Deviations from the kickoff — each ruled by Adnaan in-session or forced by a fact

| # | Kickoff said | What happened | Ruled by |
|---|---|---|---|
| 1 | c2 56-CPU shape, "c2-standard-56 `[assumption]`" | Intel `c2-standard-56` does not exist in asia-south1-a (Intel C2 = 4/8/16/30/60) and `C2_CPUS` quota is 8. Ruled **`c2d-highcpu-56`** (runbook's pre-flighted shape). | Adnaan |
| 2 | resize to 56 vCPU | `start` FAILED: **global `CPUS_ALL_REGIONS` quota = 32** (the 08-17 pre-flight checked only regional CPUS/C2D_CPUS = 100). VM was TERMINATED with nothing running, nothing lost. Ruled: run on **`c2d-highcpu-32`** now (AMD EPYC 7B13, 62 GB, **no balloon module**, `CONT_POOL_MAX`=20) and re-resize when the quota lands. Quota preference **`cpus-all-regions-64`** filed (32→64), `reconciling` at report time. | Adnaan |
| 3 | "Kamla first, oldest-first" + a minimal stop gate | The shipped intake was **not** Kamla-first: `ingest.lagging_game` (F4) balances the two games toward 50/50 — pure createdTime FIFO on a fresh ledger, then OW-first the moment Kamla led by >10% (pinned by `test_ingest.py`). Implemented the gate with forced priority **and** the stop (§2 discipline, below). | kickoff step 8 clause |
| 4 | `bash tools/vm_setup.sh --enable-continuous` | Its r-loop-4 interlock would have **refused to arm** on a fresh home with `CONT_DAILY_REPORTS=True` (it required the old 08-15/08-16 `.regen-v2-done` markers). Retired in the same commit — its premise (unstamped rebuild cohort in this home) is void under the clean slate. | clean-slate ruling |
| 5 | Telegram announce before/after the flip | Not hand-posted (standing rule: outward comms I author need an explicit "send it"). The driver's own 🟢 start message and the first 💰 daily went to the chat as designed. Draft announcement at the end of this file. | memory rule |

## Sequence, with evidence

1. **Resize** — see deviations 1–2. After: `nproc`=32, `free -g` 62 GB, `lsmod | grep balloon` empty, `os.cpu_count()-12` → 20.
2. **Gates** — Mac `860 passed, ARMING GATE OK (floor 856)`; VM side checkout `860 passed in 399.02s — ARMING GATE OK (floor 856)`. Floor raised 846→856 (= 860−4, the standing rule); runbook §6b updated and a clean-slate amendment banner added to `FLIP_RUNBOOK.md`.
3. **Drive I measured** (read-only, `tools/drive1_snapshot/run.sh`, listing finished 11:41:38Z): **865.8 raw h / 2415 canonical sessions / 238 players**. Kamla **615.4 h / 1743 sessions** (Rukaiya+Tanzeela 264.3 h, Naman+Invoker 154.5 h, Nazmul+Efan+Amirul 129.9 h, Bisrambha+Samik 66.7 h; newest Kamla upload 08-19 16:36Z — Kamla collection appears to have stopped). OW **250.4 h / 672 sessions** (still growing: +25 h / +90 sessions since the 15:14 IST snapshot). 24 quarantine-class paths. `drive1-raw-hours-2026-08-20.csv` + `drive1-issues-2026-08-20.md` regenerated (the 15:14 copies are in the session scratchpad).
4. **WIPE DRIVE II** (11:55:58Z–11:56:03Z). Listed first: root held exactly `humynlabs/` (08-16/17/18-2026 → **710 Kamla sessions = the old ledger's 710 DELIVERED / 65.21 h**, 2428 objects, 207.5 GiB) and `superseded-prerecal/` (08-15/08-16, 248 sessions, 860 objects, 79.0 GiB); 3288 files, every filename one of our 5 spec files; no root files; no `_pipeline_test/`; nothing client-authored — matched the ruling's description, no contradiction. Remote identity triple-checked (`drive-deliver` team drive 0AG7V… ≠ `drive-collect` 0AILW…). Purged both with `rclone purge`; `rclone lsf -R drive-deliver:` → 0 entries. **Trash not emptied:** `rclone cleanup` returned 9 errors (404s) twice — most likely the service account lacks permanent-delete on the shared drive's trash; the 287 GiB sits in trash until Drive's 30-day auto-purge (a Manager can empty it in the Drive UI if pooled storage matters). Log: scratchpad `flip/drive2-wipe.log`, pre-wipe listings `drive2-pre-wipe-*.txt`.
5. **Fresh state** — old home → `~/hl-pipeline-archive-2026-08-20` (61 GB, untouched; ledger 710 DELIVERED / 65.21 h). Old-era GCS DR objects (4 ledger backups + pre-recal snapshot, 20.5k dossier files, 08-15/08-16 sheets; 1.34 GiB) moved server-side to `gs://hl-gamedata-pipeline-backups/archive-pre-flip-2026-08-20/` so the nightly copy-only backup cannot interleave eras. New `~/hl-pipeline` created empty by the deploy; carries `NEW_ERA_README.md` (plan, stop query, OW re-map record rule).
6. **Canary** (runbook §5, side checkout, overrides `CONT_SCAN_INTERVAL_S=60 / CONT_MEDIA_CAP_SESSIONS=8 / CONT_DAILY_REPORTS=False` in the side checkout only; `~/hl-pipeline-test`, TEST mode, `--dest-prefix=_pipeline_test`; **all Telegram sends captured at the process boundary** per e2e F1 — 5 captured, none delivered). Against the REAL Drive I listing: 2286 registered, 17 quarantined, 1 same-player duplicate, 114 incomplete; first pick = the oldest Kamla session by createdTime (**the gate, live**). Green criteria: `[autoscale] 8 -> 10 (queue 9 > active 8, cpu 83%; 429s/min 0.00)` (**F3 resolved**); digest fired; **3 DELIVERED into `_pipeline_test/`** with remote size+md5 verified, first-per-game force-rrd-sampled (414 MB real rrd); fix pass, edge trim and mid-clip splits exercised. **Kill -9 leg mid-validation** (6 VALIDATING + 1 DOWNLOADING, no verdict.json on any): relaunch pid-reclaimed the stale lock, startup states `VALIDATING:6 DOWNLOADING:1` (no row lost), all six re-validated via crash-triage, the download resumed through the media-cap carve-out; oracles after: exactly-one DELIVERED event per sid, hours only on DELIVERED rows. Teardown: `deliver.cleanup_test_folder` → `lsf` rc=3, canary homes removed, side-checkout config restored. (Attempt 1 was aborted for a bug in **my wrapper**, not the pipeline: it lacked the `__main__` spawn guard the real `pipeline/__main__.py` has, so spawned workers re-ran the driver → `RuntimeError … bootstrapping phase`. Fixed; nothing reached Drive II.) Canary observation worth carrying: **6 of 8 Kamla roots split** on 6–16 s loading/cutscene windows (all >5 s → R1–R3 cuts as designed) — the split tax for Kamla is real.
7. **Flip** — `/tmp/deploy_prod.sh`: HEAD tarball → `~/hl-gamedata`, every runbook §6c marker present, `INTAKE_GAME_*`/`CONT_DAILY_REPORTS=True`/`PIPELINE_CONTINUOUS=True` present, production tree hash `c6a941a6…` **identical** to the gated tarball, rrd stubs re-touched. `vm_setup.sh` (units installed, nothing armed) → `vm_setup.sh --enable-continuous` → `hl-continuous.service` **enabled + active 17:53:21 IST**, `hl-pipeline.timer` disabled, `hl-backup.timer` enabled (next 03:00 IST). First daily send fired within seconds (14:00 IST gate open): window `[08-19 08:23:24Z, 08-20 08:23:24Z)`, counted [] (fresh ledger), anchor written — the new era's first counted window, empty by construction; tomorrow's 14:00 IST sheet counts everything processed by then (late-arrival arm).
8. **Processing order** — deployed gate: `INTAKE_GAME_PRIORITY="kamla"`, `INTAKE_GAME_STOP_HOURS={"kamla": 500.0}` (`pipeline/config.py`), `ingest.closed_games / priority_game / next_batch`. Stop measure = `Ledger.delivered_hours("kamla")` (SUM over DELIVERED rows, split children included — the digest's "Kamla X/500"). **Binds at new intake only:** DOWNLOADING kill-resume and the media-cap carve-out re-pick rows already holding bytes (in-flight → finishes, overshoot accepted; excluding them would re-open the r-loop 6 intake stall). Worst-case overshoot ≈ the media cap's in-flight set (~40 sessions × ~0.35 h ≈ 14 h, ~3%). After the stop the S lane still registers new Kamla folders as DISCOVERED (they stay raw by ruling) — the digest's undownloaded count will include them. Tests: `pipeline/tests/test_flip_kamla_gate.py` (10; all red on the unfixed tree; three hostile mutants of the fixed tree each caught by ≥3 tests). **OW pre-mapping record:** `~/hl-pipeline/NEW_ERA_README.md` pins the events-table query and cutoff rule; a snapshot `ow-pre-satellite-<date>.tsv` is written at each report.
9. **Payment sheets** — start fresh from this ledger (first send above). Runbook §7 legacy endgame (anchor `2026-08-16T05:32:50+00:00`, regen), §6d `recal_refix_reset`, §8 superseded-tree deletion: **SKIPPED — superseded by the clean slate, as instructed.**

## Hour-one findings and the relayed instruction set (Adnaan via the sibling session `hl-gamedata-ab`, ~13:35Z)

Measured independently before the relay arrived: the **single serial U lane** (`CONT_UPLOAD_WORKERS=1`) delivered ~40 sessions/h (upload itself 26–119 s; packaging + queueing behind the lane up to ~30 min) against ~100 verdicts/h; READY grew to 40+, and because READY rows hold local media they filled the 40-session cap and **choked intake** (INGESTED fell to 1–4). CPU was saturated (load 40–71 on 32 vCPU; autoscale bouncing 10↔18 on the 95 % ceiling), but U was the binding wall. Three relayed items, each executed and labelled as relayed:

1. **`CONT_UPLOAD_WORKERS` 1→4, `CONT_MEDIA_CAP_SESSIONS` 40→80, floor lock** — commit **`2a26abc`**. Finding while implementing: the §1.4 15 %-floor race is a *stale-count* problem, not only a lock problem (parallel lanes decide minutes before any reaches DELIVERED, so all read the same count); the fix counts in-flight decisions (PACKAGED/UPLOADED rows, READY rows already marked sampled) under `deliver._FLOOR_LOCK` and records the decision before generation. 4 tests (2 proven red pre-fix; lock-removed and count-reverted mutants both caught). Gates: **Mac 864/860, VM 864/860**. Deployed (tree hash `06110bbd…` = tarball), `systemctl restart hl-continuous` 13:56:28Z → clean drain 2 m 16 s, no in-flight row lost, cap 80 live. Result within 8 min: 4 PACKAGED at once, deliveries 9 per 5 min (was 3–4), READY 39→26 and draining, intake resumed, rrd share 20.6 % (floor ≥ 15 % holds).
2. **Quota requests** — `cpus-all-regions-64` raised to preferred **128** (granted 32, reconciling); new `c2d-cpus-asia-south1-128` — **GRANTED 128** already. Target when the global lands: `c2d-highcpu-112` (interim 56 as soon as ≥ 56 is granted).
3. **5 orphaned canary spawn workers killed** (env-verified `HL_PIPELINE_HOME=~/hl-pipeline-test`, ppid 1; leftovers of the kill-9 leg — spawn children outlive a SIGKILLed parent); production driver untouched.

## Throughput — measured (1.67 h since arming, 12:23:21Z → 14:03:31Z, includes ramp + one restart)

| measure | value |
|---|---|
| DELIVERED | 63 sessions, **8.26 Kamla h** (all Kamla, as ruled) |
| roots fully settled | 34 → 8.51 raw h in, 8.04 delivered h out — **delivered/raw 0.945** |
| roots judged (first verdict) | 44 → 11.48 raw h (≈ 6.9 raw fh/h at the validation stage, CPU-bound) |
| split tax | 2.81 nodes per judged root; depth up to 3; 38 mid-cuts all ≥ 6 s (loading/cutscene), 7 edge trims |
| rejects | 4, all genuine: short children where the cut left no ≥ 70 s segment (`split produced no >=70s segment`) |
| VLM pressure | 0 × 429, 4 × 503 absorbed by backoff; ladder never stepped |
| crashes | 0 `runner crashed`, 0 alerts (the journal's `Traceback` lines are CPython finalizer warnings on autoscale step-downs — cosmetic) |
| disk | 152 GB free; `work/` 15 GB |

**Projection (labelled):** on the c2d-32 the validation stage judges ≈ 6.9 raw fh/h ≈ **165 raw fh/day** `[measured over 1.67 h of ramp]`; with the U lane unblocked, delivered hours should approach that × 0.945 ≈ 155 h/day `[projection]`. Kamla needs ≈ 530 raw h for 500 delivered → **≈ 3.3 days from now ≈ Aug 24 morning** on this box `[projection — re-measure after a full unattended day]`; the 56/112-CPU resize is the lever once the global quota lands (CPU is the ceiling; Gemini is not — 0 × 429 at 12–18 workers). Pre-flip band for c2d-56 was 120–180 fh/day conservative.

**Kamla stop projection:** 500 h at 0.945 → the gate closes intake after ≈ 530 raw h judged; overshoot ≤ the in-flight set (≈ 80 sessions × ~0.2 h ≈ 15 h worst case with cap 80).

## Payment-surface list of record (plan §6) — honest labels

Unchanged since the e2e verdict: **O1** verified by the e2e run; **M5**, **L2-adjacent** verified live by the e2e; **N3/N4** indirectly; F6, F7+r12#1/#2+G1+H1, G5+H3/H9a, C6-era tests, I1+I2, I7, fix_sync_from_v1, J5, J6, J2, J1, K1, L1, M4 reviewed by loops ≤21, **not re-verified by this session**. This session added **no payment-code change**; it changed intake ordering (`next_batch`) and retired a shell interlock. The new-era payment history starts at anchor `2026-08-20T08:23:24Z`.

## QUEUED: OW `satellite_camera`

RULED 08-20 — implemented AFTER the flip in its own session with its own adversarial review (`SATELLITE_KICKOFF_PROMPT.md`). OW processes in queue order after the Kamla stop; every OW session delivered before the mapping lands is recoverable from the append-only events table (query + cutoff rule in `NEW_ERA_README.md`; snapshot file per report).

## Open items / watch list

- **Re-resize to `c2d-highcpu-56`** once `gcloud beta quotas preferences describe cpus-all-regions-64` shows `grantedValue: 64`: stop unit → stop VM → `set-machine-type c2d-highcpu-56` → start → `config-ssh` → `systemctl start hl-continuous` (pool ceiling is import-time; 44 workers). Note the canary saturated 32 vCPU (load 44) at 8–10 concurrent validations — CPU, not Gemini, was the first ceiling; 56 vCPU should lift it, watch `CONT_CPU_HIGH`.
- **Reject-label surface to glance at:** the 4 rejects carry `fixable:true` stored reasons with an unfixable OUTCOME (`split produced no >=70s segment`) — check how tomorrow's sheet labels them (M5 filters on the stored fixable field).
- **F2 reject signature** (v1-sniffed payload, `QA_FAIL_UNMAPPED missing delivery file: session.rrd/rrd_creation.py`): none seen yet; grep the ledger's `reasons_json` daily.
- Drive II trash (287 GiB) — auto-purge 30 d, or a Manager empties it.
- Mac `rclone` remote `drive-collect` is configured with `scope = drive` (the VM's is `drive.readonly`) — hardening candidate, not touched.
- Deadline rollover 08-24 23:59 IST: `pace.compute` floors days_left at 1e-6 → the daily message's pace line will look absurd after the deadline (digest already retires its pace line). Cosmetic; flag for a ruling.

## Draft Telegram announcement (NOT sent — say "send it" if wanted)

> 🚀 Pipeline flip done 17:53 IST. Drive II was wiped (clean slate), the continuous driver is live on a fresh ledger and is processing Drive I Kamla-first, oldest-first, until 500 delivered Kamla hours, then OW. Payment sheets restart from today's (empty) sheet. VM: c2d-highcpu-32 for now (56-CPU quota request pending).
