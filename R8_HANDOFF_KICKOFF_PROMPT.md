# Kickoff — r-loop 8 triage → e2e verification → handover

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

`FLIP_SESSION_KICKOFF_PROMPT.md` is still the plan of record.
`R6_HANDOFF_KICKOFF_PROMPT.md` superseded it on status. **This file supersedes
both on status.** The rulings in both still bind.

---

## 0. START IMMEDIATELY

**There is no launch phrase and no wait step — Adnaan retired that protocol on
2026-08-18.** Read this document and the files in §2, then begin the §3 work
order directly. Ask only if something here is wrong or a question only Adnaan
can settle.

**Session config:** Model **Opus 5**. Include `ultracode` in your first message
— the verification work is multi-agent and is not runnable single-threaded at
depth.

---

## 1. Where things stand

**HEAD = `869910d`. Suite 519 green on Mac AND VM**, both through the arming
gate — never a bare `pytest; echo $?`:

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

**Nothing is deployed.** R4 holds: the whole set ships at the flip, and a
**different session executes the canary and flip**.

**Review loop: 8 of 8 SPENT. There is no iteration 9 — do not run one.**
Iteration 8 was **stopped by Adnaan mid-run** at ~18:45 IST 2026-08-18, after
50 of 52 agents had finished. Its results were collected from the journal:

- **`R8_FINDINGS.md` (committed, this repo) — the findings of record.**
  22 raised → **21 CONFIRMED, 1 killed — 4 confirmed blockers, ~16 unique**
  after dedup (the duplicates are listed at the top of that file).
- All 44 refuter votes for the returned findings **completed** — the 2-vote
  discipline was FULLY applied to everything in that file. Nothing needs
  re-verification (unlike r-loop 5's dead-verifier situation).
- **The `tests-coverage` finder lane was lost at the stop** — it never
  returned its list. Whatever it would have raised does not exist anywhere.
  Surface this to Adnaan as an open item; re-running that single lane is his
  call, not yours (the cap is spent).
- Full machine-readable data (incl. untruncated refuter evidence):
  `/private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/r-loop8-collected.json`
  (+ `r-loop8-journal-raw.jsonl` beside it).

Per `R5_TRIAGE_KICKOFF_PROMPT.md` §7 (still binding): after iteration 8, **fix
what you can, then hand Adnaan every verified-but-unfixed finding,
severity-ordered, before the e2e verification.** That is your mandate here.

**The pattern held a fourth time:** the r-loop-8 blockers are regressions from
r-loop 7's own fixes, exactly as r-loop 7's were from r-loop 6's. Treat the
newest commits with the MOST suspicion, including everything in `869910d`.

---

## 2. Read before code

1. `R8_FINDINGS.md` — the work queue. Read it in full.
2. `FLIP_SESSION_KICKOFF_PROMPT.md` — plan + rulings R1–R4, §7, §8.
3. `R5_TRIAGE_KICKOFF_PROMPT.md` §7–§8 — still bind.
4. `R6_HANDOFF_KICKOFF_PROMPT.md` §4, §5, §7, §8 — the payment-split ruling,
   the CONT_DAILY_REPORTS ruling, accepted behaviours, traps.
5. `FLIP_HANDOVER.md` — DRAFT, yours to finish. A partially updated draft
   (payment-split section, trim-offset section, CONT_DAILY_REPORTS wording)
   exists at
   `/private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/FLIP_HANDOVER.draft.md`
   — use it as the base, then fill its §7/§8 placeholders.
6. `PIPELINE_CONTINUOUS_DESIGN.md`, `FLIP_RUNBOOK.md`, `PIPELINE_ARCHITECTURE.md`.

---

## 3. Work order

1. **Triage and fix the r-loop 8 findings** (§4 below has per-cluster
   guidance). Suite green on Mac AND VM through `tools/run_suite.sh` at every
   commit, path-scoped commits.
2. **Hand Adnaan the leftovers**: every confirmed-but-unfixed finding,
   severity-ordered, plus the lost tests-coverage lane as an open item.
3. **Independent REAL e2e verification** — a fresh agent that wrote and
   reviewed none of this code; real VLM calls; real Drive II `_pipeline_test/`
   uploads purged via `deliver.cleanup_test_folder`; real kill/resume.
   **Verdict relayed VERBATIM; a BLOCKED-with-error never becomes a pass.**
4. **Finish `FLIP_HANDOVER.md` and hand over explicitly.** While in there:
   `FLIP_RUNBOOK.md` §6c still says the deploy set is "three things" — it is
   four (driver, `a4f93de` tolerances, R1–R3, all r-loop fix sets); correct it
   and make both docs say plainly that `CONT_DAILY_REPORTS` returns to True
   immediately after the regen `--send` (it is a gap-closer, not policy).

---

## 4. Triage guidance for the r-loop 8 findings

Full claims/scenarios/evidence in `R8_FINDINGS.md`. **Reviewers' proposed
fixes have been wrong twice before in this loop** (the DISCOVERED age anchor;
the payment defer that broke d3) — simulate before adopting, and every new
test must be proved to fail against unfixed code in a scratch copy OUTSIDE the
repo.

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

## 5. Traps that keep biting (all learned this session — do not relearn them)

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
- **Do NOT deploy. Do not touch `~/hl-gamedata` on the VM. Do not stop or
  start any systemd unit.** R4: everything rides the flip.
- Drive I (`drive-collect:`) is **read-only forever** (R6).
- Secrets in `~/.config/hl-gamedata/secrets.env` — never print, log or commit.
- `pipeline/tests/conftest.py` has a guard that refuses real Drive listings —
  keep it.
- Suite through `tools/run_suite.sh` on BOTH hosts for anything that ships.
