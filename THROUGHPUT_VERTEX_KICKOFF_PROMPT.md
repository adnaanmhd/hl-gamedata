# Kickoff — pipeline throughput exploration + Vertex VLM failover

You are working in `/Users/adnaan/Documents/hl-gamedata`. The mission: 1000 delivered
gameplay hours (500 Kamla + 500 Outer Wilds) by **2026-08-24 23:59 IST**. The plan of
record is `PIPELINE_IMPLEMENTATION_PLAN.md` — read §5, §6, §9, §13, §15, §18 before
anything else. Pipeline build steps 0–6 are committed and tested; go-live is imminent.

You have four tasks. **Tasks 1–3 produce analysis and designs — no production code
changes. Task 4 is the only production change.** Suggested working order:
Task 1A (quantify) → Task 2 → Task 3 → Task 1B (recommend + spec) → Task 4.
Report once at the end.

## Inherited findings (verified 2026-08-14 late evening)

Re-verify every file:line below before relying on it — code may have moved since.

**Lockstep serialization (Task 1's subject):**
- `process_batch` runs the four phases strictly in sequence — `_download_phase` →
  `_validate_phase` → `_fix_phase` → `_deliver_phase` (`pipeline/run.py:267-271`).
- Inside the download phase, sessions download **one at a time** in a loop
  (`run.py:86-99`); the only parallelism is rclone's `--transfers 4` across the ~5
  files of one session (`pipeline/ingest.py:369`), which barely helps since
  `video.mp4` is one big file.
- Batches are serial too: the orchestrator finishes batch N completely before
  starting batch N+1 (`run.py:409-426`). Nothing downloads batch N+1 while batch N
  validates. Net effect: the internet sits idle while the CPU validates, and the CPU
  sits idle while the internet transfers.

**Measured environment (2026-08-14 ~20:45 IST, single sample — remeasure if it matters):**
- `networkQuality` (both directions loaded simultaneously): **down 59.9 Mbps, up
  78.8 Mbps**, loaded latency 880 ms (heavy bufferbloat). ISP data cap unknown.
- Drive I (collection) was **completely empty** at ~21:00 IST 08-14 (game folders
  exist, zero files). Recompute pace requirements from the real current date and the
  ledger's delivered hours when you run.
- Derived ceiling [assumptions: 20-min avg sessions, 2.6 GB/h video, decode 3–6×
  realtime single-thread (rough 08-14 number — use the §7.5 Day-0 benchmark result
  instead if it exists by now)]: lockstep ≈ **85–100 footage-h/day at 24/7** vs a
  required ~109+/day from Aug 15 evening; overlapping the stages raises the ceiling
  to transfer-bound ~180–240 h/day.

**VLM context (relevant to Task 4):**
- Model pinned `gemini-3.7-flash` (R13 — do not substitute). Real demand: both sweep
  layers batch 8 frames/request (`tools/analyze_sample.py:82`, `pipeline/vlm.py`),
  so ~12–35 requests/session, ~5–14k/day at peak, bursts ~25–70 RPM.
- `pipeline/vlm.py` has per-call 429/5xx retry (Retry-After + backoff) but **no
  fleet-wide throttle and no result cache** — both are separate tasks, OUT of your
  scope.
- The Gemini key's billing tier is unknown; Adnaan is checking it in AI Studio.
- Adnaan ruled 08-14: **VLM game identification is not required in Phase 1** — the
  wrong-game vote gate is slated for disable in a separate task. Don't build
  anything on it; treat `CNT_WRONG_GAME`-via-VLM-votes as outgoing when you read
  `validate.py`.
- A local VLM (Ollama/qwen2.5vl) was benchmarked on 08-14 and **removed** —
  thermally infeasible on this Mac at peak. Do not reinstall or re-propose it.

## Ground rules

- The machine-wide CLAUDE.md rules apply in full: verify before claiming, mark
  `[assumption]`/`[web]`, never hand-transcribe numbers, one question at a time and
  only when truly blocked.
- **Drive I is read-only forever (R6).** No uploads to Drive II. Do not create any
  GCP resource. Do not load or modify the launchd agent. Do not touch the vault.
- R5 (lockstep batch flow) and R8 are Adnaan's locked rulings. Tasks 1–3 end in
  designs and recommendations for his go/no-go — never in changed pipeline code.
- Every explainer/plan document you write MUST open with a plain-language section
  Adnaan can read in one minute: what it is, in simple words, then pros and cons.
- Commit nothing except Task 4's change, as specified there. Never push.

## Task 1 — Quantify the idle problem, then design the fix

**Part A — quantify (first).**
1. Re-verify the four serialization points in the code (citations above).
2. Build the timeline for a synthetic batch of 10 × 20-min sessions: download,
   validate, fix (assume none), upload wall-time, from the measured line speeds,
   2.6 GB/h video, the decode rate, and `cfg.workers`. Show link-idle % and
   CPU-idle % per batch.
3. Deliverable: `BOTTLENECK_FINDINGS.md` — the timeline table, resulting h/day
   ceiling at 24/7 and at ~20 h/day awake, against the required pace recomputed for
   the actual current date. Number-first, sources labeled.

**Part B — the implementation approach (after Tasks 2 and 3, so it's informed).**
Adnaan's requirement, verbatim in spirit: **the internet must not sit idle while
the CPU validates, and the CPU must not sit idle while the internet transfers.**
Design the change that achieves this. Consider the whole spectrum, not just one
option:
- minimal overlap — keep batches, but prefetch batch N+1's downloads and run batch
  N−1's uploads concurrently with batch N's validation;
- full streaming — Task 2's three-loop design;
- offload — Task 3's VM (changes *where*, not *whether*, the overlap matters);
- or a staged combination (e.g., minimal overlap for go-live, streaming after).
Recommend exactly one path. Spec it to implementation-ready detail: files and
functions touched, ledger states used, concurrency and locking (note SQLite —
single-writer; consider WAL), the disk-cap guard, crash/resume story, log/report
legibility, and the test list including a forced mid-run kill. **Do not write the
code** — R5 is Adnaan's locked ruling, so the spec ends with an explicit go/no-go
ask. Deliverable: `THROUGHPUT_FIX_PLAN.md`, which MUST open with a section titled
**"The fix, very simply"** written for Adnaan in plain words a ten-year-old could
follow: what the chosen fix is, how it keeps the internet and the CPU busy at the
same time (a concrete everyday analogy helps), what visibly changes in day-to-day
operation (reports, logs, disk use), and its pros and cons — all before any
technical detail. The go/no-go ask sits at the end of that plain section, so
Adnaan can rule from it alone without reading the spec below.

## Task 2 — Explore "stream, don't batch" (explore ONLY — no implementation)

The concept to explore: replace the lockstep with **three concurrent loops driven
by ledger state** (states are already per-session): a downloader keeps `work/`
topped up to a cap (~30 sessions ≈ well under 100 GB, so R5's storage-discipline
intent and the F7 disk guard survive); a validator pool consumes whatever is
`INGESTED`; an uploader drains `READY`. Batches remain purely as reporting windows.
Cycle time becomes the slowest stage instead of the sum of stages (~2–2.5×
throughput on identical hardware; ceiling ~90 → ~180–240 h/day).

Deliverable `STREAMING_EXPLAINER.md`, in this order:
1. **What it is — very simply.** A few sentences a ten-year-old follows (e.g., a
   kitchen where the dishwasher, the stove, and the delivery scooter all run at the
   same time instead of taking turns).
2. **Pros and cons — honest.** Include at least: driver complexity in `run.py`,
   three loops sharing one SQLite ledger (locking/WAL), harder-to-read logs and
   batch reports, VLM calls now concurrent with saturated transfers under 880 ms
   bufferbloat (mitigation: `rclone --bwlimit`), crash/resume behavior, and what
   go-live risk it adds this close to the deadline.
3. **Feasibility detail.** What changes (the driver only), what doesn't (phase
   functions, ledger schema, launchd, run-lock), migration steps, test plan
   including forced-kill resume, expected ceiling math, and the explicit
   **"R5 amendment needed: yes/no"** line.
Do not modify any production code in this task.

## Task 3 — Explore the GCP VM offload (explore ONLY — no implementation)

The concept to explore: a small GCP virtual machine — a rented computer inside
Google's datacenter — does download/decode/upload against Drive at datacenter
speed, and the Mac just orchestrates and reports (or drops out of the loop
entirely). It would remove the home line, Mac sleep, bufferbloat, and thermals from
the risk list. A "tens of dollars for 10 days" figure was floated earlier; treat it
as unverified optimism to check, not a fact.

Deliverable `GCP_OFFLOAD_EXPLAINER.md`, in this order:
1. **What it is — very simply.** E.g., renting a computer that lives in the same
   building as Google Drive, so copying files is like moving them across a hallway
   instead of across the city; our Mac keeps the clipboard and the phone.
2. **Pros and cons — honest.** Include at least: migration risk mid-crunch, a
   second machine to operate and secure (SA key + Gemini key on the VM), what the
   Mac still does, cost, and what happens if the VM dies mid-run.
3. **Verified detail**, in priority order, all [web] claims cited:
   a. **The make-or-break question first:** is GCE↔Google-Drive-API traffic billed
      as internet egress or free Google-internal traffic? ~6+ TB would flow
      through the VM; at internet egress rates the idea dies. Current pricing
      docs.
   b. VM sizing for full-video decode at 100–130 footage-h/day using Task 1A's
      decode numbers (cloud vCPUs are not M5 P-cores — be conservative);
      on-demand vs spot; 10-day total cost range.
   c. Architecture sketch: what runs where (full pipeline incl. ledger on VM, vs
      split with the Mac reporting), service-account key reuse, secrets handling,
      ffmpeg/uv setup, VLM calls from the VM (fine — Gemini quota follows the
      key/project, not the caller's location, and Task 4's Vertex lane works from
      anywhere).
   d. Recommendation with a concrete adoption trigger ("adopt if run-1 measured
      throughput < X h/day").
**Create no GCP resources.**

## Task 4 — Implement the Vertex failover in `pipeline/vlm.py` (production change)

The engine already does this: `tools/analyze_sample.py` `Gemini` class (~lines
387–440) tries the `generativelanguage` endpoint first, falls back to the **Vertex
AI express-mode** endpoint with the same API key, and sticks with whichever answers.
`pipeline/vlm.py` is single-endpoint today. Port the engine's semantics:

- Copy the exact `VERTEX_BASE` constant from the engine — do not invent a URL.
- Read the engine's loop first and mirror **when** it switches endpoints; the
  behavior to land on: run the existing §13 retry ladder against the primary; on
  final failure (429-exhaustion, 5xx-exhaustion, 403/404), run the ladder once
  against the secondary before raising `VLMError`; remember the working endpoint
  for the process lifetime (module-level, like the engine's `_which`).
- Same key, no new secrets, no new dependencies. `HOLD_VLM` semantics upstream are
  unchanged (both endpoints failing still ends in `VLMError`).
- Tests in `pipeline/tests/` (monkeypatch the POST layer): primary exhausts →
  secondary succeeds → result returned and endpoint stickiness persists; both fail
  → `VLMError`; primary healthy → secondary never called.
- **Live smoke test** with the real key from `~/.config/hl-gamedata/secrets.env`:
  one tiny text-only `generateContent` per endpoint ("reply with []"). If Vertex
  answers 403 "API not enabled" or 404 on the model name — STOP and report exactly
  what Adnaan must click (likely: enable the Vertex AI API on the key's project) or
  whether the model is named differently on Vertex. Do not work around it.
- Full suite green:
  `PYTHONPATH=. uv run --with pytest pytest pipeline/tests translator/tests -q`
- Then commit exactly this change (do not push):
  `pipeline/vlm.py: genlang→vertex express failover (mirrors engine) + tests`.

## Report back (one message at the end)

Per task: verdict first, numbers second, prose plain enough for a ten-year-old.
List the files you wrote. Close with open questions for Adnaan, grouped and
priority-ordered. If any task is blocked, say by what and continue with the rest.
