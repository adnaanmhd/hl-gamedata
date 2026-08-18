# Kickoff — r-loop 6 completion → payment fix → iterations 7/8 → e2e → handover

You are picking up the continuous-pipeline flip work in
`/Users/adnaan/Documents/hl-projects/hl-gamedata`.

`FLIP_SESSION_KICKOFF_PROMPT.md` is still the plan of record. `R5_TRIAGE_KICKOFF_PROMPT.md`
superseded it on status and scope. **This file supersedes both on status**, and adds two
new rulings (§4, §5).

---

## 0. LAUNCH PROTOCOL

Read this document and the files in §2, then **wait**. Do not summarise back what you
read. If something in here is wrong or you have a question only Adnaan can settle, say
that — otherwise say nothing and wait.

**Work begins only when Adnaan types exactly: `center, form on me and launch attack`**

**Session config:** Model **Opus 5**. Include `ultracode` in the launch message — the
review/verification work is multi-agent and is not runnable single-threaded at depth.

---

## 1. Where things stand

**HEAD = `2244758`. Suite 455 green on Mac AND VM**, both through the new arming gate.

```bash
SUITE_FLOOR=450 bash tools/run_suite.sh \
    --with numpy --with opencv-python-headless --with rerun-sdk
```

On the VM, in `~/hl-gamedata-continuous-test` (NEVER `~/hl-gamedata` — that is the
rebuild's tree), with `UV=$HOME/.local/bin/uv` and the pins
`numpy==2.4.6 opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0`.

| Commit | What |
|---|---|
| `c522279` | r-loop 5: 3 blockers + the `os._exit` suite-truncation major + Kamla keybind |
| `844d323` | r-loop 5: the 3 re-verified findings whose verifiers had died |
| `382bb42` | **RULED** — frozen-window trigger moved onto the measured span |
| `6c9720f` | r-loop 5 #11: content bars blind to pipeline-blanked rows |
| `2c240ee` | r-loop 5 #4, #5, #10 |
| `065037e` | r-loop 5 majors #7 #14 #15 #16 #17 #19 + minors #21–#26 |
| `4deee9c` | r-loop 5 #18 — closes all 26 of r-loop 5 |
| `822602f` | r-loop 6: 2 of 3 blockers + 5 regressions from r-loop 5's own fixes |
| `2244758` | r-loop 6: gate-record propagation, V-lane carve-out, arming gate |

**Nothing is deployed.** R4 holds: the whole set ships at the flip, and a **different
session executes the canary and flip**.

**Review loop: 6 of 8 spent.** Iteration 6 was **not quiet** — 28 raised, 24 confirmed,
3 unique blockers. Most of the serious ones were regressions from r-loop 5's own fixes,
the same pattern r-loop 4 saw with r-loop 3. Iterations 7 and 8 remain authorised;
**stop at the first quiet one**, where quiet is pre-registered in
`R5_TRIAGE_KICKOFF_PROMPT.md` §7 and still binds.

Full r-loop 6 results (JSON, `["result"]` → `confirmed` / `killed`):
`/private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/27e263d0-2a3c-459a-9eb5-92d5481d1216/tasks/wflezrfm5.output`

Reusable review workflow (keeps the 2-vote refute discipline and the accepted-behaviours
list — **edit and re-invoke rather than rebuilding**):
`/Users/adnaan/.claude/projects/-Users-adnaan-Documents-hl-projects-hl-gamedata/91a79740-5a2f-4be3-b676-f7624ed01f55/workflows/scripts/flip-review-iter6-wf_1cb458a0-e48.js`

---

## 2. Read before code

1. `FLIP_SESSION_KICKOFF_PROMPT.md` — the plan and rulings R1–R4, §7, §8 ground rules.
2. `R5_TRIAGE_KICKOFF_PROMPT.md` — §7 (the pre-registered "quiet" test) and §8 still bind.
3. `FLIP_HANDOVER.md` — **DRAFT, still yours to finish.**
4. `PIPELINE_CONTINUOUS_DESIGN.md`, `FLIP_RUNBOOK.md`, `PIPELINE_ARCHITECTURE.md`.
5. `REBUILD_SESSIONS_NOTE.md` + `rebuild-sessions-2026-08-18.csv` — the finished rebuild's
   1396 session ids, kept for a possible rerun through the fixed pipeline.

---

## 3. Work order

1. **Implement the RULED payment fix** (§4). This is first — it is the only outstanding
   blocker.
2. **Fix the remaining r-loop 6 findings** (§6).
3. **Iteration 7, then only if 7 is not quiet, iteration 8** (`R5_TRIAGE…` §7).
4. **Independent REAL e2e verification** — a fresh agent that wrote and reviewed none of
   this code, real VLM calls, real Drive II `_pipeline_test/` uploads purged via
   `deliver.cleanup_test_folder`, real kill/resume. **Verdict relayed VERBATIM; a
   BLOCKED-with-error never becomes a pass.**
5. **Finish `FLIP_HANDOVER.md` and hand over explicitly.**

Suite green on Mac AND VM **through `tools/run_suite.sh`** at every commit. Nothing
deploys.

---

## 4. RULED — fix the stranded-hours payment bug properly, NOW (Adnaan, 2026-08-18)

**This is a ruling. Implement it. Do not re-open it, and do not defer it past the flip.**

**The bug.** `reports.build_sheet_rows` stamps `uploaded_reported_at` on a root as soon as
it is `r_countable` (raw duration probed, which happens right after download) — even when
its split children are still being validated or delivered. The sheet records
`accepted_hrs = 0` and stamps. Once stamped, **both** `in_window` and `late` evaluate
False forever (lines ~423 and ~434), so the whole tree walk is skipped on every later
sheet and the delivered hours never reach any payment document. The player is paid
nothing for footage that shipped to the client.

**Measured on the real 08-18 rebuild dump** (do not re-derive; it is in
`rebuild-sessions-2026-08-18.csv`):

- 0 stamped roots exist today — `recal_rebuild_reset` nulled every rebuild-era stamp, so
  no historical harm is visible and the whole cohort will be counted fresh. **The risk is
  ahead, not behind.**
- **135 of 309 countable roots (43.7%)** are countable-but-unsettled right now, holding
  **16.84 h** of raw footage in unsettled nodes. Every one would be stamped at 0 accepted
  hours if a sheet ran.
- The reviewer's "97.5% of the production dump" figure could **not** be reproduced. Use
  the numbers above, not that one.

**Why the obvious fixes are wrong** — both were tried and reverted:

- *Defer the in-window path like the late path*: five tests fail. It breaks the **d3
  "family-killer" conservation invariant** (`test_uploaded_hours_conservation_invariant`:
  uploaded hours summed across ALL sheets equal `duration_raw_s` over countable roots —
  "nothing dropped, nothing doubled"), because a root that never settles is never counted
  at all. It also contradicts the **08-15 ruling** that a stalled cohort must still appear
  with a loud log (`test_stalled_cohort_logs_and_understates_attributably`).
- *Simply stamp later*: the root then appears on two sheets and its **uploaded** hours are
  counted twice — the same invariant, broken the other way.

**The ruled fix: split the one mark into two.** The stamp currently does two jobs —
"uploaded hours counted, never again" and "this root is finished, never look again". The
second is what loses the money. Separate them:

- keep `uploaded_reported_at` meaning **only** "uploaded hours have been counted";
- add a second marker meaning "accepted hours have been counted" (a new
  `accepted_reported_at` column is the clean form; deriving it by comparing the tree's max
  `delivered_at` against `uploaded_reported_at` is acceptable if you can bound it to
  counting once);
- a root whose tree delivers after its uploaded-stamp **re-enters a later sheet carrying
  accepted hours only, with uploaded 0**. Uploaded is still counted exactly once, so d3
  holds, and every delivered hour is paid.

**Adnaan has accepted** that some sheet rows will therefore show accepted hours against 0
uploaded hours. That is the intended reading, not a defect.

**Pin it with tests that fail before the fix:** a root stamped while its children are
in flight, whose children then deliver, must have those hours appear on the next sheet
exactly once; and `test_uploaded_hours_conservation_invariant` plus
`test_stalled_cohort_logs_and_understates_attributably` must both still pass unchanged.

---

## 5. RULED — daily sheets go back ON (Adnaan, 2026-08-18)

`CONT_DAILY_REPORTS` ships **True** in the repo (`config.py:192`) and that is correct.
`FLIP_RUNBOOK` step 6c's temporary `False` exists **only** to close one gap: because
`recal_rebuild_reset` nulled every rebuild-era stamp, the first 14:00 IST send would sweep
the entire cohort into one day's sheet via the late-arrival guard, stamp it, misattribute
the hours, and permanently deadlock `recal_regen_sheets`' stray-stamp gate.

**It is a gap-closer, not a policy.** Set it back True immediately after the
`recal_regen_sheets --send` regen completes — `vm_setup.sh` already skips its interlock
once both `.regen-v2-done` markers exist. Do not let any document imply daily sheets are
off by design, and make sure `FLIP_HANDOVER.md` says plainly when they go back on.

Note the interaction: **the moment that flag returns to True is when the §4 bug goes
live.** That is why §4 is first in the work order.

---

## 6. Remaining r-loop 6 findings (confirmed, still unfixed)

All survived 2-vote adversarial refute unless noted. Details in the results JSON (§1).

| Sev | Where | What |
|---|---|---|
| blocker | `pipeline/reports.py:467` | the §4 payment bug — **ruled, fix it** |
| major | `translator/trim.py:59` | `trim()` returns an ABSOLUTE container timestamp as `head_cut_s`, while `V.frame_pts` is relative — every delivered `frames.csv` misplaced by the source `start_time` (1–3 frames). **1 of 2 refuted; judge it yourself**, and note `cutter.py` and `retrim_v2_session.py` already do this correctly |
| minor | `pipeline/validate.py:101` | `"frame_id not zero-based"` maps to a fix that provably cannot touch `frame_id`, burning the whole budget + 3 paid sweeps before a fix-failed reject. **1 of 2 refuted** |
| minor | `tools/analyze_sample.py:1299` | `frame_sync` reported `"OK (≤100ms vs real PTS)"` on six of the eight checker early-returns where the check never ran |
| minor | `translator/v2.py:535` | unhashable `dx_positive`/`dx_negative`/`dy_positive`/`dy_negative` crash qa-v2 into QUARANTINED — the four fields r-loop 2's container-type guard skipped |
| minor | `pipeline/reports.py:573` | the sheet's `## Reject detail` windows on reject-TIME while the CSV columns window on upload-COHORT, so it names rejects it cannot evidence |
| minor | `pipeline/deliver.py:243` | F3-deviation duplicate reject tells the player who uploaded **first** that "only the first upload counts" |

---

## 7. Accepted behaviours — do NOT re-report these

Carry this list into every review iteration or agents will re-litigate settled ground.

1. **r-loop-3 #15** (gate-blanked rows later read as AFK): RULED IGNORED (Adnaan 08-18).
2. The frozen-window **trigger and gate ride the scanner-MEASURED span** (`aux["refined"]`)
   when one exists; the VLM is the classifier, not a boundary-finder. Widening
   `GATE_PAD_FRAMES` instead was explicitly REJECTED. Falling back to the VLM window when
   there is no refined span is intended.
3. `keep_span` (keep-vs-cut) deliberately stays `max(refined, union-with-VLM-window)` —
   r-loop 3's separate fix for a 30 s cutscene of 3–4 s held shots. Not an inconsistency.
4. On a cut-bearing plan the gate is emitted **before** the cut while hygiene/context are
   still short-circuited. Deliberate, and documented in `PIPELINE_ARCHITECTURE.md`.
5. `run()` declines (return 0) when `C.PIPELINE_CONTINUOUS` is True; seven batch-driver
   test modules arm it via an autouse fixture.
6. `CNT_ACTIONS_FEW` / `INP_KEYS_MISSING` downgrade to an advisory when restoring the
   inventory `FIX_GATE_WINDOW` destroyed would clear the bar — so such a session ships
   with fewer than `MIN_DISTINCT_ACTIONS` distinct actions in its delivered rows.
   Accepted. **A defect in the mechanism is still reportable.**
7. Sheet rows showing **accepted hours against 0 uploaded hours** once §4 lands. Intended.
8. R1–R3 (split cascade), R4 (nothing deploys before the flip), R6 (Drive I read-only).

---

## 8. Traps learned the hard way this session

- **"Suite green" must never mean "exit status 0".** Reverting the `install_signals`
  guard makes pytest run 140 of 449 tests, print no summary, and exit 0. No pytest hook
  can catch it (`os._exit` skips them all). Always use `tools/run_suite.sh`.
- **Every new test must be proved to fail before its fix**, in a scratch copy OUTSIDE the
  repo. Two of this session's tests passed against unfixed code — one because the
  successor's lock was stamped (so the old `rmdir` failed for the wrong reason), one
  because it re-ran the SQL instead of the code. A third asserted source text.
- **Test against the real writer, not a hand-built shape.** r-loop 5's entire #11 carve-out
  was dead code for exactly this reason, and its test passed.
- **Reviewers' proposed fixes are not trustworthy.** Two were wrong this session: the
  DISCOVERED age anchor (returns the identical wrong value) and the payment defer (breaks
  d3). Simulate the fix before adopting it.
- **VM sync:** `tar czf - | gcloud compute ssh` **hangs** (observed 73 min at 0.27 s CPU).
  Use `gcloud compute scp` of a tarball, then a separate `ssh` with `< /dev/null`.
- **gcloud auth expires mid-session** and cannot reauth non-interactively. Ask Adnaan to
  run `! gcloud auth login`.
- Re-check **every** module that calls a changed entry point, not just the ones whose
  tests fail — a neutered test passes vacuously and looks fine.

---

## 9. Ground rules (unchanged, and they bind)

- **Verify before claiming. Read whole sources. Mark `[assumption]`.** Grepping is not
  reading. Do not relay a reviewer's number without reproducing it.
- **NEVER push.** Commits path-scoped per green step.
- **Do NOT deploy. Do not touch `hl-recal-rebuild`. Do not stop or start any systemd unit.**
- Drive I (`drive-collect:`) is **read-only forever** (R6).
- Secrets in `~/.config/hl-gamedata/secrets.env` — never print, log or commit.
- **After every multi-agent review, verify your own working tree before committing:**
  `git diff` every hunk, `grep -rn "MUTATION" --include="*.py" .`, `git status` for
  agent-left files.
- Watch for **agents leaving orphaned threads**; `pipeline/tests/conftest.py` has a guard
  that refuses real Drive listings — keep it.
