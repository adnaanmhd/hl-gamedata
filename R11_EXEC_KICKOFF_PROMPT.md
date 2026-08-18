# Kickoff — execute R8_IMPLEMENTATION_PLAN.md §11 (fix ALL r-loop 11 → iterations 12–13 → e2e → THE FLIP)

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

**`R8_IMPLEMENTATION_PLAN.md` remains your complete work order.** Its §11
(F1–F11) was written by the session that ran review iterations 10 and 11,
read all 20 iteration-11 findings and their refuter verdicts in full, and
vetted every fix. Read the plan top to bottom (§11 and §0 especially), then
start at the first unchecked §0 item (**F1 — the BLOCKER**) and keep the
ledger current as work lands.

**START IMMEDIATELY.** No launch phrase, no readiness reply (retired
2026-08-18). Ask only if something is provably wrong or only Adnaan can
settle it — one question at a time.

**Session config:** Model **Fable 5**, `ultracode`. The review iterations
are multi-agent by ruling; the F1–F11 fix phase you implement YOURSELF,
subagents only for isolated probes/repros.

**State you inherit (verified 2026-08-19 by the r9/r10 session):**
- D1–D8 (r-loop 9 fixes) landed `640651a..81d5f06`; iteration 10 ran (16
  confirmed, 0 blockers), ALL fixed in-iteration at `6dd2e64`, judged QUIET
  (both gates 641, floor 619, close-out `1500d95`).
- Iteration 11 (RULED full-deep + r10-regressions pass) ran and was **NOT
  quiet: 20 raised → 20 confirmed (1 BLOCKER), 0 killed** — findings of
  record `R11_FINDINGS.md`, workflow snapshot
  `tools/review/flip-review-iter11.js`. The blocker is a regression from
  the iteration-10 wedge fix (three lanes found it independently).
- **Adnaan's sequencing ruling (2026-08-19):** fix ALL of §11 → run TWO
  more review iterations (12 fix-in-iteration per §5; 13 as a confirmation
  pass REGARDLESS of 12's verdict) → if 13 is not quiet: STOP and hand him
  the list → independent REAL e2e (verdict relayed VERBATIM) → THE FLIP
  per `FLIP_RUNBOOK.md`. The §0 ledger encodes this; the "12
  fix-in-iteration / 13 regardless" split is the previous executor's
  reading of his warrant — flag it to him if you read it differently.
- ⚠ **Degraded-vote caveat:** two iteration-11 refuters died on
  usage-credit exhaustion; #19 is 1/1-REFUTED (read its refuter's evidence
  in the scratchpad `r11-results.json` — or R11_FINDINGS.md — before
  writing its test; fix_v1_to_v2 may be unreachable) and #20 is 0/1. Both
  are minor test-coverage items. Check `/usage-credits` headroom BEFORE
  launching any ~45-agent review workflow.
- **Payment-surface note for the final report:** F6 (pays
  previously-unreachable hours under NULL-duration roots) and F7 (stamps
  become compare-and-set on the counted md5) are observable
  payment-surface changes — surface both to Adnaan in the final report
  exactly as the C6/D7 changes were. Neither contradicts a standing
  ruling (F7 only tightens the WHERE; positions/ordering stay as ruled);
  if your own reading disagrees, surface it BEFORE implementing.

**Authority chain if the plan seems wrong:** `R11_FINDINGS.md` →
`R10_FINDINGS.md` → `R9_FINDINGS.md` → `R8_FINDINGS.md` →
`R8_HANDOFF_KICKOFF_PROMPT.md` → the older kickoffs. Deviate only with the
discrepancy stated out loud and recorded in the plan.

**Ground rules (bind, plan §1 — do not relearn):** verify before claiming;
read whole sources; mark `[assumption]`; NEVER push; commits path-scoped
per green step; suite only via `tools/run_suite.sh` (floor 619 — re-measure
and raise after F11: passed − 4); green on Mac AND VM for anything that
ships; nothing deploys and no systemd unit is touched before the flip
phase; Drive I read-only forever; secrets never printed; every new test
proved to FAIL against unfixed code in a scratch copy OUTSIDE the repo
(session scratchpad; for r10-regression fixes the pre-fix ref is
`1500d95`, for pin-only tests use the mutation-proof pattern —
test_r_loop10.py has examples of both); after any multi-agent step verify
your own tree (`git diff` every hunk,
`grep -rn "MUTATION" --include="*.py" .`, `git status`) before committing.

**Practical notes (save yourself the rediscovery):**
- VM sync: `git archive HEAD | gzip > /tmp/tree.tgz`, then `gcloud compute
  scp /tmp/tree.tgz hl-pipeline-vm:/tmp/tree.tgz --zone=asia-south1-a
  --project=hl-gamedata-pipeline`, then `gcloud compute ssh hl-pipeline-vm
  --zone=… --project=… --command='cd ~/hl-gamedata-continuous-test && tar
  xzf /tmp/tree.tgz' < /dev/null`. Bare instance name for gcloud; the
  dotted alias is plain-ssh only. VM gate: `PATH=$HOME/.local/bin:$PATH
  SUITE_FLOOR=<floor> bash tools/run_suite.sh --with numpy==2.4.6 --with
  opencv-python-headless==5.0.0.93 --with rerun-sdk==0.36.0`. gcloud auth
  was live at handoff; if expired, ask Adnaan to run `! gcloud auth login`.
- The working tree carries pre-existing UNCOMMITTED junk (deleted sample
  dirs, `.gitignore`, `SAMPLE_ANALYSIS_PLAYBOOK.md` edits) that predates
  r8 — leave it alone, never commit it, never clean it.
- The 29 open batch rows in the ledger are the dormant batch driver's
  rollback state — never touch them.
- For iteration 12: copy `tools/review/flip-review-iter11.js` to your
  scratchpad as `flip-review-iter12.js`, retarget the regressions lane at
  the F-commits (F1–F11), refresh the suite numbers, APPEND
  accepted-behaviour entries for the F-fix rulings (keep ALL existing
  entries 1–33; amend 30/33 where F1/F2/F5 supersede their mechanics —
  same pattern the iter-11 script used for 15/17/22), and invoke via the
  Workflow tool with `scriptPath`. For 13: same again from 12's script.
  Template literals: NO raw backticks inside the ACCEPTED block (that
  parse error already bit once).
- e2e prerequisites verified on this Mac: `rclone listremotes` shows
  drive-collect:/drive-deliver:, `~/.config/hl-gamedata/secrets.env` has
  GEMINI+TELEGRAM vars (never print it). Model the e2e on FLIP_RUNBOOK §5
  (canary shape, Mac-local): fresh HL_PIPELINE_HOME, TEST-mode Telegram,
  `_pipeline_test/` only + purge via `deliver.cleanup_test_folder`, local
  sample bundles as seeds, 3-leg kill -9 matrix, bounded VLM spend, fresh
  agent that wrote none of this code, verdict relayed VERBATIM.

**The sequence:** F1–F11 (each: implement → fail-first → Mac gate →
path-scoped commit) → new floor + both host gates + tree-verify →
iteration 12 (fix-in-iteration, quiet judged AFTER fixing) → iteration 13
(confirmation, regardless) → if 13 not quiet: STOP, hand Adnaan the list →
independent REAL e2e → THE FLIP (`FLIP_RUNBOOK.md` §5 canary → §6 flip →
§7 payment endgame → §8 tree verify + LAST destructive act) →
reject-reason table, final independent live verifier, final report per
`FLIP_SESSION_KICKOFF_PROMPT.md` §6 steps 7–9. Report per plan §8:
verdict-first; include the F6/F7 payment-surface before/after.
