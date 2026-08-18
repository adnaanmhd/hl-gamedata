// r-loop-10 review workflow (edited per R8_IMPLEMENTATION_PLAN.md §9 "After
// D8" from the committed iter9 snapshot): regressions lane retargeted at the
// r-loop-9 fix commits 640651a..81d5f06 (D1-D8, + D0 ruling c9037b7 + floor
// 55cc759), accepted-behaviours additions 21-28 appended (15/17/22 minimally
// amended where ruling C and the recorded D5b md5 deviation superseded the
// drafted text). Suite refreshed (623/floor 619). LOOP_START, 2-vote refute
// discipline and the tests-coverage lane kept.
export const meta = {
  name: 'flip-review-iter10',
  description: 'r-loop 10: adversarial review of the continuous-pipeline flip work, 2-vote refute',
  phases: [
    { title: 'Find', detail: 'lane finders: whole-codebase + delta + regressions from r-loop-9 fixes' },
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
   False-monkeypatch test (C9). SUITE_FLOOR default is 619 since D8
   (was 578).
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
`

const LANES = [
  { key: 'regressions-r9', prompt:
    `Hunt for bugs INTRODUCED by r-loop 9's own fixes (the eight commits 640651a..81d5f06 on HEAD, plus the D0 ruling record c9037b7 and the floor bump 55cc759). THIS IS THE HIGHEST-VALUE LANE: in this project every iteration's worst findings have been regressions from the PREVIOUS iteration's fixes. Read \`git log --oneline 640651a~1..81d5f06\` and \`git diff 640651a~1..81d5f06\` IN FULL, then attack each change. The r-loop-9 changes are: (a) D1 translator — rebase_events carried_out kwarg + retranslate's carried-only refusal ("zero events beyond N held-key carries"); _px OverflowError arm; BundleError on out-of-range started_at; non-str session_id/exe_name guards (v2 + fix.py mirror + a non-dict game_info normalization); keybind _binding_groups now gates the whole-binding-unusable rule on key PRESENCE not truthiness; (b) D2 — aux["probed_duration_s"] threaded into map_reasons for CNT_SHORT/soft-max (claim is fallback); analyze()'s inventory-read failure keeps qa FAILs (no a.error) and skips inventory/VLM; error_kind="host" -> validate re-raises OSError; attack: stale rep["duration_s"] consumers, the skipped-sections invariants (verdict built via build_verdict with empty inventory), the host re-raise interaction with _validate_worker; (c) D3 — BrokenProcessPool first-death host-suspect via an events-count marker (attack: marker detail matching, restarts between deaths, the 2s stop-wait, double-counting with the C3 carve-out) + CalledProcessError in both U-lane host tuples; (d) D4 — gate-entry clock rebasing (_retrim_cut, _rebase_gate_entry, ordered fixlog walk): attack offset arithmetic across MULTIPLE retrims and attempts, per_window "requested" single-pair shifting, the clamp-at-0 behaviour, adoption-path propagation via _adopted_segments detail parsing, the now-raising per-child _append_fixlog inside apply_fixes (partial child writes then host-abort; duplicate entries on re-cut), and _entries_for_segment on already-rebased child entries at level 2; (e) D5 — day-agnostic pending scan (ordering, non-day dirs, unreadable records with .sent), the md5-map record + _bytes_changed skip (children with md5_video="" always match — is that safe?), doc_sent semantics (_send_sheet_document bool, _mark_doc_sent atomicity, marker-present doc-only path), the missing-row refusal (can it wedge a day forever?), interactions with the folder-issues report and with W-window anchor writes on resume; (f) D6 — the md5-conditional heal clears (clears dict built from vmd5 != existing md5: attack vmd5="" cases, INCOMPLETE folders, and whether preserved duration_raw_s can double-count anywhere); (g) D7 ruling C — paid_pieces (INSERT OR IGNORE; never cleared): attack the seconds-tolerance match (1.0s — deliveries that legitimately re-encode?), collisions with seconds NULL, the memory-aware _tree_has_uncounted_accepted (matched pieces stop re-entry, collisions re-enter forever — can a tree with BOTH a matched piece and nothing else oscillate?), skipped_sealed refusal, the pending_daily_send interlock (both tools; TOCTOU vs a driver mid-send), lsf rc 3/4 vs other, and the teardown event-detail JSON; (h) D8 — parsed_ok re-emit (naive+parsed vs aware+unparseable), the conv_other row. Attack (d), (e) and (g) hardest.` },
  { key: 'payment-split', prompt:
    `Attack the NEW two-mark payment logic in pipeline/reports.py (build_sheet_rows, mark_accepted_reported, write_payment_sheet) plus its callers in pipeline/run.py and tools/recal_regen_sheets.py, tools/recal_refix_reset.py, tools/recal_rebuild_reset.py, pipeline/ledger.py (supersede) and pipeline/ingest.py (quarantine heal). Prove or disprove, by RUNNING code against a scratch sqlite ledger in /tmp: (1) uploaded hours are still counted exactly once across all sheets; (2) every DELIVERED node's hours reach exactly one sheet, and none is lost; (3) reject labels appear exactly once; (4) no root can re-enter forever; (5) a kill between the sheet send and either stamp cannot double-pay; (6) the refix per-piece payment memory (ruling C: paid_pieces recording, sheet-side match/collision handling, sealed-root refusal) and the supersede/heal clears behave as documented; (7) the '## Reject detail' section and the CSV columns describe the same population. Read the RULED design in item 7 of the accepted list first — the DESIGN is settled, DEFECTS IN IT are exactly what you are hunting.` },
  { key: 'driver-core', prompt:
    `Review pipeline/continuous.py end to end: lanes, ownership/claims, cooldowns, the media cap, autoscale, drain/shutdown, digest, housekeeping cadences. Look for races, lost work, states that can never be left, and counters that can mislead an operator at the flip.` },
  { key: 'fix-validate', prompt:
    `Review pipeline/fix.py, pipeline/validate.py and pipeline/gate.py end to end: plan ordering, the fix budget (FIX_RETRIES=2), reason mapping and bins, what is fixable vs unfixable, and every path that can reject a session that should have been delivered or deliver one that should not. Pay particular attention to the QA-string mapping table: a needle that matches the wrong FAIL, a FAIL with no needle, or a mapping whose planned fix cannot clear the FAIL it was mapped from.` },
  { key: 'translator', prompt:
    `Review the translator/ package and tools/analyze_sample.py, tools/retrim_v2_session.py: binning, PTS handling (absolute container clock vs frame-relative clock — this class of bug was just found in trim.py, look for MORE of it), keybind resolution, qa-v2 checks, and every read of an untrusted player-supplied file. Look for crashes that become QUARANTINED instead of a typed reject, and for silent data corruption in delivered frames.csv.` },
  { key: 'ops-tools', prompt:
    `Review tools/vm_setup.sh, tools/run_suite.sh, tools/recal_regen_sheets.py, tools/recal_verify_tree.py, tools/recal_rebuild_reset.py, tools/recal_refix_reset.py, pipeline/systemd/* and FLIP_RUNBOOK.md. Look for anything that can leave production unarmed, delete live data, block the payment endgame, or give an operator a false diagnosis.` },
  { key: 'tests-coverage', prompt:
    `Attack the TEST SUITE itself. The suite is the flip's ARMING GATE (tools/run_suite.sh, floor 619, currently 623 passing). Find fixes in ${LOOP_START}..HEAD whose behaviour survives deletion with the suite still green — apply the mutation in a scratch copy OUTSIDE the repo and run pytest to PROVE it, then restore. Also look for tests that assert a bug, tests that pass for the wrong reason (re-running SQL instead of the code, asserting source text, a fixture that makes the assertion vacuous), and orphaned threads or leaked state between tests. A fix with no real test is a real finding.` },
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
    `You are reviewing a production data pipeline at ${REPO}. Branch main, HEAD 567c3e8 (close-out) carries the r-loop-9 fix set (eight code commits 640651a..81d5f06 = D1-D8, on top of the r-loop-8 set); those eight commits are the prime target.

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

log(`r-loop 10: ${all.length} raised, ${confirmed.length} confirmed (${blockers.length} blockers), ${killed.length} refuted`)

return { confirmed, killed, counts: { raised: all.length, confirmed: confirmed.length, blockers: blockers.length } }
