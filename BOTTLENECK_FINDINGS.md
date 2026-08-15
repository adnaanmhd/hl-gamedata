# Bottleneck findings — why the pipeline can't hit pace as built

Written 2026-08-14 ~23:00 IST. All arithmetic script-computed
(`bottleneck_calc.py`, session scratchpad); inputs labeled with sources below.
(§1's line refs were verified at commit f8aa10c; `run.py`/`ingest.py` moved at f49bdd6 later
that night — current pins live in `PIPELINE_IMPLEMENTATION_PLAN.md` v2 §6. The findings and
numbers themselves are unaffected.)

## The finding, very simply (one minute)

The pipeline works like one person doing chores strictly one at a time:
first download a batch of 10 videos, then check them, then upload the good
ones — and only then start the next batch. While the computer is checking
videos, the internet connection does nothing (43% of every batch). While
files are downloading or uploading, the computer's brain does almost nothing
(57% of every batch).

Measured on today's real numbers, that one-at-a-time style can push through
at most **67–78 hours of footage per day even if the Mac never sleeps** —
and the mission needs **~100–111 hours per day** from tomorrow. So as
built, the pipeline **cannot hit the Aug 24 deadline even running 24/7**.
It is not slow because any one part is slow; it is slow because the parts
take turns. Letting downloading, checking, and uploading run at the same
time lifts the ceiling to ~170–206 h/day (then the checker becomes the
limit, and 6 workers instead of 4 lifts that too). The fix design is in
`THROUGHPUT_FIX_PLAN.md`.

## 1. The four serialization points — re-verified in code (2026-08-14 ~22:45 IST)

Line numbers re-checked against the working tree (they moved since the
kickoff's citations were written; same code, same behavior):

1. **Phases run strictly in sequence per batch** — `process_batch` calls
   `_download_phase` → `_validate_phase` → `_fix_phase` → `_deliver_phase`
   one after another (`pipeline/run.py:313-323`, calls at 318/319/320/322).
2. **Sessions download one at a time** — `_download_phase` is a plain
   `for sid in sids:` loop around `ingest.download` (`pipeline/run.py:97-110`).
3. **Within one session, rclone gets `--transfers 4`** across that session's
   ~4–5 files (`pipeline/ingest.py:399`) — but `video.mp4` is ~98% of the
   bytes (frames.csv measured at ~0.25% of video on local samples; the rest
   is the +2% sidecar assumption), so this is effectively a single stream
   for most of the download.
4. **Batches are serial** — the orchestrator loop finishes `process_batch`
   (including all uploads) before picking the next batch
   (`pipeline/run.py:467-497`). Nothing downloads batch N+1 while batch N
   validates; nothing validates while batch N−1 uploads.

Also noted: inside `_deliver_phase`, sessions are processed serially —
stage → final gate → rrd regen (20%) → upload → verify per session
(`pipeline/run.py:264-310`, `pipeline/deliver.py:147-185`), so even the
upload phase alternates CPU work and link work instead of overlapping them.

## 2. Measured inputs (sources)

| Input | Value | Source |
|---|---|---|
| Downlink | 59.9 / 66.6 Mbps | `networkQuality`, 08-14 20:45 & 22:42 IST (both-directions-loaded) |
| Uplink | 78.8 / 66.2 Mbps | same two runs |
| Loaded latency | 880 / 892 ms | same two runs — heavy bufferbloat, stable across both |
| Video bitrate | 2.6 GB per footage-hour | plan §2 (08-13 tool build) |
| Sidecar download overhead | +2% | **[assumption]** frames.csv ≈0.25% of video (measured); inputs.jsonl+metadata bounded by plan §15's "tens of MB" |
| Validation cost | **27.9 min per footage-hour, single worker, incl. VLM sweep** | §7.5 Day-0 benchmark (779 s footage in 362 s, project memory 08-14). Supersedes the rough "decode 3–6×" figure |
| Workers | 4 | `pipeline/config.py:103` |
| rrd upload share | 20% of sessions × 1.01× video size | R17; size ratio measured on 2 local sessions |
| Decode speed (context) | 1080p30 H.264 → 160×90 gray: 88× realtime wall (default threads), ~7.3× per core | measured 08-14 22:45 on the 348 s local OW sample, M5 Pro |
| Stage+final-gate+rrd-regen | 3–8 min/batch | **[assumption]** — not yet benchmarked; small vs the other terms |
| Ledger state | **no ledger.db yet → 0 h delivered** | `~/hl-pipeline/` absent, checked 22:41 IST |
| Drive I | game trees exist, **zero files**; 4 operator folders are **names, not emails** (will quarantine under Q5 strict parsing — separately flagged) | `rclone lsjson -R` 22:41 IST |
| Disk | 813 GB free | `df`, 22:41 IST |

Not knowable today: ISP data cap (Q7), single-stream vs multi-stream TCP
throughput on this line (Drive I has no files to test against —
**[assumption]** the networkQuality aggregate is achievable by rclone; if
single-stream tops out lower, downloads are slower than modeled).

## 3. Timeline of one synthetic batch — 10 × 20-min sessions (3.33 footage-h)

Bytes: download 8.84 GB, upload 10.42 GB (video + 20% rrd). Validate wall
= 27.9 min/fh ÷ 4 workers = 23.2 min **[assumption: near-linear scaling to
4 workers — the single-worker benchmark is dominated by VLM wait and short
ffmpeg bursts, and the benchmark's 130 s average sessions amortize fixed
per-session overhead worse than 20-min ones, so 23.2 min is if anything
conservative]**.

| Stage | best case (66.6↓/78.8↑) | worst case (59.9↓/66.2↑) | link during it | CPU during it |
|---|---|---|---|---|
| download | 17.7 min | 19.7 min | busy | ~idle (md5 only) |
| validate | 23.2 min | 23.2 min | **idle** | busy |
| stage+gate+rrd | 3.0 min | 8.0 min | **idle** | busy |
| upload | 17.6 min | 21.0 min | busy | ~idle (staging copies done) |
| **cycle** | **61.6 min** | **71.9 min** | 43% idle | ~57% idle |

Per footage-hour: 18.5–21.6 min of wall-clock.

## 4. Ceilings vs required pace

| Mode | fh/day @ 24/7 | fh/day @ ~20 h awake |
|---|---|---|
| **Lockstep as built** | **67–78** | **56–65** |
| Overlapped, 4 workers (validate becomes the bound: 418 s/fh) | 206 | 172 |
| Overlapped, 6 workers (transfer becomes the bound) | 229–271 | 191–226 |

Required pace, recomputed at 0 h delivered (ledger empty), deadline
2026-08-24 23:59 IST:

| Uploads start flowing | days left | needed h/day |
|---|---|---|
| Aug 15 00:00 IST | 10.00 | 100.0 |
| Aug 15 18:00 IST | 9.25 | 108.1 |
| Aug 16 00:00 IST | 9.00 | 111.1 |
| Aug 17 00:00 IST | 8.00 | 125.0 |

**Verdict: lockstep's 67–78 h/day @ 24/7 is below every row of the
required-pace table.** The kickoff's earlier 85–100 estimate was computed
before the §7.5 benchmark existed; the real validation cost lowers the
ceiling further. Overlapping stages clears the bar with ~1.5–2× margin at
4 workers (206 vs ~108) — margin that absorbs Mac sleep, fix loops,
HOLD_VLM retries, and reject re-records, none of which the synthetic batch
includes.

Context for Task 3 and the data-cap risk: the full program moves ≈ **6.3 TB**
through this line (collect 1200 fh × 2.65 GB down ≈ 3.2 TB; deliver
1000 fh × 3.13 GB up ≈ 3.1 TB); at the overlapped ceiling that is
~0.5–0.65 TB/day sustained both directions.

## 5. What idle actually costs (per lockstep batch, best case)

- Internet sits idle 26.2 of 61.6 min — while validate+stage runs, ~13 GB
  could have downloaded at 66.6 Mbps (more than a whole next batch).
- CPU sits ~idle 35.3 of 61.6 min — while transfers run, ~5.1 fh could have
  validated at the 4-worker rate (1.5 batches' worth).
- Neither resource is the problem alone: **at the measured speeds, download,
  validate, and upload are each 17–23 min per batch — almost perfectly
  balanced.** Taking turns triples the cycle; running them concurrently
  costs the slowest one only.
