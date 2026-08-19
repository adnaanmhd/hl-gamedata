# Kickoff — execute the r17 K-set (K1–K6) → iteration 18 (the LAST before the checkpoint) → CHECKPOINT with Adnaan

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

**`R8_IMPLEMENTATION_PLAN.md` remains your complete work order.** Its
§0 ledger is current through review iteration 17 and carries the
vetted K1–K6 fix queue (with the K2/K3 deviations already stated
inline). Read the plan top to bottom (§0, §2, §4, §6 especially) and
`R17_FINDINGS.md` (the K-set's evidence — full finder fixes, all
proven by execution), then start at the first unchecked §0 item
(**K1**) and keep the ledger current as work lands.

**START IMMEDIATELY.** No launch phrase, no readiness reply. Ask only
if something is provably wrong or only Adnaan can settle it — one
question at a time.

**Session config:** Model **Fable 5**, `ultracode`. Iteration 18 is
multi-agent by ruling; the K1–K6 fix phase (and any fix phase after a
not-quiet 18) you implement YOURSELF, subagents only for isolated
probes/repros.

**State you inherit (verified 2026-08-19 by the I/J/K-vetting
executor; tree clean at `2b68eee`, no K code written yet):**
- I1–I8 landed `bfd96b7..fd3ea1f`; iteration 16 ran (NOT QUIET: 7→7,
  0 blockers) → J1–J6 landed `c4f1fda..ddc6da8` (J5 fail-CLOSED and
  J6 'Comma' option A RULED by Adnaan 2026-08-19); floor **778**
  pinned `cba8fd2`; **both host gates green 782/778** (Mac 148s, VM
  516s). Findings docs `R16_FINDINGS.md` + `R17_FINDINGS.md` and
  snapshots `tools/review/flip-review-iter16.js`/`iter17.js` are
  committed.
- Iteration 17 ran (run `wf_7f33bc0c-52c`, 19 agents, 0 errors,
  ~2.58M subagent tokens): **NOT QUIET pre-fix — 6 raised → 6
  confirmed (4 major / 2 minor, 0 blockers), 0 killed.** Machine
  results incl. refuter verdicts: `r16-results.json` and
  `r17-results.json` in the PREVIOUS session's scratchpad
  `/private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/85225f0b-fafc-41e1-84eb-1ff97d499532/scratchpad/`
  (tmp — may not survive a reboot; the findings docs are the durable
  record).
- **Pre-fix ref for every K-fix fail-first proof: `7ad7b71`**
  (`2b68eee` is docs-only on top). Extract your OWN scratch copy
  (`git archive 7ad7b71 | tar -x -C <your-scratchpad>/7ad7b71`) — the
  previous session's scratch copies are not yours.
- **All six K items are vetted under STANDING rules — no new Adnaan
  ruling needed** (K1 RESTORES the review-r5 #41 identity guard the
  H5 arm bypassed, entry 70's rename path untouched; K2 = the F4
  doctrine's fourth instance; K3 = degrade-never-crash; K4–K6
  tests-only). K1 is on the §6 payment-surface list.

**K-set vetted specs (full evidence in R17_FINDINGS.md; deviations
already recorded in plan §0 — do not re-derive):**
- **K1** (r17 #1, MAJOR): gate ingest.py's QUARANTINED-path heal with
  the move-heal identity test WHEN `existing["player_email"]` is
  non-empty: `(vmd5 == existing["md5_video"]) if (vmd5 and
  existing["md5_video"]) else existing["player_email"] ==
  ds.player_email`; on failure keep the row QUARANTINED + append a
  'heal REFUSED, identity mismatch' integrity flag, `continue`.
  INT_PATH chase rows (inserted with player_email="") stay unguarded
  — the heal's designed population. Tests (fail-first at `7ad7b71`;
  the takeover needs TWO scans with the other-player listing: scan 2
  collision-flags + vanish-quarantines, scan 3 is where the unguarded
  heal fired): (a) zip class (no md5 either side, files=["bundle.zip"]
  in make_session_entries) — same sid under player B stays
  QUARANTINED under player A; (b) files class with a DIFFERENT md5 —
  refused (also kills the stamp-clearing capture); (c) control:
  byte-identical cross-player move still heals (deliberate, matches
  the move-heal — pin it); (d) the existing same-player different-path
  heal test and the INT_PATH heal tests stay green untouched.
- **K2** (r17 #2, MAJOR): fix_v1_to_v2 resolves the session's OWN
  keybind before computing `bound` — kbp = work/'keybind.json' (still
  at the root when the fix runs) else work/'raw'/'keybind.json', via
  resolve_keybind anchored on the ledger slug; else built-ins; then
  KEYBIND_PATCHES. DEVIATION (recorded): the finder's delivered
  key_binding.json fallback arm is NOT adopted — the inversion sniff
  is biased against flipping and a mis-flip empties the keyboard
  column (the r-loop-4 catastrophic class). Tests (fail-first at
  `7ad7b71`): a v1 session with keybind {"interact": ";"} keeps its
  ';' presses with 'interact' through the conversion (pre-fix: all
  deleted, orphan actions ship checker-green); built-in-only control
  unchanged.
- **K3** (r17 #3, MAJOR): guard `_active` in
  translator/v2.apply_context_to_rows exactly like fix.py's `_moving`
  (try/except (TypeError, ValueError) → False — a junk cell is not
  motion). DEVIATION (recorded): the belt-and-braces plan reorder
  (FIX_SENTINELS pre-structural) is NOT adopted; sync.py's bare
  float() shielding-by-ordering is NOTED for iteration 18's lanes,
  not fixed here. Tests (fail-first at `7ad7b71`): apply_context and
  the fix_actions_context route over a '1,5' dx cell complete without
  raising; numeric-cell control unchanged.
- **K4/K5/K6** (r17 #4/#5/#6, tests-only; mutation-proof pattern with
  the finders' EXACT mutants, all three proven arming-gate-green at
  782/778 in R17_FINDINGS.md): K4 — camel discriminator for J2
  (`_os_map({"CapsLock": 3}, frozenset({"caps_lock"}))` → no
  INP_OSKEYS + BOUND advisory; bind caps_lock in the e2e too; kills
  the `t.lower()` mutant at validate.py's filter). K5 — overlap frame
  `['w','e']` added to the J3 remap pin (keys ['w'], actions
  ['move_up']; kills the row-level `if rules and not actions: kset =
  set()` mutant). K6 — the I7 coached rename path pinned: vanish-
  quarantine, then a NEW sid at a different path with the SAME md5 +
  same player → DISCOVERED, res.duplicates == [] (kills deleting the
  QUARANTINED exclusion from the scan-time dedupe; add the
  download-time twin only if cheap, else record the deviation).

**The sequence:** K1–K6 (each: sibling-site sweep recorded in the
commit → implement → fail-first/mutant proof in a scratch copy
OUTSIDE the repo → Mac gate → path-scoped commit) → new floor
(passed − 4) in run_suite.sh + FLIP_RUNBOOK §6b → both host gates →
tree-verify → **iteration 18 (headroom check with Adnaan BEFORE the
launch — the per-launch asks are NOT carried over)** → quiet? →
**CHECKPOINT: STOP and report to Adnaan** (verdict-first; the FULL §6
payment-surface list, which now includes the r16/r17 additions and
the QUEUED OW satellite-camera item). If 18 is NOT quiet: fix its
confirmed set with full §2 discipline, then STOP anyway — report
those fixes honestly labelled landed-but-unreviewed. **The
independent e2e and THE FLIP do NOT run in this session — they wait
for Adnaan's explicit go.** Never propose skipping a verification
pass (Adnaan revoked his own skip shortcut once already — memory
`confirmation-passes-never-skipped`).

**Iteration 18 script:** copy the previous committed snapshot
`tools/review/flip-review-iter17.js` to your scratchpad as
`flip-review-iter18.js`; retarget the regressions lane at the K
commits (one-line description each + per-commit attack notes, the
iter16/17 pattern); refresh the Find preamble's HEAD note, suite
numbers and floor; frame it as the LAST pass before the checkpoint
(if not quiet, fixes land but are reported landed-but-unreviewed);
APPEND accepted-behaviour entries 80+ for the K fixes and AMEND 70
(K1's guard narrows the heal — the ruled rename path unchanged), 72
(K5 pins the overlap case), 77 (K4 pins the canonicalization); NO raw
backticks inside the ACCEPTED template literal; sanity-check lane
count (7) and backtick parity before launching (a lane edit orphaned
a prompt line once — grep for stale iteration markers); invoke via
the Workflow tool with `scriptPath`. Findings docs are GENERATED from
the results JSON (see the R16/R17 pattern); save `r18-results.json`
to your session scratchpad. A Workflow DIES if the session restarts —
relaunch with `resumeFromRunId` and VERIFY the cache via
journal.jsonl.

**Ground rules (bind, plan §1 — do not relearn):** verify before
claiming; read whole sources; mark `[assumption]`; NEVER push;
commits path-scoped per green step; suite only via
`tools/run_suite.sh` (floor **778** — re-measure and raise after K6:
passed − 4); green on Mac AND VM for anything that ships; nothing
deploys and no systemd unit is touched before the flip phase (NOT
this session); Drive I read-only forever; secrets never printed;
after any multi-agent step verify your own tree (`git diff` every
hunk, `grep -rn "MUTATION" --include="*.py" .`, `git status`). The
pre-existing uncommitted junk in the tree predates r8 — leave it.

**Practical notes:** VM sync + gate recipe in plan §1 (bare instance
name for gcloud; the pipe-over-ssh form HANGS; gcloud auth expires —
ask Adnaan to run `! gcloud auth login`). The Mac gate occasionally
exceeds 7 min under load — run it with a 10-min timeout or in the
background before declaring a hang. make_session_entries /
_h5_discovered / _daily_seed / _make_session / _load are the test
idioms (see test_r_loop15/16.py for the r15/r16 usage).

**Authority chain if the plan seems wrong:** `R17_FINDINGS.md` →
`R16_FINDINGS.md` → `R15_FINDINGS.md` → older findings docs → the
older kickoffs. Deviate only with the discrepancy stated out loud and
recorded in the plan.
