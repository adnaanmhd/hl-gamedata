# R10_FINDINGS.md — review iteration 10, findings of record

**Run 2026-08-18/19** by the r-loop-9 executor session via the workflow
snapshot `tools/review/flip-review-iter10.js` (edited per plan §9 "After D8"
from the iter9 snapshot: regressions lane on the D1-D8 commits
640651a..81d5f06, accepted list 21-28 appended, 15/17/22 amended for ruling C
and the D5b md5 deviation, suite 623/floor 619). 7 finder lanes, 2-vote
refute, 41 agents, 0 errors, HEAD 567c3e8.

**Pre-fix verdict: NOT quiet — 17 raised, 16 confirmed (7 major from the
finders' own severities before dedup, 0 blockers), 1 killed (2/2 refuted).**
Known duplicate: #3 ≈ #6 (worker-death lifetime count — one root cause, one
fix). ALL 16 were fixed IN-ITERATION per R5_TRIAGE §7; the quiet judgment is
recorded in R8_IMPLEMENTATION_PLAN §0 after both host gates.

Machine-readable results (both refuter verdicts per finding):
session scratchpad `r10-results.json`.

## Killed (2/2 refuted) — do not re-raise

- [minor] pipeline/reports.py:399 — Pending-send interlock is blind to the regen tool's own resumable state; a refix in that window double-pays

## Confirmed findings and the fixes applied

### #1 [MAJOR] pipeline/fix.py:405 (regressions-r9, refuters 0/2)

**D4's adoption gate-propagation cannot rescue the same-attempt gate entry — the record is never durable during the cut, so the #14 crash window still terminally rejects deliverable sessions**

apply_fixes writes the fixlog exactly once, AFTER the whole step loop (fix.py:405 `_append_fixlog(dossier_dir, applied)`), while a cut-bearing plan applies FIX_GATE_WINDOW and FIX_CUT_SEGMENTS in the SAME attempt (fix.py:267-270: `steps.append(("FIX_GATE_WINDOW", ...)); steps.append(("FIX_CUT_SEGMENTS", ...))`). gate.gate_windows durably blanks frames.csv but returns its destroyed-inventory note only in memory. D4b's new adoption calls (run.py:539, continuous.py:856) pass `applied=[]` and read only the parent fixlog (fix.py:528-540), so they can recover ONLY prior-attempt entries. Probe (executed, monkeypatched _dispatch; BaseException standing in for the kill -9 the design targets, raised inside FIX_CUT_SEGMENTS after the manifest write): `parent fixlog exists after kill: False`; then the exact D4 adoption call → `P-p1/P-p2: fixlog exists = False | _gate_destroyed = {'actions': [], 'key…

**Fixed:** durable-before-the-cut fixlog persist in apply_fixes (exactly-once via `persisted`; propagate gets only the unpersisted tail)

### #2 [MAJOR] pipeline/run.py:1073 (regressions-r9, refuters 1/2)

**D5's day-agnostic scan serializes every tick on the oldest pending day — one permanently-refusing record silently shuts down ALL future daily reports and payment sheets**

send_daily_report_if_due's new scan (run.py:1067-1085) returns `_resume_daily_send(...)` for the FIRST day dir holding a record that is not fully settled, before today's fresh path is ever reached. _resume_daily_send returns False forever when any counted/accepted sid no longer exists (run.py:980-986) or the record is unreadable without .sent — stderr print only, no Telegram alert. Probe (executed): a single wedged day (record whose counted sid was deleted) run over three consecutive days returned `tick day=2026-08-18/19/20: returned False; today's record exists=False` each tick with `telegram messages sent: 0 documents sent: 0` — no report, no sheet, no record for ANY later day. Under r8 (640651a~1) the lookup was day-keyed (`record = cfg.reports_dir / day / ".daily-counted.json"`), so a bad old day could never block today — the cross-day starvation is introduced by D5. The wedge state …

**Fixed:** .wedged marker + alert on permanently-refusing days; scan skips them loudly; daily-report CLI now takes the run lock

### #3 [MINOR] pipeline/continuous.py:782 (regressions-r9, refuters 0/2)

**D3's first-death cushion is keyed on session_id in the append-only events table — new bytes (supersede/heal/refix re-creation) inherit the old generation's death and go terminal on their FIRST worker death**

The BrokenProcessPool branch counts prior deaths with `SELECT COUNT(*) c FROM events WHERE session_id=? AND to_state='VALIDATING' AND detail=?` (continuous.py:782-785) against the marker string, and takes the terminal 'died twice' path when prior != 0 (continuous.py:794-796 → QUARANTINED at continuous.py:816). Events are never deleted anywhere (no `DELETE FROM events` exists; ledger.supersede only UPDATEs sessions and appends an event), so the count spans byte generations. Probe (executed): after one host-suspect marker on old bytes and a `ledger.supersede(new_md5="bbb", ...)`, the driver's own query returns `prior death count seen by the driver for the NEW bytes: 1` — the new upload's first worker death takes the terminal branch, not the cushion. The ruling's own rationale (item 23: second death = 'bytes that reproducibly kill the decoder') is violated: the bytes are different. The same…

**Fixed:** worker-death marker embeds md5 + count anchored at the last successful worker return (with #6, one fix)

### #4 [MINOR] pipeline/ingest.py:285 (regressions-r9, refuters 0/2)

**D6's identical-md5 heal test is blind for zip-origin sessions (Drive-side vmd5 is always "") — r9 #5's double-count survives for the whole zip class**

The heal's clear condition is `if vmd5 != existing["md5_video"] else {}` (ingest.py:280-285) where vmd5 comes from the Drive listing: `ds.files.get("video.mp4", {}).get("md5", "")` (ingest.py:230). A zip-shaped session passes _completeness with [] (ingest.py:172-173) and its ds.files holds only the zip — video.mp4 is never listed, so vmd5 is ALWAYS "" for zips. But a downloaded zip-origin row's md5_video was backfilled from the LOCAL file (ingest.py:846-848 `local_md5 = row["md5_video"] or _md5_file(dst / "video.mp4")`). Therefore on any zip-origin quarantine heal, "" != local_md5 is always true and the stamps (uploaded_reported_at, accepted_reported_at, duration_raw_s) are cleared even when the operator moved the SAME zip to a corrected path — the precise 'operator fixing a folder-name typo, documented routine' case the D6 comment says it now preserves. r_countable is state-independent …

**Fixed:** heal treats vmd5=='' as UNKNOWABLE (stamps preserved, prev_md5 event); download-time backfill compares and applies the deferred supersede-style clear

### #5 [MINOR] pipeline/run.py:466 (regressions-r9, refuters 0/2)

**_adopted_segments reads the OLDEST 'split segment' event, but events outlive refix child-row deletion — a re-run's crash adoption propagates the gate record against stale generation-1 bounds**

The new helper takes `SELECT detail FROM events WHERE session_id=? AND detail LIKE 'split segment %' ORDER BY ts LIMIT 1` (run.py:463-467) — ascending ts, i.e. the FIRST such event ever. recal_refix_reset deletes child SESSION rows (recal_refix_reset.py:392) but nothing ever deletes events, and cutter child ids are deterministic (<sid>-pN), so a re-run that cuts differently re-creates the same id with a NEW insert event while the old one still sorts first. Probe (executed): after deleting gen-1's row and re-inserting P-p1 with detail 'split segment 150.0-400.0s', `_adopted_segments returns: [{'id': 'P-p1', 't0': 0.0, 't1': 200.0}]` — the stale gen-1 bounds, not the live row's. _propagate_gate_record then tests span overlap and computes the child clock rebase against those bounds (fix.py:573-588).

**Fixed:** _adopted_segments takes the NEWEST split-segment event

### #6 [MAJOR] pipeline/continuous.py:782 (driver-core, refuters 0/2)

**Worker-death counter is a lifetime count per sid: sessions whose bytes provably decode fine are terminally quarantined on any second SIGKILL, and superseded new bytes inherit the old bytes' death**

The D3 host-suspect branch (continuous.py:781-796) counts marker events with `SELECT COUNT(*) c FROM events WHERE session_id=? AND to_state='VALIDATING' AND detail=?` — no time/stint/bytes bound. The branch's own rationale (line 774-776: 'Second death for the SAME sid: bytes that reproducibly kill the decoder') is falsified in two verified cases. (1) Intervening successful decode: I ran a probe (scratchpad/probe_death_counter.py) driving the real _validate_one with a fake pool: death#1 -> returns None, state VALIDATING, marker written; next attempt SUCCEEDS (worker returns hold_vlm -> HOLD_VLM, proving the bytes decode); 3 more successful HOLD retry cycles; then a later BrokenProcessPool -> prior=1 -> returns QUARANTINED with ledger detail 'validation crashed: validation worker died twice (native crash decoding this session)' — probe output: 'death#2 (hours later, unrelated burst) -> QUA…

**Fixed:** see #3 — same fix

### #7 [MAJOR] pipeline/fix.py:1005 (fix-validate, refuters 0/2)

**FIX_KEY_HYGIENE cannot clear the 'non-v2 mouse button tokens' FAIL it is the planned fix for — no-op loop burns the budget into a wrongful reject**

The checker FAILs any input_mouse_buttons token outside {Left,Right,Middle,X1,X2} (translator/v2.py:751-753, 806). validate.py:84 maps that FAIL ('non-v2 mouse button' needle) to INP_TOKEN_CASE fixable=True, and plan_fixes (fix.py:159-160) plans FIX_KEY_HYGIENE for it when there are no raw sidecars. But fix_key_hygiene only round-trips buttons through the two exact-name maps: fix.py:1005 `_BTN_DISPLAY_INV.get(b, b)` then fix.py:1022 `_BTN_DISPLAY.get(b, b)` — any token that is neither an exact display name nor exact canonical name passes through verbatim, including the codebase's own raw-event vocabulary 'left'/'right' (translator/keys.py MOUSE_BUTTONS maps exactly these) and common foreign forms ('Mouse4', 'LMB'). Executed probe on a synthetic v2 frames.csv: fix returned ok ('hygiene: stripped 0 tokens...'), buttons after fix = ['left','Mouse4','Left','Left','LMB',...], checker bad_btns…

**Fixed:** fix_key_hygiene + fix_v1_to_v2 canonicalize buttons through the full vocabulary; unmappable tokens dropped and counted

### #8 [MAJOR] pipeline/fix.py:1216 (fix-validate, refuters 0/2)

**FIX_SENTINELS cannot clear — or crashes on — the 'not float-formatted' FAIL it is mapped from: dotted non-conformant dx/dy cells survive verbatim, dotless non-numeric cells raise ValueError**

The checker's conformance test is _FLOAT_RE = ^-?\d+\.\d+$ (translator/v2.py:480, applied at 758-760, FAIL at 807-809). validate.py:77 maps it to STR_SENTINELS fixable=True; plan_fixes (fix.py:194-204, no sidecars) plans FIX_SENTINELS. But fix_sentinels' repair heuristic (fix.py:1209-1218) only rewrites cells in ('', '0') or with no '.' in them: any cell that CONTAINS a dot but fails the regex is left untouched, and any dotless non-numeric cell hits `f"{float(v):.1f}"` (fix.py:1216-1217) and raises an uncaught ValueError. Executed probe: after fix_sentinels returned ok ('sentinels normalized in 0 cells (float 0.0)'), dx column was still ['.5', '1.', '+1.0', '1.2e3', '3.7'] and the checker's bad_float test re-flagged ['.5','1.','+1.0','1.2e3']; a second probe with dx='abc' raised `ValueError: could not convert string to float: 'abc'`, which apply_fixes (fix.py:399-401) classifies session-…

**Fixed:** fix_sentinels judges cells with the checker's own _FLOAT_RE; guarded parse (non-finite/unparseable -> 0.0)

### #9 [MAJOR] pipeline/fix.py:991 (fix-validate, refuters 0/2)

**FIX_KEY_HYGIENE keeps unbound keys, so INP_KEYS_NO_ACTION re-fires for keys-without-actions rows it was planned to clear**

The checker FAILs 'N frames have input_keys but null input_actions' (translator/v2.py:743-744, 763-764); validate.py:85 maps it to INP_KEYS_NO_ACTION fixable=True and plan_fixes (fix.py:144-145, no sidecars) plans FIX_KEY_HYGIENE. The delivery invariant is that unbound keys are STRIPPED (_v2_rows, translator/v2.py:199-206: `if k in bound` else strip), but fix_key_hygiene keeps every token normalize_event_key returns (fix.py:991-998) — normalize_event_key (translator/keys.py:66-90) drops only control bytes, vk codes and OS/F-keys, keeping ordinary unbound keys like 'T'/'P'. resolve_actions then yields no action for them, so rows where such a key is the only input remain keys-with-null-actions. Executed probe (kamla bind: w/a/s/d/e/esc/mouse): 4 rows with input_keys='T', empty actions, dx/dy='0.0' — after fix_key_hygiene returned ok, rows were still [('T',''),...] and the checker's keys_no…

**Fixed:** fix_key_hygiene strips unbound keys when a keybind exists (mirrors _v2_rows), counted in stripped

### #10 [MINOR] translator/v2.py:843 (translator, refuters 0/2)

**Unguarded sync.motion_track() in check_session_v2 turns an opencv open-failure into a terminal QUARANTINE instead of a typed reject/WARN**

In check_session_v2 the controls-to-video sync block calls sync.motion_track(session_dir / "video.mp4") at v2.py:843 with NO try/except anywhere in the function around it (the function body from 627 onward has only local per-read try blocks; lines 842-866 are at top level). sync.motion_track (sync.py:73-75) does `cap = cv2.VideoCapture(...); if not cap.isOpened(): raise ValueError(f"could not open video: {video_path}")`. That ValueError propagates: analyze() calls `r = check_session_v2(...)` at analyze_sample.py:1332 unguarded; validate_session calls `analysis = eng.analyze(...)` at validate.py:832 unguarded; run.py:166-180 `_validate_worker` classifies kind="host" ONLY for (OSError, MemoryError, sqlite3.OperationalError) — a ValueError is kind="crash"; run.py:329-332 then does `ledger.set_state(sid, "QUARANTINED", "validation crashed: ...")`, which is terminal (media held ~48h, manual q…

**Fixed:** motion_track guarded at all four sites: qa WARN / translate+retranslate skip-with-trail / FIX_LAGSHIFT_CSV typed FixFailed

### #11 [MAJOR] pipeline/reports.py:627 (ops-tools, refuters 0/2)

**Paid-piece memory is id-keyed only — an unsplit re-delivery silently double-pays already-paid footage**

Ruling C's memory consult matches ONLY exact session ids: build_sheet_rows line 626-627 (`mem = paid_mem.get(root["session_id"]) or {}` / `if n["session_id"] in mem:`) and _tree_has_uncounted_accepted line 433 skip/flag a re-delivered node only when its id equals a recorded piece's id; tools/recal_refix_reset.py line 367 records pieces by child id (e.g. `R-p1`). When the refix re-run delivers the SAME footage under a DIFFERENT id — the whole ROOT delivering unsplit, or a segment re-split into nested `-p1-p1` ids — no memory row matches, so the node is counted in full with no AMBIGUOUS line. Probe (scratch ledger, exact tool SQL + real build_sheet_rows): root SPLIT with p1 DELIVERED 1700s accepted-stamped + p2 fix-failed; refix capture+teardown recorded `{'R-p1': 1700.0}`; re-run set the root itself DELIVERED at 3400s (contains the paid 1700s). Output: `SHEET rows: [... "kamla_accepted_hr…

**Fixed:** orphaned paid-piece memory: not-in-memory DELIVERED nodes of such trees excluded LOUDLY, no stamp, root keeps re-entering (build_sheet_rows + _tree_has_uncounted_accepted)

### #12 [MAJOR] pipeline/validate.py:865 (tests-coverage, refuters 0/2)

**D2a's production wiring (probed_duration_s into aux) survives deletion — suite stays 623 green**

The whole point of D2a (commit 3199091, r9 #12 MAJOR) is that validate_session threads the ffprobe duration into aux so map_reasons' CNT_SHORT judges the real video length instead of session.json's claim. The only production wiring is pipeline/validate.py:865 `aux["probed_duration_s"] = probed_info.duration_s`. All three tests for D2a (test_r_loop9.py:548-574) call map_reasons directly with a HAND-BUILT aux (`aux(probed_duration_s=120.0)`), never through validate_session; grep confirms no other test references probed_duration_s. Mutation proof in a scratch clone: replacing line 865 with a comment, then `PYTHONPATH=. uv run --with pytest --with rerun-sdk --with numpy pytest pipeline/tests translator/tests -q` -> `623 passed in 67.24s` — identical to the unmutated baseline (623 passed, floor 619). The arming gate (tools/run_suite.sh) cannot detect loss of the entire fix.

**Fixed:** e2e validate_session test pins the aux wiring (claimed-45/real-100 and the inverse)

### #13 [MAJOR] pipeline/run.py:1010 (tests-coverage, refuters 1/2)

**D5b's accepted-side md5 skip is unpinned; its named test passes for the wrong reason (uploaded column masks it)**

In _resume_daily_send (commit fe91bf7), the accepted_keep loop at pipeline/run.py:1010-1018 skips re-stamping accepted marks for sids whose md5 changed under the record. Mutation proof: changing line 1012 to `if False and _bytes_changed(sid):` -> full suite `623 passed in 67.08s`. test_resume_skips_superseded_sid_and_new_bytes_count_once appears to cover exactly this scenario but only asserts uploaded_reported_at and `b"p@x.com" in docs[-1]` — my probe (probe_e.py in scratch) shows why it stays green under the mutation: the resume prints '[daily] resume: re-stamped 1 accepted node(s)', accepted_reported_at is re-stamped on the superseded root, and the D+1 sheet row becomes `...,p@x.com,1.0,0.0,0.0,0.0,...` — kamla_hrs_uploaded 1.0 satisfies the substring assertion while kamla_accepted_hrs is 0.0. Against unmutated code the same probe yields `...,1.0,0.0,1.0,0.0,...` (accepted hours land)…

**Fixed:** named test extended: accepted mark asserted None post-resume + column-precise kamla_accepted_hrs

### #14 [MAJOR] tools/analyze_sample.py:1371 (tests-coverage, refuters 0/2)

**D2b's producer half (OSError -> error_kind='host' in analyze) survives deletion — the pinning test stubs the seam**

D2b (commit 3199091, r9 #15) is a two-half fix: analyze() classifies an inventory-read OSError as error_kind='host' (tools/analyze_sample.py:1370-1371), and validate_session re-raises OSError when it sees that kind (pipeline/validate.py:846-847) so run's host carve-out cools down instead of quarantining. The only test, test_engine_oserror_is_host_classed_through_validate, monkeypatches valmod._ENGINE with a fabricated `_A` object that already carries error_kind='host' — it pins ONLY the consumer half. Mutation proof: deleting the two producer lines in the scratch clone -> full suite `623 passed in 67.23s`. The other real-engine test (empty frames.csv) exercises the different branch that returns with error=='' when typed FAILs exist, so it cannot see this either.

**Fixed:** real-analyze test pins error_kind='host' (stubbed checker + chmod-000 frames.csv)

### #15 [MINOR] pipeline/run.py:538 (tests-coverage, refuters 0/2)

**D4b's batch-driver adoption gate-propagate call survives deletion — only the continuous path is pinned**

Commit c5a145c adds _propagate_gate_record calls to BOTH mid-split crash-adoption paths (run._fix_phase done-branch at pipeline/run.py:538-544, and continuous._fixing_triage at continuous.py:853-861). Only the continuous path has a test (test_adoption_propagates_the_gate_record drives drv._fixing_triage). Mutation proof: deleting the entire try/except block at run.py:538-544 in the scratch clone -> full suite `623 passed in 67.14s`.

**Fixed:** batch-driver adoption propagation test (_validate_phase no-op)

### #16 [MINOR] pipeline/fix.py:846 (tests-coverage, refuters 0/2)

**D1's non-dict game_info guard in retranslate_from_sidecars has no test**

Commit 640651a adds `if not isinstance(game_info, dict): game_info = {}` at pipeline/fix.py:846-847 (noted in the commit message as a deviation: 'same provenance' as the tested exe_name guard). No test covers it: mutation proof — reverting the two lines in the scratch clone -> full suite `623 passed in 67.26s`. The tested guards in the same commit (numeric exe_name/session_id, falsy key) all have dedicated tests; this one shape does not.

**Fixed:** string game_info retranslate test
