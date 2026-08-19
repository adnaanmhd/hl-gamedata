# Kickoff — execute R8_IMPLEMENTATION_PLAN.md §3 (fix ALL r-loop 14 → judge 14 → iteration 15 → e2e → THE FLIP)

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

**`R8_IMPLEMENTATION_PLAN.md` remains your complete work order.** It
was context-optimized 2026-08-19 (second pass — everything landed
lives in git history, not the plan) and its §3 (H1–H9) was written by
the session that executed G1–G9 and ran review iteration 14, read all
13 iteration-14 findings and both refuter verdicts in full, and vetted
every fix — deviations from finder proposals are stated inline in each
spec. Read the plan top to bottom (§0, §2 and §3 especially), then
start at the first unchecked §0 item (**H1**) and keep the ledger
current as work lands.

**START IMMEDIATELY.** No launch phrase, no readiness reply. Ask only
if something is provably wrong or only Adnaan can settle it — one
question at a time.

**Session config:** Model **Fable 5**, `ultracode`. Iteration 15 is
multi-agent by ruling; the H1–H9 fix phase you implement YOURSELF,
subagents only for isolated probes/repros.

**State you inherit (verified 2026-08-19 by the G/r14 executor):**
- G1–G9 landed `abf052b..82f5019`, floor 718 pinned `a5fc1a0`, plan +
  ledger commits through `e86013f`; both host gates green 722/722
  (Mac + VM).
- Iteration 14 ran (33 agents, 0 errors, ~3.79M subagent tokens):
  **NOT QUIET pre-fix — 13 raised → 13 confirmed (5 major / 8 minor,
  0 blockers), 0 killed** — findings of record `R14_FINDINGS.md`,
  workflow snapshot `tools/review/flip-review-iter14.js`, machine
  results in the previous session's scratchpad `r14-results.json`
  (both refuter verdicts per finding). Clusters: #1≡#6 (G2 fallback
  anchor), #2≡#3 (G1 counted_at anchor), #11/#12/#13 = pins for
  G5/G4/G7 halves. Three of the 13 are gaps in/regressions from the G
  set; the rest pre-existing.
- **Pre-fix ref for every H-fix fail-first proof: `5f7015b`** (code is
  identical to `a5fc1a0` — the commits after it are docs-only).
- **Adnaan's sequencing (2026-08-19):** fix ALL of §3 (H1–H9) →
  post-H9 gates + floor re-pin → judge iteration 14 quiet-after-fixing
  (R5_TRIAGE §7) and record it in §0 → iteration 15 (confirmation
  pass, runs REGARDLESS — his standing preference; never propose
  skipping a verification pass) → if 15 is not quiet: STOP and hand
  him the list, severity-ordered → independent REAL e2e (verdict
  relayed VERBATIM) → THE FLIP per `FLIP_RUNBOOK.md`. The §0 ledger
  encodes this.
- **Adnaan's lane ruling, RESOLVED:** driver-core would have been
  dropped from iteration 15 only if iteration 14 confirmed zero
  driver-core findings — it confirmed TWO (#4, #5), so **all 7 lanes
  stay**. Do not re-litigate.
- **Adnaan's quality order ("no more issues"), plan §2 — BINDING:**
  sibling-site sweep recorded in every commit message (r14 #12/#13
  prove the sweep discipline extends to the TESTS of every swept
  site); durable events over transient state; discriminator tests
  split their variables both ways; both sides of every guard plus the
  hostile mutant; every new marker event checked against every
  event-anchored query.
- **Payment-surface notes for the final report:** F6, F7 + r12 #1/#2 +
  G1 + **H1** (the '' adjudication chain incl. the pre-build
  counted_at anchor), G5 + **H3/H9a** (rebuild-reset under ruling C:
  split-artifact discard, depth-2 memory keying) — surface all of them
  in the final report exactly as the C6/D7 changes were. If your own
  reading finds a contradiction with a standing ruling, surface it
  BEFORE implementing.

**Authority chain if the plan seems wrong:** `R14_FINDINGS.md` →
`R13_FINDINGS.md` → `R12_FINDINGS.md` → older findings docs → the
older kickoffs. Deviate only with the discrepancy stated out loud and
recorded in the plan.

**Ground rules (bind, plan §1 — do not relearn):** verify before
claiming; read whole sources; mark `[assumption]`; NEVER push; commits
path-scoped per green step; suite only via `tools/run_suite.sh` (floor
718 — re-measure and raise after H9: passed − 4); green on Mac AND VM
for anything that ships; nothing deploys and no systemd unit is
touched before the flip phase; Drive I read-only forever; secrets
never printed; every new test proved to FAIL against unfixed code in a
scratch copy OUTSIDE the repo (session scratchpad; pre-fix ref
`5f7015b`; pin-only tests use the mutation-proof pattern with the
finders' EXACT mutants from R14_FINDINGS.md —
test_r_loop10/11/12/13.py have examples); after any multi-agent step
verify your own tree (`git diff` every hunk,
`grep -rn "MUTATION" --include="*.py" .`, `git status`) before
committing. The pre-existing uncommitted junk in the tree predates
r8 — leave it alone.

**Practical notes (save yourself the rediscovery):**
- VM sync + gate: recipe in plan §1. Bare instance name for gcloud; the
  dotted alias is plain-ssh only. gcloud auth was live at handoff; if
  expired, ask Adnaan to run `! gcloud auth login`.
- For iteration 15: copy `tools/review/flip-review-iter14.js` to your
  scratchpad as `flip-review-iter15.js`, retarget the regressions lane
  at the H-commits, refresh the suite numbers + HEAD note, APPEND
  accepted-behaviour entries 61+ for the H rulings (keep ALL existing
  1–60; amend 55/56/57/58/60 where H1/H2/H9b/H3+H9a/H9c supersede or
  complete their mechanics — the same pattern iter13/14 used), and
  invoke via the Workflow tool with `scriptPath`. Template literals:
  NO raw backticks inside the ACCEPTED block (that parse error already
  bit twice). Check usage-credit headroom with Adnaan BEFORE the
  ~40-agent launch (iterations 12/13/14 each burned ~3–3.8M subagent
  tokens; two iteration-11 refuters died on exhaustion).
- A Workflow launched by a session DIES if that session restarts and
  the checkpoint may fail to adopt — if that happens, relaunch with
  `resumeFromRunId` (completed agents return cached) and VERIFY via
  the run's journal.jsonl whether the cache actually applied (iter
  14's first launch lost everything and re-ran fresh).
- Findings-of-record docs are GENERATED from the workflow results JSON
  (see the r12/r13/r14 pattern) — never hand-transcribed.
- e2e prerequisites verified on this Mac: `rclone listremotes` shows
  drive-collect:/drive-deliver:, `~/.config/hl-gamedata/secrets.env`
  has GEMINI+TELEGRAM vars (never print it).

**The sequence:** H1–H9 (each: sibling-site sweep → implement →
fail-first → Mac gate → path-scoped commit) → new floor + both host
gates + tree-verify → judge iteration 14 (quiet-after-fixing, record
in §0) → iteration 15 (confirmation, REGARDLESS; 7 lanes) → if 15 not
quiet: STOP, hand Adnaan the list → independent REAL e2e → THE FLIP
(`FLIP_RUNBOOK.md` §5 canary → §6 flip → §7 payment endgame → §8 tree
verify + LAST destructive act) → reject-reason table, final independent
live verifier, final report per plan §7 (verdict-first; include every
payment-surface change listed above).
