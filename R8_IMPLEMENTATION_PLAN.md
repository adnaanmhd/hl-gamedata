# R8 Implementation Plan — review iterations 19–20 (RULED 2026-08-20: two more, BOTH run, fix-in-iteration) → CHECKPOINT with Adnaan

**Context-optimized 2026-08-20 (fourth pass, on Adnaan's order) —
everything landed lives in git history, not here** (the I1–I8 spec
bodies and the full I/J/K/L ledger detail are at `bb75721` and
earlier; the H1–H9 spec bodies at `a6affa4` and earlier; the G
material at `9388458` and earlier; the C/D/F/r12 material at
`b69fee1` and earlier). This file remains the complete work order: an
executor follows it without re-reading the older evidence base. Where
this plan seems wrong, consult the findings docs (authority chain
below) before deviating, and say so out loud.

**Authority chain if the plan seems wrong:** `R18_FINDINGS.md` →
`R17_FINDINGS.md` → `R16_FINDINGS.md` → `R15_FINDINGS.md` → older
findings docs → the older kickoffs. Machine results incl. refuter
verdicts: session scratchpads `r11-results.json` … `r18-results.json`
(r16/r17's are in the 85225f0b… session's scratchpad, r18's in the
3a326910… session's scratchpad — tmp, may not survive a reboot; the
findings docs are the durable record).

---

## 0. Status ledger — executor updates this section as work lands

**DONE (full detail in git history + findings docs; do not re-derive):**
- r-loops 8–12: C1–C9, D0 ruling C, D1–D8, iteration-10 set, F1–F11,
  r12's 15 fixed in-iteration (QUIET after fixing at 696).
- Iteration 13 NOT quiet: 12 confirmed → G1–G9 (`abf052b..82f5019`).
- Iteration 14 NOT quiet: 13 confirmed → H1–H9 (`1dd69fa..747422e`),
  JUDGED QUIET AFTER FIXING per R5_TRIAGE §7 (`d16d504`).
- Iteration 15 (CONFIRMATION pass): NOT QUIET — 10 confirmed
  (`R15_FINDINGS.md`). Adnaan ruled 2026-08-19: fix all → iterations
  16–18 (stop at first quiet, fix-in-iteration) → checkpoint.
- I1–I8 ALL LANDED (`bfd96b7..fd3ea1f`; floor 761 `35a0433`; both
  host gates 765/761; I4/I7/I8 carried rulings, all encoded in the
  accepted-behaviours list entries 70–75).
- Iteration 16 NOT QUIET (7 → 7 confirmed, 0 blockers;
  `R16_FINDINGS.md`) → J1–J6 ALL LANDED (`c4f1fda..ddc6da8`; J5
  fail-CLOSED + J6 'Comma' option A RULED by Adnaan 2026-08-19; floor
  778 `cba8fd2`; both host gates 782/778).
- Iteration 17 NOT QUIET (6 → 6 confirmed, 4 major / 2 minor, 0
  blockers; `R17_FINDINGS.md`) → K1–K6 ALL LANDED
  (`c99309e..cdd03cc`; K1 DEVIATION stated in commit + `324ac8b`
  ledger: the heal guard refuses cross-player-AND-no-byte-identity,
  NOT the raw move-heal formula, which breaks two committed pins of
  the designed same-player changed-bytes heal; floor 789 `6f97449`;
  both host gates 793/789).
- Iteration 18 NOT QUIET (6 → 6 confirmed, all major, 0 blockers, 0
  killed; run `wf_e3359c57-a84`, 19 agents, ~2.71M tokens;
  `R18_FINDINGS.md`, snapshot `tools/review/flip-review-iter18.js`).
  Clusters: #1≡#2≡#3≡#5 = fix_v1_to_v2's bare float (the entry-82
  NOTED class, proven by four lanes), #4 pre-existing sidecar defect,
  #6 tests-coverage pin for K2's anchor. → **L1–L3 ALL LANDED,
  UNREVIEWED** (per the 2026-08-19 ruling 18 was the last pass):
  - L1 `e197244` — fix_v1_to_v2 degrades EVERY junk v1 payload read
    (dx/dy via fix_sentinels' _parse semantics with has_motion from
    PARSED values; canonical/trim guards; unusable created_at OMITTED
    → recompute synthesizes; session.json via _read_session_json).
    Fail-first: all five pins fail at `324ac8b` with the exact crash
    classes.
  - L2 `21c983e` — present-but-unusable raw/metadata.json reads as
    no-sidecars (has_raw_sidecars requires a dict parse — the single
    shared decision point for both drivers) so the planner falls back
    to CSV-level fixes; typed FixFailed belt-and-braces at the
    retranslate read.
  - L3 `f57b3ff` — tests-only: K2's game_name=slug anchor pinned live
    (OW ledger slug + degraded canonical + unusable keybind keeps
    W/A/S; the exact game_name=None mutant fails only the new pin).
  - Post-L: floor 798 `74b4a17` (802 − 4); BOTH host gates 802/798
    (Mac 150.6s, VM 524.1s, 2026-08-19); tree-verify clean.
- 2026-08-19 checkpoint report DELIVERED to Adnaan (verdict-first,
  full §6 list, L1–L3 labelled landed-but-unreviewed). **Adnaan ruled
  2026-08-20: run TWO more iterations** — see §4.

**DONE (cont.):**
- Iteration 19 RAN 2026-08-20 (headroom OK'd; FIRST of the two RULED
  extra passes) — **NOT QUIET: 13 raised → 13 confirmed (1 blocker /
  8 major / 4 minor), 0 killed** (run `wf_215a5af0-f51`, 33 agents, 0
  errors, ~3.81M tokens; `R19_FINDINGS.md` + snapshot committed
  `b72984e`; machine results `r19-results.json` in the session
  scratchpad). Clusters (doc numbering): #4≡#6 OverflowError past
  L1's except net; #1 BLOCKER + #10 = L1's stamp/trim degrade arms
  FABRICATE head-offset facts the sidecar verify/retranslate acts on
  (silent delivered desync or wrongful reject); #2/#11/#9 = L2's gate
  (semantic gap / aux drift / encoding-arm coverage); #3 ragged-row
  IndexError; #7/#8/#9 = the r15 #6 arming-gate-invisible class
  inside the L set; #5 PRE-EXISTING zip-supersede double-pay
  (payment-surface); #12/#13 pre-existing degrade-doctrine crashers.

**ACTIVE — resume from the first unchecked item:**
- **M-set, VETTED (Adnaan ruled 2026-08-20 at the pre-implementation
  ask: M4 fix now, M5 fix now); pre-fix ref for all fail-first proofs
  = `06ecd72`; every landed fix fail-first-proven there + per-commit
  Mac gate; iteration 20 reviews the set:**
  - [x] M2 (r19 #2, `a6af11d`, gate 805): has_raw_sidecars ALSO
    requires a parseable recording.started_at_utc (shared module _utc
    — the consumer's own parse); retranslate's non-dict-recording
    AttributeError sibling fixed in the same commit; two usable-
    control test cohorts updated (recorded in the commit).
  - [x] M1 (r19 #1 BLOCKER + #4≡#6 + #10, `cb8cfd4`, gate 810):
    fix_v1_to_v2 stamp/trim resolution runs BEFORE any write and
    never fabricates the head-offset contract. Sidecar-usable route:
    unusable trim → typed FixFailed naming canonical.trim; unusable
    stamp + usable trim → created RECOVERED = started_at + head_cut
    (fixlog note says so). No-sidecar route: junk trim VALUE/shape
    keeps a parseable stamp at head 0.0 (r19 #10); unusable stamp
    keeps omit-and-synthesize. OverflowError joins every guard
    (r19 #4≡#6). Sidecar probe _v1_sidecar_started: M2 usability rule
    at root AND raw/, each file located INDEPENDENTLY (the
    crash-between-moves split). STATED DEVIATION: the finder's
    matching-based head recovery (_seed_shift_record machinery) NOT
    adopted — new machinery would land effectively unreviewed; the
    typed refusal is the finder's own unrecoverable fallback;
    a post-checkpoint enhancement if Adnaan wants it.
  - [x] M3 (r19 #3, `cb42dae`, gate 811): ragged v1 rows pad to the
    header width in fix_v1_to_v2; the other eight _read_csv consumers
    verified shielded (verdicts in the commit).
  - [x] M4 (r19 #5, payment-surface, RULED fix-now, `e9f913b`, gate
    814): ledger.supersede with falsy new_md5 over a real stored md5
    PRESERVES uploaded/accepted stamps + duration_raw_s + tree seal
    (every other reset stays); the download-time deferral owns byte
    adjudication (changed bytes still clear via breadcrumb +
    ZIP_ADJ_CHANGED). Both r13 zip pins green; ordinary-path
    regression + changed-bytes and real-md5 controls added.
  - [x] M5 (r19 #11, RULED fix-now, `6129872`, gate 816): validate
    aux['has_raw'] routes through fix.has_raw_sidecars — the stored
    fixable field and the reject labels become truthful; validated
    end-to-end through a real validate_session run.
  - [x] M6+M7 (r19 #12 + #13, `78e384e`, gate 818): recompute
    degrades on OverflowError (incl. the negative-offset astimezone
    sibling the finder's line missed, guarded in the same try);
    'session.json unreadable' + 'is not a JSON object' map to
    STR_SJ_INVALID (entry-11 semantics preserved); stale rationale
    comment updated.
  - [x] M8 (r19 #7/#8/#9, tests-only, `1a023fb`, gate 821):
    failing-side pins for L1's non-dict-canonical arm, L1's str()
    coercion, and L2's errors='replace' read — the finders' exact
    mutants killed, mutation-proven in a fixed-tree scratch copy.
  - [x] Post-M: floor 817 pinned `a239fad` (821 − 4) in run_suite.sh
    + FLIP_RUNBOOK §6b; BOTH host gates 821/817 (Mac 158.7s, VM
    566.9s, 2026-08-20); tree-verify clean (MUTATION grep clean,
    uncommitted diff = the pre-existing junk only).
- [x] Review iteration 20 RAN 2026-08-20 (automatic per the NEW
  RULING below) — **NOT QUIET: 11 raised → 11 confirmed (0 blockers
  / 8 major / 3 minor), 0 killed** (run `wf_c14f39a5-27d`, 29
  agents, 0 errors, ~3.79M tokens; `R20_FINDINGS.md` + snapshot
  committed `5524563`; machine results `r20-results.json` in the
  session scratchpad). Clusters (doc numbering): #1/#5/#10/#11 =
  M1's stamp/trim resolution block (falsy or-0.0 bypass, destroyed-
  evidence head-0 on the live route, unguarded emit, overflow
  misattribution); #2≡#6 + #3/#7 = M4's supersede semantics
  (payment-surface: stranded delivered hours via the preserved
  labels-mark; ''-over-'' self-defeat + real-over-'' ignoring the
  breadcrumb); #4/#9 = M6 overflow holes; #8 = arming-gate-invisible
  class inside M1's new probe.
- **NEW RULING (Adnaan, 2026-08-20, post-M-set message — supersedes
  BOTH the mid-M-set amendment above and the per-launch headroom
  asks; his words: "run itr 20 automatically", "do NOT ask for token
  headroom"):** iteration 20 launches automatically; if NOT quiet →
  implement ALL its confirmed fixes (N-set, full §2 discipline,
  fail-first proofs, floor re-pin, both host gates, tree-verify) and
  launch iteration 21 automatically; if 21 NOT quiet → implement ALL
  its fixes (O-set, same discipline) and launch iteration 22
  automatically; **iteration 22's results are shown to Adnaan as THE
  CHECKPOINT** (findings doc + snapshot committed; no fix
  implementation for 22's findings without his go). **If 20 or 21
  comes back QUIET (zero confirmed findings) → automatically launch
  the independent e2e** (§5's fresh-agent Mac-local canary shape) —
  this is the explicit go §5 was waiting for, for the e2e ONLY. THE
  FLIP still waits for its own explicit go. NOTE on the standing
  payment-surface consult: N3/N4 below are payment-surface fixes
  implemented WITHOUT a fresh ask under "implement all the fixes" —
  their directions are derivable from standing rulings (count-once
  conservation, the refix tool's ruled labels-mark doctrine, the M4
  ruling's own money-safe intent, deferral-owns-bytes), no NEW money
  policy is being chosen; both are flagged for Adnaan's review at
  the checkpoint and iteration 21 reviews them.
- **N-set, vetted from R20_FINDINGS.md (pre-fix ref for all
  fail-first proofs = `5524563`, code HEAD `a239fad`; every fix
  fail-first-proven there + per-commit Mac gate; iteration 21
  reviews the set):**
  - [x] N1 (r20 #1 + #5 + #10 + #11, `f6fa524`, gate 830): fix_v1_to_v2's
    stamp/trim resolution COMPLETED — falsy/bool head_cut_s is junk
    (raw-value parse, no `or 0.0`; only a genuinely ABSENT key is the
    v1-optional head-0 shape); destroyed trim evidence (unreadable/
    non-dict session.json or canonical) REFUSES typed on the
    live-sidecar route (genuine absent-trim on a readable well-formed
    canonical keeps head 0); the created_at emit resolves INSIDE the
    pre-write resolution block under an OverflowError guard on both
    routes; a stamp+head_cut overflow is disambiguated (head's own
    timedelta constructs → the STAMP is junk → recover/omit; else the
    canonical.trim refusal stands).
  - [x] N2 (r20 #4 + #9, `002004f`, gate 832): recompute's ended emit
    joins the guard on both arms; the except arm's re-addition is
    guarded (still-overflowing duration → ended = created).
  - [x] N3 (r20 #2≡#6, payment-surface, `dde54ab`, gate 835): BOTH ''
    preserve arms (supersede + heal) clear accepted_reported_at when
    the row is not DELIVERED (labels-only mark; money marks stay
    preserved); r9's heal pin re-modeled with intent preserved
    (cohort update recorded in the commit). Direction derived from
    standing rulings; FLAGGED for checkpoint review.
  - [x] N4 (r20 #3 + #7, payment-surface, `44e72b3`, gate 838):
    zip_unknowable = not new_md5 (''-over-'' preserves; breadcrumb
    only over a real prior md5); real-over-'' adjudicates against the
    newest prev_md5 breadcrumb via the new shared
    ledger.latest_prev_md5 (equal → preserve; different/none → full
    clear); the heal's clears computation runs the same adjudication.
    Direction derived from standing rulings; FLAGGED for checkpoint
    review.
  - [x] N5 (r20 #8, tests-only, `1d82472`, gate 840): failing-side
    pins for _v1_sidecar_started's errors='replace' read and its
    OSError/JSONDecodeError degrade — the finders' exact mutants
    killed site-isolated in a fixed-tree scratch copy.
  - [x] Post-N: floor 836 pinned `f66d3ed` (840 − 4) in run_suite.sh
    + FLIP_RUNBOOK §6b; Mac gate 840/836 GREEN (2026-08-20);
    tree-verify clean. **STATED DEVIATION: the VM gate is PENDING —
    gcloud auth expired mid-session and cannot reauth
    non-interactively (the known condition; Adnaan asked to run
    `! gcloud auth login`). Iteration 21 launches on the Mac gate
    alone (it reviews the git tree, not the VM); the VM gate runs the
    moment auth returns and MUST be green before any O-set fix lands
    or the checkpoint claims both-hosts green.**
- [x] Iteration 21 LAUNCHED automatically (per the NEW RULING;
  script: committed iter20 snapshot retargeted at N1-N5 + floor,
  accepted entries 96-100 appended, 17/90/92/94 amended, suite
  numbers 840/836; 7 lanes, 2-vote refute). Results processing, then
  the ruled branch: not quiet → O-set fixed in-iteration → iteration
  22 = THE CHECKPOINT; quiet → the independent e2e launches.

---

## 1. Context capsule (all an executor needs; do not re-derive)

**Project.** `pipeline/` ingests gameplay uploads from Drive I
(read-only forever, R6), validates/fixes/splits them, delivers to
Drive II, and computes payment sheets (hours only, R11). The continuous
driver (`pipeline/continuous.py`) replaces the batch driver
(`pipeline/run.py`, dormant rollback). **Nothing is deployed**;
everything ships at the flip, which runs in a LATER session after
Adnaan's checkpoint go (`FLIP_RUNBOOK.md`).

**State.** Suite: **802 collected / 802 green**, floor **798**, via the
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
    --command='cd ~/hl-gamedata-continuous-test && rm -rf pipeline translator tools && tar xzf /tmp/tree.tgz' < /dev/null
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
copy OUTSIDE the repo (session scratchpad; pre-fix ref for M-fixes =
HEAD at session start — verify with `git log --oneline` that only
docs commits sit on top of code HEAD `f57b3ff`); pin-only tests use
the mutation-proof pattern with the finders' EXACT mutants from the
findings doc (test_r_loop15/16/17/18.py have current examples). After
every multi-agent step: `git diff` every hunk, `grep -rn "MUTATION"
--include="*.py" .`, `git status`. The working tree carries
pre-existing UNCOMMITTED junk (deleted sample dirs, `.gitignore`,
`SAMPLE_ANALYSIS_PLAYBOOK.md`) that predates r8 — leave it, never
commit it, never clean it. The 29 open batch rows in the ledger are
the dormant batch driver's rollback state — never touch them. Drive I
read-only forever.

**Known-and-NOTED, deliberately unfixed (a note is not a ruling — a
finder that PROVES a concrete harm path reports it as a normal
finding):** translator/sync.py input_track_from_rows' bare float()
over dx/dy cells (shielded today only by reasons ordering; noted at
K3, survived iteration 18 with no proven harm) and
translator/translate.py reprocess_session's built-ins-only keybind
resolve (CLI-only, outside the pipeline fix family; noted at K2).

**Key file map.** Findings of record: `R18_FINDINGS.md` (the L set's
evidence) and earlier `R8..R17_FINDINGS.md`. Review workflow
snapshots: `tools/review/flip-review-iter8..18.js`. Flip commands:
`FLIP_RUNBOOK.md`. Rulings: `FLIP_SESSION_KICKOFF_PROMPT.md` (R1–R4),
`R5_TRIAGE_KICKOFF_PROMPT.md` §7 (pre-registered "quiet"),
`R6_HANDOFF_KICKOFF_PROMPT.md` §4/§5 (payment split,
CONT_DAILY_REPORTS), `R8_HANDOFF_KICKOFF_PROMPT.md` (scope of record).

---

## 2. Hardening discipline (binding — Adnaan's "no more issues" order)

Every review round's worst findings have been REGRESSIONS from the
previous round's fixes, and the mechanism repeats. These rules bind
every fix and everything after:

1. **Sibling-site sweep, recorded.** Before implementing each fix:
   grep for EVERY writer/reader of the state or pattern the fix touches,
   list them in the commit message with a verdict per site
   (fixed / already-correct / out-of-scope-because), and fix the ones
   in scope IN THE SAME COMMIT. The sweep discipline extends to the
   TESTS of every swept site — AND to the test COHORTS: r15 #6 exists
   because every H2 test used kamla, where the swept consumers are
   no-ops. A pin must run where the pinned behavior is live.
2. **Durable events, never transient state.** Any discriminator that
   answers "did X happen before Y?" must key on a durable events row,
   never on current row values.
3. **Discriminator tests split their variables.** Every test of an
   A-preferred-over-B rule must set A ≠ B in both directions.
4. **Both sides of every guard, plus the hostile mutant.** Every new
   guard gets: the refuse case, the proceed case, and a mutation-proof
   of the most damaging bypass shape.
5. **New marker events are checked against every event-anchored
   query.** List the queries that aggregate events and state per query
   why the new row cannot perturb it. Record the list in the commit
   message.

## 3. (Retired) Fix specifications

The I1–I8 spec bodies that lived here are LANDED — full text at
`bb75721` and earlier in git history. New fix specs (M-set / N-set)
are vetted from the current iteration's findings doc into §0 as they
arise, deviations stated inline — the K/L pattern.

## 4. Review iterations 20–22 (multi-agent, ultracode; Adnaan's rulings 2026-08-20)

- **Run rule (NEW RULING, Adnaan 2026-08-20 post-M-set, superseding
  the mid-M-set stop-after-20 amendment AND the per-launch headroom
  asks — "run itr 20 automatically … do NOT ask for token
  headroom"):** 20 launches automatically after the post-M gates; if
  20 NOT quiet → fix its confirmed set (N) in-iteration and launch
  21 automatically; if 21 NOT quiet → fix its set (O) and launch 22
  automatically; 22's results are THE CHECKPOINT (shown to Adnaan,
  no 22-fix implementation without his go). **If 20 or 21 comes back
  QUIET (zero confirmed findings) → launch the independent e2e
  automatically** (§5 shape; e2e only — the flip still waits).
  Quiet-after-fixing stays as pre-registered in R5_TRIAGE §7 for
  reporting, but the branch condition here is Adnaan's own "comes
  back quiet" = zero confirmed findings from the pass.
- **Not-quiet handling: fix in-iteration** — the executor vets fix
  specs from the findings (deviations stated), implements with the
  full §2 discipline (fail-first, sweeps, gates), and the next
  iteration reviews those fixes. Payment-surface changes and
  anything contradicting a standing ruling are surfaced to Adnaan
  BEFORE implementing (standing rule, not revoked by the automation
  ruling).
- Script per iteration: copy the PREVIOUS committed snapshot
  (`tools/review/flip-review-iter18.js` for iteration 19) to the
  session scratchpad as `flip-review-iter<N>.js`. Retarget the
  regressions lane at the newest fix commits (one-line description
  each + per-commit attack notes, the iter17/18 pattern — for
  iteration 19: L1 `e197244`, L2 `21c983e`, L3 `f57b3ff`, floor
  `74b4a17`; the L set landed UNREVIEWED, treat it as the prime
  target); refresh the Find preamble's HEAD note, suite numbers and
  floor; frame the iteration honestly (19 = first of the two RULED
  extra passes, reviewing unreviewed fixes; 20 = the LAST pass
  before the checkpoint, its fixes land unreviewed); APPEND
  accepted-behaviour entries 86+ (for iteration 19: 86 L1, 87 L2,
  88 L3) and AMEND where the fixes supersede or complete earlier
  entries (for 19: entry 82's fix_v1_to_v2 NOTED site is CLOSED by
  L1 while sync.py stays NOTED; entry 81 completed by L1's
  whole-function degrade + L3's live pin) — update the list FIRST or
  agents re-litigate settled ground. NO raw backticks inside the
  ACCEPTED template literal (parse error, bit twice); sanity-check
  lane count (7), backtick parity and stale iteration markers before
  launching. Invoke via the Workflow tool with `scriptPath`.
- Keep: **ALL 7 lanes**, 2-vote refute (a finding dies only at 2/2),
  whole-codebase + regressions + tests-coverage. The per-launch
  headroom ask is REVOKED (NEW RULING above — launches are
  automatic; recent iterations: 19–33 agents, ~2.7–3.8M subagent
  tokens).
- A Workflow launched by a session DIES if that session restarts —
  relaunch with `resumeFromRunId` (completed agents return cached) and
  VERIFY via the run's journal.jsonl whether the cache applied.
- After each iteration: findings-of-record doc (`R<N>_FINDINGS.md`,
  generated from the results JSON — never hand-transcribed; save
  `r<N>-results.json` to the session scratchpad), workflow snapshot
  committed to `tools/review/`, §0 updated.

## 5. CHECKPOINT — then (later, on Adnaan's go) e2e and THE FLIP

**AMENDED by the 2026-08-20 NEW RULING (§4): the e2e's explicit go
is GIVEN, conditionally — it launches automatically if iteration 20
or 21 comes back quiet (zero confirmed findings). Otherwise the
checkpoint is iteration 22's results, reported verdict-first with
the §6 payment-surface list. THE FLIP still waits for its own
explicit go — nothing below past the e2e runs in this session.**

For the e2e (now conditionally armed, and for the LATER flip
session): the
independent REAL e2e is a fresh agent that wrote and reviewed none of
this code exercising the actual system, modeled on FLIP_RUNBOOK §5
(canary shape, Mac-local): fresh HL_PIPELINE_HOME, TEST-mode Telegram,
real VLM calls (bounded spend), real Drive II `_pipeline_test/`
uploads purged via `deliver.cleanup_test_folder`, local sample bundles
as seeds, 3-leg kill -9 matrix; verdict relayed VERBATIM — a
BLOCKED-with-error never becomes a pass. Then THE FLIP per
`FLIP_RUNBOOK.md` end to end (§5 canary → §6 flip → §7 payment
endgame with the final invariant anchor `2026-08-16T05:32:50+00:00` →
§8 tree verify + LAST destructive act → reject-reason table → final
independent live verifier → final report per
`FLIP_SESSION_KICKOFF_PROMPT.md` §6 steps 7–9), destructive gates
intact (parachute before reset-class actions, preview before
`--send`, `recal_verify_tree.py` CLEAN before any deletion). e2e
prereqs last verified on this Mac 2026-08-19: `rclone listremotes`
shows drive-collect:/drive-deliver:;
`~/.config/hl-gamedata/secrets.env` has GEMINI+TELEGRAM vars (never
print it).

## 6. Reporting

Verdict-first, per phase. The checkpoint report (and later the final
report) surfaces every observable payment-surface change of this
stream: F6 (NULL-duration roots' accepted hours now paid), F7 + r12
#1/#2 + G1 + H1 (the compare-and-set stamps and the '' adjudication
chain incl. the pre-build counted_at anchor), G5 + H3/H9a
(rebuild-reset under ruling C: split-artifact discard, depth-2 memory
keying), the two C6-era rewritten payment tests, **I1 + I2 (players
with symbol-key or combo binds are no longer wrongly rejected — their
hours now reach sheets), the I7 ruling (a vanished folder is
permanently dropped from intake; the correction is a re-upload under
a NEW folder name — sessions restored under the same name are
deliberately never processed or paid), and the fix_sync_from_v1
repairs (resolve_actions crash + portable copy).**

**r16/r17 additions to the same list:** J5 (RULED fail-CLOSED: a
transient reports-dir listing failure can no longer double-pay a
pending day's hours onto two sent sheets; worst case the daily sheet
is one 600s tick late), J6 (RULED option A: comma-bind players are no
longer wrongly rejected — their presses ship as 'Comma' and their
hours reach sheets), J2 (players binding OS-pattern keys — insert,
caps_lock, F-keys — are no longer wrongly rejected), J1 (surrogate-id
bundles now translate under the folder-name fallback instead of
looping as host-blamed crashes), and K1 (the quarantined-path heal
regains the r5 #41 identity guard: a session folder moved into
another player's tree can no longer flip payment attribution in two
scans; byte-identical moves and same-player renames still heal).

**r18 additions to the same list (UNREVIEWED until iteration 19
passes them — label their review status as of report time):** L1
(v1-format uploads carrying junk dx/dy cells, an unusable created_at
stamp/trim block, or a corrupt session.json are no longer wrongly
terminally rejected — the conversion degrades per the settled house
semantics and their hours reach sheets; an all-junk motion column
ships the blank no-capture form, never a fabricated all-zero track)
and L2 (sessions whose raw/metadata.json arrives corrupt are no
longer made WORSE by their own sidecars — the planner falls back to
the CSV-level repairs that deliver them, so those hours reach sheets
too). Any M/N-set payment-surface change joins this list as it lands.

Also report the QUEUED new-scope item promised to Adnaan 2026-08-19:
the OW Observatory satellite-camera terminal is an unmodelled context
(memory `ow-satellite-camera-context-gap`) — spec'd as a
`satellite_camera` template + label in translator/context.py +
frame-verification; delivery-vocabulary choice (strip vs new snapshot
semantics) is Adnaan's/the client's; it lands as its own ruled task
AFTER the checkpoint go, not in this stream. Label every
mixed-methodology comparison as such. Relay verifier verdicts
verbatim.
