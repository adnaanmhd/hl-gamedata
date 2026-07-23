# HumynCapture — v2-Compliance & Sync-Fix Engineering Handoff

**Audience.** The engineer taking over `HumynCapture.exe` (the Windows gameplay
capture tool). This document is self-contained: it defines the end state, the
defects to fix in priority order, the exact output contract, and the acceptance
tests that decide "done."

**Date / owner context.** Prepared 2026-07-23 by HumynLabs after Odyssey's
(client) data-quality report on the 07-17-2026 delivery
(`latest_requirements/protege-dq-findings-2026-07-17 (2).pdf`) flagged all
three delivered sessions for controls-to-video sync failure. The immediate
batch was rescued in post; **the tool itself is what must be fixed.**

---

## 1. The deliverable, in one paragraph

Rework HumynCapture so that a contributor records a session and the tool
**natively emits a spec-v2 delivery folder** (no external translator step):
`session.json` + `frames.csv` + `video.mp4` + `session.rrd` + `rrd_creation.py`,
laid out per §3 below, with inputs and video on a **single, correctly anchored
timebase**. A recording made by the fixed tool must pass:

1. **Odyssey's sync check** — `latest_requirements/action_video_grounding.py`
   reports `PASS` with **|lag| ≤ 50 ms** (their *target*, not just the 150 ms
   hard bound), with **no post-hoc lag correction applied**;
2. **The spec-v2 contract** — every check in spec §1.5 (mirrored by our
   reference validator `translator qa-v2`) passes;
3. **The capture self-check** (§6) — the tool refuses to mark a session
   "ready for upload" when any required modality or quality gate fails.

---

## 2. Step 0 — obtain the source

HumynCapture is a **PyInstaller bundle of a Python 3.12 + PySide6 app**. First
try to obtain the original source from whoever built the tool. If that fails,
reverse-engineer the shipped binary — this has already been done once and is
reproducible:

- Extract with `pyinstxtractor` (or equivalent) → the app modules live under
  `PYZ.pyz_extracted/app/`. Decompile the `.pyc` files (Python 3.12:
  `pycdc`/`decompyle`-class tools; some functions come back with
  `# WARNING: Decompyle incomplete` — cross-reference module docstrings and
  observed on-disk output when bytecode is unclear).
- The module map, with each defect's suspected home, is in
  `HumynCapture_Capture_Tool_Issues.md` (appendix). Key modules:
  `app/core/session_engine.pyc` (orchestrator — most fixes land here),
  `ffmpeg_recorder.pyc`, `raw_mouse.pyc`, `keyboard_capture.pyc`,
  `focus_tracker.pyc`, `ffprobe.pyc`.
- Companion architecture analysis: `HumynCapture_Implementation_and_Spec_Diff.md`.

**Validate every fix against a fresh real capture, not just decompiled source.**

---

## 3. The critical defect: input↔video clock anchor (P0)

### What the client measured

Odyssey cross-correlates on-screen motion (dense optical flow) against the
recorded per-frame mouse deltas. On the 07-17 delivery, all three sessions
failed their ≤150 ms tolerance — video behind inputs by **151.5 ms, 207.7 ms,
and 2326.9 ms**. The lag is **constant within a session** (verified by the
client on independent sub-clips, and by us): a clock/anchor offset, not drift.

### Root cause (issue A2 in the issues doc)

Input event `t` is recorded as *µs since `perf_counter()` at the moment ffmpeg
is confirmed running* (`session_engine.SessionEngine.run`). The video's t=0 is
the **first encoded frame's presentation time**. Those are different instants;
the startup race between them becomes a per-session constant offset between the
input stream and the video. Nothing records the offset, so downstream cannot
know it.

### Required fix

Put inputs and video on **one timebase, anchored at the first video frame**:

- Keep stamping input events with a monotonic clock (QPC /
  `perf_counter_ns`) — that part is fine.
- Capture the **wall/QPC time of the first encoded frame** and re-anchor all
  event timestamps to it at finalize. Implementation options (pick one, in
  order of robustness):
  1. Parse ffmpeg `-progress` output (or stderr) for the first
    `frame=1`/`out_time` report and pair it with a QPC timestamp taken at
    read-time; or
  2. run ffmpeg with `-use_wallclock_as_timestamps 1` on the grab input so
    packet PTS are wallclock-derived, and record the QPC↔wallclock pairing at
    start; or
  3. after recording, `ffprobe` the muxed file's first-frame PTS and combine
    with a start-of-capture QPC/wallclock pair logged by the recorder.
- Record the chosen anchor **explicitly in the session metadata** (e.g.
  `capture_health.time_anchor = "first_frame_pts"` plus the raw pairing), so
  any residual issue is diagnosable downstream.

### Why ≤50 ms native is achievable

After correct anchoring, the residual measured lag is only the genuine
input→photon latency (game render + capture pipeline), which our measurements
show is ~1 frame: a correctly-attributed session of the same games measures
within ±40 ms on the client's script. The 50 ms target therefore leaves
headroom; if a fixed build measures consistently ≥ 2 frames, treat that as a
capture-pipeline latency bug (see A1's capture-path change), not as noise.

### Acceptance for this fix

```
uv run --with numpy --with opencv-python-headless \
    python latest_requirements/action_video_grounding.py <session_dir>
```
must print `PASS` with `lag_ms` in [−50, +50] on **every** test recording
(per game, per machine class), *without* any post-processing lag correction.
Also run the deterministic marker test in §7.

**Note on the correlation sign:** the script reports a **negative** correlation
for a *correct* first-person capture — scene optical flow moves opposite to
camera rotation (script's own `--explain-sign` documents this). |correlation|
is the health signal (gate: ≥ 0.15). Do not "fix" the sign.

---

## 4. Native v2 output contract

The tool must write, per session, the exact delivery format of
`latest_requirements/v2_Game_Data_Capture_Spec.pdf` (spec v2, 07/08/2026).
Reference implementation of both writer and validator:
`translator/v2.py` (`translate_bundle_v2`, `check_session_v2`). Highlights the
engineer must honor:

### 4.1 Layout (spec §1.1.1–1.1.2)

```
<vendor>/<mm-dd-yyyy of upload, UTC>/<game>/<session-id>/
  session.json
  frames.csv
  video.mp4
  session.rrd          # rerun archive regenerated from frames.csv
  rrd_creation.py      # the script that generated it
```
No `key_binding.json` — **removed in v2** (spec changelog); key semantics live
in per-frame `input_actions`.

### 4.2 `session.json` (spec §1.1.2.1; validated by §1.5.1)

Flat object, exactly these 16 fields — no extra nesting, no vendor extras:

| field | requirement |
|---|---|
| `vendor_name` | `"humynlabs"` |
| `game_title` | canonical title (see D1 fix — never free-text) |
| `session_id` | the capture session id (also the folder name) |
| `created_at_utc`, `ended_at_utc` | timezone-aware ISO-8601; `ended > created`; both refer to the **delivered clip**, not the raw recording |
| `duration_ms`, `duration_seconds` | consistent with each other (±1 s) and with `ended−created` |
| `fps` | **real average** = frame_count / duration — never nominal 30 (issue A3) |
| `frame_count` | must equal both the video's decoded frame count and the CSV row count; ≈ fps×duration ±2 |
| `record_width_px`, `record_height_px` | actual encoded dimensions |
| `screen_width_px`, `screen_height_px` | capture screen dimensions |
| `localization` | BCP-47 (`en-US`, `en-IN`, …) |
| `platform` | enum: `PC`, `Xbox`, `Switch`, `PlayStation`, `Mobile-iOS`, `Mobile-Android`, `Steam Deck` |
| `input_mouse_convention` | `{maps_to, dx_positive, dx_negative, dy_positive, dy_negative}`; for our FPS games: `camera_look_velocity`, dx `right/left`, dy `down/up` (Windows raw-input standard, no invert) |

### 4.3 `frames.csv` (spec §1.1.2.2–1.1.2.5, §1.1.5.6; validated by §1.5.2–1.5.5)

- **36 columns**, exact header order — see `V2_FRAME_COLS` in `translator/v2.py`:
  `frame_id`, `timestamp_ms`, 16 `c2w_m##`, `camera_model`, `camera_fx/fy/cx/cy`,
  `camera_radial_k1..k6`, `camera_tangential_p1/p2`,
  `input_keys`, `input_actions`, `input_mouse_buttons`, `input_mouse_dx`, `input_mouse_dy`.
- **Camera/C2W columns stay null** (empty) — input-only capture, out of scope.
- `frame_id` zero-based sequential; **one row per real video frame** — row
  count must equal the video's frame count exactly.
- `timestamp_ms` = the frame's **real presentation timestamp** from the encoded
  video (PTS), strictly increasing. Not `frame_id × 33.3` — the encoder drops
  frames (issue A1) and a uniform grid desyncs by seconds.
- **Event binning:** an input event at time `t` (on the §3 unified timebase)
  belongs to the frame whose PTS window contains it:
  `frame = bisect_right(pts, t) − 1`. Held keys appear in every frame they span.
  Reference: `translator/binner.py` (`bin_session`).
- `input_keys`: pipe-delimited, v2 display names — single letters upper-case
  (`W`), named keys capitalized (`Space`, `Esc`, `Tab`, `Enter`), sided
  modifiers `LShift/RShift/LCtrl/RCtrl/LAlt/RAlt` (generic `Shift/Ctrl/Alt`
  only when the side is truly unknown). Strip OS/system keys, control bytes,
  raw `vk_###` (issues B4/B5).
- **Every `input_keys` token must yield a non-null `input_actions` value** on
  its row (spec rule) → keys with no binding for the game are stripped from
  `input_keys`. Action names come from the per-game keybind
  (`translator/keybinds.py` is the current registry; ship the binding data with
  the tool per game).
- `input_mouse_buttons`: `Left`, `Right`, `Middle`, `X1`, `X2`.
- `input_mouse_dx/dy`: per-frame **sum** of raw mouse deltas, formatted as
  floats with **`"0.0"` sentinel** for no movement (never null/empty when the
  mouse modality is captured). Axis convention must match the declared
  `input_mouse_convention` (Windows raw input: +x right, +y down).

### 4.4 `session.rrd` + `rrd_creation.py` (spec §1.4)

Regenerate with rerun from the final `frames.csv` + `video.mp4`; ship the
generating script alongside. Reference: `translator/rrd.py`.

---

## 5. Full defect list, prioritized

Complete root-cause briefs (symptom → evidence → suspected code location →
fix → verification) are in **`HumynCapture_Capture_Tool_Issues.md`** — read it
in full. Priorities for this engagement:

### P0 — blocks v2 compliance / sync validation / usable data

| id | defect | fix summary |
|---|---|---|
| A2 | Input/video clock anchor offset (**the client-reported lag**) | §3 above — single timebase anchored to first-frame PTS |
| B1 | Raw mouse motion silently absent (~half of all sessions!) | check `RawMouseCapture.last_error` after start; fall back to `ts_monotonic_ns` in `_merge_inputs` instead of dropping; liveness check; retry registration |
| B2 / E1 / E2 | No modality detection; threads swallow errors; no end-of-session gate | subsystem health poll; end-of-session self-check (§6) gating `ready_for_upload` |
| D1 | Free-text `game_title` (`Outerworld`, `Outer wild`, …) | dropdown of known titles + canonical slug derived from exe name; store both |
| — | Native v2 emission (new work) | finalize step (§6) writing the §4 contract |

### P1 — client-visible quality

| id | defect | fix summary |
|---|---|---|
| A1 | 12–20% frames dropped (clustered) | replace `gdigrab`+software x264 with `ddagrab`/Windows Graphics Capture + HW encoder (`h264_nvenc`/`qsv`/`amf`); raise process priority; surface ffmpeg's `drop=` counter |
| A3 / D2 | Metadata reports nominal 30 fps; no health fields | report `fps` as real average; add `frames_dropped`, modality booleans, keyboard layout |
| C1 | No audio track | add WASAPI loopback input, mux AAC; **confirm with Odyssey whether audio is required before building** — it reintroduces A/V sync work |
| C2 | Exclusive-fullscreen records black; region over-capture | window/composited capture (same path change as A1) + black-frame heuristic warning |

### P2 — input hygiene & robustness

| id | defect | fix summary |
|---|---|---|
| B3 | `shift` vs `shift_l` inconsistency | resolve side from scancode/extended-key bit; emit sided tokens deterministically |
| B4 | OS-key / `vk_###` pollution | vk→name table; drop or tag system keys at capture |
| B5 | Ctrl+letter emits control bytes | map U+0001–U+001A back to the base letter |
| B6 | 5 Hz focus polling; held keys stick/clear | `SetWinEventHook`; synthesize `up` events on focus loss |
| B7 | L+R modifier bleed | de-bounce modifier pairs |
| C3 | 5 s kill truncates MP4 (`moov` lost) | scale finalize timeout; fragmented MP4 (`+frag_keyframe+empty_moov`); remux-repair on kill |

---

## 6. The finalize step (new capability)

After the contributor stops recording, the tool runs a **finalize pass** before
marking the session ready:

1. **Trim** — lossless (stream-copy) removal of ~5 s head and tail; the head
   cut snaps to the first keyframe ≥ 5 s (a stream copy must start on a
   keyframe). This removes the app-toggling/menu edges the client has
   complained about. Reference: `translator/trim.py`.
2. **Re-anchor & bin** — rebase event timestamps to the trimmed clip (using
   the *actual* cut point) on the §3 unified timebase; read real per-frame PTS
   from the trimmed file; bin events per §4.3.
3. **Write the v2 files** (§4) and regenerate the rrd.
4. **Self-check** (gates `ready_for_upload`; every failure names itself in the
   UI and `humyncapture.log`):
   - session.json↔frames.csv↔video mutually consistent (counts, durations);
   - all expected input modalities present (>0 keyboard events, >0 mouse
     motion, buttons as expected) — else **not ready**, tell the contributor
     to re-record *now* while they still can;
   - frames dropped under threshold; video decodable end-to-end;
   - no `vk_###`/control-byte tokens; every `input_keys` token has an action;
   - **sync self-test**: run the optical-flow lag measurement on the first
     ~2 min (reference implementation: `translator/sync.py`, numbers-identical
     to the client's script) and require |lag| ≤ 50 ms.

---

## 7. Validation & acceptance protocol

### Reference tooling in this repo

| file | role |
|---|---|
| `latest_requirements/action_video_grounding.py` | **Client's own check — the canonical acceptance test.** Same algorithm/thresholds as their QA pipeline |
| `translator/` package | Reference implementation of trim/binning/v2 writing + `qa-v2` validator (`PYTHONPATH=. uv run --with numpy --with opencv-python-headless python -m translator qa-v2 <session-dirs>`) |
| `translator/sync.py` | Vendored, verified-identical implementation of the client's lag measurement (for the in-tool self-test) |
| `latest_requirements/v2_Game_Data_Capture_Spec.pdf` | The contract |
| `latest_requirements/protege-dq-findings-2026-07-17 (2).pdf` | The client report that triggered this work (tolerances table on p.3) |
| `HumynCapture_Capture_Tool_Issues.md` | Full root-cause briefs for every defect in §5 |
| `HumynCapture_Implementation_and_Spec_Diff.md` | Architecture analysis of the decompiled tool |

### Acceptance matrix (all must pass before rollout)

1. **Sync (deterministic marker):** scripted run that fires a key/mouse input at
   a visually identifiable on-screen event; the event must land on the matching
   frame (±1 frame) in `frames.csv`.
2. **Sync (statistical):** ≥ 2 recordings per game (Kamla, Outer Wilds) of
   ≥ 3 min each, on both a strong and a weak machine →
   `action_video_grounding.py` PASS with |lag| ≤ 50 ms, |corr| ≥ 0.15, no
   post-correction. Segments of each video (first/middle/last thirds) must
   measure the same lag (offset must not drift).
3. **Contract:** `translator qa-v2` on the tool's native output → no FAIL on
   any session (frame-drop spacing WARN is acceptable until A1 lands).
4. **Forced-failure drills:** break each subsystem deliberately (block the Raw
   Input window; run an exclusive-fullscreen title; kill ffmpeg finalize) → the
   tool must refuse/warn, never emit a silently defective session.
5. **Frame integrity (after A1):** 2-min high-motion capture → inter-frame PTS
   ≈ uniform 33.33 ms, drop count ≤ 0.5%.

### Lessons already paid for (don't relearn them)

- **Measure, never assume.** During the post-hoc rescue we found one session
  whose 2.3 s lag came from event *attribution* in one processing run, not the
  capture clock. Whatever you change, re-measure with the client's script;
  never hard-code an offset.
- **A negative correlation is correct** (see §3 note). The client confirmed
  their customer's "inverted axis" complaint was a misread log.
- **Real fps ≠ nominal fps.** Every timing computation must come from actual
  PTS, not the 30 fps the container advertises.
- **Anything not captured is gone.** Mouse motion, dropped-frame pixels, audio
  — none are recoverable in post. That is why the self-check gates *at record
  time*, while the contributor can still redo the session.

---

## 8. Suggested execution order

1. Recover source (§2); reproduce a capture end-to-end as-is.
2. A2 clock anchor + finalize-step skeleton (trim → bin → v2 write) — this
   makes the tool's output measurable by the acceptance tests.
3. B1/B2/E1/E2 health checks + D1 game canonicalization → P0 complete; run the
   full §7 matrix.
4. A1 capture-path replacement (biggest lift, biggest quality win), then
   A3/D2 metadata truth.
5. C1 audio (after confirming requirement with Odyssey) and C2 black-frame
   protection.
6. P2 input-hygiene items, folding each into the self-check so it can't
   regress.

Questions about client-side behavior (audio requirement, threshold changes,
their standalone QA runner) go to Jack Davis (Odyssey) — he has offered their
internal scripts before and responds quickly.
