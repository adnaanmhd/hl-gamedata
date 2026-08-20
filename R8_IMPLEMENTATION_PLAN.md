# R8 Implementation Plan — review stream CLOSED → e2e session → flip session

**Context-optimized 2026-08-20 (fifth pass, on Adnaan's order) —
everything landed lives in git history, not here.** (The M-set detail
is at `8ab9c64` and earlier; the N-set at `0fa07fd`; the O-set at
`a6f5205`; older sets and spec bodies at `06ecd72`/`bb75721` and
earlier.) This file remains the work order of record; the two NEW
sessions each get their own kickoff prompt
(`E2E_KICKOFF_PROMPT.md`, `FLIP_EXEC_KICKOFF_PROMPT.md`) — read those
first in those sessions; this plan is the shared background.

**Authority chain if the plan seems wrong:** `R21_FINDINGS.md` →
`R20_FINDINGS.md` → `R19_FINDINGS.md` → older findings docs → older
kickoffs. Machine results incl. refuter verdicts:
`r19/r20/r21-results.json` in the R19–R21 executor session's
scratchpad (tmp — the findings docs are the durable record).

---

## 0. Status ledger

**DONE (full detail in git history + findings docs; do not re-derive):**
- r-loops 8–18: C/D/F/G/H/I/J/K/L sets all landed and reviewed
  (ledger detail at `06ecd72` and earlier).
- Iteration 19 NOT QUIET (13→13, 1 blocker) → **M1–M8 landed**
  (`a6af11d..1a023fb`, floor 817 `a239fad`), reviewed by iteration 20.
- Iteration 20 NOT QUIET (11→11, 0 blockers; `R20_FINDINGS.md`,
  run `wf_c14f39a5-27d`) → **N1–N5 landed** (`f6fa524..1d82472`,
  floor 836 `f66d3ed`, BOTH host gates 840/836), reviewed by
  iteration 21.
- Iteration 21 NOT QUIET (7→7, 0 blockers, 3 major / 4 minor;
  `R21_FINDINGS.md`, run `wf_54f4e364-8b9`) → **O1–O5 landed**
  (`765105a` O1 `_stamp` accepted-column guard [payment-surface],
  `0b916fa` O3 head-cut gate completed, `4a0b54c` O2
  synthesized-stamp marker + warn-skip + typed retranslate refusal,
  `b2a833c` O4 atomic heal + O5 emit-overflow pin; floor 846
  `a6f5205`; every fix fail-first-proven at `25528f6`, per-commit
  Mac gates 841..850). **The O set is UNREVIEWED by a loop pass —
  the independent e2e is its verification (RULED below).**
- **RULING CHAIN (all Adnaan, 2026-08-20):** (1) iterations 20/21
  launch automatically, fix-in-iteration, no headroom asks; (2)
  superseding the iteration-22 arm — **"continue with the fixes in
  this session"** → O-set lands in the R19–R21 executor session,
  then that session delivers **the go-live VERDICT** (this replaces
  iteration 22 as the checkpoint); (3) if the verdict is GO: a NEW
  session runs the **independent e2e**, then another NEW session
  runs **the flip + deploy + Drive I processing**; (4) the flip
  upgrades the VM to a **c2 instance, 56 CPUs, increased workers**
  (supersedes the 08-16 "permanent e2-standard-32" ruling; only the
  e2 exists as of 08-20 — the resize is a flip-session step).
- **CLEAN-SLATE RULING (Adnaan, 2026-08-20, Q&A on record in the
  executor session):** driver = distrust of pre-hardening
  deliveries. **Drive II is wiped clean FIRST** (all ours, no client
  files, no live client access, parachute EXPLICITLY WAIVED — "wipe
  it"; content re-derivable from Drive I). **No payments have ever
  gone out** → the old ledger, its payment stamps, sheet records and
  the per-piece memory are VOID history: the flip starts a FRESH
  HL_PIPELINE_HOME/ledger with no payment memory carried (the
  payment MECHANISMS in code are unchanged and fully tested — only
  the history resets). The 29-batch-rows "never touch" rule is
  RELEASED; the vanished-folder/duplicate dead rows die with the old
  ledger (whatever is physically in Drive I re-registers fresh).
  Scope = ALL of Drive I, nothing excluded (~1285 fh at the 08-19
  snapshot: 1175 Kamla + 110 OW, 3644 sessions, 209 players, growing
  ~290 h/day — the flip session re-measures). Sequence stands: e2e
  session → flip session. **Aug 24 still BINDS.** **Processing order
  RULED (Adnaan, 08-20 follow-up): Kamla first, oldest-first, until
  the delivery drive holds 500 DELIVERED Kamla hours (ledger SUM of
  duration_delivered_s over DELIVERED kamla nodes) — then Kamla
  processing STOPS** (in-flight finishes, slight overshoot accepted,
  the rest of Kamla stays raw in Drive I); OW continues after the
  stop. OW satellite-camera mapping: mechanism + naming PROPOSED
  08-20 (a `satellite_camera` HUD-template context in
  translator/context.py — the existing template-matching recipe —
  with `satellite_`-prefixed snake_case action names pinned during
  frame verification; strip-to-blank is the fallback if the client's
  action vocabulary is closed); AWAITING Adnaan's + client-vocabulary
  confirmation, lands as its own ruled task.
- Post-O: floor 846 pinned; BOTH host gates at 850/846 — see the
  verdict report for the recorded numbers; tree-verify clean.

**ACTIVE — the remaining sequence:**
- [x] O-set landed + floor + gates + verdict delivered (this session).
- [ ] **E2E session** (new session; `E2E_KICKOFF_PROMPT.md`): the
  independent REAL e2e, fresh executor, verdict relayed VERBATIM to
  Adnaan. A BLOCKED-with-error never becomes a pass. Does NOT flip.
- [ ] **FLIP session** (new session, only after the e2e verdict and
  Adnaan's explicit go; `FLIP_EXEC_KICKOFF_PROMPT.md`): c2-56
  resize → FLIP_RUNBOOK end to end (§5 canary → §6 flip → §7
  payment endgame → §8 verify) → continuous processing of the
  Drive I backlog (~600+ fh) begins → throughput measured and
  re-projected → final report.

---

## 1. Context capsule (all an executor needs; do not re-derive)

**Project.** `pipeline/` ingests gameplay uploads from Drive I
(read-only forever, R6), validates/fixes/splits them, delivers to
Drive II, and computes payment sheets (hours only, R11). The
continuous driver (`pipeline/continuous.py`) replaces the batch
driver (`pipeline/run.py`, dormant rollback). **Nothing is deployed**
until the flip session.

**State.** Suite: **850 collected / 850 green**, floor **846**, via
the arming gate on Mac AND the VM side checkout:

```bash
bash tools/run_suite.sh --with numpy --with opencv-python-headless --with rerun-sdk
```

VM: `hl-pipeline-vm` (asia-south1-a, project `hl-gamedata-pipeline`),
e2-standard-32 today, c2/56-CPU after the flip resize. Work in
`~/hl-gamedata-continuous-test` ONLY (`~/hl-gamedata` is the
production tree until the flip deploy). Sync recipe (the pipe-over-ssh
form HANGS — never use it); bare instance name for gcloud:

```bash
git archive HEAD | gzip > <scratchpad>/tree.tgz
gcloud compute scp <scratchpad>/tree.tgz hl-pipeline-vm:/tmp/tree.tgz \
    --zone=asia-south1-a --project=hl-gamedata-pipeline
gcloud compute ssh hl-pipeline-vm --zone=asia-south1-a \
    --project=hl-gamedata-pipeline \
    --command='cd ~/hl-gamedata-continuous-test && rm -rf pipeline translator tools && tar xzf /tmp/tree.tgz' < /dev/null
```

VM gate: `PATH=$HOME/.local/bin:$PATH SUITE_FLOOR=846 bash
tools/run_suite.sh --with numpy==2.4.6 --with
opencv-python-headless==5.0.0.93 --with rerun-sdk==0.36.0`. gcloud
auth expires mid-session and cannot reauth non-interactively — ask
Adnaan to run `! gcloud auth login`. The Mac gate can exceed 7 min
under load (10-min timeout / background); the VM gate takes ~9 min.

**Ground rules (bind).** Verify before claiming; read whole sources;
mark `[assumption]`. NEVER push. Commits path-scoped per green step.
Secrets in `~/.config/hl-gamedata/secrets.env` — never print/log/
commit. `pipeline/tests/conftest.py` `_no_real_drive` guard stays.
Suite through `tools/run_suite.sh` on BOTH hosts for anything that
ships. New tests prove FAIL against unfixed code in a scratch copy
OUTSIDE the repo; pin-only tests use the mutation-proof pattern
(test_r_loop19/20.py have current examples). After any multi-agent
step: `git diff` every hunk, `grep -rn "MUTATION" --include="*.py"
.`, `git status`. The working tree carries pre-existing UNCOMMITTED
junk (deleted sample dirs, `.gitignore`, `SAMPLE_ANALYSIS_PLAYBOOK.md`)
that predates r8 — leave it, never commit it, never clean it. The 29
open batch rows in the ledger are the dormant batch driver's rollback
state — never touch them. Drive I read-only forever.

**Known-and-NOTED, deliberately unfixed (a note is not a ruling — a
PROVEN concrete harm path is a normal finding):**
translator/sync.py input_track_from_rows' bare float() over dx/dy
cells (shielded by reasons ordering; survived iterations 18–21) and
translator/translate.py reprocess_session's built-ins-only keybind
resolve (CLI-only).

**Key file map.** Findings of record: `R19..R21_FINDINGS.md` (+ the
older `R8..R18`). Review snapshots: `tools/review/flip-review-iter8..21.js`.
Flip commands: `FLIP_RUNBOOK.md`. Rulings:
`FLIP_SESSION_KICKOFF_PROMPT.md` (R1–R4), `R5_TRIAGE_KICKOFF_PROMPT.md`
§7 ("quiet"), `R6_HANDOFF_KICKOFF_PROMPT.md` §4/§5 (payment split,
CONT_DAILY_REPORTS), `R8_HANDOFF_KICKOFF_PROMPT.md` (scope of record).

## 2. Hardening discipline (binding)

1. **Sibling-site sweep, recorded** in the commit message with a
   verdict per site; extends to the TESTS and test COHORTS of every
   swept site (a pin must run where the pinned behavior is live).
2. **Durable events, never transient state** for any
   "did X happen before Y?" discriminator.
3. **Discriminator tests split their variables** (A ≠ B both ways).
4. **Both sides of every guard, plus the hostile mutant.**
5. **New marker events are checked against every event-anchored
   query**, recorded in the commit message.

## 3. (Retired) Fix specifications

All landed — full text in git history (M at `8ab9c64`-, N at
`0fa07fd`-, O at `a6f5205`- and the findings docs).

## 4. (CLOSED) Review iterations

Iterations 8–21 ran; the stream closed on Adnaan's 2026-08-20 ruling
(fixes-then-verdict superseding iteration 22). If a LATER ruling
reopens review, copy `tools/review/flip-review-iter21.js`, retarget
the regressions lane at the newest commits, append accepted-behaviour
entries (96–100 were the last, for the N set; the O set has none
yet), refresh suite numbers, and keep ALL 7 lanes + 2-vote refute.

## 5. E2E session, then FLIP session

**E2E (next session; full brief in `E2E_KICKOFF_PROMPT.md`):** a
fresh executor that wrote and reviewed none of this code exercises
the actual system, modeled on FLIP_RUNBOOK §5 (canary shape,
Mac-local): fresh HL_PIPELINE_HOME, TEST-mode Telegram, real VLM
calls (bounded spend), real Drive II `_pipeline_test/` uploads purged
via `deliver.cleanup_test_folder`, local sample bundles as seeds,
3-leg kill -9 matrix; verdict relayed VERBATIM. e2e prereqs last
verified on this Mac 2026-08-19: `rclone listremotes` shows
drive-collect:/drive-deliver:; `~/.config/hl-gamedata/secrets.env`
has GEMINI+TELEGRAM vars (never print it).

**FLIP (after the e2e verdict + Adnaan's go; full brief in
`FLIP_EXEC_KICKOFF_PROMPT.md`, which carries the CLEAN-SLATE
amendments):** c2-56 resize → Drive I measured → **Drive II wiped
(ruled, parachute waived)** → fresh HL_PIPELINE_HOME/ledger →
FLIP_RUNBOOK §5 canary (measure real min/fh in the first hours and
re-project) → §6 flip → full-drive processing in the confirmed
priority order → fresh-era payment sheets → final report per
`FLIP_SESSION_KICKOFF_PROMPT.md` §6 steps 7–9. The runbook's §7
legacy payment endgame (the `2026-08-16T05:32:50+00:00` anchor) and
§8 legacy-reconciliation acts are SUPERSEDED by the clean slate —
skip and say so. Timeline arithmetic at the 08-19 snapshot (1285 fh):
conservative 8–12 min/fh → ~7–11 days for the full drive; optimistic
6–8 → ~5.5–7 days; **Aug 24 covers roughly 350–550 fh at conservative
rates from an Aug-21 start** — hence the priority order. The Gemini
quota ladder is the likeliest ceiling at 44 workers; watch
429-pressure.

## 6. Reporting — the payment-surface list of record

Verdict-first, per phase. **CLEAN-SLATE NOTE (08-20): no payments
ever went out, and the flip retires the old ledger — the list below
is the record of payment-CODE behavior (all of it ships unchanged
into the new era and stays reportable); the old sheets/stamps/memory
are void history.** Every observable payment-surface change of this
stream (surface at every checkpoint/final report):
- F6 (NULL-duration roots' accepted hours paid), F7 + r12 #1/#2 +
  G1 + H1 (compare-and-set stamps and the '' adjudication chain incl.
  the pre-build counted_at anchor), G5 + H3/H9a (rebuild-reset under
  ruling C), the two C6-era rewritten payment tests, I1 + I2
  (symbol-key/combo binds no longer wrongly rejected), the I7 ruling
  (a vanished folder is permanently dropped; correction = re-upload
  under a NEW name), fix_sync_from_v1 repairs.
- J5 (RULED fail-CLOSED reports-dir listing), J6 (RULED 'Comma'), J2
  (OS-pattern binds), J1 (surrogate-id bundles), K1 (identity-guarded
  quarantined-path heal).
- L1/L2 (junk v1 payloads / corrupt sidecars degrade instead of
  terminal-rejecting — hours reach sheets), reviewed by iteration 19.
- **M4** (the zip-class '' supersede PRESERVES payment stamps; the
  download-time deferral owns byte adjudication) and **M5** (the
  stored fixable field and reject labels truthful), RULED fix-now,
  reviewed by iteration 20.
- **N3** (the '' preserve arms clear the LABELS-only accepted mark on
  non-DELIVERED rows — shipped hours stay payable; cost bounded to a
  re-printed label) and **N4** ('' means unknowable regardless of
  stored md5; real-over-'' adjudicates against the prev_md5
  breadcrumb), directions derived from standing rulings, reviewed by
  iteration 21.
- **O1** (`_stamp` skips the accepted column loudly when a '' writer
  reset the generation mid-window — the N3 class one step
  downstream), direction derived from N3's doctrine, **UNREVIEWED by
  a loop pass — verified by the e2e**.

Also report the QUEUED new-scope item: the OW Observatory
satellite-camera terminal is an unmodelled context (memory
`ow-satellite-camera-context-gap`) — spec'd as a `satellite_camera`
template + label in translator/context.py + frame-verification; the
delivery-vocabulary choice is Adnaan's/the client's; it lands as its
own ruled task. **Flip-session decision (Adnaan): process OW now and
reprocess the action columns when that task lands (recommended;
~1–3 min/fh on OW sessions only), or hold OW until it lands.**
Label every mixed-methodology comparison as such. Relay verifier
verdicts verbatim.
