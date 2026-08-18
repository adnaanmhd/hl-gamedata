// r-loop-9 review workflow (edited per R8_IMPLEMENTATION_PLAN.md §5 from the
// committed iter8 snapshot): regressions lane retargeted at the r-loop-8 fix
// commits c3eab1b..b694456, accepted-behaviours additions 12-20 appended,
// suite size refreshed (582/floor 578). LOOP_START, 2-vote refute discipline
// and the tests-coverage lane kept.
export const meta = {
  name: 'flip-review-iter9',
  description: 'r-loop 9: adversarial review of the continuous-pipeline flip work, 2-vote refute',
  phases: [
    { title: 'Find', detail: 'lane finders: whole-codebase + delta + regressions from r-loop-8 fixes' },
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
   immediately, loudly); refix REFUSES mixed trees and reports both
   lists in skipped_mixed (C6).
16. Gate record (C7): per_window rides in the note; the aggregate
   destroyed is retained for the parent's own _gate_destroyed
   (overlapping windows may double-count key_frames across per_window
   entries — documented); a child inherits only its overlapping windows'
   share via a synthetic entry; legacy entries without per_window
   propagate whole.
17. The suite is CONT_DAILY_REPORTS-independent via a conftest autouse
   fixture forcing True; the suppression is pinned by an explicit
   False-monkeypatch test (C9). SUITE_FLOOR default is 578.
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
`

const LANES = [
  { key: 'regressions-r8', prompt:
    `Hunt for bugs INTRODUCED by r-loop 8's own fixes (the nine commits c3eab1b..b694456 on HEAD). THIS IS THE HIGHEST-VALUE LANE: in this project every iteration's worst findings have been regressions from the PREVIOUS iteration's fixes. Read \`git log --oneline c3eab1b~1..b694456\` and \`git diff c3eab1b~1..b694456\` IN FULL, then attack each change. The r-loop-8 changes are: (a) translator C1 — rebase_events str-only held-state carry, raw_int OverflowError arm, normalize_literal ""-for-non-str + _binding_groups empty-token rules, v2.BundleError on the raw-only metadata path, fix_translate_raw try/finally rmtree; (b) C2 — retranslate_from_sidecars' duration guard REPLACED by a zero-events check: attack sessions with empty sidecars, held-key carries that fabricate an event, and the bogus-stamp path; (c) C3 — the host carve-out splits on any(applied ok): REVALIDATING + refund + return False vs FIX_QUEUED; check refund arithmetic, the batch driver's pass-loop interaction, and whether a REVALIDATING row parked by a host error can wedge or lose its reasons; (d) C4 — _stuck_lines' new 4-state stint query, the _house_tick extraction (cadence state moved to __init__), CONT_DIGEST_RETRY_S gating, AlertBook stamp retraction under races; (e) C5 — the .daily-counted.json record: torn writes, resume idempotence, the record-vs-marker-vs-anchor orderings under kill at EVERY interstice, _build_daily_stats drift vs the sheet, and the folder-issues report's interaction with a resumed daily; (f) C6 — tree_sealed_at: every writer/clearer, the deleted late-arrival deferral (can an unsettled late tree double-count or lose hours now?), the refix mixed-tree refusal (plan-time paid/unpaid computation vs teardown-time state), and build_sheet_rows' sealed/accepted_due interplay; (g) C7 — gate.per_window inventory (pads, overlapping windows, double-counting), _entries_for_segment synthetic entries (are they readable by _gate_destroyed? do multi-attempt fixlogs double-propagate?), _gate_entry_touches now preferring note windows; (h) C8 — _conv_valid vs the checker (can they drift? does the rewrite now DESTROY a valid non-default convention?), the _TS_RE re-emit (timezone shifts, non-UTC offsets); (i) C9 — the conftest fixture ordering vs test-local monkeypatches and the C.CONT_DAILY_REPORTS reads captured at import time anywhere. Attack (e), (f) and (g) hardest.` },
  { key: 'payment-split', prompt:
    `Attack the NEW two-mark payment logic in pipeline/reports.py (build_sheet_rows, mark_accepted_reported, write_payment_sheet) plus its callers in pipeline/run.py and tools/recal_regen_sheets.py, tools/recal_refix_reset.py, tools/recal_rebuild_reset.py, pipeline/ledger.py (supersede) and pipeline/ingest.py (quarantine heal). Prove or disprove, by RUNNING code against a scratch sqlite ledger in /tmp: (1) uploaded hours are still counted exactly once across all sheets; (2) every DELIVERED node's hours reach exactly one sheet, and none is lost; (3) reject labels appear exactly once; (4) no root can re-enter forever; (5) a kill between the sheet send and either stamp cannot double-pay; (6) the refix "seal" and the supersede/heal clears behave as documented; (7) the '## Reject detail' section and the CSV columns describe the same population. Read the RULED design in item 7 of the accepted list first — the DESIGN is settled, DEFECTS IN IT are exactly what you are hunting.` },
  { key: 'driver-core', prompt:
    `Review pipeline/continuous.py end to end: lanes, ownership/claims, cooldowns, the media cap, autoscale, drain/shutdown, digest, housekeeping cadences. Look for races, lost work, states that can never be left, and counters that can mislead an operator at the flip.` },
  { key: 'fix-validate', prompt:
    `Review pipeline/fix.py, pipeline/validate.py and pipeline/gate.py end to end: plan ordering, the fix budget (FIX_RETRIES=2), reason mapping and bins, what is fixable vs unfixable, and every path that can reject a session that should have been delivered or deliver one that should not. Pay particular attention to the QA-string mapping table: a needle that matches the wrong FAIL, a FAIL with no needle, or a mapping whose planned fix cannot clear the FAIL it was mapped from.` },
  { key: 'translator', prompt:
    `Review the translator/ package and tools/analyze_sample.py, tools/retrim_v2_session.py: binning, PTS handling (absolute container clock vs frame-relative clock — this class of bug was just found in trim.py, look for MORE of it), keybind resolution, qa-v2 checks, and every read of an untrusted player-supplied file. Look for crashes that become QUARANTINED instead of a typed reject, and for silent data corruption in delivered frames.csv.` },
  { key: 'ops-tools', prompt:
    `Review tools/vm_setup.sh, tools/run_suite.sh, tools/recal_regen_sheets.py, tools/recal_verify_tree.py, tools/recal_rebuild_reset.py, tools/recal_refix_reset.py, pipeline/systemd/* and FLIP_RUNBOOK.md. Look for anything that can leave production unarmed, delete live data, block the payment endgame, or give an operator a false diagnosis.` },
  { key: 'tests-coverage', prompt:
    `Attack the TEST SUITE itself. The suite is the flip's ARMING GATE (tools/run_suite.sh, floor 578, currently 582 passing). Find fixes in ${LOOP_START}..HEAD whose behaviour survives deletion with the suite still green — apply the mutation in a scratch copy OUTSIDE the repo and run pytest to PROVE it, then restore. Also look for tests that assert a bug, tests that pass for the wrong reason (re-running SQL instead of the code, asserting source text, a fixture that makes the assertion vacuous), and orphaned threads or leaked state between tests. A fix with no real test is a real finding.` },
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
    `You are reviewing a production data pipeline at ${REPO}. Branch main, HEAD b694456 is the r-loop-8 fix set (nine commits c3eab1b..b694456 on top of the r-loop-7 set); those nine commits are the prime target.

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

log(`r-loop 9: ${all.length} raised, ${confirmed.length} confirmed (${blockers.length} blockers), ${killed.length} refuted`)

return { confirmed, killed, counts: { raised: all.length, confirmed: confirmed.length, blockers: blockers.length } }
