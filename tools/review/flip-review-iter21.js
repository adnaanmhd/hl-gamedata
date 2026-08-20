// r-loop-21 review workflow (RULED Adnaan 2026-08-20, mid-session message:
// iterations launch automatically, no headroom asks; iteration 21 reviews
// the r-loop-20 N fix set, which landed UNREVIEWED; if NOT quiet, its
// confirmed findings are fixed in-iteration (the O set) and iteration 22
// launches automatically — 22's results are THE CHECKPOINT; if 21 IS
// quiet, the independent e2e launches automatically). Edited from the
// committed iter20 snapshot: regressions lane retargeted at the
// ITERATION-20 N FIX SET (commits f6fa524 N1 stamp/trim resolution
// completed [falsiness-is-not-absence, destroyed-evidence refusal,
// pre-write emit, overflow disambiguation], 002004f N2 recompute overflow
// completion, dde54ab N3 labels-mark clear on the '' preserve arms
// [payment-surface], 44e72b3 N4 ''-means-unknowable + breadcrumb
// adjudication [payment-surface], 1d82472 N5 probe pins + floor f66d3ed —
// the executor's own, landed unreviewed, treat as prime target),
// accepted-behaviours 96-100 appended for the N fixes (mechanism defects
// stay reportable; 17/90/92/94 amended where the N fixes complete or
// close their mechanics), suite refreshed (840/floor 836). LOOP_START,
// 2-vote refute discipline and all 7 lanes kept.
export const meta = {
  name: 'flip-review-iter21',
  description: 'r-loop 21: adversarial review of the flip work incl. the UNREVIEWED r20 N fix set, 2-vote refute',
  phases: [
    { title: 'Find', detail: 'lane finders: whole-codebase + delta + regressions from the r-loop-20 N fixes' },
    { title: 'Refute', detail: '2 independent refuters per finding' },
  ],
}

const REPO = '/Users/adnaan/Documents/hl-projects/hl-gamedata'
const LOOP_START = '2244758'   // the tree r-loop 6 reviewed; HEAD adds its fixes

const ACCEPTED = `
ACCEPTED BEHAVIOURS — these are SETTLED. Do NOT report them. Reporting one
wastes the iteration and will be discarded:

1. r-loop-3 #15 (gate-blanked rows subsequently read as AFK, producing a
   larger cut): RULED IGNORED by Adnaan 2026-08-18. A DIFFERENT mechanism
   causing real harm is a finding on its own merits; "#15 again" is not.
2. The frozen-window trigger and gate ride the scanner-MEASURED span
   (aux["refined"]) rather than the VLM window, whenever the scanner
   produced one. RULED by Adnaan 2026-08-18. The VLM is the CLASSIFIER, not
   a boundary-finder. Widening GATE_PAD_FRAMES instead was explicitly
   REJECTED. When there is no refined span the VLM window IS the
   measurement and both fall back to it — that fallback is intended.
3. keep_span (the keep-vs-cut bar) deliberately stays max(refined,
   union-with-VLM-window). r-loop 3's separate fix for a 30s cutscene built
   from 3-4s held shots. NOT an inconsistency with (2).
4. On a cut-bearing plan, FIX_GATE_WINDOW is emitted before
   FIX_CUT_SEGMENTS while hygiene/context are still short-circuited. That
   asymmetry is deliberate and documented in PIPELINE_ARCHITECTURE.md.
5. run() declines with return 0 when C.PIPELINE_CONTINUOUS is True, and
   seven batch-driver test modules arm the flag via an autouse fixture.
6. CNT_ACTIONS_FEW / INP_KEYS_MISSING downgrade to an operator advisory
   when — and only when — restoring the inventory FIX_GATE_WINDOW destroyed
   would clear the bar. ACCEPTED CONSEQUENCE: such a session ships with
   fewer than MIN_DISTINCT_ACTIONS distinct actions. A DEFECT IN THE
   MECHANISM is still reportable.
7. RULED 2026-08-18 (the payment stamp split, HEAD commit b851ea2):
   uploaded_reported_at now means ONLY "uploaded hours counted";
   accepted_reported_at (per NODE) means "that node's accepted hours or
   reject labels counted". A root stamped mid-flight RE-ENTERS a later
   sheet carrying accepted hours with UPLOADED 0. Sheet rows showing
   accepted hours against 0 uploaded hours are the INTENDED reading, not a
   defect. Do not propose deferring the in-window stamp (breaks d3's
   conservation invariant) or stamping later (double-counts uploaded
   hours); both were tried, reverted, and ruled out. A DEFECT IN THE NEW
   MECHANISM — hours counted twice, hours that still cannot reach any
   sheet, a stamp written for something the sheet did not count, the
   refix "seal" firing wrongly — IS a finding and a valuable one.
8. Rulings R1-R3 (split cascade), R4 (nothing deploys before the flip),
   R6 (Drive I read-only). Not up for re-litigation.
9. Nothing is deployed. The VM's ~/hl-gamedata is the rebuild tree and is
   NOT the code under review; ~/hl-gamedata-continuous-test is.
10. tools/retrim_v2_session.py plans its cut on absolute keyframe times
   while head_s is relative. Verified HARMLESS and deliberately left, with
   a comment saying why: head_cut only chooses the cut point (already
   keyframe-snapped) and is returned for the fixlog line only; every
   alignment there is re-derived from what ffmpeg actually produced.
11. r-loop 7 settled these deliberately — do NOT re-report them as bugs:
   - subprocess.CalledProcessError is classified "session", NOT "host":
     ffmpeg exiting non-zero is usually undecodable footage, and calling
     it a host fault would retry a broken clip forever. A host-class
     failure retrying forever with a cooldown + hourly alert is the SAME
     contract HOLD_VLM and the upload lane already have.
   - a FIX_QUEUED row parked by a host error is deliberate, and the
     CLI smoke test admits it ONLY with the host diagnosis on the row.
   - _stuck_lines no longer labels an EMPTY work dir DISCOVERED(media);
     that is the same "an empty dir is not media" rule the cap uses.
   - STR_SJ_INVALID never routes to FIX_RETRANSLATE, sidecars or not.
   A DEFECT IN ANY OF THESE MECHANISMS is still a finding.
12. Host carve-out (r-loop 8 C3): partially-applied host failures route to
   REVALIDATING with the attempt refunded; FIX_QUEUED park only when
   nothing applied. In the continuous driver _fix_one returns False in
   BOTH branches (cooldown + re-pick, never an in-runner revalidate).
13. Retranslate guard (C2) is zero-events-based; the clip duration is
   deliberately absent from the test; split children legitimately have
   head_s >> their own duration.
14. The daily send's reports/<day>/.daily-counted.json durable record
   (C5): a resume re-sends the CSV already on disk and NEVER regenerates;
   an unreadable record refuses loudly and returns False; a duplicate
   daily MESSAGE on resume is the accepted cost (the sheet is
   authoritative).
15. tree_sealed_at is the ONLY whole-tree seal, written by
   recal_refix_reset alone; per-node accepted_reported_at decides
   everything else; the late-arrival settle deferral was DELETED
   deliberately (kickoff §4d cleanest shape — a late root counts
   immediately, loudly). NOTE: C6's refix mixed-tree refusal was
   SUPERSEDED at D0 by ruling C (per-piece payment memory) — see
   entry 21; tree_sealed_at itself stays honored defensively.
16. Gate record (C7): per_window rides in the note; the aggregate
   destroyed is retained for the parent's own _gate_destroyed
   (overlapping windows may double-count key_frames across per_window
   entries — documented); a child inherits only its overlapping windows'
   share via a synthetic entry; legacy entries without per_window
   propagate whole.
17. The suite is CONT_DAILY_REPORTS-independent via a conftest autouse
   fixture forcing True; the suppression is pinned by an explicit
   False-monkeypatch test (C9). SUITE_FLOOR default is 836 since the
   r-loop-20 N set (was 817 after the M set, 798 after the L set, 789
   after the K set, 778 after the J set, 761 after the I set, 745
   after the H set, 718 after the G set, 692 after r12, 670 after
   F11).
18. CONT_DIGEST_RETRY_S (600s) bounds digest RETRY cadence only —
   CONT_DIGEST_INTERVAL_H still gates success; AlertBook stamps only
   successful sends (failed sends retract the stamp;
   duplicate-over-silence under a race is accepted) (C4).
19. raw_int catches OverflowError; normalize_literal type-guards non-str
   (empty tokens dropped in _binding_groups; an empty-normalizing KEY
   makes the whole binding unusable while an empty modifier drops
   alone); BundleError names metadata fields on the raw-only path;
   STR_SJ_INVALID's rewrite now validates-and-overwrites
   present-but-invalid constants using the checker's own enums (C1/C8).
20. The KILLED r-loop-8 finding — the QUARANTINED-empty-dir media-cap
   claim at continuous.py:369 — is SETTLED (2/2 refuted): cap membership
   deliberately differs from _held_discovered. Do not re-raise it.
21. RULED C (Adnaan 2026-08-18 at D0): the refix tool uses PER-PIECE
   payment memory — accepted-counted DELIVERED pieces are recorded in
   the ledger's paid_pieces table (INSERT OR IGNORE, first record wins)
   before teardown, and build_sheet_rows skips a re-delivered node
   matching its record (same id, seconds within 1.0s) WITHOUT stamping
   it; an id COLLISION (same id, different seconds, or seconds NULL) is
   excluded + a LOUD "AMBIGUOUS" line that repeats every sheet until a
   human reconciles (the root deliberately keeps re-entering for it).
   The tool never writes tree_sealed_at (the column + its reports-side
   honor logic stay defensively); an already-SEALED root is refused into
   skipped_sealed with the seal preserved; C6's mixed-tree refusal is
   SUPERSEDED — mixed and fully-paid trees proceed; skipped_mixed /
   sealed_roots stay [] in the output for schema stability; payment
   memory is NEVER auto-deleted (supersede/heal/rebuild leave it; the
   rebuild tool prints a loud NOTE when rows exist) (D7).
22. The daily resume is day-agnostic: send_daily_report_if_due scans
   reports/*/ for any pending record (no .sent, or .sent without
   doc_sent) BEFORE opening today and resumes the OLDEST first, one send
   per tick; the resume REFUSES loudly on a missing counted/accepted row;
   it skips (loudly) re-stamping any sid whose md5_video changed under
   the record — the md5 is the discriminator BY RECORDED DEVIATION (plan
   §9 D5b): an updated_at test would false-positive on innocent state
   churn of in-flight counted roots and double-count them; records
   without "md5" (pre-r9) stamp unconditionally. doc_sent in the record
   gates document-only resends: _send_sheet_document returns bool and a
   swallowed TelegramError leaves doc_sent unset so the next tick
   retries the DOCUMENT only (dup-over-silence; the report MESSAGE is
   never duplicated — test_review_r1 pins both halves) (D5).
   NOTE (r-loop 11 F7): the stamps themselves are now ALSO
   compare-and-set on the recorded md5 — see entry 40; the resume
   pre-filter above stays as the crash-recovery-gap half.
23. BrokenProcessPool with stop unset is host-suspect on the FIRST death
   (row stays VALIDATING + cooldown, a VALIDATING->VALIDATING events
   marker "validation worker died (host-suspect)" counts deaths) and
   terminal QUARANTINED on the second; the U-lane host tuple includes
   subprocess.CalledProcessError in BOTH drivers (delivery lane only —
   the fix-lane CalledProcessError="session" ruling, item 11, is
   unchanged) (D3).
24. Gate-record spans are rebased at propagation time: every gate entry
   is brought onto the CURRENT parent clock (subtracting later ok
   FIX_RETRIM_HEAD cuts via note.head_cut_s, fallback params.head_s),
   and a child's inherited entry is rebased onto the CHILD clock (minus
   segment t0, clamped at 0); unreadable spans propagate whole and
   unadjusted; both crash-adoption paths propagate the gate record
   before the SPLIT commit with bounds parsed from child insert details
   (t0=None => propagate-whole), LOUD-not-blocking there by recorded
   deviation ([gate-propagate-failed]); on the apply_fixes path the
   per-child _append_fixlog OSError is no longer swallowed — it
   surfaces as kind=host and the carve-out re-derives (D4).
25. The quarantine heal clears payment stamps + duration_raw_s ONLY when
   the newly listed video md5 differs from the stored md5_video;
   identical-md5 path heals preserve them (the supersede rule needs new
   bytes) (D6).
26. CNT_SHORT and the soft-max advisory judge the PROBED duration
   (aux["probed_duration_s"], threaded from validate_session's existing
   ffprobe) when available; session.json's claimed duration is only the
   fallback; _map_windows geometry deliberately unchanged this pass (D2).
27. analyze()'s engine error is suppressed when typed qa FAILs already
   exist (the QA_FAIL_UNMAPPED -> FIX_RETRANSLATE routing applies);
   an engine-level OSError with no FAILs carries error_kind="host" and
   validate_session re-raises OSError so the host/crash split holds (D2).
28. rebase_events reports carried re-presses via the carried_out kwarg;
   retranslate_from_sidecars refuses a carried-only rebase — "leaves
   zero events beyond N held-key carries"; a split child with in-band
   events beside a carry still retranslates (D1).
29. r-loop 10: apply_fixes persists the attempt-so-far to the parent
   fixlog BEFORE dispatching FIX_CUT_SEGMENTS (exactly-once via a
   'persisted' watermark; _propagate_gate_record receives only the
   unpersisted tail). Multiple fixlog RECORDS per attempt are the
   intended shape. A DEFECT in the watermark accounting is a finding.
   The exactly-once invariant is now pinned on the PARENT on both the
   failed-cut and success paths (r-loop 11 F11).
30. r-loop 10: a PERMANENTLY-refusing daily resume (unreadable record,
   missing CSV, deleted counted row) writes reports/<day>/.wedged, and
   the day-agnostic scan skips wedged days LOUDLY so later days proceed;
   transient failures never wedge and retry next tick; the daily-report
   CLI takes the run lock. NOTE: the one-shot-alert and bare-text
   .wedged mechanics of this entry were SUPERSEDED by r-loop 11 F1/F5 —
   see entries 34 and 38 for the current shape. A wedge fired for a
   transient condition, or a wedge that silences instead of alerting,
   IS still a finding.
31. r-loop 10: the worker-death count is evidence-scoped — the marker
   embeds the row's md5 and only markers since the last successful
   worker RETURN (HOLD_VLM/READY/FIX_QUEUED/REJECTED event) count;
   back-to-back deaths still terminate on the second. Defects in the
   anchor/marker mechanics are findings.
32. r-loop 10: a heal with NO Drive-side video md5 (vmd5 == "", the zip
   class) preserves payment stamps + duration_raw_s and remembers the
   pre-heal md5 in the heal event (prev_md5=); the download-time
   backfill compares the recomputed local hash and applies the deferred
   supersede-style clear only on changed bytes. Defects in the deferral
   (a clear that never fires, a prev_md5 parse hole) are findings.
33. r-loop 10 (ruling C hardening): ORPHANED paid-piece memory voids the
   id-keyed match — every not-in-memory DELIVERED node of that tree is
   excluded LOUDLY with no stamp and the root keeps re-entering until a
   human reconciles; money-safe (withheld hours are hand-recoverable,
   double-pays are not) is the RULED direction. NOTE: the void KEY of
   this entry (bare id-absence) was SUPERSEDED by r-loop 11 F2 — see
   entry 35 for the current reconcile-against-DELIVERED shape. A
   false-orphan lockout of genuinely-new hours with no loud line, or a
   silent double-pay that still slips through, IS still a finding.
34. r-loop 11 F1 (the iteration's BLOCKER, #1/#14/#16): the fresh daily
   path refuses (return False) whenever TODAY's dir holds .wedged OR
   .daily-counted.json — every record the day-agnostic scan did not
   resume is by definition wedged or settled, and regenerating over it
   destroyed the payment CSV + counted record. A fully-SETTLED today
   (sent + doc_sent, or sent + unreadable record — the scan's own
   settled test) refuses SILENTLY like the old marker check; the loud
   WEDGED/pending line prints only for genuinely pending/wedged states.
   Defects in the guard (a reachable regeneration over a record, a
   scare line on a normal settled day) are findings.
35. r-loop 11 F2 (#2/#13/#20): the paid-memory void fires when ANY
   memory row fails to reconcile against the tree's DELIVERED nodes —
   recorded id absent, present as non-DELIVERED (SPLIT/REJECTED/
   pending), or seconds-mismatched — because deterministic cutter ids
   make bare id-presence meaningless (any re-cut re-creates R-p1). A
   void tree excludes every not-in-memory DELIVERED node loudly AND the
   ROOT prints one ORPHANED reconcile line per sheet even when no such
   node exists (all-matched trees were silent forever). Shared helper
   _mem_reconcile_failures feeds both build_sheet_rows and
   _tree_has_uncounted_accepted. In-memory matched nodes still skip
   with the quiet paid-piece line; in-memory mismatched nodes still
   print AMBIGUOUS. Defects in the reconcile logic are findings.
36. r-loop 11 F3 (#3): fix_lagshift_csv re-raises the host tuple
   (OSError, MemoryError, sqlite3.OperationalError, TimeoutExpired)
   BEFORE the generic except-Exception -> typed FixFailed arm, so
   apply_fixes' classifier refunds host-class failures; genuine decode
   failures keep the typed session-kind message (D3/r10 #10 intact).
37. r-loop 11 F4 (#4/#11): fix_key_hygiene resolves the session's OWN
   work/raw/keybind.json via resolve_keybind (game_name=the ledger
   slug; resolve_keybind's built-in fallback covers unusable files)
   then applies KEYBIND_PATCHES; bound, the normalize_event_key
   exemption, the r10 unbound strip and the action re-resolution all
   judge against it; no-sidecar sessions keep the built-in path.
38. r-loop 11 F5 (#5/#8/#10): .wedged is JSON {why, alerted}; the
   alerted stamp is written ONLY after telegram.send_message returns
   (atomic rewrite); _wedge_day never overwrites an existing .wedged;
   the scan's wedge-skip re-attempts the alert while undelivered (one
   success, then silence); legacy plain-text .wedged reads as
   un-alerted (dup-over-silence). Transient split: the resume's CSV
   check is an explicit os.stat (FileNotFoundError wedges, any other
   OSError prints + retries next tick) and the record read wedges only
   on parse-family errors (JSONDecodeError/KeyError/TypeError/
   ValueError) while bare OSError retries. Defects (alert spam, a
   transient path that still wedges, a wedge that still silences) are
   findings.
39. r-loop 11 F6 (#6, payment-surface, to be surfaced to Adnaan): a
   duration_raw_s=NULL root stays payable. Both halves: (a) BOTH
   drivers backfill the ledger's NULL duration_raw_s from the
   validate-time probe (probed_duration_s rides MapResult.metrics into
   the worker result; an existing value is NEVER overwritten); (b)
   build_sheet_rows gained a third re-entry arm — unstamped, unsealed,
   up < hi, NOT countable, not REJECTED, tree has uncounted accepted
   nodes — which pays the accepted side with a loud UNCOUNTABLE line;
   uploaded hours stay 0 (never fabricated from a NULL probe). If such
   a root later validates and becomes countable, the late guard counts
   its uploaded hours once — intended. Defects (double-pay through the
   new arm, hours still unreachable) are findings.
40. r-loop 11 F7 (#7, payment-surface, to be surfaced to Adnaan): the
   payment stamps are compare-and-set on the bytes the sheet counted.
   build_sheet_rows emits an md5 snapshot from its OWN row read
   (md5_out; it replaced the fresh path's post-build re-query and is
   stored as the durable record's "md5" map);
   mark_uploads_reported/mark_accepted_reported stamp via
   ledger.update_where_md5 (WHERE session_id AND md5_video) with a loud
   per-sid SKIPPED line on mismatch — the new hours stay countable;
   fresh AND resume paths pass the snapshot; a sid without a snapshot
   entry (pre-r9 records, tools, NULL md5) keeps the unconditional
   stamp. Stamp POSITIONS/ordering are unchanged (ruled) — only the
   WHERE tightened. AMENDED by r-loop 12 #1/#2 (entry 45): '' is the
   UNKNOWABLE sentinel and never reads as byte change on either side.
   Defects in the CAS are findings.
41. r-loop 11 F8 (#9): the DISCOVERED-media reclaim sweep and the
   digest's disc_media query anchor within the CURRENT intake stint:
   inner anchor = MIN(DOWNLOADING ts) after COALESCE(MAX ts of events
   whose to_state is outside DISCOVERED/DOWNLOADING, ''), so
   supersede/heal re-entries age from the new generation's first claim
   while never-successful rows are unchanged. AMENDED by r-loop 12 #4
   (entry 47): the grace also re-arms at each reclaim via the
   RECLAIM_MARKER event.
42. r-loop 11 F9 (#12): session_id is constrained to ONE safe path
   component by the shared translate.safe_session_id (rejects
   separators, '..', '.', empty, non-str -> bundle folder-name
   fallback) at BOTH the v2 and v1 translate joins, plus a resolve()
   containment assert on out_dir. Defects (a bypass that still
   escapes out_root) are findings.
43. r-loop 11 F10 (#15): pending_daily_send also returns a day whose
   .regen-v2-counted.json exists without its .regen-v2-done marker, so
   both reset tools refuse during the regen --send crash window.
   EXTENDED by r-loop 12 #11/#13 (entry 53): the DAILY send refuses on
   the same condition, and both checks fail CLOSED.
44. r-loop 11 F11 (#19 disposition, degraded-vote): fix_v1_to_v2's
   button contract is canonicalize-or-drop — 'left' -> 'Left';
   'Mouse4'/'LMB' are UNMAPPABLE in the v2 vocabulary and are dropped
   (r10 #7 design) so the set FAIL cannot re-fire. The refuted harm
   chain (a regressed v1 half surfaces as INP_TOKEN_CASE and
   FIX_KEY_HYGIENE recovers on attempt 2) is recorded in
   R11_FINDINGS.md; do not re-raise it as a terminal-reject risk.
45. r-loop 12 #1/#2: '' (the zip class's UNKNOWABLE-md5 sentinel, r10
   #4) NEVER reads as byte change in the payment stamps: only a
   real-vs-real mismatch skips. A caller with no snapshot (tools,
   pre-r9 records, unrecorded sids) stamps unconditionally; a sid the
   sheet RECORDED as '' stamps unless the download-time deferral has
   since adjudicated NEW bytes (real md5 beside its supersede-style
   NULL duration -> loud skip, new hours stay countable); a CAS miss
   against a row now holding '' stamps (the deferral owns that byte
   adjudication); the resume pre-filter uses the same real-vs-real
   rule. Defects in this composition are findings. AMENDED by r-loop 13
   G1 (entry 55): the recorded-'' skip now keys on the DURABLE
   ZIP_ADJ_CHANGED adjudication event versus the count record's "at" —
   never on the transient NULL-duration row state, which the probe/F6
   refill legitimately erases.
46. r-loop 12 #3/#12: recal_rebuild_reset acquires and HOLDS the run
   lock for its whole duration (the r-loop 1 sibling shape), with the
   stale-lock reclaim that comes along. EXTENDED by r-loop 13 G8:
   refuse-when-live (lock + ledger untouched), stale-reclaim-proceed
   and the live-lock STEAL mutant are pinned.
47. r-loop 12 #4: the DISCOVERED-media reclaim writes a same-state
   RECLAIM_MARKER event before the rmtree, and both first_disc queries
   (sweep + digest) additionally require ts > MAX(marker ts) so the
   12h grace re-arms per reclaim; never-successful rows without a
   reclaim keep the F8 behaviour.
48. r-loop 12 #5/#8: fix_actions_context (and the operator twin
   tools/fix_actions_from_v2.py) resolve the session's own
   raw/keybind.json exactly as F4's fix_key_hygiene does; built-ins
   only as fallback.
49. r-loop 12 #6: _pre_cut_csv_fixes runs unconditionally before the
   hygiene loop, so structural surgery (header/rows/tsrepair) precedes
   hygiene/context in cut-less plans too; hygiene-before-context,
   gate-last-among-writers, and the rank-2 csv-writer positions are
   unchanged and still ruled.
50. r-loop 12 #7: CNT_NOTIF_EDGE and CNT_CHAT_PII edge arms emit
   CNT_SHORT (blocking, unfixable, post_cut_s) at map time when the
   planned t±1.0 edge cut would leave under MIN_CLIP_S, judged on the
   probed duration — mirroring the CNT_EDGE_NONGAMEPLAY arm. COMPLETED
   by r-loop 13 G3/G9: the notif/chat EDGE-VS-MID classification also
   judges dur_true (a corrupt claim can no longer turn a fixable tail
   edge into an unfixable mid reject), and the r12 #7 tests split
   probed vs claimed in BOTH directions (the dur-for-dur_true mutant
   is killed).
51. r-loop 12 #9: the VLM sweep grid is clamped — analyze() passes
   min(claimed, probed container duration) (probe_streams now exposes
   format duration), and vlm_sweep itself caps duration at 24h as the
   backstop for every caller.
52. r-loop 12 #10: OverflowError is caught at the qa-v2 frame-sync
   compare (degrades to a typed 'timestamp_ms values out of range'
   FAIL) and at inventory()'s timestamp cast (counts as unparseable) —
   the checker's degrade-never-crash contract.
53. r-loop 12 #11/#13: send_daily_report_if_due refuses loudly while
   any .regen-v2-counted.json lacks its .regen-v2-done marker (shared
   reports.pending_regen_send); pending_daily_send/pending_regen_send
   fail CLOSED (truthy UNKNOWN sentinel) when the reports dir cannot
   be listed, while a genuinely MISSING reports dir is
   nothing-pending. COMPLETED by r-loop 16 J5 (entry 78): the
   day-agnostic resume scan inside send_daily_report_if_due itself —
   the doctrine's last fail-open sibling — now routes through the
   shared reports._report_day_dirs and refuses the tick on None.
54. r-loop 12 #14/#15: the F6 producer chain (_metrics ->
   _validate_worker forwarding) and the F9 v1 translate_bundle join
   are pinned end to end; the pins were mutation-proven.
55. r-loop 13 G1 (#1/#2/#3): the zip-class '' adjudication is DURABLE.
   ledger.supersede with a falsy new_md5 over a real stored md5 appends
   '; prev_md5={old}' to its event detail, so BOTH '' writers (the
   stamp-preserving heal and the stamp-clearing zip supersede) are
   covered by the download-time deferral. Arm 4 (CAS miss, row now '')
   STILL STAMPS by design — the refuters' own recommendation: a falsely
   landed stamp SELF-HEALS at download (changed bytes: the deferral
   clears the stamps and writes a durable same-state DOWNLOADING event
   whose detail starts with the ZIP_ADJ_CHANGED constant; identical
   bytes: the stamp correctly stands). Arm 2 (sid RECORDED as '') skips
   loudly iff a ZIP_ADJ_CHANGED event for the sid is at-or-after the
   count record's "at" (counted_at, threaded from BOTH production
   paths; the '>=' is deliberate — a marker in the record-write second
   is post-count, any earlier marker leaves a real md5 in the
   snapshot); counted_at=None (tools/legacy records) keeps the
   unconditional stamp. The marker's forensic suffix deliberately
   avoids the literal prev_md5= token so the deferral's rsplit parse
   can never pick the marker up; the marker was checked against every
   event-anchored query (commit message of abf052b). The resume
   pre-filter _bytes_changed stays real-vs-real only — one decision
   site. Defects in this composition are findings. AMENDED by r-loop
   14 H1 (entry 61): counted_at is now captured BEFORE the sheet's
   row read, so the marker-vs-anchor compare covers the whole build
   window; the "any earlier marker leaves a real md5" rationale is
   true again by construction. AMENDED by r-loop 19 M4 (entry 92):
   the '' zip supersede over a real stored md5 now PRESERVES the
   payment stamps + duration_raw_s + tree seal — BOTH '' writers
   preserve, and the download-time deferral owns the clear; both r13
   zip pins stayed green. This entry's "stamp-clearing zip supersede"
   phrasing is superseded accordingly.
56. r-loop 13 G2 (#4): FIX_RETRANSLATE's plan step carries
   params {"rerouted": bool} from plan_fixes' single emission site, and
   _dispatch passes game_override=game ONLY when rerouted (AND game in
   C.GAMES). Both drivers resolve game to the corrected slug and update
   the ledger BEFORE apply_fixes, so the override carries the corrected
   game (review-2 #5 preserved verbatim); non-reroute retranslates
   resolve the session's own raw/keybind.json. plan_fixes is pure and
   recomputed per attempt — no persisted-plan staleness exists.
   COMPLETED by r-loop 14 H2 (entry 62): the non-reroute session
   branch anchors its slug and its built-in-keybind fallback on the
   ledger slug (ledger_game, always passed by _dispatch), never on
   player-typed metadata.
57. r-loop 13 G4 (#6): OverflowError degrade arms across the checker
   and the operator tool: _check_session_json's numeric tuple (message
   unchanged, still maps to STR_SJ_INVALID); the ts-stats/frame-spacing
   block degrades to the typed FAIL 'frame spacing: timestamp_ms or fps
   values out of range' which is DELIBERATELY unmapped (routes to
   QA_FAIL_UNMAPPED -> retranslate-when-sidecars, the r12 #10 route);
   the video-duration compare keeps the 'video duration' needle
   (STR_SJ_INVALID); _verify_against_raw degrades to its documented
   warn-skip plus a per-event skip; analyze_sample's _num returns the
   default. float(str) saturates to inf and never raises — string-cell
   sites are deliberately unguarded (verified by execution).
   COMPLETED by r-loop 14 H9b (entry 69): the _verify_against_raw
   pins now drive the production call shape (raw_bundle passed —
   there is NO auto-detection) and pin the degrade line; the
   per-event bigint-t arm has its own test.
58. r-loop 13 G5 (#8, payment-surface, ruling C extended — flagged in
   the plan Adnaan read): recal_rebuild_reset REFUSES rc=2 when any
   in-scope root carries uploaded_reported_at or any DELIVERED node
   carries accepted_reported_at, unless --allow-reported. Under the
   flag: uploaded stamps are PRESERVED through the reset (dropped from
   the NULL list unconditionally — without the flag a stamped root
   cannot reach the UPDATE), every accepted-stamped DELIVERED node is
   recorded via ledger.record_paid_piece keyed to its TREE root
   (parent-chain walk) BEFORE the child DELETE, and accepted marks are
   then nulled — the memory carries the payment fact. Dry-run JSON and
   the --yes summary print the stamped-root / stamped-node /
   recorded-piece counts. The r6 test's uploaded-NULL assert was
   INVERTED to preserved accordingly. Defects (a double-pay through the
   preserved stamp, a piece recorded under the wrong root, an abort
   bypass) are findings. EXTENDED by r-loop 14 H3 + H9a (entries
   63/69): the teardown also discards split manifests, rowless
   segment dirs and the -analysis dir (shared
   _discard_split_artifacts), and the tree-root paid-piece keying is
   pinned at depth 2 against the immediate-parent mutant.
59. r-loop 13 G6 (#9): reports.pending_daily_send_detail returns
   (day, kind) with kind in daily/wedged/regen/unreadable ("wedged" =
   daily record + .wedged; "unreadable" = the fail-closed sentinel);
   pending_daily_send is a thin back-compat wrapper over it (the
   pending SET is unchanged — only diagnosis was added); both reset
   tools print the SHARED reports.PENDING_SEND_GUIDANCE why/how and an
   ABORT JSON carrying day/kind/why/how that keeps the 'pending
   resume' phrase.
60. r-loop 13 G7 (#7): tools/fix_actions_from_v2 AND
   tools/fix_sync_from_v1 route the delivered session.json's
   session_id through translate.safe_session_id with the translate
   joins' is_relative_to containment assert; a traversal id falls back
   to the bundle dir name. COMPLETED by r-loop 14 H9c (entry 69): the
   fix_sync_from_v1 half now has its own traversal twin test, and the
   tool's stale resolve_actions tuple unpack (a TypeError crash on
   every real run, invisible because the tool had zero coverage) was
   fixed in the same commit.
61. r-loop 14 H1 (r14 #2≡#3): the daily send's counted_at anchor is
   captured BEFORE the write_payment_sheet call (pre-build), stays the
   durable record's "at" and both stamp calls' counted_at (one
   capture, identical string), and the resume replays the recorded
   pre-build "at". A marker at-or-after the pre-build instant is not
   provably pre-count — skipping is the money-safe direction; a marker
   strictly before it leaves a REAL md5 in the snapshot and routes to
   the CAS arm. counted_at=None (tools/legacy) keeps the unconditional
   recorded-'' stamp — deliberate. Defects in this composition are
   findings.
62. r-loop 14 H2 (r14 #1≡#6): retranslate_from_sidecars takes
   ledger_game (always passed by _dispatch as game when game in
   C.GAMES); the non-override branch computes slug = ledger_game
   first (metadata-derived only as fallback) and resolves the keybind
   with game_name = ledger_game or game_name. The session's own
   raw/keybind.json still WINS when usable (r13 #4 intact);
   game_override stays reroute-only (G2 intact); every downstream
   consumer of the branch's slug (KEYBIND_PATCHES, context gating,
   fix_sessionjson_recompute) keys on the same slug variable. A
   post-hoc empty-keybind fallback layer was deliberately NOT added —
   resolve_keybind's internal parsed-but-unusable fallback lands on
   the right built-in once anchored. Defects are findings. COMPLETED
   by r-loop 15 I3 (843a2ec, tests-only): the slug half is now pinned
   where its consumers are LIVE — two OW-ledger discriminators drive
   plan_fixes -> apply_fixes with degraded metadata and a usable
   custom keybind, covering the KEYBIND_PATCHES half and the
   context-gating half; the finder's exact slug-revert mutant (which
   was arming-gate-green at 749) now fails both.
63. r-loop 14 H3 (r14 #10): recal_rebuild_reset's teardown calls the
   shared pipeline.run._discard_split_artifacts per sid (child rows
   are already DELETEd there, so every segment dir is rowless by
   construction) and wipes work/sid-analysis, exactly like the refix
   sibling. A post-reset re-run's _recover_split returns
   complete=False — re-derive, never stale adoption of the
   pre-recalibration cut. Defects are findings.
64. r-loop 14 H4 (r14 #4): ingest.run_rclone strips the leading
   per-line wall-clock prefix from rclone stderr before returning
   (CompletedProcess rebuilt), so every alert embedder inherits a
   stable dedup key; the synthetic timeout text was already stable and
   is unchanged; AlertBook's contract (dedup on literal text, TTL,
   optimistic-stamp-then-retract) is untouched. An explicit key= param
   on AlertBook was deliberately NOT added. Defects are findings.
65. r-loop 14 H5 (r14 #5), AMENDED by the r-loop-15 RULING (Adnaan
   2026-08-19, "if the folder is gone, it's gone" — see entry 70):
   ingest.scan has a THIRD vanished-folder arm under the same
   healthy-listing guard as the two siblings (games_present + path not
   in listed_dirs): DISCOVERED rows only -> QUARANTINED, detail
   "folder gone from Drive I — dropped from intake; re-upload under a
   NEW folder name to re-enter", NO INT_PATH reason (off the chase
   list), one loud [vanished-discovered] line per row. The
   DISCOVERED->QUARANTINED event is a GENUINE transition (the digest
   quarantine counter rightly counts it). SAME-PATH-TERMINAL IS THE
   RULED DESIGN, not a gap: a reappearance at the SAME path (Drive
   trash restore, identical re-upload — the r15 #1≡#2≡#3≡#10
   cluster's scenario) deliberately never re-registers; only the same
   sid at a DIFFERENT path re-registers, via the existing
   quarantined-path heal — which is IDENTITY-GUARDED since r-loop 17
   K1 (entry 80). This entry's previous phrasing ("a
   clean-path reappearance re-registers") was corrected in-code at I7.
   The alternative (a failure counter in _download_one) was
   deliberately NOT adopted — the scan-side arm keeps the driver
   stateless. Defects OUTSIDE the ruled dead-end (e.g. the guard
   pruning a live row) are findings; the same-path dead-end itself is
   NOT.
66. r-loop 14 H6 (r14 #7): validate._joint_edge_short, called from
   map_reasons after _map_windows + _map_flags, composes the joint
   head+tail remainder from EXACTLY the cut points plan_fixes will
   derive (CNT_EDGE_NONGAMEPLAY cut_at_s, notif/chat t±1.0, same
   blocking+fixable filter); under MIN_CLIP_S it appends ONE map-time
   CNT_SHORT (blocking, unfixable, post_cut_s = joint remainder). It
   SKIPS when an individual arm already emitted CNT_SHORT (duplicate-
   free reason list). Entry 26's _map_windows geometry untouched.
   Defects are findings. COMPLETED by r-loop 15 I6 (a93847d,
   tests-only): the CNT_EDGE_NONGAMEPLAY TAIL accumulation and the
   chat-HEAD sub-branch (t <= 3.0, params carrying only t) are pinned;
   the finder's exact delete-the-else-branch mutant (arming-gate-green
   at 749) and a drop-the-t<=3.0-disjunct head-arm mutant now fail
   them, split both ways.
67. r-loop 14 H7 (r14 #8): safe_session_id additionally rejects ids
   containing control characters (any ord(c) < 32) — they take the
   bundle-folder-name fallback at the shared decision point covering
   all five join sites. JOINED by r-loop 15 I5 (7169922, entry 74):
   the same accept condition now also bounds the id to 200 utf-8
   bytes. Defects are findings.
68. r-loop 14 H8 (r14 #9): analyze_sample's build_verdict derives
   dur_true = probed (a.video_probe duration_s) or claim, and judges
   the clip-short gate and the VLM-window dur/at_tail tests on it;
   the claim is only the fallback when the probe yielded nothing.
   Report/table rows still DISPLAY the claim beside the qa-v2
   duration-mismatch line — deliberate (mutating the display would
   hide the discrepancy). Defects are findings.
69. r-loop 14 H9 (r14 #11/#12/#13, tests + one stated deviation): the
   G5 paid-piece tree-root walk is pinned at depth 2 (root-p1-p1;
   fails on the immediate-parent mutant); the G4 site-4 raw-verify
   test drives check_session_v2(d, raw_bundle=...) and pins the
   degrade line, with a per-event bigint-t sibling; fix_sync_from_v1
   has a traversal twin (lag machinery + grid alignment stubbed — the
   JOIN is the pin). DEVIATION recorded in the plan: the twin exposed
   a REAL pre-existing crash — fix_sync_from_v1 kept the
   pre-context-gating resolve_actions call shape (the function
   returns a tuple; the writer crashed on the first row of every real
   run) — fixed with a one-line unpack in the same commit. Defects in
   any of these mechanisms are findings.
70. r-loop 15 RULING (Adnaan 2026-08-19, disposing the r15
   #1≡#2≡#3≡#10 cluster): "if the folder is gone, it's gone." The H5
   vanished-arm's same-path dead end is THE DESIGN: a folder restored
   at the SAME path (Drive trash restore, identical re-upload, a
   transient listing flap that later clears) stays QUARANTINED
   silently, forever — no same-path heal, no consecutive-listing
   counters, no event churn on restore scans (pinned). The correction
   path is a re-upload under a NEW folder name, which mints a new
   session id and processes as a completely separate session —
   verified: BOTH dedupe sites (scan-time and download-time) exclude
   QUARANTINED rows, so the dead row never blocks or dup-rejects the
   renamed copy. The quarantine detail and the [vanished-discovered]
   loud line carry exactly that coaching (I7, f3f131e). Do NOT
   re-raise the same-path dead end, propose a same-path heal, or
   propose reappearance counters — RULED. Still findings: the guard
   quarantining a row whose folder IS listed, the coaching string
   perturbing an event-anchored query, a renamed re-upload that the
   dead row DOES block, or the detail exceeding the event cap.
   AMENDED by r-loop 17 K1 (entry 80): the different-path heal this
   entry relies on is now identity-guarded for rows with a real prior
   registration — cross-player claims without byte identity are
   refused (r5 #41 restored); the ruled rename path (same player, NEW
   folder name, new sid) is UNCHANGED and is pinned end-to-end at
   both dedupe sites by K6 (entry 85).
71. r-loop 15 I1 (r15 #4, RULED bfd96b7): check_session_v2's key-token
   case clause flags only tokens that HAVE case (t.lower() == t and
   t.upper() != t and not t.isdigit()) — caseless symbol keys (';',
   '-', '[', ...) are REAL GAMEPLAY DATA, stay in the delivery, and
   no longer FAIL the grammar the writer's own key_display satisfies.
   Multi-char lowercase tokens ('left_shift') still flag; digits stay
   exempt; the whitespace/comma arms and the button set-membership
   test are untouched; the INP_TOKEN_CASE needles in validate.py are
   unchanged (the FAIL simply stops firing for caseless tokens);
   fix_key_hygiene agrees by construction (same key_display).
   Defects (a cased token slipping through, a symbol key stripped or
   re-FAILed anywhere in the chain) are findings. COMPLETED by r-loop
   16 J6 (entry 79, RULED): the comma-arm sibling of this exact class
   is closed by NAMING the comma key 'Comma' — see entry 79 before
   raising anything comma-related.
72. r-loop 15 I2 (r15 #5, RULED 348c93d): the delivery writers enforce
   the keys-have-actions invariant via CREDITED literals — a kept key
   token must be credited by a rule whose FULL group set was
   satisfied and fired. resolve_actions exposes its existing credit
   accounting through the keyword-only credited_out set (behavior
   unchanged for every caller that does not pass it); _v2_rows takes
   the rules (required param) and strips-and-counts bound-but-
   uncredited tokens exactly like unbound ones (the combo-half case);
   fix_key_hygiene mirrors the rule at its resolve call;
   retranslate_from_sidecars inherits via _v2_rows; the
   fix_sync_from_v1 remap applies the same rule. Deliberate scope:
   motion-axis rules credit no literals so keys are NEVER stripped
   for lacking motion (credit is computed with motion False,False —
   exact); mouse buttons keep today's behavior (the checker invariant
   covers keys only); collapse_ambiguous_runs and the context-gating
   dead_literals path are untouched; fix_v1_to_v2 deliberately keeps
   v1-resolved actions verbatim (violations in its output route to
   the fixed hygiene/retranslate). The hostile per-group-credit
   mutant is killed. Defects (a credited token stripped, an
   uncredited one shipped with null actions, the credit accounting
   diverging from rule satisfaction) are findings. COMPLETED by
   r-loop 16 J3 (tests-only, c0d37de): the fourth writer site — the
   fix_sync_from_v1 remap's credited strip — is now pinned; the exact
   pre-I2 revert (which was arming-gate-green at 765/761) fails it.
   COMPLETED AGAIN by r-loop 17 K5 (entry 84): that pin now carries
   the I2 cohort's OVERLAP frame, so the row-level restatement (which
   was arming-gate-green at 782/778) fails it too.
73. r-loop 15 I4 (r15 #7, ee35d3f): fix_v1_to_v2 repairs a NAIVE v1
   created_at_utc in place (tzinfo=utc) before the head_cut_s
   adjustment and the astimezone write — the exact sibling guard
   every other parse-then-astimezone site already had. The sweep also
   added the same two-line guard to tools/retrim_v2_session.py, which
   was shielded only by plan ordering (STR_SJ_INVALID's rewrite
   precedes FIX_RETRIM_HEAD in every plan). The naive pin forces the
   host TZ in-test (Asia/Kolkata + tzset) so it fails pre-fix on
   every host. Defects are findings. COMPLETED by r-loop 16 J4
   (tests-only, c0d37de): the retrim sweep half is now pinned through
   the REAL tool under in-test TZ; the exact guard-deletion mutant
   (arming-gate-green at 765/761) fails it.
74. r-loop 15 I5 (r15 #8, 7169922): safe_session_id's shared accept
   condition also bounds the id to 200 utf-8 bytes (encode with
   ignore) — over-length ids take the designed bundle-folder-name
   fallback at all join sites instead of crashing mkdir with OSError
   errno 63 (which the fix lane classifies HOST and retries forever).
   200, not 255: the pipeline derives longer names from the sid
   (split-manifest +20, -analysis +9, -pN suffixes) that must stay
   under NAME_MAX. Boundary pinned both ways (200 kept, 201 falls
   back) plus byte-vs-char semantics. Defects are findings. AMENDED
   by r-loop 16 J1 (entry 76): the byte length is now measured with a
   STRICT encode (try/except UnicodeEncodeError -> fallback) — the
   'ignore' measure admitted JSON-legal lone surrogates that crashed
   every join.
75. r-loop 15 I8 (RULED fd3ea1f): fix_sync_from_v1 copies delivery
   files with shutil.copy2 — the macOS-only cp -c APFS clone is gone
   (clone efficiency is dispensable for this operator tool), the
   subprocess import went with it, and the H9c twin's _portable_cp
   stub was REMOVED so the twin exercises the tool's real copy on
   both hosts (the VM gate is the Linux prover; the pre-82c86da
   unstubbed VM failure is the on-record fail-first). Defects are
   findings.
76. r-loop 16 J1 (r16 #1≡#4, c4f1fda): safe_session_id measures the
   byte length with a STRICT utf-8 encode inside try/except
   UnicodeEncodeError -> the bundle-folder-name fallback. A JSON-legal
   lone-surrogate id (the \\ud800 escape json.loads accepts) is
   GARBAGE and degrades to the fallback like non-str/NUL/over-length
   ids; for encodable ids the semantics are byte-identical to I5.
   Defects (an unencodable id that still reaches a join, an encodable
   id wrongly rejected) are findings.
77. r-loop 16 J2 (r16 #3, c0d37de): the INP_OSKEYS trigger judges the
   session's OWN binding — validate_session resolves it exactly as
   fix_key_hygiene does (new _session_bound_literals: session
   raw/keybind.json via resolve_keybind anchored on expected_game =
   the ledger slug, built-in fallback, KEYBIND_PATCHES) into
   aux['bound_literals'] (degrade-with-note to all-unbound, never
   crash); map_reasons filters the engine's pattern-only os_keys by
   key_canonical against it. Unbound pollution keeps the blocking
   fixable reason (hygiene really clears it); bound hits surface as
   an advisory (the dossier still shows them); callers without the
   aux key keep the all-flagged behavior. The engine inventory
   deliberately stays pattern-only (standalone recommend-only
   design). Defects (a bound key still looping to a reject, an
   unbound key silently waved through, the aux wiring dropped) are
   findings. COMPLETED by r-loop 17 K4 (entry 83): the filter's
   key_canonical is pinned with a camel discriminator (CapsLock bound
   as caps_lock) at map level AND in the e2e, so the str.lower
   restatement (which was arming-gate-green at 782/778) fails both.
78. r-loop 16 J5 (r16 #2, RULED Adnaan 2026-08-19, eaaee0d): the
   day-agnostic resume scan in send_daily_report_if_due fails CLOSED
   — it routes through the shared reports._report_day_dirs; None
   (listing failed) prints the loud '[daily] reports dir unlistable'
   line and refuses the TICK (return False, retry next tick — the
   r-loop-11 #10 transient doctrine, no wedge); [] (reports dir
   genuinely absent, first-ever send) keeps the fresh path reachable.
   'Could not look' must never read as 'nothing pending' where it
   orders resume-before-fresh — one flaky listing let the fresh path
   double-count a pending day's hours onto two sent sheets. No stamp,
   sheet-math, or ordering change. Defects (a reachable fresh-past-
   pending path, a refusal that wedges or never retries) are
   findings.
79. r-loop 16 J6 (r16 #5, RULED Adnaan 2026-08-19 option A, ddc6da8):
   the comma KEY ships as the NAMED token 'Comma' (like Space/Enter):
   ',' -> 'Comma' in _KEY_DISPLAY (inverse/round-trip inherited by
   key_canonical, hygiene, both G7 tools), plus the 'comma' -> ','
   literal alias so a keybind writing 'Comma' binds the raw ','
   events. The checker's comma ARM is deliberately untouched: glued
   multi-char tokens (W,A) still FAIL, and a foreign bare-',' cell
   still FAILs but is now genuinely repairable by hygiene in ONE
   attempt (',' round-trips to 'Comma'). No raw comma character ever
   sits inside a delivered input_keys cell. Do not propose a
   checker-side ',' exemption — option B was considered and NOT
   chosen. Defects in the round-trip or the one-attempt repair are
   findings.
80. r-loop 17 K1 (r17 #1, c99309e): the quarantined-path heal refuses
   CROSS-PLAYER identity claims without byte identity — the review-r5
   #41 refusal restored for rows with a REAL prior registration
   (existing player_email non-empty). INT_PATH chase rows (inserted
   with player_email '') stay unguarded — the heal's designed
   population. SAME-player re-uploads at a new path heal with ANY
   bytes (the review-r3 #7 correction class; bytes can differ per
   review-r4 #7; changed bytes clear stamps as new hours per D6/r9 —
   entry 25 unchanged). A byte-identical cross-player move still
   heals, DELIBERATELY, exactly like the move-heal (pinned). The
   refusal appends a loud 'heal REFUSED — identity mismatch'
   integrity flag (a stderr-printed ScanResult flag, NO ledger event
   — no event-anchored query can see it) and re-fires every scan
   while the foreign claim persists, and the row keeps its original
   attribution, stamps and md5. STATED DEVIATION (plan §0): the guard
   is cross-player-AND-no-byte-identity, NOT the raw move-heal
   formula the r17 finder transcribed — that formula's md5 arm would
   also refuse same-player different-md5 heals, the heal's designed
   population pinned by two committed tests. Two accidental
   cross-player test SEEDS were aligned with their tests' own intent
   in the same commit (review-r4 wipe test seeds player '' = the
   INT_PATH population; review-r5 reset test seeds the builder's
   p1@x.com — its scenario is an operator rename and its assertion
   already hard-coded the p1 path). Do not re-raise the deviation
   itself. Defects (a cross-player claim that still heals without
   byte identity through ANY arm, a same-player or INT_PATH heal
   wrongly refused, a takeover path around the guard, the flag text
   perturbing anything) are findings.
81. r-loop 17 K2 (r17 #2, bd94829): fix_v1_to_v2 resolves the
   session's OWN keybind before computing bound — kbp =
   work/keybind.json (still at the work ROOT at that point; the
   function moves sidecars into raw/ only later), else
   work/raw/keybind.json (the re-entrant shape), via resolve_keybind
   anchored on the ledger slug, else the built-ins; then
   KEYBIND_PATCHES. resolve_keybind's parsed-but-unusable fallback
   lands on the right built-in. v1-resolved actions still ship
   VERBATIM (entries 44/72 scope unchanged — violations in its
   output route to the fixed hygiene/retranslate). STATED DEVIATION
   (plan §0): the delivered key_binding.json fallback arm was NOT
   adopted — the inversion sniff is biased against flipping and a
   mis-flip empties the keyboard column (the r-loop-4 catastrophic
   class); do not propose it. reprocess_session's built-ins-only
   resolve is CLI-only, outside the pipeline fix family — NOTED-not-settled
   (see entry 82's closing rule). Defects (a custom-bound press still
   deleted on any arm, the raw/ arm dead, a mis-anchored slug) are
   findings. COMPLETED by r-loop 18 L1 + L3 (entries 86/88): L1's
   whole-function degrade closed the NOTED float site in this same
   function, and the game_name=slug anchor is now pinned where it is
   LIVE — an OW-ledger v1 conversion with a parsed-but-unusable
   keybind keeps its movement presses; the exact game_name=None
   mutant fails only that pin.
82. r-loop 17 K3 (r17 #3, 22614ef): apply_context_to_rows' _active is
   guarded exactly like fix.py's _moving — try/except (TypeError,
   ValueError) -> False; a junk dx/dy cell is NOT motion (matches
   _num_cell/fix_sentinels semantics); keys still resolve on the
   poisoned row; FIX_ACTIONS_CONTEXT completes over STR_SENTINELS
   cells and the sentinel repair follows later in the same plan; the
   operator tool tools/fix_actions_from_v2.py inherits. STATED
   DEVIATION (plan §0): the belt-and-braces plan reorder
   (FIX_SENTINELS pre-structural) was NOT adopted — the guard closes
   the confirmed mechanism; do not re-propose the reorder as a
   finding in itself. NOTED-NOT-SETTLED status after r-loop 18:
   fix_v1_to_v2's float(dx or 0) site is CLOSED — iteration 18
   proved the harm path and L1 fixed it (entry 86).
   translator/sync.py input_track_from_rows' bare float() over
   dx/dy cells stays NOTED-not-settled (shielded today only by
   reasons ordering — STR_SENTINELS maps before the sync FAIL; it
   survived iteration 18 with no proven harm), as does
   reprocess_session's built-ins-only keybind resolve (CLI-only,
   entry 81). A note is not a ruling — if you can PROVE a concrete
   harm path through either, report it as a normal finding.
83. r-loop 17 K4 (r17 #4, tests-only, 3f4f917): the J2 INP_OSKEYS
   cohort carries a camel discriminator — map-level CapsLock bound
   as caps_lock asserts no INP_OSKEYS plus the BOUND advisory, and
   the e2e binds caps_lock BESIDE insert with camel CSV tokens — so
   the suite-green t.lower() restatement of the validate.py filter
   fails both pins (the finder's exact mutant, killed in a
   fixed-tree scratch proof). Amends entry 77. No production code
   changed.
84. r-loop 17 K5 (r17 #5, tests-only, 000d87a): the J3 fix_sync
   remap pin carries the I2 cohort's OVERLAP frame — ['w','e'] under
   rules (interact: ctrl+e, move_up: w) must ship keys ['w'] actions
   ['move_up'] — so the row-level restatement (if rules and not
   actions then empty the kset) fails the pin (the finder's exact
   mutant, killed). Amends entry 72. No production code changed.
85. r-loop 17 K6 (r17 #6, tests-only, cdd03cc): entry 70's
   load-bearing precondition is pinned END-TO-END at BOTH dedupe
   sites: scan-time — after a vanish-quarantine, a NEW sid at a
   different path with the SAME md5 and player lands DISCOVERED with
   no dup verdict anywhere and the dead row stays dead; download
   time — a zip-payload re-upload whose bytes hash to the dead row's
   md5 downloads to INGESTED instead of dup-parking. Both exact
   QUARANTINED-exclusion mutants killed, site-isolated. The
   rejected-slot side-door filters deliberately keep their
   QUARANTINED-only exclusion tuple (review-2 #1 semantics: they
   judge the REPLACEMENT, not the dead row) — not an inconsistency.
   No production code changed.
86. r-loop 18 L1 (r18 #1≡#2≡#3≡#5, e197244): fix_v1_to_v2 degrades
   EVERY read of the player-typed v1 payload instead of crashing —
   the K3 doctrine at the site K3's own sweep NOTED, where no other
   plan step can rescue a crash (the checker early-returns on
   key_binding.json present, so no repair can ever precede this
   step). dx/dy parse through _parse_motion (fix_sentinels' _parse
   rule: junk/non-finite -> 0.0 — a junk cell is not motion);
   has_motion judges PARSED values, so a junk-only or all-zero
   column ships the blank no-capture form instead of a fabricated
   all-zero motion track — a DELIBERATE behavior change matching
   fix_sentinels' own has_motion rule. created_at_utc/trim: str()-
   coerced parse + float()-coerced head cut inside try/except
   (TypeError, ValueError) -> an unusable stamp/trim is OMITTED and
   fix_sessionjson_recompute (already called by this fix)
   synthesizes a canonical stamp from ground truth (its designed
   r-loop 7/8 job); non-dict trim and non-dict canonical normalize
   to {} (session_id falls back to the folder name); session.json
   is read via _read_session_json ({} on unreadable — reachable
   because sniff types the payload v1 on key_binding.json alone).
   No plan-order change. Defects (a junk shape that still crashes
   the route, a REAL motion track wrongly blanked, a parseable
   stamp wrongly omitted or shifted, the stray-key_binding early
   return misfiring on the degraded s={} read) are findings.
   AMENDED by r-loop 19 M1/M3/M8 (entries 90/91/95): the stamp/trim
   block was REBUILT by M1 — iteration 19 proved this entry's
   omit-and-synthesize arm FABRICATED head-offset facts on the
   sidecar route (the r19 BLOCKER); see entry 90 for the current
   recover-or-refuse shape (the no-sidecar arms keep this entry's
   semantics, junk-trim now stamp-preserving per r19 #10); row SHAPE
   joined the degrade contract (M3, entry 91); the
   non-dict-canonical and str()-coercion arms got failing-side pins
   (M8, entry 95).
87. r-loop 18 L2 (r18 #4, 21c983e): has_raw_sidecars — the single
   shared plan gate; both drivers route through it — additionally
   requires raw/metadata.json to PARSE to a dict (errors='replace'
   read, except (OSError, JSONDecodeError) -> False), so a
   present-but-unusable sidecar reads as NO-sidecars and the
   planner falls back to the CSV-level fixes ("unusable equals
   missing" — the r-loop-7 gray-zone rule's open half closed).
   inputs.jsonl deliberately has NO parse test (load_events reads
   it line-tolerantly, r-loop 4). Belt-and-braces: the read inside
   retranslate_from_sidecars raises a typed FixFailed naming
   raw/metadata.json on any residual path. Defects (a has_raw
   consumer disagreeing with the plan gate in a way that ACTS on
   the disagreement, a session stranded UNFIXABLE where a CSV-level
   repair exists, a gate-vs-read race with real harm) are findings.
   AMENDED by r-loop 19 M2/M5/M8 (entries 89/93/95): the gate
   additionally requires a parseable recording.started_at_utc (the
   consumer's own parse, shared _utc); validate's aux['has_raw'] now
   routes THROUGH the gate (the existence-based drift this entry's
   defect clause anticipated was confirmed as r19 #11 and closed);
   the errors='replace' read got its failing-side pin.
88. r-loop 18 L3 (r18 #6, tests-only, f57b3ff): K2's game_name=slug
   anchor is pinned where it is LIVE — an outer_wilds-LEDGER v1
   conversion with DEGRADED canonical metadata (game: 12345, so a
   metadata-anchored restatement fails too — the r14 H2 regression
   shape) and a parsed-but-unusable keybind at the work root
   asserts the W/A/S movement presses survive with their v1
   actions; the finder's EXACT game_name=None mutant fails ONLY
   this pin; KEYBIND_PATCHES (outer_wilds) is live in the cohort
   for the first time. Sibling anchors were already pinned live
   (I3's OW discriminators, the F4/r12 cohorts, J2/K4). No
   production code changed. Amends entry 81.
89. r-loop 19 M2 (r19 #2, a6af11d): has_raw_sidecars additionally
   requires a parseable recording.started_at_utc — 'usable' means
   what retranslate_from_sidecars actually NEEDS (it hard-requires
   the stamp for the head offset and FixFails both attempts without
   it, superseding the CSV-level repair that would have delivered).
   Usability is judged by the consumer's OWN parse: _utc is
   module-level and SHARED by the gate and retranslate, so the two
   sites cannot drift. Sibling fixed in the same commit:
   retranslate's (meta.get('recording') or {}) crashed an untyped
   AttributeError on a truthy non-dict recording block — now
   degrades to the existing typed 'cannot derive the head offset'
   FixFailed. Two 'usable' test controls (test_r_loop7's
   has_raw_means_the_same_thing_everywhere, test_r_loop18's
   usable_metadata_still_plans_retranslate_control) were re-modeled
   to genuinely usable sidecars, intent preserved. Defects (a
   semantically-unusable sidecar that still supersedes a delivering
   CSV repair, a usable one wrongly read False, gate-vs-consumer
   parse drift) are findings. Amends entry 87.
90. r-loop 19 M1 (r19 #1 BLOCKER + #4≡#6 + #10, cb8cfd4):
   fix_v1_to_v2's stamp/trim resolution runs BEFORE any write (a
   refusal leaves the work dir byte-identical for attempt 2),
   parses the stamp and the head cut SEPARATELY, and never
   fabricates the created−started head-offset contract.
   Sidecar-usable route (the contract is LIVE): an unusable stamp
   beside a usable head cut is RECOVERED from ground truth —
   created = started_at_utc + head_cut, exact, the fixlog note says
   so; an unusable head cut is a typed FixFailed naming
   canonical.trim. No-sidecar route (nothing downstream consumes
   created − started): a junk head-cut VALUE or shape keeps a
   parseable stamp at head 0.0 (r19 #10); an unusable stamp keeps
   L1's omit-and-synthesize. ABSENT trim/head_cut_s stays head 0.0
   (the documented v1-optional shape); PRESENT-but-junk is
   destroyed evidence and never silently reads as 0.0 on the
   live-contract route. OverflowError joins every parse/arithmetic
   guard (JSON bigints, Infinity, '1e999', large-but-finite 1e18).
   The sidecar probe _v1_sidecar_started applies the M2 usability
   rule at BOTH locations the fix can see the sidecars in (work
   root on a first run, raw/ on a re-entrant run), locating each
   file INDEPENDENTLY — a crash between the per-file moves splits
   the pair across root and raw/ and the probe still finds it. A
   present-but-M2-unusable sidecar still degrades to
   omit-and-synthesize BUT the raw verify warn-skips on the same
   _utc class, so no consumer acts on the synthesized stamp.
   STATED DEVIATION (plan §0): the finder's matching-based head
   recovery (_seed_shift_record machinery) was NOT adopted — new
   machinery would land effectively unreviewed; the typed refusal
   is the finder's own unrecoverable fallback and the data-safe end
   state; a post-checkpoint enhancement if Adnaan wants it. Do not
   re-raise the deviation itself. Defects (a fabricated head-offset
   fact any consumer acts on, a recovery arithmetic error, a
   refusal that leaves the work dir mutated, a junk shape that
   still crashes the route) are findings. Amends entry 86. AMENDED by
   r-loop 20 N1 (entry 96): iteration 20 proved four bypasses of this
   entry's resolution — the falsy or-0.0 short-circuit, the
   destroyed-canonical head-0 recovery, the unguarded post-write
   emit, and the stamp-side overflow misattributed to the head — all
   closed; see entry 96 for the current shape.
91. r-loop 19 M3 (r19 #3, cb42dae): a short v1 body row pads to the
   header width right after the header check in fix_v1_to_v2 —
   each missing cell degrades to '', the empty value every
   downstream read already handles ('' keys split to none,
   _parse_motion('') is 0.0). Padding inside _read_csv itself was
   deliberately NOT done (it would silently mask raggedness from
   nine consumers at once, including checker-adjacent surfaces);
   the other eight consumers run only on v2 routes where a ragged
   CSV maps to QA_FAIL_UNMAPPED first. Longer-than-header rows
   already degrade (fixed-position reads below the header width;
   the conversion rebuilds rows fresh). Defects (another consumer
   that actually meets a ragged row, a pad that fabricates data a
   checker or delivery consumer trusts) are findings. Amends
   entry 86.
92. r-loop 19 M4 (r19 #5, payment-surface, RULED fix-now by Adnaan
   2026-08-20, e9f913b): ledger.supersede with the '' unknowable-
   md5 sentinel over a REAL stored md5 PRESERVES the payment
   stamps + duration_raw_s + tree seal (state/attempts/bin/
   reasons/delivered fields/dossier archive/breadcrumb all still
   reset); the download-time deferral owns byte adjudication —
   the stamps clear at download iff the bytes prove CHANGED (the
   existing prev_md5 breadcrumb + durable ZIP_ADJ_CHANGED marker,
   unchanged). The never-downloads case is money-safe: preserved
   stamps mean no re-count. A REAL new md5 keeps the immediate
   full clear. The '' quarantine heal (entries 25/32) is NOW
   CONSISTENT with the supersede twin. Reports arm 2/4 keys on
   the durable event vs counted_at, not row state — unaffected
   (both r13 zip pins green). Defects (a double-pay that still
   slips through, genuinely-new-bytes hours stranded by a
   preserved stamp, a preserved-but-reset field combination that
   confuses the sheet math) are findings. Amends entry 55. AMENDED by
   r-loop 20 N3/N4 (entries 98/99): iteration 20 proved both invited
   defect classes — the preserved LABELS-only accepted mark stranding
   a delivered re-run's hours (N3 clears it on non-DELIVERED rows)
   and the ''-semantics self-defeat over stored-'' slots (N4:
   unknowable regardless of stored md5 + breadcrumb adjudication for
   real-over-''); see entries 98/99 for the current shape.
93. r-loop 19 M5 (r19 #11, RULED fix-now by Adnaan 2026-08-20,
   6129872): validate's aux['has_raw'] routes through
   fix.has_raw_sidecars (lazy function-scope import; no module
   cycle), so the stored fixable field and the reject labels are
   truthful — pre-M5 a corrupt-sidecar session with an unmapped qa
   FAIL stored fixable=TRUE, the fix phase (fed has_raw=False by
   the gate) planned nothing and rejected 'unfixable', and the
   ruled reject surface degraded to the false bare fix-failed
   marker. raw_by_sid deliberately stays existence-based: the
   engine's raw verify and shift-record seeding degrade internally
   by design (G4/H9b) and must keep seeing existing files. Every
   aux['has_raw'] consumer swept: map_reasons:637 inherits (THE
   fix); both drivers' plan gates already call the gate directly
   and now AGREE with the stored field; no other reader. Defects
   (a consumer that still drifts, a truthful-label regression)
   are findings. Amends entry 87.
94. r-loop 19 M6+M7 (r19 #12 + #13, 78e384e): M6 —
   fix_sessionjson_recompute's whole emit block degrades on
   OverflowError to the designed unusable-stamp arm (synthesize
   from now), covering both the created+duration addition and the
   negative-offset astimezone sibling near datetime.max (the
   entries-52/57 doctrine extended to the repair chain's own
   arithmetic). M7 — 'session.json unreadable' and 'session.json
   is not a JSON object' now map to STR_SJ_INVALID (fixable): the
   stale r-loop-3 'no fix can clear them' rationale was falsified
   by r-loop 7's _read_session_json {} rebuild —
   FIX_SESSIONJSON_REWRITE rebuilds a valid session.json from
   video+CSV ground truth, precedes any retranslate in every plan,
   and STR_SJ_INVALID never routes through one (entry-11 semantics
   preserved). Ragged rows and unreadable frames.csv stay
   deliberately unmapped (the rewrite cannot clear those). Defects
   (an overflow shape that still crashes the repair chain, a
   mapped FAIL the planned fix cannot clear, a needle-order
   collision) are findings. COMPLETED by r-loop 20 N2 (entry 97):
   the ended emit joined the guard and the except arm's re-addition
   is guarded (a still-overflowing duration degrades to
   ended = created).
95. r-loop 19 M8 (r19 #7/#8/#9, tests-only, 1a023fb): the three
   arming-gate-invisible L arms carry failing-side pins in
   test_r_loop19.py — L1's non-dict-canonical guard (both shapes
   convert with the folder-name session_id and a recompute-rebuilt
   session.json), L1's str() coercion at the stamp parse (both
   non-str shapes convert with a synthesized conformant stamp),
   and L2's errors='replace' at the gate's metadata read (latin-1
   bytes inside a metadata string value parse under replace and
   the gate answers True). The finders' exact deletion/revert
   mutants — each proven FULL-gate-green at 802/802 pre-M8 — now
   each fail exactly their own pin (site-isolated, fixed-tree
   scratch proofs). No production code changed. Amends entries
   86/87.
96. r-loop 20 N1 (r20 #1+#5+#10+#11, f6fa524): fix_v1_to_v2's
   stamp/trim resolution completed. Falsiness is not absence: the
   head cut parses the RAW value (no or-0.0); only a genuinely
   ABSENT head_cut_s key is the documented v1-optional head-0 shape;
   None and bool are refused before float() (bool is an int
   subclass). Destroyed trim evidence — an unreadable/non-object
   session.json or a non-dict canonical (trim_evidence_ok =
   canonical parses to a dict) — REFUSES typed on the live-sidecar
   route naming session.json; a readable well-formed canonical with
   no trim key keeps the true head-0 reading. The created_at emit
   string resolves INSIDE the pre-write resolution block (_emit_utc)
   under an OverflowError guard: no-sidecar → the stamp is unusable,
   omit-and-synthesize; sidecar → ground-truth recovery; ground
   truth that itself cannot emit refuses typed ('cannot recover').
   A stamp+head_cut overflow is disambiguated: if
   timedelta(head_cut) constructs, the STAMP is the junk side
   (omit/recover — the committed unusable-stamp disposition); only a
   head whose own timedelta overflows keeps the canonical.trim
   refusal. No-sidecar arms keep the r19 #10 semantics (junk head
   VALUE keeps a parseable stamp at head 0.0). Defects (a falsy or
   destroyed shape that still fabricates a head fact, a refusal that
   mutates the work dir, a genuinely-optional shape wrongly refused)
   are findings. Amends entries 86/90.
97. r-loop 20 N2 (r20 #4+#9, 002004f): fix_sessionjson_recompute's
   overflow degrade completed — the ended_at_utc emit resolves
   inside the guard on both the main and degrade arms, and the
   except arm's re-run addition is itself guarded: a duration whose
   timedelta cannot be added even to now (crafted/corrupt container
   duration) degrades to ended = created (zero-length; the checker's
   duration compare owns the junk duration, G4). Defects (an
   overflow shape that still crashes the chain, the degrade emitting
   a non-conformant stamp) are findings. Completes entry 94.
98. r-loop 20 N3 (r20 #2≡#6, dde54ab, payment-surface — direction
   derived from standing rulings, flagged for the checkpoint): BOTH
   '' preserve arms (ledger.supersede's unknowable arm and the
   quarantined-path heal's preserve arms) clear accepted_reported_at
   when the row is NOT DELIVERED — on those writers' populations
   (REJECTED/QUARANTINED slots) the mark is the refix doctrine's
   LABELS-only mark, and preserving it stranded a later-DELIVERED
   identical-bytes re-run's hours off every sheet silently and
   forever; clearing costs at most one re-printed reject label (no
   money moves through a label). uploaded_reported_at +
   duration_raw_s + tree_sealed_at stay preserved exactly as ruled
   (M4, entries 25/32); a DELIVERED row keeps its accepted mark (an
   hours mark; belt-and-braces — no caller supersedes DELIVERED,
   pinned). COHORT UPDATE recorded: r9's
   test_identical_md5_heal_preserves_payment_stamps now asserts the
   labels mark CLEARED (its money asserts and counted-once
   conservation assert are unchanged). Defects (an hours mark
   cleared anywhere, a labels mark that still strands hours, a
   label double-print that carries money) are findings. Amends
   entry 92.
99. r-loop 20 N4 (r20 #3+#7, 44e72b3, payment-surface — direction
   derived from standing rulings, flagged for the checkpoint): ''
   means unknowable REGARDLESS of the stored md5 — zip_unknowable =
   not new_md5, so a second '' supersede over the stamps+''-md5 row
   the first one created PRESERVES too (the breadcrumb is written
   only over a REAL prior md5, so the deferral's newest-breadcrumb
   lookup keeps naming the counted bytes). A REAL md5 landing over a
   stored-'' slot adjudicates against the newest prev_md5 breadcrumb
   via the shared ledger.latest_prev_md5 helper (equal = provably
   identical bytes, preserve the payment columns; different or no
   breadcrumb = the full clear stands); the heal's clears
   computation runs the same adjudication. N3's labels rule rides
   every preserve arm. The download-time deferral's inline
   breadcrumb query is deliberately unchanged (identical behavior
   for its ''-md5 population; the helper adds a rowid tiebreak the
   deferral's population cannot need). Defects (a ''-writer that
   still full-clears without byte evidence, a breadcrumb-equal write
   that clears, a DIFFERENT-md5 write that preserves, the helper
   picking up a ZIP_ADJ_CHANGED marker) are findings. Amends
   entries 92/55.
100. r-loop 20 N5 (r20 #8, tests-only, 1d82472): the M1 sidecar
   probe's degrade envelope carries failing-side pins — (a)
   player-typed latin-1 inside a metadata string value parses under
   errors='replace' and the recovery completes with the exact
   ground-truth stamp; (b) a PRESENT-but-truncated metadata.json
   reads as no-sidecars and the junk stamp omit-and-synthesizes.
   The finders' exact mutants (drop-errors and delete-try/except,
   both proven FULL-gate-green at 821 pre-N5) each fail exactly
   their own pin (site-isolated, fixed-tree scratch proofs). No
   production code changed.
`

const LANES = [
  { key: 'regressions-r20', prompt:
    `Hunt for bugs INTRODUCED by the ITERATION-20 N fix set — commits f6fa524 (N1: fix_v1_to_v2's stamp/trim resolution completed — the head cut parses the RAW value with None/bool refused before float() and only a genuinely ABSENT head_cut_s key reading as the v1-optional head 0; destroyed trim evidence (unreadable/non-object session.json or non-dict canonical, trim_evidence_ok = canonical parses to a dict) refuses typed on the live-sidecar route; the created_at emit string resolves INSIDE the pre-write resolution block via _emit_utc under an OverflowError guard; a stamp+head_cut overflow is disambiguated by whether timedelta(head_cut) constructs — stamp-side junk recovers/omits, head-side junk keeps the canonical.trim refusal; a ground truth that cannot emit refuses 'cannot recover'), 002004f (N2: fix_sessionjson_recompute — the ended_at_utc emit joined the OverflowError guard on both arms, and the except arm's re-run addition is guarded: a still-overflowing duration degrades to ended = created), dde54ab (N3, payment-surface: BOTH '' preserve arms — ledger.supersede's unknowable arm and the quarantined-path heal's preserve arms — clear accepted_reported_at when the row is NOT DELIVERED, the LABELS-only mark; money marks uploaded/duration/seal stay preserved; the r9 heal pin was re-modeled accordingly, cohort update recorded in the commit), 44e72b3 (N4, payment-surface: zip_unknowable = not new_md5 so ''-over-'' preserves with the breadcrumb written only over a REAL prior md5; a REAL md5 over a stored-'' slot adjudicates against the newest prev_md5 breadcrumb via the new shared ledger.latest_prev_md5 — equal preserves, different-or-none clears; the heal's clears computation runs the same adjudication), 1d82472 (N5 tests-only: failing-side pins for _v1_sidecar_started's errors='replace' read and its OSError/JSONDecodeError degrade) and the floor bump f66d3ed (SUITE_FLOOR 836) — all written by the same executor whose work you are checking, and ALL LANDED UNREVIEWED (you are the FIRST review these commits get); this loop's worst findings have always been regressions from the previous iteration's fixes. Read each commit with git show IN FULL, then attack: (f6fa524) the genuine-absent arm on the LIVE route (a readable well-formed canonical with NO trim key beside a junk stamp now RECOVERS started + 0.0 — is head 0 defensible there when every genuine HumynCapture v1 records a >=5s trim, or is that the fabrication door re-opened for one shape?), the trim_evidence_ok rule against every session.json read shape (a dict sj whose canonical is a dict but arrived mojibake-replaced; the M8 non-dict-canonical NO-sidecar pins' cohort overlap), the disambiguation try (both-sides-junk combos: near-max stamp + near-max-but-constructible head; does blaming the stamp ever fabricate where refusing was right?), the _emit_utc naive-stamp interplay (the r15 #7 tzinfo repair upstream — can a naive near-max stamp shift under the host TZ before the guard sees it?), microsecond precision of the float-seconds recovery arithmetic, and whether every one of the three refusal messages really leaves the work dir byte-identical for attempt 2; (002004f) the degrade's internally-inconsistent output (duration_ms/duration_seconds written from the junk probe beside ended = created — trace what the checker then FAILs, what maps where, and whether the rewrite can loop: rewrite -> same degrade -> same FAIL deterministically burning attempts), and the synthesize-from-now stamp against the N1/M1-sealed head-offset contract (a sidecar-usable session whose recompute degrades — prove no consumer acts on the synthesized stamp); (dde54ab) BOTH money directions of the labels-clear against a scratch sqlite ledger (a re-REJECT after the '' supersede: the reasons were reset to [] — whose labels print on the next sheet, can the OLD generation's labels reappear anywhere, and is exactly-once preserved for the label surface?), the DELIVERED guard's populations (can any caller reach supersede/heal-preserve with a DELIVERED row carrying an hours mark?), and the heal-arm sweep (both preserve arms: identical-md5 AND ''-vmd5 — verify both really clear, and that the REFUSED heal (K1 guard) still preserves everything); (44e72b3) the breadcrumb parse surface (the heal's re-registered detail embeds ds.drive_path — can a player-typed folder name containing the literal 'prev_md5=' poison latest_prev_md5's LIKE match or the deferral's rsplit? prove or refute by execution), the never-had-a-real-md5 population (a zip-origin root counted while md5='' then ''-superseded: no breadcrumb exists — what preserves/clears, and does the durable-event rule (entry 55 arm 2) still own the stamp skip?), latest_prev_md5's ts-ordering vs the deferral's inline query under same-second events, and the real-over-'' preserve vs the deferral being skipped afterward (row md5 real again — prove no path needed the deferral to run); (1d82472) do the two pins pass for the RIGHT reason (mutate scratch copies yourself), and the cohort gaps that REMAIN in test_r_loop20.py (is any NEW N arm pinned only where it is a no-op — route, game, or state-wise?); (f66d3ed) the floor arithmetic (840 passed − 4 = 836) and script-vs-runbook agreement. Report at most 5 findings — the ones you can PROVE.` },
  { key: 'payment-split', prompt:
    `Attack the two-mark payment logic in pipeline/reports.py (build_sheet_rows, _tree_has_uncounted_accepted, _mem_reconcile_failures, _stamp, mark_uploads_reported, mark_accepted_reported, pending_daily_send, write_payment_sheet) plus its callers in pipeline/run.py (fresh + resume daily paths, the wedge machinery) and tools/recal_regen_sheets.py, tools/recal_refix_reset.py, tools/recal_rebuild_reset.py, pipeline/ledger.py (supersede, update_where_md5, paid_pieces) and pipeline/ingest.py (quarantine heal). Prove or disprove, by RUNNING code against a scratch sqlite ledger in /tmp: (1) uploaded hours are still counted exactly once across all sheets; (2) every DELIVERED node's hours reach exactly one sheet, and none is lost — including under duration_raw_s=NULL roots (the new third re-entry arm) and re-cut trees (the new reconcile-against-DELIVERED void); (3) reject labels appear exactly once; (4) no root can re-enter forever silently; (5) a kill between the sheet send and either stamp cannot double-pay, and a supersede/heal INSIDE the stamp window cannot strand the corrected re-upload (the new md5 compare-and-set); (6) the refix per-piece payment memory, the sealed-root refusal, the supersede/heal clears, the zip-heal deferred clear, the wedge machinery (F1 guard + F5 durable alert) and the regen interlock (F10) behave as documented; (7) the '## Reject detail' section and the CSV columns describe the same population. Include the r-loop-13 changes on your surface: G1's durable '' adjudication (supersede breadcrumb, ZIP_ADJ_CHANGED marker, _stamp's counted_at arm), G5's rebuild-reset refusal/preserve/record, and G6's pending_daily_send_detail kinds — now joined by the r-loop-14 changes: H1's PRE-BUILD counted_at capture (one string into the record and both stamp calls; the resume replays it) and H3's split-artifact discard in the rebuild-reset teardown — and by the r-loop-16 change ON THIS SURFACE: J5's fail-CLOSED day-agnostic resume scan (RULED, entry 78 — run.py's send_daily_report_if_due now routes its listing through reports._report_day_dirs and refuses the tick on None; verify both sides of the guard, the regen-guard interplay under asymmetric listing failures, and that nothing is built/sent/stamped on a refused tick) — and by the r-loop-19 M4 change (entry 92, reviewed by iteration 20) NOW AMENDED by the r-loop-20 changes ON THIS SURFACE, LANDED UNREVIEWED (prime suspects): N3's labels-mark clear on BOTH '' preserve arms (entry 98 — accepted_reported_at cleared when the row is not DELIVERED at supersede and at the heal's preserve arms; verify no hours mark can be cleared, no labels mark still strands a delivered re-run's hours, the label surface stays exactly-once, and the re-modeled r9 heal pin still pins the counted-once conservation) and N4's ''-means-unknowable + breadcrumb adjudication (entry 99 — zip_unknowable = not new_md5; real-over-'' preserves iff equal to the newest prev_md5 breadcrumb via ledger.latest_prev_md5; verify BOTH money directions against a scratch ledger across the ''-over-'', real-over-''-equal, real-over-''-different and never-had-a-real-md5 populations, plus the deferral interplay now that a real md5 skips it). Read the RULED design in items 7, 21, 22, 34-35, 39-40, 45, 55-61, 78, 92, 98 and 99 of the accepted list first — the DESIGN is settled, DEFECTS IN IT are exactly what you are hunting.` },
  { key: 'driver-core', prompt:
    `Review pipeline/continuous.py end to end: lanes, ownership/claims, cooldowns, the media cap, autoscale, drain/shutdown, digest, housekeeping cadences. Look for races, lost work, states that can never be left, and counters that can mislead an operator at the flip. Include the r-loop-11/12 changes on your surface: the validate-time duration backfill in _validate_one, the stint-scoped + reclaim-re-armed disc_media anchor in _stuck_lines, and the RECLAIM_MARKER event's interplay with every other event-anchored query — now joined by G1's ZIP_ADJ_CHANGED same-state DOWNLOADING marker (r-loop 13): verify it cannot perturb the stint anchors, the worker-death count, disc_media, the digest queries, or the reclaim filters — and by the r-loop-14 changes: H4's rclone-stderr normalization at ingest.run_rclone (every alert embedder inherits; verify the dedup cadence and that no consumer depended on the timestamped text) and H5's DISCOVERED vanished-folder arm (its genuine DISCOVERED->QUARANTINED transitions now flow into the digest's quarantine counters and drain the undownloaded backlog — verify the guard cannot prune live rows) — now joined by the r-loop-15 change: I7's rename-re-upload coaching on the vanished arm's detail and loud line (the same-path dead end is RULED design, entry 70 — verify the new text cannot perturb any event-anchored query and that a renamed re-upload really does process as a separate session) — and by the r-loop-17 change: K1's identity guard on the quarantined-path heal (entry 80 — verify the refusal cannot strand a LEGITIMATE correction (same-player, INT_PATH, byte-identical), that the vanished-arm -> heal pipeline respects the guard across consecutive scans, that the per-scan stderr flag has no alert/storm implications, and that no takeover path routes AROUND the guard through supersede or the download-time backfill).` },
  { key: 'fix-validate', prompt:
    `Review pipeline/fix.py, pipeline/validate.py and pipeline/gate.py end to end: plan ordering, the fix budget (FIX_RETRIES=2), reason mapping and bins, what is fixable vs unfixable, and every path that can reject a session that should have been delivered or deliver one that should not. Pay particular attention to the QA-string mapping table: a needle that matches the wrong FAIL, a FAIL with no needle, or a mapping whose planned fix cannot clear the FAIL it was mapped from. Include the r-loop-11/12 changes on your surface: fix_key_hygiene's AND fix_actions_context's session-keybind resolution (F4 + r12 #5/#8), fix_lagshift_csv's host re-raise (F3), the probed_duration_s threading through _metrics (F6/r12 #14), the cut-less structural-first plan order (r12 #6), and the notif/chat map-time CNT_SHORT arms (r12 #7) — now joined by the r-loop-13 changes: G2's rerouted-only game_override in _dispatch (the plan carries the fact) and G3's dur_true edge-vs-mid classification in _map_flags — and by the r-loop-14 changes: H2's ledger_game anchoring of the non-reroute retranslate branch (slug + resolve_keybind fallback; session keybind.json still wins) and H6's _joint_edge_short map-time composition (joint head+tail CNT_SHORT from plan_fixes' exact cut geometry, skipped when an individual CNT_SHORT exists) — now joined by the r-loop-15 changes: I1's caseless-token exemption (the INP_TOKEN_CASE needles stay but the FAIL stops firing for symbol keys — verify no consumer still assumes it fires), I2's credited-token strip in fix_key_hygiene and, via _v2_rows, in retranslate_from_sidecars (verify both fix routes now CONVERGE on symbol-bind and combo-bind sessions instead of burning attempts, and that no credited press is lost), and I4's naive-stamp guard in fix_v1_to_v2 — and by the r-loop-16 changes: J2's bound-aware INP_OSKEYS trigger (aux['bound_literals'] resolved in validate_session via _session_bound_literals, filtered in map_reasons by key_canonical; verify the F4-chain agreement, the degrade note, and that unbound pollution still clears through hygiene) and J6's one-attempt hygiene repair of a foreign bare-',' cell — and by the r-loop-17 changes: K2's session-keybind resolution in fix_v1_to_v2 (entry 81 — verify the root/raw resolution against every caller state, the patches-over-session-bind ordering, and that the built-in control really is unchanged) and K3's guarded _active on the FIX_ACTIONS_CONTEXT route (entry 82 — verify the degrade semantics against the context strip) — and by the r-loop-18 changes, BOTH LANDED UNREVIEWED (treat as prime suspects): L1's whole-function degrade in fix_v1_to_v2 (entry 86 — verify every degrade arm against the conversion's consumers: the PARSED-values has_motion rule and the blank-vs-zero motion form, the omitted-stamp/fix_sessionjson_recompute synthesis interplay, the {}-normalized canonical/trim, and the _read_session_json arm feeding the stray-key_binding early return) and L2's usable-sidecars plan gate (entry 87 — verify BOTH sides: unusable raw/metadata.json plans the CSV-level fixes, usable metadata keeps the retranslate supersede, a missing file still reads False, no has_raw consumer drifts from the gate, and trace what the QA_FAIL_UNMAPPED route now plans when the sidecars are unusable) — and by the r-loop-19 M set (entries 89-95, reviewed by iteration 20) NOW COMPLETED by the r-loop-20 N set, ALL LANDED UNREVIEWED (prime suspects, entries 96-100): N1's completed stamp/trim resolution in fix_v1_to_v2 (entry 96 — verify every arm: the raw-value head parse with None/bool refused, the trim_evidence_ok destroyed-canonical refusal vs the genuine-absent head-0 arm, the pre-write _emit_utc resolution on both routes, the overflow disambiguation, the three refusal messages' byte-identical property and their kind/budget interplay under FIX_RETRIES=2) and N2's completed recompute degrade (entry 97 — verify the degrade's output against the checker: duration fields from the junk probe beside ended = created, can the rewrite loop, and does the synthesized stamp stay out of the sealed head-offset contract). Entry 82's one remaining NOTED site is translator/sync.py's bare float: prove a concrete harm path through it and it is a normal finding.` },
  { key: 'translator', prompt:
    `Review the translator/ package and tools/analyze_sample.py, tools/retrim_v2_session.py: binning, PTS handling (absolute container clock vs frame-relative clock — this class of bug was found in trim.py, look for MORE of it), keybind resolution, qa-v2 checks, and every read of an untrusted player-supplied file (the r-loop-11 session_id traversal fix F9 closed ONE such hole — hunt for siblings: other metadata fields reaching paths, filenames, subprocess argv, or file writes unsanitized). Look for crashes that become QUARANTINED instead of a typed reject, and for silent data corruption in delivered frames.csv. Include the r-loop-13 changes on your surface: G4's OverflowError degrade arms (v2.py's numeric tuple, ts-stats block, duration compare, _verify_against_raw; analyze_sample's _num) and G7's safe_session_id + containment in tools/fix_actions_from_v2.py and tools/fix_sync_from_v1.py — now joined by the r-loop-14 changes: H7's control-character rejection in safe_session_id (all five join sites) and H8's dur_true (probed-or-claim) verdicts in analyze_sample's build_verdict — and by the r-loop-15 changes: I1's caseless-key grammar exemption in check_session_v2 (symbol keys ship; cased tokens still flag), I2's credited-literal strip in _v2_rows (required rules param; resolve_actions credited_out; motion never strips keys), I5's 200-byte session_id bound at the shared decision point, and I8's shutil.copy2 delivery copy in tools/fix_sync_from_v1.py (its remap also applies the I2 credit rule) — and by the r-loop-16 changes: J1's strict-encode byte measure in safe_session_id (unencodable ids fall back) and J6's 'Comma' named display token (',' in _KEY_DISPLAY, the 'comma' literal alias; the checker's comma arm untouched — trace the round-trip through every display consumer) — and by the r-loop-17 change: K3's guarded _active in apply_context_to_rows (entry 82 — a junk dx/dy cell is not motion; verify against _num_cell/fix_sentinels semantics, and note sync.py's input_track_from_rows bare float over the SAME cells REMAINS NOTED-NOT-SETTLED after r-loop 18: prove the reasons-ordering shield can break and it is a finding) — and by the r-loop-18 change (LANDED UNREVIEWED): L1's degrade-everything fix_v1_to_v2 (entry 86) leans on this surface — fix_sentinels' _parse semantics, _read_session_json, fix_sessionjson_recompute's stamp synthesis — verify the conversion's output still satisfies check_session_v2 on every degrade arm (blank dx/dy vs the mouse-capture checks, a synthesized stamp vs the duration compare, a folder-name session_id vs the id grammar and the safe_session_id joins) — and by the r-loop-19 changes on this surface (entries 90/91/94, reviewed by iteration 20) NOW COMPLETED by the r-loop-20 changes, LANDED UNREVIEWED (prime suspects): N1's completed stamp/trim resolution (entry 96 — verify the conversion's output still satisfies check_session_v2 on the recovery, all-three-refusal, genuine-absent-head-0, disambiguated-overflow and emit-degrade arms: a recovered created_at vs the duration compare and the raw verify, a synthesized stamp vs the grammar) and N2's completed recompute degrade (entry 97 — the ended = created zero-length degrade beside junk duration fields vs qa-v2's checks: what FAILs, what maps where, does anything loop).` },
  { key: 'ops-tools', prompt:
    `Review tools/vm_setup.sh, tools/run_suite.sh, tools/recal_regen_sheets.py, tools/recal_verify_tree.py, tools/recal_rebuild_reset.py, tools/recal_refix_reset.py, pipeline/systemd/* and FLIP_RUNBOOK.md. Look for anything that can leave production unarmed, delete live data, block the payment endgame, or give an operator a false diagnosis. Include the r-loop-11/12 changes on your surface: the extended, fail-CLOSED pending interlocks (F10 + r12 #11/#13, now consulted by the daily send too), rebuild-reset's held run lock (r12 #3/#12), and the wedge machinery both reset tools sit behind — now joined by the r-loop-13 changes: G5's payment-evidence refusal + --allow-reported preserve/record path and G6's kind-specific PENDING_SEND_GUIDANCE diagnosis in both tools — and by the r-loop-14 changes: H3's split-artifact discard in the rebuild-reset teardown (shared _discard_split_artifacts + -analysis wipe) and the H9c-adjacent fix_sync_from_v1 repair (resolve_actions tuple unpack — the tool crashed on every real run before it) — now joined by the r-loop-15 changes: I8's portable shutil.copy2 delivery copy in fix_sync_from_v1 (the H9c twin runs UNSTUBBED on both hosts; the VM gate is the Linux prover) and I4's sweep guard in tools/retrim_v2_session.py (naive stamp repaired to UTC before astimezone) — and by the r-loop-17 tests-only pins on this surface: K5's overlap frame in the fix_sync_from_v1 remap pin (entry 84) and K6's coached rename-re-upload pins (entry 85 — the download-time twin drives ingest.download with a fake rclone; verify the pin exercises the real dedupe arm) — and by the r-loop-20 floor pin f66d3ed (SUITE_FLOOR 836 in tools/run_suite.sh + FLIP_RUNBOOK §6b — verify the arithmetic against the 840-passing suite and that script and runbook agree) and the r-loop-20 ledger changes, LANDED UNREVIEWED (entries 98/99 — payment-surface; verify the reset tools and the verify-tree tool still diagnose truthfully over the new row shapes: a labels-cleared superseded row, a ''-over-'' preserved row, and a breadcrumb-preserved real-md5 row).` },
  { key: 'tests-coverage', prompt:
    `Attack the TEST SUITE itself. The suite is the flip's ARMING GATE (tools/run_suite.sh, floor 836, currently 840 passing). Find fixes in ${LOOP_START}..HEAD whose behaviour survives deletion with the suite still green — apply the mutation in a scratch copy OUTSIDE the repo and run pytest to PROVE it, then restore. Also look for tests that assert a bug, tests that pass for the wrong reason (re-running SQL instead of the code, asserting source text, a fixture that makes the assertion vacuous), and orphaned threads or leaked state between tests. A fix with no real test is a real finding. A test COHORT that runs a pin only where the pinned behavior is a no-op (the r15 #6 class — every H2 test used kamla) is a real finding too. The r-loop-20 N set (test_r_loop20.py — commits f6fa524..1d82472, incl. the N5 pins and the re-modeled r9 heal pin test_identical_md5_heal_preserves_payment_stamps) landed UNREVIEWED and is your PRIME target: a pin that passes for the wrong reason, an N1/N3/N4 arm with no failing-side test, a re-modeled pin that no longer pins what it originally did, or a cohort where the fixed lines are no-ops.` },
]

const FINDING = {
  type: 'object', additionalProperties: false,
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['severity', 'file', 'line', 'title', 'claim', 'scenario', 'fix'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          file: { type: 'string' },
          line: { type: 'integer' },
          title: { type: 'string' },
          claim: { type: 'string', description: 'Mechanism, with real line numbers and real command output proving it' },
          scenario: { type: 'string', description: 'Concrete end-to-end path from real input to real harm' },
          fix: { type: 'string' },
        },
      },
    },
  },
}

const VERDICT = {
  type: 'object', additionalProperties: false,
  required: ['refuted', 'confidence', 'evidence', 'reasoning'],
  properties: {
    refuted: { type: 'boolean' },
    confidence: { type: 'string', enum: ['low', 'medium', 'high'] },
    evidence: { type: 'string' },
    reasoning: { type: 'string' },
  },
}

phase('Find')

const results = await pipeline(
  LANES,
  lane => agent(
    `You are reviewing a production data pipeline at ${REPO}. Branch main, HEAD carries the r-loop-20 N fix set (f6fa524..f66d3ed: N1-N5 + floor 836, on top of the r-loop-19 M set a6af11d..a239fad, the r-loop-18 L set e197244..74b4a17, the r-loop-17 K set c99309e..6f97449 and the r-loop-16 J set c4f1fda..cba8fd2; suite 840 on the Mac gate at floor 836 — the VM gate is pending a gcloud reauth and runs before anything further ships). This is review iteration 21 (RULED Adnaan 2026-08-20: if this pass is NOT quiet, its confirmed findings are fixed in-iteration — the O set — and iteration 22 reviews them as THE CHECKPOINT pass; if it IS quiet, the independent e2e launches next). The N set landed UNREVIEWED — this pass is the first review those commits get. A false confirm burns an iteration on a non-bug; a false quiet sends a defect toward the e2e and the flip — verify accordingly. Scope (plan §4, standing): review your lane's surface END TO END — whole-codebase, not just a delta pass — AND, wherever your lane touches code changed by the r-loop-20 N commits, treat those commits as the prime suspect: they were written by the executor whose work this pass audits, they landed unreviewed, and this loop's worst findings have always been regressions from the previous iteration's fixes.

LANE: ${lane.key}
${lane.prompt}

${ACCEPTED}

RULES:
- VERIFY BEFORE CLAIMING. Read whole functions, not greps. RUN the code — write probe scripts in /tmp (NEVER inside ${REPO}) and paste real output.
- Do NOT modify any file inside ${REPO}. Leave the tree exactly as you found it. If you mutation-test, do it in a scratch COPY outside the repo.
- A finding needs a MECHANISM (proved) and HARM (a concrete path to a wrong reject, lost data, lost money, production down, or an operator misled). "This could be cleaner" is not a finding.
- Severity: blocker = data loss / production down / wrongful reject at scale. major = real harm on a plausible path. minor = correctness or ops-clarity issue with bounded harm.
- Report at most 5 findings. Quality over quantity — every one will be adversarially refuted by two independent agents.
- If you find nothing real, return an empty list. That is a valid and useful answer.`,
    { label: `find:${lane.key}`, phase: 'Find', schema: FINDING }
  ),
  (res, lane) => {
    const found = (res && res.findings) || []
    if (!found.length) return []
    return parallel(found.map(f => () =>
      parallel([
        `Lens A - DOES IT EXECUTE THAT WAY. Ignore the narrative. Read the whole function in the real file and RUN the real code with the exact inputs described. If the claim quotes output, reproduce it yourself. A claim you cannot make reproduce is refuted.`,
        `Lens B - IS THE HARM REACHABLE. Grant the mechanism. Attack the harm chain: is that state actually reachable in this codebase's control flow, or blocked upstream by a guard, an early return, a caller that never passes those args, or a state machine that cannot produce that combination? Trace every caller. Unreachable harm = refuted.`,
      ].map((lens, i) => () =>
        agent(
          `You are an adversarial verifier in the git repo at ${REPO}.

REFUTE this claimed bug. Default to refuted=true when uncertain — a finding must EARN survival with reproduced evidence.

SEVERITY: ${f.severity}
FILE: ${f.file}:${f.line}
TITLE: ${f.title}
CLAIM: ${f.claim}
SCENARIO: ${f.scenario}

${lens}

${ACCEPTED}
If the finding is really one of the accepted behaviours above, refute it on that ground.

Do NOT modify any file inside ${REPO}; probe in /tmp only. Quote real line numbers and real output; never invent output.`,
          { label: `refute:${lane.key}:${i === 0 ? 'exec' : 'harm'}`, phase: 'Refute', schema: VERDICT }
        )
      )).then(votes => {
        const v = votes.filter(Boolean)
        const refuters = v.filter(x => x.refuted).length
        return { ...f, lane: lane.key, refuters, votes: v.length, survives: refuters < 2, verdicts: v }
      })
    ))
  }
)

const all = results.flat().filter(Boolean)
const confirmed = all.filter(f => f.survives)
const killed = all.filter(f => !f.survives)
const blockers = confirmed.filter(f => f.severity === 'blocker')

log(`r-loop 21: ${all.length} raised, ${confirmed.length} confirmed (${blockers.length} blockers), ${killed.length} refuted`)

return { confirmed, killed, counts: { raised: all.length, confirmed: confirmed.length, blockers: blockers.length } }
