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

**Adversarial review loop: 5 of 5 iterations spent.** Adnaan's standing ruling is
*"stop when an iteration goes quiet."* No iteration has gone quiet: r-loop 3 found
2 blockers, r-loop 4 found 6, r-loop 5 found 3.

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

## 4. Adnaan's decision needed — r-loop-3 #6 (design is ready to apply)

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

**r-loop-3 #15** (gate-blanked rows read as AFK): my assessment is that it is close to
a non-issue — the stretch it cuts is genuinely non-gameplay, so the larger cut is
arguably correct. Note `validate.py:511` above may be the real harm hiding behind it.
Adnaan to rule.

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

## 7. Open decisions for Adnaan — surface, do not decide

1. **A sixth review iteration, beyond the cap of 5?** No iteration has gone quiet,
   and each has found regressions in the previous one's fixes. The counter-argument
   is real and measured: the cascade was producing **~76 child rows/hour** under the
   old rules, every one judged under the methodology R1–R3 replaces, so the hold has
   a running cost. Present both; let him choose.
2. **r-loop-3 #6** (§4) — apply the design, or leave open.
3. **r-loop-3 #15** — worth fixing at all?

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
