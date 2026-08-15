# Phase-1 Gaming Data Pipeline — Implementation Plan

Single source of truth for building and running the automated
collection→validation→post-processing→delivery pipeline. Everything here is either a
**verified fact** (source cited), a **locked ruling** (Adnaan, dated), or a **fiat decision**
(mine, technically motivated, veto-able — collected in §16). Nothing else is binding.
Written 2026-08-14; **revised 2026-08-15 (v2)**. Deadline governs everything: **1000 delivered
hours by 2026-08-24**.

**What changed in v2, very simply.** The pipeline moves from Adnaan's Mac to a rented computer
inside Google's datacenter (a GCP VM in Mumbai), because the home internet line made the deadline
arithmetic impossible (measured: the Mac tops out at 67–78 footage-h/day vs ~110+ needed —
`BOTTLENECK_FINDINGS.md`). On the VM, copying files to/from Drive is like moving boxes across a
hallway, so the only job that still takes real time is *checking* the videos. Three additions make
the checker never wait: (1) an **overlap driver** — while batch N is checked, batch N+1 downloads
and batch N−1 uploads; (2) the **workers knob turned up** until the VM's cores are ~full;
(3) a **Vertex failover** for Gemini so one Google-endpoint outage stops stalling sessions;
(4) a **Gemini quota ladder** (R23) — if 3.7-flash is rate-limited, the run steps down to
3.5-flash, then 3.1-pro, then tries the previous API key, before ever parking a session.
Landing zone: **~172–240 footage-h/day**, capped by Drive's own 750 GB/day upload limit — about
1.3–1.8× the required pace. **Steps 0–6 of the build are already implemented and tested; the rule
of this revision is REUSE — the delta is one driver, one VLM client change, and provisioning.**

---

## 1. Mission & context

- **Client chain**: Humyn Labs (vendor, `humynlabs`) → Protege (intermediary) → **Odyssey** (client).
  Protege imposes no requirements beyond Odyssey's spec (vault ruling, 2026-08-09).
- **Deliverable**: gameplay sessions in **Game Data Capture Spec v2** format
  (`latest_requirements/v2_Game_Data_Capture_Spec.pdf`, 07/08/2026). Camera columns stay null
  (decided 2026-08-09: we do not service the camera requirement).
- **Phase 1 volume**: **500 delivered hours per game** — Kamla and Outer Wilds, **only these two**.
  Surplus counts for nothing. Collection target **600 h/game** (over-record buffer for rejects).
  Phase 2 (larger) is out of scope for this document.
- **Capture**: ≥150 players record with `HumynCapture_v1` (built with agency MHXP, exe dated
  2026-08-13). The tool natively emits v2 delivery folders. Recording **already started 2026-08-14**
  (Drive I still had zero files at 08-14 22:41 IST — collection ramp is the other clock).
- **Collection model**: clan-chief. Operator (clan chief) manages players (clan members). Uploads
  land in **Shared Drive I (collection)**; deliverables go to **Shared Drive II (delivery)**.
  Verdicts drive **per-player payment** (accepted hours; rejected = $0).
- **Runtime host (R19, revised 2026-08-15)**: a **GCP VM — `e2-standard-16` (16 vCPU / 64 GB),
  on-demand, region `asia-south1` (Mumbai), 250 GB pd-balanced disk** — running 24/7 in project
  `hl-gamedata-pipeline`. **The Mac becomes control-plane only**: development, `git` history,
  Telegram reading, report sharing; it holds no live pipeline state. Straight-to-VM go-live —
  the pipeline never goes live on the Mac (R19; Drive I was empty, so nothing waits on migration).
- **Why a VM works economically (verified)**: Google does not charge a VM for Drive traffic —
  "Data transfer to specific Google products such as Gmail, YouTube, Google Maps, DoubleClick, and
  Google Drive, whether from a VM in Google Cloud with an external IP address or an internal IP
  address: No charge" [web: cloud.google.com/vpc/network-pricing, fetched raw 08-14]. Full cost
  math in §15.

## 2. Systems & identifiers (verified 2026-08-14 unless marked "created at §7")

| Thing | Value |
|---|---|
| Collection Shared Drive (I) | `0AILWuC6lcBKLUk9PVA` (created 08-14, reachable) |
| Delivery Shared Drive (II) | `0AG7V2qXT35aQUk9PVA` (created 08-14, reachable) |
| Drive I layout (**amended 08-15**) | `{kamla|outer_wilds}/<operator_NAME>/<player_email>/<session_folder>/` — game folders sit **directly at the drive root** (created by Adnaan). **Operator folders are free-text names (Adnaan 08-15, supersedes the operator half of Q5)**; player folders remain **strict emails**; session folders remain the strict id pattern. The name-folders seen 08-14 (`kamla/Rukaiya+Tanzeela`, …) are now valid by design (§17.6) |
| Session folder name | `<UTC-timestamp>_<game>_c_<hex16>` (contributor id = HMAC of player email, computed in-tool) |
| Telegram bot | `@ozark_updates_bot`, token verified; chat id `1207316330` captured; end-to-end test DM delivered 08-14 |
| Service account | `pipeline-runner@hl-gamedata-pipeline.iam.gserviceaccount.com`, key at `~/.config/hl-gamedata/sa.json` (600) on Mac, copied to the VM at §7.4; **verified member of both drives**. `rclone about` yields nothing for SAs on Shared Drives — quota is watch-at-runtime with upload-failure alerts |
| GCP project | `hl-gamedata-pipeline` (billing enabled — Adnaan, 2026-08-15) |
| **VM** (created at §7.2) | `hl-pipeline-vm`: `e2-standard-16`, on-demand, `asia-south1`, Debian 12, 250 GB pd-balanced, no public inbound except SSH via `gcloud compute ssh` |
| **GCS backup bucket** (created at §7.2) | `gs://hl-gamedata-pipeline-backups` (or suffixed if name taken), `asia-south1`, Standard class, uniform bucket-level access, private; `pipeline-runner` SA granted `roles/storage.objectAdmin` **on this bucket only** |
| VLM | `gemini-3.7-flash` (model of record, R13 as amended by R23), endpoints `generativelanguage.googleapis.com/v1beta` + Vertex AI express `aiplatform.googleapis.com/v1/publishers/google` (R21). **§7.6 smoke matrix run on the VM 2026-08-15 ~14:00 IST**: genlang answers BOTH keys (new 1.5 s, prev 8.8 s first-call from Mumbai); vertex is **403 `API_KEY_SERVICE_BLOCKED` for BOTH keys** (the keys are restricted from aiplatform.googleapis.com) → `VLM_FAILOVER_ENABLED` stays **False** per §7.6's vertex-dead outcome; prev-key rung ARMED. Ladder ids **verified by generateContent probes on both keys**: `gemini-3.5-flash` OK; assumed `gemini-3.1-pro` does NOT exist (404) — corrected to **`gemini-3.1-pro-preview`** (probed OK both keys; config commit) |
| Secrets | `~/.config/hl-gamedata/secrets.env` (chmod 600, outside repo) on Mac AND VM: `GEMINI_API_KEY` (new, 08-15), `GEMINI_API_KEY_PREV` (old, R23 last resort), `GEMINI_MODEL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DRIVE_COLLECTION_ID`, `DRIVE_DELIVERY_ID`. All credentials rotate after Phase 1 (they lived on a cloud box, and both Gemini keys have been pasted into chats) |
| Installed (Mac) | ffmpeg/ffprobe, uv, rclone — all present 08-14. **gcloud SDK installed + authed (`adnaan@humynlabs.ai`), project set — done 08-15**; the §7.1 quota check is still pending |
| Installed (VM, at §7.3) | ffmpeg (apt), rclone (rclone.org install script — apt's is stale), uv, repo copy, Python deps via `uv run --with numpy --with opencv-python-headless --with rerun-sdk` |
| Tool bitrate (08-13 build) | video ≈ 0.72–0.74 MB/s (~2.6 GB/h); `session.rrd` ≈ 1.01× video size (measured on 2 local sessions) |

## 3. Sources of truth & precedence

1. **Adnaan's rulings** (§4, §5) — override everything below.
2. **v2 spec** — the client contract. Key sections: §1.1.1 upload path, §1.1.2 file structure,
   §1.1.2.1 session.json schema (16 required fields, timezone-aware ISO-8601, platform enum,
   BCP-47 localization, `input_mouse_convention`), §1.1.3 36-column frames.csv, §1.4 rrd (15% rule),
   §1.5 validation checks. Notable verified facts: **no audio requirement anywhere**; **no minimum
   resolution** (schema minimum 1 px); §1.5.2 requires consistent frame spacing; §1.5.5 couples
   `input_keys`↔`input_actions` (every key token must yield a non-null action).
3. **`translator qa-v2`** (`translator/v2.py::check_session_v2`) — our reference validator, mirrors
   §1.5 plus client-derived gates (≥70 s, controls-to-video grounding, PTS frame-sync ≤100 ms,
   same-literal fan-out). The client's own `qa_checks.py` is NOT authoritative here (it FAILs on
   legal empty camera columns).
4. **`SAMPLE_ANALYSIS_PLAYBOOK.md`** — check battery & history; automated as
   `tools/analyze_sample.py` (sibling session, 08-14).
5. **Capture-defect briefs** — `HumynCapture_Capture_Tool_Issues.md`,
   `HumynCapture_V2_Fix_Handoff.md`: what goes wrong at record time and why.
6. **Companion docs of this revision** (annexes; this plan wins on conflict):
   `BOTTLENECK_FINDINGS.md` (measured throughput facts), `THROUGHPUT_FIX_PLAN.md` (overlap-driver
   spec of record), `VERTEX_FAILOVER_PLAN.md` (VLM failover spec of record),
   `GCP_OFFLOAD_EXPLAINER.md` (egress verification + migration runbook detail).

## 4. Locked rulings registry (Adnaan, with rationale)

| # | Ruling | Why |
|---|---|---|
| R1 | Games: Kamla + Outer Wilds only; anything else rejects | Phase-1 scope |
| R2 | Bins: #1 deliverable / #2 fix in post / #3 reject; fixes auto-applied from the reason→fix registry; **2 fix retries** then demote to reject with residual reasons | Automation with a bounded loop |
| R3 | Uploads include **raw sidecars** `inputs.jsonl` + `metadata.json` | Enables full re-translate — the strongest fix class |
| R4 | Ingestion: download **only folders with all required files**; incomplete folders → separate report, retried every run. No done-markers, no stability windows | Drive files appear only when fully uploaded |
| R5 | Batch flow: ≤10 sessions at a time → validate → fix → report → delete rejected media locally → upload deliverables to Drive II → wipe local media. **Amended 2026-08-15: batches may overlap in flight — while batch N validates/fixes, batch N+1 may download and batch N−1 may upload; at most 3 batches (≤30 sessions) local at once; per-batch order and every other R5 step unchanged** | Storage discipline; the amendment removes take-turns idle (`BOTTLENECK_FINDINGS.md`) |
| R6 | Drive I originals stay untouched forever; no status files written into Drive I | Archive of record + payment-dispute evidence |
| R7 | Delivery: **Shared Drive II**, spec layout `humynlabs/<mm-dd-yyyy UTC upload date>/<game>/<session-id>/`, **5 spec files only** (sidecars never delivered) | Client-facing tree mirrors spec §1.1.1; PII stays internal |
| R8 | **Rewritten 2026-08-15**: continuous 24/7 operation on the VM; **systemd timer every 30 min** (was launchd), lockfile-guarded, queue-drain-then-exit unchanged. `caffeinate` and Mac-sleep tolerance retired — the host never sleeps; interruption tolerance (§13) is kept for VM restarts/maintenance | Deadline arithmetic; VM is always-on |
| R9 | **No fast-sync — decode the full video** for controls-to-video grounding | Adnaan's explicit call (round 3 Q4) |
| R10 | Rolling daily delivery; 1000 h counted as **delivered post-trim clip duration**, 500/game | De-risks the deadline |
| R11 | Payment per-player (operator rollups derived); rejected sessions = $0; reports contain **hours only, no money** | Round 2/3 rulings |
| R12 | Reports saved on the pipeline host; synced to GCS (R19) and readable by Adnaan; he shares them himself. Telegram: **per-batch topline** + **daily payment report at 14:00 IST** with attached sheet | Concise ops channel |
| R13 | VLM = `gemini-3.7-flash` (not Claude), no rate cap. **Amended by R23 (08-15): 3.7-flash is the model of record at the top of a quota ladder — substitution happens only under sustained rate limiting, per R23** | Adnaan's choice |
| R14 | ≥3 distinct actions **per session** (stricter than the client's per-game bar — deliberate) | Round 2 Q8 |
| R15 | Splitting one recording into multiple delivered sessions is **allowed** | Enables the mid-clip rule |
| R16 | Players told: ≥70 s, target 10–30 min per session; operators told to over-record (600 h/game target) | Buffer for rejects |
| R17 | rrd: **never downloaded** from Drive I (fully regenerable); delivered for a **random 20% per game per day** (spec §1.4 requires 15%) | Halves transfer both directions; rrd ≈ video-sized |
| R18 | Service account for Drive access | Robot badge; no token expiry; clean audit |
| **R19** | **(2026-08-15) Runtime host = GCP VM**: `e2-standard-16` on-demand, `asia-south1`, straight-to-VM go-live (no Mac go-live); provisioning **scripted via gcloud** (Adnaan authenticates once); disaster-recovery copies of ledger backups + dossiers + reports go to a **small GCS bucket**; Mac = control-plane only | Home line can't carry the mission (67–78 vs ~110+ fh/day); VM↔Drive traffic verified free; ~$140/10 days |
| **R20** | **(2026-08-15) Overlap driver approved** — the batch-pipelining design of `THROUGHPUT_FIX_PLAN.md` (D/V/U threads at batch granularity, ≤3 batches in flight, batches remain the unit of flow AND reporting), minus its `--bwlimit` clause (that existed for home-line bufferbloat; datacenter line doesn't need it) | Keeps CPU (the scarce resource on a VM) from ever waiting on transfers |
| **R21** | **(2026-08-15) Vertex failover approved** — `pipeline/vlm.py` ports the engine's genlang→vertex-express failover per `VERTEX_FAILOVER_PLAN.md`; **merges dark behind NEW config knob `VLM_FAILOVER_ENABLED=False`, enabled by a one-line config flip only after the live smoke test passes from the VM (§7.6)**; both endpoints failing still ends in `HOLD_VLM` (F5 unchanged) | One Google-endpoint outage must not stall sessions the other endpoint could serve; the flag makes the ship-gate mechanical instead of aspirational |
| **R22** | **(2026-08-15) Workers ramp policy** — start `HL_PIPELINE_WORKERS=8` on 16 vCPU; raise to **10 — the hard ceiling while V feeds the pool one ≤10-session batch at a time (`run.py:131-156`); beyond 10 is a no-op** — while sustained CPU <~90% AND Gemini 429s stay quiet; step back down **manually** on sustained 429s (no automatic worker step-down exists in code — the automatic answer to 429s is the R23 model ladder). Validation workers wait on Gemini for a large share of wall-time, so oversubscribing vCPUs is intended. If day-0 shows cores idle at 10 workers, the §15 two-batch-feed escalation is the next lever | "Turn the knob until cores are full" — the biggest lever on a VM |
| **R23** | **(2026-08-15) VLM quota ladder** (amends R13) — on sustained rate limiting, step the model down `gemini-3.7-flash` → `gemini-3.5-flash` → `gemini-3.1-pro` **[ids assumed — the §7.6 probes verify]**. Semantics (Adnaan's four calls, 08-15): trigger = one call exhausting the §13 429-ladder on BOTH endpoints; **sticky per run** — the rest of that run stays at the lower rung, next run resets to 3.7; applies to **ALL VLM calls including the engine's sweep**, via a pipeline-side `LadderGemini` subclass of the engine's client (wrap, don't fork — §10); after the bottom rung fails on both endpoints, **the prev-key rung: `GEMINI_API_KEY_PREV` at 3.7-flash (sticky like every other rung)**, then `HOLD_VLM` (F5 unchanged — never pass unlooked-at); fallback-model verdicts **deliver normally and are flagged** (model recorded in the dossier verdict; batch message gains an "N on fallback model" line — R12 format addition approved 08-15) | Throughput survives quota exhaustion without weakening the never-unlooked-at rule |

## 5. Numeric thresholds (all gates in one table)

Rows unchanged from v1 except the marked additions/edits.

| Gate | Value | Source |
|---|---|---|
| Min delivered clip length | **70 s** (hard; also per split segment) | Client guideline; qa-v2 |
| Session length guidance | 10–30 min (soft; >30 min accepted with note) | R16 |
| Distinct actions per session | **≥3** (hard; per split segment too) | R14 |
| Mid-clip non-gameplay keep-vs-cut | keep+gate if **≤2 s contiguous AND ≤0.2% of clip**; else split; segments <70 s dropped; none survive → reject | Adnaan round-3 |
| AFK window (both games) | **>30 s** zero input + near-static screen (OW: dialogue/map/reading are gameplay; only true AFK) | Adnaan round-3 |
| Frozen-context confirmation | window mean inter-frame diff **<40%** of that session's live-gameplay baseline; probes strictly inside window span | Sibling measurement |
| Notifications | edge → trim; **mid-clip → reject** | Adnaan round-3 |
| Burned-in personal text mid-clip | reject | Round-2 table |
| Audio | **never blocks**; absent/silent → warn note | Adnaan round-3 + spec verified |
| Dropped frames (irregular intervals) | ≤1% pass · 1–5% deliver+warn · >5% reject | Round-2 table |
| Controls↔video lag | ≤50 ms pass · 50–150 ms constant → fix+re-verify · >150 ms constant → fix+re-verify · drifting or unmeasurable-with-visible-action → reject | Client gates + round-2 |
| Lag measurability | active ≥2% and |corr| ≥0.15; **negative corr is correct** | Client script; 07-21 agreement |
| Frame-sync (timestamp vs real PTS) | ≤100 ms per row | qa-v2 |
| VLM game-identity tripwire | report-only in Phase 1 (Adnaan 08-14; `VLM_GAME_TRIPWIRE_GATES=False` in config); unanimity thresholds retained for the log line | Post-plan ruling, recorded in config.py |
| Duplicates | md5-identical: same player → skip silently; cross-identity → **accept earliest Drive `createdTime`**, reject the other + integrity flag | Adnaan round-3 |
| Fix retries | 2, then reject | R2 |
| Batch size | 10 sessions | R5 |
| **Batches in flight** | **≤3 (≈ ≤30 sessions local)** | R5 amendment / R20 |
| rrd delivery sampling | random 20% per game per day (recorded in ledger) | R17 |
| Disk low-water | pause downloads below **100 GB free** + Telegram alert (VM disk is 250 GB; normal peak use ≈ 50 GB, so this floor only trips on a leak) | Fiat F7 |
| Scheduler | **systemd timer** every **30 min**, lockfile-guarded (was launchd) | R8 |
| **Workers** | **start 8; ramp 8→10 while CPU <~90% and 429s quiet (10 = batch size = the useful max; never below 4)** | R22 |
| **VLM model ladder** | **rungs 3.7-flash → 3.5-flash → 3.1-pro → prev-key@3.7; trigger = one call exhausting 429-retries on both endpoints; ALL rungs sticky for the rest of the run (run-level, parent-carried — §10a), reset next run; below the last rung → HOLD_VLM** | R23 |
| Incomplete folder escalation | listed every run; highlighted in daily report when **>48 h** old | Fiat F8 |
| Pace alarm | fires when needed h/day > trailing 24 h average ×1.15, or projected finish > Aug 24 | §11.3 |
| **Drive upload ceiling** | **750 GB per user (SA) per 24 h ≈ 240 fh/day of deliveries** — external hard cap; alert when a day's uploads pass 600 GB | [web: knowledge.workspace.google.com, 08-14]; §15 |

## 6. Architecture

```
              ┌────────────────────── GCP VM (asia-south1, 24/7) ──────────────────────┐
 Shared       │   OVERLAP DRIVER (R20): three threads at batch granularity             │  Shared
 Drive I      │   [D]ownload ─▶ queue ─▶ [V]alidate+Fix ─▶ queue ─▶ [U]pload           │  Drive II
 (collection) │    batch N+1             batch N                    batch N−1          │  (delivery)
 kamla/… ─────┼─▶ ingest.py            validate.py + engine        deliver.py          ├─▶ humynlabs/<date>/…
 outer_wilds/…│    scan/completeness    scanner + VLM (workers=8+,  package+verify     │
              │    download ≤10/batch   R22 ramp) → fix.py (≤2)     upload, wipe       │
              │    ≤3 batches in flight        └─▶ reject ─▶ dossier+report            │
              │                                                                        │
              │   ledger.db (SQLite WAL, permanent) · dossiers/ (permanent)            │
              │   telegram.py (per-batch topline · daily 14:00 IST payment report)     │
              │   nightly sync ─▶ gs://hl-gamedata-pipeline-backups (ledger backups,   │
              │                    dossiers, reports)                                  │
              └────────────────────────────────────────────────────────────────────────┘
                    Mac (control plane): dev + git, rsync deploys to VM, reads
                    Telegram/reports; holds NO live pipeline state.
```

**Session state machine** (ledger `state`) — UNCHANGED from v1, implemented and tested:
`DISCOVERED → INCOMPLETE* → DOWNLOADING → INGESTED → VALIDATING → {READY | FIX_QUEUED | REJECTED | QUARANTINED | DUPLICATE | HOLD_VLM}`;
`FIX_QUEUED → FIXING → REVALIDATING → {READY | FIX_QUEUED(≤2) | REJECTED}`;
`READY → PACKAGED → UPLOADED(verified) → DELIVERED`. `INCOMPLETE` re-enters `DISCOVERED` on later
runs. Every transition appends to the `events` audit table. Split segments become child rows
(`parent_id` set, ids `-p1/-p2/…`); the parent ends as `SPLIT`.

**Code layout — REUSE FIRST.** All of `pipeline/` exists, is tested (build steps 0–6 green,
08-14), and moves to the VM **unchanged** except where §18 says otherwise: `config.py`,
`ledger.py`, `ingest.py`, `validate.py`, `scanner.py`, `vlm.py`, `fix.py`, `cutter.py`, `gate.py`,
`deliver.py`, `telegram.py`, `reports.py`, `pace.py`, `pipeline/tests/` (9 test files), plus the
`translator/` package and `tools/analyze_sample.py` (the engine). **The v2/v3 delta touches exactly:
`run.py` (overlap driver + spawn `mp_context` + in-loop daily-report/backup calls + per-batch
fallback-model count), `__main__.py` (the `if __name__ == "__main__":` guard — one line,
required for spawn), `vlm.py` (Vertex failover + the R23 model/key ladder + the `LadderGemini`
subclass the sweep uses), `config.py` (knobs: `PIPELINE_OVERLAP`, `MAX_BATCHES_IN_FLIGHT`,
`VLM_FAILOVER_ENABLED`, `VLM_MODEL_LADDER`, `gemini_key_prev` property), `validate.py` (client
swap `eng.Gemini` → `vlmmod.LadderGemini` at construction (~`validate.py:632`) + models-used
passthrough into verdict metrics), `reports.py` (optional "N on fallback model" batch line),
`ingest.py` (operator-level parsing accepts free-text names — drop the email check on `op`
only, `ingest.py:125-126`; player/session strictness unchanged), `ledger.py` (busy_timeout
pragma + `start_batch(sessions=…)` + concurrency docstring), and NEW `tools/provision_vm.sh` +
`tools/vm_setup.sh` + systemd unit files under `pipeline/systemd/`.** No rclone-argv change is
planned (a `--bwlimit` addition at `ingest.py:413`/`deliver.py:123` remains a documented
one-liner, deliberately not built — R20). Anything beyond this list being rewritten is a plan
violation.

Working data lives OUTSIDE the repo in `~/hl-pipeline/` **on the VM**: `work/` (transient media),
`dossiers/<sid>/` (permanent evidence), `reports/`, `ledger.db` (+ `backups/`, 14 daily copies),
`logs/`, `delivery-stage/`. The GCS bucket mirrors `backups/`, `dossiers/`, `reports/` nightly.

**Overlap driver contract (R20; spec of record = `THROUGHPUT_FIX_PLAN.md`, adjusted to the VM —
where that doc's details or line refs disagree with this section, this section, pinned at
commit f49bdd6, wins):**
- Threads D/V/U as in the diagram; handoff via two `queue.Queue`s of batch descriptors; batch
  bookkeeping via the existing `batches` table. **Delta: `start_batch` records the batch's sid
  list in `summary_json` at batch START** — today it holds `'{}'` until `finish_batch`
  (`ledger.py:207-212`, list written only at `run.py:401-404`), so an in-flight batch — the only
  kind that ever needs resuming — would have no grouping. `finish_batch` unchanged.
- **The validation pool must use the spawn start method**:
  `ProcessPoolExecutor(mp_context=multiprocessing.get_context("spawn"), …)`. Today's call
  (`run.py:154-156`) takes the platform default — `fork` on Linux — and forking a multi-threaded
  parent that holds sqlite/stdio locks intermittently deadlocks children while the process holds
  `run.lock`. macOS defaults to spawn, so Mac testing can never surface the fork wedge.
  `_validate_worker` is a top-level function with picklable dict args — but spawn is NOT a
  drop-in yet: **`pipeline/__main__.py` is an unguarded `raise SystemExit(main())` (lines 1–3),
  and under spawn's documented "safe importing of main module" contract every worker re-imports
  the main module (`__mp_main__` via runpy for `-m` launches) and would execute `main()` — the
  child dies at bootstrap (its spawn argv is the `--multiprocessing-fork` bootstrap, not a
  pipeline command, so `main()` exits on "unknown command" before ever reaching the worker
  loop) and the pool dies as BrokenProcessPool with every session quarantined "validation
  worker died". Delta: wrap `pipeline/__main__.py` in `if __name__ == "__main__":`.** Since macOS already defaults
  to spawn, this bug is live on the Mac today for any real `python -m pipeline run` with ≥2
  validating sessions — the guard fixes both hosts. Acceptance: a REAL threads+pool test
  (actual `ProcessPoolExecutor`, no monkeypatch) in the suite, **plus one `python -m pipeline
  run` smoke with workers≥2 and ≥2 seeded sessions** (pytest-context pool tests structurally
  cannot see the `__main__` re-import failure — pytest owns a guarded `__main__`), re-run on
  the VM at step 10.
- One `Ledger` connection per thread (`sqlite3` `check_same_thread` guard stays); WAL already on
  (`ledger.py:65`); add `PRAGMA busy_timeout=10000`. Validator subprocesses never touch the
  ledger (unchanged pattern — the owning thread writes).
- **State ownership, stated honestly: writes are phase-scoped, not thread-exclusive.**
  D writes DISCOVERED/DOWNLOADING→INGESTED, plus re-queue-to-DISCOVERED and QUARANTINED on
  download outcomes (`run.py:110-128`). V writes VALIDATING→{READY, FIX_QUEUED, REJECTED,
  HOLD_VLM, QUARANTINED} plus the whole fix loop. U writes READY→PACKAGED→UPLOADED→DELIVERED,
  QUARANTINED on delivery crash (`run.py:317`), **and FIX_QUEUED/REJECTED on final-gate failure
  (`run.py:341-346`)**. A gate-failed session re-enters V's domain **on the NEXT RUN, not
  in-process** — exactly today's semantics: the `attempted` set blocks same-run re-pick
  (`run.py:512-523`), and the 30-min tick is the retry cadence for this rare event. There is no
  write contention: a session is in exactly one phase's hands at any moment; the queue handoff,
  not the ledger, sequences the threads. **Batch completion is owned solely by U**:
  `finish_batch` + the Telegram message fire only when every session of the batch has reached a
  terminal-or-HOLD state; a batch with a gate-regressed session stays open, **carries over via
  the now-written `summary_json` regroup, and completes (message included) on the run that
  finishes it** — U never blocks waiting on a drained V.
- On startup, partition `RESUMABLE` rows by state to the owning thread's queue, regrouped by the
  now-written `batches.summary_json` (FIFO regroup for any rows predating the change) — kill at
  any instant resumes exactly (§13).
- **Periodic duties move inside the drain loop**: `send_daily_report_if_due` and
  `ledger.backup_daily` are called between batch completions. Today both run only at run
  start/end (`run.py:495`, `run.py:546-547`) — correct for 30-min Mac runs, but a multi-hour VM
  backlog run would sail past 14:00 IST without the payment report and leave the nightly GCS
  sync mirroring a stale backup.
- `PIPELINE_OVERLAP=False` config flag = byte-identical lockstep fallback (`process_batch` kept).
- Per-batch log line of the three stage times (dl/val/up minutes) — the live tuning gauge for R22.
- One pass per session per run via the `attempted` set (`run.py:506-523`; f49bdd6 replaced the
  older `hold_retried` one-shot) — preserved under the driver: all three queues feed through the
  shared `attempted` set, and the next timer tick is the retry cadence.

## 7. One-time setup — VM edition (Day 0, in order)

Status: Mac-era steps (rclone, SA + both-drive membership, Telegram chat id, §7.5 benchmark
27.9 min/fh) are **done and stay valid**. The launchd plist exists but was never loaded — it is
retired unloaded (R8). New sequence (target: all done 2026-08-15, go-live in the evening IST;
slack to Aug 16 morning):

1. **SDK + auth + quota check**: SDK install, `gcloud auth login`, and project set — **DONE by
   Adnaan 08-15** (billing already enabled). **Still pending, BEFORE provisioning**:
   `gcloud compute regions describe asia-south1` must show ≥16 CPUS available — a day-old
   project's default quota can be lower and bumps are not same-day. If short: file the bump
   immediately and put the fallback (other region vs 2× `e2-standard-8`) to Adnaan (§17.11).
2. **Provision (scripted — NEW `tools/provision_vm.sh`, committed)**: create
   `hl-pipeline-vm` (`e2-standard-16`, on-demand, `asia-south1-a` with `-b/-c` fallback on
   capacity errors, Debian 12, 250 GB pd-balanced, **`--no-service-account --no-scopes`** —
   without these gcloud silently attaches the default compute SA, widening access beyond F10's
   bucket-scoped grant — no HTTP/S ingress; SSH via `gcloud compute ssh` only) and the GCS
   bucket `gs://hl-gamedata-pipeline-backups`
   (`asia-south1`, Standard, uniform access, private; suffix the name if taken), then
   `roles/storage.objectAdmin` for `pipeline-runner@…` **on the bucket only**. Acceptance:
   `gcloud compute ssh hl-pipeline-vm -- true` succeeds; `gcloud storage ls` shows the bucket.
3. **Bootstrap (scripted — NEW `tools/vm_setup.sh`, run on the VM)**: `apt install ffmpeg
   sqlite3`; rclone via rclone.org install script; `uv` via its installer; create
   `~/hl-pipeline/` + `~/.config/hl-gamedata/`. Acceptance: `ffmpeg -version`, `rclone version`,
   `uv --version`.
4. **Code + secrets over**: from the Mac, `rsync -a` the repo → `VM:~/hl-gamedata` (exclude
   `out/`, `__pycache__/`, `*.rrd` — the six local benchmark sessions' videos DO go along);
   `gcloud compute scp` `sa.json` + `secrets.env` → `~/.config/hl-gamedata/` (chmod 600). Write
   `~/.config/rclone/rclone.conf` on the VM: `drive-collect`, `drive-deliver` (same as Mac,
   `service_account_file` pointing at the VM path) + NEW `gcs-backup` remote (type
   `google cloud storage`, same `sa.json`). **Post-rsync: `touch` a stub `session.rrd` into each
   of the six benchmark session dirs** — the `*.rrd` exclude strips their real rrds, and qa-v2
   early-returns on missing delivery files, silently skipping the expensive PTS/grounding
   battery; the stub is exactly what production work-copies carry (`ingest.py:458`), so the
   §7.5(b) benchmark stays production-faithful. Acceptance: `rclone lsd` on all three remotes
   from the VM. **Redeploy procedure from then on: edit+commit on Mac → same rsync → next timer
   tick picks it up** (one-way, `--delete`, same excludes).
5. **Day-0 measurements on the VM** (records go into §15; replaces the Mac assumptions):
   (a) Drive→VM and VM→Drive throughput: `rclone copy` one real ≥1 GB object each way (delivery
   test folder `_pipeline_test/` on Drive II, purged after — `deliver.cleanup_test_folder`
   exists); (b) re-run the §7.5 validation benchmark on the six local sessions → per-fh cost on
   VM cores → confirm the R22 starting worker count; (c) Gemini latency from Mumbai (one timed
   call). Acceptance: three numbers written into §15's "VM measured" row.
6. **VLM smoke matrix (R21+R23 gate)**: run the smoke script **on the VM** — one text-only
   `generateContent` per cell of {new key, prev key} × {genlang, vertex-express} at 3.7-flash,
   **plus one tiny `generateContent` probe per R23 ladder id (`gemini-3.5-flash`,
   `gemini-3.1-pro`) on each key's WORKING endpoint** — `list_models` alone is a genlang-side
   call (engine precedent `analyze_sample.py:405-414`) and cannot vouch for ids on a
   vertex-only key. Outcomes: whichever endpoint(s) answer the NEW key define the working
   set — **the new key is `AQ.`-format (Vertex-express issue), so genlang may well be the one
   that fails; if ONLY vertex answers — and equally if BOTH answer — flip
   `VLM_FAILOVER_ENABLED=True` before gate (b)** (either flip IS smoke-verified, satisfying
   R21's gate; the flag stays False only when the matrix proved vertex dead); if a ladder
   model id differs or 404s, correct `VLM_MODEL_LADDER` in config (one-line commit); Vertex
   403 "API not enabled" → Adnaan gets the exact console click; prev-key results decide
   whether the R23 rung-3 is armed or logged as dead. Go-live proceeds on whatever the matrix
   proves working — never on assumption.
7. **systemd units (NEW, committed under `pipeline/systemd/`, templated by `vm_setup.sh`)**:
   `vm_setup.sh` first runs `timedatectl set-timezone Asia/Kolkata` (Debian defaults to UTC — a
   naive 03:00 OnCalendar would fire at 08:30 IST; pipeline code is host-TZ-safe regardless,
   `config.py` carries explicit IST) and substitutes the real username and home into the units —
   **OS Login derives usernames from the Google account, so `User=` cannot be hardcoded**.
   `hl-pipeline.service`: `Type=oneshot`, `User=<templated>`,
   `WorkingDirectory=<home>/hl-gamedata`, `Environment=HL_PIPELINE_WORKERS=8`,
   `ExecStart=<home>/.local/bin/uv run --with numpy --with opencv-python-headless
   --with rerun-sdk python -m pipeline run` (absolute uv path — systemd has no user PATH),
   journald + `~/hl-pipeline/logs/`. `hl-pipeline.timer`: `OnCalendar=*:0/30`,
   `Persistent=true`. `hl-backup.service`/`.timer` (03:00 local = IST): **`rclone copy`**
   (amended by review-r1 #5 — NEVER `sync`: a sync from a fresh/empty dir after a VM recreate
   would DELETE the DR copies it exists to keep) of
   `backups/`, `dossiers/`, `reports/` → `gcs-backup:`; on failure an inline alert —
   `uv run python -c "from pipeline import config, telegram;
   telegram.send_message(config.load(), '⚠️ GCS backup failed')"` — because `telegram.py` has
   no CLI entry point. Run-lock (`~/hl-pipeline/run.lock`) already guarantees single instance
   if a run overruns the tick — unchanged. Acceptance: timer fires; a manual `systemctl start
   hl-pipeline` completes a no-op run; backup objects appear in the bucket; a forced backup
   failure (bad remote name) produces the Telegram alert.

## 8. Ledger schema (SQLite, `~/hl-pipeline/ledger.db` on the VM)

UNCHANGED and implemented (`ledger.py`, WAL + daily backups + events audit + `duration_raw_s`
addition). v2 delta: `PRAGMA busy_timeout=10000` and the docstring's concurrency note now reads
"one process, up to three writer threads (D/V/U), one connection per thread, short transactions"
(§6). Schema itself untouched:

```sql
sessions(
  session_id TEXT PRIMARY KEY,          -- folder name; children get -pN suffixes
  parent_id TEXT NULL,                  -- set on split segments
  game TEXT, operator_email TEXT, player_email TEXT,
  drive_path TEXT, drive_ctime TEXT,    -- provenance (Drive I)
  md5_video TEXT, bytes INTEGER,
  state TEXT, bin INTEGER NULL,         -- 1/2/3 after validation
  reasons_json TEXT,                    -- [{code, blocking, params, evidence}]
  fix_attempts INTEGER DEFAULT 0,
  duration_delivered_s REAL NULL,       -- the paid number
  duration_raw_s REAL NULL,             -- pre-trim, for the collected line
  rrd_sampled INTEGER DEFAULT 0,
  delivered_at TEXT NULL, dossier_path TEXT,
  created_at TEXT, updated_at TEXT
);
batches(batch_no INTEGER PRIMARY KEY, started TEXT, finished TEXT, summary_json TEXT);
events(id INTEGER PRIMARY KEY, session_id TEXT, ts TEXT, from_state TEXT, to_state TEXT, detail TEXT);
incomplete(drive_path TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, missing_json TEXT);
```

Rules: ledger and dossiers are **never deleted**; daily backup copy (keep 14) + nightly GCS sync
(R19). `duration_delivered_s` sums per player/game = payment + the 500 h counters.

## 9. Phase I — ingestion (`ingest.py`) — IMPLEMENTED, REUSE (one v3 amendment)

Built and tested 08-14 (steps 1 acceptance green). Behavior of record: scan via
`rclone lsjson -R --hash --drive-use-created-date`; path parsing with QUARANTINE — **amended
08-15: the operator level accepts free-text names (the email check at `ingest.py:125-126` is
removed for `op` only); player folders stay strict emails and session folders stay the strict
id pattern, so the junk guard now lives one level down.** The ledger column `operator_email`
keeps its name (schema locked, §8) but carries the operator label; payment sheets and rollups
show names — mechanically unchanged (R11 groups by the column, whatever it holds).
Pre-program cutoff scoped to junk outside the game trees; supersede rule (same session-id, new
md5, after reject); completeness per R4 (+zip reassembly); md5 dedupe (same-player DUPLICATE,
cross-identity earliest-ctime wins with shipped-copy exception); FIFO batching with lagging-game
priority (F4); download with checksum verify, payload sniff (v2/v1/raw/garbage), sidecars moved
to `raw/`, rrd stub for the qa gate (never staged). Download-failure semantics as implemented at
f49bdd6: **md5-mismatch → QUARANTINED; transient rclone failures and incomplete multi-part zips
re-queue as DISCOVERED with an alert and retry next run** (`run.py:110-128`). v3 delta: **the
operator-level parsing amendment only** (`ingest.py:125-126`, above). (No bwlimit hook exists
in code; if transfer shaping is ever needed it is a one-line argv addition at
`ingest.py:413`/`deliver.py:123` — deliberately not built, R20.)

## 10. Phase II — validation (`validate.py`) — IMPLEMENTED, REUSE AS-IS

Built and tested 08-14 (step 2 acceptance: six local sessions reproduce the sibling's verdicts
code-for-code). Engine (`tools/analyze_sample.py`) wrapped, not forked; scanner precision layer;
escalation of soft engine findings to blocking codes; structured-fields-only mapping (the two
qa-v2 exact phrases excepted); VLM hygiene → `HOLD_VLM`, never pass unlooked-at (F5); keybind SOP
ruling; bin logic; the complete reason-code registry — **all as in v1 of this plan, verbatim in
code**. The game-identity tripwire is report-only (Adnaan 08-14; `VLM_GAME_TRIPWIRE_GATES=False`).
v2 delta: **none in this module**. (Its VLM calls gain resilience transitively through §10a.)

### 10a. VLM client (`vlm.py`) — v2/v3 delta: Vertex failover (R21) + quota ladder (R23)

`pipeline/vlm.py` today: §13 retry ladder (Retry-After, 2 s base / 60 s cap, 5 tries) against
`generativelanguage` only. Change per `VERTEX_FAILOVER_PLAN.md` (spec of record — endpoint list,
sticky `_which`, 401/403/404 immediate switch, ladder-then-switch for 429/5xx/network,
safety-blocks never fail over, key never in error strings): run the ladder against the sticky
endpoint, then once against the other; both exhausted → `VLMError` → `HOLD_VLM` (F5 unchanged).
Tests per that plan (5 new + 2 adjusted in `test_scanner_vlm.py`), run in both flag states.
**Merges dark behind `VLM_FAILOVER_ENABLED=False`; a one-line config flip (committed) enables it
once the §7.6 smoke from the VM passes** — this supersedes `VERTEX_FAILOVER_PLAN.md` §5's
stop-before-commit sequencing (§3.6: this plan wins on conflict). Post-f49bdd6 implementation
notes: the ladder's network-except clause now also catches `http.client.HTTPException`
(`vlm.py:62-64`) — `_generate_once` must carry it; current pins `_GENLANG` `vlm.py:24`, ladder
`vlm.py:39-78`, `classify_stills` `vlm.py:103`, `confirm_flag` `vlm.py:160`. The engine's own
client already fails over — this closes the wrapper gap (`classify_stills`, `confirm_flag`)
that would otherwise HOLD sessions during a genlang-only outage.

**R23 ladder architecture (on top of the endpoint failover).** The ladder is FOUR sticky
rungs: 0 = 3.7-flash, 1 = 3.5-flash, 2 = 3.1-pro (all on `GEMINI_API_KEY`), 3 = **prev-key
rung** (`GEMINI_API_KEY_PREV` at 3.7-flash — independent quota, quality-first). Per-call
escalation:
1. current sticky rung × sticky endpoint, full §13 429-ladder;
2. same rung × the other endpoint — **only when `VLM_FAILOVER_ENABLED`** (§13) — full ladder;
3. rung steps down (sticky, prev-key rung included) → repeat 1–2; below rung 3 →
   `VLMError` → `HOLD_VLM` (F5).
**Stickiness is RUN-level, not merely process-level.** Adnaan ruled "sticky per run", and
validation workers are fresh spawn processes per batch pool (`run.py:154-156`) — per-process
state alone would reset every batch and re-pay full discovery ladders (minutes, not one POST)
constantly. So: the PARENT carries the run's current rung and injects it into every
validation job's args (which already carry `gemini_model`, `run.py:148`); each worker starts
there, reports the rung it ended on in its result dict, and the parent keeps the maximum for
subsequent batches. Within a process, `vlm.py` holds module state `_which` (endpoint) +
`_rung`, initialized from the injected start. Next run resets to rung 0. Hard auth/not-found
failures (401/403/**404**) burn no retries at any rung, so a dead key or misnamed model
collapses through rungs in seconds. Safety-blocks still never ladder (the model answered;
switching models to dodge a refusal would be verdict-shopping — F5). One accepted behavior
change, stated: with `VLM_FAILOVER_ENABLED=False`, `LadderGemini`'s override also removes the
engine's native always-on vertex fallback from the sweep — acceptable because the flag stays
False only when the §7.6 matrix proved vertex dead for the active key (the prev-key rung may
still use whatever the matrix proved for the prev key). **`LadderGemini(eng.Gemini)`** lives
in `vlm.py` and overrides `generate()` to route through this chain; `validate.py` constructs
it instead of `eng.Gemini` (~`validate.py:632`), which gives the SWEEP the whole ladder
without touching the engine file. The subclass records every (key, model) that actually
answered (`models_used`); the wrapper writes it into verdict metrics; `run.py` counts
sessions whose verdict used any rung above 0; `reports.py` prints the optional "N on fallback
model" batch line (R23).

## 11. Phase III — post-processing (`fix.py`, `cutter.py`, `gate.py`) — IMPLEMENTED, REUSE AS-IS

Built and tested 08-14 (step 3 acceptance green: OW fan-out fixed → qa-v2 PASS; synthetic
mid-clip pause → cut segments each pass ≥70 s; gated windows satisfy §1.5.5). The fix registry
as implemented in `fix.py` (canonical order **FIX_REMUX … FIX_SESSIONJSON_REWRITE/RECOMPUTE**,
applier dispatch `fix.py:279-323`; v1's table name "FIX_RRD_REGEN" is not a `fix.py` id — rrd
regeneration happens at packaging, `deliver.py:91-92`, per R17), ≤2 passes (R2), fixes on the
working copy only (R6),
`fixlog.json` audit — all in code. v2 delta: **none**.

## 12. Phase IV — packaging & delivery (`deliver.py`) — IMPLEMENTED, REUSE AS-IS

Built and tested 08-14 (step 4 acceptance green). Staging with explicit file lists (stub rrd can
never ship), deterministic 20% rrd sampling recorded in ledger (R17), final qa-v2 gate with
rrd-presence waivers **by filename**, `rclone copy --checksum` + size/md5 verify, DELIVERED +
hours recorded once + local wipe only after verify, rejected-session dossier finalization with
coaching notes, cross-midnight resume under the staged date. v2 delta: **none**.

## 13. Continuity, failure modes, recovery (VM edition)

| Failure | Behavior |
|---|---|
| VM reboot / GCP maintenance | e2 live-migrates by default; after any reboot the systemd timer (`Persistent=true`) fires, run-lock is stale-reclaimed (pid check — exists), ledger states resume the run exactly |
| Crash mid-batch / kill −9 | every step is temp-write + atomic rename; state partition on startup re-queues D/V/U work; downloads resume (rclone); uploads re-verify; hours recorded once at DELIVERED — **asserted by the §18.8 kill-matrix test** |
| Drive API errors/quota | scan failure → alert, run continues without new discoveries (`run.py:496-504`); per-download rclone retries then re-queue-with-alert (`ingest.py:download`, `run.py:110-128`). **v1's "2-consecutive-failures" escalation was never implemented — the 30-min tick + per-run alert dedup is the actual behavior** |
| Gemini rate limit (429) | per call: §13 429-ladder on the active endpoint → other endpoint (R21, once `VLM_FAILOVER_ENABLED`) → **R23 model step-down, sticky for the run (3.5-flash, then 3.1-pro, each × both endpoints) → prev-key rung (sticky) at 3.7 → `HOLD_VLM`**, one pass per session per run (`attempted` set, `run.py:506-523`), retried next timer tick at 3.7 again; non-VLM work continues. Worker-count step-down stays MANUAL (R22) — the automatic answer to 429s is the model ladder, not fewer workers |
| Gemini outage / safety-block / malformed | `HOLD_VLM` (never silently passed), retried next run; the implemented alert is END-OF-RUN when sessions remain held (`run.py:542-545`) — **v1's "after 1 h sustained" was intent, not code**. Safety-blocks do NOT fail over (the endpoint answered) — R21 |
| Corrupt download | checksum retry ×2 → `QUARANTINED` + alert (exists) |
| Disk < 100 GB free | pause new downloads, keep uploading/wiping, alert (F7; VM disk 250 GB — this floor trips only on a leak) |
| Drive upload cap approached | alert when a rolling 24 h's uploads exceed 600 GB (≈192 fh) — the 750 GB/day SA ceiling is external and hard (§5); mitigation if ever needed: second delivery SA (doubles the cap; needs Adnaan) |
| Ledger corruption | daily local backups (14) + nightly GCS sync; Drive I + dossiers allow full replay |
| GCS backup failure | Telegram alert from the backup unit; pipeline itself unaffected |
| VM dies / is deleted | recreate via `tools/provision_vm.sh` + `vm_setup.sh` (~1 h); restore `~/hl-pipeline/` from the GCS bucket; worst case (bucket stale) replay from Drive I + re-validate — possible by design, costs hours |
| Mac offline | no impact — control-plane only (R19) |
| Repeated same-code fix failures (3 in a row) | alert — likely a systemic tool bug (exists) |

Human-intervention-only list: service-account/Drive permission changes, Telegram token rotation,
threshold changes (edit `config.py`, logged), anything in `QUARANTINED`, VM recreate, the
two-batch-feed escalation beyond 10 workers (R22/§15), enabling Vertex AI API if the §7.6 smoke
asks for it.

## 14. Reporting & alerting (`telegram.py`, `reports.py`, `pace.py`) — IMPLEMENTED, REUSE AS-IS

Built and tested 08-14 (step 5 acceptance: byte-match on fixture data). Formats unchanged — under
R20 a "batch" is still a real ≤10-session batch, so the per-batch topline keeps its meaning; the
message is sent when that batch's deliver phase completes. `duration_min` becomes the batch's
end-to-end elapsed (spans overlap) — same field, noted meaning shift. v2/v3 delta: the
per-batch stage-times log line (§6) feeding R22 tuning; the in-loop `send_daily_report_if_due`
relocation (§6) — the 14:00 IST report can no longer be starved by a long run; and one
approved format addition (R23, 08-15): the batch message gains an **optional "N on fallback
model" line** when any of the batch's verdicts came from a laddered-down model. Everything
else byte-identical.

Per-batch Telegram DM and daily 14:00 IST payment report + CSV/MD sheet: exactly as v1 (examples
retained in `reports.py` tests). Pace math unchanged
(`needed = max(0, 500 − delivered)/days_left`; trailing 24 h; alarm ×1.15; collected line tracks
600/game).

## 15. Capacity plan (v2 — measured numbers, sources labeled)

Replaces v1's pre-benchmark estimates. Arithmetic script-computed
(`BOTTLENECK_FINDINGS.md` + session calc scripts).

- **VM measured (Day 0, 2026-08-15 ~14:10 IST)**: (a) Drive↔VM throughput with a real 1.2 GB
  object via rclone: **VM→Drive II upload 183 Mbit/s (52.5 s), Drive II→VM download
  474 Mbit/s (20.3 s)** — download comfortably clears the ≥300 Mbps assumption; upload
  headroom ≈ 3× the ~7.4 MB/s a 240 fh/day delivery pace needs. (c) Gemini latency from
  Mumbai: **1.5 s** text-only 3.7-flash call (genlang). (b) validation benchmark (same six
  sessions as the Mac baseline, single worker, real VLM sweep, 0 errors): **779 s footage in
  728 s = 56.1 min/fh** → VM vCPU ≈ **2.0×** slower than an M5 core (optimistic end of the
  2–3× band). Extrapolated: 8 workers ≈ 7.0 min/fh ≈ **205 fh/day**; 10 workers ≈ 5.6 min/fh
  ≈ 257 → clipped by the **240 fh/day external upload cap**. R22 start-at-8 confirmed; the
  landing zone ~172–240 stands with measured, not assumed, inputs.

- **Validation cost**: **27.9 min per footage-hour single-worker incl. VLM sweep** (§7.5
  benchmark on the Mac, 779 s of footage in 362 s). One full decode pass measured at ~7.3×
  realtime per M5 core; a cloud vCPU is assumed 2–3× slower **[assumption — §7.5 Day-0 re-run on
  the VM measures it]** → 16 vCPUs bound validation at ~204–408 fh/day; the R22 ramp finds the
  real number.
- **The ceilings ladder** (why each decision exists):
  Mac lockstep **67–78 fh/day @24/7** → VM lockstep ~121–176 [assumption: ≥300 Mbps effective
  Drive throughput — §7.5(a) measures] → VM + overlap driver at the starting 8 workers
  ~138–206 (27.9 × (2–3) ÷ 8 ≈ 7.0–10.5 min/fh, re-derived for VM vCPUs — the old 183–217 was
  the Mac figure) → + workers ramp to 10 **~172–240**, where **240 fh/day is the hard external
  cap** (Drive's 750 GB/user/day upload limit ÷ 3.13 GB per delivered fh). The ramp's floor is set by V feeding the pool one
  ≤10-session batch at a time (`run.py:131-156`): >10 workers is a no-op, and at the pessimistic
  vCPU assumption 10 workers ≈ 27.9 × 3 ÷ 10 ≈ 8.4 min/fh ≈ **172 fh/day**. **Escalation lever,
  only on measured need**: V feeds the pool from up to 2 batches concurrently (small driver
  change; batches still complete individually; ≤3 in flight unchanged) — restores the CPU-bound
  204+ zone.
- **Demand**: 1000 h over Aug 15→24 ≈ 100–111 delivered-h/day depending on start hour; with the
  600/500 over-collection buffer that is **~133 processed-fh/day** [assumption: reject ratio ≤
  the planned buffer]. The landing zone covers it 1.3–1.8×.
- **Transfer volumes at ~204 fh/day**: ~541 GB/day down, ~639 GB/day up — free on the VM
  (§1 egress verification), inside the 750 GB/day upload cap with the §13 alert at 600 GB.
  Program total ≈ 6.3 TB.
- **Batch shape** (10 × 20 min): 8.84 GB down, 10.42 GB up (video 2.6 GB/fh + 2% sidecars
  [assumption] + 20%×1.01 rrd share — rrd ratio measured).
- **Local footprint**: ≤3 batches in flight ≈ ≤30–45 GB + staging — comfortable on 250 GB (F7
  floor 100 GB).
- **VLM volume**: ~12–35 requests/session today; at ~200 fh/day roughly 2–3× the v1 estimate —
  order 10⁴–10⁵ calls/day. **Billing tier of the key is the open capacity question** (§17.7);
  R21 failover adds availability, not quota.
- **Cost (10 days)**: VM e2-standard-16 on-demand ≈ **$129 at the us-central1 rate** [web:
  cloudprice.net + economize.cloud 08-14]; **asia-south1 typically runs ~10–20% higher [not
  verified — read the exact rate at creation]**; 250 GB pd-balanced ≈ **$8**; GCS bucket ≈
  **$1**; Drive/VM transfer **$0** (verified). Total ≈ **$140–160**.

## 16. Fiat decisions (mine — veto any, but they're load-bearing until vetoed)

| # | Decision | Reasoning |
|---|---|---|
| F1 | `gate.py` blanks keys+actions, keeps dx/dy+buttons | Spec couples only keys↔actions |
| F2 | Split ids `-p1/-p2`, per-segment session.json recompute | Uniqueness + spec-valid segments |
| F3 | Cross-dup winner = earliest Drive `createdTime` | First uploader is presumptively the source |
| F4 | FIFO batching + lagging-game priority >10% pace gap | Fairness + hitting both 500s |
| F5 | VLM outage → HOLD, never pass unlooked-at | Silent pass is the worst failure |
| F6 | Incomplete >48 h highlighted for coaching | Distinguishes mid-upload from forgotten sidecars |
| F7 | Disk low-water 100 GB (unchanged on the 250 GB VM disk — trips only on leaks) | Simple, already implemented and tested |
| F8 | Reports as CSV + MD twin | Machine + human readers |
| F9 | Payment hours = delivered `duration_seconds` sum | Only defensible number; matches R10 |
| **F10** | GCS access via rclone `gcs-backup:` remote reusing the SAME `sa.json` (+ bucket-scoped objectAdmin) — no VM-attached service account, no new key | One credential file, one grant, reuses the installed tool |
| **F11** | Repo deploys Mac→VM via one-way `rsync --delete` (no git remote exists); commits stay Mac-side | Zero new infrastructure; the repo has no remote by design |
| **F12** | VM zone `asia-south1-a` with `-b/-c` capacity fallback; Debian 12; OS Login/SSH via gcloud only, no public inbound | Snappy ops from IST; smallest attack surface for the two keys on the box |
| **F13** | Vertex smoke failure does NOT block go-live (primary endpoint carries; failover ships when smoke passes) | Availability upgrade must not gate the deadline |

## 17. Known ambiguities & risks (stated, not hidden)

1. **rrd 15% reading**: unchanged from v1 (we deliver 20%; config flip + backfill if Odyssey
   reads §1.4 stricter).
2. **Client-side frame-spacing check vs real dropped frames**: unchanged from v1 (1%/5% gates
   are the buffer; open vault question to Odyssey stands).
3. **The capture tool's self-check isn't gating**: unchanged from v1 (pipeline catches it; cost
   is avoidable rejects).
4. **Kamla LMB/RMB unbound**: unchanged from v1 (VLM combat evidence carries the call).
5. **VM performance assumptions**: the 121–176 lockstep and 204–408 validation numbers rest on
   [assumption] Drive↔VM ≥300 Mbps effective and vCPU = M5-core/2–3. Both are measured at §7.5
   on Day 0 before the workers ramp; continuous 24/7 operation absorbs ~2–3× estimate error,
   not 10×.
6. **Operator folders are free-text names by ruling (08-15)** — the 08-14 name-folders are now
   valid, and the day-1 quarantine wave is off the table. Residual cost, stated: with no format
   check at the operator level, ANY stray folder there becomes an "operator" label in payment
   sheets (junk detection now relies on the player-email and session-pattern levels below it),
   and two spellings of one operator ("Samik" vs "samik ") become two rollup rows — a
   reporting-hygiene issue, not a correctness one.
7. **Gemini billing tier unknown** (Adnaan checking in AI Studio). At 10⁴–10⁵ calls/day the
   per-minute quota is the real VLM capacity bound; §13's retry ladder + HOLD, with manual R22
   step-down, is the safety net either way. R21 helps availability, not quota.
8. **Vertex express — resolved 08-15 (smoke run): 403 `API_KEY_SERVICE_BLOCKED` on both keys.**
   Not "API not enabled" on the project — the API KEYS themselves are blocked from
   `aiplatform.googleapis.com` (key-restriction). Adnaan's console fix, when he wants the
   failover armed: API key settings → API restrictions → allow Vertex AI API (or issue an
   unrestricted key), then re-run `~/hl-pipeline/smoke_matrix.py` on the VM and flip
   `VLM_FAILOVER_ENABLED=True` (one-line commit). Go-live not blocked (F13).
9. **Both secret keys live on a cloud VM** for 10 days (mitigations: no public inbound, gcloud
   SSH only, bucket-scoped grant; **rotate both keys after Phase 1** — §2).
10. **Drive-side throttling from one SA doing ~1.2 TB/day combined**: upload cap is verified
    (750 GB/day); download-side daily limits are **not published — unverified**; day-0
    measurement plus the §13 alert ladder is the detection. Mitigation if hit: second SA
    (uploads) — needs Adnaan.
11. **GCE quota of a day-old project** may be under 16 on-demand vCPUs in asia-south1 — checked
    first thing at §7.1, before the evening window; if short: immediate bump request, and the
    fallback (other region vs 2× e2-standard-8) goes to Adnaan.
12. **Plan versioning**: v1 of this plan was never committed and v2 overwrote it in place, so
    "unchanged from v1" claims are attested by this revision, not by git history. Resolved
    08-15: Adnaan approved path-scoped commits — v2 is commit `10b70c3`; every revision commits
    from here on.
13. **Ladder verdict-consistency**: 3.5-flash / 3.1-pro may label frozen windows, notifications
    or chat differently than 3.7-flash (all calibration was done on 3.7). Fences: the frozen
    keep-vs-cut gate is decided by DETERMINISTIC measured stillness (scanner + the <40% rule —
    the accepted "measured stillness, not VLM confidence" ruling), the VLM only proposes
    labels; and every fallback-model verdict is flagged in the dossier and batch line (R23), so
    a drift pattern would be visible within a batch, not a month later. Ladder model ids are
    [assumption] until the §7.6 probes confirm them.

## 18. Build order & acceptance (against the Aug 24 clock)

Steps 0–6 (v1): **DONE and green 08-14** — setup, ledger+ingest, validate+scanner+mapper,
fix+cutter+gate, deliver, telegram/reports/pace, run.py+lock+resume. They are the reuse base;
they do not reopen.

v2 delta steps (order; 7∥8 can run in parallel, 9 needs neither):

| Step | Component | Acceptance |
|---|---|---|
| 7 | **Vertex failover + R23 ladder** — `vlm.py` per `VERTEX_FAILOVER_PLAN.md` (endpoint part dark behind `VLM_FAILOVER_ENABLED=False`) + the §10a ladder (`_rung`, `LadderGemini`, prev-key rung); `validate.py` client swap + metrics passthrough; `reports.py` fallback line; `run.py` fallback count | endpoint tests (5 new + 2 adjusted) green in BOTH flag states; ladder tests green: step-down on 429-exhaustion, sticky-for-run + reset **across two pool generations (proving the parent inject→report→max round-trip, not just module state)**, prev-key rung fires only below the model rungs, safety-block never ladders, models_used surfaces in verdict metrics and the batch line; full suite green; §7.6 smoke matrix decides flags/ids (F13) |
| 8 | **Overlap driver + ingest amendment** — `run.py` D/V/U threads + queues + resume partition + **spawn `mp_context`** + in-loop daily-report/backup; **`__main__.py` guard**; `config.py` knobs (`PIPELINE_OVERLAP`, `MAX_BATCHES_IN_FLIGHT=3`, `VLM_FAILOVER_ENABLED`, `VLM_MODEL_LADDER`); `ledger.py` busy_timeout + `start_batch(sessions=…)`; **`ingest.py` operator-name parsing (`ingest.py:125-126`) + tests (name-operator fixture ingests; non-email player still quarantines)** | overlap-proof test (batch N+1 download completes before batch N−1 upload ends); 3-thread SQLite contention test (zero `database is locked`); **REAL threads+ProcessPool test — actual pool, no monkeypatch (fork-wedge is Linux-only; its step-10 VM run is the one that counts)**; **`python -m pipeline run` smoke, workers≥2 with ≥2 seeded sessions — the only test shape that exercises the `__main__` re-import under spawn (pytest cannot)**; lockstep-flag regression (existing `test_run.py` untouched and green); full suite green |
| 9 | **Provisioning** — `tools/provision_vm.sh` (VM + bucket + IAM grant), Adnaan's one-time `gcloud auth login` | VM SSH-able; bucket listed; grant visible in `gcloud storage buckets get-iam-policy` |
| 10 | **Bootstrap + migration** — `tools/vm_setup.sh`, rsync repo (+rrd stubs, §7.4), scp secrets, 3 rclone remotes | all three remotes list from the VM; `uv run --with numpy --with opencv-python-headless --with rerun-sdk python -m pipeline status` runs (bare python fails by design: `deliver.py:21` → `translator/rrd.py:26` imports rerun at module top); full test suite green **on the VM**, including the step-8 threads+pool test on Linux + a re-run of the step-8 `python -m pipeline run` smoke |
| 11 | **Day-0 measurements on the VM** — §7.5(a–c) + the §7.6 smoke matrix (2 keys × 2 endpoints + `list_models` per key) | throughput, per-fh validation cost, Gemini latency recorded in §15; smoke matrix recorded; `VLM_FAILOVER_ENABLED` / `VLM_MODEL_LADDER` set to what the matrix proved (config commit) — **before step 13** |
| 12 | **systemd units** — pipeline timer + nightly GCS backup timer | timer fires on schedule; manual run completes; backup objects appear in the bucket; simulated backup failure alerts on Telegram |
| 13 | **Go-live + kill matrix + ramp** — gate (a), if real uploads exist: one full unattended cycle on them; **else gate (b), synthetic: the six local sessions seeded at INGESTED into a TEST pipeline home (`HL_PIPELINE_HOME=~/hl-pipeline-test`, test-mode Telegram) → full validate/fix/deliver pass to Drive II `_pipeline_test/` (purged after — `deliver.cleanup_test_folder` exists), with D exercised by the live (empty) Drive scan** — Drive I still had zero files at 08-14 22:41, so (a) is likely unavailable on the target evening; the first real uploads then run under watch as production, not as the go-live gate. Kill matrix: `kill -9` during validation and during upload, each resuming cleanly (no double-DELIVERED, hours counted once, no stub rrd staged); **under gate (b) the download-kill leg is deferred to the first watched real uploads — nothing downloads in (b) by its own premise (DOWNLOADING-resume is already unit-covered in `test_run.py`/`test_ingest.py`)**; then R22 ramp 8→10 against the stage-times gauge | gate (a) or (b) green + kill matrix green (2 legs under (b), 3rd on first real uploads); ramp decision logged; per-batch stage times visible in logs |

Go-live = step 13's gate green. Target: **live 2026-08-15 evening IST** (slack to Aug 16
morning). **ACHIEVED 2026-08-15 16:30 IST** — gate (a) ran on the first 13 real uploads
(watched cycle 15:26–16:26: 21 delivered = 1.98 h to Drive II, 6 rejected with dossiers,
10 split parents; 3-leg kill matrix green — download/validation/upload kills each resumed
exactly, zero double-DELIVERED, hours once, no stub rrd shipped, rrd sampling 5/21 ≈ 20%);
both systemd timers armed; first scheduled fire observed 16:30 IST. R22 ramp decision:
HOLD at 8 workers — the stage gauge is validation-bound but loadavg > vCPUs during
validation bursts; ramp to 10 only after a CPU%-based (not loadavg) reading on a full
batch. **Reading taken 08-15 ~21:50 IST** (post-r5 tick, full overlap in flight —
b15 downloading, b14 validated+uploading, 8 workers): **94.5% busy over a 60 s
/proc/stat sample across 16 vCPUs** → the R22 "<~90% sustained" ramp condition is
NOT met; **HOLD at 8 stands, now data-backed** (Adnaan acknowledged the reading
requirement same evening). Re-sample before any future ramp attempt. Residual wart filed to the review loop: a batch emptied by kill-regroup is never
finish_batch()ed (observed batch_no=2). Tests after every step:
`PYTHONPATH=. uv run --with pytest --with numpy --with opencv-python-headless --with rerun-sdk
pytest pipeline/tests translator/tests -q` (Mac AND VM from step 10 on; the extra `--with`s are
mandatory on a clean host — `translator/rrd.py:26` imports rerun at module top).

## 19. Requirements Q&A — all resolved (Adnaan)

| # | Question | Ruling |
|---|---|---|
| Q1 | Drive I root layout | `kamla/` + `outer_wilds/` directly at drive root, already created (08-14) |
| Q2 | Google account for the SA project | humynlabs.ai workspace (08-14) |
| Q3 | Custom key remapping | Default bindings only (SOP); remap signatures → reject with coaching (08-14) |
| Q4 | Content uploaded before go-live | In scope inside the game trees; junk outside → quarantine/ignore (corrected 08-14) |
| Q5 | Operator folder naming | ~~Exact emails (08-14)~~ **Amended 08-15: operator folders are free-text NAMES; player folders remain exact emails; strict parsing stays for player+session levels** (§9, §17.6) |
| Q6 | Kamla "one match per recording" | No — pipeline auto-splits at match boundaries (08-14) |
| Q7 | Line speeds / data cap | Superseded by R19 — the home line is out of the loop; VM throughput measured at §7.5 (08-15) |
| Q8 | Mac's sleep hours | Superseded by R19/R8 — host never sleeps (08-15) |
| Q9 | Daily report 14:00 IST, CSV attachment | Confirmed (08-14) |
| Q10 | Same session-id re-upload after reject | Supersede automatically (08-14) |
| **Q11** | Go-live sequencing vs migration | **Straight to VM** — no Mac go-live; target Aug 15 evening, slack Aug 16 morning (08-15) |
| **Q12** | VM shape & region | **e2-standard-16, on-demand, asia-south1 (Mumbai)** (08-15) |
| **Q13** | Provisioning path | **Billing already on; scripted via gcloud** after Adnaan's one-time `gcloud auth login` (08-15) |
| **Q14** | DR home for ledger backups + dossiers | **Small GCS bucket**, nightly sync (08-15) |
| **Q15** | VLM quota ladder semantics | **Sticky per run; all calls incl. the sweep; fallback verdicts deliver + flag; prev key as automatic last resort** (08-15 → R23) |
| **Q16** | New Gemini key | **Supplied 08-15** (AQ.-format — endpoints settled by the §7.6 smoke matrix); old key kept as `GEMINI_API_KEY_PREV` for the R23 rung |
| **Q17** | Build-session review protocol | **Adversarial code-review loop ≤5 iterations; leftovers flagged to Adnaan; then an independent FULLY-LIVE e2e verifier (real VLM calls, Drive II `_pipeline_test/` only); path-scoped commit per green iteration, never push** (08-15). **Amended mid-loop 08-15 (after iteration 1): iterations 2–4 EACH run full-codebase review + delta review (files changed since loop start) + adversarial hunting for bugs introduced by the loop's own fixes. Second amendment 08-15 (after iteration 4's launch): ~~iteration 5 runs delta + fix-regression lanes ONLY, no full-codebase lane~~. Third amendment 08-15 (during iteration 4's fixes, supersedes the second): iteration 5 ALSO runs the full composition — full-codebase review + delta review + adversarial hunting for bugs introduced by the loop's own changes/fixes; anything verified still remaining after iteration 5's fix round is highlighted to Adnaan severity-ordered. All iterations run** |
| **Q18** | Plan-doc versioning | **Commit plan + companion docs at every revision, path-scoped** (08-15; v2 = `10b70c3`) |

**Remaining action items**: (a) ~~gcloud SDK + auth + project~~ **done 08-15**; (b) if the §7.6
smoke matrix returns Vertex 403 — one console click to enable the Vertex AI API; (c) rotation
of ALL credentials after Phase 1 (§2 — both Gemini keys have appeared in chats); (d) ~~operator
renames~~ superseded by the Q5 amendment — instead: tell operators the folder rule is
"your name / player's email / session folder"; (e) ~~commit ok~~ **granted 08-15** — every
revision commits path-scoped (Q18).
