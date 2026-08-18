# Kickoff — execute R8_IMPLEMENTATION_PLAN.md §3 (fix ALL r-loop 13 → iterations 14–15 → e2e → THE FLIP)

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

**`R8_IMPLEMENTATION_PLAN.md` remains your complete work order.** It was
context-optimized on 2026-08-19 (everything landed lives in git history,
not the plan) and its §3 (G1–G9) was written by the session that ran
review iterations 12 and 13, read all 12 iteration-13 findings and both
refuter verdicts in full, and vetted every fix — deviations from finder
proposals are stated inline in each spec. Read the plan top to bottom
(§0, §2 and §3 especially), then start at the first unchecked §0 item
(**G1 — the money-path cluster**) and keep the ledger current as work
lands.

**START IMMEDIATELY.** No launch phrase, no readiness reply. Ask only if
something is provably wrong or only Adnaan can settle it — one question
at a time.

**Session config:** Model **Fable 5**, `ultracode`. The review
iterations are multi-agent by ruling; the G1–G9 fix phase you implement
YOURSELF, subagents only for isolated probes/repros.

**State you inherit (verified 2026-08-19 by the F/r12 executor):**
- F1–F11 landed `82e42df..2f5ca04`; iteration 12 ran (15 confirmed, 0
  blockers), ALL fixed in-iteration `986368f..0ad8747`, judged QUIET
  after fixing (both gates 696, floor 692).
- Iteration 13 (confirmation pass, RE-RULED by Adnaan to run regardless
  of 12's verdict) was **NOT quiet: 12 raised → 12 confirmed (9 major /
  3 minor, 0 blockers), 0 killed** — findings of record
  `R13_FINDINGS.md`, workflow snapshot
  `tools/review/flip-review-iter13.js`, machine results in the previous
  session's scratchpad `r13-results.json`. Three findings are
  regressions from the r12 fix `986368f`; one (#4) is the F4-doctrine's
  third instance. HEAD at handoff: `b69fee1` — also the pre-fix ref for
  every G-fix fail-first proof.
- **Adnaan's sequencing ruling (2026-08-19):** fix ALL of §3 (G1–G9) →
  TWO more review iterations — 14 fix-in-iteration per plan §4, **15
  runs REGARDLESS of 14's verdict** (his standing preference: he
  revoked his own skip-a-confirmation-pass shortcut mid-run; never
  propose skipping a verification pass) → if 15 is not quiet: STOP and
  hand him the list, severity-ordered → independent REAL e2e (verdict
  relayed VERBATIM) → THE FLIP per `FLIP_RUNBOOK.md`. The §0 ledger
  encodes this.
- **Adnaan's quality order ("no more issues"), encoded as plan §2 —
  BINDING:** sibling-site sweep recorded in every commit message;
  durable events over transient state for any history discriminator;
  discriminator tests split their variables both ways; both sides of
  every guard plus the hostile mutant; every new marker event checked
  against every event-anchored query. The r12→r13 regressions all came
  from violating these — do not repeat them.
- **Payment-surface notes for the final report:** F6, F7 + r12 #1/#2 +
  G1 (the '' adjudication chain), and G5 (rebuild-reset brought under
  ruling C — flagged in the plan Adnaan has read) are observable
  payment-surface changes — surface all of them in the final report
  exactly as the C6/D7 changes were. If your own reading finds a
  contradiction with a standing ruling, surface it BEFORE implementing.

**Authority chain if the plan seems wrong:** `R13_FINDINGS.md` →
`R12_FINDINGS.md` → `R11_FINDINGS.md` → older findings docs → the older
kickoffs. Deviate only with the discrepancy stated out loud and
recorded in the plan.

**Ground rules (bind, plan §1 — do not relearn):** verify before
claiming; read whole sources; mark `[assumption]`; NEVER push; commits
path-scoped per green step; suite only via `tools/run_suite.sh` (floor
692 — re-measure and raise after G9: passed − 4); green on Mac AND VM
for anything that ships; nothing deploys and no systemd unit is touched
before the flip phase; Drive I read-only forever; secrets never
printed; every new test proved to FAIL against unfixed code in a
scratch copy OUTSIDE the repo (session scratchpad; pre-fix ref
`b69fee1`; pin-only tests use the mutation-proof pattern —
test_r_loop10/11/12.py have examples); after any multi-agent step
verify your own tree (`git diff` every hunk,
`grep -rn "MUTATION" --include="*.py" .`, `git status`) before
committing. The pre-existing uncommitted junk in the tree predates r8 —
leave it alone.

**Practical notes (save yourself the rediscovery):**
- VM sync + gate: recipe in plan §1. Bare instance name for gcloud; the
  dotted alias is plain-ssh only. gcloud auth was live at handoff; if
  expired, ask Adnaan to run `! gcloud auth login`.
- For iteration 14: copy `tools/review/flip-review-iter13.js` to your
  scratchpad as `flip-review-iter14.js`, retarget the regressions lane
  at the G-commits, refresh the suite numbers, APPEND
  accepted-behaviour entries 55+ for the G rulings (keep ALL existing
  entries 1–54; amend 45/46/50 where G1/G8/G3+G9 supersede their
  mechanics — the same pattern iter12/13 used), and invoke via the
  Workflow tool with `scriptPath`. For 15: same again from 14's script.
  Template literals: NO raw backticks inside the ACCEPTED block (that
  parse error already bit twice). Check `/usage-credits` headroom with
  Adnaan BEFORE each ~40-agent launch (two iteration-11 refuters died
  on exhaustion; iterations 12/13 each burned ~3–3.7M subagent
  tokens).
- Findings-of-record docs are GENERATED from the workflow results JSON
  (see the r12/r13 pattern) — never hand-transcribed.
- e2e prerequisites verified on this Mac: `rclone listremotes` shows
  drive-collect:/drive-deliver:, `~/.config/hl-gamedata/secrets.env`
  has GEMINI+TELEGRAM vars (never print it).

**The sequence:** G1–G9 (each: sibling-site sweep → implement →
fail-first → Mac gate → path-scoped commit) → new floor + both host
gates + tree-verify → iteration 14 (fix-in-iteration, quiet judged
AFTER fixing) → iteration 15 (confirmation, REGARDLESS) → if 15 not
quiet: STOP, hand Adnaan the list → independent REAL e2e → THE FLIP
(`FLIP_RUNBOOK.md` §5 canary → §6 flip → §7 payment endgame → §8 tree
verify + LAST destructive act) → reject-reason table, final independent
live verifier, final report per plan §7 (verdict-first; include every
payment-surface change listed above).
