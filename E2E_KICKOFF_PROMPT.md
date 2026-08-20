# Kickoff — INDEPENDENT E2E VERIFICATION (new session; RULED Adnaan 2026-08-20)

You are the independent end-to-end verifier for the continuous
pipeline in `/Users/adnaan/Documents/hl-projects/hl-gamedata`. You
wrote and reviewed NONE of this code — that independence is the point
of this session. **Your job: exercise the ACTUAL system on canary
data and relay a verdict VERBATIM. You do NOT fix code, do NOT
deploy, and do NOT start the flip** (that is the NEXT session, on
Adnaan's explicit go after your verdict).

**START IMMEDIATELY.** No launch phrase. Ask only if something is
provably wrong or only Adnaan can settle it — one question at a time.

**Background (read, do not re-derive):** `R8_IMPLEMENTATION_PLAN.md`
(the work order of record — §1 capsule, §2 discipline, §5, §6),
`FLIP_RUNBOOK.md` §5 (the canary shape you model), and
`R21_FINDINGS.md` → `R20_FINDINGS.md` → `R19_FINDINGS.md` if you need
the recent defect history. State you inherit: code HEAD carries the
M/N/O fix sets; suite **850/850, floor 846, BOTH host gates green**
(verify with `git log --oneline` that only docs commits sit on top of
`a6f5205`; re-run the Mac gate yourself if anything looks off:
`bash tools/run_suite.sh --with numpy --with opencv-python-headless
--with rerun-sdk`). The O set (`765105a..b2a833c`) is UNREVIEWED by a
review pass — your e2e is its verification (RULED); if your run
surfaces a defect, that is a finding for the report, not something
you patch.

## The run (FLIP_RUNBOOK §5 canary shape, Mac-local)

1. **Fresh `HL_PIPELINE_HOME`** in your session scratchpad — never
   the real one. **TEST-mode Telegram** (config test_mode / the
   TEST-channel vars — verify before any send; no message may reach
   the real channel). Real VLM calls with BOUNDED spend (the canary
   bundle count below; stop and report if spend balloons).
2. **Seeds:** the local sample bundles in the repo (the
   `2026-08-1*` session folders) staged as canary uploads. Cover at
   minimum: one clean Kamla, one clean OW, one v1-format bundle, one
   with corrupt/junk metadata (the M/N/O surface), one zip payload.
3. **Real Drive II uploads to `_pipeline_test/` ONLY**, purged
   afterward via `deliver.cleanup_test_folder` (verify the purge ran;
   `rclone listremotes` must show drive-collect:/drive-deliver: —
   prereqs last verified on this Mac 2026-08-19). Drive I is
   READ-ONLY FOREVER — nothing writes there, ever.
4. **The 3-leg kill -9 matrix:** kill the driver mid-download,
   mid-validate/fix, and mid-deliver; restart each time; verify
   convergence (no lost session, no double state, no duplicate
   delivery, the ledger consistent).
5. **Payment sheets:** run the daily-send path in TEST mode against
   the canary ledger; verify counted-exactly-once across two
   consecutive sheets, a reject label surface that matches the ruled
   unfixable-only rule, and no stamp on a reset generation (the
   O1/N3/N4 surface).
6. Secrets (`~/.config/hl-gamedata/secrets.env`): source, NEVER
   print/log/commit.

## Verdict + report (the deliverable)

Report to Adnaan, verdict-first: **GREEN / GREEN-WITH-FINDINGS /
BLOCKED**, with every anomaly listed verbatim (raw error text, never
paraphrased into a pass; a BLOCKED-with-error NEVER becomes a pass).
Include: what ran and what did not, the kill-matrix outcomes, sheet
invariant results, VLM spend, and the §6 payment-surface list's
review-status labels (plan §6 — O1 is verified by YOUR run; say so
honestly either way). Do NOT start the flip; the flip session's brief
is `FLIP_EXEC_KICKOFF_PROMPT.md` and it waits for Adnaan's explicit
go on your verdict.

**Ground rules (bind, plan §1):** verify before claiming; read whole
sources; mark `[assumption]`; NEVER push; commits (if any — e.g. your
findings doc) path-scoped; nothing deploys; no systemd unit touched;
the pre-existing uncommitted junk in the tree predates r8 — leave it.
gcloud auth may expire — ask Adnaan to run `! gcloud auth login`.
