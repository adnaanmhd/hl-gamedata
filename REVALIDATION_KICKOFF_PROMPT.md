# Kickoff — black-frozen recalibration → full re-validation → payment-sheet rebuild → ozark-delivery repopulation

You are starting a fresh session in `/Users/adnaan/Documents/hl-gamedata`. The pipeline is
LIVE on the GCP VM (project `hl-gamedata-pipeline`, VM `hl-pipeline-vm`, asia-south1-a).
Review iterations r1–r5 are committed; suite baseline **310 passed on Mac AND VM** (HEAD
includes `d09fb8a` zip-stall clock + `d99c6dc` analyze_sample tracking + `f846c52` plan
R22 reading). Read `PIPELINE_IMPLEMENTATION_PLAN.md` §4/§5/§6/§18 before touching code.

## Why this session exists (verified 2026-08-16, evidence on the VM)

The `CNT_BLACK_FROZEN` reject reason mass-false-positived on Kamla. Kamla ("Find Kamla")
is a **dark horror game**: its legitimate scenes average below luma 16 on the scanner's
160×90 grayscale downscale, so the old rule (frame "near-black" when mean luma <16;
reject when >50% of frames near-black; plus a motion arm at baseline <0.3) rejected real
gameplay. Evidence: **all 122** black-frozen ledger rows measured 50–76% near-black —
none ≥90%, i.e. the signature of a true capture failure (uniform ~100% black) appears
**zero times**. Frames the metric called "near-black" were visually confirmed as live
gameplay (torch + smoke + crosshair + health bar at luma 7.4; furnished rooms at 10–14).
23 sessions (2.1 h, 23 players) had it as their SOLE blocking reason; 99 more rows
(20.4 h) carried other reasons that may share the dark-frame root cause (VLM mislabels,
deflated motion) — NOT yet verified either way. Sample frames:
VM `/tmp/bfcheck/*.jpg` + the two downloaded videos in `/tmp/bfcheck/{kumail,tiger}/`
(re-download from Drive I if purged; paths in the ledger).

## Ground rules (unchanged, load-bearing)

- Machine-wide CLAUDE.md: verify before claiming; read whole sources; mark `[assumption]`.
- Commits path-scoped per green step; NEVER push; never touch the Obsidian vault.
- Secrets: `~/.config/hl-gamedata/secrets.env` (Mac + VM). Never print/log/commit keys;
  vertex URLs embed `?key=` — error strings carry endpoint tags, never URLs.
- **Drive I (`drive-collect:`) read-only forever (R6).** It is the archive that makes
  this whole rebuild possible.
- Full suite after every step (Mac AND VM for code that ships):
  `PYTHONPATH=. uv run --with pytest --with numpy --with opencv-python-headless --with rerun-sdk pytest pipeline/tests translator/tests -q`
  VM: `ssh hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline 'cd ~/hl-gamedata && PYTHONPATH=. ~/.local/bin/uv run --with pytest --with numpy==2.4.6 --with opencv-python-headless==5.0.0.93 --with rerun-sdk==0.36.0 pytest pipeline/tests translator/tests -q'`
- Deploy: `rsync -a --delete --exclude 'out/' --exclude '__pycache__/' --exclude '*.rrd' ./ hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline:hl-gamedata/`
  then re-touch rrd stubs: `for d in ~/hl-gamedata/2026-08-1*_c_*/; do case "$d" in *-analysis/) ;; *) touch "$d/session.rrd";; esac; done`
- Peer sessions: `hl-gamedata-b0` (docs session; wrote `PIPELINE_ARCHITECTURE.md`;
  **Adnaan is reachable through it** — relays confirmed 08-15). d3 (payment respec
  session) is GONE; `NOTE_FOR_D3.md` at repo root is the dead-drop — update it when sheet
  semantics/artifacts change. Pre-existing uncommitted deletions/modifications in git
  status predate everything — LEAVE THEM UNCOMMITTED.

## Locked decisions (Adnaan, 2026-08-16 — do NOT re-ask)

1. **New black-frozen rule**: a frame is "dead-black" when mean luma **< 5** (was 16);
   the session rejects when **≥ 50%** of frames are dead-black. Nothing else rejects.
2. **The frozen-motion arm is DROPPED** (the `baseline < 0.3` elif at validate.py:925-929
   deletes entirely). `aux["video_active"]` and `scanner_stats` stay — the
   INP_KEYS_MISSING check at validate.py:470-483 depends on `video_active`; keep that
   interplay coherent (near-static + zero keys stays advisory-only; that is accepted).
3. **Re-validate EVERYTHING** — all 212 root sessions (60.4 h), including the 233
   DELIVERED rows. Verdicts may move in BOTH directions (VLM re-runs are not perfectly
   repeatable; demotions are accepted).
4. QUARANTINED (9) and DUPLICATE rows: **untouched**.
5. **No other threshold/VLM changes this pass.** After re-validation, deliver an
   exhaustive `rejection reason × count of sessions` table — Adnaan decides next steps
   from it (decision gate for any future cascade fixes; do not act on it yourself).
6. Payment sheets: regenerate for **every day since first upload (08-15 onward)** under
   the new verdicts; **resend each day's corrected sheet to Telegram marked as
   superseding** AND keep files/GCS; **purge the old sheet files** (VM reports dirs +
   GCS mirrors; note the obsolete repo-root `payment-2026-08-15.csv` reference in
   NOTE_FOR_D3.md). Old sheets are VOID for payment; players are paid per the new ones.
   Folder-issues reports are unaffected; per-batch Telegram history is NOT replayed.
7. **Drive II = "ozark-delivery"** (the `drive-deliver:` rclone remote; top level:
   `humynlabs/`, 807 objects / 73.5 GiB at decision time). Client (Odyssey/Protege) has
   pulled NOTHING — confirmed. Repopulation order: **upload the rebuilt set first under
   new upload-date folders (spec: path date = UTC upload date), checksum-verify, THEN
   delete the old date folders.** No empty-drive window. rrd sampling: **resample fresh**
   (random 20% per game per day).
8. **Rebuild runs exclusively**: stop/disable the `hl-pipeline` timer for the duration
   (Adnaan-authorized; re-enable + verify armed at the end). Nightly backup timer stays.
   Expected ~7–8 h validation wall-clock at 8 workers + ~10⁴ VLM calls (R23 ladder
   protects quota) + ~160 GB re-download (free). Aug-24 clock: prefer evening start.
9. Player coaching correction for the 23 wrongly-coached players: **Adnaan handles it
   himself** — do nothing player-facing.

## Steps, in order

1. **Code change** (`pipeline/validate.py`, ~lines 918-929 + reject site ~515-520):
   implement the new rule (constants named/config-visible; evidence string reports the
   measured dead-black %); delete the motion arm; update the fallback evidence text
   (the "borderless-windowed coaching" wording stays for true positives). Regression
   tests (new, non-tautological): dark-gameplay luma profile (e.g. 60% of frames at
   luma 8–15) PASSES; ≥50% of frames under 5 REJECTS; baseline 0.1 alone no longer
   rejects; the reports label mapping (test_reports_pace.py:385/575/598) stays green.
   **Acceptance:** re-run `scanner.scan_video` on BOTH `/tmp/bfcheck` videos and assert
   each now passes (compute frac(luma<5) — expected well under 50%; record the numbers).
   Suite green Mac.
2. **Adversarial review** of the diff: spawn code-reviewer agents (full-diff review +
   bug-hunt for regressions the change could cause — e.g. INP_KEYS_MISSING interplay,
   sessions that formerly rejected early now flowing into fix/cutter paths, dossier
   wording). Verify findings against the tree, fix confirmed ones, add tests, commit
   path-scoped (r-loop message style, cite this prompt).
3. **Deploy + freeze**: stop+disable hl-pipeline.timer on the VM (wait for any in-flight
   run to drain — check `run.lock` pid); deploy; VM suite green.
4. **Rebuild design before touching the ledger** (write the plan into the session, then
   execute): reset all non-QUARANTINED/non-DUPLICATE rows to re-validatable state
   preserving upload identity + events audit (supersede-style column resets: verdicts,
   bins, reasons, durations_delivered, rrd_sampled, delivered_at, fix_attempts,
   **uploaded_reported_at=NULL** so regenerated sheets recount cleanly); tear down SPLIT
   child rows (they are derived — the cutter re-derives); clear the daily-report anchor
   (`~/hl-pipeline/reports/.last_daily_sent`) + per-day `.sent` markers for regenerated
   days IN A COORDINATED way with step 6 (design the exact order the way r5's
   stamps→anchor→marker BLOCKER fix demands — no double-count, no lost window);
   `translation_report.json` per-sid entries cleared via the existing locked helpers.
   Back up `ledger.db` BEFORE any reset (`.backup` + copy to GCS) — this is the abort
   parachute. Suppress per-batch Telegram during the rebuild (log only): one start
   message, one completion summary.
5. **Run the re-validation** (batched through the normal driver in rebuild mode,
   overnight); monitor stage lines; every session must land terminal
   (DELIVERED-equivalent READY→…→DELIVERED via step 7's upload, REJECTED, SPLIT).
6. **Regenerate payment sheets** for each day window since 08-15 (original window
   boundaries, new verdicts; the sheet code's counted_out/stamp path does the
   bookkeeping); resend each to Telegram marked "SUPERSEDES <date> sheet — methodology
   v2 (black-frozen recalibration)"; purge old sheet files after their replacements are
   verified; final summary message. Payment is the FIRST deliverable priority after
   validation completes.
7. **Repopulate ozark-delivery**: package+upload all newly-valid deliverables under the
   new upload-date folder(s), checksum-verify remote (existing deliver.py machinery),
   resampled rrd 20%/game/day, no stub rrd ever staged; then delete the OLD date folders
   (08-15/08-16 trees) — deletion is the LAST destructive act and only after the new
   tree verifies complete against the ledger.
8. **Reject-reason table**: exhaustive `reason code × count of sessions` (plus hours)
   over the post-rebuild ledger, compared side-by-side with the committed pre-rebuild
   baseline **`reject-reasons-pre-rebuild.json`** (repo root — the exact table Adnaan
   reviewed on 08-16: 138 rows / 26.3 h / 113 recordings, CNT_BLACK_FROZEN on 126 rows;
   cross-check against the step-4 ledger backup if they disagree). The MID/EDGE_
   NONGAMEPLAY deltas in that diff ARE the dark-frame-cascade measurement. This is a
   decision input for Adnaan — present, don't act.
9. **Independent live verifier** (fresh general-purpose agent, never one that wrote or
   reviewed this session's code; verdict relayed VERBATIM; BLOCKED-with-error never
   becomes a pass). Checks: suite both hosts; new-rule unit evidence (the two /tmp/bfcheck
   videos pass); ledger consistency (every non-quarantine/dup row terminal; hours counted
   once; no stub rrd staged); Drive II tree ↔ ledger DELIVERED set exactly matches,
   checksums spot-verified, old date folders gone; regenerated sheets exist for every day
   + markers/anchor coherent; old sheets purged; timers re-armed (hl-pipeline re-enabled
   AFTER verifier's production checks); secrets sweep (counts only); read-only on
   production except where a check says otherwise; test uploads only under
   `_pipeline_test/`, purged after.
10. **Re-enable the timer**, watch one live tick complete, then **final report**
    (verdict-first): before/after state table; per-player delivered-hours delta (payment
    impact of the recalibration); the reason×count table; sheets resent (list);
    ozark-delivery repopulation numbers (sessions/files/GiB, old tree deletion proof);
    verifier verdict verbatim; anything verified-but-unfixed severity-ordered; open
    items (Gemini tier still unverified; credential rotation deferred by Adnaan 08-16;
    vertex failover still dark; R22 hold at 8 confirmed by the 94.5% CPU reading).

## VM / production facts

Remotes: `drive-collect:` (Drive I, READ-ONLY), `drive-deliver:` (Drive II =
ozark-delivery), `gcs-backup:` (bucket `hl-gamedata-pipeline-backups` — 2,624 objects,
verified healthy 08-16). Pipeline home `~/hl-pipeline/` (ledger.db, dossiers/, reports/,
logs/, work/). Timers: hl-pipeline *:0/30 (to be paused in step 3), hl-backup 03:00 IST.
Deps pinned: numpy==2.4.6 opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0. Workers 8
(R22 hold, CPU-reading-backed). VLM ladder: gemini-3.7-flash → 3.5-flash →
3.1-pro-preview → prev-key; failover dark (vertex key-blocked). Folder-issues report
LIVE daily ≥14:00 IST after payment `.sent` (do not break its markers for days you are
not regenerating). Ledger snapshot at prompt time: 212 roots / 60.4 h; states 233
DELIVERED / 134 REJECTED / 131 SPLIT / 20 INGESTED / 10 VALIDATING / 9 QUARANTINED /
1 DISCOVERED (will have drifted — re-query, don't trust these numbers).

Begin with step 1. Verify every line number cited here against the current tree first —
they drift.
