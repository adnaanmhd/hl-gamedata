# Kickoff — THE FLIP: deploy + start processing Drive I (new session; RULED Adnaan 2026-08-20)

**PRECONDITION — do not start without BOTH:** (1) the e2e session's
verdict (GREEN or GREEN-WITH-FINDINGS that Adnaan explicitly accepted)
and (2) **Adnaan's explicit go in THIS session.** If either is
missing, stop and ask.

You are executing the production flip for the continuous pipeline in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`, then starting the
real processing of the Drive I backlog (~600+ footage-hours).
**`FLIP_RUNBOOK.md` is your command script — follow it end to end.**
Background: `R8_IMPLEMENTATION_PLAN.md` (§1 capsule, §5, §6 — the
payment-surface list you must carry into the final report), and the
e2e session's verdict report (relay its findings verbatim in yours).

**Ground rules (bind, plan §1):** verify before claiming; NEVER push;
Drive I read-only forever; secrets never printed; suite floor 846 via
`tools/run_suite.sh` on both hosts before deploying anything;
destructive gates INTACT — parachute before every reset-class action,
preview before any `--send`, `recal_verify_tree.py` CLEAN before any
deletion; the 29 open batch rows are the dormant batch driver's
rollback state — never touch them. gcloud auth expires — ask Adnaan
to run `! gcloud auth login`.

## Sequence

1. **c2-56 resize (RULED Adnaan 2026-08-20, supersedes the 08-16
   e2-standard-32 ruling):** stop `hl-pipeline-vm` → set machine type
   to the c2 56-CPU shape (confirm the exact type with Adnaan — his
   words were "c2 instance, 56 cpus"; c2-standard-56 is the obvious
   candidate `[assumption]`) → start → verify with `gcloud compute
   instances describe`. `CONT_POOL_MAX = cpu_count − 12` autoscales
   the validation pool to 44 — no config change needed; confirm the
   systemd template/unit worker env agrees before install.
2. **Sync + both host gates at floor 846** (plan §1 recipe; the
   pipe-over-ssh form HANGS).
3. **FLIP_RUNBOOK §5 canary** (production shape this time). In the
   canary's FIRST HOURS: **measure real min/fh and fh/day, and
   re-project the backlog** (conservative pre-measurement band:
   8–12 min/fh ≈ 120–180 fh/day → ~600 fh clears in ~3.5–5 days;
   optimistic 6–8 min/fh). Watch the digest for 429-pressure — the
   Gemini quota ladder is the likeliest ceiling at 44 workers; if it
   pins, the lever is quota/tier, not CPU.
4. **FLIP_RUNBOOK §6 — the flip itself** (systemd deploy on the VM;
   `~/hl-gamedata` becomes the continuous tree per the runbook).
5. **FLIP_RUNBOOK §7 — payment endgame** with the final invariant
   anchor `2026-08-16T05:32:50+00:00`; preview before `--send`.
6. **FLIP_RUNBOOK §8** — tree verify + the LAST destructive act
   (only with `recal_verify_tree.py` CLEAN) → reject-reason table →
   final independent live verifier (verdict relayed VERBATIM).
7. **Drive I processing begins** (the whole point): the continuous
   driver scans Drive I; name-operator folders are VALID (Q5
   amended); Telegram per-batch reports are live; the folder-issues
   daily report fires after the payment `.sent` marker.
   **OW decision — ask Adnaan at this step, one question:** process
   OW sessions now and reprocess their input-action columns when the
   `satellite_camera` task lands (recommended; ~1–3 min/fh extra on
   OW only, later), or hold OW until it lands. Kamla processes
   either way.
8. **Final report** per `FLIP_SESSION_KICKOFF_PROMPT.md` §6 steps
   7–9: verdict-first; the FULL plan-§6 payment-surface list with
   honest review-status labels (O1 verified by the e2e, not a review
   pass); measured throughput vs the projection; the backlog ETA;
   the QUEUED OW satellite-camera item.

## Rollback

The batch driver (`pipeline/run.py`) and its 29 open ledger rows are
the rollback state. If the canary or the first processing hours show
a blocker-class defect: stop the unit, report verbatim, do NOT
improvise fixes in production — the fix returns through a session
with the full §2 discipline.
