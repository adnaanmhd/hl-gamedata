# R8 Implementation Plan — fix ALL r-loop 8 → iterations 9–11 → e2e → THE FLIP

**Written 2026-08-18 by the session that read every source in full.** This plan is
self-contained: an executor session follows it WITHOUT re-reading the r-loop-8
evidence base. `R8_HANDOFF_KICKOFF_PROMPT.md` remains the ruling of record on
scope; this file supersedes it on *how*. Where this plan seems wrong, consult
`R8_FINDINGS.md` (the findings of record) before deviating, and say so out loud.

Every claim below was verified against the tree at code HEAD `869910d`
(docs HEAD `577bf92`) by full reads of: `pipeline/{continuous,run,fix,reports,
gate,validate,ledger,config,cutter,ingest(§heal)}.py`, `tools/{recal_refix_reset,
recal_regen_sheets,recal_rebuild_reset(§reset),retrim_v2_session,run_suite.sh}`,
`translator/{trim,binner,keys,keybind,translate,v2}.py`, and the test modules
named below. Line numbers are anchors as of that HEAD — **locate by symbol,
never by remembered line number** once edits land.

---

## 0. Status ledger — executor updates this section as work lands

Mark each item when its commit is green on Mac (and VM where noted). Resume from
the first unchecked item.

- [x] C1 translator minors (trim/binner/keys/v2 + fix_translate_raw cleanup) — Mac suite 552 green; fail-first 29 failed pre-fix
- [x] C2 BLOCKER fix.py retranslate guard (split children) — zero-events check replaces duration guard; fail-first 2/2
- [x] C3 BLOCKER host carve-out, both drivers — partial-applied → REVALIDATING; fail-first 3/4 (nothing-applied case is the r7-preserving control); Mac 558 green
- [x] C4 ops surfaces: stuck-list stint, digest retry stamp, AlertBook — fail-first 3/3; two existing stuck tests minimally re-seeded (stint events); Mac 561 green
- [x] C5 BLOCKER daily send durable counted record — .daily-counted.json + resume path; fail-first 4/4; Mac 564 green
- [x] C6 seal semantics (tree_sealed_at) + late-arrival deferral removal — fail-first 9/9; two payment tests deliberately rewritten (recorded in commit msg for Adnaan); Mac 571 green
- [x] C7 per-window gate record — real-writer rewrite of the r7 test closes the hand-built-shape trap; fail-first 2/2 (+1 legacy control); Mac 573 green
- [x] C8 STR_SJ_INVALID rewrite validates — 7/7 classes fail-first through the real fix chain; naive-ts control green; Mac 581 green
- [x] C9 suite knob-independence + SUITE_FLOOR 578 + doc corrections — repro CONFIRMED on the pinned runbook invocation (14 failed/567 passed at pre-C9 HEAD; 11→14 = the three new C5 send-path tests); green at BOTH knob values (582/582)
- [x] Full gate green on Mac AND VM at final r8-fix HEAD b694456 — Mac 582 (61s), VM side checkout 582 (251s), both floor 578; tree-verify (diff/status/MUTATION) clean
- [x] Review iteration 9 RAN (2026-08-18, workflow `tools/review/flip-review-iter9.js`, 53 agents) — **NOT QUIET: 23 confirmed (14 major / 9 minor / 0 blockers), 0 killed**; findings of record in `R9_FINDINGS.md`; fixes synthesized into §9 as D1–D8 (Adnaan redirected the r8 session to hand off instead of fixing in-iteration)
- [ ] D0 DISCUSSION FIRST (Adnaan, 2026-08-18): the D7 payment rule needs his ruling before D7 is implemented — measure the affected population on the real ledger, present options A/B/C, record the ruling in §9 D7 (procedure: R9_EXEC_KICKOFF_PROMPT.md "FIRST ITEM"). Blocks D7 only, but runs before everything.
- [ ] D1 translator hardening (#2 carried-only rebase guard, #3/#16 v2 untyped crashes, #17 falsy-key binding) — §9
- [ ] D2 validation truth sources (#12 CNT_SHORT from probed duration, #15 analyze() typed-FAIL path) — §9
- [ ] D3 driver host classes (#9 BrokenProcessPool first-death=host, #10 U-lane CalledProcessError) — §9
- [ ] D4 gate-record clock rebasing + adoption propagation (#11/#20, #14, #22 tests) — §9
- [ ] D5 daily-send resume robustness (#6/#21 day-agnostic, #4 conditional re-stamp, #8 doc_sent) — §9
- [ ] D6 quarantine heal md5-conditional stamp clearing (#5) — §9
- [ ] D7 refix tool payment-evidence refusal + pending-record interlock + lsf honesty (#1/#18, #7, #19) — §9 — OBSERVABLE payment-behaviour change, surface to Adnaan
- [ ] D8 C8 leftovers (#13 parse-and-regex re-emit, #23 conv_other test) — §9
- [ ] Post-D8: new SUITE_FLOOR measured+pinned; full gate green Mac AND VM; tree-verify
- [ ] Review iteration 10 (fix-in-iteration; quiet-judged; accepted-list additions 21–28 from §9)
- [ ] Review iteration 11 (only if 10 not quiet) — if still not quiet: STOP, hand Adnaan the list
- [ ] Independent REAL e2e verification (verdict relayed VERBATIM)
- [ ] FLIP §5 canary (kill matrix, autoscale, digest; `_pipeline_test/` purged)
- [ ] FLIP §6 (stop units → resize → deploy False-interlock → refix reset → arm → first hour)
- [ ] FLIP §7 payment endgame (regen preview → send → CONT_DAILY_REPORTS=True)
- [ ] FLIP §8 tree verify + deletion (LAST destructive act)
- [ ] Reject-reason table, final independent live verifier, final report

---

## 1. Context capsule (all an executor needs; do not re-derive)

**Project.** `pipeline/` ingests gameplay uploads from Drive I (read-only forever,
R6), validates/fixes/splits them, delivers to Drive II, and computes payment
sheets (hours only, R11). A continuous driver (`pipeline/continuous.py`) replaces
the batch driver (`pipeline/run.py`, kept dormant as rollback). **Nothing is
deployed**; everything ships at the flip, which THIS work stream executes at the
end (`FLIP_RUNBOOK.md` + kickoff §3.4).

**State.** Suite: **519 collected / 519 green** via the arming gate on Mac and on
the VM side checkout:

```bash
SUITE_FLOOR=450 bash tools/run_suite.sh \
    --with numpy --with opencv-python-headless --with rerun-sdk
```

(Contested "511 collected" in one refuter note = running WITHOUT the three
`--with` deps; 8 tests then skip collection. 519 is correct for the gate's
invocation — measured 2026-08-18 on the Mac.)

VM: `hl-pipeline-vm` (asia-south1-a, project `hl-gamedata-pipeline`, ssh alias
`hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline`). Work in
`~/hl-gamedata-continuous-test` ONLY (post-rebuild, `~/hl-gamedata` is still the
production tree until the flip deploy). `UV=$HOME/.local/bin/uv`, pins
`numpy==2.4.6 opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0`.
Sync recipe (the pipe-over-ssh form HANGS — never use it):

```bash
git archive HEAD | gzip > /tmp/tree.tgz
gcloud compute scp /tmp/tree.tgz hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline:/tmp/
gcloud compute ssh hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline \
  --command='cd ~/hl-gamedata-continuous-test && tar xzf /tmp/tree.tgz' < /dev/null
```

gcloud auth expires mid-session and cannot reauth non-interactively — ask Adnaan
to run `! gcloud auth login`.

**Ground rules (bind).** Verify before claiming; read whole sources; mark
`[assumption]`. NEVER push. Commits path-scoped per green step. Nothing deploys
before the flip; do not touch `~/hl-gamedata` on the VM or any systemd unit
until then. Secrets in `~/.config/hl-gamedata/secrets.env` — never print/log/
commit. `pipeline/tests/conftest.py` has a `_no_real_drive` guard — keep it.
Suite through `tools/run_suite.sh` on BOTH hosts for anything that ships.

**Traps (all learned this loop).**
- After every multi-agent step: `git diff` every hunk, `grep -rn "MUTATION"
  --include="*.py" .`, `git status` for agent-left files. Suite green is NOT
  proof the tree is unmodified.
- Every new test is proved to FAIL against unfixed code in a scratch copy
  OUTSIDE the repo: `mkdir /tmp/scratch && git archive <pre-fix-commit> | tar -x
  -C /tmp/scratch`, copy the new test file in, run it there, expect failure.
  Use the session scratchpad dir for scratch copies.
- Test against the REAL writer, never a hand-built shape (C7 fixes an instance).
- Reviewers' proposed fixes have been wrong twice — simulate before adopting.
  (This plan already vetted each; deviations from a finding's proposal are
  deliberate and explained inline.)
- Re-check every module that calls a changed entry point, not just the ones
  whose tests fail.

**Key file map.**
- Work queue evidence: `R8_FINDINGS.md` (committed). Machine-readable + refuter
  transcripts: `/private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/r-loop8-collected.json` (+ `r-loop8-journal-raw.jsonl`).
- Review workflow (2-vote discipline + accepted list): committed copy at
  `tools/review/flip-review-iter8.js` (snapshot of the scratchpad original).
- `FLIP_HANDOVER.draft.md` (ahead of the committed FLIP_HANDOVER.md): same
  scratchpad dir. Adopt at finalization.
- Plans/rulings: `FLIP_SESSION_KICKOFF_PROMPT.md` (R1–R4), `R5_TRIAGE_KICKOFF_PROMPT.md`
  §7 (pre-registered "quiet"), `R6_HANDOFF_KICKOFF_PROMPT.md` §4/§5 (payment split,
  CONT_DAILY_REPORTS rulings), `R8_HANDOFF_KICKOFF_PROMPT.md` (scope of record),
  `FLIP_RUNBOOK.md` (flip commands).

**The r-loop-8 verdict set** (dedup): 4 blockers → C2, C3, C5; major cluster →
C6 (four findings, one root cause); majors → C4, C7, and the red arming gate →
C9; translator crash classes → C1; STR_SJ_INVALID no-op fix → C8. One finding
KILLED (2/2 refuted): the QUARANTINED-empty-dir media-cap claim
(`continuous.py:369`) — **do not fix it, do not let a review re-raise it**
(cap membership deliberately differs from `_held_discovered`; see the killed
entry in R8_FINDINGS.md).

---

## 2. Execution order

Commits C1→C9 in order (each: implement → fail-first proof for new tests →
suite green on Mac → path-scoped commit). VM gate run once after C9 (plus once
mid-way after C5 if you want early warning; mandatory only at the end of the fix
phase and per review iteration). Then phases: review 9–11 → e2e → flip.

Rationale for order: C1/C2 are independent leaves; C3 before C4 (C4's stuck-list
tests exercise the carve-out's states); C5 before C6 (C6 rewrites two payment
tests whose modules C5 also touches); C7/C8 independent; C9 last because the
SUITE_FLOOR value must be measured after all tests exist.

---

## 3. Fix specifications

### C1 — translator minors (one commit: `translator/`, `pipeline/fix.py` cleanup, tests)

**C1a `translator/trim.py` `rebase_events` (~137/142).** The held-state carry
uses `e["key"]` / `e["button"]` as dict keys; container values (list/dict) are
unhashable → TypeError crashes every translate/retranslate whose sidecar has one
such event before the head cut. Fix: track held state only for `isinstance(...,
str)` identities (non-str is exactly what `keys.normalize_event_key` /
`binner` drop two steps later — nothing is lost):

```python
if et == "key":
    k = e.get("key")
    if isinstance(k, str):
        if act == "down": held_keys[k] = e
        else:             held_keys.pop(k, None)
elif et == "mouse_button":
    b = e.get("button")
    if isinstance(b, str):
        ...
```

**C1b `translator/binner.py` `raw_int` (~79-82).** `int(float(v or 0))` raises
OverflowError on `Infinity`/1e999/10**400 (json.loads accepts all three), which
neither except-arm catches → checker crash → QUARANTINED. Fix: widen to
`except (TypeError, ValueError, OverflowError): return 0`. (NaN already lands in
ValueError.)

**C1c `translator/keys.py` `normalize_literal` (~93) + `translator/keybind.py`
`_binding_groups`.** `raw.strip()` crashes on non-str (the `{modifier, key}`
form with VK numbers — r-loop 7 covered only the flat form). Fix BOTH halves:
- `normalize_literal`: `if not isinstance(raw, str): return ""` before strip.
- `_binding_groups`: drop empty normalized tokens so `""` can never enter
  `bound_literals` (a non-empty junk `bound` would defeat the r-loop-7
  built-in-keybind fallback). In the str branch skip when the token normalizes
  empty; in the dict branch: a `modifier` that normalizes empty is dropped
  alone (keep the key-only group); a `key` that normalizes empty makes the
  WHOLE binding unusable — emit nothing for it.
- `invert_keybind` is fixed transitively by the normalize_literal guard.

**C1d `translator/v2.py` `translate_bundle_v2`/`build_session_json` +
`pipeline/fix.py` `fix_translate_raw`.** The raw-only path reads metadata.json
with zero guards; truncated/malformed shapes raise bare
JSONDecodeError/AttributeError/ValueError (kind='session', both attempts burned,
unattributable error in fixlog), and each failed attempt leaks the
`work/<sid>/_translated/` video-sized temp tree. Fix:
- Add `class BundleError(Exception)` to `translator/v2.py`.
- `translate_bundle_v2`: wrap the metadata read in try/except
  `(OSError, json.JSONDecodeError)` → `BundleError("metadata.json unreadable:
  …")`; coerce non-dict `meta`→`{}`, non-dict `meta["game"]`→`{}`.
- `build_session_json`: guard `recording` non-dict; `started_at_utc`
  missing/non-str/unparseable → `BundleError` NAMING
  `recording.started_at_utc` (mirror `fix.py` `_utc`'s error text); guard
  `system` non-dict; tolerant `screen_width/height` cast
  (`int(float(v))` under try, fallback `info.width/height`).
- `fix_translate_raw`: wrap the whole body in try/finally with
  `shutil.rmtree(out, ignore_errors=True)` in the finally (currently
  success-path-only), so failed attempts don't leak the temp copy.

**C1 tests** (`translator/tests/test_r_loop8_translator.py` + extend
`pipeline/tests/test_r_loop7.py` parametrizes):
- rebase_events: parametrize `[65, ["w"], {"k": "w"}, None]` for key AND button
  with the event at `t < head_us` → no raise; plus a str key held across the
  cut is still re-pressed at t=0 (protects the r-loop-4 carry).
- raw_int: add `float("inf")`, `float("-inf")`, `10**400` to
  `test_raw_numeric_fields_degrade_to_zero`'s list.
- normalize_literal: parametrize `[65, None, ["w"], {"k": "w"}]` → `""`;
  `resolve_keybind` on `{"move_up": {"modifier": None, "key": 87}}` and
  `{"move_up": [{"modifier": "ctrl", "key": 87}]}` falls back to the built-in
  (extend `test_unusable_keybind_falls_back_to_the_builtin`); a usable
  `{"modifier": 16, "key": "w"}` degrades to a key-only bind without raising;
  `invert_keybind({"a": {"modifier": 16, "key": "w"}})` does not raise.
- v2: metadata variants (truncated file, whole-file array, `recording` a list,
  `started_at_utc` null/number/non-ISO, `system` a list,
  `screen_width: "1920x1080"`) → `BundleError` naming the field, for the
  cheap-to-reach ones through `translate_bundle_v2` with a tiny real ffmpeg
  clip (builder pattern exists in translator/tests/test_core.py), the rest
  through `build_session_json` directly with a hand-built `V.VideoInfo`.
- fix_translate_raw cleanup: monkeypatch `fixmod.translate_bundle_v2` to create
  `out/…` then raise → assert `work/_translated` is gone after `apply_fixes`.

### C2 — BLOCKER: retranslate guard kills split children (`pipeline/fix.py` ~725-734)

**Verified mechanism.** `head_s = created_at_utc − raw started_at_utc` is the
clip's offset into the RAW recording. `cutter.py` (~192, ~210) gives every split
child `created_at = parent_created + src_pts[i0]` AND a copy of the parent's
`raw/` precisely so children can retranslate. So any second-or-later segment
whose start offset exceeds its own length trips r-loop-7's
`if head_s > info.duration_s: raise FixFailed("implausible head offset …")` on
both attempts → wrongful terminal reject of good, already-cut footage.

**Fix.** Delete the duration-based guard entirely (the clip duration must not
appear in the test — kickoff §4b). Replace with the output-based check, placed
after the existing `events = trimmod.rebase_events(raw_events, head_s,
info.duration_s)`:

```python
if raw_events and not events:
    raise FixFailed(
        f"head offset {head_s:.1f}s leaves zero events from a non-empty "
        f"sidecar — session.json created_at_utc and raw metadata "
        f"started_at_utc do not describe this video; refusing to re-bin")
```

This preserves the original defence (never ship empty input columns off bogus
stamps) while making legitimate `head_s >> duration_s` children pass. The
r-loop-7 attributability tests (`test_retranslate_fails_attributably…`) fail at
the earlier None-check and stay green.

**Tests** (`pipeline/tests/test_r_loop8.py`):
- Split-child regression: build a real session via
  `pipeline/tests/test_fix_cut_gate.py`'s `_make_session` pattern (~100s ffmpeg
  clip, kamla, keyboard-only events so the sync/context branches stay quiet),
  give it `raw/` whose `started_at_utc` is 725s before `created_at_utc`, events
  in the [725, 725+dur) band → `retranslate_from_sidecars` succeeds and the
  rebuilt frames.csv carries those events. Fail-first: on the pre-fix tree this
  raises "implausible head offset".
- True-mismatch still refused: `created_at` beyond every event → `FixFailed`
  with "zero events".

### C3 — BLOCKER: host carve-out re-runs a partially-applied plan (`pipeline/run.py` ~575, `pipeline/continuous.py` ~880)

**Verified mechanism.** The r-loop-7 carve-out refunds the attempt and parks on
FIX_QUEUED with `reasons_json` untouched; `plan_fixes` is pure so the retry
re-dispatches the IDENTICAL steps from step 0 — including already-succeeded
destructive ones. `tools/retrim_v2_session.py::retrim` probes the CURRENT video
and removes `head_s` again on every call (verified by my own read; the r8
probes measured 300s→175s over five passes). In the batch driver `_fix_phase`'s
pass loop can even re-trim within a single run.

**Fix (both drivers, mirrored).** Park on FIX_QUEUED only when NOTHING was
applied; otherwise refund the attempt but route through REVALIDATING so the
next plan re-derives from the half-fixed copy (the pre-r-loop-7 invariant at
run.py ~509/571 comments):

```python
if out.get("kind") == "host":
    ledger.update(sid, fix_attempts=row["fix_attempts"])      # refund either way
    if not any(a.get("ok") for a in (out.get("applied") or [])):
        # step 0 failed: nothing was mutated, the identical plan is safe
        ledger.set_state(sid, "FIX_QUEUED",
                         f"host-level fix failure before any step applied — "
                         f"retrying: {out['error']}"[:300])
    else:
        # partially applied: NEVER re-run the plan blind (review finding #6)
        ledger.set_state(sid, "REVALIDATING",
                         f"host-level fix failure after applied step(s) — "
                         f"re-deriving from the current copy: "
                         f"{out['error']}"[:300])
    <alert>; <continuous only: self.cool.set(sid, C.CONT_RUNNER_CRASH_RETRY_MIN*60)>
    <run.py: continue; continuous _fix_one: return False in BOTH branches>
```

Continuous detail: in the REVALIDATING branch `_fix_one` must `return False`
(exit the runner) rather than `return True` — with the host condition live, an
immediate in-runner revalidation would burn a paid sweep; the cooldown + re-pick
is the correct pacing. Note `_discard_split_artifacts` already runs before this
branch in both drivers — keep it.

Existing test to keep green:
`test_r_loop7.py::test_host_error_refunds_the_attempt_and_parks_the_row` uses
`applied: []` → still parks FIX_QUEUED. Unchanged.

**Tests** (`test_r_loop8.py`):
- `applied=[{ok:True},{ok:False}]` + kind host → REVALIDATING, attempts
  refunded (both `cont._fix_one` and, for the batch driver, a `_fix_phase`
  invocation with `fix.apply_fixes` monkeypatched).
- `applied=[{ok:False}]` + kind host → FIX_QUEUED, refunded.
- Double-trim regression (the money shot): monkeypatch `fix._dispatch` so
  `FIX_RETRIM_HEAD` increments a counter file and `FIX_SESSIONJSON_RECOMPUTE`
  raises `OSError(28)`; drive `cont._fix_one` → assert retrim ran ONCE, state
  REVALIDATING, and a second `_fix_one` call is a no-op (state not FIX_QUEUED).
  Fail-first: pre-fix the state is FIX_QUEUED and a second call re-runs retrim
  (counter = 2).

### C4 — ops surfaces (`pipeline/continuous.py`, `pipeline/config.py`)

**C4a stuck-list blindness (`_stuck_lines` ~1157-1264).** The two host-error
retry loops (V lane cooldown-retry leaves state VALIDATING; C3's park leaves
FIX_QUEUED↔FIXING) re-stamp `updated_at` every 5 min so the
`updated_at < cut` predicate can never fire. Fix exactly like the existing
HOLD_VLM / READY-PACKAGED-UPLOADED stint queries: exclude
`('VALIDATING','FIX_QUEUED','FIXING','REVALIDATING')` from the `rows`-derived
entries (extend the python-side filter that already drops
READY/PACKAGED/UPLOADED) and add a stint query mirroring `undelivered`
verbatim with that 4-state set — anchored at MIN(e.ts) into the set since the
last event outside it; append when `first_v < cut`. The merged-list re-sort at
the end already handles ordering.

**C4b digest retry cadence (`_housekeeping_thread` ~1443).** The digest runs on
every ~20s tick; a Telegram outage = ~180 full digest rebuilds+sends/hour (the
exact defect CONT_DAILY_RETRY_S fixed for the daily, 25 lines below). Fix: new
config `CONT_DIGEST_RETRY_S = 600.0` (provenance comment: r-loop 8; failure-case
bound only — the 3h window still gates success cadence; worst added latency
10 min on a 3 h cadence); driver gains `self._next_digest = 0.0` beside
`_next_daily`; body:

```python
if self.send_telegram and now >= self._next_digest:
    self._next_digest = now + C.CONT_DIGEST_RETRY_S
    _duty("digest", lambda: self._send_digest(led))
```

Check `pipeline/tests/test_continuous.py` digest tests — any that tick H
multiple times expecting per-tick digest evaluation must advance the injected
mono clock past CONT_DIGEST_RETRY_S; fix tests minimally, never weaken them.

**C4c AlertBook stamps before sending (`AlertBook.alert` ~186-199).** A failed
send consumes the whole TTL. Fix: on TelegramError, give the slot back:

```python
except telegram.TelegramError as e:
    with self._lock:
        if self._sent.get(text) == now:      # still our stamp — retract it
            del self._sent[text]
    print(f"[alert-undelivered] {text} ({e})", file=sys.stderr)
```

(Optimistic-stamp-then-retract keeps at-most-one send in the healthy path; a
rare double-send under a race is dup-over-silence, the accepted trade.)

**Tests** (`test_r_loop8.py`):
- Stuck: seed a ledger where a sid ping-pongs FIXING↔FIX_QUEUED via hand-written
  events rows with a 20h-old stint start but fresh `updated_at` → appears in
  `_stuck_lines`; a session mid-normal-fix (stint 10 min) does not. Same for a
  VALIDATING self-refresh loop. Fail-first: pre-fix both invisible.
- Digest: telegram always raises; drive the H body N ticks with an injected
  mono clock inside one CONT_DIGEST_RETRY_S window → exactly 1 build+attempt;
  advance past the window → second attempt.
- AlertBook: send raises → a second `alert(same_text)` within TTL attempts the
  send again (attempt count 2); send succeeds → second call within TTL is
  deduped (count 1).

### C5 — BLOCKER: daily send durable counted record (`pipeline/run.py` `send_daily_report_if_due` ~775-927)

**Verified mechanism.** Nothing between sheet-build (877) and marker (913)
persists `counted`/`accepted`. Any interruption after a partial stamp (one
`database is locked` inside the per-root update loop, ENOSPC on the anchor
write) leaves marker absent → the 600s retry REGENERATES; post-stamp,
`build_sheet_rows` excludes every stamped root → a smaller (even header-only)
sheet overwrites `payment-<day>.csv` and is sent as the payment document.
`tools/recal_regen_sheets.py` already solves this exact problem with
`.regen-v2-counted.json` + `read/write_counted_record` — copy the pattern.

**Fix.** In `send_daily_report_if_due`, after the marker check:

- **Resume path** — `reports/<day>/.daily-counted.json` exists (and `.sent`
  does not): load `{lo, hi, counted, accepted}`; if unreadable → print a loud
  `[daily] resume record unreadable — REFUSING to regenerate post-stamp;
  reconcile by hand` to stderr and `return False` (never regenerate). Rebuild
  the DailyStats message from the STORED bounds (same queries; recomputed
  counters may drift slightly — the attached sheet is authoritative, duplicate
  message is the accepted cost), send it; `csv_path =
  reports_dir/<day>/payment-<day>.csv` — if missing, alert + `return False`
  (record-before-CSV ordering makes this unreachable except external deletion);
  stamp from the record (`mark_uploads_reported(..., sids=counted)`,
  `mark_accepted_reported(ledger, accepted)` — idempotent re-stamps are fine);
  `anchor.write_text(stored hi)`; marker; send the CSV on disk. **Never call
  `write_payment_sheet` on this path.**
- **Fresh path** — current flow with one insertion: after
  `write_payment_sheet(...)` returns and BEFORE `telegram.send_message`, write
  the record atomically (tmp + `os.replace`):
  `{"lo": lo, "hi": hi, "counted": counted, "accepted": accepted}`.
  Everything downstream (message → stamps → anchor → marker → document)
  unchanged, preserving the r5 #39 stamps→anchor→marker ordering.

**Existing tests.** `test_review_r5_driver.py::
test_daily_resend_after_kill_before_marker_no_double_count` asserts the resend
REGENERATES a smaller sheet — that behaviour is the bug's benign half and is
replaced: UPDATE the test to assert `build_sheet_rows` is called exactly once
across both sends, the resent CSV is byte-identical, and conservation holds
(cite r-loop 8 in the comment). The order and counted-set tests stay green
untouched. `test_payment_split_r6.py::
test_accepted_mark_is_written_by_the_daily_send_before_the_anchor` stays green
(fresh path unchanged around it).

**New tests** (`test_r_loop8.py`):
- Partial-stamp crash: monkeypatch `reports.mark_accepted_reported` to raise
  `sqlite3.OperationalError` once; first call raises; retry (fault removed) →
  CSV bytes unchanged from the first generation, document re-sent, every root
  stamped, anchor == stored hi, marker present. Fail-first: pre-fix the retry
  writes a smaller/empty sheet.
- Post-stamp kill (all stamps landed, marker missing): retry re-sends the
  identical CSV (pre-fix: header-only regeneration).
- Unreadable record → `return False`, sheet file untouched, loud stderr.

### C6 — seal semantics: `tree_sealed_at` (`pipeline/ledger.py`, `pipeline/reports.py`, `tools/recal_refix_reset.py`, `tools/recal_rebuild_reset.py`, `pipeline/ingest.py`)

**One root cause, four findings.** `accepted_reported_at` on a ROOT carries two
meanings: per-node "this node's hours/labels were counted" AND whole-tree
"sealed, never look again" (reports.py ~506 reads it as the seal; ~560/567 skip
every tree node on it; ~507 kills accepted_due). Consequences fixed here:
(i) an ordinary daily send stamping a DELIVERED/REJECTED root's own mark
locks its live children's future hours out forever; (ii) recal_refix_reset's
all-or-nothing root seal swallows a partly-paid tree's unpaid delivered hours
and its JSON names only the PAID nodes; (iii) the late-arrival deferral
(~512-532) is pure loss post-split — an unsettled late tree reaches NO sheet
while a HOLD_VLM node blocks it, though the identical in-window tree is paid
incrementally. The kickoff (§4d) endorses the separate-column shape as
cleanest. This touches RULED payment semantics: the ONLY observable behaviour
changes are the holes themselves; include a before/after of the two rewritten
tests in the commit message and surface it in the final report to Adnaan.

**Fix.**
1. `ledger.py`: additive migration `tree_sealed_at TEXT NULL` (same pattern as
   the two existing marks); add to `update()`'s allowed set; `supersede()`
   clears it (new bytes = new hours; extend the UPDATE + comment).
2. `reports.py build_sheet_rows`: add `tree_sealed_at` to the SELECT;
   `sealed = bool(root["tree_sealed_at"])` — the root's own
   `accepted_reported_at` goes back to meaning ONLY the root node's count
   (rewrite the ~503-506 comment: the SEAL lives in its own column, written
   only by recal_refix_reset). Keep `sealed` in the 507 accepted_due guard and
   in the 560/567 node-skip. DELETE the late-arrival settle-check `continue`:
   a late root is counted immediately exactly like an in-window one (uploaded
   now; accepted lands via accepted_due). Keep loud logs — when the late tree
   is unsettled print `LATE ARRIVAL (tree still in flight — uploaded counted
   now; accepted hours follow on later sheets)`, else the existing
   conservation line.
3. `recal_refix_reset.py`: compute per plan-root
   `delivered = [(sid,row) for DELIVERED nodes in [root]+kids]`,
   `paid`/`unpaid` split on `accepted_reported_at`.
   - **Mixed tree (both non-empty): REFUSE that root** — exclude it from the
     plan BEFORE the Drive-move stage, list it in the output JSON as
     `skipped_mixed: [{root, paid_nodes, unpaid_delivered_nodes:
     [{sid, hours}]}]` with a loud print (a human reconciles; an automatic
     choice either double-pays or swallows unpaid hours — there is no per-node
     fidelity after teardown deletes the child rows). Other roots proceed.
   - Fully-paid (paid non-empty, unpaid empty): proceed; root UPDATE writes
     `tree_sealed_at = now` and `accepted_reported_at = NULL`;
     `sealed_roots` entry unchanged in shape.
   - No paid nodes: proceed; both columns NULL.
4. `recal_rebuild_reset.py` (~95): add `tree_sealed_at=NULL` to the reset
   UPDATE. `pipeline/ingest.py` quarantine heal (~281): add
   `tree_sealed_at=None` to the heal's `ledger.update`.

**Existing tests to update** (cite r-loop 8 in comments):
- `test_payment_split_r6.py::test_refix_seal_only_fires_where_hours_were_actually_counted`:
  tree B asserts `tree_sealed_at` set and `accepted_reported_at` None on the
  root; A/C additionally assert `tree_sealed_at` None. (B is fully-paid — its
  one delivered node was counted; it is NOT the mixed case.)
- `test_reports_pace.py::test_late_arrival_incomplete_folder_counted_once`:
  rewrite to the post-split behaviour — w2 counts uploaded 1.0 / accepted 0.0
  immediately with the loud in-flight line; w3 carries accepted 0.94 via
  accepted_due; w4 empty. Conservation total unchanged.
- Everything else in `test_reports_pace.py` (d3 conservation invariant,
  `test_late_arrival_undownloaded_at_generation_counted_once` — its root
  settles before generation, unaffected) and `test_payment_split_r6.py` must
  pass UNCHANGED. Run these two modules first after this cluster.

**New tests** (`test_r_loop8.py`):
- Daily-send self-mark is not a seal: root REJECTED (labels counted → root's
  accepted mark stamped by `_sheet` helper) with a live VALIDATING child; child
  then delivers → hours on the next sheet exactly once. Fail-first: pre-fix
  day2/day3 are empty forever.
- Late root with a HOLD_VLM node: counted now (uploaded), delivered sibling's
  hours land via accepted_due; nothing deferred. Fail-first: pre-fix produces
  no row at all.
- Refix mixed tree: refused + both lists in JSON + other roots processed;
  the unpaid delivered node's row/hours untouched.
- Refix fully-paid tree end-to-end: sealed via `tree_sealed_at`; after the
  re-run re-delivers, no sheet ever counts that tree again.
- `supersede`, rebuild-reset, quarantine heal each clear `tree_sealed_at`.

### C7 — per-window gate record (`pipeline/gate.py`, `pipeline/fix.py`, r7 test rewrite)

**Verified mechanism.** `plan_fixes` emits ONE `FIX_GATE_WINDOW` step carrying
ALL windows (fix.py ~268/297/348); `gate.gate_windows` returns ONE aggregate
`destroyed` inventory; `_propagate_gate_record` filters per ENTRY
(`_gate_entry_touches` = ANY window overlaps) — so with frozen windows in two
different segments BOTH inherit the FULL inventory, and a sibling's genuine
`INP_KEYS_MISSING`/`CNT_ACTIONS_FEW` is downgraded by inventory destroyed
elsewhere (validate.py ~581-590/~609-619 need only a truthy count).

**Fix.**
1. `gate.gate_windows`: compute per-REQUESTED-window padded row sets first;
   `blank` = their union (behaviour identical). Before blanking, compute
   per-window destroyed inventory from the original rows and per-window applied
   spans; add to the note:
   `"per_window": [{"requested": [t0,t1], "windows": [[a,b],…],
   "destroyed": {"actions": […], "key_frames": n}}, …]`.
   The aggregate `destroyed` stays as-is (the parent's own `_gate_destroyed`
   reads it). Comment: overlapping windows may double-count `key_frames`
   across per_window entries; the aggregate remains the truth for the parent —
   per_window exists for split-child attribution (r-loop 8).
2. `fix._propagate_gate_record`: for each segment, for each gate entry — if the
   note has `per_window`, select the windows whose APPLIED spans (fallback:
   requested) overlap `[t0, t1)`; skip the entry when none; otherwise append a
   SYNTHETIC entry `{"fix": "FIX_GATE_WINDOW", "ok": True, "params":
   {"windows": [selected requested]}, "note": {"windows": [their applied
   spans], "destroyed": {union actions, summed key_frames}, "propagated_from":
   parent}}`. Entries WITHOUT `per_window` (older logs) keep today's
   whole-entry behaviour via `_gate_entry_touches`.
3. `_gate_entry_touches`: test against `note["windows"]` (actually-blanked
   spans, pads included) when present, falling back to `params["windows"]`;
   unknown/unreadable still propagates (never drop silently).

**Tests.**
- REWRITE `test_r_loop7.py::test_gate_record_only_reaches_the_segment_that_holds_the_window`
  against the REAL writer: build a synthetic v2 frames.csv, run the real
  `gate.gate_windows(dir, [(40,42),(300,302)])`, propagate to p1=[0,100) /
  p2=[200,400) → p1's child fixlog carries only (40,42)'s inventory, p2 only
  (300,302)'s; `validate._gate_destroyed(child_dossier)` returns each child's
  share only. (This also closes the standing "hand-built note shape" trap —
  that test was the instance.)
- Keep `test_gate_record_propagates_when_bounds_are_unknown` green (legacy
  path).
- New: sibling-honesty regression — segment with genuine zero key frames whose
  inherited record (correctly filtered) is empty → `map_reasons` still raises
  `INP_KEYS_MISSING`; pre-fix the inherited 65 key_frames downgraded it.

### C8 — STR_SJ_INVALID rewrite actually repairs (`pipeline/fix.py` `fix_sessionjson_recompute`)

**Verified mechanism** (refuters ran the full round trip): the rewrite defaults
only ABSENT/FALSY fields while the checker rejects PRESENT-but-invalid values —
platform `'Windows'`, localization `'english'`, partial/invalid
`input_mouse_convention`, and aware-but-nonconforming `created_at_utc`
(`'…08:33:31+0000'`, space separator) all survive both attempts → fix-failed
reject with three paid sweeps. Unmapping instead would reject sessions the
rewrite CAN repair — so make the rewrite validate:

- Import (function-local, like the file's other lazy imports)
  `_PLATFORMS, _MAPS_TO, _CAMERA_MAPS, _LOC_RE, _TS_RE` from `translator.v2`.
- `created_at_utc`: after parsing, if the original string is non-str or fails
  `_TS_RE`, re-emit canonically from the parsed instant
  (`created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"`) —
  covers the naive case (already handled) AND the aware-nonconforming cases.
- `platform`: keep iff `isinstance(str) and in _PLATFORMS`, else `"PC"`.
- `localization`: keep iff `isinstance(str) and _LOC_RE.match`, else
  `LOCALIZATIONS.get(slug, "en-US")`.
- convention: new `_conv_valid(conv)` helper replicating the checker's
  acceptance (five keys present; `maps_to` a str in `_MAPS_TO`; `other` needs
  `maps_to_other`; camera maps need str axis fields in the right enums;
  non-camera needs all `not_applicable`); replace the current weak test with
  `if not _conv_valid(conv): s["input_mouse_convention"] = dict(MOUSE_CONVENTION)`.

**Tests** (`test_r_loop8.py`): one parametrized round trip per class
(bad_platform / bad_localization / conv_partial / conv_bad_axes /
conv_bad_mapsto / space-separated ts / `+0000` ts): build a valid session
(reuse the `test_fix_cut_gate` builder), corrupt one field, run
`check_session_v2 → validate._map_qa_issues → fix.plan_fixes → fix.apply_fixes
→ check_session_v2` → AFTER has no FAIL. Control: naive ts (already repaired
pre-fix) stays green. Fail-first mandatory (pre-fix: identical FAIL after).

### C9 — suite knob-independence + floor + docs (`pipeline/tests/conftest.py`, `tools/run_suite.sh`, `FLIP_RUNBOOK.md`)

**REPRODUCE FIRST** (kickoff §4f mandate): scratch copy of the PRE-fix tree,
set `CONT_DAILY_REPORTS = False` in config.py, run the exact runbook gate
(`SUITE_FLOOR=440 bash tools/run_suite.sh --with numpy==2.4.6 --with
opencv-python-headless==5.0.0.93 --with rerun-sdk==0.36.0`) → expect
`11 failed, 508 passed` + `FATAL: pytest exited 1`. Record the output verbatim
in the commit message. (Both refuters reproduced it; if it does not reproduce,
record why and continue — the fixture is correct regardless.)

**Fix.**
- `pipeline/tests/conftest.py` autouse fixture (before test-local
  monkeypatches, which override it):

```python
@pytest.fixture(autouse=True)
def _daily_reports_knob_independent(monkeypatch):
    """The gate must be green regardless of the DEPLOYED CONT_DAILY_REPORTS:
    FLIP_RUNBOOK 6c ships False committed and the arming gate runs on that
    exact tree — 11 send-path tests went red on the runbook's own pinned
    invocation (r-loop 8). Tests asserting the suppression set False
    themselves and win."""
    monkeypatch.setattr(C, "CONT_DAILY_REPORTS", True)
```

- One direct suppression test in `test_r_loop8.py` (flag False →
  `send_daily_report_if_due` returns False, prints the suppressed line) — the
  interlock stays pinned under the fixture regime.
- `tools/run_suite.sh`: `SUITE_FLOOR="${SUITE_FLOOR:-<N>}"` where `<N>` =
  (passed count at final r8-fix HEAD − 4). Measure, don't guess (519 + new
  tests ⇒ expect ~545+).
- Verify in scratch: flag=False + fixture → gate green; flag=True → green.
- Doc edits (same commit or a doc-only sibling):
  - `FLIP_RUNBOOK.md` §6b: pin the new floor; add: "the gate is valid with
    `CONT_DAILY_REPORTS` at either value — the suite is knob-independent
    (r-loop 8)".
  - `FLIP_RUNBOOK.md` §6c: deploy set is **FOUR** things (continuous driver;
    `a4f93de` tolerances; R1–R3; ALL r-loop fix sets) — matching
    FLIP_HANDOVER §2.
  - `FLIP_RUNBOOK.md` §6c + step 7.3: say plainly `CONT_DAILY_REPORTS` returns
    to True IMMEDIATELY after the regen `--send` completes — a gap-closer,
    never policy.
  - `FLIP_RUNBOOK.md` §6c verification grep gains the r-loop-8 markers (see
    §6 of this plan).

---

## 4. After C9 — fix-phase close-out

1. Full gate on Mac. Then sync to the VM side checkout (recipe §1) and run the
   gate there with the pins. Both must be green.
2. Tree-verify discipline: `git diff` (should be empty), `git status` for
   strays, `grep -rn "MUTATION" --include="*.py" .`.
3. Update §0 checkboxes; update this file's floor number if it drifted.

---

## 5. Review iterations 9 → 10 → 11 (kickoff §3.2; multi-agent, ultracode)

- Script: `tools/review/flip-review-iter8.js` (committed snapshot). Copy to the
  session scratchpad, edit per iteration, invoke via the Workflow tool with
  `scriptPath`. Keep: 2-vote refute discipline (a finding dies only when BOTH
  refuters defeat it), whole-codebase + delta-since-loop-start (LOOP_START
  stays `2244758`) + adversarial hunting for regressions from the PREVIOUS
  iteration's fixes + **a tests-coverage lane** (r-loop 8's was lost at the
  stop; each iteration must run one).
- UPDATE THE ACCEPTED-BEHAVIOURS LIST first, or agents re-litigate settled
  ground. Add (keeping all existing entries):
  1. Host carve-out: partially-applied host failures route to REVALIDATING
     with the attempt refunded; FIX_QUEUED park only when nothing applied (C3).
  2. Retranslate guard is zero-events-based; clip duration deliberately absent
     from the test; split children legitimately have head_s >> duration (C2).
  3. The daily send's `.daily-counted.json` durable record; a resume re-sends
     the CSV on disk and NEVER regenerates; unreadable record refuses loudly
     (C5).
  4. `tree_sealed_at` is the ONLY whole-tree seal, written by recal_refix_reset
     alone; per-node `accepted_reported_at` decides everything else; the
     late-arrival deferral was DELETED deliberately (kickoff §4d cleanest
     shape); refix REFUSES mixed trees and reports both lists (C6).
  5. Gate record: per_window in the note; aggregate `destroyed` retained for
     the parent; child inherits only overlapping windows' share; legacy
     entries propagate whole (C7).
  6. Suite is CONT_DAILY_REPORTS-independent via the conftest autouse fixture;
     suppression is pinned by an explicit False-monkeypatch test (C9).
  7. `CONT_DIGEST_RETRY_S` bounds digest retry; AlertBook stamps only
     successful sends (duplicate-over-silence accepted) (C4).
  8. `raw_int` catches OverflowError; `normalize_literal` type-guards non-str
     (empty tokens dropped in `_binding_groups`); `BundleError` names
     metadata fields on the raw path; STR_SJ_INVALID's rewrite now
     validates-and-overwrites invalid constants (C1/C8).
  9. The KILLED r-loop-8 finding (QUARANTINED empty-dir cap membership) is
     settled — do not re-raise.
- Fix EVERY confirmed finding in the same iteration; suite green both hosts;
  path-scoped commits; fail-first for every new test; tree-verify after the
  workflow returns and again before each commit.
- **Quiet** (pre-registered, R5_TRIAGE §7 — judge AFTER fixing): zero confirmed
  blockers AND every confirmed major/minor fixed in that same iteration with
  the suite green on both hosts. Stop at the first quiet iteration. Run
  strictly in order; never in parallel. If 11 is not quiet: STOP — hand Adnaan
  every verified-but-unfixed finding, severity-ordered; do NOT proceed.

## 6. Independent REAL e2e verification (kickoff §3.3)

Only after the loop exits clean. A fresh agent that wrote and reviewed none of
this code exercises the actual system: real VLM calls, real Drive II
`_pipeline_test/` uploads purged afterwards via `deliver.cleanup_test_folder`,
real kill -9/resume. Verdict relayed VERBATIM; a BLOCKED-with-error never
becomes a pass.

## 7. THE FLIP (kickoff §3.4; this session executes)

Execute `FLIP_RUNBOOK.md` end to end — §5 canary (side checkout,
`HL_PIPELINE_HOME=~/hl-pipeline-test`, TEST-mode Telegram, `_pipeline_test/`
only + purge, 3-leg kill matrix, autoscale observed, digest fires; canary may
touch NOTHING in the real pipeline home) → §6 flip (Telegram announce
before/after; stop `hl-recal-watch` then any rebuild-era unit; E2→C2D resize
with the balloon check, never blocking on a zone stockout — §6b has the undo;
`CONT_DAILY_REPORTS = False` committed; deploy HEAD by rsync; re-touch rrd
stubs; verify on the VM BEFORE arming:

```bash
cd ~/hl-gamedata
grep -n "KEEP_GATE_MAX_S\|SCANNER_STATIC_MIN_S\|KEEP_GATE_MAX_FRAC" pipeline/config.py
#   -> KEEP_GATE_MAX_S = 5.0, SCANNER_STATIC_MIN_S = 0.8, NO KEEP_GATE_MAX_FRAC
grep -c "accepted_reported_at" pipeline/reports.py pipeline/ledger.py   # non-zero both
grep -n "read_counted_record\|write_counted_record" tools/recal_regen_sheets.py  # both
grep -n "first_pts_abs" translator/trim.py                              # trim-clock fix
# r-loop 8 markers:
grep -n "daily-counted.json" pipeline/run.py                            # C5 durable record
grep -n "tree_sealed_at" pipeline/ledger.py pipeline/reports.py tools/recal_refix_reset.py
grep -n "per_window" pipeline/gate.py pipeline/fix.py                   # C7
grep -n "CONT_DIGEST_RETRY_S" pipeline/config.py pipeline/continuous.py # C4
```

If any check fails, the rsync did not ship what was tested — stop and fix
before arming) → `recal_refix_reset` dry-run → review JSON (now includes
`skipped_mixed`) → `--yes` → `vm_setup.sh --enable-continuous` → watch the
first hour (429 rate in `~/hl-pipeline/logs/vlm-pressure.jsonl`, autoscale in
journald, disk, first digest) → §7 payment endgame (driver stopped →
`recal_regen_sheets.py` preview → sanity-read BOTH sheets → `--send`; final
invariant anchor == `2026-08-16T05:32:50+00:00` → `CONT_DAILY_REPORTS = True`,
commit, deploy, restart; update `NOTE_FOR_D3.md`; purge old sheet copies from
the GCS mirror after replacements verify) → §8 tree verify + LAST destructive
act → reject-reason table (both baselines, labelled mixed-methodology), final
independent live verifier (verdict verbatim), final report
(`FLIP_SESSION_KICKOFF_PROMPT.md` §6 steps 7–9).

Flip-time destructive gates keep their runbook protections: parachute backup
before reset-class actions, preview before `--send`, `recal_verify_tree.py`
CLEAN before any deletion. Drive I read-only forever. 29 open batch rows in the
ledger are the dormant batch driver's rollback state — never touch them.

`FLIP_HANDOVER.md`: adopt the draft
(`…/5d20eb3b…/scratchpad/FLIP_HANDOVER.draft.md`) as the running record of flip
state (no longer a baton-pass), fill §1/§7/§8 at each milestone, and add the
r-loop-8 material: C5's durable record, C6's seal redesign + the two
deliberately-changed payment behaviours, the new grep markers, the new floor.

## 8. Reporting

Verdict-first, per phase. For C6 include the before/after of the two rewritten
tests (deferral + refix seal) so Adnaan sees exactly which observable payment
behaviours changed and why each is the hole itself, not a side effect. Label
every mixed-methodology comparison as such. Relay verifier verdicts verbatim.

## 9. R-LOOP 9 FINDINGS → fix specifications D1–D8 (added 2026-08-18 by the r8 executor)

**Iteration 9 ran and was NOT quiet: 23 raised → 23 confirmed (14 major, 9
minor, 0 blockers), 0 killed.** Evidence of record: `R9_FINDINGS.md` (every
claim reproduced by both refuters against the real code). Workflow used:
`tools/review/flip-review-iter9.js` (committed snapshot of the script actually
run). These specs were written after reading all 23 claims TOGETHER — where a
spec deviates from a finding's own proposed fix, the deviation is deliberate
and explained inline. Finding numbers (#N) refer to R9_FINDINGS.md.

Execution order: D1→D8, same per-commit discipline as C1–C9 (implement →
fail-first proof in a scratch copy OUTSIDE the repo → suite green on Mac →
path-scoped commit; VM gate once after D8; locate by SYMBOL, never by
remembered line numbers). Rationale: D1/D2/D3/D6 are independent leaves;
D5 before D7 (D7's interlock reads D5's record semantics, incl. the new
`doc_sent` key); D4 independent; D8 tiny, last-but-one. After D8: bump
SUITE_FLOOR to (new passed − 4) in `tools/run_suite.sh` + FLIP_RUNBOOK §6b
(measure, don't guess), re-run BOTH host gates, then review iteration 10.

### D1 — translator hardening (#2, #3, #16, #17) — `translator/{trim,v2,keybind}.py`, `pipeline/fix.py`

**D1a (#3+#16, one fix)** `translator/v2.py`:
- `_px`: except tuple gains `OverflowError` (json.loads accepts
  Infinity/1e999 → `int(inf)` escapes — the exact class C1 closed in
  `raw_int`).
- `build_session_json`: wrap the `created = started + timedelta(...)` and
  `ended = created + timedelta(...)` arithmetic in try/except `OverflowError`
  → `BundleError("recording.started_at_utc unusable (out of range): …")` —
  a parseable-but-extreme stamp (`9999-12-31T23:59:59Z`) currently crashes
  untyped AFTER the full trim+bin+sync wall-clock.
- `translate_bundle_v2`: `session_id` — keep only `isinstance(str)` and
  non-empty, else fall back to `bundle_dir.name` (a numeric session_id
  currently crashes the Path join untyped). `exe_name` — treat non-str as
  None at the `game_info.get("exe_name")` read (a numeric exe_name reaches
  `keybinds.game_key_from_name`'s `re.sub` and crashes untyped). Mirror the
  same two guards in `pipeline/fix.py retranslate_from_sidecars` where it
  reads `game_info.get("exe_name")` (same provenance, same crash).

**D1b (#17)** `translator/keybind.py _binding_groups` dict branch: the
whole-binding-unusable rule is gated behind `if key:` — a PRESENT-but-falsy
key (`""`, `0`, `null`) skips it and emits the bare-modifier group the C1
comment explicitly forbids (probe: `{'crouch': {'modifier':'ctrl','key':''}}`
puts ctrl in bound_literals and fires 'crouch' on every ctrl-hold — ships
silently, qa-v2 cannot see it). Fix: enter the key path whenever
`"key" in value`; inside it, a key whose `normalize_literal` is empty (which
covers None/0/""/junk) voids the WHOLE binding (`return out`). The
modifier-only fallthrough survives ONLY for a genuinely ABSENT key field.
Extend `test_vk_number_key_makes_the_whole_binding_unusable` with the falsy
cases.

**D1c (#2, MAJOR)** the C2 zero-events guard is defeated by the r-loop-4
held-key carry: with bogus stamps (head_s beyond the whole recording), every
unmatched 'down' in the sidecar (keys held when capture stopped) is re-pressed
at t=0, so `events` is non-empty and the guard passes — the binner then holds
that key on EVERY row of a clip the stamps do not describe (fabricated input,
terminal unfixable reject with player-blaming coaching, or worse: shipped).
Fix: `trim.rebase_events` gains an optional `carried_out: list | None = None`
kwarg (append the synthetic re-presses there too; return value unchanged —
backward compatible for translate.py/v2.py callers). In
`retranslate_from_sidecars`: pass `carried_out=carried` and refuse when
`raw_events and len(events) == len(carried)` (every survivor is a carry).
KEEP the phrase "leaves zero events" in the message (the existing r8 test
greps it); extend it, e.g. "leaves zero events beyond N held-key carries".
Ruling 13 (zero-events shape, no clip duration in the test) still holds — a
legitimate split child always retains in-band events. Tests: bogus-stamp
sidecar whose only survivors are carries → FixFailed; split child WITH a
held key across its cut AND in-band events → still succeeds (protects both
C2 and the r-loop-4 carry).

### D2 — validation truth sources (#12, #15) — `pipeline/validate.py`, `tools/analyze_sample.py`

**D2a (#12, MAJOR)** `map_reasons` computes `dur` from the ENGINE's
`duration_s`, which `analyze()` fills from session.json's CLAIMED
`duration_seconds` — a present-but-wrong claim under 70 on a real ≥70s clip
emits CNT_SHORT (blocking, UNFIXABLE, `_VIDEO_INDEPENDENT` so it beats
HOLD_VLM) → bin 3 → terminal reject with zero fix attempts, while the SAME
verdict carries a fixable STR_SJ_INVALID whose planned rewrite recomputes the
very field. Fix: `validate_session` already probes the video (the
STR_VIDEO_UNREADABLE check) — thread `info.duration_s` into aux (e.g.
`aux["probed_duration_s"]`) and have `map_reasons` prefer it for the
CNT_SHORT / SESSION_SOFT_MAX tests, falling back to `rep["duration_s"]` only
when the probe failed. Do NOT touch `_map_windows` geometry in this pass
(bounded scope; the VLM-sweep-samples-to-claimed-duration amplifier is noted
in R9_FINDINGS #12 for a later loop). Tests: hand-built rep with claimed 45s
+ aux probed 120s → no CNT_SHORT (and the reverse: probed 45s → CNT_SHORT
regardless of claim).

**D2b (#15, MAJOR)** `tools/analyze_sample.py analyze()`: when its OWN
inventory re-read of frames.csv fails (StopIteration/csv.Error/OSError) it
sets `a.error` → `validate_session` raises RuntimeError("engine error…") →
QUARANTINED "validation crashed" — although `check_session_v2` already
produced the TYPED early-return FAIL ("frames.csv is empty…"/"unreadable…")
whose designed route (validate.py's own comment) is QA_FAIL_UNMAPPED →
FIX_RETRANSLATE when sidecars exist. Fix in `analyze()`: when the inventory
read fails AND `a.qa_issues` already contains FAILs, do NOT set `a.error` —
keep qa_status/qa_issues, skip the inventory/VLM sections, return a normal
analysis (map_reasons applies the designed routing). Preserve the host split:
when the failed read was an OSError and there are NO qa FAILs to fall back
on, keep `a.error` but ALSO set `a.error_kind = "host"`, and have
`validate.py` re-raise it as an OSError (or thread the kind through) so
`run._validate_worker`'s host classifier still sees host. Tests: 0-byte
frames.csv beside intact raw sidecars → reasons contain QA_FAIL_UNMAPPED
fixable=True (has_raw), never a quarantine; engine-level OSError path stays
host-classed.

### D3 — driver host classes (#9, #10) — `pipeline/continuous.py`, `pipeline/run.py`

**D3a (#9, MAJOR)** `_validate_one`'s BrokenProcessPool branch (stop unset)
builds a res dict with NO "kind", so an externally SIGKILLed worker — the
ONLY way kernel-OOM/systemd-oomd/cgroup kills present — takes the terminal
QUARANTINED path, bypassing the r-loop-6 host carve-out (MemoryError never
propagates through a SIGKILL). One OOM burst can terminally quarantine every
in-flight validation (up to CONT_POOL_MAX sessions) and the 48h sweep then
deletes their media. Fix: on BrokenProcessPool with stop unset, count prior
worker-deaths for this sid from the events audit (write a
`VALIDATING→VALIDATING` event with a distinct detail marker, e.g.
"validation worker died (host-suspect)" via `led.set_state(sid, "VALIDATING",
…)`; count events with that detail). First death → treat as host: cooldown
`CONT_RUNNER_CRASH_RETRY_MIN`, alert, return None (row stays VALIDATING —
the stint-based stuck list keeps aging it correctly since
VALIDATING→VALIDATING does not move the stint anchor). Second death for the
same sid → today's QUARANTINED (a session whose bytes reproducibly kill the
decoder still terminates). Tests: fake worker death twice via a _WORKER_FN
that SIGKILLs itself is not needed — drive the branch by monkeypatching the
pool result path; assert first death leaves VALIDATING+cooldown+no
quarantine, second death quarantines.

**D3b (#10, MAJOR)** the U-lane host tuple omits
`subprocess.CalledProcessError`: an rrd child (the ~20% R17-sampled
sessions) dying on ENOSPC/OOM/broken-pin exits non-zero →
CalledProcessError → generic except → terminal QUARANTINED for a
FULLY-VALIDATED session, during exactly the disk-low incident the lane's own
r-loop-3 comment documents (a hung rrd child is already host-classed —
inconsistent). Fix: add `subprocess.CalledProcessError` to the host tuple in
`continuous._deliver_one` AND the mirrored `run._deliver_phase` (delivery
lane ONLY — the fix-lane's CalledProcessError="session" classification is
RULED, accepted item 11, and stays). Tests: deliver_session raising
CalledProcessError → state stays READY, cooldown set, no quarantine, in both
drivers.

### D4 — gate-record clock rebasing + adoption propagation (#11, #20, #14, #22) — `pipeline/fix.py`, `pipeline/run.py`, `pipeline/continuous.py`

**D4a (#11+#20, MAJOR, one root cause)** every span in a gate record is on
the clock of the frames.csv AT GATE TIME, but two fixes shift that clock:
`cutter._cut_loop` rebases child rows to the segment's own PTS, and
`FIX_RETRIM_HEAD` rebases the parent's surviving rows. `_gate_entry_touches`
/ `_entries_for_segment` compare stored spans verbatim against CURRENT-clock
bounds, so: (i) a level-2 split drops the record from ALL grandchildren
(wrongful unfixable reject of the one holding the blanked rows — the
r-loop-6 blocker shape one level down; grandchildren exist in production);
(ii) after a retrim, an attempt-2 cut WITHHOLDS the record from the segment
that contains the blanked rows and WRONGLY hands it to the sibling (the
r-loop-7 harm, resurrected). Fix (rebase-at-write, chosen over
offset-annotation for reasoning simplicity):
1. In `_propagate_gate_record`, before selecting/writing entries for a
   segment, compute the entry's spans on the CURRENT parent clock: walk the
   parent fixlog for `FIX_RETRIM_HEAD` `ok:true` entries that come AFTER the
   gate entry and subtract the cumulative ACTUAL cut (`note.head_cut_s` —
   the retrim tool returns it; fall back to `params.head_s`).
2. When writing a child's inherited entry (synthetic per-window AND legacy
   whole-entry alike), rebase every span into the CHILD's clock: subtract
   the segment's `t0` and clamp at 0 (the child's row 0 sits at parent-clock
   t0; `src_pts[i0]` is what cutter used and `t0 = round(start,3)` is its
   second-precision twin — accept the ≤1ms skew, the spans are
   pad-widened). Legacy entries therefore become copies with adjusted
   `note.windows`/`params.windows`; the never-drop doctrine is preserved
   (unreadable spans still propagate whole and unadjusted).
3. `validate._gate_destroyed` is unaffected (reads only `destroyed`).
Tests (extend the C7 real-writer pattern in test_r_loop7/test_r_loop8):
level-2 — real gate on a parent, real propagation to an i0>0 child
(segments with t0>0), then propagate AGAIN with child-clock segments →
the grandchild holding the blanked rows inherits its share, the sibling
does not; retrim — gate, then a FIX_RETRIM_HEAD fixlog entry, then
propagate with post-trim bounds → correct segment inherits.

**D4b (#14)** two silent-loss holes for the same artifact: (i) BOTH mid-split
crash-adoption paths (`run._fix_phase` done-branch, `continuous._fixing_triage`)
complete the SPLIT without ever calling `_propagate_gate_record` — a kill
between the cutter's manifest write and the propagation loop ships children
with no inherited record. Fix: call `fix._propagate_gate_record(parent_dossier,
dossier_root, [], segments)` in both adoption sites before the SPLIT commit
(the earlier-attempt entries are read from the parent fixlog; `applied` may
be empty). Segments' t0/t1: parse from each child's ledger `detail`
("split segment {t0}-{t1}s") where present; adopted-child inserts whose
detail lacks bounds pass t0=None → propagate-whole (never drop; dup-over-drop
is the doctrine). (ii) the per-child `_append_fixlog` is wrapped in
`except OSError: pass` — ENOSPC silently ships a child without its record.
Fix: let the OSError propagate out of `_propagate_gate_record`; apply_fixes'
existing except classifies OSError as HOST, the C3 carve-out discards the
rescinded cut artifacts and re-derives — correct, and already tested
machinery. Tests: adoption path propagates (seed parent fixlog with a gate
entry, drive `_fixing_triage` adoption, assert child fixlog); OSError from
the child write → apply_fixes returns kind=host.

**D4c (#22, tests only)** the applied-span-vs-requested preference is
suite-invisible at BOTH sites (mutation-proved: reverting it leaves 582
green). Add one test per site where a segment overlaps only the PAD-widened
applied span, not the requested window (gate a window ending exactly at a
cut boundary so pad rows spill into the next segment) — legacy path via
`_gate_entry_touches`, per-window path via `_entries_for_segment`.

### D5 — daily-send resume robustness (#6/#21, #4, #8, #7-resume-side) — `pipeline/run.py`

**D5a (#6≡#21, MAJOR)** the resume record is looked up under TODAY's key
only — an interruption that outlives IST midnight strands the stamped
cohort's uploaded hours off every sheet ever delivered (the stamps exclude
them from all future sheets; the only CSV carrying them is never sent).
Fix: at the top of `send_daily_report_if_due` (after the interlock + hour
gates, BEFORE today's marker check), scan `cfg.reports_dir/*/` for any
`.daily-counted.json` whose sibling `.sent` is absent; if found, resume the
OLDEST such day first via `_resume_daily_send(cfg, ledger, now_ist, that_day,
record, that_marker)` and RETURN its result (one send per tick; the next
tick opens today). `_resume_daily_send` already takes the day string — no
signature change. Alert (via the caller's normal print/stderr path) when the
resumed day is not today. Tests: partial-stamp kill on day D → call with
now_ist = D+1 14:0x → day-D CSV document-sent and day-D marker lands, no
D+1 fresh generation on that tick; conservation across the sends.

**D5b (#4 + #7's resume half)** the resume re-stamps its recorded sids
blindly — over a supersede/heal/recal reset that legitimately cleared the
marks in the crash-recovery gap, and even over DELETED rows (silent no-ops).
Fix: write the record with a stamp-time field (`"at": <utc iso>`). On
resume: for each counted/accepted sid — row MISSING → the ledger changed
under the record (a recal tool ran): print a LOUD
"[daily] resume: counted row <sid> no longer exists — REFUSING; reconcile
by hand" to stderr and return False (same doctrine as the unreadable
record; the D7 interlock makes this near-unreachable). Row present with
`updated_at > record.at` AND its mark now NULL → a supersede/heal
deliberately cleared it: SKIP stamping that sid (loud per-sid line), stamp
the rest, continue the send (the CSV is authoritative for what was counted;
the new bytes' hours must stay countable). Backward compat: a record
without "at" stamps unconditionally (today's behaviour) — note it in the
code. Tests: supersede-between-kill-and-resume → resumed send does NOT
re-stamp that sid, its new upload's hours reach a later sheet exactly once;
deleted-row record → refusal, sheet untouched.

**D5c (#8)** `.sent` is touched before the document goes out; a kill in
between suppresses the CSV forever with a dangling "attached" message.
Fix: extend the durable record with `"doc_sent": true`, written (atomic
rewrite of the record) only AFTER `_send_sheet_document` returns on BOTH
paths. The marker-exists early return becomes: marker present AND
(record absent OR record.doc_sent) → False; marker present but record says
doc unsent → re-send ONLY the document (+ set doc_sent). Note
`_send_sheet_document` swallows TelegramError by design (alert path) — set
doc_sent only on the no-exception return, accepting that an in-flight
Telegram outage leaves it unset and the next tick retries the document
(dup-over-silence, consistent with the digest doctrine). Tests: kill
between marker and document (simulate: marker exists, doc_sent absent) →
next call re-sends the document only; steady state → False.

### D6 — quarantine heal must not wipe counted stamps (#5, MAJOR) — `pipeline/ingest.py`

The QUARANTINED-path heal clears
`uploaded_reported_at/accepted_reported_at/tree_sealed_at` + `duration_raw_s`
UNCONDITIONALLY — but unlike supersede there is no new-bytes evidence: on an
IDENTICAL-md5 path heal (operator fixed a folder-name typo — routine), a
root already counted on a sent sheet re-enters via the late-arrival guard
and its uploaded hours land on a SECOND sheet (probe broke d3's conservation
invariant: 2.0 counted for 1.0 uploaded). The sibling pre-download move-heal
already preserves stamps on the same rename. Fix: in the heal branch,
compare the newly listed video md5 (`vmd5`) against the stored `md5_video`:
DIFFERENT → today's full clear (genuinely new bytes — the supersede rule);
IDENTICAL → preserve `uploaded_reported_at`, `accepted_reported_at`,
`tree_sealed_at` AND `duration_raw_s` (keep the rest of the heal — attempts
reset, reasons cleared, dossier archived, state DISCOVERED — unchanged).
Tests: identical-md5 heal of a counted root → stamps survive, sheet
conservation holds across the heal; different-md5 heal → stamps cleared
(existing r6/r8 tests keep passing — check
`test_quarantine_heal_clears_the_accepted_mark` and
`test_quarantine_heal_clears_the_tree_seal`: they use
`make_session_entries(sid=sid)` whose md5 differs from the seeded "old" —
verify, and if the seeded md5 matches, adjust THEIR seeds so they pin the
different-md5 case, citing r-loop 9).

### D7 — refix tool: payment-evidence refusal + probe honesty (#1, #18, #19, #7-tool-side) — `tools/recal_refix_reset.py`, `tools/recal_rebuild_reset.py`

**⚠ D7a IS PENDING ADNAAN'S RULING (ledger item D0).** He asked to discuss
this rule before it lands (2026-08-18) — the spec below is the session's
RECOMMENDATION (option A), not yet a ruling. Hold the D0 discussion first
(procedure in R9_EXEC_KICKOFF_PROMPT.md "FIRST ITEM": measure the affected
population, present options A/refuse, B/seal-patched, C/per-piece memory),
then implement D7a per whatever he rules and update this spec + the two
named test rewrites to match. D7b (#7 pending-record interlock) and D7c
(#19 lsf honesty) are NOT part of the dilemma and proceed as specified
regardless.

**D7a (#1+#18, MAJOR, one coherent rule)** two seal defects with one root:
the tool's plan-time paid/unpaid computation reads only per-node accepted
marks. (#18) a fully-paid tree seals and re-runs, but its recovered
fix-failed child's hours can then NEVER reach a sheet — the seal swallows
the exact money the refix path exists to recover — while NOT sealing would
double-pay the re-delivered paid child: the same "no automatable answer"
dilemma C6 ruled must go to a human. (#1) a SEALED tree re-selected on a
later pass (its re-run child fix-failed again) recomputes paid=[] — the
seal itself suppresses new accepted marks — and the teardown OVERWRITES the
seal with NULL, re-opening already-paid footage for a second payment with
`sealed_roots: []` hiding it. One rule fixes both: **the tool proceeds ONLY
on trees with ZERO payment evidence** — refuse (into `skipped_mixed`, with
paid_nodes / unpaid_delivered_nodes / an existing `tree_sealed_at` listed)
any plan root where `paid` is non-empty OR the root's `tree_sealed_at` is
already set. Consequences: the seal-writing branch becomes unreachable
(keep `tree_sealed_at=None` on the proceeding UPDATE — proceeding trees
have no payment evidence by construction; the column, its reports-side
honor logic and its clears all STAY as defense-in-depth for the sealed
trees refused here and any historical rows). This is an OBSERVABLE
payment-behaviour change to C6's "fully-paid tree proceeds and seals" rule —
rewrite `test_refix_seal_only_fires_where_hours_were_actually_counted`
(tree B now lands in skipped_mixed with its rows untouched; A and C still
proceed) and `test_refix_fully_paid_tree_never_recounted_end_to_end`
(refused, not sealed; assert its rows/hours untouched and it appears in
skipped_mixed), citing r-loop 9 — and SURFACE both rewrites to Adnaan in
the final report exactly as the C6 changes were. NOTE for production: no
sealed trees exist yet (nothing is deployed; the flip's refix run is the
first), so no migration concern.

**D7b (#7, MAJOR)** the already-reported guard is blind to a PENDING daily
send: with `.daily-counted.json` written but stamps not yet applied
(Telegram outage window), the tool sees zero reported roots, tears the
cohort down, the later resume sends the STALE sheet crediting deleted rows
and the re-run's same-id children get counted AGAIN. Fix: both
`recal_refix_reset._locked_main` and `recal_rebuild_reset.main` refuse to
run (rc=2, loud JSON naming the day) while ANY
`reports/<day>/.daily-counted.json` exists without BOTH its `.sent` marker
and `doc_sent` (D5c) — same doctrine as their run-lock check. (The
resume-side sid verification is D5b.) Tests: pending record → tool refuses
before any Drive/DB action; record+marker+doc_sent → proceeds.

**D7c (#19)** `rclone lsf` failure ≠ absence: only rc==3 (and rc==4) mean
"directory not found"; today ANY non-zero rc (network outage = rc 1) prints
"remote dir absent", SKIPS the compensating moveto, and the teardown
proceeds — the re-run then re-delivers a duplicate to the client, violating
the tool's abort-before-DB contract. Fix: `rclone(...)` already returns
(rc, tail); treat rc in (3, 4) as absent-skip; any OTHER non-zero rc →
print the captured stderr and abort pre-DB with rc=3 exactly like the
moveto branch. Tests: monkeypatched rclone returning rc=1 on lsf → abort,
no DB change; rc=3 → skip-and-proceed (today's behaviour).

### D8 — C8 leftovers (#13, #23) — `pipeline/fix.py` + tests

**D8a (#13)** the rewrite keeps a `created_at_utc` that MATCHES `_TS_RE` but
does not PARSE (hour 25, month 13 — regex checks digit shapes only), so the
checker re-FAILs "timestamps unparseable" and both attempts burn. Fix: track
`parsed_ok` from the existing try in `fix_sessionjson_recompute`; the
canonical re-emit condition becomes
`not parsed_ok or not isinstance(created_raw, str) or not _TS_RE.match(created_raw)`
(mirroring the checker's regex+parse acceptance). Add a round-trip
parametrization row (`"2026-08-18T25:30:00Z"`, id="regex_valid_unparseable_ts").

**D8b (#23, test only)** `_conv_valid`'s maps_to='other' branch is unpinned
(mutation-proved). Add the parametrization row: `{"maps_to": "other",
"dx_positive": "not_applicable", …all not_applicable…}` (no `maps_to_other`),
id="conv_other_missing_label".

### After D8 — close-out and iteration 10

1. Measure the new passed count; set `SUITE_FLOOR` default = passed − 4 in
   `tools/run_suite.sh` AND FLIP_RUNBOOK §6b (part of the D-commit that
   lands last, or a sibling).
2. Full gate green on Mac AND the VM side checkout (sync recipe §1; use the
   BARE instance name with gcloud — `gcloud compute scp … hl-pipeline-vm:…
   --zone=asia-south1-a --project=hl-gamedata-pipeline`; the dotted alias is
   for plain ssh only and gcloud REJECTS it).
3. Tree-verify discipline (§1 traps), update §0.
4. Review iteration 10: copy `tools/review/flip-review-iter9.js` → scratchpad
   `flip-review-iter10.js`; retarget the regressions lane at the D1–D8
   commits; refresh suite numbers; APPEND to the accepted list (keeping ALL
   existing entries):
   21. The refix tool refuses ANY tree with payment evidence (paid nodes or
       an existing tree_sealed_at) into skipped_mixed; the seal-write branch
       is intentionally unreachable; tree_sealed_at stays honored
       defensively (D7).
   22. The daily resume is day-agnostic (oldest pending record first, one
       send per tick); resume refuses loudly on a missing counted row and
       skips (loudly) re-stamping rows whose marks were cleared after the
       record's `at`; `doc_sent` in the record gates document-only resends
       (D5).
   23. BrokenProcessPool with stop unset is host-suspect on the FIRST death
       (VALIDATING + cooldown, events-audit marker) and terminal on the
       second; U-lane host tuple includes CalledProcessError — the fix-lane
       CalledProcessError="session" ruling is unchanged (D3).
   24. Gate-record spans are rebased at propagation time (retrim offsets
       from the parent fixlog via note.head_cut_s; child clock via segment
       t0); adoption paths propagate with detail-parsed bounds, t0=None ⇒
       propagate-whole; _append_fixlog OSError is host-kind, never
       swallowed (D4).
   25. The quarantine heal clears payment stamps ONLY on a changed md5;
       identical-md5 heals preserve stamps + duration_raw_s (D6).
   26. CNT_SHORT (and the soft-max advisory) judge the PROBED duration when
       available; the claimed duration is only a fallback (D2).
   27. analyze()'s engine error is suppressed when typed qa FAILs already
       exist (QA_FAIL_UNMAPPED routing); engine OSError carries
       error_kind=host (D2).
   28. rebase_events reports carried re-presses via carried_out; the
       retranslate refuses carried-only rebases — "zero events beyond N
       held-key carries" (D1).
   Then run 10 (and 11 if 10 is not quiet) per §5's unchanged discipline —
   fix-in-iteration, quiet judged AFTER fixing, stop at first quiet, STOP
   and hand Adnaan the list if 11 is not quiet. Then §6 e2e → §7 flip.

**Deviations from finder proposals, recorded:** #1/#18 are unified into the
zero-payment-evidence refusal (each finding's own fix would leave the other's
defect half-open: preserving the seal (#1) still swallows recovered hours
(#18); refusing paid+fix-failed trees (#18) still lets a SEALED tree without
current paid marks erase its seal (#1)). #4/#7's resume-side overlaps are
split: missing row ⇒ refuse (7's severity), changed row ⇒ per-sid skip (4's
proposal). #9's fix uses set_state VALIDATING→VALIDATING for the death
marker (stint-anchor-safe) rather than a new events writer. #12 keeps
_map_windows out of scope (bounded change before the flip). #14's fixlog
OSError re-raise deliberately reuses the C3 host machinery instead of a new
alert path.

## 10. Status after iteration 9 (for the incoming executor)

- Fix phase C1–C9: DONE, committed `c3eab1b..b694456`, gates green both
  hosts at 582 (floor 578). Close-out commit `1e3320f`.
- Review iteration 9: DONE, NOT quiet — 23 confirmed (R9_FINDINGS.md);
  fixes NOT yet applied (Adnaan redirected the r8 session to hand off).
- Next: D1–D8 above → both gates → iteration 10 → (11) → e2e → flip,
  all per the unchanged §5/§6/§7 discipline and ground rules.
