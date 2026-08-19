# R8 Implementation Plan — fix ALL r-loop 15 (I1–I8) → iterations 16–18 (stop at first quiet, fix-in-iteration) → CHECKPOINT with Adnaan

**Context-optimized 2026-08-19 (third pass, on Adnaan's order) —
everything landed lives in git history, not here** (the H1–H9 spec
bodies are at `a6affa4` and earlier; the G material at `9388458` and
earlier; the C/D/F/r12 material at `b69fee1` and earlier). This file
remains the complete work order: an executor follows it without
re-reading the older evidence base. Where this plan seems wrong,
consult the findings docs (authority chain below) before deviating,
and say so out loud.

**Authority chain if the plan seems wrong:** `R15_FINDINGS.md` →
`R14_FINDINGS.md` → `R13_FINDINGS.md` → older findings docs → the
older kickoffs. Machine results incl. refuter verdicts: session
scratchpads `r11-results.json` … `r15-results.json` (r15's is in the
2026-08-19 executor session's scratchpad).

---

## 0. Status ledger — executor updates this section as work lands

**DONE (full detail in git history + findings docs; do not re-derive):**
- r-loops 8–12: C1–C9, D0 ruling C, D1–D8, iteration-10 set, F1–F11,
  r12's 15 fixed in-iteration (QUIET after fixing at 696).
- Iteration 13 NOT quiet: 12 confirmed → G1–G9 landed
  (`abf052b..82f5019`).
- Iteration 14 NOT quiet pre-fix: 13 → 13 confirmed (0 blockers) →
  **H1–H9 ALL LANDED** (`1dd69fa..747422e`; floor 745 pinned
  `37d7d88`; both host gates 749/749; every fix fail-first-proven at
  `5f7015b`; H9 pins proven on the finders' exact mutants; sweeps in
  the commit messages). Two stated deviations, both recorded and
  queued for the final report: the fix_sync_from_v1
  resolve_actions-unpack crash (fixed in the H9 commit) and its
  `cp -c` macOS-only copy (fixed as I8 below, RULED). **Iteration 14
  JUDGED QUIET AFTER FIXING per R5_TRIAGE §7** (`d16d504`).
- Iteration 15 (CONFIRMATION pass, run `wf_0098c165-80b`, 27 agents,
  0 errors, ~3.23M subagent tokens): **NOT QUIET — 10 raised → 10
  confirmed (6 major / 4 minor, 0 blockers), 0 killed**
  (`R15_FINDINGS.md`, snapshot `tools/review/flip-review-iter15.js`).
  Clusters: #1≡#2≡#3≡#10 (H5 same-path dead end), #6/#9 (pins for
  H2/H6 halves), #4/#5/#7/#8 pre-existing. Executor STOPPED per the
  ruling and handed Adnaan the list; **Adnaan ruled 2026-08-19** (all
  rulings encoded in §3 below) and ordered: fix all → up to three
  more iterations (16–18, stop at first quiet, fix-in-iteration) →
  STOP and checkpoint with him BEFORE the e2e.

**ACTIVE — resume from the first unchecked item:**
- [x] I1 qa-v2 exempts caseless key tokens (r15 #4, MAJOR) — LANDED
  `bfd96b7` (fail-first at ce26148; Mac gate 752 passed)
- [x] I2 writer strips action-less combo halves (r15 #5, MAJOR) —
  LANDED `348c93d` (fail-first at ce26148; hostile mutant killed; Mac
  gate 755 passed)
- [x] I3 tests-only: H2 slug-half OW discriminator pins (r15 #6,
  MAJOR) — LANDED `843a2ec` (exact-mutant proof; Mac gate 757)
- [x] I4 fix_v1_to_v2 naive-timezone guard (r15 #7, minor) — LANDED
  `ee35d3f` (fail-first at ce26148, in-test TZ forcing; sweep FOUND +
  fixed the unguarded retrim_v2_session sibling; Mac gate 759)
- [x] I5 safe_session_id length bound (r15 #8, minor) — LANDED
  `7169922` (fail-first at ce26148; Mac gate 761)
- [x] I6 tests-only: H6 composition tail-arm + chat-head pins
  (r15 #9, minor) — LANDED `a93847d` (exact mutant + head-arm mutant
  both killed, split both ways; Mac gate 763)
- [x] I7 H5 ruling: gone-is-gone + rename coaching line
  (r15 #1≡#2≡#3≡#10, RULED) — LANDED `f3f131e` (coaching string
  fail-first at ce26148; same-path ruling pin; rule-5 query sweep in
  the commit; Mac gate 765)
- [x] I8 fix_sync_from_v1 portable delivery copy (RULED) — LANDED
  `fd3ea1f` (H9c twin unstubbed; fail-first = the on-record
  pre-82c86da VM CalledProcessError; Mac gate 765)
- [x] Post-I8: sweep results recorded per commit (§2); SUITE_FLOOR
  761 pinned `35a0433` (765 passed − 4, run_suite.sh + FLIP_RUNBOOK
  §6b); BOTH host gates green — Mac 765/761, VM 765/761 (509.5s,
  2026-08-19; the unstubbed I8 twin passed on Linux, the I4 naive-tz
  pin passed on the non-IST host); tree-verify clean (no MUTATION
  markers, only plan-ledger edit + pre-existing junk)
- [x] Review iteration 16 RAN (headroom OK'd by Adnaan; run
  `wf_ebb4ece9-f00`, 21 agents, 0 errors, ~2.70M subagent tokens):
  **NOT QUIET pre-fix — 7 raised → 7 confirmed (3 major / 4 minor, 0
  blockers), 0 killed** (`R16_FINDINGS.md`, snapshot
  `tools/review/flip-review-iter16.js`, machine results
  `r16-results.json` in the session scratchpad). Clusters: #1≡#4 (I5
  lone-surrogate hole), #6/#7 (pins for I2/I4 sweep halves), #2/#3/#5
  pre-existing. Fix-in-iteration J-set (pre-fix ref `4dc37b4`):
  - [x] J1 = #1≡#4 safe_session_id rejects unencodable ids — LANDED
    `c4f1fda` (fail-first at 4dc37b4; Mac gate 767)
  - [x] J2 = #3 INP_OSKEYS trigger made bound-aware — LANDED
    `c0d37de` (fail-first at 4dc37b4 incl. the e2e wiring pin;
    over-filter mutant killed)
  - [x] J3 = #6 tests-only: fix_sync remap credited-strip pin —
    LANDED `c0d37de` (finder's exact revert mutant killed)
  - [x] J4 = #7 tests-only: retrim naive-guard pin — LANDED
    `c0d37de` (exact guard-deletion mutant killed; Mac gate 776)
  - [x] J5 = #2 daily resume scan fails CLOSED — **RULED (Adnaan
    2026-08-19: fail CLOSED)**, LANDED `eaaee0d` (fail-first at
    c0d37de: the flaky tick provably built, sent AND stamped;
    asymmetric-transient repro at the helper seam; first-ever-send
    control)
  - [x] J6 = #5 comma key — **RULED (Adnaan 2026-08-19: option A,
    'Comma' named display token)**, LANDED `ddc6da8` (fail-first at
    eaaee0d; one-attempt hygiene repair of foreign bare-',' cells;
    glued-token control; sweep across every display consumer)
  - [x] Post-J: SUITE_FLOOR 778 pinned `cba8fd2` (782 passed − 4);
    BOTH host gates green — Mac 782/778 (148s), VM 782/778 (515.6s,
    2026-08-19); tree-verify clean (no MUTATION markers, only
    plan-ledger edit + pre-existing junk)
- [x] Review iteration 17 RAN (headroom OK'd by Adnaan; run
  `wf_7f33bc0c-52c`, 19 agents, 0 errors, ~2.58M subagent tokens):
  **NOT QUIET pre-fix — 6 raised → 6 confirmed (4 major / 2 minor, 0
  blockers), 0 killed** (`R17_FINDINGS.md`, snapshot
  `tools/review/flip-review-iter17.js`, machine results
  `r17-results.json` in the session scratchpad). Fix-in-iteration
  K-set (pre-fix ref `7ad7b71`; all six vetted under standing rules —
  K1 RESTORES the r5 #41 identity guard the H5 arm bypassed, entry 70
  untouched; K2 = F4 doctrine instance 4; K3 = degrade-never-crash;
  K4–K6 tests-only; K1 flagged for the §6 payment-surface list):
  - [x] K1 = #1 quarantined-path heal gains the identity guard for
    rows with a real prior registration (major) — LANDED `c99309e`
    (fail-first at 7ad7b71; '' == '' hostile mutant killed; Mac gate
    785). DEVIATION (stated in the commit): the guard refuses exactly
    the r5 #41 class — cross-player AND no byte identity — NOT the
    raw move-heal formula the spec transcribed, whose md5 arm also
    refuses SAME-player different-md5 heals; that is the heal's
    DESIGNED population (review-r3 #7 correction, bytes can differ
    per review-r4 #7, stamps clear as new hours per r9 D6) and is
    pinned by two committed tests the literal formula breaks
    (test_quarantine_heal_clears_the_tree_seal,
    test_quarantine_heal_clears_the_accepted_mark) — contradicting
    the same spec's own 'same-player re-uploads at a new path still
    heal'. Every K1-mandated case behaves as specified. Two
    accidental cross-player test SEEDS aligned with their tests' own
    intent (review-r4 wipe test → player="" INT_PATH population;
    review-r5 reset test → p1@x.com, its scenario is an operator
    rename and its assertion already hard-coded the p1 path).
  - [x] K2 = #2 fix_v1_to_v2 resolves the session's own keybind
    (major) — LANDED `bd94829` (fail-first at 7ad7b71 incl. the
    raw/-location re-entrant arm; Mac gate 788; recorded deviation
    upheld: the key_binding.json fallback arm NOT adopted;
    reprocess_session's built-ins-only resolve NOTED for iteration
    18's lanes — CLI-only, outside the pipeline fix family)
  - [x] K3 = #3 apply_context_to_rows' _active degrades on junk cells
    (major) — LANDED `22614ef` (fail-first at 7ad7b71 with the exact
    '1,5' ValueError, unit + fix_actions_context route; Mac gate 790;
    recorded deviation upheld: no plan reorder; sync.py's bare float
    AND fix_v1_to_v2's dx/dy float(dx or 0) — same class on the
    ARR_V1_FORMAT route, found in the K3 sweep — both NOTED for
    iteration 18's lanes)
  - [x] K4 = #4 tests-only: J2 camel-token discriminator (CapsLock)
    — LANDED `3f4f917` (map-level + caps_lock bound in the e2e; the
    finder's exact t.lower() mutant killed by BOTH pins; Mac gate
    791)
  - [x] K5 = #5 tests-only: J3 overlap-frame pin — LANDED `000d87a`
    (['w','e'] frame added to the same remap call; the finder's exact
    row-level mutant killed; Mac gate 791)
  - [x] K6 = #6 tests-only: I7 coached rename-re-upload path pinned
    at BOTH dedupe sites (scan-time + the download-time twin, adopted
    as cheap via the r_loop10 fake-rclone idiom) — LANDED `cdd03cc`
    (both exact mutants killed, site-isolated; Mac gate 793)
  - [x] Post-K: SUITE_FLOOR 789 pinned `6f97449` (793 passed − 4,
    run_suite.sh + FLIP_RUNBOOK §6b); BOTH host gates green — Mac
    793/789 (148.3s), VM 793/789 (517.9s, 2026-08-19); tree-verify
    clean (no MUTATION markers, only pre-existing junk) →
    iteration 18 (headroom check with Adnaan first)
- [ ] Review iteration 18 (ONLY if 17 was not quiet; same rules; if 18
  is ALSO not quiet: fix its confirmed set, then STOP — report the
  fixes as landed-but-unreviewed, honestly labelled)
- [ ] **CHECKPOINT (RULED 2026-08-19): STOP at the first quiet
  iteration (or after 18).** Report to Adnaan verdict-first with the
  full payment-surface list (§7). The independent e2e and THE FLIP
  run ONLY on his explicit go, in a later session — do NOT start
  them.

---

## 1. Context capsule (all an executor needs; do not re-derive)

**Project.** `pipeline/` ingests gameplay uploads from Drive I
(read-only forever, R6), validates/fixes/splits them, delivers to
Drive II, and computes payment sheets (hours only, R11). The continuous
driver (`pipeline/continuous.py`) replaces the batch driver
(`pipeline/run.py`, dormant rollback). **Nothing is deployed**;
everything ships at the flip, which runs in a LATER session after
Adnaan's checkpoint go (`FLIP_RUNBOOK.md`).

**State.** Suite: **749 collected / 749 green**, floor **745**, via the
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
copy OUTSIDE the repo (session scratchpad; pre-fix ref for I-fixes:
**`ce26148`** — the docs after `82c86da` leave the code identical);
pin-only tests use the mutation-proof pattern with the finders' EXACT
mutants from R15_FINDINGS.md (test_r_loop10/11/12/13/14.py have
examples). After every multi-agent step: `git diff` every hunk,
`grep -rn "MUTATION" --include="*.py" .`, `git status`. The working
tree carries pre-existing UNCOMMITTED junk (deleted sample dirs,
`.gitignore`, `SAMPLE_ANALYSIS_PLAYBOOK.md`) that predates r8 — leave
it, never commit it, never clean it. The 29 open batch rows in the
ledger are the dormant batch driver's rollback state — never touch
them. Drive I read-only forever.

**Key file map.** Findings of record: `R15_FINDINGS.md` (the ACTIVE
work queue's evidence) and earlier `R8..R14_FINDINGS.md`. Review
workflow snapshots: `tools/review/flip-review-iter8..15.js`. Flip
commands: `FLIP_RUNBOOK.md`. Rulings: `FLIP_SESSION_KICKOFF_PROMPT.md`
(R1–R4), `R5_TRIAGE_KICKOFF_PROMPT.md` §7 (pre-registered "quiet"),
`R6_HANDOFF_KICKOFF_PROMPT.md` §4/§5 (payment split,
CONT_DAILY_REPORTS), `R8_HANDOFF_KICKOFF_PROMPT.md` (scope of record).

---

## 2. Hardening discipline (binding — Adnaan's "no more issues" order)

Every review round's worst findings have been REGRESSIONS from the
previous round's fixes, and the mechanism repeats. These rules bind
every I-fix and everything after:

1. **Sibling-site sweep, recorded.** Before implementing each I-fix:
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

## 3. Fix specifications I1–I8 (from R15_FINDINGS.md; Adnaan's rulings 2026-08-19; vetted 2026-08-19)

Execution order I1 → I8 (the two wrongful-reject majors first; ruled
items last). Per-commit discipline: implement → fail-first proof in a
scratch copy of `ce26148` OUTSIDE the repo → Mac gate → path-scoped
commit; VM gate once after I8; locate by SYMBOL, never remembered line
numbers. Where a spec deviates from a finder's proposal, the deviation
is stated inline.

### I1 — qa-v2 exempts caseless key tokens (r15 #4, MAJOR; RULED direction)

The checker's bad-token grammar (`translator/v2.py`, the
`t.lower() == t and not t.isdigit()` clause inside check_session_v2's
per-row token loop) flags every caseless symbol key (';', '-', '[',
'/', …) that the writer's own `key_display` legitimately emits
(single-char tokens come back as `tok.upper()`, which for symbols IS
the caseless token) and `_v2_rows` keeps whenever the session keybind
binds it. The FAIL maps to INP_TOKEN_CASE → FIX_KEY_HYGIENE, which
re-tokenizes through the SAME `key_display` — a provably no-op fix
loop: both attempts burn, terminal reject, every session from that
player unpaid. **RULED (Adnaan 2026-08-19): the checker exempts
caseless tokens — symbol keys are real gameplay data and stay in the
delivery.** Fix: the case clause flags only tokens that HAVE case,
i.e. `(t.lower() == t and t.upper() != t and not t.isdigit())`.
Multi-char lowercase tokens ('left_shift') still flag (their upper
differs); digits stay exempt; the whitespace/comma arms are untouched.
Sweep rule 1: every checker token-grammar site (keys, buttons — the
button set-membership test is a different vocabulary, verdict per
site), the INP_TOKEN_CASE needle in `pipeline/validate.py`'s QA map
(unchanged — the FAIL simply stops firing for caseless tokens), and
fix_key_hygiene's key_display round-trip (now agrees with the checker
by construction). Tests (fail-first at `ce26148`): e2e — a real
bundle whose keybind binds ';' → translate → `check_session_v2` shows
NO non-v2-token FAIL and the ';' presses ride the delivered rows with
their actions; hygiene idempotence — fix_key_hygiene on that session
strips 0 and the re-check stays clean; control (§2 rule 3) — a
genuinely lowercase LETTER token still FAILs the grammar.

### I2 — writer strips action-less combo halves (r15 #5, MAJOR; RULED direction)

`bound_literals` includes every alt token of a `{modifier, key}`
combo group, so both halves are "bound" and `_v2_rows` keeps them —
but `resolve_actions` fires only when ALL of a rule's groups are held,
so any frame holding one half alone ships keys with null actions and
check_session_v2 FAILs the keys-have-actions invariant. BOTH fix
routes reproduce it (hygiene strips only unbound tokens; retranslate
re-bins identically) — terminal reject, every session from that
player unpaid. **RULED (Adnaan 2026-08-19): restore the invariant at
the writer — a combo half pressed alone is stripped-and-counted
exactly like an unbound key.** Fix (the finder's main proposal): a
kept token must be CREDITED by the frame's resolved actions — extend
resolve_actions' credited-literals accounting to the no-context path
(or equivalently: drop tokens whose every satisfied rule requires an
unheld co-group), strip-and-count the uncredited ones in `_v2_rows`
(the existing strip_stats/`stripped N unbound key presses` plumbing),
and MIRROR the same rule in fix_key_hygiene's own keep/strip loop.
retranslate_from_sidecars inherits via `_v2_rows`. Scope guards
(sweep rule 1 + §2 rules 3/4, enumerate in the commit message):
motion-axis rules credit no literals (their lits set is empty — keys
are not stripped for lacking motion); mouse buttons keep today's
behavior unless a combo group names one (verdict per site);
collapse_ambiguous_runs and the context-gating dead_literals path are
untouched. Tests (fail-first at `ce26148`, both routes end-to-end):
combo bind `{"interact": {"modifier": "ctrl", "key": "e"}}` + bare
'e' presses → delivered rows NEVER carry keys with null actions and
the checker passes that axis, through the hygiene route AND the
retranslate route; control — ctrl+e held together keeps BOTH tokens
and fires interact; control — plain single binds unaffected; hostile
mutant (rule 4) — the most damaging bypass shape (e.g. crediting via
ANY satisfied rule instead of the full-group test) must fail.

### I3 — tests-only: H2 slug-half OW discriminator pins (r15 #6, MAJOR)

All three H2 tests used kamla as the ledger game, and every consumer
of the branch slug (KEYBIND_PATCHES, CONTEXT_GAMES gating,
fix_sessionjson_recompute) is a no-op for kamla — so reverting ONLY
the slug assignment in retranslate_from_sidecars' non-override branch
(`slug = ledger_game or …`) passes the FULL arming gate while
silently stripping every patch-bound key press ('e'/'enter' →
general_confirm) and skipping mandatory OW context gating in
production. Fix (tests-only, in `pipeline/tests/test_r_loop14.py`
beside the H2 block): (a) ledger outer_wilds + degraded metadata
`{"name": 12345}` + usable custom raw/keybind.json + an 'e' press,
driven through plan_fixes → apply_fixes → assert the 'E' press
survives with action `general_confirm` (the OW KEYBIND_PATCHES half);
(b) an OW-ledger variant pinning that context gating keys on the
ledger slug (stub ctxmod.available/classify_video, the
test_r_loop12._context_work idiom). Mutation-proof: the finder's
EXACT mutant (revert the slug line to
`slug = game_key_from_name(game_name or "", exe_name) or
"unknown_game"`) must fail these tests — it passes 749/749 today.

### I4 — fix_v1_to_v2 naive-timezone guard (r15 #7, minor)

`fix_v1_to_v2` parses the v1 canonical `created_at_utc` with
`fromisoformat(ca.replace("Z", "+00:00"))` then writes
`created.astimezone(timezone.utc)` — for a NAIVE stamp (no tz suffix,
a real HumynCapture provenance class) astimezone interprets HOST-LOCAL
time and shifts the written stamp by the host's UTC offset (−5h30m on
this Mac). Every sibling site already guards this
(fix_sessionjson_recompute, cutter.py, retranslate's `_utc`,
translator/v2 `_utc_aware`); this is the sole omission, and the qa
checker that would flag naive stamps never runs before ARR_V1_FORMAT
routes here. Fix: the exact two-line sibling guard after parsing —
`if created.tzinfo is None: created = created.replace(
tzinfo=timezone.utc)` — BEFORE the head_cut_s adjustment and the
astimezone write. Sweep rule 1: enumerate the sibling guard sites
with already-correct verdicts. Tests (fail-first at `ce26148`): force
a non-UTC host tz IN-TEST (`os.environ["TZ"] = "Asia/Kolkata"` +
`time.tzset()`, restored after — the Mac gate is IST but the VM is
not; the test must fail pre-fix on BOTH hosts): naive input
"2026-08-10T15:34:03" → the converted session.json carries
15:34:03Z, byte-identical wall clock; aware-input control unchanged.

### I5 — safe_session_id length bound (r15 #8, minor)

H7 rejected control characters but a >255-byte session_id still
passes and crashes every join's mkdir with OSError (errno 63), which
apply_fixes' classifier calls HOST — the row parks FIX_QUEUED and
retries forever (never terminal, never the designed folder-name
fallback), with an hourly alert blaming the host; the same crash
kills both G7 operator tools mid-batch. Fix: add a byte-length bound
to the shared accept condition —
`and len(session_id.encode("utf-8", "ignore")) <= 200` — 200, not
255, because the pipeline derives longer names from the sid
(`<sid>.split-manifest.json` +20, `<sid>-analysis` +9, `-pN`
segment/grandchild suffixes) that must all stay under NAME_MAX.
Over-length ids take the bundle-folder-name fallback at all five join
sites. Tests (fail-first at `ce26148`): 'x'*300 through the REAL v2
translate join → output inside out_root under the folder-name
fallback, no raise; unit pin at the shared decision point incl. the
boundary (a 200-byte id is kept, 201 falls back) and the clean-id
control.

### I6 — tests-only: H6 composition tail-arm + chat-head pins (r15 #9, minor)

`_joint_edge_short`'s CNT_EDGE_NONGAMEPLAY tail accumulation
(`tail_cut = min(tail_cut or 1e9, p["cut_at_s"])`) and the chat-HEAD
sub-branch (`t <= 3.0`, no edge param) are exercised by no test —
deleting the tail else-branch passes the FULL arming gate. Fix
(tests-only, beside the H6 block in
`pipeline/tests/test_r_loop14.py`): (a) head notif t=2.0 +
CNT_EDGE_NONGAMEPLAY tail cut_at_s=71.0 → exactly one CNT_SHORT with
post_cut_s == 68.0; (b) chat-head sibling (chat t <= 3.0, params
carry only t) + a tail cut → composed CNT_SHORT. Mutation-proof: the
finder's EXACT mutant (delete the CNT_EDGE_NONGAMEPLAY else-branch)
must fail (a); a head-arm mutant must fail (b); both pass 749/749
today.

### I7 — H5 ruling: gone-is-gone + rename coaching line (r15 #1≡#2≡#3≡#10, RULED)

**RULED (Adnaan 2026-08-19): "if the folder is gone, it's gone."** No
same-path heal, no consecutive-listing counters. The four confirmed
findings are DISPOSED as accepted behavior with one string change:
the natural correction is a re-upload under a NEW folder name, which
mints a new session id and processes as a completely separate session
— verified: BOTH dedupe sites (scan-time and download-time) exclude
QUARANTINED rows, so the dead row never blocks or dup-rejects the
renamed copy. Fix: (a) the vanished-arm detail and loud line gain the
coaching — detail becomes "folder gone from Drive I — dropped from
intake; re-upload under a NEW folder name to re-enter" (stays under
the 300-char event cap); (b) the arm's comment is corrected to state
the same-path restore is DELIBERATELY terminal (the old "reappears at
a clean path re-registers" phrasing reads as if any reappearance
heals — r15 proved the same-path case does not, and that is now the
RULED design, not a gap); (c) accepted-behaviour entry 70 (§4) so
iterations 16–18 do not re-raise it. Tests: same-path reappearance
stays QUARANTINED silently (pins the RULING against a future
well-meaning heal), and the quarantine event detail carries the
coaching string (fail-first at `ce26148`: string absent); the
existing different-path heal test and guard controls stay green
untouched. Rule 5: no new events (the detail string changes, the
transition does not; re-verify the ZIP_ADJ_CHANGED/breadcrumb/
paid-piece LIKE queries cannot match the new text).

### I8 — fix_sync_from_v1 portable delivery copy (RULED)

The tool copies delivery files with `subprocess.run(["cp", "-c", …])`
— the APFS-clone flag exists only on macOS, so the tool cannot run on
the Linux VM (where ops move at the flip). **RULED (Adnaan
2026-08-19): fix — portable copy.** Fix: replace the cp subprocess
with `shutil.copy2` (clone efficiency is dispensable for this
operator tool), and REMOVE the H9c twin's `_portable_cp` subprocess
stub so the twin exercises the tool's real copy on both hosts.
Fail-first evidence is already on record: the unstubbed twin FAILED
the VM gate at the tree before `82c86da` (CalledProcessError from
`cp -c`) — cite that run; after I8 the unstubbed twin must pass the
VM gate (the Linux prover) as part of the post-I8 gates. Sweep rule
1: grep the repo for other `cp -c` / mac-only subprocess sites,
verdict per site.

## 4. Review iterations 16–18 (multi-agent, ultracode; Adnaan's warrant 2026-08-19)

- **Stop rule (RULED): run 16; run 17 ONLY if 16 was not quiet; run 18
  ONLY if 17 was not quiet. STOP at the first quiet iteration** —
  quiet per R5_TRIAGE §7's pre-registered definition (zero confirmed
  blockers AND every confirmed major/minor fixed with the suite green
  on both hosts).
- **Not-quiet handling (RULED): fix in-iteration** — the executor vets
  fix specs from the findings (deviations stated), implements with the
  full §2/§3 discipline (fail-first, sweeps, gates), and the NEXT
  iteration reviews those fixes. Payment-surface changes and anything
  contradicting a standing ruling are surfaced to Adnaan BEFORE
  implementing. If 18 is also not quiet: fix its confirmed set, then
  STOP and report the fixes as landed-but-unreviewed, honestly
  labelled.
- Script per iteration: copy the PREVIOUS committed snapshot
  (`tools/review/flip-review-iter15.js` for iteration 16) to the
  session scratchpad as `flip-review-iter<N>.js`. Retarget the
  regressions lane at the newest fix commits (one-line description
  each, the iter15 pattern); refresh the suite numbers + HEAD note +
  iteration number/framing; APPEND accepted-behaviour entries 70+ (for
  iteration 16: the I rulings — 70 H5 gone-is-gone + rename coaching;
  71 caseless-key exemption; 72 combo-half writer strip; 73 v1 tz
  guard; 74 session_id length bound; 75 portable fix_sync copy — and
  AMEND 62 where I3 pins the slug half, 65 where I7 makes same-path
  terminal THE RULED DESIGN, 66 where I6 pins the composition arms,
  67 where I5 joins the length bound) — update the list FIRST or
  agents re-litigate settled ground. NO raw backticks inside the
  ACCEPTED template literal (parse error, bit twice). Invoke via the
  Workflow tool with `scriptPath`.
- Keep: **ALL 7 lanes**, 2-vote refute (a finding dies only at 2/2),
  whole-codebase + regressions + tests-coverage. **Check usage-credit
  headroom with Adnaan BEFORE EACH launch** (~27–40 agents,
  ~3–4M subagent tokens per iteration; two iteration-11 refuters died
  on exhaustion).
- A Workflow launched by a session DIES if that session restarts —
  relaunch with `resumeFromRunId` (completed agents return cached) and
  VERIFY via the run's journal.jsonl whether the cache applied.
- After each iteration: findings-of-record doc (`R<N>_FINDINGS.md`,
  generated from the results JSON — never hand-transcribed; save
  `r<N>-results.json` to the session scratchpad), workflow snapshot
  committed to `tools/review/`, §0 updated.

## 5. CHECKPOINT — then (later, on Adnaan's go) e2e and THE FLIP

**RULED 2026-08-19: the executor STOPS at the first quiet iteration
(or after 18) and reports to Adnaan. The independent e2e does NOT run
until he explicitly says go.** The report is verdict-first and carries
the §7 payment-surface list.

For the LATER session that gets the go (keep, unexecuted): the
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
looping as host-blamed crashes), and K1 once landed (the
quarantined-path heal regains the r5 #41 identity guard: a session
folder moved into another player's tree can no longer flip payment
attribution in two scans; byte-identical moves and same-player
renames still heal). Also report the QUEUED new-scope item promised
to Adnaan 2026-08-19: the OW Observatory satellite-camera terminal is
an unmodelled context (memory `ow-satellite-camera-context-gap`) —
spec'd as a `satellite_camera` template + label in
translator/context.py + frame-verification; delivery-vocabulary
choice (strip vs new snapshot semantics) is Adnaan's/the client's; it
lands as its own ruled task AFTER the checkpoint go, not in this
stream. Label every mixed-methodology comparison as such. Relay
verifier verdicts verbatim.
