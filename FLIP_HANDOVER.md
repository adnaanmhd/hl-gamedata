# Handover — code session → flip session

**Status: DRAFT (review loop still running). Do not act on this until the
"Handover is live" line at the top of §0 says so.**

This document exists because `FLIP_SESSION_KICKOFF_PROMPT.md` §8 requires an
explicit hand-over when a different session runs the flip: *"confirm R1–R3 are
in the deploy set the flip actually executes. If a different session runs the
flip, hand this document over explicitly — do not assume."*

Adnaan ruled on 2026-08-18 that **a new session executes the canary and flip**.
This session (the code session) owns everything up to and including the
independent end-to-end verification; the flip session owns
`FLIP_SESSION_KICKOFF_PROMPT.md` §6 steps 3–9.

---

## 0. What the flip session must read, in order

1. `FLIP_SESSION_KICKOFF_PROMPT.md` — the live plan and Adnaan's rulings. Still
   binding in full **except** that §6 steps 1–2 are DONE (see §1 below).
2. `FLIP_RUNBOOK.md` — the command sequences. **§6b and §6c were rewritten this
   session**; read them rather than the kickoff's summary of them.
3. `PIPELINE_CONTINUOUS_DESIGN.md` — the driver spec of record.
4. `PIPELINE_IMPLEMENTATION_PLAN.md` §5 — including the new supersession ¶ for
   the mid-clip keep rule.
5. This document.

**Handover is live: NO — review loop in progress.**

---

## 1. What is already done (do not redo)

| Kickoff step | State |
|---|---|
| §6.1 — R1–R3 + 8 regression tests, suite green both hosts | **DONE**, commit `ba1b17d` |
| §6.2 — review iterations + independent e2e verification | *(in progress — filled in at finalisation)* |
| §6.3 onward — canary, flip, endgame, deletion, reports | **YOURS** |

---

## 2. The deploy set — confirmed, and how to verify it yourself

Per R4 nothing here is deployed; it all ships at the flip. The set is **four**
things, not the three the kickoff lists (r-loop 3 added a fourth):

1. the continuous driver (`pipeline/continuous.py` + units),
2. the `a4f93de` fix-failed tolerance patches,
3. **the split-cascade rulings R1–R3** (`ba1b17d`),
4. **the r-loop 3 review fixes** (`c0831f2` and any later r-loop commits).

Because they are all ancestors of `HEAD` on `main`, deploying HEAD deploys all
four. **Verify after rsync, before arming** — this is cheaper than discovering
it from throughput a day later:

```bash
ssh hl-pipeline-vm.asia-south1-a.hl-gamedata-pipeline \
  'grep -n "KEEP_GATE_MAX_S\|SCANNER_STATIC_MIN_S\|KEEP_GATE_MAX_FRAC" \
     ~/hl-gamedata/pipeline/config.py'
```

Expect `KEEP_GATE_MAX_S = 5.0`, `SCANNER_STATIC_MIN_S = 0.8`, and **no**
`KEEP_GATE_MAX_FRAC` assignment. `FLIP_RUNBOOK.md` §6c carries the same check.

---

## 3. Facts established this session that change the runbook

### 3.1 The E2 → C2D resize is cleared on every checkable gate

Verified against the project's own API on 2026-08-17 (not from documentation —
one web source was wrong about zone availability):

| Gate | Verified state |
|---|---|
| `c2d-highcpu-56` in `asia-south1-a` | offered, 56 vCPU / 114688 MB, not deprecated (also `-b`, `-c`) |
| `C2D_CPUS` quota, asia-south1 | limit 100, usage 0 → 56 fits |
| aggregate `CPUS` quota | limit 100, usage 32 → 56 after the move |
| `minCpuPlatform` | **unset** — an Intel pin would have blocked the AMD move outright |
| boot disk | `pd-balanced` 250 GB, C2D-compatible |
| `onHostMaintenance` | `MIGRATE`; C2D supports live migration |

**What is still unknowable in advance:** zone capacity.
`ZONE_RESOURCE_POOL_EXHAUSTED` can only surface when the instance starts, and
the VM will already be stopped at that moment. `FLIP_RUNBOOK.md` §6b now carries
the exact undo. **Do not block the flip on the resize** — it fixes the balloon
(throttle cause A) and nothing else; R1–R3 fix the cascade (cause B). They are
independent.

### 3.2 `free -g` on the VM is meaningless — read the balloon

Measured 2026-08-17 22:51 IST: balloon fully deflated
(`balloon_inflate == balloon_deflate == 552697465`, net 0 GiB) with `free -g`
reading **2 GiB used / 101 free** — while cumulative inflation over 27 h of
uptime was **2.06 TiB**. The same command returns anywhere between ~2 and
~94 GiB used depending on when you run it. The kickoff's "naive `free -g` reads
~89 GiB used" is one sample of an oscillation, not a stable reading. Use:

```bash
awk '/balloon_inflate/{i=$2} /balloon_deflate/{d=$2} END {printf "%.1f GiB\n", (i-d)*4096/1073741824}' /proc/vmstat
```

### 3.3 The VM has **no service account attached**

Metadata `service-accounts/` is empty, so the VM cannot call GCP APIs at all —
it could not answer the C2D question itself. Nothing in the pipeline needs it
today (Drive goes through rclone, Gemini through an API key), but any tool you
write that assumes the VM can reach GCP will fail.

### 3.4 `uv run` forwards SIGTERM to its python child

Verified on both hosts (Mac homebrew uv; VM uv 0.12.5) by installing a handler
in the child, SIGTERMing the uv wrapper, and confirming the handler ran. This is
what makes `KillMode=mixed` safe in `hl-continuous.service.in` — if a future
change drops the uv wrapper or pins a different uv, **re-run that probe**.

---

## 4. Ledger measurements (re-query — these drift fast)

Measured 2026-08-17 17:29 UTC, read-only, with the same recursive CTE the sister
session used, so it is like-for-like:

| | kickoff (17:03 UTC) | measured (17:29 UTC) |
|---|---|---|
| roots / children / grandchildren | 320 / 600 / 145 | 320 / **623** / **155** |
| depth-1 children themselves SPLIT | 109 | **116** |
| open (non-terminal) rows | 257 | 245 |
| DELIVERED | 485 | 497 (**48.51 h**, still 100 % kamla) |

**The cascade was still running at ~76 new child rows/hour** under the old
rules — every one judged under the methodology R1–R3 replaces. That is the cost
of the R4 hold, and it is why Adnaan ruled the review loop should stop as soon
as an iteration goes quiet rather than spending all five.

**One correction to the kickoff:** it says net queue drain ≈ zero. Over the
measured window the queue *was* draining, ~28 rows/hour net. That is a single
26-minute sample and the 6-hour delivered rate is lower (~17/h), so treat it as
indicative only — but do not plan on the assumption that drain is zero.

**Timestamp trap:** `updated_at`/`delivered_at` are ISO-8601 with a `T`, so
SQLite string comparison against `datetime('now')` (space-separated) matches
**every** row and silently returns garbage. Parse in Python, or compare against
an ISO-formatted bound. This produced a plausible-looking wrong answer on the
first attempt.

---

## 5. Open items you inherit

- **Mixed methodology is now unavoidable and must always be labelled.** The
  ledger holds rows judged under the old checkers and (post-flip) rows judged
  under R1–R3 + the tolerance patches. Comparisons against
  `reject-reasons-pre-rebuild.json` and the payment sheets are therefore
  mixed-methodology. Adnaan has accepted this; never present it as like-for-like.
- Gemini billing tier unverified; credential rotation deferred (Adnaan 08-16);
  Vertex failover dark (`VLM_FAILOVER_ENABLED=False`, both keys 403).
- `_seed_shift_record` exact-equality can burn one fix attempt (minor); torn
  `verdict.json` in the nightly DR mirror (cosmetic); 29 stale open batch rows
  (deliberate rollback state — never touch them); lagging-game ordering note.
- CONTESTED 1–1 review finding on `video_active` dark-footage interplay
  (mechanism confirmed, delivery impact refuted via the ≥3-actions gate) —
  Adnaan may want a ruling.
- **Post-flip: re-measure throughput.** Every existing figure was taken under
  balloon pressure with the cascade active. They are floors, not estimates.

---

## 6. Discipline that must survive the handover

- **Never push.** Commits are path-scoped per green step.
- Full suite on **Mac AND VM** for anything that ships:
  `PYTHONPATH=. uv run --with pytest --with numpy --with opencv-python-headless --with rerun-sdk pytest pipeline/tests translator/tests -q`
  (VM pins `numpy==2.4.6 opencv-python-headless==5.0.0.93 rerun-sdk==0.36.0`).
- Until the flip, test on the side checkout `~/hl-gamedata-continuous-test`
  only. `~/hl-gamedata` is the running rebuild's tree.
- Drive I (`drive-collect:`) is **read-only forever** (R6).
- Secrets live in `~/.config/hl-gamedata/secrets.env` — never print, log or
  commit them.
- **After any multi-agent review: verify your own working tree before
  committing.** `git diff` every hunk, `grep -rn "MUTATION" --include="*.py" .`,
  and `git status` for agent-left files. A verifier once left mutations behind
  and the full suite passed with them — "suite green" is not proof the tree is
  unmodified.
