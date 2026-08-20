# Kickoff — SATELLITE_CAMERA context: implement → ADVERSARIAL REVIEW → deploy + OW re-map (new session; RULED Adnaan 2026-08-20)

**PRECONDITION: the flip has happened** (the continuous pipeline is
LIVE on the VM, processing Drive I under the clean-slate ruling). If
it has not, stop — this session runs AFTER the flip
(`FLIP_EXEC_KICKOFF_PROMPT.md`); Adnaan ruled the flip must not wait
on this work.

You are implementing the RULED `satellite_camera` feature for Outer
Wilds, then subjecting your own implementation to an adversarial
review pass (RULED — Adnaan wants bugs this implementation may cause
hunted before it deploys), then deploying and re-mapping the
already-delivered OW sessions. Background:
`R8_IMPLEMENTATION_PLAN.md` (§0 ruling chain, §1 capsule incl. VM
recipe, §2 discipline — ALL of it binds here), memory
`ow-satellite-camera-context-gap`, and translator/context.py's own
docstring (the template mechanism you are extending).

## The ruled spec (schema APPROVED by Adnaan 2026-08-20)

**Problem:** the OW Observatory's satellite-photo screen changes what
the controls mean; the translator doesn't know that screen, so it
labels those frames with the normal meanings (RMB/R/mouse-look
mislabels). Invisible to qa-v2 and the VLM sweep — the keys really
were pressed; only the MEANING is wrong.

**Mechanism (the existing recipe in translator/context.py):**
1. Crop a distinctive FIXED UI element of the satellite screen from a
   real session frame (the sample that surfaced the gap; 640x360
   space) → `translator/templates/outer_wilds/`.
2. Register it in the position/threshold tables, add
   `satellite_camera` to the context set with a deliberate priority
   slot (justify the slot against the existing ordering in a
   comment, the file's own style).
3. Map the keys for that context with the APPROVED naming schema,
   pinned to the FRAME-VERIFIED terminal prompts (memory
   `ow-satellite-camera-context-gap`, verified 08-15: `Leave [Q]` ·
   `Forward Snapshot [RMB]` · `Rearview Snapshot [R]`, camera
   frozen): RMB → `satellite_forward_snapshot`, R →
   `satellite_rearview_snapshot`, Q → `satellite_exit`; mouse-look →
   action cells BLANK (the camera is frozen — today's
   `movement_look_*` labels there are the defect). Re-verify these
   prompts against the sample frames before landing (the memory is
   point-in-time); add the `CONTEXT_ALLOWED` entry the gap analysis
   named.
4. Frame-verify against the known sample segments (the file's
   established validation style: every gated press and segment
   visually reviewed).

**Client-vocabulary check FIRST (one question, before writing
code):** confirm against Odyssey's spec/QA scripts (in-repo sources)
whether the action vocabulary is CLOSED. If closed → the RULED
fallback: keep the keys, leave the action cells blank in
satellite_camera segments (no new tokens ship). If open → the named
actions above ship. If the sources are ambiguous, ask Adnaan — one
question.

**Tests (§2 discipline, fail-first in a scratch copy outside the
repo):** context classification pins on real frames (positive at the
terminal, negative on lookalike screens), the per-context action
mapping both ways (a satellite-context frame never emits the on-foot
meaning and vice versa), the priority-order pin against the
neighbouring contexts, and the no-opencv degrade path
(`available()` False must behave exactly as today). Both host gates
(floor as pinned in tools/run_suite.sh — raise it after landing,
passed − 4).

## The adversarial review pass (RULED — runs BEFORE deploy)

The established machinery: copy the newest committed snapshot in
`tools/review/` (`flip-review-iter21.js` unless a newer one exists)
to your scratchpad as `flip-review-satellite.js`; retarget the
regressions lane at YOUR satellite commits (one-line description +
attack notes per commit — the context misfiring on lookalike frames,
the priority slot perturbing the seven existing contexts, the new
action names leaking into non-OW games or the checker's key/action
invariants, the re-map tool's idempotence); keep ALL 7 lanes, the
2-vote refute, and the accepted-behaviours list (append an entry for
the satellite schema RULING so finders don't re-litigate the naming);
refresh suite numbers; invoke via the Workflow tool with scriptPath.
Findings doc + snapshot committed (the R*_FINDINGS.md pattern).
**Confirmed findings are fixed with full §2 discipline before
anything deploys.** Not-quiet does not need a fresh ask — Adnaan
pre-ordered this review; report the results either way.

## Deploy + re-map (only after the review's confirmed findings are fixed)

1. Deploy the updated tree to the VM the same way the flip did
   (stop the unit → sync → both host gates → restart) — production
   is LIVE now; no untested tree ever reaches it.
2. Run the action-column re-map over the RECORDED pre-mapping OW
   deliveries (the flip session's durable sid list in the pipeline
   home): regenerate the affected sessions' action columns, re-run
   qa on each, re-deliver exactly those files to Drive II. Idempotent
   and bounded — touch nothing outside the recorded list.
3. Report verdict-first: what the review found and killed, per-key
   mapping as frame-verified, how many OW sessions were re-mapped and
   re-delivered, and the open/closed vocabulary determination with
   its source.

**Ground rules (bind, plan §1):** verify before claiming; NEVER push;
Drive I read-only forever; secrets never printed; destructive gates
intact; gcloud auth expires — ask Adnaan for `! gcloud auth login`.
