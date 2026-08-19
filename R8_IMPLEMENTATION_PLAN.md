# R8 Implementation Plan — fix ALL r-loop 14 → iteration 15 → e2e → THE FLIP

**Context-optimized 2026-08-19 (second pass, on Adnaan's order) —
everything landed lives in git history, not here** (the G1–G9 spec
bodies are at `9388458` and earlier; the C/D/F/r12 material at
`b69fee1` and earlier). This file remains the complete work order: an
executor follows it without re-reading the older evidence base. Where
this plan seems wrong, consult the findings docs (authority chain
below) before deviating, and say so out loud.

**Authority chain if the plan seems wrong:** `R14_FINDINGS.md` →
`R13_FINDINGS.md` → `R12_FINDINGS.md` → `R11_FINDINGS.md` → older
findings docs → the older kickoffs. Machine results incl. refuter
verdicts: session scratchpads `r11-results.json` … `r14-results.json`.

---

## 0. Status ledger — executor updates this section as work lands

**DONE (full detail in git history + findings docs; do not re-derive):**
- r-loops 8–12: C1–C9, D0 ruling C, D1–D8, iteration-10 set,
  F1–F11, r12's 15 fixed in-iteration (QUIET after fixing at 696).
- Iteration 13 NOT quiet: 12 confirmed → **G1–G9 ALL LANDED**
  (`abf052b..82f5019`; floor 718 pinned `a5fc1a0`; both host gates
  722/722; every fix fail-first-proven at `b69fee1`; sweeps in the
  commit messages).
- Iteration 14 ran 2026-08-19 (33 agents, 0 errors): **NOT QUIET
  pre-fix — 13 raised → 13 confirmed (5 major / 8 minor, 0 blockers),
  0 killed** (`R14_FINDINGS.md`, snapshot
  `tools/review/flip-review-iter14.js`, commit `5f7015b`). Clusters:
  #1≡#6 (G2 fallback anchor), #2≡#3 (G1 counted_at anchor),
  #11/#12/#13 (pins for G5/G4/G7 halves). Adnaan's driver-core
  conditional NOT triggered (2 confirmed) — **all 7 lanes stay for
  iteration 15**. Fix specs vetted into §3 (H1–H9); per Adnaan's
  2026-08-19 instruction they are executed by the NEXT session and
  iteration 14's quiet is judged there AFTER fixing.

**ACTIVE — resume from the first unchecked item:**
- [x] H1 counted_at captured BEFORE the sheet's row read (r14 #2≡#3) —
  landed `1dd69fa`, fail-first at 5f7015b, gate 725/725
- [x] H2 retranslate session branch anchors its fallback on the ledger
  slug (r14 #1≡#6, MAJOR) — landed `1d54775`, fail-first at 5f7015b
  (both harm shapes), gate 728/728
- [x] H3 rebuild-reset discards split manifests + rowless segment dirs
  (r14 #10, MAJOR) — landed `a13e2ac`, fail-first at 5f7015b, gate
  729/729
- [x] H4 stable alert dedup: rclone stderr normalized at the choke
  point (r14 #4, MAJOR) — landed `924755b`, fail-first at 5f7015b,
  gate 732/732
- [x] H5 vanished-folder arm for DISCOVERED rows (r14 #5) — landed
  `c731e32`, fail-first at 5f7015b, gate 736/736 (two sibling tests'
  partial listings corrected in-commit)
- [x] H6 joint head+tail edge cuts get the map-time CNT_SHORT (r14 #7)
  — landed `25e900e`, fail-first at 5f7015b, gate 740/740 (deviation:
  test uses the finding's t=2.0/69.0 pair; plan's t=2.5 was a slip,
  noted inline in §3)
- [x] H7 safe_session_id rejects control characters (r14 #8) — landed
  `492a076`, fail-first at 5f7015b, gate 743/743
- [x] H8 analyze_sample verdicts judge the probed duration (r14 #9) —
  landed `e01edc7`, fail-first at 5f7015b, gate 746/746
- [ ] H9 tests-only: G5 depth-2 paid-piece pin + G4 site-4 de-vacuous
  + fix_sync_from_v1 traversal twin (r14 #11/#12/#13) — §3
- [ ] Post-H9: sweep results recorded per commit (§2); new SUITE_FLOOR
  measured+pinned (passed − 4, run_suite.sh + FLIP_RUNBOOK §6b); full
  gate green Mac AND VM; tree-verify; then judge **iteration 14
  QUIET/not per R5_TRIAGE §7** (0 blockers + all 13 fixed + both gates
  green ⇒ QUIET after fixing) and record it here
- [ ] Review iteration 15 (confirmation pass, runs REGARDLESS of 14's
  verdict — RULED by Adnaan 2026-08-19, his standing preference; ALL 7
  lanes — the driver-core conditional resolved NOT-triggered; check
  usage-credit headroom with Adnaan BEFORE the ~40-agent launch; if 15
  is not quiet: **STOP, hand Adnaan the list, severity-ordered — do
  NOT fix, do NOT proceed**)
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

**State.** Suite: **722 collected / 722 green**, floor **718**, via the
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
copy OUTSIDE the repo (session scratchpad; pre-fix ref for H-fixes:
**`5f7015b`** — docs-only commits after `a5fc1a0` leave the code
identical); pin-only tests use the mutation-proof pattern
(test_r_loop10/11/12/13.py have examples; use the finders' EXACT
mutants from R14_FINDINGS.md). After every multi-agent step:
`git diff` every hunk, `grep -rn "MUTATION" --include="*.py" .`,
`git status`. The working tree carries pre-existing UNCOMMITTED junk
(deleted sample dirs, `.gitignore`, `SAMPLE_ANALYSIS_PLAYBOOK.md`) that
predates r8 — leave it, never commit it, never clean it. The 29 open
batch rows in the ledger are the dormant batch driver's rollback
state — never touch them. Drive I read-only forever.

**Key file map.** Findings of record: `R14_FINDINGS.md` (the ACTIVE
work queue's evidence) and earlier `R8..R13_FINDINGS.md`. Review
workflow snapshots: `tools/review/flip-review-iter8..14.js`. Flip
commands: `FLIP_RUNBOOK.md`. Rulings: `FLIP_SESSION_KICKOFF_PROMPT.md`
(R1–R4), `R5_TRIAGE_KICKOFF_PROMPT.md` §7 (pre-registered "quiet"),
`R6_HANDOFF_KICKOFF_PROMPT.md` §4/§5 (payment split,
CONT_DAILY_REPORTS), `R8_HANDOFF_KICKOFF_PROMPT.md` (scope of record).

---

## 2. Hardening discipline (binding — Adnaan's "no more issues" order)

Every review round's worst findings have been REGRESSIONS from the
previous round's fixes, and the mechanism repeats. These rules bind
every H-fix and everything after:

1. **Sibling-site sweep, recorded.** Before implementing each H-fix:
   grep for EVERY writer/reader of the state or pattern the fix touches,
   list them in the commit message with a verdict per site
   (fixed / already-correct / out-of-scope-because), and fix the ones
   in scope IN THE SAME COMMIT. (r14 #12/#13 exist because the G4/G7
   sweeps fixed sibling SITES but the tests covered only one — the
   sweep discipline extends to the TESTS of every swept site.)
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

## 3. Fix specifications H1–H9 (from R14_FINDINGS.md; vetted 2026-08-19)

Execution order H1 → H9 (H1 money-path first; H9 tests-only last).
Per-commit discipline: implement → fail-first proof in a scratch copy
of `5f7015b` OUTSIDE the repo → Mac gate → path-scoped commit; VM gate
once after H9; locate by SYMBOL, never remembered line numbers. Where a
spec deviates from a finder's proposal, the deviation is stated inline.

### H1 — counted_at captured BEFORE the sheet's row read (r14 #2≡#3, minor; completes G1)

G1's arm-2 rationale ("a marker before the sheet's row read leaves a
REAL md5 in the snapshot") is false for the window between
build_sheet_rows' single SELECT and the `counted_at` capture, which
today happens AFTER `write_payment_sheet` returns — a ZIP_ADJ_CHANGED
adjudication landing in that gap (CSV+MD build + record write) is
missed by `ts >= counted_at` and the stamp lands silently; no
self-heal remains (the deferral already ran). Fix: in
`send_daily_report_if_due`, move the `counted_at = now()` capture to
just ABOVE the `write_payment_sheet` call, keeping it as the durable
record's `"at"` and both stamp calls' `counted_at` (the
identical-string property stays). Conservative-correct by G1's own
`>=` argument: a marker at/after the pre-build instant is not provably
pre-count, and skipping is the money-safe direction. The resume path
needs no change (it replays the recorded `"at"`). Tests (fail-first at
`5f7015b`): marker written between the build and the stamps with the
pre-build anchor → stamp SKIPPED loudly, next sheet counts the new
hours once; control: marker strictly before the build leaves a real
md5 in the snapshot → CAS arm governs (never arm 2); pin the
record-"at" == passed-counted_at identity.

### H2 — retranslate session branch anchors its fallback on the ledger slug (r14 #1≡#6, MAJOR; completes G2)

G2 made the session branch production for every non-reroute
FIX_RETRANSLATE, and that branch alone anchors its built-in-keybind
fallback on the PLAYER-TYPED chain (`game_info.name or meta.game_name
or s.game_title` + exe_name) instead of the ledger slug `_dispatch`
holds — degraded metadata (numeric/absent name, the r-loop 9/3
provenance class) yields `resolve_keybind -> {}`, which strips 100% of
key presses silently and terminally rejects a good session
(INP_KEYS_MISSING + CNT_ACTIONS_FEW, both unfixable) — or re-bins
under the WRONG game's built-in. Both siblings (fix_key_hygiene,
fix_actions_context) already pass `game_name=<ledger slug>` (F4
doctrine, accepted entry 37). Fix: `retranslate_from_sidecars` gains
`ledger_game: str | None = None`; `_dispatch` ALWAYS passes it
(`game if game in C.GAMES else None`) while `game_override` stays
reroute-only (G2 preserved). In the non-override branch:
`slug = ledger_game or game_key_from_name(...) or "unknown_game"` and
`resolve_keybind(keybind_path=raw/"keybind.json",
game_name=ledger_game or game_name, exe_name=exe_name)` — the
session's own keybind.json still wins when usable (r13 #4 intent
intact), and EVERY downstream consumer of the metadata-derived slug in
this branch (KEYBIND_PATCHES, context gating, the sessionjson
recompute) keys on the same `slug` variable (sweep rule 1: enumerate
them in the commit message). DEVIATION from #6's proposal: no post-hoc
empty-keybind fallback layer — resolve_keybind's internal
parsed-but-unusable fallback already lands on the right built-in once
anchored on the ledger slug. NOTE: #1 survived 1/2 — the dissent's
"intake would pre-reject such bundles" holds only for RAW payloads; v2
payloads (the production norm) carry player-produced frames.csv and
first meet our resolution chain at the retranslate, so the harm is
reachable (twin #6 survived 0/2). Tests (fail-first at `5f7015b`,
production chain plan_fixes → apply_fixes): degraded metadata
`{"name": 12345}`, no keybind.json, ledger kamla → keys survive with
built-in actions (pre-fix: keys == []); degraded metadata WITH a
custom keybind.json → the custom bind still wins; the existing G2
reroute control stays green.

### H3 — rebuild-reset discards split manifests + rowless segment dirs (r14 #10, MAJOR)

The teardown wipes only `work/<sid>` for ROWED sids — it leaves
`work/<sid>.split-manifest.json` and rowless `work/<sid>-p<N>` dirs,
so after a kill in the cutter's manifest-to-child-insert window a
post-reset re-run's crash triage ADOPTS the pre-recalibration (VOID)
cut (`_recover_split` → complete=True over the stale gen-1 segments),
and the dirs leak unreclaimably (`_sweep_terminal_work` needs a SPLIT/
REJECTED/DELIVERED parent or a rowed sid). Its refix sibling's
`discard_split_artifacts` exists for exactly this class (r-loop 3).
Fix: in the teardown loop, for every sid in `all_sids`, discard the
split artifacts beside the existing rmtree — reuse the shared
implementation the drivers/refix already use if its signature fits
(pipeline.run's `_discard_split_artifacts` or refix's 6-line
`discard_split_artifacts` with the `-p\d+` fullmatch shape), and wipe
`work/<sid>-analysis` as refix does. Tests (fail-first at `5f7015b`):
reset over a manifest + rowless-dirs / zero-child-rows state → the
manifest AND the `-pN` dirs AND `-analysis` are gone; a subsequent
`_recover_split` on the re-run root returns complete=False (no stale
adoption).

### H4 — stable alert dedup: rclone stderr normalized at the choke point (r14 #4, MAJOR)

AlertBook dedups on the literal message text, but rclone stderr lines
start with a wall-clock timestamp, so every rclone-backed failure
alert (download-failed, upload-failed, scan-failed) has a fresh dedup
key per attempt — the 60-min TTL never fires and a Drive/network
incident becomes a per-retry alert storm on the flip's ONLY ops
surface (verified: 12 sends/hour vs the designed 1). Fix: normalize at
the single choke point `ingest.run_rclone` — strip the leading
`YYYY/MM/DD HH:MM:SS ` prefix from each stderr line before returning
(rebuild the CompletedProcess with cleaned stderr); all three alert
sites inherit, restoring the documented per-sid hourly cadence
(accepted item 11), and the full (cleaned) rclone error text still
rides in the message. The timeout branch's synthetic message is
already stable — unchanged. DEVIATION from the finder's alternative
(an explicit `key=` param on AlertBook): not adopted — the choke-point
normalization fixes every present and future embedder without
changing AlertBook's contract. Sweep rule 1: enumerate every
`alert(...)` call site embedding volatile text and give a verdict per
site. Tests (fail-first at `5f7015b`): real AlertBook + fake clock +
production-shaped timestamped stderr → exactly 1 send per TTL
(pre-fix: one per attempt); run_rclone normalization unit test; stable
synthetic-timeout text unchanged.

### H5 — vanished-folder arm for DISCOVERED rows (r14 #5, minor)

A DISCOVERED row whose Drive folder was deleted retries forever (no
terminal state; the two existing prune arms cover only `incomplete`
rows and INT_PATH quarantines; the empty work dir holds no media so no
reclaim; the digest's undownloaded backlog is permanently inflated and
an `--until-idle` canary can never reach idle). Fix: a third
vanished-folder arm in `ingest.scan` under the SAME healthy-listing
guard as the existing two (games_present + path not in listed_dirs),
for rows in state DISCOVERED only → `set_state QUARANTINED` with
detail "folder gone from Drive I — dropped from intake", NO INT_PATH
reason (stays off the folder-issues chase list), one loud line per
row. Self-healing: if the same sid later reappears at a clean path,
the existing QUARANTINED-heal branch re-registers it. DEVIATION from
the finder's alternative (N-consecutive-failure counter in
`_download_one`): the scan-side arm mirrors the two sibling prunes and
keeps the driver stateless. Rule 5 note: this writes a genuine
DISCOVERED→QUARANTINED transition — the digest's quarantine counter
counts it as a real new quarantine, which it is; no marker-event
pollution. Tests (fail-first at `5f7015b`): DISCOVERED row + healthy
listing without its path → QUARANTINED + loud line; guard controls —
failed/empty listing and absent game tree → untouched; a
holds-media DISCOVERED row → also pruned only via the same
listing-derived evidence (state, not media, is the trigger); reappear
control → heals.

### H6 — joint head+tail edge cuts get the map-time CNT_SHORT (r14 #7, minor; completes r12 #7/G3)

The CNT_SHORT arms judge each edge individually; a clip with one
confirmed head flag and one confirmed tail flag that each pass alone
still plans BOTH cuts, and when the joint keep (min tail_cut − max
head_cut) is under MIN_CLIP_S the cutter drops every segment and the
session terminally rejects under 'split produced no >=70s segment' —
a burned attempt, a pointless ffmpeg cut, and a misdirecting reason
(the r12 #7 class, composition case). Fix: where both flag families'
planned cuts are visible in the mapper, after the individual arms:
when at least one head-edge and one tail-edge cut are planned, compute
the joint remainder on `dur_true`; if < MIN_CLIP_S append ONE
CNT_SHORT (blocking, unfixable, post_cut_s=joint remainder). Entry
26's `_map_windows` geometry untouched — the check only composes
already-computed cut points (CNT_EDGE_NONGAMEPLAY cut_at_s included).
Tests (§2 rule 3; fail-first at `5f7015b`): probed 75s, confirmed head
notif t=2.5 + tail chat t=73 → exactly CNT_SHORT with
post_cut_s == 69.0 (pre-fix: two fixable edge reasons); probed 200s
control → both fixable edges stand; single-edge tests unchanged.
[EXECUTOR DEVIATION 2026-08-19: the t=2.5/69.0 pair is arithmetically
inconsistent (head cut t+1.0 = 3.5, tail cut 72.0 → 68.5); the
finding's own probe (R14_FINDINGS #7) uses head t=2.0 → 3.0, joint
69.0. Test written with t=2.0 → 69.0 per the authority chain.]

### H7 — safe_session_id rejects control characters (r14 #8, minor)

An embedded NUL passes `safe_session_id` and crashes every join's
`resolve()`/`mkdir` with an untyped ValueError — burning both fix
attempts into a terminal "fix retries exhausted" reject (raw path) or
crashing the G7 operator tools mid-batch. Garbage ids are DESIGNED to
degrade to the bundle-folder-name fallback. Fix: add
`and not any(ord(c) < 32 for c in session_id)` to the accept
condition — one shared decision point covers all five join sites.
Tests (fail-first at `5f7015b`): NUL-bearing sid through the real
translate join → output inside out_root under the folder-name
fallback, no raise; a tab-bearing sid likewise.

### H8 — analyze_sample verdicts judge the probed duration (r14 #9, minor; completes D2/G3 in the operator tool)

`build_verdict` judges clip-short and tail-edge-ness on the CLAIMED
duration while the probed truth sits in `a.video_probe` — a
corrupt-small claim makes the recommend-only report tell an operator
to re-record good footage; a corrupt-large claim turns every genuine
tail window into "mid-clip". Fix: derive
`dur_true = float(a.video_probe.get("duration_s") or 0) or
a.duration_s` in analyze() and use it for the clip-short gate and
build_verdict's dur/at_head/at_tail tests, claim as fallback only.
Pipeline verdicts are unaffected (already probed-based). Tests
(fail-first at `5f7015b`): claim 59.9 / probe 600 → no re-record-short
verdict; claim huge / probe 300 with a window ending at the probed
end → tail-edge advice, not mid-clip.

### H9 — tests-only: G5 depth-2 paid-piece pin + G4 site-4 de-vacuous + fix_sync twin (r14 #11/#12/#13)

(a) r14 #11: depth-2 variant of the G5 allow-reported test — cohort
root(SPLIT) → root-p1(SPLIT) → root-p1-p1(DELIVERED 900s, accepted-
stamped); after the tool: `paid_pieces_for(root) == {root-p1-p1:
900.0}` and `paid_pieces_for(root-p1) == {}`; re-put the same children
and assert the next sheet skips (no row). Mutation-proof: the finder's
exact mutant (`parent_of.get(sid) or sid` in place of the walk) must
fail. (b) r14 #12: my G4 raw-sidecars test never invoked
`_verify_against_raw` (check_session_v2 needs `raw_bundle=` passed —
there is no auto-detection). De-vacuous it: call
`check_session_v2(d, raw_bundle=d/"raw")` and assert the specific
degrade line ('raw verification skipped' + OverflowError) rides
r.issues; add the per-event-arm sibling (duration 1e999-class + one
bigint-t mouse_raw event → returns without raising). Mutation-proof:
reverting BOTH site-4 arms must fail these tests (it passes 722/722
today). (c) r14 #13: the fix_sync_from_v1 traversal twin — `_load` the
tool, minimal v1+v2 pair (needs_ffmpeg), sid `../../../../ESCAPED` →
contained under out_root, folder-name fallback, no escape dir;
mutation-proof: reverting the G7 hunk must fail. All three proved
against a HEAD scratch copy with the finders' EXACT mutants.
[EXECUTOR DEVIATION 2026-08-19: H9 was tests-only, but writing the
H9c twin uncovered a REAL pre-existing crash in
tools/fix_sync_from_v1.py:167 — `actions = resolve_actions(...)`
predates the context-gating signature change (the function returns
(actions, dead_literals); v2.py unpacks it), so the tool's CSV writer
crashed with TypeError on the first row of EVERY real run; invisible
precisely because the tool had zero coverage (the r14 #13 class,
worse). One-line unpack fix landed in the H9 commit; fail-first
evidence: the twin test itself crashed with that TypeError on the
unfixed tree before reaching the join. Surfaced for the final
report.]

## 4. Review iteration 15 (multi-agent, ultracode; Adnaan's warrant 2026-08-19)

- Script: copy `tools/review/flip-review-iter14.js` (committed
  snapshot) to the session scratchpad as `flip-review-iter15.js`.
  Retarget the regressions lane at the H-commits (list each with a
  one-line description, as iter14 did for G); refresh the suite
  numbers + HEAD note; APPEND accepted-behaviour entries 61+ for the H
  rulings (keep ALL existing 1–60; amend 55 where H1 completes the
  counted_at anchor, 56 where H2 completes the fallback anchor, 57
  where H9b de-vacuouses the site-4 pin, 58 where H3+H9a extend the
  rebuild-reset teardown/pins, 60 where H9c pins the fix_sync half) —
  update the list FIRST or agents re-litigate settled ground. NO raw
  backticks inside the ACCEPTED template literal (parse error, bit
  twice). Invoke via the Workflow tool with `scriptPath`.
- Keep: **ALL 7 lanes** (Adnaan's driver-core conditional resolved
  NOT-triggered — driver-core confirmed 2 findings in iter 14), 2-vote
  refute (a finding dies only at 2/2), whole-codebase + regressions +
  tests-coverage. Check usage-credit headroom with Adnaan BEFORE the
  ~40-agent launch (two iteration-11 refuters died on exhaustion;
  iterations 12/13/14 each burned ~3–3.8M subagent tokens).
- Iteration 15 is the CONFIRMATION pass: if NOT quiet — **STOP; hand
  Adnaan every verified-but-unfixed finding, severity-ordered; do NOT
  fix, do NOT proceed.** If quiet: proceed to §5.
- After the iteration: findings-of-record doc (R15_FINDINGS.md,
  generated from the results JSON — never hand-transcribed), workflow
  snapshot committed to `tools/review/`, §0 updated.

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
roots' accepted hours now paid), F7 + r12 #1/#2 + G1 + H1 (the
compare-and-set stamps and the '' adjudication chain, including H1's
pre-build anchor), G5 + H3/H9a (rebuild-reset under ruling C, its
split-artifact discard and the depth-2 memory keying), and the two
C6-era rewritten payment tests. Label every mixed-methodology
comparison as such. Relay verifier verdicts verbatim.
