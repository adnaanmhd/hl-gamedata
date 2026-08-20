# Kickoff — THE FLIP: wipe Drive II, deploy, process ALL of Drive I (new session; RULED Adnaan 2026-08-20)

**PRECONDITION — do not start without BOTH:** (1) the e2e session's
verdict (GREEN or GREEN-WITH-FINDINGS that Adnaan explicitly accepted)
and (2) **Adnaan's explicit go in THIS session.** If either is
missing, stop and ask.

You are executing the production flip for the continuous pipeline in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`, under the
**CLEAN-SLATE RULING (Adnaan, 2026-08-20, his answers on record in
the R19–R21 executor session):** Drive II is wiped clean (all ours,
nothing of the client's there, client has no live access, parachute
EXPLICITLY WAIVED — "wipe it"), the ledger starts FRESH (no payment
memory carried — **no payments have ever gone out**, so there is no
paid history to protect), and the pipeline processes **ALL of Drive I
from zero** (nothing excluded; ~1285 raw hours as of the 08-19
snapshot, growing ~290 h/day — re-measure at flip time). Background:
`R8_IMPLEMENTATION_PLAN.md` (§0 ruling chain, §1 capsule, §6) and the
e2e session's verdict report (relay its findings verbatim in yours).

**Ground rules (bind, plan §1):** verify before claiming; NEVER push;
**Drive I read-only forever** (the wipe is Drive II ONLY — triple-check
every purge target against the drive-deliver: remote); secrets never
printed; suite floor 846 on both hosts before deploying; look before
every delete — list the target and confirm it matches "all ours"
before purging; gcloud auth expires — ask Adnaan for
`! gcloud auth login`.

## Clean-slate amendments to FLIP_RUNBOOK.md (RULED — read before following the runbook)

The runbook's §7 payment endgame (the legacy invariant anchor
`2026-08-16T05:32:50+00:00`, the reject-reason table over old ledger
data) and §8's legacy-reconciliation destructive acts existed to
reconcile the OLD ledger's payment history. **They are SUPERSEDED by
the clean slate: no payments went out, the old sheets are void, the
old ledger is retired.** Follow the runbook for the canary (§5), the
systemd flip (§6) and the verify patterns; SKIP the legacy payment
reconciliation, and say so explicitly in your report. Payment
reporting starts fresh from the new ledger's first daily sheet. The
29 open batch rows' "never touch" rule is RELEASED (Adnaan, 08-20) —
they retire with the old ledger. The old local `HL_PIPELINE_HOME` is
NOT deleted — move it aside as a dated archive (the only history of
the pre-wipe era); the new home starts empty.

## Sequence

1. **c2-56 resize (RULED):** stop `hl-pipeline-vm` → set machine type
   to the c2 56-CPU shape (confirm exact type with Adnaan — "c2
   instance, 56 cpus"; c2-standard-56 is the candidate
   `[assumption]`) → start → verify. `CONT_POOL_MAX = cpu_count − 12`
   → 44 workers automatically; confirm the systemd unit's env agrees.
2. **Sync + both host gates at floor 846** (plan §1 recipe).
3. **Measure Drive I** (listing only — read-only): total sessions/fh
   per game/operator; report the number and the re-projected ETA
   before proceeding.
4. **WIPE DRIVE II** (the ruled destructive act): `rclone lsd`/size
   the drive-deliver: remote first and confirm the contents match
   "our deliveries, ~65h-era plus test folders, nothing
   client-authored"; if anything contradicts that description STOP
   and surface it. Then purge everything including `_pipeline_test/`.
   Parachute WAIVED by explicit ruling — do not re-ask; Drive II
   content is re-derivable from Drive I + this pipeline.
5. **Fresh state:** new `HL_PIPELINE_HOME` (new ledger, new reports
   dir; old home archived aside). No recal/rebuild tooling needed —
   there is nothing to reconcile.
6. **FLIP_RUNBOOK §5 canary** (production shape, small seed set from
   Drive I). In the canary's FIRST HOURS: **measure real min/fh and
   fh/day and re-project** (pre-measurement band on c2-56: 8–12
   min/fh ≈ 120–180 fh/day conservative, 6–8 optimistic). Watch the
   digest for 429-pressure — the Gemini quota ladder is the likeliest
   ceiling at 44 workers; if it pins, the lever is quota/tier, not
   CPU.
7. **FLIP_RUNBOOK §6 — the flip itself** (systemd deploy; the
   continuous driver goes live against Drive I).
8. **Full-drive processing, priority order (Aug 24 BINDS and the
   full drive cannot clear by then — see the arithmetic in the plan
   §5):** confirm the order with Adnaan at this step in one
   question — the default recommendation is Kamla first (the 500h
   phase-1 target side with 1175h collected), oldest-first within a
   game, OW behind it. OW's input-action mapping
   (`satellite_camera`) may still be pending a ruling — check plan
   §6's QUEUED item status before delivering OW; if unbuilt and
   Adnaan ruled process-now, deliver OW as-is and record which OW
   sessions need the later action-column re-map.
9. **Daily payment sheets start fresh** from the new ledger (first
   send is the new era's first counted window; the folder-issues
   report follows its marker rule).
10. **Final report** per `FLIP_SESSION_KICKOFF_PROMPT.md` §6 steps
   7–9: verdict-first; measured throughput vs projection; backlog
   ETA per game; the wipe recorded (what was listed, what was
   purged); the plan-§6 payment-surface list with honest
   review-status labels (O-set verified by the e2e, not a review
   pass); the QUEUED OW item.

## Rollback

The batch driver rollback is RETIRED with the old ledger (clean
slate). The rollback for the new era is: stop the systemd unit
(processing halts; Drive I is untouched by design; Drive II holds
only new-era deliveries, re-derivable). If the canary or first hours
show a blocker-class defect: stop the unit, report verbatim, do NOT
improvise fixes in production — fixes return through a session with
the full §2 discipline.
