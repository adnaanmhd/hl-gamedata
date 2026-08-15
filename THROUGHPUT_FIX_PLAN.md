# Throughput fix plan — recommendation + implementation-ready spec

Written 2026-08-14 ~23:35 IST. Companion documents:
`BOTTLENECK_FINDINGS.md` (the numbers), `STREAMING_EXPLAINER.md` (option
explored), `GCP_OFFLOAD_EXPLAINER.md` (option explored). **No code has been
changed — R5 is your locked ruling; this ends in a go/no-go ask.**

> **Superseded in detail by `PIPELINE_IMPLEMENTATION_PLAN.md` v2 §6 (2026-08-15).** Adnaan ruled
> the host moves to a GCP VM (R19) and approved this driver (R20); the plan's §6 contract
> carries the corrections found in adversarial review — spawn `mp_context` for the pool under
> threads; the honest state-ownership matrix (U also writes FIX_QUEUED/REJECTED on final-gate
> failure and hands back to V); `start_batch` must WRITE the sids list (this doc's "the
> `summary_json` sessions list is already written" is wrong — it is `'{}'` until `finish_batch`,
> `ledger.py:207-212`); in-loop daily-report/backup calls; and re-pinned line refs (code moved
> at f49bdd6). The `--bwlimit` clause is dropped on the VM. Where this doc and plan §6 disagree,
> plan §6 wins.

## The fix, very simply

**The problem in one line:** the pipeline does its three jobs — download,
check, upload — strictly one after another, so two of the three resources
are always doing nothing, and the maths says it tops out at 67–78 footage
hours per day when the mission needs ~110.

**The chosen fix: keep the batches, overlap the batches.** Like a laundry
room: while the washer runs load 2, the dryer runs load 1, and load 3 is
being fetched. Same loads, same order, same folding rules — nobody waits
for a machine that's free. Concretely: while batch 12 is being *checked*
(CPU busy), batch 13 is already *downloading* (internet busy) and batch 11
is *uploading* (internet's other direction busy). Never more than three
batches in the house at once.

**Why this one and not the two alternatives explored:**
- *Full streaming* (no batches at all, three endless loops) buys ~10–15%
  more ceiling but dissolves the batch — your reports would describe
  rolling windows instead of real batches, and the driver rewrite is
  bigger. Not needed to hit pace; kept in reserve.
- *Renting a Google cloud computer* fixes the slow line instead of the
  idle time. It works (verified: Google does not charge a rented VM for
  Drive traffic; ~$135/10 days) but means migrating everything mid-crunch
  and moving both secret keys onto a cloud machine. Wrong first move,
  right insurance — it has concrete adoption triggers in
  `GCP_OFFLOAD_EXPLAINER.md` §3d.

**What you'll see change day-to-day:**
- *Reports*: nothing. Same per-batch Telegram message, same daily 14:00
  report, and a batch still means a real batch of ≤10 sessions.
- *Logs*: lines now interleave, each prefixed `[dl b13]` / `[val b12]` /
  `[up b11]` so the story stays readable; each batch also logs its three
  stage times (your live gauge of what binds).
- *Disk*: up to ~3 batches on disk at once (~30 GB typical) instead of ~1;
  the 100 GB free-space guard is unchanged and stays the hard floor.
- *Throughput*: ceiling goes from 67–78 to **154–183 fh/day at 24/7**
  (128–153 with ~4 h of Mac sleep) at today's 4 workers; flipping the
  existing workers knob to 6 lifts it to 183–217 (152–181). The mission
  needs ~111 delivered-h/day from Aug 16 — with rejects that means
  processing ~133 fh/day — so 4 workers clears it if the Mac mostly stays
  awake, and 6 workers clears it with room even at the pessimistic end.

**Pros:** hits the pace with margin on the machine we already trust; phase
logic (validation, fixes, delivery, all gates) untouched; batches and
reports keep their meaning; one config flag switches back to today's
lockstep instantly. **Cons:** it is a concurrency change to the
orchestrator days before go-live (three threads sharing the ledger — the
spec below pins the locking rules and a forced-kill test); transfers now
run while the VLM talks to Gemini on a bufferbloated line, so transfers get
capped at ~80% speed to keep latency headroom (already priced into the
numbers above); and Gemini usage per day roughly doubles-to-triples with
throughput (the billing-tier question you're checking matters more).

---

> ## GO / NO-GO — the ask
>
> Approve **Stage 1: batch-pipelining overlap**, which needs this narrow
> amendment to R5 (nothing else in R5 changes):
>
> *"Batches may overlap in flight: while batch N validates/fixes, batch
> N+1 may download and batch N−1 may upload; at most 3 batches (≤30
> sessions) local at once; per-batch order and all other R5 steps
> unchanged."*
>
> Also note (no ruling needed): WORKERS stays 4 at go-live and flips to 6
> only on the criterion in §5; streaming and the VM stay shelved behind
> their own documents and would come back to you separately.
>
> **Reply "go" and I build it as specced below; "no-go" and lockstep
> ships as-is (67–78 fh/day @ 24/7 — below required pace, stated
> plainly).**

---

## Implementation-ready spec (for the build, after "go")

### 1. Shape

One process (run-lock unchanged), three threads at batch granularity:

- **D (downloader thread)** — pulls the next batch of `DISCOVERED` via
  `ingest.next_batch` and runs today's `_download_phase` on it, when
  `batches_in_flight < 3` and `disk_free_gb ≥ 100` (F7 check stays exactly
  where it is, `run.py:102-105`).
- **V (validator, the main thread)** — today's `_validate_phase` +
  `_fix_phase` on the oldest downloaded batch (ProcessPool workers
  unchanged, `cfg.workers`).
- **U (uploader thread)** — today's `_deliver_phase` + `finalize_rejected`
  + batch stats + Telegram message for the oldest validated batch.

Handoffs are two `queue.Queue`s of batch descriptors; batch numbering via
the existing `batches` table (`start_batch` when D starts it,
`finish_batch` when U completes it — the `summary_json` sessions list is
already written and becomes the resume grouping).

### 2. Files and functions touched

| File | Change |
|---|---|
| `pipeline/run.py` | The only structural change. `run()`'s batch loop (`run.py:467-497`) becomes `_overlapped_run()` (threads + queues + drain/exit logic + HOLD_VLM one-shot at the tail, logic moved from `run.py:470-476`); `process_batch` stays for the lockstep path and tests. Batch-stats assembly (`run.py:325-365`) moves into U's completion step, unchanged in content. Log prefixes `[dl bN]`/`[val bN]`/`[up bN]` + one per-batch stage-times line. `caffeinate -i` held from first activity to full drain (same R8 intent). |
| `pipeline/config.py` | `PIPELINE_OVERLAP = True` (False = byte-identical lockstep fallback), `MAX_BATCHES_IN_FLIGHT = 3`, `RCLONE_BWLIMIT_DOWN/UP` (start ~"6.5M"/"6.9M" ≈ 80% of measured; tuned day 1; empty string = off). |
| `pipeline/ledger.py` | Add `PRAGMA busy_timeout=10000` next to the WAL pragma (`ledger.py:65`); update the concurrency docstring (`ledger.py:11-13`) from "one writer process" to "one process, three writer threads, one connection per thread, short transactions". No schema change. |
| `pipeline/ingest.py` | `download()`: append `--bwlimit` from config to the rclone argv (`ingest.py:399`). |
| `pipeline/deliver.py` | `upload_and_verify()`: same one-line `--bwlimit` addition (`deliver.py:123`). |

Phase functions' *bodies* are untouched — that is the safety argument.

### 3. Concurrency & locking (SQLite)

- WAL is already on (`ledger.py:65`). WAL gives many readers + one writer
  at a time; `busy_timeout` makes concurrent writers queue briefly instead
  of raising `database is locked`.
- **One `Ledger` (= one sqlite3 connection) per thread**; sqlite3's
  default `check_same_thread=True` stays as a guard against sharing.
- Validator **subprocesses** still never touch the ledger (results return
  to V, which writes) — today's pattern, unchanged.
- No row is written by two threads: ownership follows the existing phase
  boundaries (D: DISCOVERED→DOWNLOADING→INGESTED/QUARANTINED; V:
  INGESTED→…→{READY, FIX_QUEUED…, REJECTED, SPLIT, HOLD_VLM}; U:
  READY→PACKAGED→UPLOADED→DELIVERED, + REJECTED finalization). The queue
  handoff, not the ledger, sequences the threads.

### 4. Ledger states, crash/resume

State machine unchanged, no new states. On startup (every launchd tick):
partition all `RESUMABLE` rows by state to the right stage — DOWNLOADING
→ D's queue; INGESTED/VALIDATING/FIX_QUEUED/FIXING/REVALIDATING → V;
READY/PACKAGED/UPLOADED → U — grouped into their original batches via
`batches.summary_json` where present, FIFO-regrouped otherwise. Everything
downstream already resumes idempotently (rclone re-copy, re-validate,
re-verify upload; hours recorded once at the DELIVERED transition —
`deliver.py:147-185`). A kill −9 at any instant therefore loses at most
in-flight work, never state — same guarantee as today, now asserted by
test (§6.3).

### 5. Throughput expectation & the workers knob

Script-computed (`BOTTLENECK_FINDINGS.md` inputs, transfers at 80% for the
bwlimit):

| Config | bound | fh/day @24/7 | @~20 h |
|---|---|---|---|
| overlap, 4 workers | validate | 154–183 | 128–153 |
| overlap, 6 workers | transfers | 183–217 | 152–181 |

Demand: ~111 delivered-h/day from Aug 16 ≈ **~133 processed-fh/day**
[assumption: 600/500 over-collection ratio holds]. **Flip WORKERS 4→6**
(env `HL_PIPELINE_WORKERS=6`, no code change, §7.5-sanctioned) when either
(a) the pace alarm fires with the overlap live, or (b) per-batch stage
times show validate as the binding stage for >6 consecutive batches while
transfers finish early.

### 6. Test list (all before the flag defaults on)

1. **Overlap proof** (unit, monkeypatched phases with sleeps): with 3 fake
   batches, batch 2's download completes before batch 1's upload finishes;
   `MAX_BATCHES_IN_FLIGHT` never exceeded; F7 low-disk pauses D while U
   keeps draining.
2. **SQLite contention**: 3 threads × 500 `set_state` calls on one ledger
   file; zero `database is locked` escapes; events table consistent.
3. **Forced mid-run kill** (the step-6 acceptance, rerun against the new
   driver): `kill -9` separately during (a) mid-download, (b)
   mid-validation, (c) mid-upload; restart; assert every session reaches a
   terminal state, no double-DELIVERED, delivered-hours sum counted once,
   no stub `session.rrd` ever staged.
4. **Lockstep regression**: `PIPELINE_OVERLAP=False` runs the existing
   `process_batch` path; existing `test_run.py` suite green unmodified.
5. **Report shape**: one batch → one Telegram message, byte-shaped per
   §14, sent at that batch's deliver completion.
6. Full suite: `PYTHONPATH=. uv run --with pytest pytest pipeline/tests
   translator/tests -q` green.
7. One real unattended cycle on live uploads before the default flips
   (same bar as build step 6).

### 7. Rollback & escalation

- Rollback: `PIPELINE_OVERLAP=False` — instant return to today's lockstep.
- If pace still lags with overlap + 6 workers: streaming
  (`STREAMING_EXPLAINER.md`, needs the full R5 amendment) or the VM
  (`GCP_OFFLOAD_EXPLAINER.md` §3d triggers) — each returns to Adnaan as
  its own go/no-go.
