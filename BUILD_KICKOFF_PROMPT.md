# Build kickoff prompt (paste into a fresh session after /clear)

Build the Phase-1 gaming-data pipeline end-to-end, in one shot. The single source of truth is
`PIPELINE_IMPLEMENTATION_PLAN.md` at the repo root — every ruling, threshold, reason code, fix,
schema, template, and acceptance test is in it. Everything is already decided: do NOT re-ask
settled rulings, §16 fiat decisions are approved as written, §19 records every requirements answer.
Deadline context: 1000 delivered hours by Aug 24 — recordings are already flowing, so speed matters,
but correctness gates payment, so never trade it away.

## Order of work

**0 — Read first.** `PIPELINE_IMPLEMENTATION_PLAN.md` in full. Then skim what you'll reuse:
`translator/` (v2.py, cli.py, keys.py, trim.py, rrd.py, sync.py, context.py),
`tools/analyze_sample.py` (the Phase-II engine — its design is locked, wrap it, don't fork it),
`tools/retrim_v2_session.py`, `tools/fix_actions_from_v2.py`, `SAMPLE_ANALYSIS_PLAYBOOK.md`.
Secrets live in `~/.config/hl-gamedata/secrets.env` — never print or commit them. Already verified
working: rclone remotes `drive-collect:` / `drive-deliver:` (service account), Telegram bot + chat
id (a test DM was delivered), Gemini key (`gemini-3.7-flash`, generativelanguage v1beta).

**1 — Build** per plan §6 (components) and §18 (order + acceptance): the `pipeline/` package
(run, config, ledger, ingest, validate, scanner, vlm, fix, cutter, gate, deliver, telegram,
reports, pace) plus `pipeline/tests/`. Includes the remaining Day-0 steps: the validation
benchmark (§7.5, sets worker count) and writing the launchd plist (§7.6) — but do NOT load the
plist yet. Write real pytest tests as you go and keep
`PYTHONPATH=. uv run --with pytest pytest pipeline/tests translator/tests -q` green.
Any test upload to Drive II goes only under a `_pipeline_test/` folder and is deleted afterward.
Drive I is read-only, forever. Commit to git after each green §18 step (small, descriptive
commits; never commit secrets, media, or `*-analysis/` outputs — .gitignore already guards these).

**2 — Adversarial review loop (max 5 iterations).** After the build, run the code-review skill at
**max** effort over the new/changed code (do NOT use "ultra" — that is user-triggered only).
Adversarial focus, per iteration: correctness of every gate/threshold against plan §5 · state
machine and mid-batch-kill resume safety · deletion safety (nothing local deleted before
checksum-verified upload; ledger/dossiers never deleted) · spec §1.5 compliance of everything
written for delivery · payment/ledger integrity (hours math, supersede rule, duplicate rules) ·
error handling (Gemini 429/outage → HOLD_VLM never silent-pass, partial downloads, Drive API
failures, disk low-water) · the two qa-v2 exact-phrase parsing traps (plan §10.5). Fix every
finding you assess as real, then review again. Stop early if a pass produces no findings needing
fixes; hard cap 5 iterations either way.

**3 — Independent verification.** Spawn a fresh agent (general-purpose) as code-verifier with this
brief: "Trust nothing the builder claims. Contract: `PIPELINE_IMPLEMENTATION_PLAN.md` §18 (+§5
thresholds). Independently run the full test suite and re-execute every acceptance check yourself,
then report PASS/FAIL per criterion with evidence." The checks include at minimum:
- The six local sample sessions at the repo root reproduce the expected verdicts:
  `2026-08-12…kamla` → wrong game, actually Xonotic (out-of-scope → reject path);
  `2026-08-12…xonotic` → deliverable; `2026-08-13T07-19-56…kamla` → reject (<70 s, zero mouse
  buttons); `07-34-23` → deliverable; `07-40-03` → reject (<70 s + menu tail);
  `2026-08-13…outer_wilds` → fix-in-post (fan-out + frozen-context pause at ~109.5–111.5 s +
  100 ms lag) and, after Phase-III fixes, qa-v2 PASS.
- Synthetic mid-clip pause → cutter produces `-pN` segments that each pass qa-v2 and the ≥70 s /
  ≥3-actions bars; gated windows satisfy spec §1.5.5 (no keys without actions).
- Staged upload to Drive II `_pipeline_test/` with checksum verification, deletion only
  post-verify, then test-folder cleanup.
- Forced kill mid-batch → next run resumes cleanly with no state corruption or double-count.
Fix whatever the verifier fails and re-run it until it passes (verifier re-runs don't count
against the 5 review iterations).

**4 — Go-live.** Only after the verifier passes: load the launchd agent, trigger the first real
run, process the first real batch end-to-end, and let it send the real per-batch Telegram message.

**5 — Report.** Final message: what was built, benchmark numbers + chosen worker count, review-loop
tally (iterations, findings fixed), verifier verdict per criterion, and first-batch results
(sessions, verdicts, hours). Update the status lines in `PIPELINE_IMPLEMENTATION_PLAN.md` and
republish it to the existing artifact by passing
`url: https://claude.ai/code/artifact/23216af1-94ed-43b9-a462-0f16e80d7c4e` — do not create a new
artifact. Commit everything.

## Guardrails

- Never print or commit secrets. Never modify Drive I. Never fabricate data (locked rule).
- Deletion of local media only after checksum-verified upload; ledger + dossiers are permanent.
- Telegram messages during build/testing are prefixed `TEST` and kept few.
- If hard-blocked on something only Adnaan can resolve, send ONE concise Telegram message about it
  via the bot and keep building everything that isn't blocked.
- Sub-agents you spawn have no context: their briefs must point at
  `PIPELINE_IMPLEMENTATION_PLAN.md` as the contract.
