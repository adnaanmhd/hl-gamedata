// r-loop-15 review workflow (CONFIRMATION pass per R8_IMPLEMENTATION_PLAN §4:
// if NOT quiet the executor STOPS and hands Adnaan the confirmed list — no
// fixing in-iteration). Edited from the committed iter14 snapshot: regressions
// lane retargeted at the ITERATION-14 H FIX SET (commits 1dd69fa H1, 1d54775
// H2, a13e2ac H3, 924755b H4, c731e32 H5, 25e900e H6, 492a076 H7, e01edc7 H8,
// 747422e H9 + floor 37d7d88 — the executor's own, treat as prime target),
// accepted-behaviours 61-69 appended for the H rulings (mechanism defects stay
// reportable; 17/55/56/57/58/60 amended where the H fixes supersede or
// complete their mechanics), suite refreshed (749/floor 745). LOOP_START,
// 2-vote refute discipline and all 7 lanes kept (driver-core conditional
// resolved NOT-triggered).
export const meta = {
  name: 'flip-review-iter15',
  description: 'r-loop 15: confirmation-pass adversarial review of the continuous-pipeline flip work, 2-vote refute',
  phases: [
    { title: 'Find', detail: 'lane finders: whole-codebase + delta + regressions from the r-loop-14 H fixes' },
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
   False-monkeypatch test (C9). SUITE_FLOOR default is 745 since the
   r-loop-14 H set (was 718 after the G set, 692 after r12, 670 after
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
   nothing-pending.
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
   true again by construction.
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
   the right built-in once anchored. Defects are findings.
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
65. r-loop 14 H5 (r14 #5): ingest.scan has a THIRD vanished-folder arm
   under the same healthy-listing guard as the two siblings
   (games_present + path not in listed_dirs): DISCOVERED rows only ->
   QUARANTINED, detail "folder gone from Drive I — dropped from
   intake", NO INT_PATH reason (off the chase list), one loud
   [vanished-discovered] line per row. The DISCOVERED->QUARANTINED
   event is a GENUINE transition (the digest quarantine counter
   rightly counts it). A clean-path reappearance re-registers via the
   existing quarantined-path heal. The alternative (a failure counter
   in _download_one) was deliberately NOT adopted — the scan-side arm
   keeps the driver stateless. Defects are findings.
66. r-loop 14 H6 (r14 #7): validate._joint_edge_short, called from
   map_reasons after _map_windows + _map_flags, composes the joint
   head+tail remainder from EXACTLY the cut points plan_fixes will
   derive (CNT_EDGE_NONGAMEPLAY cut_at_s, notif/chat t±1.0, same
   blocking+fixable filter); under MIN_CLIP_S it appends ONE map-time
   CNT_SHORT (blocking, unfixable, post_cut_s = joint remainder). It
   SKIPS when an individual arm already emitted CNT_SHORT (duplicate-
   free reason list). Entry 26's _map_windows geometry untouched.
   Defects are findings.
67. r-loop 14 H7 (r14 #8): safe_session_id additionally rejects ids
   containing control characters (any ord(c) < 32) — they take the
   bundle-folder-name fallback at the shared decision point covering
   all five join sites. Defects are findings.
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
`

const LANES = [
  { key: 'regressions-r14', prompt:
    `Hunt for bugs INTRODUCED by the ITERATION-14 H fix set — commits 1dd69fa (H1: counted_at captured pre-build in send_daily_report_if_due; reports._stamp arm-2 rationale comment corrected), 1d54775 (H2: retranslate_from_sidecars gains ledger_game, always passed by _dispatch; non-override slug + keybind fallback anchored on it), a13e2ac (H3: rebuild-reset teardown discards split manifests + rowless -pN dirs + -analysis via the shared pipeline.run._discard_split_artifacts), 924755b (H4: ingest.run_rclone strips the per-line wall-clock prefix from rclone stderr; CompletedProcess rebuilt), c731e32 (H5: third vanished-folder arm in ingest.scan — DISCOVERED rows -> QUARANTINED under the healthy-listing guard, no INT_PATH; two sibling tests' partial listings corrected in-commit), 25e900e (H6: validate._joint_edge_short — joint head+tail map-time CNT_SHORT composed from plan_fixes' exact cut geometry), 492a076 (H7: safe_session_id rejects control characters), e01edc7 (H8: analyze_sample build_verdict judges dur_true = probed-or-claim), 747422e (H9: depth-2 paid-piece pin, de-vacuoused G4 site-4 tests, fix_sync_from_v1 traversal twin + its stale resolve_actions tuple-unpack crash fixed) and the floor bump 37d7d88 — all written by the same executor whose work you are checking; this loop's worst findings have always been regressions from the previous iteration's fixes. Read each commit with git show IN FULL, then attack: (1dd69fa) the pre-build anchor against BOTH stamp arms and the resume replay — a marker in the anchor's own second, an adjudication BEFORE the anchor whose backfilled md5 the row read then sees, the CAS arm interplay, pace/_build_daily_stats reading a clock the anchor no longer follows; (1d54775) ledger_game=None callers (direct/test/tool paths), a ledger slug that is wrong while metadata is right (reroute NOT planned), the KEYBIND_PATCHES/context/sessionjson consumers under the re-anchored slug, custom keybind.json still winning, the reroute override untouched; (a13e2ac) the shared discard called with child rows already deleted (rowed-children guard vacuous by construction — is any LIVE state reachable in that loop?), -analysis wipes racing nothing, the import placement inside _locked_main; (924755b) the regex against real rclone stderr shapes (multi-line, NOTICE/ERROR variants, lines legitimately starting with a date the operator wrote), stdout untouched, the timeout branch, ingest/deliver/scan embedders and their [:300] truncation points; (c731e32) the guard controls (empty listing, other-game tree, still-listed path), rows in DOWNLOADING/other states left alone, the heal on reappearance, the two corrected sibling tests (did the correction weaken what they pinned?), digest counters counting the new genuine quarantines; (25e900e) the composition filter (blocking+fixable) vs plan_fixes' actual accumulation, CNT_CHAT_PII rows with no edge param, the skip-on-existing-CNT_SHORT arm hiding a DIFFERENT joint geometry, rounding at the .1 boundary, bin-3 immediacy; (492a076) the ord<32 test vs legitimate ids, the five join sites, fallback COLLISIONS; (e01edc7) dur_true=0 when both probe and claim are falsy, the report/table still displaying the claim, vlm sweep_dur interplay; (747422e) the depth-2 cohort's stamps vs the sheet asserts, the stubbed lag machinery making the twin's pin unreachable, the resolve_actions unpack against dead_literals semantics. Report at most 5 findings — the ones you can PROVE.` },
  { key: 'payment-split', prompt:
    `Attack the two-mark payment logic in pipeline/reports.py (build_sheet_rows, _tree_has_uncounted_accepted, _mem_reconcile_failures, _stamp, mark_uploads_reported, mark_accepted_reported, pending_daily_send, write_payment_sheet) plus its callers in pipeline/run.py (fresh + resume daily paths, the wedge machinery) and tools/recal_regen_sheets.py, tools/recal_refix_reset.py, tools/recal_rebuild_reset.py, pipeline/ledger.py (supersede, update_where_md5, paid_pieces) and pipeline/ingest.py (quarantine heal). Prove or disprove, by RUNNING code against a scratch sqlite ledger in /tmp: (1) uploaded hours are still counted exactly once across all sheets; (2) every DELIVERED node's hours reach exactly one sheet, and none is lost — including under duration_raw_s=NULL roots (the new third re-entry arm) and re-cut trees (the new reconcile-against-DELIVERED void); (3) reject labels appear exactly once; (4) no root can re-enter forever silently; (5) a kill between the sheet send and either stamp cannot double-pay, and a supersede/heal INSIDE the stamp window cannot strand the corrected re-upload (the new md5 compare-and-set); (6) the refix per-piece payment memory, the sealed-root refusal, the supersede/heal clears, the zip-heal deferred clear, the wedge machinery (F1 guard + F5 durable alert) and the regen interlock (F10) behave as documented; (7) the '## Reject detail' section and the CSV columns describe the same population. Include the r-loop-13 changes on your surface: G1's durable '' adjudication (supersede breadcrumb, ZIP_ADJ_CHANGED marker, _stamp's counted_at arm), G5's rebuild-reset refusal/preserve/record, and G6's pending_daily_send_detail kinds — now joined by the r-loop-14 changes: H1's PRE-BUILD counted_at capture (one string into the record and both stamp calls; the resume replays it) and H3's split-artifact discard in the rebuild-reset teardown. Read the RULED design in items 7, 21, 22, 34-35, 39-40, 45 and 55-60 of the accepted list first — the DESIGN is settled, DEFECTS IN IT are exactly what you are hunting.` },
  { key: 'driver-core', prompt:
    `Review pipeline/continuous.py end to end: lanes, ownership/claims, cooldowns, the media cap, autoscale, drain/shutdown, digest, housekeeping cadences. Look for races, lost work, states that can never be left, and counters that can mislead an operator at the flip. Include the r-loop-11/12 changes on your surface: the validate-time duration backfill in _validate_one, the stint-scoped + reclaim-re-armed disc_media anchor in _stuck_lines, and the RECLAIM_MARKER event's interplay with every other event-anchored query — now joined by G1's ZIP_ADJ_CHANGED same-state DOWNLOADING marker (r-loop 13): verify it cannot perturb the stint anchors, the worker-death count, disc_media, the digest queries, or the reclaim filters — and by the r-loop-14 changes: H4's rclone-stderr normalization at ingest.run_rclone (every alert embedder inherits; verify the dedup cadence and that no consumer depended on the timestamped text) and H5's DISCOVERED vanished-folder arm (its genuine DISCOVERED->QUARANTINED transitions now flow into the digest's quarantine counters and drain the undownloaded backlog — verify the guard cannot prune live rows).` },
  { key: 'fix-validate', prompt:
    `Review pipeline/fix.py, pipeline/validate.py and pipeline/gate.py end to end: plan ordering, the fix budget (FIX_RETRIES=2), reason mapping and bins, what is fixable vs unfixable, and every path that can reject a session that should have been delivered or deliver one that should not. Pay particular attention to the QA-string mapping table: a needle that matches the wrong FAIL, a FAIL with no needle, or a mapping whose planned fix cannot clear the FAIL it was mapped from. Include the r-loop-11/12 changes on your surface: fix_key_hygiene's AND fix_actions_context's session-keybind resolution (F4 + r12 #5/#8), fix_lagshift_csv's host re-raise (F3), the probed_duration_s threading through _metrics (F6/r12 #14), the cut-less structural-first plan order (r12 #6), and the notif/chat map-time CNT_SHORT arms (r12 #7) — now joined by the r-loop-13 changes: G2's rerouted-only game_override in _dispatch (the plan carries the fact) and G3's dur_true edge-vs-mid classification in _map_flags — and by the r-loop-14 changes: H2's ledger_game anchoring of the non-reroute retranslate branch (slug + resolve_keybind fallback; session keybind.json still wins) and H6's _joint_edge_short map-time composition (joint head+tail CNT_SHORT from plan_fixes' exact cut geometry, skipped when an individual CNT_SHORT exists).` },
  { key: 'translator', prompt:
    `Review the translator/ package and tools/analyze_sample.py, tools/retrim_v2_session.py: binning, PTS handling (absolute container clock vs frame-relative clock — this class of bug was found in trim.py, look for MORE of it), keybind resolution, qa-v2 checks, and every read of an untrusted player-supplied file (the r-loop-11 session_id traversal fix F9 closed ONE such hole — hunt for siblings: other metadata fields reaching paths, filenames, subprocess argv, or file writes unsanitized). Look for crashes that become QUARANTINED instead of a typed reject, and for silent data corruption in delivered frames.csv. Include the r-loop-13 changes on your surface: G4's OverflowError degrade arms (v2.py's numeric tuple, ts-stats block, duration compare, _verify_against_raw; analyze_sample's _num) and G7's safe_session_id + containment in tools/fix_actions_from_v2.py and tools/fix_sync_from_v1.py — now joined by the r-loop-14 changes: H7's control-character rejection in safe_session_id (all five join sites) and H8's dur_true (probed-or-claim) verdicts in analyze_sample's build_verdict.` },
  { key: 'ops-tools', prompt:
    `Review tools/vm_setup.sh, tools/run_suite.sh, tools/recal_regen_sheets.py, tools/recal_verify_tree.py, tools/recal_rebuild_reset.py, tools/recal_refix_reset.py, pipeline/systemd/* and FLIP_RUNBOOK.md. Look for anything that can leave production unarmed, delete live data, block the payment endgame, or give an operator a false diagnosis. Include the r-loop-11/12 changes on your surface: the extended, fail-CLOSED pending interlocks (F10 + r12 #11/#13, now consulted by the daily send too), rebuild-reset's held run lock (r12 #3/#12), and the wedge machinery both reset tools sit behind — now joined by the r-loop-13 changes: G5's payment-evidence refusal + --allow-reported preserve/record path and G6's kind-specific PENDING_SEND_GUIDANCE diagnosis in both tools — and by the r-loop-14 changes: H3's split-artifact discard in the rebuild-reset teardown (shared _discard_split_artifacts + -analysis wipe) and the H9c-adjacent fix_sync_from_v1 repair (resolve_actions tuple unpack — the tool crashed on every real run before it).` },
  { key: 'tests-coverage', prompt:
    `Attack the TEST SUITE itself. The suite is the flip's ARMING GATE (tools/run_suite.sh, floor 745, currently 749 passing). Find fixes in ${LOOP_START}..HEAD whose behaviour survives deletion with the suite still green — apply the mutation in a scratch copy OUTSIDE the repo and run pytest to PROVE it, then restore. Also look for tests that assert a bug, tests that pass for the wrong reason (re-running SQL instead of the code, asserting source text, a fixture that makes the assertion vacuous), and orphaned threads or leaked state between tests. A fix with no real test is a real finding.` },
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
    `You are reviewing a production data pipeline at ${REPO}. Branch main, HEAD carries the r-loop-14 H fix set (1dd69fa..37d7d88: H1-H9 + floor 745, on top of the r-loop-13 G set abf052b..a5fc1a0 and the r-loop-12 set 986368f..0ad8747). This is review iteration 15 — the CONFIRMATION pass: if any finding is confirmed, the executor STOPS and hands Adnaan the list (nothing is fixed in-iteration), so a false confirm blocks the flip and a false quiet ships a defect — verify accordingly. Scope (plan §4, standing): review your lane's surface END TO END — whole-codebase, not just a delta pass — AND, wherever your lane touches code changed by the r-loop-14 H commits, treat those commits as the prime suspect: they were written by the executor whose work this pass audits, and this loop's worst findings have always been regressions from the previous iteration's fixes.

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

log(`r-loop 15: ${all.length} raised, ${confirmed.length} confirmed (${blockers.length} blockers), ${killed.length} refuted`)

return { confirmed, killed, counts: { raised: all.length, confirmed: confirmed.length, blockers: blockers.length } }
