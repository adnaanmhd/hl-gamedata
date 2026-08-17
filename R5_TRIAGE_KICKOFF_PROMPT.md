# Kickoff — r-loop 5 triage → e2e verification → handover

You are picking up the continuous-pipeline work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

`FLIP_SESSION_KICKOFF_PROMPT.md` is still the plan of record and everything in it
that this file does not change still binds. This file supersedes it on **status**
(what is done) and **scope** (what is yours).

---

## 0. LAUNCH PROTOCOL

Read this document and the files in §1, then reply with a SHORT readiness message
(what you understand the job to be, anything you think is wrong, and any question
you must have answered). Then **wait**.

**Work begins only when Adnaan types exactly: `center, form on me and launch attack`**

**Session config:** Model **Opus 5**. Include `ultracode` in the launch message —
the review/verification work is multi-agent and is not runnable single-threaded at
the required depth.

---

## 0a. Work order (this sequencing is a ruling)

1. **Triage + fix r-loop 5** (§3), re-verifying the four incomplete findings first.
2. **Apply the r-loop-3 #6 fix** (§4) — RULED. #15 ruled IGNORED.
3. **Iterations 6 → 7 → 8, stopping at the first quiet one** (§7) — RULED.
4. **Independent REAL e2e verification** (§5), fresh agent, verdict relayed verbatim.
5. **Finish `FLIP_HANDOVER.md` and hand over explicitly** (§6).

Suite green on Mac AND VM at every commit. Nothing deploys — R4 holds.

---

## 1. Read before code

1. `FLIP_SESSION_KICKOFF_PROMPT.md` — the plan and Adnaan's rulings (§4 R1–R4, §7,
   §8 ground rules).
2. `FLIP_HANDOVER.md` — **DRAFT, yours to finish**. Already carries the verified
   C2D pre-flight, the balloon finding, ledger measurements and known traps.
3. `PIPELINE_CONTINUOUS_DESIGN.md` (driver spec of record), `FLIP_RUNBOOK.md`
   (§6b/§6c rewritten), `PIPELINE_ARCHITECTURE.md`, `PIPELINE_IMPLEMENTATION_PLAN.md` §5.

---

## 2. Where things stand

**HEAD = `9886596`. Suite 404 green on Mac AND on the VM side checkout.**

```
PYTHONPATH=. uv run --with pytest --with numpy --with opencv-python-headless \
    --with rerun-sdk pytest pipeline/tests translator/tests -q
```
VM pins `numpy==2.4.6 opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0`, run in
`~/hl-gamedata-continuous-test` (NEVER `~/hl-gamedata` — that is the running
rebuild's tree).

| Commit | What |
|---|---|
| `ba1b17d` | R1–R3 split-cascade rulings + 9 regression tests |
| `c0831f2` | r-loop 3 — 33 findings fixed |
| `9886596` | r-loop 4 — 30 findings fixed (5 of its 6 blockers were r-loop-3 regressions) |

**Nothing is deployed.** Per R4 the whole set ships at the flip. **A different
session executes the canary and flip** (Adnaan, 2026-08-18) — see §6.

**Adversarial review loop: 5 spent, cap lifted to 8** (Adnaan 2026-08-18). No
iteration has gone quiet yet — r-loop 3 found 2 blockers, r-loop 4 found 6 (five of
them regressions from r-loop 3's own fixes), r-loop 5 found 3. Iterations 6–8 are
authorised but NOT mandatory: **stop at the first quiet one** — see §7, where
"quiet" is pre-registered.

---

## 3. FIRST JOB — triage and fix r-loop 5

Full results: `/private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/27e263d0-2a3c-459a-9eb5-92d5481d1216/tasks/wesl99r3j.output`
(JSON; the payload is under `["result"]`, with `confirmed` and `killed`).
Workflow script (reusable, keeps the 2-vote refute discipline and the
accepted-behaviours list): `.../workflows/scripts/flip-review-iter-wf_73905abc-9e5.js`

**26 confirmed, 9 refuted — 3 blockers, 17 majors, 6 minors.**

### 3.1 Four findings are NOT properly confirmed — re-verify, do not assume

Four verifier agents died on `API Error: Connection lost mid-response`, so these
survived on **one vote instead of two**. The 2-vote refute discipline was not
actually applied to them. **Re-run verification on these four before fixing or
discarding them:**

- `pipeline/continuous.py:1459` — unclean-drain `os._exit(0)` ends the pytest
  process with status 0 mid-suite
- `pipeline/fix.py:192` — `plan_fixes` silently drops every gate window whenever
  the plan contains a cut
- `pipeline/validate.py:928` — a holder whose lock the staleness breaker rescinded
  still rmdirs that path, disarming the mutex for the next holder
- `pipeline/validate.py:115` — de-mapping "frame_id column unparseable" cancels a
  repairable row-count delta

The first is credible and cheap to check: r-loop 4 added that `os._exit(0)`, and if
a test ever reaches it the suite exits **green having run only part of itself**.
Check that first.

### 3.2 The three blockers

1. **`continuous.py:347` — DISCOVERED work dirs are invisible to the media cap and
   to every reclaim sweep.** A failing multi-part zip leaves gigabytes in
   `work/<sid>` while `_download_one` returns the row to DISCOVERED forever;
   `_local_count` doesn't count it, no sweep reclaims it, `_stuck_lines` excludes
   DISCOVERED by design. Disk fills, `_pick_download` refuses on the F7 check, and
   cap-pressure stays silent so nothing says where the disk went. **Pre-existing,
   not a regression, and shared with the batch driver — keep `run.py`
   behaviour-compatible.** Same class as the QUARANTINED blocker fixed in r-loop 4.
2. **`tools/retrim_v2_session.py:73`** — r-loop 4 fixed the ms-rounded `created_at`
   anchor in `cutter.py` **only**; `FIX_RETRIM_HEAD` has the identical bug.
3. **`tools/vm_setup.sh:108`** — the lock-liveness check r-loop 4 added aborts
   falsely unless cwd is the repo root (it runs `sys.path.insert(0, ".")`), **after**
   the script has already disarmed the batch driver — so it leaves nothing armed.

### 3.3 Findings that contradict r-loop 4's own fixes — read these carefully

- `fix.py:274` — the gate-before-retrim ordering **does not** save the second fix
  attempt, because `FIX_RETRIM_HEAD` re-phases the VLM sample grid and
  `INP_FROZEN_ACTIONS` is re-raised anyway. (1 of 2 refuters defeated it; judge it
  yourself. This interacts directly with §4.)
- `translator/translate.py:83` — the keybind-direction fix now **stops a genuinely
  inverted Kamla `keybind.json` from being flipped**, so every key reads unbound and
  the keyboard column ships empty. That is the same catastrophic outcome the fix was
  meant to prevent, in the opposite direction. Needs a discriminator that is right
  in both directions, with tests for both.
- `validate.py:511` — gating can manufacture `CNT_ACTIONS_FEW` / `INP_KEYS_MISSING`,
  an **unfixable** reject that blames the player for rows the pipeline blanked.

---

## 4. RULED — apply the r-loop-3 #6 fix (Adnaan, 2026-08-18)

**This is a ruling, not a proposal. Implement it. Do not re-open it.**
**r-loop-3 #15 is RULED IGNORED** — do not fix it, and add it to the workflow's
accepted-behaviours list so no later iteration spends agents re-reporting it.
(If a future finding shows a *different* mechanism causing real harm — e.g.
`validate.py:511`, gating manufacturing an unfixable `CNT_ACTIONS_FEW` — that is a
separate finding on its own merits, not #15.)

The mechanism is verified: `analyze_sample._windows` sets window bounds as
*midpoints between VLM sample times*, and `rows_in_window` counts `action_frames`
over that same window — so **both the trigger and the gate span are derived from
VLM label boundaries, which are not stable across passes.** One boundary sample
flipping label moves the bound 15–30 frames; the recheck recounts, re-raises
`INP_FROZEN_ACTIONS`, spends attempt 2 re-gating, and rejects on pass 3.

**Proposed fix — move both the count and the gate onto the measured frozen run:**

- count `action_frames` over the **scanner-refined span** (`aux["refined"]`, already
  computed) rather than the VLM window — recompute in `_build_aux` from
  `has_action` + `tl.frame_at`, exactly as the scanner path already does for
  `extra_windows`;
- gate that same span plus `GATE_PAD_FRAMES` (2 frames, correctly sized for the
  ±1-frame scanner jitter it was built for);
- keep the VLM as the **classifier** (is this stretch menu/loading/pause rather than
  a legitimately still moment of play?) and stop using it as a boundary-finder.

Why it is more correct, not merely more stable: "frozen" is a *measurement*, and an
action on a **moving** frame outside the VLM's fuzzy edge is real gameplay — counting
it is a false positive and blanking it destroys real data. It preserves review-r3 #3's
invariant (*gate everything the trigger measured*) by moving the trigger.

**Rejected:** widening `GATE_PAD_FRAMES` to cover the drift — it blanks up to ~2 s of
genuine input per gate, destroying data to protect a counter.

**Optional narrow belt:** don't charge a fix attempt when the newly planned gate span
is already covered by the previously applied one (read from `fixlog.json`, now
atomic), bounded to one free repeat. This is **not** the reverted sidecar: it compares
*this plan to the last plan*, making no claim about file contents, so nothing can go
stale.

**r-loop-3 #15** (gate-blanked rows read as AFK): **RULED IGNORED** (Adnaan,
2026-08-18). The stretch it cuts is genuinely non-gameplay, so the larger cut is
arguably the correct outcome; the cost of chasing it is a coordinate-tracking
artifact of exactly the kind that already had to be reverted once.

**Pin the fix with tests that would fail before it:** identical span, two different
VLM boundary placements across passes → identical verdict and identical gate params;
plus an action on a *moving* frame just outside the window → NOT counted, NOT
blanked.

---

## 5. Then — independent REAL end-to-end verification

Per `FLIP_SESSION_KICKOFF_PROMPT.md` §5, and **only after** the triage above lands
with the suite green on both hosts.

A **fresh agent that wrote and reviewed none of this code**, exercising the actual
system: real VLM calls, real Drive II `_pipeline_test/` uploads purged afterwards via
`deliver.cleanup_test_folder`, real kill/resume. **Its verdict is relayed VERBATIM,
and a BLOCKED-with-error never becomes a pass.** This is in addition to the step-10
production verifier at the very end.

---

## 6. Then — finish the handover and hand over explicitly

`FLIP_HANDOVER.md` is drafted and marked **not live**. Finish it:

- flip the "Handover is live" line in §0 once §3–§5 are done;
- fill in §1's review-loop row with the final state;
- carry forward every verified-but-UNFIXED finding, severity-ordered, so nothing is
  lost silently;
- confirm §2's deploy set still matches reality (it is **four** items — driver,
  `a4f93de` tolerances, R1–R3, and every r-loop commit — not the three the older
  kickoff lists).

Then hand it to the flip session **explicitly**, per §8: *"confirm R1–R3 are in the
deploy set the flip actually executes… do not assume."*

---

## 7. RULED — up to three more iterations, stop at the first quiet one

**Adnaan, 2026-08-18.** The cap of 5 is lifted to **8**: iterations 6, 7 and 8 are
authorised, but **not mandatory**. **The moment an iteration comes back quiet, stop —
do not run the remaining ones.** Go straight to §5 (e2e verification) and §6
(handover).

**"Quiet" is pre-registered here so it cannot be redefined to suit the result:**

> **Quiet** = zero confirmed **blockers**, AND every confirmed major/minor is fixed
> in that same iteration with the suite green on **both** hosts.
>
> **Not quiet** = any confirmed blocker, or any finding you cannot land and verify
> cleanly.

Judge it **after** fixing, on the findings as confirmed — and note that a finding
whose verifiers died (§3.1) is *not* confirmed and must be re-verified before it
counts either way.

Run them in order — 6, then only if 6 is not quiet, 7; then only if 7 is not quiet,
8 — never in parallel, since each must review the previous one's fixes. Every
iteration keeps the full composition: whole-codebase + delta-since-loop-start +
adversarial hunting for bugs the loop's own fixes introduced, 2-vote refute, and the
§8 tree verification before each commit.

**Keep the accepted-behaviours list current each round** (add that iteration's ruled
decisions, plus r-loop-3 #15 per §4) or agents will spend themselves re-litigating
settled ground.

**If iteration 8 still is not quiet:** stop anyway — that is the authorised limit.
Fix what you can, then hand Adnaan every verified-but-unfixed finding,
severity-ordered, before the e2e verification. Do not silently spend a ninth.

---

## 8. Ground rules (unchanged, and they bind)

- **Verify before claiming. Read whole sources. Mark `[assumption]`.** Grepping is
  not reading.
- **NEVER push.** Commits path-scoped per green step.
- Full suite on **Mac AND VM** for anything that ships.
- **Do NOT deploy. Do not touch `hl-recal-rebuild`. Do not stop or start any systemd
  unit.** R4: everything rides the flip.
- Drive I (`drive-collect:`) is **read-only forever** (R6).
- Secrets in `~/.config/hl-gamedata/secrets.env` — never print, log or commit.
- **After every multi-agent review, verify your own working tree before committing:**
  `git diff` every hunk, `grep -rn "MUTATION" --include="*.py" .`, `git status` for
  agent-left files. A verifier once left mutations behind and **the full suite passed
  with them**. "Suite green" is not proof the tree is unmodified.
- Watch for **agents leaving orphaned threads**: one r-loop-3 test faked
  `shutdown()→False` without setting the stop event, and after monkeypatch teardown
  the orphaned lane thread listed **production Drive I**. `pipeline/tests/conftest.py`
  now has a guard that refuses real Drive listings — keep it.
