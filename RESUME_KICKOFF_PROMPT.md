# Resume — review loop iteration 4 (mid-fix), then r5, verifier, final report

You are resuming a build session in `/Users/adnaan/Documents/hl-gamedata`. The pipeline is
**LIVE** (go-live 2026-08-15 16:30 IST, commit 67b32ea) on the GCP VM and delivering real
player sessions. You are mid-way through the adversarial review loop. Read
`PIPELINE_IMPLEMENTATION_PLAN.md` §4/§6/§18 and `VM_BUILD_KICKOFF_PROMPT.md` (esp. its
Phase 2/3 sections and the Q17 amendments inside) before touching anything.

## Ground rules (unchanged, load-bearing)

- Machine-wide CLAUDE.md: verify before claiming; read whole sources; mark `[assumption]`.
- Commits path-scoped per green step/iteration (Q17/Q18); NEVER push; never touch the vault.
- Secrets: `~/.config/hl-gamedata/secrets.env` (Mac + VM). Never print/log/commit keys; the
  Vertex URL embeds `?key=` — error strings carry endpoint tags, never URLs.
- Drive I read-only forever (R6). Test uploads only to Drive II `_pipeline_test/`, purged.
- Full suite after every step (Mac AND VM for code that ships):
  `PYTHONPATH=. uv run --with pytest --with numpy --with opencv-python-headless --with rerun-sdk pytest pipeline/tests translator/tests -q`
  Baseline before the uncommitted changes: **236 passed**.
- Deploy = `rsync -a --delete --exclude 'out/' --exclude '__pycache__/' --exclude '*.rrd' ./ hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline:hl-gamedata/`
  then on the VM re-touch rrd stubs: `for d in ~/hl-gamedata/2026-08-1*_c_*/; do case "$d" in *-analysis/) ;; *) touch "$d/session.rrd";; esac; done`
  If systemd units changed: re-template with sed (__USER__=$(id -un), __HOME__=$HOME,
  __BUCKET__=hl-gamedata-pipeline-backups) into /etc/systemd/system/ + daemon-reload.
- SSH alias works: `ssh hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline`. Both timers armed
  (hl-pipeline *:0/30, hl-backup 03:00 IST). drive-collect rclone scope is drive.readonly.
- The repo's pre-existing deletions/modifications in git status (HumynCapture.exe, sample
  dirs, .gitignore, SAMPLE_ANALYSIS_PLAYBOOK.md, qa_checks.py etc.) predate this work —
  LEAVE THEM UNCOMMITTED, never sweep them into a commit (path-scoped adds only).

## Review-loop state (Q17 as twice-amended)

Protocol: adversarial loop ≤5 iterations; iterations 2–4 = full-codebase + delta +
fix-regression lanes; **iteration 5 = delta + fix-regression ONLY** (second amendment,
commit 0607454). Verify every finding yourself against CURRENT code before acting (reviewers
cite line numbers from their read-time snapshot; reports.py especially has churned). Exit
after r5; leftovers go to Adnaan severity-ordered. Then Phase 3 (below).

Ledger so far (for the final report):
- r1: 33 raw → 32 confirmed / 1 refuted → **fixed 30** (7 BLOCKER, 11 MAJOR), 2 accepted
  (flight-slots-for-resumed-batches F7-bounded; commit 51be3a4, 192 green)
- r2: 50 confirmed / 2 refuted → **fixed 45**, accepted: bucket-name propagation,
  copy-only-backup restore hygiene; test debt: FIX_REROUTE/shift-record/retranslate
  media fixtures (74e3b8f + 7938925, 207 green)
- r3: 47 confirmed / 1 refuted → **fixed 44**, accepted: transitive-dep pinning
  (da00ec9, 225 green)
- r4: 42 confirmed / 1 refuted, 0 BLOCKER — **full list in `review-r4-findings.json`**
  (repo root; severity-sorted summary in each item; verifier reasoning included).
  Fixes IN PROGRESS — see next section.
- Workflow scripts for r1–r4 (crib the exact lane structure, schemas, verify pattern for
  r5): `/Users/adnaan/.claude/projects/-Users-adnaan-Documents-hl-gamedata/fc47f24f-632c-4779-8c6e-9cf9db494961/workflows/scripts/`
  (a fresh session cannot RESUME those runs — launch r5 as a new Workflow).

## UNCOMMITTED work in the tree (r4 fixes already APPLIED — suite NOT yet re-run)

`pipeline/run.py` + `pipeline/cutter.py` hold these applied-but-uncommitted r4 fixes:
1. Split-manifest ordering (#0/#4/#20): `_recover_split` no longer unlinks the manifest —
   the caller unlinks AFTER the SPLIT commit; adopt-rowed-children rule when rows exist,
   manifest gone, and no rowless partials.
2. `_discard_split_artifacts` (#5/#19): wipes rowless `{sid}-p*` dirs + manifest on ALL four
   rescinded-plan branches (unfixable / no-steps / out["error"] / no-≥70s-segment).
3. cutter pre-cleans an existing out_dir before cutting (#0 overwrite/merge protection).
4. `_reclaim_stale_lock`: rename-aside-then-delete (atomic; kills the rmtree TOCTOU,
   #2/#36/#39).
5. `_download_phase`: broad `except OSError` → transient re-queue (host-level errors must
   not quarantine, #3/#17); non-OSError still quarantines.
6. `_partition_resume` carry: TERMINAL states only (DELIVERED/REJECTED/SPLIT/DUPLICATE/
   QUARANTINED) — DISCOVERED/HOLD_VLM no longer ride (#35).
7. d_thread: HOLD_VLM sessions get ONE guaranteed batch per run BEFORE new intake (#9).

## Remaining r4 to-do (verify each against code first; then suite → path-scoped commit
"review iteration 4: …" → deploy → launch r5)

Code:
- ingest.py collision branch: heal MOVED/renamed folders for pre-download states — existing
  state in (DISCOVERED, INCOMPLETE) + clean parse at new path (+ md5 match when both known)
  → update drive_path/ctime + integrity flag (#6). QUARANTINED heal exists; mirror it.
- Quarantine-heal site: wipe stale work dir + `-analysis` (both supersede sites already do,
  #21).
- Supersede + heal: remove the sid's stale `translation_report.json` entry in cfg.work
  (extend `validate._locked_report_update` with a delete mode or add
  `locked_report_remove`) — replacement uploads must not validate against the old shift
  (#7).
- Scan cross-dup clobber branch (ingest.py ~336): require ALL dupes clobberable
  (DISCOVERED/INCOMPLETE), reject all of them, else reject the incoming copy with the F3
  deviation note (#37 third-copy hole).
- vlm.py `_generate_once` network-except: scrub `key=` from exception text before it enters
  `last` (re.sub(r"key=[^&\s]+", "key=***", str(e))) — http.client.InvalidURL embeds the
  URL (#25).
- validate.py `_locked_report_update` stale-break: rename-aside like `_reclaim_stale_lock`
  (#38/#45 residual).
- validate.py: `aux["notes"]` is WRITE-ONLY — surface it into the verdict's advisories
  (find where map_reasons/advisories assemble; extend with aux notes) or the r3 F5 fix is a
  no-op (#18/#22).
- fix.py plan_fixes: FIX_HEADER_REWRITE must run BEFORE FIX_GATE_WINDOW / FIX_CUT_SEGMENTS
  (gate.py/cutter.py hard-assert a v2 header; v1-header sessions currently burn the fix
  budget → wrongful reject, #23). Extend `_pre_cut_csv_fixes` want-list + emit before the
  gate append; dedupe against the later csv loop (its `seen` set won't know).
- run(): the `except RuntimeError` around ingest.scan → broaden to Exception (#29).
- systemd/hl-backup.service.in: add `TimeoutStartSec=3600` (#33).
- DELETE `pipeline/launchd/` (retired plist, second-pipeline footgun, #34). git rm.
- vm_setup.sh acceptance: add an rclone gcs-backup list check against $BUCKET (#31, cheap).
Tests (write the cheap ones, note the rest as debt):
- next_batch exclude-before-slice starvation regression (#15); acquire_lock recycled-pid +
  rename-reclaim (#16); cutter manifest-producer protocol (#12: failure → dirs cleaned + no
  manifest; success → manifest lists exactly the cut segments); §1.4 rrd floor branch (#10);
  cross-midnight staged_date pinning (#11); orphan-sweep -analysis/missing-dossier triggers
  (#30); md5-mismatch test pins kind="quarantine" (#28); scan-crash containment (#29).
Accepted (record in commit msg): #32/#40 non-Linux _pid_is_pipeline fallback (prod is
Linux); #31 already accepted r3 (now with the cheap check).

## After r4 lands: iteration 5 (NEW Workflow; delta + fix-regression lanes ONLY)

- Delta range: `git diff 67b32ea..HEAD` (loop start is 67b32ea). Delta now includes ALL the
  reports.py schema work (v2→v4, offset, late-arrival guard) — give it a dedicated lane.
- Fix-regression lanes attack r3+r4 fixes AND the payment-sheet rework (cohort walk,
  late-arrival stamps, mark-before-marker ordering, anchor seeding).
- Same structured-output schemas + adversarial verify (default-REFUTE) as r1–r4 scripts.
- Then: verify → fix → suite → commit "review iteration 5: …" → deploy. If verified
  BLOCKER/MAJOR remain after r5, STOP and present to Adnaan severity-ordered.

## Phase 3 — independent live e2e verifier (after r5)

Fresh agent, not one that wrote/reviewed code, authority to run everything live: full suite
Mac+VM; `python -m pipeline run` smoke both hosts; §7.6 smoke matrix LIVE (both keys ×
genlang+vertex + ladder-id probes — vertex was 403 API_KEY_SERVICE_BLOCKED for both keys on
08-15, expect same unless Adnaan unblocked); synthetic gate-(b) run on the VM (seed 6 local
benchmark sessions at INGESTED into `HL_PIPELINE_HOME=~/hl-pipeline-test`, test-mode
Telegram, deliver to Drive II `_pipeline_test/`, checksum verify, purge via
`deliver.cleanup_test_folder`); 2-leg kill matrix with resume assertions; secrets sweep (no
key in logs/ledger/dossiers/Telegram). Its verdict is REPORTED AS-IS.

## Final report (one message; spec in VM_BUILD_KICKOFF_PROMPT.md "Report back")

Verdict-first per §18 step; Day-0 numbers (already in plan §15: up 183 / down 474 Mbit/s;
56.1 min/fh single-worker ≈ 2.0× M5; Gemini 1.5 s from Mumbai); smoke-matrix table (genlang
OK both keys, vertex 403 both, 3.1-pro→gemini-3.1-pro-preview corrected); review-loop ledger
(above + r4/r5 results); verifier verdict verbatim; go-live status (live 16:30 IST 08-15,
3-leg kill matrix green, ~30+ delivered; R22 held at 8 workers pending CPU% reading); open
items for Adnaan, priority-ordered:
1. Gemini billing tier still unverified (vault has an OPEN CONTRADICTION on tier rules).
2. Rotate ALL credentials after Phase 1 (both keys + TG token were pasted in chats; both
   Gemini keys are genlang-only — vertex blocked by key restriction; unblocking enables R21
   failover: re-run smoke, flip VLM_FAILOVER_ENABLED, one-line commit).
3. §17.6 operator-label hygiene (free-text names → rollup dupes possible).
4. Pending-cohort stderr log → Telegram routing? (Adnaan's call; d3 flagged.)
5. Sheet column naming inconsistencies (hours/hrs, delivered/accepted) — Adnaan's exact
   strings kept deliberately; d3 flagged for harmonization.
6. Sheet column ORDER (three pairs vs all-kamla-then-ow) — d3 flagged.
7. R22 ramp 8→10 decision needs a CPU%-based reading on a full batch.
8. Old-format sheet went out on Telegram 08-15 before the schema respecs; artifacts on disk
   are corrected (d3); re-send is Adnaan's call.
9. Test debt needing real media fixtures: FIX_REROUTE orchestration, shift-record subsystem,
   retranslate fix paths, AFK/static VLM-label filters in _build_aux.

## Peer session protocol (hl-gamedata-d3)

Another Claude session (`hl-gamedata-d3`, reply address `uds:/tmp/cc-socks/55364.sock` via
SendMessage) relays Adnaan's payment-sheet respecs and independently rebuilds the reference
artifacts (`payment-2026-08-15.csv` repo root + VM `~/hl-pipeline/reports/2026-08-15/`).
Division of labor: THEY never edit pipeline source; YOU never edit their artifacts.
Acceptance protocol for any sheet change: pull a fresh ledger snapshot
(`sqlite3 ~/hl-pipeline/ledger.db ".backup /tmp/s.db"`, scp it), run build_sheet_rows with
the artifact's window, diff — must be byte-identical. Current sheet contract (commit
e9dad80): 11 columns (date, operator, player_email, kamla/ow_hrs_uploaded,
kamla/ow_accepted_hrs, kamla/ow_rejection_reasons, total_uploaded_hours,
total_delivered_hours), cohort accounting (recursive tree walk, SPLIT contributes nothing),
REPORT_OFFSET_H=4.0, contiguity anchor `~/hl-pipeline/reports/.last_daily_sent` (VM-seeded
to 2026-08-15T06:45:22+00:00 — do NOT disturb), late-arrival guard via additive ledger
column `uploaded_reported_at` (VM backfilled: 2 roots stamped), pending computed internally
→ loud stderr log only, unreadable-reasons marker distinct from fix-failed at all five
reject surfaces. Adnaan's operator note ("nothing is lost") was cleared as safe after
e9dad80.

## VM / production facts

Project hl-gamedata-pipeline, VM hl-pipeline-vm (e2-standard-16, asia-south1-a), bucket
gs://hl-gamedata-pipeline-backups. Pipeline home `~/hl-pipeline/`. ~30+ sessions DELIVERED
(≥2 h) as of last check; collection ramping (uploads arriving continuously). Deps pinned in
the unit: numpy==2.4.6 opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0. Known quarantine:
"exerising kamla" junk folder (correct behavior). Two incomplete folders pending
(giveusheirloom, harshitrameja — missing video.mp4); when they complete they'll late-attribute
correctly. VLM ladder verified: gemini-3.7-flash → gemini-3.5-flash → gemini-3.1-pro-preview,
prev-key rung armed, failover dark (vertex key-blocked).

Begin by running the full suite (the uncommitted r4 fixes have NOT been tested), fix any
breakage, then continue the r4 to-do list above.
