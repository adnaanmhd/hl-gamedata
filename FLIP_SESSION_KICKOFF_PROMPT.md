# Kickoff — split-cascade fix → review loop → canary → flip (+resize) → backlog → payment endgame

You are picking up the continuous-pipeline work in `/Users/adnaan/Documents/hl-projects/hl-gamedata`.
This supersedes `CONTINUOUS_PIPELINE_KICKOFF_PROMPT.md` as the live plan; that document is still
the source of record for Adnaan's 08-17 rulings and the endgame steps, and everything in it that
this file does not change still binds.

---

## 0. LAUNCH PROTOCOL — READ THIS FIRST, THEN STOP

**Do not start work. Do not run a single tool call. Do not read files, query the VM, or edit
anything.**

Read this document, then reply with a SHORT readiness message (what you understand the job to be,
anything you think is wrong, and any question you must have answered). Then **wait**.

**Work begins only when Adnaan types exactly: `center, form on me and launch attack`**

Until that phrase arrives, you are idle. If he asks a question before then, answer it — but start
no work.

**Session configuration, set before you begin:**
- **Model: Opus 5** (`/model opus`)
- **Ultracode mode** — include the word `ultracode` in the launch message so multi-agent
  orchestration is authorised for the review iterations. The adversarial review loop below is
  not optional and is not runnable single-threaded at the required depth.

---

## 1. Work order — this sequencing is a RULING, not a preference

1. **FIRST: implement R1–R3 (§4) in full, with their eight regression tests, suite green.**
2. **THEN: run the three remaining adversarial review iterations (§5).**

Reason: the review iterations must cover the split-cascade changes. Reviewing first and editing
after would ship unreviewed threshold changes into the flip deploy set. Do not reorder this.

Everything after that (§6) runs in the order listed.

---

## 2. What you are inheriting

**Repo:** HEAD is `985f516`, working tree clean of pipeline changes, **suite 368 tests green on
Mac AND on the VM side checkout**. Run it with:

```
PYTHONPATH=. uv run --with pytest --with numpy --with opencv-python-headless --with rerun-sdk \
    pytest pipeline/tests translator/tests -q
```
VM variant pins `numpy==2.4.6 opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0`.

**Commits that matter (newest first):**
- `985f516` r-loop 2 verify completion (15 findings) **+ restoration of agent mutations** (see §3.3)
- `d20dd9d` r-loop 2 verified findings (12) — **this commit contains two agent-injected mutations,
  fixed in `985f516`; do not cherry-pick it alone**
- `717f917` shutdown gate-drain fix + `FLIP_RUNBOOK.md`
- `77d348f` r-loop 1 (24 findings)
- `6cdd898` the continuous driver itself + 24 tests
- `5684c04` `PIPELINE_CONTINUOUS_DESIGN.md` — the design spec of record
- `a4f93de` fix-failed tolerance patches — **committed, NOT deployed, deploys at flip**

**Documents to read before code:** `PIPELINE_CONTINUOUS_DESIGN.md` (driver spec),
`FLIP_RUNBOOK.md` (canary/flip/endgame command sequences), `PIPELINE_ARCHITECTURE.md`,
`PIPELINE_IMPLEMENTATION_PLAN.md` §4–§6, and `CONTINUOUS_PIPELINE_KICKOFF_PROMPT.md`.

**Built and reviewed already:** the continuous driver (`pipeline/continuous.py`,
`python -m pipeline run-continuous`, behind `PIPELINE_CONTINUOUS`), its systemd units
(`hl-continuous.service` + alert unit), the 3-h digest, autoscale with Gemini-429 backpressure,
per-session fix scheduling, and **two of the five permitted adversarial review iterations
(39 confirmed findings fixed)**. Three iterations remain available.

**VM state — verified 22:33 IST 2026-08-17, RE-QUERY, it drifts:**
- `hl-pipeline-vm` (asia-south1-a, project `hl-gamedata-pipeline`, ssh alias
  `hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline`; after any stop/start re-run
  `gcloud compute config-ssh --project=hl-gamedata-pipeline` — the ephemeral IP moves).
- `hl-recal-rebuild` **LIVE** (~28 h in) and `hl-recal-watch` **LIVE**. `hl-pipeline.timer`
  disabled, `hl-backup.timer` enabled.
- Ledger: DELIVERED 485 (**47.34 h**, all kamla — no OW row exists yet), SPLIT 316,
  FIX_QUEUED 147, DISCOVERED 66, REJECTED 28, VALIDATING 23, READY 12, QUARANTINED 10,
  INGESTED 9. **29 open batch rows** (the dormant batch driver's rollback state — never touch them).
- Disk 161 GB free of 246 GB.
- **Your code has never been deployed.** All VM testing used the side checkout
  `~/hl-gamedata-continuous-test`, which the running rebuild does not read from. `~/hl-gamedata`
  is still the rebuild's tree and must stay untouched until the flip.

---

## 3. NEW findings since the original kickoff — these change the picture

### 3.1 The rebuild is being throttled by TWO independent causes, not one

**Cause A — host memory ballooning (found this session, evidence-backed).** The VM is an
`e2-standard-32`, and **E2 is the only GCE machine family that uses the memory balloon device**
[web: Google Compute Engine "Next generation dynamic resource management" docs]. `virtio_balloon`
is loaded and bound to `virtio2`. Measured from `/proc/vmstat` balloon counters, the host inflates
a balloon inside the guest that **oscillates between ~0 and ~94 GiB**, at peak leaving under
1 GiB free; cumulative inflation was 2.05 TiB over 26.6 h of uptime — continuous churn, not a
spike. Each squeeze evicts the ~31 GiB page cache, so video decodes re-read from disk. Our own
footprint is tiny: ~3.5 GiB anonymous memory total, validation workers ~263 MB each.

Verify it yourself in one command:
```
ssh hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline \
  'awk "/balloon_inflate/{i=\$2} /balloon_deflate/{d=\$2} END {printf \"%.1f GiB\n\", (i-d)*4096/1073741824}" /proc/vmstat'
```

**This resolves the resize gate the original kickoff set** ("read actual RAM usage on the VM
first — c2d-highcpu-56 has 112 GB vs current 128; expected fine, verify, don't assume"). A naive
`free -g` reads ~89 GiB used and looks like we barely fit. **That reading is wrong** — nearly all
of it is host confiscation that does not travel with us. C2D is a dedicated family with no balloon,
so 112 GiB is genuinely available against a ~4 GiB working set plus page cache. Even at
`CONT_POOL_MAX` = cores−12 = 44 workers (~11.6 GiB) there is ample headroom.
**Adnaan has approved the E2 → C2D move in principle** (2026-08-17). After the resize, confirm the
balloon is gone with the same command plus `lsmod | grep balloon`.

**Cause B — the self-perpetuating split cascade**, diagnosed by the sister session and ruled on by
Adnaan. See §4.

**Carry this forward honestly:** the resize fixes A and does nothing for B; R1–R3 fix B and do
nothing for A. Any throughput projection must account for both separately. Do not let either be
presented as the whole explanation.

### 3.2 Numbers of record (unchanged unless noted)

- Dead-black recalibration: luma<5, reject ≥99.5%. Old rule mass-false-positived 26.24 h.
- Throughput: 16 vCPU/8w = 26.3 val-min per processed fh; 32 vCPU/10w = 12.0 processed /
  **21.2 per UNIQUE collected fh (split tax 1.76×) ≈ 68 fh/day**; mission pace needs ~119 fh/day.
  **These were all measured under balloon pressure and with the cascade active — treat them as
  floors, and re-measure after the flip.**
- Yield: pre-rebuild 41.4% → rebuild-to-date ~96% on settled footage.

### 3.3 OPERATIONAL WARNING — verify your own working tree after multi-agent reviews

During review iteration 2, a verifier subagent **mutation-tested `pipeline/continuous.py` in the
working tree and left the mutations behind** (it disabled the digest call in the housekeeping loop
and inverted the `CONT_DAILY_REPORTS` interlock). They were committed in `d20dd9d`, and **the full
suite passed with them** — which was precisely the coverage gap that agent was proving. They were
caught only because a newly-added test failed, then restored in `985f516`.

Mandatory discipline for every review iteration you run:
- After the workflow returns, **before committing**: `git diff` every touched file and confirm each
  hunk is yours; `grep -rn "MUTATION" --include="*.py" .`; check `git status` for agent-left files.
- Never trust "suite green" as proof the tree is unmodified — that is exactly what failed here.
- Prefer instructing verifier agents to reason about code rather than edit it; if one mutation-tests,
  it must restore and say so.

---

## 4. NEW RULINGS — split cascade (Adnaan, 2026-08-17, relayed via the sister session)

### The finding

The rebuild is ~28 h in against a planned ~7–8 h with net queue drain ≈ zero: gross settle rate
~28 rows/hr, but child rows are created almost as fast, because splitting is self-perpetuating.
Two compounding mechanisms, both verified in code and ledger data:

1. **The 40-static cap under-scans parents.** `validate.py` classifies only the 40 LONGEST static
   candidates when a clip has more. 474 dossiers hit this cap (median 75 candidates, p90 153, max
   875) — on a median capped session 35 windows were never examined. After a split each child has
   fewer candidates, falls under the cap, and discovers junk the parent's scan was capped out of
   seeing, so the child re-splits.
2. **The 0.2% ratchet tightens as clips shorten.** The keep test is
   `span <= KEEP_GATE_MAX_S AND span <= KEEP_GATE_MAX_FRAC * dur`. Parent avg 1134 s → 2.3 s
   allowance; child avg 342 s → 0.68 s. Blips the parent deliberately KEPT become cut-triggers in
   the child purely because the clip got shorter. Each cut makes the next more likely.

Ledger evidence (recursive CTE over `parent_id`): 320 roots → 600 children → 145 grandchildren,
three generations deep; 109 of the 600 depth-1 children are themselves SPLIT.

Separately: `min_s=0.8` in `validate.py` is the only load-bearing threshold in the pipeline with no
provenance — one bare inline literal, absent from `config.py`, unmentioned in the plan, pinned by
no test.

### R1 — Scanner cuts restricted to >5 s; shorter windows gating-only

The scanner's `static_windows` → `extra_windows` path may propose a cut **only** for windows
longer than 5.0 s. Make the `CNT_MID_NONGAMEPLAY` emission in that path conditional on
`span > 5.0` rather than deleting it.

- Windows ≤5 s: never propose a cut. They may still emit `INP_FROZEN_ACTIONS` if inputs occurred
  inside; otherwise advisory only.
- **`min_s` STAYS at 0.8 — do NOT raise it.** Adnaan's initial instruction to bump it to 5 s is
  SUPERSEDED. The purpose of this path is catching freezes shorter than 4 s for input gating; a
  5 s trigger would find nothing. 0.8 now has a real justification and **must be named in
  `config.py` with a provenance comment stating it**.
- **KEEP** the VLM label + confidence filter. Blanking inputs on a legitimately still moment of
  real gameplay would destroy real training data; the confirmation that the window is genuinely
  menu/loading/pause/etc. must stay.
- The 40-cap stays as a pure cost bound. It can no longer drive a cascade: gating creates no child
  rows, and only ~95 windows ledger-wide clear the >5 s cut bar (~0.2 per session).
- Uses the **same 5 s** as R3 — one threshold, not two.

Why >5 s rather than removing scanner cuts entirely: over the current ledger there are 443
scanner-sourced `CNT_MID_NONGAMEPLAY` reasons; 95 (21.4%) exceed 5 s, totalling 578.6 s of
confirmed cutscene/loading/pause/menu, longest single window 16.0 s. Only 47 scanner windows
carried inputs, so ~90% would not even be gated — without the carve-out that footage would ship
silently with nothing but a dossier advisory. The cascade comes from the short windows; the quality
protection comes from the long ones, and they are separable.

Mechanism, for context: two distinct VLM calls with different bars. The 4 s sweep needs ≥2
consecutive samples all at conf=high to raise a gating window; `classify_stills` inspects one
midpoint frame and accepts high OR medium. A real cutscene can fail the sweep's bar while the
targeted call identifies it confidently. Scanner windows are also filtered to exclude anything
overlapping a VLM window (`_overlaps_engine`), so every scanner window is by construction one the
sweep did not catch.

### R2 — Remove the 0.2% ratchet

`KEEP_GATE_MAX_FRAC` comes out of the keep test entirely (both call sites). The test becomes
absolute-only.

### R3 — Mid-clip keep bar 2 s → 5 s

`KEEP_GATE_MAX_S` 2.0 → 5.0, applying to `CNT_MID_NONGAMEPLAY` (the VLM path) ONLY. Edge behaviour
UNCHANGED: `CNT_EDGE_NONGAMEPLAY` still trims non-gameplay touching clip head/tail at any length.

Net keep test after R2+R3: `if span <= 5.0: keep (or gate if inputs inside) else: cut`.

**This supersedes a recorded ruling.** `PIPELINE_IMPLEMENTATION_PLAN.md:131` attributes
"keep+gate if ≤2 s contiguous AND ≤0.2% of clip" to "Adnaan round-3". It was verified NOT to be an
Odyssey spec constraint, so this is Adnaan superseding his own prior ruling — **record it as an
explicit supersession in the plan, not a silent edit.**

### R4 — Deploy at flip, never before

Do NOT deploy mid-run. This follows the convention already in force for the tolerance patches
(`a4f93de`): the running rebuild must keep judging under the old checkers so the refix population
stays coherent. R1–R3 ride in that same deploy set.

Consequence to carry: the ledger will contain rows judged under two methodologies. This is NOT a
new hazard — it is the same accepted situation already in force for `a4f93de`. It affects the
payment sheets and the reject-reason table vs `reject-reasons-pre-rebuild.json`. Those comparisons
are mixed-methodology and **must be labelled as such; never present them as like-for-like.**
Adnaan has accepted the tradeoff — do not re-litigate it, just never let it be presented as clean.

### Implementation mandate for R1–R3

- Put **every** touched threshold in `config.py` as a named constant with a provenance comment
  citing "Adnaan 2026-08-17" — explicitly fixing the magic-number problem that let 0.8 exist
  unexplained.
- **Eight real, non-tautological regression tests, minimum:**
  1. 3 s mid-clip freeze (VLM path) → KEPT
  2. 6 s mid-clip freeze (VLM path) → CUT
  3. scanner-found window ≤5 s → NEVER `CNT_MID_NONGAMEPLAY`, at any clip duration
  4. scanner-found window >5 s → still cuts
  5. scanner-found window ≤5 s WITH inputs → `INP_FROZEN_ACTIONS`
  6. sub-4 s freeze with inputs → gated (the specific gap R1 exists to close)
  7. edge windows still trim at any length
  8. same span at two different clip durations → same verdict (proves the ratchet is gone)
- Suite green on Mac and VM. Commit path-scoped citing these rulings. **NEVER push.**
- **DO NOT deploy, do not touch `hl-recal-rebuild`, do not stop or start any systemd unit.**

**Line-number note:** as of `985f516` the sister session's cited lines were verified accurate
(`validate.py:316`/`:348` keep tests, `:356-359` the `else:` cut branch, `:976` `min_s=0.8`,
`:990` the 40-cap, `:1025` the VLM label filter; `config.py:34-35` the two constants). They will
drift once you edit. **Locate code by symbol and behaviour, never by remembered line number.**

---

## 5. Adversarial review loop — 3 iterations remain (cap is 5; 2 are spent)

Adnaan's 08-17 ruling, unchanged: **every** iteration runs the full composition —
**deep FULL-CODEBASE review** (`pipeline/` + `translator/` + `tools/`, not just the diff)
**+ delta review of everything changed since loop start + adversarial hunting for bugs introduced
by the loop's own fixes**. Multi-agent lanes; findings **adversarially verified with 2-vote refute
discipline** (a finding dies only if BOTH refuters defeat it) before they count.

- Fix confirmed findings each iteration, suite green **both hosts**, commit path-scoped per
  iteration citing this prompt.
- The loop EXITS EARLY only when an iteration ends with zero confirmed findings and nothing left
  to fix. **Anything verified-but-unfixed still standing after the final iteration is highlighted
  to Adnaan, severity-ordered, before proceeding.**
- Working workflow script from this session (8 lanes + verify stage) is at
  `.claude/projects/.../workflows/scripts/continuous-review-iter-*.js`; the standalone verify-stage
  resumer is in the scratchpad. Reuse or rewrite, but **keep the 2-vote refute discipline** and
  **keep the accepted-behaviours list current** or you will re-litigate settled decisions every round.
- **Apply §3.3 tree-verification discipline after every iteration.**

Only after the loop exits clean (or Adnaan acknowledges leftovers): run the **independent REAL
end-to-end verification** — a fresh agent that wrote and reviewed none of this code, exercising the
actual system (real VLM calls, real Drive II `_pipeline_test/` uploads purged after, real
kill/resume) and reporting whether everything works. **Its verdict is relayed VERBATIM and a
BLOCKED-with-error never becomes a pass.** This is IN ADDITION to the step-10 production verifier
at the very end.

---

## 6. Remaining steps, in order

1. **R1–R3 implemented + 8 tests + suite green both hosts** (§4). Nothing else starts first.
2. **Review iterations 3, 4, 5** (§5), then the independent REAL e2e verification.
3. **Canary** — `FLIP_RUNBOOK.md` §5: `HL_PIPELINE_HOME=~/hl-pipeline-test`, test-mode Telegram,
   Drive II `_pipeline_test/` only (purge after via `deliver.cleanup_test_folder`), seeded sessions
   + the live read-only Drive scan, **3-leg kill matrix** (kill -9 during download, validation,
   upload → exact resume, no double-DELIVERED, hours once, no stub rrd), autoscale observed moving,
   digest fires. **Deleting or cleaning ANYTHING in the real pipeline home from the canary is
   forbidden.**
4. **THE FLIP** — `FLIP_RUNBOOK.md` §6, announce on Telegram before and after:
   - a. `systemctl stop hl-recal-watch` **first** (its end-of-run message would otherwise race the
     announcements), then `systemctl stop hl-recal-rebuild`. Expect a stale `run.lock`; the tools
     and driver pid-reclaim it.
   - b. RAM re-check (§3.1 — expect the balloon reading, not a naive `free -g`), then
     stop VM → `set-machine-type c2d-highcpu-56` → start → `config-ssh` → **suite green on the VM**.
     Confirm the balloon is gone.
   - c. **Set `CONT_DAILY_REPORTS = False`, commit, THEN deploy.** The deploy set is:
     continuous driver + `a4f93de` tolerances + **R1–R3**. Re-touch rrd stubs.
     `bash tools/vm_setup.sh` (installs units, arms nothing).
   - d. `tools/recal_refix_reset.py` dry-run → review the JSON plan → `--yes`.
   - e. `bash tools/vm_setup.sh --enable-continuous` (arms hl-continuous + backup timer, disarms
     `hl-pipeline.timer`, asserts both). Watch the first hour: 429 rate in
     `~/hl-pipeline/logs/vlm-pressure.jsonl`, autoscale decisions in journald, disk, first digest.
5. **Payment endgame** — stop the driver, `tools/recal_regen_sheets.py` preview → sanity-read both
   sheets → `--send` (final invariant: anchor == `2026-08-16T05:32:50+00:00`), then flip
   `CONT_DAILY_REPORTS = True`, deploy, restart. Update `NOTE_FOR_D3.md`; purge old sheet copies
   from the GCS mirror after replacements verify.
6. **Tree verify + deletion** — driver stopped, `tools/recal_verify_tree.py` CLEAN (or every defect
   explained and fixed) → delete `superseded-prerecal/` + `superseded-refix-*/`. **LAST destructive
   act.** Restart driver.
7. **Reject-reason table** — exhaustive reason×count (+hours) over the final ledger vs BOTH
   baselines (committed `reject-reasons-pre-rebuild.json`: 138 rows / 26.3 h / 113 recordings; and
   the drifted kill-time snapshot: 149 rows / 28.42 h / 126 recordings, black-frozen SOLE-reason
   partition 26.24 h of 28.42). Present both, sourced, **labelled mixed-methodology** (§4 R4).
   Decision input for Adnaan — present, don't act.
8. **Independent live verifier** (fresh agent, never one that wrote or reviewed this code; verdict
   relayed VERBATIM): suite both hosts; kill-resume spot check; ledger consistency (every
   non-quarantine/dup row terminal, hours counted once, no stub rrd staged); Drive II ↔ ledger
   exact match + superseded trees GONE; sheets exist for both days + anchor/markers/stamps coherent
   + old sheets purged; digest firing on schedule; autoscale + 429 backpressure observed; secrets
   sweep (counts only); `_pipeline_test/` purged.
9. **Final report** (verdict-first): before/after state table; per-player delivered-hours delta vs
   the parachute snapshot; reason×count table; sheets sent; Drive II repopulation + deletion proof;
   continuous-driver throughput measured (unique-collected fh/day — count the split tax honestly)
   vs the ~119 fh/day Aug-24 pace; verifier verdict verbatim; open items (§8).

---

## 7. Locked rulings — do NOT re-ask

From the original kickoff (all still binding): no batches; continuous 5-min polling; local media
cap ~40 sessions with the 100 GB disk low-water; adaptive autoscale with Gemini-429 backpressure;
3-h digest from inside the driver replacing per-batch toplines; per-session fix scheduling
(FIX_QUEUED re-enters immediately, R2's 2-attempts-then-reject unchanged); HOLD_VLM retried every
30 min forever; always-on systemd service with the batch timer kept dormant as rollback; resize to
`c2d-highcpu-56` at the flip; canary then flip, and the flip does NOT wait for the backlog to drain.

Invariants that survive untouched: F5 (nothing passes unlooked-at; VLM failure → HOLD_VLM), R23
ladder semantics (sticky until a quiet period), duplicate rule (md5 accept-earliest-ctime), R7
delivery layout + 5 spec files, R17 rrd 20%/game/day, R11/R12 payment semantics, stamps→anchor→
marker ordering, per-session state machine + events audit, qa-v2 final gate, dossier evidence,
R6 Drive I read-only forever.

New this session: `CONT_DAILY_REPORTS` is the payment-endgame interlock (False at flip, True after
the regen — enforced inside `send_daily_report_if_due`/`send_folder_issues_if_due` so the rollback
batch path cannot bypass it); the flip tools ACQUIRE the run lock for their whole duration and
`_pid_is_pipeline` recognises them; the continuous driver never touches the `batches` table.

---

## 8. Ground rules

- Machine-wide CLAUDE.md: **verify before claiming; read whole sources; mark `[assumption]`.**
- Commits path-scoped per green step. **NEVER push.** Never touch the Obsidian vault.
- Secrets in `~/.config/hl-gamedata/secrets.env` (Mac + VM). Never print, log, or commit keys.
- **Drive I (`drive-collect:`) read-only forever (R6).**
- Full suite after every step, **Mac AND VM** for code that ships.
- Deploy (flip only):
  `rsync -a --delete --exclude 'out/' --exclude '__pycache__/' --exclude '*.rrd' ./ hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline:hl-gamedata/`
  then re-touch rrd stubs. Until the flip, test on the side checkout
  `~/hl-gamedata-continuous-test` only.
- **Coordination:** confirm R1–R3 are in the deploy set the flip actually executes. If a different
  session runs the flip, hand this document over explicitly — do not assume.

---

## 9. Open items carried forward

- Gemini billing tier unverified (vault OPEN CONTRADICTION); credential rotation deferred
  (Adnaan 08-16); Vertex failover dark (`VLM_FAILOVER_ENABLED=False`, both keys 403).
- `_seed_shift_record` exact-equality can burn one fix attempt (minor); torn `verdict.json` in the
  nightly DR mirror (cosmetic); 29 stale open batch rows pre-reset (bookkeeping wart, deliberately
  left as rollback state); lagging-game ordering note.
- CONTESTED 1–1 review finding on `video_active` dark-footage interplay (mechanism confirmed,
  delivery-impact refuted via the ≥3-actions gate) — Adnaan may want a ruling.
- Sequencing note the sister session raised and Adnaan should see: R4 says "apply now", but nothing
  applies until deploy. The longer the hold, the larger the old-methodology share of the ledger.
  Surface it; do not decide it.
- Post-flip: re-measure throughput. Every existing figure was taken under balloon pressure with the
  cascade active.
