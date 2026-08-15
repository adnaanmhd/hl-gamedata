# Resume — r5 fixes APPLIED+GREEN (uncommitted), then r5 tests → commit → deploy → Phase 3 verifier → final report

You are resuming a build session in `/Users/adnaan/Documents/hl-gamedata`. The pipeline is
**LIVE** on the GCP VM and delivering. Review iterations 1–4 are committed and deployed;
**iteration 5's adversarial review has RUN and its code fixes are APPLIED to the working
tree, suite-verified (276 passed on Mac) but NOT COMMITTED** — the iteration completes when
its regression tests land. Read `PIPELINE_IMPLEMENTATION_PLAN.md` §4/§6/§18 (note the Q17
THIRD amendment in §19) and `review-r5-findings.json` (repo root) before touching anything.

## Ground rules (unchanged, load-bearing)

- Machine-wide CLAUDE.md: verify before claiming; read whole sources; mark `[assumption]`.
- Commits path-scoped per green step/iteration (Q17/Q18); NEVER push; never touch the vault.
- Secrets: `~/.config/hl-gamedata/secrets.env` (Mac + VM). Never print/log/commit keys; the
  Vertex URL embeds `?key=` — error strings carry endpoint tags, never URLs.
- Drive I read-only forever (R6). Test uploads only to Drive II `_pipeline_test/`, purged.
- Full suite after every step (Mac AND VM for code that ships):
  `PYTHONPATH=. uv run --with pytest --with numpy --with opencv-python-headless --with rerun-sdk pytest pipeline/tests translator/tests -q`
  Baseline WITH the uncommitted r5 fixes: **276 passed** (Mac). VM is one deploy behind
  (at commit 2d90bbd, 272 passed there).
- Deploy = `rsync -a --delete --exclude 'out/' --exclude '__pycache__/' --exclude '*.rrd' ./ hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline:hl-gamedata/`
  then on the VM re-touch rrd stubs: `for d in ~/hl-gamedata/2026-08-1*_c_*/; do case "$d" in *-analysis/) ;; *) touch "$d/session.rrd";; esac; done`
  If systemd units changed (none pending): re-template with sed + daemon-reload.
- VM suite invocation: `ssh hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline 'cd ~/hl-gamedata && PYTHONPATH=. ~/.local/bin/uv run --with pytest --with numpy==2.4.6 --with opencv-python-headless==5.0.0.93 --with rerun-sdk==0.36.0 pytest pipeline/tests translator/tests -q'`
- The repo's pre-existing deletions/modifications in git status (HumynCapture.exe, sample
  dirs, .gitignore, SAMPLE_ANALYSIS_PLAYBOOK.md, qa_checks.py etc.) predate this work —
  LEAVE THEM UNCOMMITTED. `tools/analyze_sample.py` is deliberately-left UNTRACKED but
  carries a review fix (key= scrub in the generic VLM-sweep handler) — it deploys via
  rsync, never via git; flagged to Adnaan in the final report.

## Commits landed this session (ledger for the final report)

- `b35df90` plan: Q17 THIRD amendment (Adnaan, mid-session): **iteration 5 runs the full
  composition** (full-codebase + delta + fix-regression), superseding the delta-only second
  amendment; anything verified still remaining after r5's fix round is highlighted to
  Adnaan severity-ordered.
- `bb63cda` review iteration 4: 29 fixes + 8 test-gap closures (42 confirmed / 1 refuted,
  0 BLOCKER; 2 accepted [#32/#40 non-Linux pid fallback], 1 accepted-with-check [#31
  bucket], 2 media-fixture test-debt) + 28 regression tests. Deployed; VM green.
- `da4e119` folder-issues daily report (NEW FEATURE, Adnaan via d3): incomplete uploads +
  badly-named folders; separate Telegram message + one CSV, marker-guarded snapshot; +6
  tests. Acceptance vs the live VM ledger returned exactly the 3 expected rows.
- `2d90bbd` folder-issues amendments A+B (Adnaan via d3): payment-message heartbeat count
  line ("folder issues: N — see next message" / "folder issues: 0"); guarded ghost-
  incomplete-row prune. +2 tests. **Today's (08-15) first-ever folder-issues send is HELD**:
  `~/hl-pipeline/reports/2026-08-15/.issues-sent` was touched manually at 19:33 IST before
  anything fired (verified: no log line, no CSV). Adnaan decides when sends start (delete
  the marker for today, or let tomorrow's fire) — his answer comes via d3. Do NOT unhold on
  your own.

## Review-loop ledger so far

- r1: 33 raw → 32 confirmed / 1 refuted → fixed 30, 2 accepted (51be3a4, 192 green)
- r2: 50 confirmed / 2 refuted → fixed 45, 2 accepted + test debt (74e3b8f+7938925, 207)
- r3: 47 confirmed / 1 refuted → fixed 44, 1 accepted (da00ec9, 225 green)
- r4: 42 confirmed / 1 refuted, 0 BLOCKER → 29 fixed + 8 test-gaps closed, 2 accepted,
  1 accepted-with-check, 2 test-debt (bb63cda, 264 green)
- r5 (full composition per third amendment): 47 raw → 46 dedup → **44 confirmed
  (1 BLOCKER / 22 MAJOR / 21 MINOR), 2 refuted, 0 uncertain** — full detail in
  `review-r5-findings.json`. Heavy cross-lane duplication; the DISTINCT findings and their
  disposition are below. Additionally a FOCUSED REVIEW of the folder-issues diff (which
  landed after r5's snapshot) found 4 MAJOR + 4 MINOR + 1 pre-existing note — all fixed in
  the uncommitted batch except the accepted ones listed below.

## UNCOMMITTED work in the tree (APPLIED + suite-green at 276; commit = step 2 below)

All r5 + focused-review code fixes. By file (verify anchors before further edits):

`pipeline/run.py`:
1. BLOCKER r5 idx39: `send_daily_report_if_due` persistence order is now
   **stamps → anchor → marker** (was anchor-first — a kill between anchor and stamps made
   the next tick re-count a whole window's hours as late arrivals). Comment rewritten.
2. r5 idx3-group: the sheet's counted roots thread through
   `write_payment_sheet(..., counted_out=counted)` → `mark_uploads_reported(..., sids=counted)`
   — stamping exactly what the sheet counted (the old stamp-time re-derive raced D).
3. r5 idx4: run-start `backup_daily` wrapped in try/except → alert (was: any backup error
   killed every tick).
4. r5 idx25: `_download_phase` transient arm now `(OSError, sqlite3.OperationalError)`.
5. r5 idx26: new `_partial_dirs()` helper — all three `{sid}-p[0-9]*` glob sites now
   fullmatch `-p\d+$` so grandchild dirs are never wiped as rowless partials.
6. r5 idx38: `_recover_split` — an EXISTING-but-unreadable (OSError) manifest returns
   `(False, have_rows)` touching NOTHING (was: treated as absent → wiped a complete cut).
7. r5 idx27: adopted-split branch best-effort `fix._propagate_shift_record` to children.
8. r5 idx22: `_partition_resume` skips terminal-member carry for sids already in a
   FINISHED batch's summary (no double batch-message reporting).
9. Focused #1: overlap end-of-run + `daily-report` CLI now call
   `send_folder_issues_if_due` (idle production ticks never reached the drain-loop site).
10. Focused #5: `send_folder_issues_if_due` requires today's payment `.sent` marker first.
11. Imports added: `re`, `sqlite3`.

`pipeline/reports.py`:
12. `mark_uploads_reported(ledger, lo, hi, sids=None)` — sids path stamps exactly those.
13. `build_sheet_rows(..., counted_out=None)`: in_window now ALSO requires the root be
    unstamped (r5 idx33/43 — anchor loss/rewind can never re-count); late guard counts
    terminal REJECTED never-downloaded roots (idx12); **late arrivals DEFER until their
    tree is settled** (idx29 — instant counting froze accepted_hrs at 0 under the stamp),
    logged `LATE ARRIVAL DEFERRED`; counted_out gets countable-or-REJECTED counted roots.
14. Focused #2/#6/#8: folder-issues message degrades to counts-only above 3500 chars;
    stray rows no longer print the path twice; list-1 age parse guarded.

`pipeline/ingest.py`:
15. r5 idx23/7: the QUARANTINED heal now resets the slot like supersede —
    fix_attempts=0, durations NULL, rrd_sampled=0, delivered_at NULL,
    uploaded_reported_at NULL (plus the already-committed reasons-clear + wipe + report-
    entry removal).
16. r5 idx41: move-heal md5-unknown case requires the PLAYER segment unchanged (operator
    rename still heals; cross-player same-id relocation stays a collision).
17. r5 idx2: path-quarantine rows for non-session-shaped basenames get a path-derived sid
    (`name~md5[:8]`) — no more PK collisions collapsing two players' junk folders; legacy
    bare-name rows for the same path are respected (no dup insert).
18. r5 idx1: QUARANTINED INT_PATH rows whose folder VANISHED from a healthy listing get
    reasons cleared + same-state audit event + `[bad-path-resolved]` log — drops off the
    chase list, audit trail preserved. Same games_present guard as the incomplete prune.
19. Focused #4: the prune guard's `games_present` is now built from PARSED content
    (sessions + depth>=2 quarantined paths), not listed_dirs — the bare game-dir entry or
    root junk file no longer satisfies it.

`pipeline/ledger.py`:
20. r5 idx7-group: `supersede` clears `uploaded_reported_at`.
21. r5 idx4/28: `backup_daily` pre-cleans stale `.ledger-*.db.tmp` files and unlinks its
    tmp on failure (BaseException) before re-raising.

Tests updated (semantic re-pins, all deliberate): `test_reports_pace.py` — spies accept
`**kw`; `_sheet_and_mark` uses counted_out+sids (production wiring); late-arrival tests
re-pinned to settle-then-count; conservation test settles the slow root; cohort test
stamps counted before asserting later-window-empty. `test_review_r3.py` — spy `**kw`.
`test_folder_issues.py` — `_payment_sent` precondition helper; +4 tests (prune guard needs
parsed content, heal clears stale INT_PATH reasons, overlong message degrades, end-of-run
wiring calls payment-then-issues in BOTH modes).

## r5 disposition map (do not re-fix; dupes were cross-lane)

FIXED (above): idx 0/5/8/11/17/24/31/37 (one wiring fix), 1, 2, 3/6/32/42 (one fix),
4, 7/10/30/36/40 (one fix), 9/23 (one fix), 12, 22, 25, 26, 27, 28, 29, 33/43 (one fix),
34 (earlier), 38, 39, 41.
STILL TO DO — the 8 TEST-GAP findings (r4's fixes lacking regression tests):
idx 13 (adopt-rowed-children rule), 14 (_discard_split_artifacts on all four rescinded
branches), 15 (HOLD_VLM guaranteed batch WITH competing intake — the existing test seeds
no intake), 16 (late-arrival run.py wiring: stamps→anchor→marker order + counted
threading, kill-interstice conservation), 18 (_download_phase OSError→transient branch),
19 (partition-carry exclusion of DISCOVERED/HOLD_VLM), 20 (cutter pre-clean), 21
(_sweep_terminal_work stray-manifest reclamation).
ACCEPTED (record in the r5 commit message + final report): idx 35 (post-outage 48h-clamp
gap drops rejects from the daily MESSAGE counts only — sheet conservation unaffected;
Adnaan note), r5 idx38 second half (cutter pre-clean cannot consult ledger rows — residual
narrowed by fix 6), focused #7 (kill between marker and send_document loses the CSV for a
day — same doctrine as the payment report), focused #5b (heartbeat count vs list computed
minutes apart may drift — cosmetic), focused note #9 (zip-incomplete rows flicker in/out
of the incomplete table with reset ages so the >48h escalation never trips for them —
needs an Adnaan ruling, final-report item).

## Next steps, in order (was mid-step-1 when the session ended)

1. **Write the r5 regression tests** — 3 parallel agents, one NEW file each, mirroring the
   r4 pattern (agents read conftest + existing tests first, run their file, then the full
   suite; no source edits; no tautologies):
   (a) `pipeline/tests/test_review_r5_splits.py`: idx 13, 14, 20, 21 + pin the new fixes
   5/6/7 (glob tighten spares a rowed grandchild dir; unreadable-manifest touches nothing;
   adopted split propagates the shift record).
   (b) `pipeline/tests/test_review_r5_driver.py`: idx 15 (guaranteed hold batch WITH
   DISCOVERED intake present), 16 (stamps-before-anchor order pin via spies + counted
   threading; kill-interstice: stamped-but-no-marker regeneration yields no double count),
   18, 19 + pin fix 8 (already-reported terminal member not re-carried).
   (c) `pipeline/tests/test_review_r5_ingest_reports.py`: pin fixes 15-21 (heal slot
   reset incl. budget; move-heal player gate blocks cross-player md5-unknown; phantom-sid
   keying two-same-basename case + legacy row respected; bad-path vanished clear +
   guard; supersede clears stamp; backup tmp pre-clean + failure unlink; in_window skips
   stamped root; REJECTED never-downloaded late arrival reaches the sheet once).
2. Full suite Mac → **commit path-scoped**: `review iteration 5: …` — include the ledger
   line (44 confirmed / 2 refuted / 0 uncertain; 1 BLOCKER fixed: payment-sheet
   anchor-before-stamps double-count), the disposition map, and the accepted list. Also
   commit `review-r5-findings.json` alongside (r4 precedent).
3. Deploy to VM + VM suite green. Verify the held marker survives the rsync
   (`ls ~/hl-pipeline/reports/2026-08-15/.issues-sent`).
4. **Message d3** (SendMessage to `uds:/tmp/cc-socks/55364.sock`, reply-address may have
   rotated — check ListAgents): (i) announce you are the successor session so their relays
   reach you; (ii) tell them the payment-sheet semantics changed under r5 and their
   reference-artifact acceptance protocol must adopt it: stamps are now written for
   EXACTLY what a sheet counted, in_window skips stamped roots, supersede/heal clear the
   stamp, and LATE ARRIVALS ARE DEFERRED UNTIL THEIR TREE SETTLES (their "moves to
   tomorrow" figure may shift by one window for unsettled cohorts); (iii) ask whether
   Adnaan's folder-issues start-date decision came back.
5. **Phase 3 — independent live e2e verifier** (fresh agent, never one that wrote or
   reviewed code; its verdict is REPORTED AS-IS). Full prompt spec below — launch it as a
   general-purpose agent with exactly that brief.
6. **Final report** (one message, verdict-first per §18): Day-0 numbers (plan §15: up 183 /
   down 474 Mbit/s; 56.1 min/fh single-worker ≈ 2.0× M5; Gemini 1.5 s from Mumbai);
   smoke-matrix table (genlang OK both keys, vertex 403 API_KEY_SERVICE_BLOCKED both,
   3.1-pro→gemini-3.1-pro-preview corrected); the review-loop ledger above + r5 numbers;
   the folder-issues feature story (spec→amendments→hold) with the focused-review
   findings; verifier verdict verbatim; go-live status (live 16:30 IST 08-15, 3-leg kill
   matrix green; R22 held at 8 workers pending CPU% reading); open items for Adnaan,
   priority-ordered:
   1. Gemini billing tier still unverified (vault OPEN CONTRADICTION).
   2. Rotate ALL credentials after Phase 1 (both keys + TG token appeared in chats;
      vertex blocked by key restriction — unblocking enables R21 failover: re-run smoke,
      flip VLM_FAILOVER_ENABLED, one-line commit).
   3. Folder-issues first-send decision (today's held; tomorrow fires unless held again).
   4. §17.6 operator-label hygiene (free-text names → rollup dupes possible).
   5. `tools/analyze_sample.py` is untracked yet load-bearing (carries the key-scrub fix;
      rsync-only deploy) — decide whether to track it.
   6. Zip-incomplete rows' flickering first_seen defeats the >48h escalation (ruling).
   7. Post-outage 48h-clamp gap in daily-message counts (accepted r5 idx35; sheet safe).
   8. Sheet column naming/order harmonization (d3-flagged, deliberate for now).
   9. R22 ramp 8→10 needs a CPU%-based reading on a full batch.
   10. Test debt needing real media fixtures: FIX_REROUTE orchestration, shift-record
       subsystem, retranslate fix paths, AFK/static VLM-label filters, LadderGemini
       engine-subclass contract.
   Per the Q17 third amendment: if anything VERIFIED remains unfixed after step 2's
   commit, present it to Adnaan severity-ordered in this report — do not silently defer.

## Phase 3 verifier prompt (launch verbatim as a fresh general-purpose agent)

> You are an INDEPENDENT end-to-end verifier for a live production pipeline. You did not
> write or review this code; run everything for real and render pass/fail per check, with
> evidence. Your verdict is relayed VERBATIM. A check that cannot run is BLOCKED with the
> exact error, never a pass.
> Repo: /Users/adnaan/Documents/hl-gamedata (Mac). Production VM:
> `ssh hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline` (repo ~/hl-gamedata, pipeline
> home ~/hl-pipeline — production, read-only except where a check says otherwise).
> HARD RULES: secrets in ~/.config/hl-gamedata/secrets.env — NEVER print keys/tokens;
> report status codes + endpoint tags only (vertex URLs embed ?key=). Drive I read-only.
> Uploads only to Drive II `_pipeline_test/`, purged after via deliver.cleanup_test_folder.
> Synthetic runs use HL_PIPELINE_HOME=$HOME/hl-pipeline-test and cfg test_mode Telegram.
> Never stop/start production systemd units; never git commit/push; never edit source.
> CHECKS: (1) full suite Mac AND VM (pinned --with versions on VM). (2) `python -m
> pipeline run` spawn smoke both hosts in a throwaway HL_PIPELINE_HOME (workers>=2 —
> proves the spawn/__main__ guard; also run the suite's real-threads+pool tests
> explicitly). (3) §7.6 smoke matrix LIVE from the VM: {GEMINI_API_KEY, GEMINI_API_KEY_PREV}
> × {generativelanguage, aiplatform express} one tiny generateContent each +
> per-ladder-id probes (gemini-3.7-flash / 3.5-flash / 3.1-pro-preview); record a 4-cell
> status matrix; 08-15 expectation: genlang OK both, vertex 403 API_KEY_SERVICE_BLOCKED
> both — report what you measure; a few cents total, no retry storms. (4) synthetic
> gate-(b) e2e ON THE VM in the test home: seed the six benchmark dirs
> (~/hl-gamedata/2026-08-1*_c_*, excluding -analysis; COPY, don't move) at INGESTED,
> run to completion, assert every session terminal, delivered files on Drive II under
> _pipeline_test/ checksum-verified, no stub rrd staged, hours counted once; then purge
> and verify empty. (5) 2-leg kill matrix in the test home: kill -9 during validation and
> during upload, resume each; assert every session terminal, no double-DELIVERED, exactly
> one dated remote dir per delivered sid, hours once, no duplicate batch messages. (6)
> secrets sweep over every artifact produced (test-home logs, ledger dump, dossiers,
> transcripts): grep -F for the actual key/token values — print match COUNTS only;
> expect zero outside secrets.env. (7) production spot-checks read-only: both timers
> armed; `pipeline status` runs; per-batch stage lines in logs; today's refreshed ledger
> backup exists; GCS bucket has last night's sync; the folder-issues hold marker for
> 2026-08-15 still in place. REPORT: verdict-first table (check → PASS/FAIL/BLOCKED),
> evidence per check, anomalies, overall GO / GO-with-notes / NO-GO.

## Peer session protocol (hl-gamedata-d3)

Another Claude session (`hl-gamedata-d3`) relays Adnaan's payment/report respecs and owns
the reference artifacts (`payment-2026-08-15.csv` repo root + VM
`~/hl-pipeline/reports/2026-08-15/`). THEY never edit pipeline source; YOU never edit
their artifacts. Their last-known reply address was `uds:/tmp/cc-socks/55364.sock` — this
may be stale; check ListAgents and announce yourself FIRST (step 4). Sheet contract as of
2d90bbd + the uncommitted r5 changes: 11 columns, cohort accounting (recursive walk,
SPLIT contributes nothing), REPORT_OFFSET_H=4.0, contiguity anchor
`~/hl-pipeline/reports/.last_daily_sent` (VM-seeded 2026-08-15T06:45:22+00:00 — do NOT
disturb), late-arrival guard via `uploaded_reported_at` with the NEW r5 semantics
(stamp-what-counted, in_window skips stamped, settle-before-late-count, supersede/heal
clear the stamp), heartbeat count line in the daily message, folder-issues report held for
08-15. Acceptance protocol for sheet changes: fresh ledger snapshot
(`sqlite3 ~/hl-pipeline/ledger.db ".backup /tmp/s.db"`, scp), run build_sheet_rows with
the artifact's window, diff — byte-identical, allowing for the r5 semantic deltas above
until d3 regenerates their reference.

## VM / production facts

Project hl-gamedata-pipeline, VM hl-pipeline-vm (e2-standard-16, asia-south1-a), bucket
gs://hl-gamedata-pipeline-backups. Pipeline home `~/hl-pipeline/`. Timers: hl-pipeline
*:0/30, hl-backup 03:00 IST (TimeoutStartSec=3600 since bb63cda). As of ~19:15 IST 08-15:
38 DELIVERED, 25 SPLIT, 17 REJECTED, 3 READY, 6 VALIDATING, 1+ QUARANTINED (incl.
"exerising kamla"; two more malformed paths quarantined ~19:30: a depth-5 nested folder
and a depth-3 stray under kamla/Rukaiya+Tanzeela). Incomplete: giveusheirloom (video.mp4),
harshitrameja (video.mp4+inputs.jsonl). Deps pinned in the unit: numpy==2.4.6
opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0. VLM ladder verified: gemini-3.7-flash
→ 3.5-flash → 3.1-pro-preview; prev-key rung armed; failover dark (vertex key-blocked).

Begin with step 1 (the three r5 test agents). The uncommitted fixes are already
suite-verified at 276 — do not re-derive them, but DO verify any finding you act on
against the current tree (reviewer line numbers drift).
