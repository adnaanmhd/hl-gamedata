# Kickoff — execute R8_IMPLEMENTATION_PLAN.md (fix ALL r-loop 8 → iterations 9–11 → e2e → THE FLIP)

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

**`R8_IMPLEMENTATION_PLAN.md` is your complete work order.** It was written by
a session that read every affected source, finding, ruling and test in full,
and it encodes every decision — you do NOT need to re-read `R8_FINDINGS.md`,
the prior kickoff prompts, or the design docs to begin. Read the plan top to
bottom, then start at the first unchecked item in its §0 status ledger and keep
that ledger current as you land work.

**START IMMEDIATELY.** No launch phrase, no readiness reply (Adnaan retired
that protocol 2026-08-18). Ask only if something in the plan is provably wrong
or a question only Adnaan can settle — one question at a time.

**Session config:** Model **Fable 5**. This message carries `ultracode` — the
review iterations (plan §5) are multi-agent by ruling; the fix phase (§3) you
implement yourself, with subagents only for isolated probes/repros.

**Authority chain if the plan seems wrong somewhere:** `R8_FINDINGS.md` (the
confirmed findings + refuter evidence) → `R8_HANDOFF_KICKOFF_PROMPT.md`
(Adnaan's scope ruling of record) → the older kickoffs it cites. Deviate only
with the discrepancy stated out loud and recorded in the plan file.

**Ground rules (bind, from the plan §1 — do not relearn them):** verify before
claiming, read whole sources, mark `[assumption]`; NEVER push; commits
path-scoped per green step; suite only via `tools/run_suite.sh`, green on Mac
AND VM for anything that ships; nothing deploys and no systemd unit is touched
before the flip phase; Drive I read-only forever; secrets never printed;
every new test proved to fail against unfixed code in a scratch copy OUTSIDE
the repo; after any multi-agent step verify your own tree (`git diff` every
hunk, `grep -rn "MUTATION" --include="*.py" .`, `git status`) before
committing.
