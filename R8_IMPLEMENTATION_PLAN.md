# R8 Implementation Plan — fix ALL r-loop 13 → iterations 14–15 → e2e → THE FLIP

**Context-optimized 2026-08-19 on Adnaan's order** (original long-form
plan with the C/D/F/r12 fix specifications is preserved in git history
at `b69fee1` and earlier — everything landed is REMOVED here, not
re-stated). This file remains the complete work order: an executor
follows it without re-reading the older evidence base. Where this plan
seems wrong, consult the findings docs (§authority chain below) before
deviating, and say so out loud.

**Authority chain if the plan seems wrong:** `R13_FINDINGS.md` →
`R12_FINDINGS.md` → `R11_FINDINGS.md` → `R10_FINDINGS.md` →
`R9_FINDINGS.md` → `R8_FINDINGS.md` → the older kickoffs. Machine
results incl. refuter verdicts: session scratchpads
`r11-results.json` / `r12-results.json` / `r13-results.json`.

---

## 0. Status ledger — executor updates this section as work lands

**DONE (full detail in git history + findings docs; do not re-derive):**
- r-loop 8 fixes C1–C9 (`c3eab1b..b694456`) · iteration 9 NOT quiet
  (23 confirmed) · D0 ruling C (per-piece payment memory, Adnaan
  2026-08-18) · r-loop 9 fixes D1–D8 (`640651a..81d5f06`).
- Iteration 10 (16 confirmed, ALL fixed in-iteration `6dd2e64`) →
  QUIET. Iteration 11 ran anyway by ruling: **NOT quiet, 20 confirmed
  incl 1 BLOCKER** → fixes F1–F11 (`82e42df..2f5ca04`, floor 670 at
  `09cbf20`), gates green both hosts 674/674.
- Iteration 12 (37 agents): pre-fix NOT quiet, 15 confirmed, 0
  blockers → ALL 15 fixed in-iteration (`986368f..0ad8747`, floor 692
  at `6b87023`) → QUIET after fixing; gates green both hosts 696/696.
- Iteration 13 (31 agents, confirmation pass RE-RULED to run
  regardless): **NOT QUIET — 12 raised → 12 confirmed (9 major /
  3 minor, 0 blockers), 0 killed** (`R13_FINDINGS.md`). STOPPED per
  ruling; Adnaan ruled 2026-08-19: fix all 12 (G1–G9 below) → TWO more
  iterations (14 fix-in-iteration; **15 runs REGARDLESS of 14's
  verdict**; if 15 is not quiet → STOP, hand Adnaan the list) → e2e →
  flip.

**ACTIVE — resume from the first unchecked item:**
- [x] G1 zip-class '' adjudication made durable: supersede breadcrumb +
  CHANGED event + _stamp arms re-keyed (r13 #1/#2/#3) — §3 — LANDED
  `abf052b` (suite 699/699, floor 692; deviations in commit msg:
  marker suffix avoids the prev_md5= token; arm-2 skip uses >=)
- [x] G2 FIX_RETRANSLATE honors the session keybind; override only on
  reroute plans (r13 #4) — §3 — LANDED `2d1b071` (suite 701/701)
- [x] G3 notif/chat edge-vs-mid judged on the probed duration (r13 #5)
  — §3 — LANDED `b383f05` (suite 703/703)
- [x] G4 OverflowError arms: _check_session_json + frame-spacing +
  sweep (r13 #6) — §3 — LANDED `161232f` (suite 709/709; sweep found 3
  extra in-scope sites: video-duration compare, _verify_against_raw
  x2, analyze_sample _num — all fixed in the same commit)
- [x] G5 rebuild-reset stamped-root refusal + paid-piece recording +
  visibility (r13 #8) — §3 (payment-surface: extends ruling C to the
  rebuild tool — flagged to Adnaan in this plan, which he has read) —
  LANDED `dc97f9d` (suite 712/712; r6 accepted-mark test re-keyed:
  uploaded stamp now asserted PRESERVED — accepted-behaviours
  amendment needed in iter14)
- [x] G6 kind-specific pending-interlock diagnosis (r13 #9) — §3 —
  LANDED `792ef61` (suite 716/716)
- [x] G7 fix_actions_from_v2 session_id sanitized + contained (r13 #7)
  — §3 — LANDED `623ab8a` (suite 717/717; sweep fixed the identical
  join in tools/fix_sync_from_v1.py in the same commit)
- [x] G8 tests-only: operator-twin keybind pin + rebuild-reset lock
  refuse/steal/stale pins (r13 #10/#11) — §3 — LANDED `231d4db`
  (suite 721/721; both mutants proven caught vs HEAD scratch)
- [ ] G9 tests-only: split probed-vs-claimed in the r12 #7 edge tests
  (r13 #12) — §3
- [x] Post-G9: sibling-site sweep results recorded (§2 discipline — in
  every G commit message); new SUITE_FLOOR 718 pinned (722 − 4,
  `a5fc1a0`); full gate green Mac AND VM (both 722/722, floor 718;
  VM run 2026-08-19, 442.9s); tree-verify clean (no MUTATION strings,
  no stray edits; pre-existing junk untouched)
- [ ] Review iteration 14 (fix-in-iteration per §4; regressions lane
  targets the G-commits; check usage-credit headroom with Adnaan BEFORE
  launching)
- [ ] Review iteration 15 (confirmation pass, runs REGARDLESS of 14's
  verdict — RULED by Adnaan 2026-08-19, his standing preference after
  revoking the earlier skip-13 shortcut; if 15 is not quiet: STOP, hand
  Adnaan the list)
- [ ] Independent REAL e2e verification (fresh agent, verdict relayed
  VERBATIM)
- [ ] FLIP §5 canary (kill matrix, autoscale, digest; `_pipeline_test/`
  purged)
- [ ] FLIP §6 (stop units → resize → deploy False-interlock → refix
  reset → arm → first hour)
- [ ] FLIP §7 payment endgame (regen preview → send →
  CONT_DAILY_REPORTS=True)
- [ ] FLIP §8 tree verify + deletion (LAST destructive act)
- [ ] Reject-reason table, final independent live verifier, final report

---

## 1. Context capsule (all an executor needs; do not re-derive)

**Project.** `pipeline/` ingests gameplay uploads from Drive I
(read-only forever, R6), validates/fixes/splits them, delivers to
Drive II, and computes payment sheets (hours only, R11). The continuous
driver (`pipeline/continuous.py`) replaces the batch driver
(`pipeline/run.py`, dormant rollback). **Nothing is deployed**;
everything ships at the flip, which THIS work stream executes at the
end (`FLIP_RUNBOOK.md`).

**State.** Suite: **696 collected / 696 green**, floor **692**, via the
arming gate on Mac AND the VM side checkout:

```bash
bash tools/run_suite.sh --with numpy --with opencv-python-headless --with rerun-sdk
```

VM: `hl-pipeline-vm` (asia-south1-a, project `hl-gamedata-pipeline`).
Work in `~/hl-gamedata-continuous-test` ONLY (`~/hl-gamedata` is the
production tree until the flip deploy). Sync recipe (the pipe-over-ssh
form HANGS — never use it); bare instance name for gcloud, the dotted
alias is plain-ssh only:

```bash
git archive HEAD | gzip > <scratchpad>/tree.tgz
gcloud compute scp <scratchpad>/tree.tgz hl-pipeline-vm:/tmp/tree.tgz \
    --zone=asia-south1-a --project=hl-gamedata-pipeline
gcloud compute ssh hl-pipeline-vm --zone=asia-south1-a \
    --project=hl-gamedata-pipeline \
    --command='cd ~/hl-gamedata-continuous-test && tar xzf /tmp/tree.tgz' < /dev/null
```

VM gate: `PATH=$HOME/.local/bin:$PATH SUITE_FLOOR=<floor> bash
tools/run_suite.sh --with numpy==2.4.6 --with
opencv-python-headless==5.0.0.93 --with rerun-sdk==0.36.0`. gcloud auth
expires mid-session and cannot reauth non-interactively — ask Adnaan to
run `! gcloud auth login`.

**Ground rules (bind).** Verify before claiming; read whole sources;
mark `[assumption]`. NEVER push. Commits path-scoped per green step.
Nothing deploys before the flip; no systemd unit touched before then.
Secrets in `~/.config/hl-gamedata/secrets.env` — never print/log/
commit. `pipeline/tests/conftest.py` `_no_real_drive` guard stays.
Suite through `tools/run_suite.sh` on BOTH hosts for anything that
ships. Every new test proved to FAIL against unfixed code in a scratch
copy OUTSIDE the repo (session scratchpad; pre-fix ref for G-fixes:
**`b69fee1`**); pin-only tests use the mutation-proof pattern
(test_r_loop10/11/12.py have examples). After every multi-agent step:
`git diff` every hunk, `grep -rn "MUTATION" --include="*.py" .`,
`git status`. The working tree carries pre-existing UNCOMMITTED junk
(deleted sample dirs, `.gitignore`, `SAMPLE_ANALYSIS_PLAYBOOK.md`) that
predates r8 — leave it, never commit it, never clean it. The 29 open
batch rows in the ledger are the dormant batch driver's rollback
state — never touch them. Drive I read-only forever.

**Key file map.** Findings of record: `R13_FINDINGS.md` (the ACTIVE
work queue's evidence) and earlier `R8..R12_FINDINGS.md`. Review
workflow snapshots: `tools/review/flip-review-iter8..13.js`. Flip
commands: `FLIP_RUNBOOK.md`. Rulings: `FLIP_SESSION_KICKOFF_PROMPT.md`
(R1–R4), `R5_TRIAGE_KICKOFF_PROMPT.md` §7 (pre-registered "quiet"),
`R6_HANDOFF_KICKOFF_PROMPT.md` §4/§5 (payment split,
CONT_DAILY_REPORTS), `R8_HANDOFF_KICKOFF_PROMPT.md` (scope of record).

---

## 2. Hardening discipline (NEW, binding — Adnaan's "no more issues" order)

Every review round's worst findings have been REGRESSIONS from the
previous round's fixes, and the mechanism repeats. These rules bind
every G-fix and everything after:

1. **Sibling-site sweep, recorded.** The F4→r12#5/#8→r13#4 chain (three
   rounds to find three keybind resolvers) and the r12#10→r13#6 chain
   (OverflowError arms found piecemeal) are the same miss: fixing the
   reported site without enumerating the mechanism's OTHER sites.
   Before implementing each G-fix: grep for EVERY writer/reader of the
   state or pattern the fix touches (every writer of `md5_video=''`,
   every keybind resolver, every `int(float(`/bigint arithmetic on
   player input, every `session_id` path join, every lock acquisition),
   list them in the commit message with a verdict per site
   (fixed / already-correct / out-of-scope-because), and fix the ones
   in scope IN THE SAME COMMIT.
2. **Durable events, never transient state.** r13 #2 exists because my
   r12 arm inferred adjudication history from `duration_raw_s IS NULL`
   — a value another fix legitimately refills. Any discriminator that
   answers "did X happen before Y?" must key on a durable events row
   (the codebase's marker pattern), never on current row values.
3. **Discriminator tests split their variables.** r13 #12 exists
   because both r12 #7 tests set probed == claimed. Every test of an
   A-preferred-over-B rule must set A ≠ B in both directions.
4. **Both sides of every guard, plus the hostile mutant.** r13 #11
   exists because the lock test pinned held-during + released-after but
   not refuse-when-live. Every new guard gets: the refuse case, the
   proceed case, and a mutation-proof of the most damaging bypass shape.
5. **New marker events are checked against every event-anchored
   query.** Before adding any events row (G1's CHANGED marker), list
   the queries that aggregate events (F8 stint anchors, reclaim-marker
   filter, worker-death count, _stuck_lines stints, disc_media,
   adoption-bounds) and state per query why the new row cannot perturb
   it. Record the list in the commit message.

## 3. Fix specifications G1–G9 (from R13_FINDINGS.md; vetted 2026-08-19)

Execution order G1 → G9 (G1 money-path first; G8/G9 tests-only last).
Per-commit discipline: implement → fail-first proof in a scratch copy
of `b69fee1` OUTSIDE the repo → Mac gate → path-scoped commit; VM gate
once after G9; locate by SYMBOL, never remembered line numbers. Where a
spec deviates from a finder's proposal, the deviation is stated inline.

### G1 — zip-class '' adjudication made durable (#1≡#3 + #2, all MAJOR; regressions from r12 `986368f`)

Root cause: `md5_video=''` has TWO writers with opposite payment
semantics — the stamp-PRESERVING quarantine heal and the
stamp-CLEARING `ledger.supersede` (ingest's zip-re-upload branch calls
it with `new_md5=""`) — and the r12 `_stamp` arms cannot tell them
apart, while the arm-2 discriminator reads transient row state the F6
backfill erases. Four coordinated changes:

1. **`ledger.supersede`**: when `new_md5` is falsy and the old
   `md5_video` is truthy, append `; prev_md5={old}` to the existing
   `superseded: new md5 ` event detail (verified: the download-time
   deferral's `LIKE '%prev_md5='` + `rsplit("prev_md5=", 1)` parse
   matches this format as-is). The deferral then adjudicates the
   supersede class exactly like the heal class, so a falsely-landed
   stamp SELF-HEALS at download: changed bytes → clear fires, hours
   re-enter via late/uncountable; identical bytes (ctime-only re-zip)
   → the stamp correctly stands. CHECK first that no existing test
   asserts the exact `superseded: new md5 X` detail string.
2. **Download backfill (`ingest.py`, the deferred-clear branch)**: when
   the clear FIRES, also write a durable adjudication marker — a
   same-state audit event (the D3 marker pattern; state at that point
   is DOWNLOADING) with a module-level constant detail, e.g.
   `ZIP_ADJ_CHANGED = "zip-backfill: bytes CHANGED"` plus
   ` (prev_md5={old} -> {local})` for forensics. Per §2 rule 5:
   enumerate every event-anchored query and record why a
   DOWNLOADING→DOWNLOADING row at download-completion time perturbs
   none of them (expected: F8/reclaim anchors key on
   MIN(DOWNLOADING)-within-stint — this event never precedes the
   stint's first claim; worker-death keys on VALIDATING; disc_media
   first_disc keys on DISCOVERED; verify each).
3. **`reports._stamp`**: arm 2 (sid RECORDED as '') drops the
   NULL-duration inference entirely (r13 #2). New rule: skip loudly iff
   a `ZIP_ADJ_CHANGED` event for the sid has `ts > counted_at`; else
   stamp. Arm 4 (CAS miss, row now '') is UNCHANGED — with the
   breadcrumb from (1), both '' writers are deferral-covered and a
   false stamp self-heals (the #3 refuter's own recommendation:
   "Do NOT instead make _stamp skip whenever the row holds ''").
   The real-vs-real loud skip is unchanged.
4. **Threading `counted_at`**:
   `mark_uploads_reported`/`mark_accepted_reported` gain
   `counted_at: str | None = None`; the fresh path passes the exact
   `"at"` string it just wrote into the durable record (capture it in
   a variable — do not re-call now()); the resume passes
   `rec.get("at")` (present in every record since D5b wrote "md5"+"at"
   together). `counted_at=None` (tools/legacy) keeps the unconditional
   arm — document that the two production paths always pass it. The
   resume pre-filter `_bytes_changed` stays real-vs-real only; the
   recorded-'' decision is consolidated in `_stamp` (deviation from
   #2's proposal to also change the pre-filter: one decision site, not
   two).

Tests (fail-first at `b69fee1`): (a) the #1/#3 shape end-to-end — zip
REJECTED root counted (real snapshot md5), `supersede(new_md5="")`
mid-send → stamps land via arm 4, download adjudicates CHANGED → clear
+ marker, new bytes deliver → hours reach exactly one later sheet
loudly; (b) the #2 shape — recorded-'' root, crash before stamps,
deferred clear + F6 backfill in the gap → resume SKIPS loudly, next
sheet counts the new hours once; (c) identical-bytes re-zip supersede →
stamp stands, no re-entry, no double-count (control); (d) all r12
#1/#2 tests stay green (heal class unchanged). Sweep per §2 rule 1:
every writer of `md5_video` (grep `md5_video=`) listed with a verdict.

### G2 — FIX_RETRANSLATE honors the session keybind (#4, MAJOR)

`_dispatch` passes `game_override=game if game in C.GAMES else None`,
and the ledger game is ALWAYS in C.GAMES (ingest scoping) — so the
override branch (built-in keybind) runs on EVERY production
retranslate and the session-keybind branch is dead code there. Fix:
carry the reroute fact in the plan — `plan_fixes` emits
`("FIX_RETRANSLATE", {"rerouted": reroute})` at its single emission
site (verified: one `steps.append(("FIX_RETRANSLATE", ...)` site), and
`_dispatch` passes `game_override=game only when params.get("rerouted")`
(deviation from the finder's apply_fixes-threading proposal: the plan
carries the fact, `_dispatch`'s signature is unchanged, review-2 #5's
reroute ruling is preserved verbatim). Tests: custom-keybind bundle
through the PRODUCTION dispatch path (plan_fixes → apply_fixes,
game='kamla', a has_raw-routed FAIL) → custom-bound presses survive
with their actions; reroute plan → built-in governs (pins review-2 #5);
per §2 rule 1 sweep: grep every `resolve_keybind`/`KEYBINDS[` consumer
and record the verdict table (expected all-correct now: translate v1/v2,
retranslate non-reroute, hygiene, context, operator twin).

### G3 — edge-vs-mid judged on the probed duration (#5, MAJOR)

`_map_flags` classifies notif/chat edge-ness against the CLAIMED
duration (`dur`) while the r12 #7 arms three lines up already use
`dur_true`. Fix: both edge tests (`n["t"] >= dur - 3.0`,
`c["t"] >= dur - 3.0`) use `dur_true` (which already encodes the
claimed-only fallback). Tests per §2 rule 3 — variables split BOTH
ways: claim=300000/probed=300 + confirmed notif and chat at t=298 →
CNT_NOTIF_EDGE + fixable CNT_CHAT_PII (pre-fix: MID/unfixable →
terminal reject); claim=300/probed=300 control unchanged.

### G4 — OverflowError arms completed in the checker (#6, MAJOR)

Per §2 rule 1 this is the r12 #10 sweep done properly: add
OverflowError to `_check_session_json`'s numeric except tuple (the
`duration_ms/1000.0`, `fps*duration_seconds` arithmetic) and guard the
frame-spacing median block (`0.2 * med` on bigint ts) with
try/except OverflowError → typed FAIL (mirror the r12 frame-sync arm's
message shape). THEN run the full sweep: grep every `int(`/`float(`/
arithmetic on player-supplied numerics across translator/ + tools/
analyze_sample.py, list every site with a verdict in the commit
message, and fix any further in-scope site in the same commit. Tests:
parametrized `duration_ms`/`fps`/`frame_count` = 10**400 →
check_session_v2 returns typed FAIL (no raise); all-bigint timestamp
column → typed FAIL.

### G5 — rebuild-reset payment-evidence refusal + memory (#8, MAJOR)

**Payment-surface (flagged to Adnaan here, per the C6/D7 precedent —
this plan is his read of it): extends ruling C's per-piece payment
memory to the rebuild tool.** Mirror the refix sibling's doctrine in
`recal_rebuild_reset._locked_main`:
1. Pre-flight count: in-scope roots carrying `uploaded_reported_at`
   and DELIVERED nodes (root or child) carrying `accepted_reported_at`.
   If any exist and `--allow-reported` is absent → ABORT rc=2 naming
   both counts (refix's exact shape).
2. Under `--allow-reported`: PRESERVE `uploaded_reported_at` through
   the reset (drop it from the root UPDATE's NULL list — the refix
   comment's rationale verbatim: preserved stamps mean nothing is
   double-paid); record a paid piece (`ledger.record_paid_piece`) for
   EVERY accepted-stamped DELIVERED node — root included — BEFORE the
   child DELETE, mirroring refix's recording block; then NULL
   `accepted_reported_at` (the memory now carries the payment fact).
   `tree_sealed_at=NULL` stays (r8 C6).
3. Visibility: the dry-run JSON and the `--yes` summary both print the
   stamped-root / stamped-node / recorded-piece counts.
Tests: stamped cohort without the flag → rc 2, nothing changed; with
the flag → uploaded stamps survive, paid pieces recorded, and after a
simulated re-run re-delivers a same-id/same-seconds piece, the next
sheet skips it via memory (no double-pay) while a genuinely new piece
counts once; dry-run JSON carries the counts.

### G6 — kind-specific pending-interlock diagnosis (#9, minor)

New `reports.pending_daily_send_detail(cfg) -> tuple[day, kind] | None`
with kind ∈ {"daily", "wedged", "regen", "unreadable"} ("wedged" =
record present + `.wedged` present; "unreadable" = the fail-closed
sentinel). `pending_daily_send` becomes a thin wrapper (back-compat —
existing tests keep passing). Both reset tools print kind-specific
why/how: daily → "let the driver finish the resume"; regen → "finish
recal_regen_sheets --send (the driver refuses dailies while this
record exists)"; wedged → "reconcile by hand, then rm
reports/<day>/.wedged"; unreadable → "fix the reports dir and re-run".
Same text in both tools. Tests: one per kind through a real tool
invocation.

### G7 — fix_actions_from_v2 session_id sanitized (#7, minor)

Route `sid` through `translate.safe_session_id(s.get("session_id"),
session_dir)` before the out_dir join and add the same containment
assert the v1/v2 joins carry. Per §2 rule 1: grep every remaining
`session_id`-into-path join across tools/ and record verdicts. Tests:
traversal sid through the tool's fix_session → output inside out_root,
no escape dir.

### G8 — tests-only: operator-twin + lock pins (#10, #11)

(a) `fix_actions_from_v2` keybind pin: load the tool via the `_load`
pattern, build a session dir with `raw/keybind.json` binding a custom
literal, stub `translator.context.classify_video`/`available` exactly
as test_r_loop12's `_context_work` does, drive `fix_session`, assert
the custom action survives; built-in fallback control. (b)
rebuild-reset lock pins per §2 rule 4: live lock (monkeypatch
`pipeline.run._pid_is_pipeline` → True) → `main()` == 2, ledger
untouched, lock dir intact; stale lock (dead pid) → reclaimed,
proceeds, released. Both mutation-proved (the #11 steal-mutant shape
and the #10 revert) against a HEAD scratch copy.

### G9 — tests-only: de-vacuous the r12 #7 edge tests (#12)

Split the variables per §2 rule 3: claimed=500/probed=71 → CNT_SHORT
with post_cut_s == 67.5; claimed=71/probed=200 → fixable
CNT_NOTIF_EDGE. Mutation-proof: the finder's exact `dur`-for-`dur_true`
mutant must fail.

## 4. Review iterations 14 → 15 (multi-agent, ultracode; Adnaan's warrant 2026-08-19)

- Script: copy `tools/review/flip-review-iter13.js` (committed
  snapshot) to the session scratchpad as `flip-review-iter14.js`.
  Retarget the regressions lane at the G-commits (list each with its
  one-line description, as iter13 did for r12); refresh the suite
  numbers and HEAD note; APPEND accepted-behaviour entries 55+ for the
  G rulings (keep ALL existing 1–54; amend 45 where G1 supersedes its
  arm-2 mechanics, 46 where G8 pins land, 50 where G3/G9 complete the
  probed-duration migration) — update the list FIRST or agents
  re-litigate settled ground. NO raw backticks inside the ACCEPTED
  template literal (parse error, bit twice). Invoke via the Workflow
  tool with `scriptPath`. For 15: same again from 14's script.
- Keep: 7 lanes, 2-vote refute (a finding dies only at 2/2), whole-
  codebase + regressions-from-previous-fixes + tests-coverage lane.
  Check usage-credit headroom with Adnaan BEFORE each ~40-agent launch
  (two iteration-11 refuters died on exhaustion).
- Iteration 14: fix EVERY confirmed finding in the same iteration under
  §2 discipline (fail-first, both host gates, path-scoped commits,
  tree-verify after the workflow and before each commit); quiet judged
  AFTER fixing (R5_TRIAGE §7: zero confirmed blockers AND every
  confirmed finding fixed in-iteration with both gates green).
- Iteration 15: runs REGARDLESS of 14's verdict. If 15 is not quiet:
  **STOP — hand Adnaan every verified-but-unfixed finding,
  severity-ordered; do NOT fix, do NOT proceed.** If quiet: proceed to
  §5.
- After each iteration: findings-of-record doc (R14_FINDINGS.md /
  R15_FINDINGS.md, generated from the results JSON — never
  hand-transcribed), workflow snapshot committed to `tools/review/`,
  §0 updated.

## 5. Independent REAL e2e verification

Only after the loop exits clean. A fresh agent that wrote and reviewed
none of this code exercises the actual system, modeled on FLIP_RUNBOOK
§5 (canary shape, Mac-local): fresh HL_PIPELINE_HOME, TEST-mode
Telegram, real VLM calls (bounded spend), real Drive II
`_pipeline_test/` uploads purged via `deliver.cleanup_test_folder`,
local sample bundles as seeds, 3-leg kill -9 matrix. Verdict relayed
VERBATIM; a BLOCKED-with-error never becomes a pass. Prereqs verified
on this Mac: `rclone listremotes` shows drive-collect:/drive-deliver:;
`~/.config/hl-gamedata/secrets.env` has GEMINI+TELEGRAM vars (never
print it).

## 6. THE FLIP (this work stream executes)

Execute `FLIP_RUNBOOK.md` end to end — §5 canary → §6 flip (stop
units → E2→C2D resize with balloon check → `CONT_DAILY_REPORTS=False`
committed → deploy by rsync → §6c verification greps on the VM BEFORE
arming → `recal_refix_reset` dry-run → review JSON (`paid_pieces_to_
record` + `skipped_sealed`; `skipped_mixed`/`sealed_roots` stay `[]`)
→ `--yes` → `vm_setup.sh --enable-continuous` → watch the first hour)
→ §7 payment endgame (driver stopped → `recal_regen_sheets.py` preview
→ sanity-read BOTH sheets → `--send`; final invariant anchor ==
`2026-08-16T05:32:50+00:00` → `CONT_DAILY_REPORTS=True`, commit,
deploy, restart; update `NOTE_FOR_D3.md`) → §8 tree verify + LAST
destructive act → reject-reason table (both baselines, labelled
mixed-methodology) → final independent live verifier (verdict
verbatim) → final report (`FLIP_SESSION_KICKOFF_PROMPT.md` §6 steps
7–9). Destructive gates keep their runbook protections: parachute
backup before reset-class actions, preview before `--send`,
`recal_verify_tree.py` CLEAN before any deletion.

## 7. Reporting

Verdict-first, per phase. The final report to Adnaan surfaces every
observable payment-surface change of this stream: F6 (NULL-duration
roots' accepted hours now paid), F7 + r12 #1/#2 + G1 (compare-and-set
stamps and the '' adjudication chain), G5 (rebuild-reset under ruling
C), and the two C6-era rewritten payment tests. Label every
mixed-methodology comparison as such. Relay verifier verdicts verbatim.
