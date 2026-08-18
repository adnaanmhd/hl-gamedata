# Kickoff — fix ALL of r-loop 8 → 3 more review iterations → e2e → THE FLIP

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

`FLIP_SESSION_KICKOFF_PROMPT.md` is still the plan of record.
`R6_HANDOFF_KICKOFF_PROMPT.md` superseded it on status. **This file supersedes
both on status and scope.** The rulings in both still bind except where §3
explicitly supersedes them (Adnaan, 2026-08-18).

---

## 0. START IMMEDIATELY

**There is no launch phrase and no wait step — Adnaan retired that protocol on
2026-08-18.** Read this document and the files in §2, then begin the §3 work
order directly. Ask only if something here is wrong or a question only Adnaan
can settle.

**Session config:** Model **Opus 5**. Include `ultracode` in your first message
— the review/verification work is multi-agent and is not runnable
single-threaded at depth.

---

## 1. Where things stand

**HEAD = `f8c629f` (code HEAD `869910d`). Suite 519 green on Mac AND VM**,
both through the arming gate — never a bare `pytest; echo $?`:

```bash
SUITE_FLOOR=450 bash tools/run_suite.sh \
    --with numpy --with opencv-python-headless --with rerun-sdk
```

VM: in `~/hl-gamedata-continuous-test` (NEVER `~/hl-gamedata` — post-rebuild
it is still the production tree), `UV=$HOME/.local/bin/uv`, pins
`numpy==2.4.6 opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0`. The side
checkout already has the r-loop-7 code synced (519 green there, verified).

| Commit | What |
|---|---|
| `2244758` | r-loop 6 fix set (the tree r-loop 7 reviewed) |
| `b851ea2` | RULED payment fix — the uploaded/accepted stamp split |
| `06b936b` | r-loop 6: trim-clock major + five minors |
| `869910d` | r-loop 7: 2 blockers + 5 majors + 2 minors (the tree r-loop 8 reviewed) |
| `f8c629f` | this handoff + `R8_FINDINGS.md` |

**Nothing is deployed yet.** R1–R3 and every r-loop fix set ship at the flip —
which is now YOURS to execute (§3.4).

**Iteration 8 was stopped by Adnaan mid-run** at ~18:45 IST 2026-08-18, after
50 of 52 agents had finished. Its results were collected from the journal:

- **`R8_FINDINGS.md` (committed, this repo) — the findings of record.**
  22 raised → **21 CONFIRMED, 1 killed — 4 confirmed blockers, ~16 unique**
  after dedup (the duplicates are listed at the top of that file).
- All 44 refuter votes for the returned findings **completed** — the 2-vote
  discipline was FULLY applied to everything in that file. Nothing needs
  re-verification.
- **The `tests-coverage` finder lane was lost at the stop** — it never
  returned its list. The three authorized iterations below each run a
  tests-coverage lane, which re-covers whatever was lost.
- Full machine-readable data (incl. untruncated refuter evidence):
  `/private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/r-loop8-collected.json`
  (+ `r-loop8-journal-raw.jsonl` beside it).

**The pattern held a fourth time:** the r-loop-8 blockers are regressions from
r-loop 7's own fixes, exactly as r-loop 7's were from r-loop 6's. Treat the
newest commits with the MOST suspicion, including everything in `869910d` —
and everything YOU commit.

---

## 2. Read before code

1. `R8_FINDINGS.md` — the immediate work queue. Read it in full.
2. `FLIP_SESSION_KICKOFF_PROMPT.md` — plan + rulings R1–R4, §7, §8.
3. `R5_TRIAGE_KICKOFF_PROMPT.md` §7–§8 — the pre-registered "quiet" test and
   the review discipline still bind.
4. `R6_HANDOFF_KICKOFF_PROMPT.md` §4, §5, §7, §8 — the payment-split ruling,
   the CONT_DAILY_REPORTS ruling, accepted behaviours, traps.
5. `FLIP_RUNBOOK.md` — you will execute it end to end (§3.4). §6b/§6c are the
   authoritative command sequences.
6. `FLIP_HANDOVER.md` — DRAFT. Now that the flip is yours (§3.4) it is no
   longer a baton-pass; keep it as the running record of flip state. A
   partially updated draft (payment-split section, trim-offset section,
   CONT_DAILY_REPORTS wording) exists at
   `/private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/FLIP_HANDOVER.draft.md`.
7. `PIPELINE_CONTINUOUS_DESIGN.md`, `PIPELINE_ARCHITECTURE.md`.

---

## 3. Work order (RULED — Adnaan, 2026-08-18)

### 3.1 Fix ALL the r-loop 8 findings first

Every confirmed finding in `R8_FINDINGS.md` — blockers, majors AND minors —
gets fixed. Not "what you can": all of them. §4 has per-cluster guidance.
Suite green on Mac AND VM through `tools/run_suite.sh` at every commit,
path-scoped commits, every new test proved to fail against unfixed code in a
scratch copy OUTSIDE the repo.

### 3.2 Then: up to THREE more adversarial review iterations (9, 10, 11)

Adnaan authorized three more on 2026-08-18 (supersedes the spent cap of 8).
Each keeps the full composition: **whole-codebase + delta-since-loop-start +
adversarial hunting for regressions from the previous iteration's own fixes +
a tests-coverage lane**, multi-agent, findings verified with the **2-vote
refute discipline** (a finding dies only if BOTH refuters defeat it). Fix
every confirmed finding in the same iteration.

- Run them in order — 9, then only if 9 is not quiet, 10; then 11 — never in
  parallel: each must review the previous one's fixes.
- **Stop at the first QUIET one.** "Quiet" stays as pre-registered in
  `R5_TRIAGE_KICKOFF_PROMPT.md` §7: zero confirmed blockers AND every
  confirmed major/minor fixed in that same iteration with the suite green on
  both hosts. Judge it after fixing.
- **If iteration 11 is still not quiet: STOP.** Hand Adnaan every
  verified-but-unfixed finding, severity-ordered, and do NOT proceed to §3.3
  or §3.4 without his explicit go-ahead. Clean is the gate to everything
  after this point.
- Reusable workflow (keeps the 2-vote discipline + accepted-behaviours list —
  edit and re-invoke rather than rebuilding; keep the accepted list CURRENT
  each round or agents re-litigate settled ground):
  `/private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/flip-review-iter8.js`
- Apply the tree-verification discipline after every iteration (§5).

### 3.3 Then: independent REAL end-to-end verification

Only once the loop has exited clean. A fresh agent that wrote and reviewed
none of this code, exercising the actual system: real VLM calls, real Drive II
`_pipeline_test/` uploads purged afterwards via `deliver.cleanup_test_folder`,
real kill/resume. **Verdict relayed VERBATIM; a BLOCKED-with-error never
becomes a pass.**

### 3.4 Then: THE FLIP — this session executes it

**Supersession (Adnaan, 2026-08-18):** the earlier ruling that "a different
session executes the canary and flip" is WITHDRAWN. You proceed through the
flip yourself, per `FLIP_RUNBOOK.md`:

1. **Canary** — runbook §5: side checkout, `HL_PIPELINE_HOME=~/hl-pipeline-test`,
   TEST-mode Telegram, Drive II `_pipeline_test/` only (purged after), the
   3-leg kill matrix, autoscale observed, digest fires. Nothing in the real
   pipeline home may be touched by any canary step.
2. **Flip** — runbook §6, Telegram announce before and after: stop
   `hl-recal-watch` then any rebuild-era unit still live; E2→C2D resize with
   the balloon check (do not block the flip on a zone stockout — §6b carries
   the undo); `CONT_DAILY_REPORTS = False` committed, deploy HEAD, verify,
   `recal_refix_reset` dry-run → `--yes`, arm via
   `vm_setup.sh --enable-continuous`, watch the first hour.
3. **THE DEPLOY SET MUST CARRY ALL THE SHEET-PRODUCING LOGIC AND CODE.**
   Deploying HEAD does this by construction, but VERIFY it on the VM after
   rsync, before arming — expect ALL of:
   ```bash
   cd ~/hl-gamedata
   grep -n "KEEP_GATE_MAX_S\|SCANNER_STATIC_MIN_S\|KEEP_GATE_MAX_FRAC" pipeline/config.py
   #   -> KEEP_GATE_MAX_S = 5.0, SCANNER_STATIC_MIN_S = 0.8, NO KEEP_GATE_MAX_FRAC
   grep -c "accepted_reported_at" pipeline/reports.py pipeline/ledger.py
   #   -> non-zero in both (the RULED stamp split)
   grep -n "read_counted_record\|write_counted_record" tools/recal_regen_sheets.py
   #   -> both present (the r-loop-8 resume-record fix)
   grep -n "first_pts_abs" translator/trim.py     # the trim-clock fix
   ```
   plus whatever grep-visible markers your r-loop-8 daily-send fix adds
   (the durable counted record, §4c). If any check fails, the rsync did not
   ship what you tested — stop and fix before arming.
4. **Payment endgame** — runbook §7, the sheet-producing step itself: driver
   stopped → `recal_regen_sheets.py` preview → sanity-read BOTH sheets →
   `--send` (final invariant: anchor == `2026-08-16T05:32:50+00:00`) → flip
   `CONT_DAILY_REPORTS = True`, commit, deploy, restart. Dailies resume from
   the regen anchor. Update `NOTE_FOR_D3.md`; purge old sheet copies from the
   GCS mirror after replacements verify.
5. **Tree verify + LAST destructive act** — runbook §8.
6. **Reject-reason table, final independent live verifier, final report** —
   `FLIP_SESSION_KICKOFF_PROMPT.md` §6 steps 7–9. Mixed-methodology
   comparisons labelled as such, verifier verdict verbatim.

While in the docs: `FLIP_RUNBOOK.md` §6c still says the deploy set is "three
things" — it is FOUR (driver, `a4f93de` tolerances, R1–R3, all r-loop fix
sets); correct it, and make the docs say plainly that `CONT_DAILY_REPORTS`
returns to True immediately after the regen `--send` (a gap-closer, not
policy).

---

## 4. Triage guidance for the r-loop 8 findings

Full claims/scenarios/evidence in `R8_FINDINGS.md`. **Reviewers' proposed
fixes have been wrong twice before in this loop** (the DISCOVERED age anchor;
the payment defer that broke d3) — simulate before adopting.

**(a) BLOCKER — host carve-out re-runs a partially-applied plan**
(`continuous.py:880` + `run.py:575`, one defect, two drivers). The r-loop-7
carve-out refunds the attempt and parks on FIX_QUEUED with reasons untouched;
the retry re-dispatches the identical plan from step 0, so a plan that got
past FIX_RETRIM_HEAD before the host error trims ANOTHER head off the same
video on every retry (measured 300s→175s over five passes). The reviewer's
shape is plausible — park on FIX_QUEUED only when `not any(a["ok"] for a in
out["applied"])`, otherwise refund the attempt but route to REVALIDATING so
the plan re-derives from the half-fixed copy — but verify the
FIX_RETRIM_HEAD/FIX_LAGSHIFT_CSV non-idempotence claims yourself first.

**(b) BLOCKER — the `head_s > duration_s` guard breaks split children**
(`fix.py:730`). `head_s` is the offset into the RAW recording; for a split
child that is the segment's start, so any second-or-later segment with offset
greater than its own length fails FIX_RETRANSLATE on both attempts and is
rejected. The clip duration must not appear in that test at all — bound
against what the raw sidecar can cover (e.g. the last event timestamp), or
fail only when rebase_events keeps zero events from a non-empty sidecar. Add
a split-child regression test (parent raw/ copied in, created_at far into the
source).

**(c) BLOCKER — the daily send has no durable counted record**
(`run.py:902`). Pre-existing, exposed by the split: any interruption between
the stamps and the `.sent` marker (one `database is locked` on a stamp, ENOSPC
on the anchor write) makes the retry REGENERATE the sheet — and post-stamp,
regeneration excludes every stamped root, so a SMALLER (even header-only)
sheet overwrites the real one and is sent as the payment document. The flip
tool already solved exactly this with `.regen-v2-counted.json` written before
any side effect; give `send_daily_report_if_due` the same durable record
(write `{counted, accepted}` atomically before stamping; on retry with the
record present, re-send the CSV on disk and stamp from the record — never
regenerate).

**(d) MAJOR cluster — the accepted-mark seal is overloaded**
(`reports.py:506`, `reports.py:527`, `recal_refix_reset.py:280/:284` — four
findings, one root cause). One column carries two meanings: per-node
"counted" AND whole-tree "sealed". Consequences: an ordinary daily send
stamps a DELIVERED/REJECTED root's own mark, which then reads as a tree seal
locking its live children's future hours out; the refix seal is all-or-nothing
over a per-node mark, so a partly-paid tree loses its unpaid delivered hours
(and `sealed_roots` names only the PAID nodes, hiding exactly what was
swallowed); and the late-arrival deferral is now pure loss (the split removed
its premise — an in-window root is paid incrementally, the identical late
root reaches no sheet at all while a HOLD_VLM node blocks it). The reviewer's
separate-column suggestion (`tree_sealed_at`, written only by
recal_refix_reset; `sealed` reads only that; per-node marks decide everything
else; delete the late-arrival `continue`, keep the loud log) is the cleanest
shape — but this touches RULED payment semantics, so if the fix changes any
observable sheet behaviour beyond closing these holes, show Adnaan the
before/after on the d3 conservation invariant and the r6 split tests first.

**(e) MAJOR — gate-record propagation is per-ENTRY, not per-WINDOW**
(`fix.py:557`, found by two lanes). One FIX_GATE_WINDOW step carries ALL
windows and ONE aggregate destroyed-inventory, so with two frozen windows in
different segments, both segments inherit the full inventory and a sibling's
genuine INP_KEYS_MISSING/CNT_ACTIONS_FEW is still downgraded. Needs
per-window destroyed inventory from `gate.gate_windows` (it already computes
per-window blanked rows) and per-segment filtering on those. Note the r-loop-7
unit test hand-built the note shape (`{"actions":..., "key_frames":...}`)
instead of gate.py's real `{"destroyed": {...}}` — fix the test to use the
real writer's shape while you are there (that is the standing "test against
the real writer" trap).

**(f) MAJOR — `run_suite.sh` red on the runbook's pinned invocation**
(`tools/run_suite.sh:23`). Claim: the gate fails under the exact
`--with numpy==2.4.6 ...` form FLIP_RUNBOOK §6b mandates. **REPRODUCE THIS
FIRST on both hosts before touching anything** — if real it breaks the flip
runbook's own command; if not reproducible, record why and move on.

**(g) MAJOR — digest/stuck-list blindness + retry loops**
(`continuous.py:1175`, `continuous.py:1444`, `continuous.py:195`). The stuck
list cannot see either host-error retry loop (V lane and the new fix-lane
park), so a permanently broken host condition spins invisibly; the 3-h digest
has no retry cadence stamp (a Telegram outage → rebuild-and-resend every
housekeeping tick); AlertBook records an alert as sent before sending, so a
failed send silences that alert for the full TTL. Bounded, mechanical fixes.

**(h) MAJOR — `rebase_events` uses untrusted key/button as dict keys**
(`translator/trim.py:137`) — the same container-key-code class r-loop 7 fixed
in bin_session, one call upstream. Unhashable key/button crashes the
translate; guard with the same isinstance discipline.

**(i) Minors** — `raw_int` OverflowError on `Infinity`/huge ints (catch
OverflowError + non-finite floats, two duplicate findings); `keys.py:93`
normalize_literal non-string crash (the nested/modifier form the r-loop-7
guard missed); `v2.py:211` translate_bundle_v2 reads metadata.json unguarded
on the raw-only path; `validate.py:90` five STR_SJ_INVALID classes map to a
rewrite that provably cannot clear them (check against the r-loop-7
FIX_SESSIONJSON_REWRITE change before believing it); `run_suite.sh`
SUITE_FLOOR default is 440 against 519 collected tests — raise the script
default to ~515 and keep raising it as the suite grows.

---

## 5. Traps that keep biting (all learned this loop — do not relearn them)

- **After every multi-agent step, verify your own tree before committing:**
  `git diff` every hunk, `grep -rn "MUTATION" --include="*.py" .`,
  `git status` for agent-left files. A verifier once left mutations behind
  with the suite green.
- **Every new test proves it fails first**, in a scratch copy OUTSIDE the
  repo (`git archive HEAD | tar -x -C <scratch>`). Tests that guard existing
  code prove themselves by catching a mutation instead.
- **Test against the real writer, not a hand-built shape** — r-loop 8 found
  an r-loop-7 test neutered by exactly this (see §4e).
- **VM sync:** `tar czf - | gcloud compute ssh` HANGS. Use
  `gcloud compute scp` of a tarball, then a separate `ssh` with `< /dev/null`.
- **gcloud auth expires mid-session** and cannot reauth non-interactively —
  ask Adnaan to run `! gcloud auth login`.
- Re-check **every** module that calls a changed entry point, not just the
  ones whose tests fail.

---

## 6. Ground rules (unchanged, and they bind)

- **Verify before claiming. Read whole sources. Mark `[assumption]`.**
  Grepping is not reading. Never relay a reviewer's number without
  reproducing it.
- **NEVER push.** Commits path-scoped per green step.
- **Nothing deploys before §3.4** — until the flip itself, do not touch
  `~/hl-gamedata` on the VM and do not stop or start any systemd unit.
- Drive I (`drive-collect:`) is **read-only forever** (R6).
- Secrets in `~/.config/hl-gamedata/secrets.env` — never print, log or commit.
- `pipeline/tests/conftest.py` has a guard that refuses real Drive listings —
  keep it.
- Suite through `tools/run_suite.sh` on BOTH hosts for anything that ships.
- The flip's destructive steps keep their runbook gates: parachute backup
  before `recal_rebuild_reset`-class actions, preview before `--send`,
  `recal_verify_tree.py` CLEAN before any deletion.
