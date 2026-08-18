# Kickoff — execute R8_IMPLEMENTATION_PLAN.md §9 (fix ALL r-loop 9 → iterations 10–11 → e2e → THE FLIP)

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

**`R8_IMPLEMENTATION_PLAN.md` remains your complete work order.** Its §9
(D1–D8) was written by the session that ran review iteration 9, read all 23
confirmed findings and both refuter verdicts in full, and vetted every fix —
you do NOT need to re-derive them. Read the plan top to bottom (§9 and §10
especially), then start at the first unchecked item in its §0 status ledger
(**D1**) and keep that ledger current as work lands.

**START IMMEDIATELY.** No launch phrase, no readiness reply (Adnaan retired
that protocol 2026-08-18). Ask only if something in the plan is provably wrong
or a question only Adnaan can settle — one question at a time.

**Session config:** Model **Fable 5**. This message carries `ultracode` — the
review iterations (plan §5, procedure refreshed in §9 "After D8") are
multi-agent by ruling; the D1–D8 fix phase you implement yourself, with
subagents only for isolated probes/repros.

**State you inherit (verified 2026-08-18 by the r8 session):**
- C1–C9 fixed and committed (`c3eab1b..b694456`); arming gate green on Mac
  AND the VM side checkout at **582 passed, floor 578**; close-out `1e3320f`.
- Review iteration 9 RAN and was **NOT quiet**: 23 confirmed (14 major,
  9 minor, 0 blockers), 0 killed. Findings of record: `R9_FINDINGS.md`.
  The workflow actually run is committed at `tools/review/flip-review-iter9.js`.
- The fixes for those 23 findings are specified as **D1–D8 in plan §9**,
  with deviations from the finders' own proposals recorded at the end of §9.
- D7 carries an **observable payment-behaviour change** (the refix tool now
  refuses ANY tree with payment evidence) — two named tests get rewritten;
  surface the before/after to Adnaan in the final report exactly as the C6
  changes were.

**Authority chain if the plan seems wrong somewhere:** `R9_FINDINGS.md` (the
iteration-9 findings + refuter evidence) → `R8_FINDINGS.md` →
`R8_HANDOFF_KICKOFF_PROMPT.md` (Adnaan's scope ruling of record) → the older
kickoffs it cites. Deviate only with the discrepancy stated out loud and
recorded in the plan file.

**Ground rules (bind, from plan §1 — do not relearn them):** verify before
claiming, read whole sources, mark `[assumption]`; NEVER push; commits
path-scoped per green step; suite only via `tools/run_suite.sh` (floor is now
578; re-measure and raise it after D8), green on Mac AND VM for anything that
ships; nothing deploys and no systemd unit is touched before the flip phase;
Drive I read-only forever; secrets never printed; every new test proved to
FAIL against unfixed code in a scratch copy OUTSIDE the repo (use the session
scratchpad dir); after any multi-agent step verify your own tree (`git diff`
every hunk, `grep -rn "MUTATION" --include="*.py" .`, `git status`) before
committing.

**Practical notes from the r8 session (save yourself the rediscovery):**
- VM sync: `gcloud compute scp <tgz> hl-pipeline-vm:/tmp/tree.tgz
  --zone=asia-south1-a --project=hl-gamedata-pipeline` then `gcloud compute
  ssh hl-pipeline-vm --zone=… --project=… --command='cd
  ~/hl-gamedata-continuous-test && tar xzf /tmp/tree.tgz' < /dev/null`.
  The dotted alias (`hl-pipeline-vm.asia-south1-a.…`) is for PLAIN ssh only —
  gcloud rejects it. VM pins: `UV=$HOME/.local/bin/uv`, `numpy==2.4.6
  opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0`. gcloud auth was live
  at handoff; if it expires, ask Adnaan to run `! gcloud auth login`.
- The working tree carries pre-existing UNCOMMITTED junk (deleted sample
  dirs, `.gitignore`, `SAMPLE_ANALYSIS_PLAYBOOK.md` edits) that predates the
  r8 session — leave it alone, never commit it, never clean it.
- The 29 open batch rows in the ledger are the dormant batch driver's
  rollback state — never touch them.
- For review iteration 10: copy `tools/review/flip-review-iter9.js` to your
  scratchpad as `flip-review-iter10.js`, retarget the regressions lane at the
  D1–D8 commits, refresh the suite numbers, APPEND accepted-behaviours
  entries 21–28 (drafted verbatim in plan §9 "After D8") keeping all existing
  entries, and invoke via the Workflow tool with `scriptPath`.

**The sequence, unchanged in substance:** D1–D8 (each: implement → fail-first
→ Mac gate → path-scoped commit) → new floor + both host gates + tree-verify
→ review iteration 10 (fix-in-iteration, quiet judged AFTER fixing, per
R5_TRIAGE §7) → iteration 11 only if 10 is not quiet — if 11 is not quiet:
STOP and hand Adnaan the list → independent REAL e2e (verdict relayed
VERBATIM) → THE FLIP per `FLIP_RUNBOOK.md` (§5 canary → §6 flip → §7 payment
endgame → §8 tree verify + LAST destructive act) → reject-reason table, final
independent live verifier, final report per `FLIP_SESSION_KICKOFF_PROMPT.md`
§6 steps 7–9. Report per plan §8: verdict-first, the D7 payment-behaviour
before/after included.
