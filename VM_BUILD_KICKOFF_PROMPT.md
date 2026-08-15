# Kickoff — build plan v3 (VM + overlap + VLM ladder), review loop, live verification

You are working in `/Users/adnaan/Documents/hl-gamedata`. The mission: take the Phase-1 gaming
pipeline live on a GCP VM by tonight (2026-08-15 evening IST, slack to Aug 16 morning), at
~172–240 footage-h/day, against the 1000-delivered-hours-by-Aug-24 deadline.

**The plan of record is `PIPELINE_IMPLEMENTATION_PLAN.md` (v3, adversarially reviewed across
5 rounds, APPROVED).** Read it in FULL before anything else — especially §4 (rulings R1–R23),
§6 (architecture + the overlap-driver contract + the exact delta list), §7 (VM Day-0 runbook),
§10a (VLM ladder architecture), §13, §18 (build steps 7–13 with acceptance). Where any other
document disagrees with the plan, the plan wins (§3.6). Specs-of-record it delegates to:
`THROUGHPUT_FIX_PLAN.md` (driver detail) and `VERTEX_FAILOVER_PLAN.md` (failover detail) —
both carry supersede notes; honor them.

## Inherited verified facts (re-verify pins before relying on them; code pinned at f49bdd6)

- Build steps 0–6 are DONE and tested (08-14). **REUSE IS A HARD RULE**: the only files you may
  change are the §6 delta list — `run.py`, `__main__.py`, `vlm.py`, `config.py`, `validate.py`
  (client swap + metrics passthrough), `reports.py` (one optional line), `ingest.py`
  (operator-name parsing, `ingest.py:125-126`), `ledger.py` (pragma + `start_batch(sessions=…)`)
  — plus NEW `tools/provision_vm.sh`, `tools/vm_setup.sh`, `pipeline/systemd/*`. Rewriting
  anything else is a plan violation.
- Two booby traps are already documented — do not rediscover them the hard way:
  (1) the validation pool must use `mp_context=spawn` AND `pipeline/__main__.py` needs the
  `if __name__ == "__main__":` guard first (it is an unguarded `raise SystemExit(main())`;
  under spawn every worker would re-import it and die — this bug is live on macOS today);
  (2) `translator/rrd.py:26` imports rerun at module top, so EVERY run/test command needs
  `--with numpy --with opencv-python-headless --with rerun-sdk`.
- Secrets: `~/.config/hl-gamedata/secrets.env` has `GEMINI_API_KEY` (new, `AQ.`-format —
  likely Vertex-express-issued; which endpoint works is UNKNOWN until the §7.6 smoke matrix)
  and `GEMINI_API_KEY_PREV` (old key, the R23 rung-3). **Never print, log, or commit either
  key; the Vertex URL embeds `?key=` — error strings must carry endpoint tags, never URLs.**
- gcloud SDK is installed, authed (`adnaan@humynlabs.ai`), project `hl-gamedata-pipeline` set,
  billing on. The asia-south1 CPU-quota check (§7.1) has NOT been run — it is your first
  action; if <16 CPUs, file the bump and ask Adnaan (region vs 2× e2-standard-8).
- Drive I had zero files at 08-14 22:41; operator folders are free-text NAMES by ruling
  (Q5 amended 08-15) — player folders stay strict emails.

## Ground rules

- Machine-wide CLAUDE.md applies in full: verify before claiming, read whole sources, mark
  `[assumption]`/`[web]`, never hand-transcribe numbers, one question at a time and only when
  truly blocked.
- **Drive I is read-only forever (R6).** Until the §18.13 gate is green, uploads go ONLY to
  Drive II `_pipeline_test/` (purge after via `deliver.cleanup_test_folder`). Never load the
  old launchd plist. Never touch the Obsidian vault. Never push to any remote.
- Commits: path-scoped only, per green step / green review iteration (Adnaan's ruling Q17).
  Plan-doc revisions also commit (Q18). Spend cap without asking: ~$200 total GCP.
- Adnaan has already ruled everything in §4 and §19 — do not re-ask settled rulings. Pause for
  him ONLY on: quota <16, Vertex 403 "API not enabled" (he clicks the console), a QUARANTINED
  ledger/security surprise, or spend beyond the cap.

## Phase 1 — implement (plan §18 steps 7–13, in order; 7∥8 may interleave)

Follow each step's acceptance column literally. Highlights the plan already decided:
- Step 7: Vertex failover (dark behind `VLM_FAILOVER_ENABLED=False`) + the R23 four-rung ladder
  (`_rung`, `LadderGemini`, run-level stickiness via parent inject→report→max) with the §18.7
  test list, including the two-pool-generation stickiness test and both flag states.
- Step 8: overlap driver per the §6 contract (spawn context, `__main__` guard, start-written
  `summary_json`, in-loop daily-report/backup, cross-run gate-failure hand-back, U-owned batch
  completion) + operator-name ingest amendment + the `python -m pipeline run` smoke.
- Steps 9–10: provision VM + bucket (`--no-service-account --no-scopes`), bootstrap, rsync
  (+rrd stubs for the six benchmark dirs), secrets over, three rclone remotes, full suite green
  ON THE VM.
- Step 11: Day-0 measurements + the §7.6 smoke MATRIX ({new,prev} key × {genlang,vertex} +
  per-ladder-id probes on working endpoints) — its results SET `VLM_FAILOVER_ENABLED` and
  `VLM_MODEL_LADDER` (config commit) before any gate. Record all numbers into plan §15.
- Steps 12–13: systemd units, then the go-live gate — gate (a) real uploads if any exist, else
  gate (b) synthetic (six sessions, TEST pipeline home `HL_PIPELINE_HOME=~/hl-pipeline-test`,
  test-mode Telegram, `_pipeline_test/` delivery, purged) + the 2-leg kill matrix (download leg
  deferred to first watched real uploads).

Full suite after every step:
`PYTHONPATH=. uv run --with pytest --with numpy --with opencv-python-headless --with rerun-sdk pytest pipeline/tests translator/tests -q`

## Phase 2 — adversarial code-review loop (max 5 iterations; Adnaan's ruling Q17)

After Phase 1 is complete and green, iterate:

1. Launch adversarial code-review agent(s) with instructions to deeply and thoroughly hunt
   real defects — correctness, concurrency (threads+spawn+SQLite), resume/idempotency
   (double-DELIVERED, double-counted hours), security (key leakage into logs/ledger/Telegram),
   ruling violations (R1–R23, F1–F13), test gaps, and Linux-vs-macOS behavior.
   **Amended by Adnaan mid-loop (2026-08-15, after iteration 1): EVERY remaining iteration
   (2–5) runs the full composition — (a) FULL-codebase review (`pipeline/`, changed `tools/`,
   systemd units, interfaces into `translator/`/the engine), (b) a dedicated DELTA review of
   all files changed since the loop started, and (c) adversarial hunting for bugs/issues
   introduced by the loop's own changes/fixes — and all four remaining iterations run (the
   original rounds-3–5-delta-only clause is superseded).** Findings must carry evidence
   (file:line, repro, or recomputation) and severity (BLOCKER/MAJOR/MINOR).
2. **Verify every finding yourself against the code before acting** — reviewers can be wrong;
   fix only verified findings, note rejected ones with the disproof.
3. Implement fixes → full suite green → path-scoped commit (`review iteration N: …`).
4. Next iteration reviews fresh (a new agent or the same one instructed to verify prior
   findings resolved + hunt new ones).

Exit the loop early when a round yields zero verified BLOCKER/MAJOR findings. **If verified
findings remain after iteration 5, STOP and present them to Adnaan severity-ordered with your
recommendation — do not silently continue.**

## Phase 3 — independent live e2e verifier (after the loop exits)

Launch a FRESH, independent verifier agent — not any agent that wrote or reviewed the code —
with authority to run everything for real ("fully live", Adnaan's ruling):

- Full test suite on the Mac AND on the VM; the `python -m pipeline run` spawn smoke on both.
- The §7.6 smoke matrix LIVE (both keys × both endpoints × ladder-id probes — a few cents).
- The synthetic gate-(b) run end-to-end ON THE VM: seed → validate → fix → deliver to Drive II
  `_pipeline_test/` → checksum verify → purge; then the 2-leg kill matrix with resume
  assertions (every session terminal, hours counted once, no stub rrd staged, no duplicate
  batch messages).
- A secrets sweep: assert neither key appears in any log, ledger row, dossier, or Telegram
  payload produced during the run.
- Verdict per §18 acceptance row, pass/fail, with evidence. Its verdict is REPORTED AS-IS —
  you do not grade your own work.

## Report back (one message at the end)

Verdict-first per §18 step; the measured Day-0 numbers (also written into plan §15 + committed);
the smoke-matrix table; a review-loop ledger (per iteration: findings raised / verified /
fixed / rejected-with-disproof); the verifier's verdict verbatim; go-live status; then open
items for Adnaan, priority-ordered (three known carry-forwards to include: Gemini billing tier
still unverified; rotate ALL credentials after Phase 1; the §17.6 operator-label hygiene note).
If anything is blocked, say by what and continue with the rest.
