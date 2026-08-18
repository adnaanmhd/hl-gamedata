# R11_FINDINGS.md — review iteration 11, findings of record

**Run 2026-08-19** by the r-loop-9/10 executor session via the workflow
snapshot `tools/review/flip-review-iter11.js` (RULED by Adnaan 2026-08-19:
a FULL DEEP review of the whole codebase PLUS the hunt for regressions from
the iteration-10 fixes, run REGARDLESS of iteration 10's quiet judgment).
7 finder lanes, 2-vote refute, 47 agents (45 completed), HEAD 1500d95
(code HEAD 6dd2e64).

**Verdict: NOT QUIET — 20 raised → 20 confirmed (1 BLOCKER), 0 killed.**
The blocker (#1≡#14≡#16, found independently by THREE lanes) is a
regression from the iteration-10 wedge fix — Adnaan's order to run this
pass was vindicated. Iteration 11 can NEVER judge quiet (a confirmed
blocker disqualifies quiet outright per R5_TRIAGE §7).

**⚠ Degraded-vote caveat:** two refuter agents (both
`refute:tests-coverage:exec`) died on a usage-credit exhaustion mid-run,
so #19 carries 1/1 votes (its single completed refuter voted REFUTE —
check its evidence in r11-results.json before acting) and #20 carries 0/1.
Both are minor test-coverage items; every other finding has the full 2-vote
verdict (all 0/2 or 1/2 — survives).

**Known duplicates (same defect, multiple lanes):** #1 ≡ #14 ≡ #16 (wedged
TODAY regenerated), #2 ≈ #13 (orphan-void keyed on id presence, not
DELIVERED presence; #20's silent-void half is the same mechanism), #4 ≡ #11
(hygiene judges the built-in keybind, not the session's), #5 ≡ #8 ≡ #10
(wedge alert one-shot / transient-OSError wedge).

**The fix specifications of record live in `R8_IMPLEMENTATION_PLAN.md` §11
(F1–F11)** — written after reading all 20 claims together; they supersede
the per-finding `fix` proposals below where they differ. Machine-readable
results incl. both refuter verdicts: session scratchpad `r11-results.json`.

## Confirmed findings

### #1 [BLOCKER] pipeline/run.py:1107 (regressions-r10, refuters 0/2)

**A wedged TODAY is freshly regenerated post-stamp on the very next tick, overwriting the payment CSV and counted record**

6dd2e64 changed the permanent-refusal outcome from 'return False forever' to 'write .wedged and skip with continue' (run.py:1101-1107). But the fresh-generation path below the scan (run.py:1121-1124) guards only on `.sent` — it never checks for an existing `.daily-counted.json` or `.wedged`. So when the wedged day IS today (the common case: a send interrupted at 14:00 IST wedges on the 14:10 retry), the next tick continues past the wedge, reaches the fresh path, and REGENERATES: write_payment_sheet overwrites reports/<day>/payment-<day>.csv (reports.py:756-760), os.replace overwrites the counted record (run.py:1186), and the regenerated sheet is sent as the payment document — violating the 'NEVER regenerate post-stamp' r-loop-8 BLOCKER doctrine stated in _resume_daily_send's own docstring (run.py:959). Pre-commit, the permanent refusal starved later days but never regenerated. Probe (real output, both wedge variants): tick1 'WEDGED day 2026-08-19 skipped' then tick2 same day 'returned True; .sent exists: True; csv overwritten: True (186-byte header-only sheet); record overwritten: Tr…

**Finder's proposed fix** (spec of record: plan §11): Before opening a fresh day (run.py:1121), refuse when today's dir already holds a pending state: `if (cfg.reports_dir/day/'.wedged').exists() or record.exists(): return False` — a wedged or recorded-but-unsent today must stay parked for the human, exactly like a past day; only the absence of both may enter the fresh-generation path.

### #2 [MAJOR] pipeline/reports.py:645 (regressions-r10, refuters 0/2)

**The orphan-void (r10 #11) never fires when a recorded paid id reappears in the re-cut tree — deterministic cutter ids make silent double-pay the norm, not the exception**

The void condition is `orphaned = sorted(pid for pid in mem if pid not in tree_ids)` (reports.py:645) — it fires only when a recorded id is ABSENT from the tree. But cutter ids are deterministic (-p1, -p2 by segment index; the same commit's #5 comment says so), so ANY re-cut with >=1 segment re-creates R-p1 and the recorded id is present again. Two proven variants (real probe output): (1) re-cut same level — gen-1 paid piece R-p1=1700s; re-run cuts R-p1(900s)+R-p2(800s): p1 prints AMBIGUOUS and is withheld, but orphaned=[] so R-p2 is counted IN FULL and stamped — probe: 'kamla_accepted_hrs': 0.22, 'R-p2 stamped accepted_reported_at: 2026-08-18T19:11:44+00:00', no line mentions p2. (2) nested re-split ('-p1-p1 nesting', the EXACT case the code comment at reports.py:639-640 claims this check covers) — R-p1 exists again but as a SPLIT node, which the walk never compares against memory (SPLIT hits neither the DELIVERED nor REJECTED branch), so the grandchildren covering the same 1700s are counted in full: probe printed 'stderr lines about this tree: NONE; kamla_accepted_hrs: 0.47; grandc…

**Finder's proposed fix** (spec of record: plan §11): Void the tree's not-in-memory matches whenever ANY memory row fails to reconcile, not only when an id is absent: treat (a) a recorded id matching a non-DELIVERED node (SPLIT/REJECTED/missing) and (b) a seconds-mismatched AMBIGUOUS match as the same tree-level void the orphan case gets — both prove the cut changed, which is the entire premise of the exclusion.

### #3 [MAJOR] pipeline/fix.py:1149 (regressions-r10, refuters 0/2)

**fix_lagshift_csv's new `except Exception` re-types host-class failures (MemoryError/OSError) as session-kind FixFailed, regressing the r-loop-7 host carve-out into terminal rejects**

6dd2e64 wrapped sync.motion_track in `except Exception: raise FixFailed(...)` (fix.py:1147-1153). FixFailed is not in apply_fixes' host tuple (fix.py:417-419: OSError, MemoryError, sqlite3.OperationalError, TimeoutExpired), so a host-class error raised inside motion_track — realistically MemoryError: the function decodes up to MAX_ANALYSIS_FRAMES via cv2 and allocates per-frame numpy/Farneback flow fields (translator/sync.py:68-97), on the same host whose OOM bursts r-loop 9 #9 documented — is converted from kind='host' (attempt refunded, cooldown, retry) to kind='session' (attempt burned, REVALIDATING 'fix failed'). A/B probe, real output — PRE-FIX 567c3e8: "error = FIX_LAGSHIFT_CSV: MemoryError: Unable to allocate 1.9 GiB...; kind = host (attempt refunded, retried)"; HEAD: "error = FIX_LAGSHIFT_CSV: FixFailed: lag shift cannot measure: video not decodable by opencv (MemoryError); kind = session (attempt BURNED as a session fault)". The motivating #10 error (ValueError 'could not open video') was already session-kind pre-fix — the guard only needed to add the typed message, but the …

**Finder's proposed fix** (spec of record: plan §11): Narrow the guard to decode-class failures and let host classes propagate to the classifier: `except (OSError, MemoryError, sqlite3.OperationalError, subprocess.TimeoutExpired): raise` before the generic `except Exception as e: raise FixFailed(...)` — same net message for genuine opencv decode failures, host refund preserved for infrastructure errors.

### #4 [MINOR] pipeline/fix.py:1041 (regressions-r10, refuters 0/2)

**The unbound-key strip (r10 #9) judges against the BUILT-IN keybind only, deleting keypresses bound by the session's own authoritative keybind.json**

fix_key_hygiene builds `bound` exclusively from KEYBINDS[game]+patches (fix.py:1008-1011) and now deletes every key outside it (fix.py:1041-1043). But translator/keybinds.py's own docstring rules that per-session keybind.json 'is authoritative' with built-ins only a fallback, and the pipeline's own FIX_RETRANSLATE honors raw/keybind.json (fix.py:910-912) — while hygiene ignores the same file sitting in the same work dir. A/B probe (kamla session whose raw/keybind.json adds 'sprint': 'shift_l'; frames.csv carries 6 LShift-press rows plus one stray Cmd row that makes hygiene plannable via INP_OSKEYS): PRE-FIX 567c3e8: 'stripped 1 tokens... rows still carrying LShift: 6/6' — the keypresses survive and the action mismatch stays visible/FAILable; HEAD: 'stripped 7 tokens... rows still carrying LShift: 0/6' — all six real keypresses deleted, lumped invisibly into the OS-key strip count, and the session now passes the checker cleanly.

**Finder's proposed fix** (spec of record: plan §11): Build `bound` from the session's own binding when one exists — merge bound_literals(resolve_keybind(raw/keybind.json,...)) into the built-in set before stripping (mirroring retranslate_from_sidecars fix.py:910), and only strip keys unbound under the UNION; alternatively route hygiene of has-raw sessions through FIX_RETRANSLATE, which already gets this right.

### #5 [MINOR] pipeline/run.py:951 (regressions-r10, refuters 0/2)

**The wedge's one-and-only Telegram alert is never retried — a TelegramError at wedge time permanently silences a condition that needs a human**

_wedge_day sends its alert exactly once and swallows TelegramError with only a stderr print (run.py:946-952, '[daily-wedge-alert-undelivered]'). Every subsequent tick takes the `.wedged` skip branch, which also only prints to stderr (run.py:1104-1106). There is no retry path anywhere: once the single send fails, the Telegram channel — the operator's actual interface (per-batch reports, daily sheets, and every other alert go there) — never hears about the wedged day. This inverts the codebase's own alert contract: AlertBook retracts stamps on failed sends so alerts retry (item 18), and the daily message itself retries on TelegramError every tick. Item 30 of the ruling states directly: 'a wedge that silences instead of alerting IS a finding'. The inverse failure also exists: if the .wedged WRITE fails (OSError, run.py:943-945) while Telegram works, every 600s tick re-runs the refusing resume and re-alerts — unbounded alert spam for one condition.

**Finder's proposed fix** (spec of record: plan §11): Make the wedge alert durable like the daily message: only stamp an `.wedged-alerted` marker (or a field in .wedged) after telegram.send_message succeeds, and have the scan's wedge-skip branch re-attempt the alert when the marker is absent — one successful send, then silence; also skip _wedge_day's alert (not the file write) when .wedged already exists to close the write-failure spam path.

### #6 [MAJOR] pipeline/reports.py:577 (payment-split, refuters 0/2)

**DELIVERED hours under a duration_raw_s=NULL root are silently lost from every sheet once its cohort window passes**

The re-entry lattice in build_sheet_rows has one unreachable cell. A root whose duration_raw_s is NULL is never added to counted_out (line 614-615 requires `r_countable(root) or root["state"] == "REJECTED"`), so it is never uploaded-stamped. After its cohort window passes: `late` (lines 557-559) requires the same countable-or-REJECTED test -> False; `accepted_due` (lines 576-580) requires `bool(root["uploaded_reported_at"])` -> False; line 581 `if not in_window and not late and not accepted_due: continue` then skips the tree on every future sheet, so DELIVERED/REJECTED nodes that settle after the window can never be counted. Not even the PENDING-COHORT warning fires post-window, and pre-window it sums duration_raw_s (NULL -> 0.0), so the loss is fully silent. duration_raw_s for a root is written ONLY at ingest.py:943-945 (`dur = _probe_duration(...); if dur: ledger.update(...)`) — a single, unlogged, unretried ffprobe call that swallows TimeoutExpired(120s)/ValueError/rc!=0 (ingest.py:950-959) while the session proceeds to INGESTED and delivers (validate re-probes for gates per D2 bu…

**Finder's proposed fix** (spec of record: plan §11): Two independent one-liners, either sufficient: (a) persist the validate-time probed duration into the ledger when the root's duration_raw_s is NULL (validate.py already computes aux["probed_duration_s"] at line 865 — the D2 truth source), which restores countability so the existing late guard pays uploads AND accepted exactly once; and/or (b) close the lattice in reports.py: add a third re-entry arm for an unstamped, unsealed root with `up < hi_dt` whose tree has uncounted accepted nodes (reuse _tree_has_uncounted_accepted without the uploaded-stamp gate when `not r_countable(root)`), which wa…

### #7 [MAJOR] pipeline/run.py:1208 (payment-split, refuters 0/2)

**Supersede/heal landing between sheet build and the stamps re-stamps the reset slot — the corrected re-upload's hours become permanently unreachable**

The fresh daily-send path stamps unconditionally: run.py:1208 `mark_uploads_reported(..., sids=counted)` and :1214 `mark_accepted_reported(ledger, accepted)` resolve to `UPDATE sessions SET ...=? WHERE session_id=?` (ledger.py:183-186) with no state or md5 guard. These run in the housekeeping thread (continuous.py:1593, hl-H) while the hl-S scan thread concurrently runs ingest.scan — which calls ledger.supersede (ingest.py:398/458; clears uploaded_reported_at/accepted_reported_at/duration_raw_s, ledger.py:259-266) and the different-md5 quarantine heal (ingest.py:290-307, same clears). No lock serializes them: intake_lock covers only 'scan pass vs D pick+claim' (continuous.py:258), and the daily send never takes it. The stamp window spans the Telegram message send (continuous.py's own comment: up to three 60s urlopen timeouts per tick). If a counted REJECTED/QUARANTINED root is superseded inside that window, the stamps land on the reset slot: the re-upload's uploaded hours are excluded forever by the `not root["uploaded_reported_at"]` guards (reports.py:548, 559) and, when it re-deliv…

**Finder's proposed fix** (spec of record: plan §11): Make the stamps compare-and-set on the bytes the sheet actually counted: have build_sheet_rows also emit {sid: md5_video} from its own row snapshot (it already reads the rows; add md5_video to the SELECT at reports.py:498), store THAT map in the durable record (replacing the post-build re-query at run.py:1183), and change mark_uploads_reported/mark_accepted_reported to stamp via `UPDATE sessions SET ...=? WHERE session_id=? AND md5_video=?` — a skipped stamp is exactly the existing resume-skip semantics ('the new hours stay countable') and needs only the same loud stderr line. This is conditio…

### #8 [MINOR] pipeline/run.py:942 (payment-split, refuters 0/2)

**_wedge_day writes .wedged before its only alert attempt — a TelegramError there permanently silences the wedged day**

_wedge_day (run.py:933-952) first writes `.wedged` (line 942) and only then attempts the single Telegram alert; a TelegramError is swallowed with a stderr print (line 951-952). Once `.wedged` exists, every future tick's day-agnostic scan skips the day with a stderr-only line (run.py:1101-1107 `continue`) — the alert is never retried, and pending_daily_send keeps returning the day so the recal tools also refuse (reports.py:398-409) with an operator hint ('let the driver finish the resume') that can never come true for a wedged day. Item 30 of the accepted list names this exact class a finding: 'a wedge that silences instead of alerting'. The wedge-alert coupling was introduced by 6dd2e64 (the old missing-CSV path re-attempted its alert on every tick because nothing was ever marked; the new one-shot trades that retry away). Verified by running the real code (probe3_resume.py): the wedge fires loudly when Telegram works ('⚠️ daily send for 2026-08-01 is WEDGED...' sent once, later days proceed) — and by reading the code path there is no second send site for a day whose .wedged already e…

**Finder's proposed fix** (spec of record: plan §11): Invert the coupling: attempt the alert first and write .wedged only after telegram.send_message succeeds (a failed alert leaves the day pending, so the next tick retries both — the refusal itself is already idempotent and cheap); or keep the early .wedged write but re-send the alert on an hourly cadence while any .wedged day exists (the same cooldown+hourly-alert contract HOLD_VLM and the upload lane already use, per accepted item 11).

### #9 [MINOR] pipeline/run.py:1859 (driver-core, refuters 0/2)

**DISCOVERED-media reclaim anchor crosses supersede/heal generations: 12h grace collapses to ~0 for re-entered slots**

The hourly H-lane sweep (_sweep_terminal_work, run.py:1858-1867) ages a media-holding DISCOVERED row from 'MIN(ts) of DISCOVERED events after MIN(ts) of DOWNLOADING events' — the first DOWNLOADING EVER, across generations. ledger.supersede (ledger.py:249-284) and the quarantine-heal (ingest.py:315) re-enter a slot as DISCOVERED without deleting events, so for any re-entered slot whose earlier generation had a failed or even just a completed download, the anchor lands on a stale gen-1 event instead of the current generation's first failure. Probe against the real functions (real ledger, real sweep): gen-1 failure 29h ago, supersede 1h ago, gen-2 transient failure 25 min ago leaving a partial — output: 'sweep anchor first_disc = 2026-08-17T14:16:51+00:00 (gen-1 failure)', 'anchor age hours = 29.0', 'partial survives sweep?  False'. The digest's _stuck_lines disc_media query (continuous.py:1346-1353) uses the same anchor shape and printed 'DISCOVERED(media) 29.0h' for the 25-minute-old failure. The comment block's own invariant ('a row only returns to DISCOVERED holding media from a FAI…

**Finder's proposed fix** (spec of record: plan §11): Scope both anchors to the CURRENT intake stint, the same pattern _stuck_lines already uses for HOLD/READY/FIX: inner anchor = MIN(ts) of DOWNLOADING events with ts > COALESCE(MAX(ts) of events whose to_state is NOT IN ('DISCOVERED','DOWNLOADING'), '') — for never-successful rows this is unchanged (no outside events), while supersede/heal re-entries (whose last outside event is the REJECTED/QUARANTINED exit) anchor on the new generation's first claim; then first_disc = MIN DISCOVERED after that. Apply to run.py:1858-1863 and continuous.py:1346-1353.

### #10 [MINOR] pipeline/run.py:979 (driver-core, refuters 0/2)

**Daily wedge (r-loop 10 #2) can fire on a transient OSError and its one Telegram alert is never retried — a silent permanent payment-sheet stop**

Two defects in the new wedge mechanism, both classes explicitly called out as findings in the ruling that accepted it. (a) run.py:979 gates the wedge on `csv_path.exists()`: pathlib's Path.exists() swallows OSError and returns False, so a transient host error at that stat (EMFILE under the driver's fd pressure from spawn workers/ffmpeg/rclone children, or an EIO blip) reads as 'payment CSV missing' and _wedge_day writes reports/<day>/.wedged — a PERMANENT wedge (human must rm it) for a condition that would have passed on the next 600s retry tick; the record-read `except (OSError, ...)` at run.py:973 has the same transient-OSError-to-permanent-wedge conversion. (b) _wedge_day (run.py:946-952) sends its Telegram alert exactly once with `except TelegramError: print(...)` — no AlertBook, no stamp-retract, no retry; the day-agnostic scan thereafter skips the wedged day with stderr prints only (run.py:1101-1107). Every other alert surface in this codebase retries (AlertBook retracts failed stamps, digest holds its anchor unwritten, daily sends return False and retry); this is the one path …

**Finder's proposed fix** (spec of record: plan §11): (a) Treat stat-level uncertainty as transient: replace `csv_path.exists()` with an explicit os.stat in try/except where FileNotFoundError wedges but any other OSError prints and returns False (retry next tick); same split for the record read at run.py:968-977 (JSONDecodeError/KeyError wedge, bare OSError retries). (b) Make the wedge alert durable: have the day-agnostic scan's wedged-day skip (run.py:1101-1107) re-raise through the driver's AlertBook (TTL-deduped to ~1/h, stamp-retracted on failure) — or at minimum record alert-failed in .wedged and retry the send on each skip until one deliver…

### #11 [MAJOR] pipeline/fix.py:1042 (fix-validate, refuters 0/2)

**fix_key_hygiene judges 'bound' against the BUILT-IN keybind, so it deletes every key a session's authoritative raw/keybind.json actually binds — silently corrupting delivered data or wrongfully rejecting**

fix_key_hygiene builds its binding authority from the built-ins only — fix.py:1008-1011 `kb = dict(KEYBINDS.get(game, {})); kb.update(KEYBIND_PATCHES...); bound = bound_literals(kb)` — and never reads raw/keybind.json, even though translator/keybinds.py's own docstring rules the per-session keybind.json 'authoritative' and every other binding consumer (translate_bundle_v2, retranslate_from_sidecars via resolve_keybind at fix.py:910-912) honors it. Three strips then run against that wrong binding: (a) fix.py:1023 normalize_event_key(bound=built-in, aggressive=True) drops custom-bound F/OS keys; (b) the r-loop-10 #9 strip at fix.py:1040-1042 (`unbound = {t for t in kset if t not in bound}; kset -= unbound`) — commit 6dd2e64, claimed to 'mirror _v2_rows' delivery invariant', but _v2_rows strips against the session's RESOLVED keybind (custom included) while this mirrors only the built-in — deletes every remaining custom-bound key; (c) fix.py:1072 re-resolves input_actions with the built-in resolver, erasing the custom binds' actions. The trigger is systematic, not exotic: the engine flag…

**Finder's proposed fix** (spec of record: plan §11): In fix_key_hygiene (and the INP_OSKEYS trigger), use the session's authoritative binding: when work/raw/keybind.json exists, build kb via resolve_keybind(keybind_path=raw/'keybind.json', game_name=..., exe_name=...) exactly as retranslate_from_sidecars does (then kb.update(KEYBIND_PATCHES) as today), falling back to the built-in only when no sidecar keybind exists — so `bound`, the normalize_event_key exemption, the #9 unbound strip, and the action re-resolution all judge against the binding the CSV was actually translated under. Alternatively (or additionally), make the engine's os_keys inven…

### #12 [MAJOR] translator/v2.py:315 (translator, refuters 0/2)

**Untrusted metadata.json session_id is joined into the output path unsanitized — path traversal / arbitrary file write on the pipeline VM**

translate_bundle_v2 reads session_id straight from the player-supplied metadata.json (v2.py:307) and the only guard (v2.py:308) checks isinstance str + non-empty — it does NOT reject path separators or '..'. That value is then used directly in Path(out_root)/VENDOR/date/slug/session_id (v2.py:315) followed by out_dir.mkdir(parents=True) and writes of video.mp4/frames.csv/session.json/rrd_creation.py. Proven with a real probe: a bundle whose metadata.json set session_id='../../../../ESCAPED_dir' produced out_dir='.../scratchpad/out/humynlabs/08-18-2026/kamla/../../../../ESCAPED_dir' and the four delivery files were written to '.../scratchpad/ESCAPED_dir/' — four directory levels OUTSIDE the intended out/ tree. The r-loop-9 D1 guard (commit 640651a) added at line 308 was meant to make session_id safe but only closed the non-str crash, leaving the string traversal open. translate.py:247 (v1 translate_bundle) has the identical unsanitized join.

**Finder's proposed fix** (spec of record: plan §11): Before the join, constrain session_id to a single safe path component: reject or replace it when it contains os.sep/'/'/'\\' or '..' (or validate against ^[A-Za-z0-9._-]+$), falling back to bundle_dir.name exactly as the empty/non-str case already does. Apply the same at translate.py:247. Optionally assert out_dir.resolve() is within out_root.resolve() after construction as a defense-in-depth check.

### #13 [MAJOR] pipeline/reports.py:645 (ops-tools, refuters 0/2)

**Paid-piece memory: a paid segment re-split deeper is double-paid silently (orphan void keyed on id presence, not DELIVERED presence)**

The r-loop-10 #11 orphan void (6dd2e64) computes orphaned = ids in paid memory absent from tree_ids (reports.py:629-645, mirrored at :429-436 in _tree_has_uncounted_accepted). But cutter child ids are deterministic ({sid}-p<n>, grandchildren {sid}-p1-p1 — run.py:351-352), so ANY re-split of the root re-creates the recorded paid id as a row. If that re-created segment is itself further split (state SPLIT), the id IS in tree_ids (orphan never fires) yet is never visited by the DELIVERED-branch mem match (:657-679), so its grandchildren — carrying the already-paid footage — are counted at :680 and stamped via accepted_out. Probe against the real modules (scratch ledger; paid memory (r1, r1-p1, 300s); re-run tree r1->r1-p1[SPLIT]->{r1-p1-p1 150s, r1-p1-p2 140s}, plus recovered r1-p2 200s): output 'kamla_accepted_hrs': 0.14 (= (150+140+200)/3600), accepted_out ['r1-p2','r1-p1-p2','r1-p1-p1'], stderr '(none)' for that tree — no ORPHANED/AMBIGUOUS line, and the stamps make it permanent so the root never re-enters. The control direction (paid grandchild, shallower re-delivery) correctly prin…

**Finder's proposed fix** (spec of record: plan §11): Void the id-keyed match against the tree's DELIVERED nodes, not all rows: in both build_sheet_rows (:629-645) and _tree_has_uncounted_accepted (:429-436), compute the void as 'any recorded paid id that is not currently a DELIVERED node of the tree' (a paid id now SPLIT/REJECTED/pending means its footage re-delivers under other ids). Then every not-in-memory DELIVERED node of such a tree is excluded loudly per the existing orphan branch, and the root keeps re-entering until a human reconciles.

### #14 [MAJOR] pipeline/run.py:1101 (ops-tools, refuters 0/2)

**A wedged TODAY falls through the scan-skip and is freshly REGENERATED post-stamp — record destroyed, stamped hours leave every sheet**

6dd2e64's wedge skip (run.py:1101-1107) `continue`s past a wedged day, but when the wedged day IS the current IST day the loop then falls through to the fresh path (:1121-1124), which regenerates the sheet and os.replace's the durable .daily-counted.json (:1162-1186) — the exact 'NEVER regenerate post-stamp' r-loop-8 BLOCKER the resume docstring (:957-967) forbids. Probe against the real send_daily_report_if_due (today's dir with a valid record counted=['rootA','rootB'], .wedged, no .sent; rootA already stamped by the interrupted original send): returned True; stderr shows '[daily] WEDGED day 2026-08-19 skipped' immediately followed by a full fresh send; record counted now ['rootB'] (forensic record overwritten); payment-2026-08-19.csv of record overwritten (original content gone); rootA's uploaded hours absent from the new sheet (stamped roots are excluded by build_sheet_rows:548) — so the only sheet that ever credited them was destroyed; .sent written, day settles under the standing .wedged. test_r_loop10.py:107 pins only the NEXT-day case (send + timedelta(days=1)); the same-day c…

**Finder's proposed fix** (spec of record: plan §11): After the day-agnostic scan, refuse the fresh path whenever today's .daily-counted.json exists: a wedged today must return False exactly like any wedged day (its record's existence alone means fresh generation is forbidden). Separately consider not wedging on transient OSError classes when reading the record (retry next tick; wedge only on parse failure/ENOENT).

### #15 [MINOR] tools/recal_refix_reset.py:141 (ops-tools, refuters 0/2)

**The teardown interlock is blind to the regen's own resumable send (.regen-v2-counted.json) — a teardown in step 7.2's crash window re-creates the stale-sheet/double-count class**

recal_refix_reset (:141-152) and recal_rebuild_reset (:47-59) refuse to tear rows down only while reports.pending_daily_send finds a pending .daily-counted.json (reports.py:386-409). But tools/recal_regen_sheets.py --send maintains its own durable resumable-send record with the same semantics: .regen-v2-counted.json is written at :385 BEFORE telegram (:394-401) and stamps (:402-404), and a re-run resumes from it verbatim (:347-354), re-sending the stored CSV and blind-stamping the recorded sids (mark_uploads_reported's sids path, reports.py:322-325, is a no-op ledger.update for deleted rows). Neither reset tool checks it, so in the window between a killed/aborted regen --send and its re-run, both teardown tools run clean (lock free, pending_daily_send None). Verified by reading all three tools end-to-end: the only regen-record consumers are the regen itself and its stray-stamp gate.

**Finder's proposed fix** (spec of record: plan §11): Extend the interlock: treat a reports/<day>/.regen-v2-counted.json without its .regen-v2-done as a pending send — either inside pending_daily_send or as a second check in both reset tools — refusing with the same 'let the send finish (re-run recal_regen_sheets --send) or reconcile by hand' shape.

### #16 [MAJOR] pipeline/run.py:1121 (tests-coverage, refuters 0/2)

**A wedged TODAY is regenerated, overwritten, and re-sent on the very next tick**

The r10 #2 wedge is honored only inside the day-agnostic resume scan (run.py:1104-1110 'WEDGED day ... skipped' + continue). The fresh-generation path that follows (run.py:1121 'marker = cfg.reports_dir / day / ".sent"' onward) never checks .wedged and never checks whether a .daily-counted.json already exists for today — write_payment_sheet overwrites payment-<day>.csv and os.replace overwrites the counted record (run.py:1162-1187). PROVED by probe on unmutated HEAD (scratch copy, test_probe_wedge.py, real output): after today's send wedges via the deleted-counted-row path (the exact mechanism the shipped test test_wedged_day_does_not_starve_later_dailies pins for a PAST day), a tick 10 minutes later on the SAME day printed 'WEDGED skip printed: True' and then 'tick2 returned: True / .sent written for the wedged day: True / payment CSV overwritten: True / counted record overwritten: True / documents sent: 1 / new record counted list: []'. The shipped test only exercises the wedge on a day that is already yesterday; the common case — the wedge fires within minutes of the crash, same I…

**Finder's proposed fix** (spec of record: plan §11): In send_daily_report_if_due's fresh path, before generating for `day`: if (reports_dir/day/'.wedged').exists() or (reports_dir/day/'.daily-counted.json').exists(), print the WEDGED/pending line and return False (an existing record that the scan did not resume is by definition wedged or settled — never regenerate over it). Add a test: wedge today, tick again the same day, assert False + CSV/record byte-identical + no .sent.

### #17 [MAJOR] pipeline/fix.py:423 (tests-coverage, refuters 1/2)

**The r10 #1 exactly-once watermark is unpinned: a one-line regression doubles the gate record and the 641-test gate stays green**

Mutating the post-loop append at fix.py:423-424 from `_append_fixlog(dossier_dir, applied[persisted:])` back to `_append_fixlog(dossier_dir, applied)` — i.e. deleting only the watermark half of r-loop 10 fix #1 while keeping the pre-cut persist — leaves the full suite green at exactly the arming-gate count: real run in the scratch copy, '641 passed in 117.83s'. The two shipped tests pin only the durable-before-the-cut half (kill mid-cut) and the child-side single-inheritance (test_gate_record_not_double_propagated_on_success counts CHILD entries at propagate time, which happens BEFORE the post-loop append); nothing ever reads the PARENT fixlog after apply_fixes returns. Probe on the failed-cut path (FIX_GATE_WINDOW ok, FIX_CUT_SEGMENTS raises FixFailed): HEAD gives _gate_destroyed = {'actions': ['general_cancel'], 'key_frames': 3}; the mutated tree gives key_frames: 6 — the gate entry lands in the parent fixlog twice (once from the pre-cut persist, once from the full post-loop append).

**Finder's proposed fix** (spec of record: plan §11): Add a test that runs apply_fixes with a gate step followed by a FAILING cut step and asserts _gate_destroyed on the PARENT dossier equals the single-count inventory (key_frames 3, not 6), and that the parent fixlog holds exactly one ok FIX_GATE_WINDOW entry across all records; a second variant on the SUCCESS path asserting the same single-entry invariant after the post-loop append.

### #18 [MAJOR] translator/v2.py:350 (tests-coverage, refuters 0/2)

**r10 #10 shipped four guard sites but pinned only one — the translate, retranslate, and lagshift guards all survive deletion with the gate green**

Neutering all three untested motion_track guards at once (translator/v2.py:349-357 translate_bundle_v2, pipeline/fix.py:946-954 retranslate_from_sidecars, pipeline/fix.py:1147-1153 fix_lagshift_csv — each `except Exception` changed to `except ()` so the handler never fires, exactly reverting the guard's behavior) leaves the full suite green at the exact arming-gate count: real run in the scratch copy, '641 passed in 117.48s'. Only the check_session_v2 site (v2.py:855-864) has a test (test_undecodable_video_degrades_sync_check_to_warn). Grep proof: 'motion_track' appears in exactly one test file across pipeline/tests + translator/tests. The commit message's 'fail-first 13/13' cannot have covered these sites. sync.motion_track really raises ValueError on an unopenable video (translator/sync.py:75), and fix_translate_raw (fix.py:819) calls translate_bundle_v2 directly.

**Finder's proposed fix** (spec of record: plan §11): Three tests mirroring the existing check_session_v2 one: monkeypatch sync.motion_track to raise ValueError, then (a) translate_bundle_v2 completes with the 'lag correction skipped' warning in its report; (b) retranslate_from_sidecars returns the re-translated note with the skip trail; (c) fix_lagshift_csv raises FixFailed (not ValueError) so the failure stays typed.

### #19 [MINOR] pipeline/fix.py:1452 (tests-coverage, refuters 1/1)

**The fix_v1_to_v2 half of r10 #7 has no test — full revert to the pre-fix exact-name map keeps the suite green**

Replacing the whole fix_v1_to_v2 button-canonicalization block (fix.py:1447-1457) with the pre-fix code (`btns = [_BTN_DISPLAY.get(b, b) for b in (...).split("|") if b]`) leaves the suite green at 641 (same scratch run as the guard mutations, '641 passed'). Grep proof: neither 'fix_v1_to_v2' nor 'FIX_V1_TO_V2' appears anywhere under pipeline/tests or translator/tests — the only #7 test (test_key_hygiene_clears_foreign_button_tokens) exercises fix_key_hygiene, a different function. The commit message claims both functions were fixed and fail-first-proven; the v1 half was shipped with zero coverage.

**Finder's proposed fix** (spec of record: plan §11): Add a round-trip test like the fix_key_hygiene one but through fix_v1_to_v2: seed a v1 session with 'left'/'Mouse4'/'LMB' button cells, apply FIX_V1_TO_V2, assert the checker's mouse-button FAIL cannot re-fire and mappable tokens canonicalized rather than dropped.

**Disposition (F11 executor, 2026-08-19, per the degraded-vote caveat):**
the single completed refuter's evidence (r11-results.json) was read before
acting. It does NOT show fix_v1_to_v2 unreachable — the opposite: it
reproduced attempt-1 planning [FIX_V1_TO_V2, FIX_SESSIONJSON_RECOMPUTE]
on a real v1 payload. What it REFUTED is the harm claim: a fully
regressed v1 half still converts the session to v2, the foreign tokens
re-surface as INP_TOKEN_CASE (a DIFFERENT code — never an identical
re-fire), attempt 2 plans FIX_KEY_HYGIENE (the tested half) and the
session recovers within the 2-attempt budget. Net harm of a regression is
one wasted fix attempt/sweep, not a terminal reject. The zero-coverage
mechanism stands, so the pin was written anyway
(`test_v1_to_v2_canonicalizes_foreign_button_tokens`,
mutation-proved against the finder's exact revert); note the real
vocabulary maps 'left'→'Left' and DROPS 'Mouse4'/'LMB' (unmappable — the
r10 #7 design), so the test pins canonicalize-or-drop, not
canonicalize-everything.

### #20 [MINOR] pipeline/reports.py:657 (tests-coverage, refuters 0/1)

**Orphaned paid-piece memory goes silent when every surviving node id-matches memory; the re-entry void is also unpinned**

Two proofs on HEAD + one mutation. (1) Probe (test_probe_orphan.py, real output): memory holds {root-p1: 1700s, root-p2: 1600s}; the re-refixed tree contains only -p1 DELIVERED (id-identical — cutter ids are deterministic per r10 #5) because -p2's footage was dropped. Sheets W2 and W3 print ONLY '[sheet] paid-piece memory: ...-p1 (1700s) was paid before its refix teardown — not counted again' — no ORPHANED line ever, because the loud print at reports.py:657-663 is attached exclusively to not-in-mem DELIVERED nodes, and this tree has none; the orphan void at reports.py:429-436 meanwhile makes the root re-enter every future sheet, silently, forever. (2) Mutation: replacing 'orphaned = any(pid not in tree_ids for pid in mem)' (reports.py:436) with 'orphaned = False' keeps all 641 shipped tests green — the shipped orphan test's tree re-enters via its not-in-mem root regardless, so the void's behavior is never observed by the suite.

**Finder's proposed fix** (spec of record: plan §11): In build_sheet_rows, when `orphaned` is non-empty, print the ORPHANED reconcile line once per sheet for the ROOT (naming the orphaned ids) even when no not-in-mem DELIVERED node exists; pin both halves with tests — the all-matched tree stays loud on every sheet, and a mutation-style test asserting _tree_has_uncounted_accepted returns True for an all-matched orphaned tree (so the void itself is pinned).
