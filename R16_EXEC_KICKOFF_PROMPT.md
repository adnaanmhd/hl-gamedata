# Kickoff — execute R8_IMPLEMENTATION_PLAN.md §3 (fix ALL r-loop 15 → iterations 16–18, stop at first quiet → CHECKPOINT with Adnaan)

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

**`R8_IMPLEMENTATION_PLAN.md` remains your complete work order.** It
was context-optimized 2026-08-19 (third pass — everything landed
lives in git history, not the plan) and its §3 (I1–I8) was written by
the session that executed H1–H9, ran review iteration 15, and put
every open question to Adnaan — his 2026-08-19 rulings are encoded
inline (RULED markers). Read the plan top to bottom (§0, §2, §3 and
§4 especially), then start at the first unchecked §0 item (**I1**)
and keep the ledger current as work lands.

**START IMMEDIATELY.** No launch phrase, no readiness reply. Ask only
if something is provably wrong or only Adnaan can settle it — one
question at a time.

**Session config:** Model **Fable 5**, `ultracode`. Iterations 16–18
are multi-agent by ruling; the I1–I8 fix phase (and any in-iteration
fix phase after a not-quiet iteration) you implement YOURSELF,
subagents only for isolated probes/repros.

**State you inherit (verified 2026-08-19 by the H/r15 executor):**
- H1–H9 landed `1dd69fa..747422e`, floor 745 pinned `37d7d88`, both
  host gates green 749/749 (Mac + VM), iteration 14 judged QUIET
  after fixing (`d16d504`).
- Iteration 15 ran (27 agents, 0 errors, ~3.23M subagent tokens, run
  `wf_0098c165-80b`): **NOT QUIET — 10 raised → 10 confirmed (6 major
  / 4 minor, 0 blockers), 0 killed** — findings of record
  `R15_FINDINGS.md`, workflow snapshot
  `tools/review/flip-review-iter15.js`, machine results in the
  previous session's scratchpad `r15-results.json` (both refuter
  verdicts per finding). Clusters: #1≡#2≡#3≡#10 (H5 same-path dead
  end — now RULED design, see below), #6/#9 (pins for H2/H6 halves),
  #4/#5/#7/#8 pre-existing.
- **Pre-fix ref for every I-fix fail-first proof: `ce26148`** (code
  is identical to `82c86da` — the commits after it are docs-only).
- **Adnaan's rulings (2026-08-19), all encoded in plan §3 — do NOT
  re-ask, do NOT re-litigate:**
  - H5 cluster: **"if the folder is gone, it's gone."** No same-path
    heal, no listing counters. One coaching-string change (I7): the
    quarantine tells the operator to re-upload under a NEW folder
    name (a renamed re-upload already processes as a separate
    session; both dedupe sites exclude QUARANTINED — verified).
  - r15 #4: the checker exempts caseless key tokens (symbol keys
    stay in deliveries) — I1.
  - r15 #5: the writer strips action-less combo halves — I2.
  - fix_sync_from_v1's macOS-only `cp -c`: fix, portable copy — I8.
- **Adnaan's sequencing (RULED 2026-08-19):** fix ALL of §3 (I1–I8) →
  post-I8 gates + floor re-pin (passed − 4) → iteration 16 → 17 ONLY
  if 16 not quiet → 18 ONLY if 17 not quiet — **stop at the FIRST
  quiet iteration** (R5_TRIAGE §7's pre-registered definition), and
  when an iteration is NOT quiet you **fix in-iteration** (vet specs
  with deviations stated, full §2 discipline, fail-first, gates) and
  the next iteration reviews those fixes; if 18 is also not quiet,
  fix its confirmed set and STOP with the fixes honestly labelled
  landed-but-unreviewed → **CHECKPOINT: STOP and report to Adnaan.
  The independent e2e and THE FLIP do NOT run in this session — they
  wait for his explicit go.** Never propose skipping a verification
  pass. The §0 ledger encodes this.
- **Adnaan's quality order ("no more issues"), plan §2 — BINDING:**
  sibling-site sweep recorded in every commit message; the sweep
  extends to the TESTS of every swept site AND to test cohorts (r15
  #6: a pin must run where the pinned behavior is live — every H2
  test used kamla, where the swept consumers are no-ops); durable
  events over transient state; discriminator tests split their
  variables both ways; both sides of every guard plus the hostile
  mutant; every new marker event checked against every event-anchored
  query.
- **Payment-surface notes for the checkpoint report:** the full list
  lives in plan §6 — including new entries I1 + I2 (wrongly-rejected
  symbol-key/combo-bind players now paid) and the I7 ruling (vanished
  folders are permanently dropped; same-name restores are
  deliberately never processed or paid — the correction is a rename).
  If your own reading finds a contradiction with a standing ruling,
  surface it BEFORE implementing.

**Authority chain if the plan seems wrong:** `R15_FINDINGS.md` →
`R14_FINDINGS.md` → `R13_FINDINGS.md` → older findings docs → the
older kickoffs. Deviate only with the discrepancy stated out loud and
recorded in the plan.

**Ground rules (bind, plan §1 — do not relearn):** verify before
claiming; read whole sources; mark `[assumption]`; NEVER push; commits
path-scoped per green step; suite only via `tools/run_suite.sh` (floor
745 — re-measure and raise after I8: passed − 4); green on Mac AND VM
for anything that ships; nothing deploys and no systemd unit is
touched before the flip phase (which is NOT this session); Drive I
read-only forever; secrets never printed; every new test proved to
FAIL against unfixed code in a scratch copy OUTSIDE the repo (session
scratchpad; pre-fix ref `ce26148`; pin-only tests use the
mutation-proof pattern with the finders' EXACT mutants from
R15_FINDINGS.md — test_r_loop10/11/12/13/14.py have examples; I8's
fail-first is host-specific and already on record: the unstubbed H9c
twin failed the VM gate pre-`82c86da`); after any multi-agent step
verify your own tree (`git diff` every hunk,
`grep -rn "MUTATION" --include="*.py" .`, `git status`) before
committing. The pre-existing uncommitted junk in the tree predates
r8 — leave it alone.

**Practical notes (save yourself the rediscovery):**
- VM sync + gate: recipe in plan §1. Bare instance name for gcloud;
  the dotted alias is plain-ssh only. gcloud auth expires and cannot
  reauth non-interactively — ask Adnaan to run `! gcloud auth login`.
- For each iteration: copy the previous committed snapshot
  (`tools/review/flip-review-iter15.js` for 16) to your scratchpad as
  `flip-review-iter<N>.js`, retarget the regressions lane at the
  newest fix commits, refresh the suite numbers + HEAD note +
  iteration framing (16–18 are fix-in-iteration passes, NOT
  stop-and-hand-over like 15 was), APPEND accepted-behaviour entries
  70+ per plan §4 (keep ALL existing 1–69; amend 62/65/66/67 as §4
  lists — 65's amendment is load-bearing: same-path-terminal is now
  THE RULED DESIGN, or agents will re-raise the r15 H5 cluster), and
  invoke via the Workflow tool with `scriptPath`. Template literals:
  NO raw backticks inside the ACCEPTED block (that parse error bit
  twice). Check usage-credit headroom with Adnaan BEFORE each launch
  (~27–40 agents, ~3–4M subagent tokens per iteration).
- A Workflow launched by a session DIES if that session restarts and
  the checkpoint may fail to adopt — if that happens, relaunch with
  `resumeFromRunId` (completed agents return cached) and VERIFY via
  the run's journal.jsonl whether the cache actually applied.
- Findings-of-record docs are GENERATED from the workflow results
  JSON (see the r14/r15 pattern) — never hand-transcribed; save
  `r<N>-results.json` to your session scratchpad.

**The sequence:** I1–I8 (each: sibling-site sweep → implement →
fail-first → Mac gate → path-scoped commit) → new floor + both host
gates + tree-verify → iteration 16 (headroom check first) → not
quiet? fix in-iteration → 17 → 18 (stop at first quiet) → **STOP:
checkpoint report to Adnaan** (verdict-first; every payment-surface
change per plan §6) — e2e and THE FLIP only on his explicit go, in a
later session.
