// SNAPSHOT (2026-08-18) of the r-loop-8 review workflow, preserved from the
// prior session's scratchpad so the review phase survives a reboot.
// Per R8_IMPLEMENTATION_PLAN.md §5: for iterations 9/10/11 COPY this to the
// session scratchpad and edit there — (1) point the regressions lane at the
// PREVIOUS iteration's fix commits (not 869910d), (2) append the §5
// accepted-behaviours additions, (3) refresh the suite size in the
// tests-coverage lane, (4) keep LOOP_START=2244758, the 2-vote refute
// discipline and the tests-coverage lane. Invoke via Workflow {scriptPath}.
export const meta = {
  name: 'flip-review-iter8',
  description: 'r-loop 8: adversarial review of the continuous-pipeline flip work, 2-vote refute',
  phases: [
    { title: 'Find', detail: 'lane finders: whole-codebase + delta + regressions from r-loop-7 fixes' },
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
`

const LANES = [
  { key: 'regressions-r7', prompt:
    `Hunt for bugs INTRODUCED by r-loop 7's own fixes (commit 869910d). THIS IS THE HIGHEST-VALUE LANE: in this project every iteration's worst findings have been regressions from the PREVIOUS iteration's fixes — r-loop 4 from r-loop 3, r-loop 6 from r-loop 5, and r-loop 7 found four regressions from r-loop 6. Read \`git show 869910d\` and \`git diff ${LOOP_START}..HEAD\` IN FULL, then attack each change. The r-loop-7 changes are: (a) tools/recal_regen_sheets.py read_counted_record/write_counted_record replacing three inline readers/writers; (b) fix.apply_fixes now returns a "kind" (host|session) and BOTH drivers refund the fix attempt and park the row on host — check the refund arithmetic, the FIX_QUEUED re-entry, whether a permanently-broken host now spins forever, and whether any caller reads out["kind"] when apply_fixes returned early; (c) tools/recal_refix_reset.py seals only where a DELIVERED node carries accepted_reported_at; (d) continuous._held_discovered() now serves the cap count, the cap carve-out AND _stuck_lines, and the carve-out no longer uses ingest.next_batch — check ordering, the intake_lock, and whether any row is now unreachable or double-claimed; (e) plan_fixes emits FIX_SESSIONJSON_REWRITE for STR_SJ_INVALID before any retranslate, and retranslate raises FixFailed on an unusable/implausible head offset; (f) _propagate_gate_record filters by segment overlap; (g) resolve_keybind falls back to the built-in when nothing binds — check a game with NO built-in, and the inverted-keybind detector's interaction; (h) translator raw_int() + isinstance guards in binner/keys/v2. Attack (b), (d) and (g) hardest.` },
  { key: 'payment-split', prompt:
    `Attack the NEW two-mark payment logic in pipeline/reports.py (build_sheet_rows, mark_accepted_reported, write_payment_sheet) plus its callers in pipeline/run.py and tools/recal_regen_sheets.py, tools/recal_refix_reset.py, tools/recal_rebuild_reset.py, pipeline/ledger.py (supersede) and pipeline/ingest.py (quarantine heal). Prove or disprove, by RUNNING code against a scratch sqlite ledger in /tmp: (1) uploaded hours are still counted exactly once across all sheets; (2) every DELIVERED node's hours reach exactly one sheet, and none is lost; (3) reject labels appear exactly once; (4) no root can re-enter forever; (5) a kill between the sheet send and either stamp cannot double-pay; (6) the refix "seal" and the supersede/heal clears behave as documented; (7) the '## Reject detail' section and the CSV columns describe the same population. Read the RULED design in item 7 of the accepted list first — the DESIGN is settled, DEFECTS IN IT are exactly what you are hunting.` },
  { key: 'driver-core', prompt:
    `Review pipeline/continuous.py end to end: lanes, ownership/claims, cooldowns, the media cap, autoscale, drain/shutdown, digest, housekeeping cadences. Look for races, lost work, states that can never be left, and counters that can mislead an operator at the flip.` },
  { key: 'fix-validate', prompt:
    `Review pipeline/fix.py, pipeline/validate.py and pipeline/gate.py end to end: plan ordering, the fix budget (FIX_RETRIES=2), reason mapping and bins, what is fixable vs unfixable, and every path that can reject a session that should have been delivered or deliver one that should not. Pay particular attention to the QA-string mapping table: a needle that matches the wrong FAIL, a FAIL with no needle, or a mapping whose planned fix cannot clear the FAIL it was mapped from.` },
  { key: 'translator', prompt:
    `Review the translator/ package and tools/analyze_sample.py, tools/retrim_v2_session.py: binning, PTS handling (absolute container clock vs frame-relative clock — this class of bug was just found in trim.py, look for MORE of it), keybind resolution, qa-v2 checks, and every read of an untrusted player-supplied file. Look for crashes that become QUARANTINED instead of a typed reject, and for silent data corruption in delivered frames.csv.` },
  { key: 'ops-tools', prompt:
    `Review tools/vm_setup.sh, tools/run_suite.sh, tools/recal_regen_sheets.py, tools/recal_verify_tree.py, tools/recal_rebuild_reset.py, tools/recal_refix_reset.py, pipeline/systemd/* and FLIP_RUNBOOK.md. Look for anything that can leave production unarmed, delete live data, block the payment endgame, or give an operator a false diagnosis. Note FLIP_RUNBOOK 6c still says the deploy set is "three things" while FLIP_HANDOVER says four — that specific doc inconsistency is already known and being fixed, do not report it.` },
  { key: 'tests-coverage', prompt:
    `Attack the TEST SUITE itself. The suite is the flip's ARMING GATE (tools/run_suite.sh, floor 450, currently 482 passing). Find fixes in ${LOOP_START}..HEAD whose behaviour survives deletion with the suite still green — apply the mutation in a scratch copy OUTSIDE the repo and run pytest to PROVE it, then restore. Also look for tests that assert a bug, tests that pass for the wrong reason (re-running SQL instead of the code, asserting source text, a fixture that makes the assertion vacuous), and orphaned threads or leaked state between tests. A fix with no real test is a real finding.` },
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
    `You are reviewing a production data pipeline at ${REPO}. Branch main, HEAD is the r-loop-7 fix set (three commits on top of ${LOOP_START}); the NEWEST commit 869910d is the r-loop-7 fixes and is the prime target.

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

log(`r-loop 8: ${all.length} raised, ${confirmed.length} confirmed (${blockers.length} blockers), ${killed.length} refuted`)

return { confirmed, killed, counts: { raised: all.length, confirmed: confirmed.length, blockers: blockers.length } }
