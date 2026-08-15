# Streaming ("stream, don't batch") — exploration only

Written 2026-08-14 ~23:10 IST. **No production code was changed for this
document.** Numbers come from `BOTTLENECK_FINDINGS.md` (script-computed).

## 1. What it is — very simply

Today the pipeline is one cook doing everything in strict order: shop for
ten ingredients, then cook all ten, then deliver all ten meals — and only
then shop again. Streaming turns it into a small kitchen where three
things run at the same time, forever:

- a **shopper** (downloader) keeps the pantry stocked — never more than
  ~30 sessions on disk;
- a **cook** (the 4 validation workers) takes whatever is in the pantry
  and checks/fixes it;
- a **delivery scooter** (uploader) takes every finished session out the
  moment it's ready.

Nobody waits for anybody. The internet is busy while the CPU checks
videos, and the CPU is busy while files move. A day's output stops being
"sum of everyone's turns" and becomes "whatever the slowest worker can do
alone" — on today's numbers that is ~2.7× more footage per day. "Batches"
stop being how work moves and stay only as how progress is *reported* (a
Telegram message every 10 finished sessions, exactly the same shape).

## 2. Pros and cons — honest

**Pros**

- Ceiling rises from 67–78 to ~206 footage-h/day @ 24/7 (172 @ ~20 h
  awake) with 4 workers; 229–271 with 6 (`BOTTLENECK_FINDINGS.md` §4) —
  ~2.7–3.1× on identical hardware, no new accounts, no new machines.
- The slowest stage becomes visible in the ledger within hours (whichever
  queue grows), so tuning (workers 4→6, `--bwlimit`) has a live gauge.
- Storage discipline survives: the downloader stops at a cap (~30 sessions
  ≈ 27–40 GB even at 30-min sessions, plus staged copies — well under the
  100 GB F7 low-water), and the F7 disk guard stays as the hard floor.
- Crash story stays state-driven, same as today: every session is in some
  ledger state, every loop resumes from states alone.

**Cons — each one real**

- **`run.py` driver complexity.** The orchestrator goes from ~40 lines of
  sequential calls to three long-lived loops (threads) plus a coordinator
  that knows when to stop (queues empty + Drive scan clean). This is the
  most invasive change the pipeline has had since build, days before
  go-live. The phase *functions* don't change, but the thing that calls
  them does, and its bugs are concurrency bugs — the annoying kind.
- **Three loops, one SQLite ledger.** WAL is already on
  (`pipeline/ledger.py:65`) and allows many readers + one writer at a
  time, but today's code is built on a documented one-writer assumption
  (`ledger.py:11-13`). Three threads writing means: one `Ledger`
  connection per thread (sqlite3 connections are not thread-safe to
  share), `busy_timeout` so writers queue instead of erroring, and a rule
  that validator *subprocesses* still never touch the ledger (parent
  thread writes results — unchanged). Wrong locking here shows up as
  `database is locked` crashes at 2 a.m.
- **Logs and reports get harder to read.** Interleaved lines from three
  loops instead of a clean download→validate→deliver story. The per-batch
  Telegram message no longer maps to "the 10 sessions we just downloaded";
  it becomes "the last 10 sessions that finished". Same format
  (`reports.build_batch_message` untouched), different meaning — worth one
  explicit line in the message ("rolling window") so future-you isn't
  confused.
- **VLM calls now run while the pipe is saturated.** The line shows
  880–892 ms loaded latency (bufferbloat, both `networkQuality` runs).
  Today VLM traffic mostly runs when transfers are quiet; streamed, every
  Gemini call fights rclone for the uplink. 180 s timeouts + §13 retries
  absorb it, but validation slows and HOLD_VLM alerts get likelier.
  **Mitigation:** `rclone --bwlimit` at ~80% of measured (e.g. `50M:52M`
  down:up in rclone's MB-notation equivalent — exact value tuned on day 1)
  to keep latency headroom; costs ~20% of the transfer ceiling, which the
  margin covers.
- **VLM daily volume scales with throughput.** Concurrency stays ≤4
  sessions (worker count unchanged) but the key now works near-24/7:
  ~2.7× more requests/day at the same burst rate. Billing-tier question
  (already open with Adnaan) matters ~2.7× more; Task 4's Vertex failover
  helps availability, not quota.
- **Go-live risk.** This close to the deadline, a driver rewrite can cost
  a day of debugging exactly when a day costs ~110 delivered hours. The
  forced-kill resume test exists to catch that, but risk is not zero.

## 3. Feasibility detail

**What changes — the driver only.**

- `pipeline/run.py`: replace the `for _ in range(max_batches)` loop
  (`run.py:467-497`) with three loops + a small coordinator:
  - **Downloader loop**: while Drive scan finds `DISCOVERED` and
    (sessions on disk in states DOWNLOADING/INGESTED/VALIDATING/FIX_*/
    READY) < CAP (~30) and disk ≥ F7 floor → `ingest.download(next)`
    (FIFO + lagging-game priority via `ingest.next_batch(size=1)`).
  - **Validator loop**: feed every `INGESTED`/`REVALIDATING` session to
    the existing `ProcessPoolExecutor` (persistent pool instead of
    per-phase); apply results via the existing post-validate logic; run
    `_fix_phase` logic per-session as sessions need it.
  - **Uploader loop**: `deliver.deliver_session` on every `READY`
    (serial — one upload at a time saturates the uplink already;
    per-session stage+rrd CPU cost now overlaps other sessions' work
    for free).
  - Coordinator: rescan Drive every ~10 min; report every 10th terminal
    outcome (DELIVERED/REJECTED) via the existing `BatchStats`; exit when
    queues are empty and the scan adds nothing; `caffeinate -i` held for
    the whole active period instead of per batch (same R8 intent:
    caffeinate only while working).
- `pipeline/ledger.py`: no schema change; add `PRAGMA busy_timeout` and
  "one connection per thread" usage. (`batches` table keeps numbering the
  reporting windows.)

**What doesn't change:** the four phase functions' internals
(`ingest.download`, `validate_session`, `fix.apply_fixes`/`plan_fixes`,
`deliver.deliver_session`), ledger schema, reason codes, launchd plist +
30-min tick, run-lock (still exactly one pipeline process), F7 disk guard,
R6 (Drive I read-only), R17 rrd sampling, report formats, daily 14:00
report, HOLD_VLM semantics (retry cadence moves from "once per run" to
"once per rescan cycle" — same bound per §13).

**Migration steps** (order): (1) land driver behind a config flag
`STREAMING=true/false` defaulting false, lockstep path kept intact;
(2) fixture-run both modes over the test suite; (3) one real cycle in
lockstep (go-live unblocked, today's plan); (4) flip the flag during a
quiet window, watch one full day; (5) delete the flag after the deadline,
not before.

**Test plan**

1. All existing `pipeline/tests` green untouched (phase functions
   unchanged is *proven* by not editing those files).
2. Driver unit test with monkeypatched phases: 25 fake sessions,
   assert download of session N+11 starts before session N's upload
   finishes (the whole point), cap never exceeded, F7 pause honored.
3. SQLite contention test: 3 threads hammering `set_state` on one ledger
   file for 30 s — zero `database is locked` escapes.
4. **Forced mid-run kill**: `kill -9` the driver while (a) a download is
   mid-flight, (b) a validation subprocess is running, (c) an upload is
   mid-flight; restart; assert every session reaches a terminal state,
   no double-DELIVERED, no double-counted hours (same assertions as
   today's step-6 acceptance, run against the new driver).
5. Reporting window test: 10 terminal outcomes → exactly one Telegram
   message, byte-shaped like §14.

**Expected ceiling** (from `BOTTLENECK_FINDINGS.md` §4): validate-bound
206 fh/day @ 24/7 (172 @ 20 h) at 4 workers; raising to 6 workers makes it
transfer-bound at 229–271 (191–226). Both clear the required 100–111 h/day
with margin; lockstep (67–78) does not.

**R5 amendment needed: YES.** R5's *storage-discipline intent* survives
(bounded local footprint via cap + F7; delete-after-verified-upload
unchanged), but its letter — "≤10 sessions at a time → validate → fix →
report → … → next batch" — describes exactly the lockstep this replaces.
Streaming must not ship without Adnaan amending R5 (proposed wording in
`THROUGHPUT_FIX_PLAN.md`).
