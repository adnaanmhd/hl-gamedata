# Kickoff — run review iterations 19 AND 20 (RULED 2026-08-20: two more, both run) → CHECKPOINT with Adnaan

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

**`R8_IMPLEMENTATION_PLAN.md` remains your complete work order.** It
was context-optimized 2026-08-20 (fourth pass): its §0 ledger is
current through iteration 18 + the L-set, and §4 carries the new
run rule. Read the plan top to bottom (§0, §2, §4, §6 especially) and
`R18_FINDINGS.md` (the L set's evidence — the L fixes are what
iteration 19 reviews first), then start at the first unchecked §0
item (**iteration 19**, headroom ask first) and keep the ledger
current as work lands.

**START IMMEDIATELY.** No launch phrase, no readiness reply. Ask only
if something is provably wrong or only Adnaan can settle it — one
question at a time. The two headroom asks below are MANDATED asks.

**Session config:** Model **Fable 5**, `ultracode`. The iterations
are multi-agent by ruling; any fix phase (M-set after 19, N-set after
20) you implement YOURSELF, subagents only for isolated probes/repros.

**The ruling you execute (Adnaan, 2026-08-20, checkpoint response —
his words: "i want to run two more iterations"):** iterations **19
and 20 BOTH run** — this supersedes the old stop-at-first-quiet rule.
If 19 is not quiet, 20 reviews its M-set fixes; if 19 IS quiet, 20
runs as a CONFIRMATION pass (the iteration-15 precedent). Check
usage-credit headroom with Adnaan BEFORE EACH launch (recent
iterations: ~19 agents, ~2.6–2.7M subagent tokens); if 19 was quiet,
RESTATE at the 20 ask that it will be a confirmation pass so he can
redirect. After 20: **STOP and report — the checkpoint.** The
independent e2e and THE FLIP do NOT run in this session; they wait
for Adnaan's explicit go. Never propose skipping a verification pass
(memory `confirmation-passes-never-skipped`).

**State you inherit (verified 2026-08-20 by the K/L executor; code
HEAD `f57b3ff`, docs commits on top; suite 802/802, floor 798, BOTH
host gates green — Mac 150.6s, VM 524.1s):**
- Iterations 16/17/18 all ran NOT QUIET → J1–J6 (`c4f1fda..ddc6da8`),
  K1–K6 (`c99309e..cdd03cc`), L1–L3 (`e197244..f57b3ff`) landed.
  **L1–L3 are UNREVIEWED** (18 was the last pass under the old
  ruling) — iteration 19's regressions lane treats them as the prime
  target. Findings docs `R16..R18_FINDINGS.md` and snapshots
  `tools/review/flip-review-iter16..18.js` are committed.
- Iteration 18 (run `wf_e3359c57-a84`, 19 agents, 0 errors, ~2.71M
  tokens): 6 raised → 6 confirmed (all major, 0 blockers), 0 killed.
  Machine results: `r18-results.json` in the PREVIOUS session's
  scratchpad
  `/private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/3a326910-ec6d-4e83-9360-b506523b871a/scratchpad/`
  (tmp — may not survive a reboot; `R18_FINDINGS.md` is the durable
  record).
- **Pre-fix ref for every M-fix fail-first proof: HEAD at your
  session start** (verify with `git log --oneline` that only docs
  commits sit on top of code HEAD `f57b3ff`). Extract your OWN
  scratch copy (`git archive <ref> | tar -x -C <your-scratchpad>/<ref>`)
  — the previous session's scratch copies are not yours.

**The L set iteration 19 reviews (full evidence in R18_FINDINGS.md;
one line each for the regressions lane):**
- **L1** `e197244` (r18 #1≡#2≡#3≡#5, major): fix_v1_to_v2 degrades
  every junk v1 payload read — dx/dy via fix_sentinels' _parse
  semantics with has_motion from PARSED values (all-junk/all-zero
  columns now ship the blank no-capture form: a deliberate behavior
  change matching fix_sentinels), canonical/trim non-dict guards,
  unusable created_at OMITTED (recompute synthesizes), session.json
  via _read_session_json. Attack: the has_motion semantics change
  against every v1 consumer, the _parse_motion float(v or 0)
  truthiness, the omitted-stamp interplay with recompute's now-UTC
  synthesis (a wrong-but-parseable stamp vs an omitted one), the
  early-return arm with s={}.
- **L2** `21c983e` (r18 #4, major): has_raw_sidecars — the single
  shared plan-gate for both drivers — now requires raw/metadata.json
  to parse to a dict; typed FixFailed belt-and-braces at the
  retranslate read. Attack: every has_raw consumer (validate aux vs
  plan gate drift), the per-scan reparse cost/races, QA_FAIL_UNMAPPED
  routes that now go unfixable instead of retranslate, the
  errors='replace' read vs load_events' tolerance.
- **L3** `f57b3ff` (r18 #6, tests-only): K2's game_name=slug anchor
  pinned live (OW ledger + degraded canonical + unusable keybind
  keeps W/A/S; exact game_name=None mutant killed). Attack: does the
  pin pass for the wrong reason; cohort gaps that remain.
- Floor bump `74b4a17` (SUITE_FLOOR 798 = 802 − 4).

**Iteration 19 script:** copy the previous committed snapshot
`tools/review/flip-review-iter18.js` to your scratchpad as
`flip-review-iter19.js`; retarget the regressions lane at the L
commits above (one-line description each + per-commit attack notes,
the iter17/18 pattern); refresh the Find preamble's HEAD note, suite
numbers (802) and floor (798); frame it as the FIRST of the two RULED
extra passes, reviewing fixes that landed UNREVIEWED; APPEND
accepted-behaviour entries 86 (L1), 87 (L2), 88 (L3) and AMEND: entry
82 — its fix_v1_to_v2 NOTED site is CLOSED by L1, while
translator/sync.py's bare float stays NOTED-not-settled (no r18
finder proved harm through it; a PROVEN harm path is still a normal
finding), as does reprocess_session (CLI-only, K2 note); entry 81 —
completed by L1's whole-function degrade + L3's live pin; entry 44 —
unchanged (L1 touched motion cells, not the button contract). NO raw
backticks inside the ACCEPTED template literal; sanity-check lane
count (7), backtick parity, and stale iteration markers before
launching (grep for the previous iteration's numbers); invoke via the
Workflow tool with `scriptPath`. Findings docs are GENERATED from the
results JSON (the R16–R18 pattern); save `r19-results.json` to your
session scratchpad; commit the findings doc + snapshot, update §0. A
Workflow DIES if the session restarts — relaunch with
`resumeFromRunId` and VERIFY the cache via journal.jsonl.

**Iteration 20 script:** same recipe from your committed
`flip-review-iter19.js` → `flip-review-iter20.js`: retarget the
regressions lane at the M commits (or, if 19 was quiet, keep the L
set as the newest-fixes target and frame it as a CONFIRMATION pass);
append/amend accepted entries for anything M landed; refresh
numbers; frame it as the LAST pass before the checkpoint (its fixes
land landed-but-unreviewed).

**The sequence:** headroom ask → iteration 19 → results JSON +
findings doc + snapshot commit + §0 → if not quiet: vet M-set specs
from the findings (deviations stated; payment-surface changes and
ruling contradictions surfaced to Adnaan BEFORE implementing) →
implement each with full §2 discipline (sibling-site sweep recorded
in the commit → implement → fail-first/mutant proof in a scratch
copy OUTSIDE the repo → Mac gate → path-scoped commit) → new floor
(passed − 4) in run_suite.sh + FLIP_RUNBOOK §6b → both host gates →
tree-verify → headroom ask (restate confirmation-pass framing if 19
was quiet) → iteration 20 → same processing → if not quiet: fix
N-set with full discipline + floor + both gates + tree-verify →
**CHECKPOINT: STOP and report to Adnaan** (verdict-first; the FULL
§6 payment-surface list — labelling each L/M/N item's review status
honestly — plus the QUEUED OW satellite-camera item). Do NOT start
the e2e or the flip.

**Ground rules (bind, plan §1 — do not relearn):** verify before
claiming; read whole sources; mark `[assumption]`; NEVER push;
commits path-scoped per green step; suite only via
`tools/run_suite.sh` (floor **798** — re-measure and raise after each
fix set: passed − 4); green on Mac AND VM for anything that ships;
nothing deploys and no systemd unit is touched before the flip phase
(NOT this session); Drive I read-only forever; secrets never printed;
after any multi-agent step verify your own tree (`git diff` every
hunk, `grep -rn "MUTATION" --include="*.py" .`, `git status`). The
pre-existing uncommitted junk in the tree predates r8 — leave it.

**Practical notes:** VM sync + gate recipe in plan §1 (bare instance
name for gcloud; the pipe-over-ssh form HANGS; gcloud auth expires —
ask Adnaan to run `! gcloud auth login`). The Mac gate occasionally
exceeds 7 min under load — run it with a 10-min timeout or in the
background before declaring a hang; the VM gate takes ~9 min — run it
in the background and monitor. make_session_entries / _h5_discovered
/ _daily_seed / _make_session / _load / _v1_work are the test idioms
(see test_r_loop17/18.py for the k/l-era usage).

**Authority chain if the plan seems wrong:** `R18_FINDINGS.md` →
`R17_FINDINGS.md` → `R16_FINDINGS.md` → older findings docs → the
older kickoffs. Deviate only with the discrepancy stated out loud and
recorded in the plan.
