# HumynCapture.exe — Capture-Tool Issues & Root-Cause Brief

**Purpose.** Hand this file to Claude Code (with the decompiled source) to **identify root causes and fix the capture tool**. Every issue below is grounded in observed output data and cross-referenced to the decompiled module that most likely owns the bug.

**Subject:** `HumynCapture.exe` — PyInstaller bundle of a Python 3.12 + PySide6 app. Decompiled modules live under `PYZ.pyz_extracted/app/` (see the module map at the end). Companion analysis: `HumynCapture_Implementation_and_Spec_Diff.md`.

**Scope.** These are *capture-side* defects (data is lost or wrong at record time). Defects that are merely *post-processing* concerns are already handled by the `translator/` package and are called out only where they reveal a capture bug. Camera capture is explicitly out of scope.

---

## How to use this document

Each issue has: **Symptom → Evidence → Suspected root cause (with code location) → Impact → Suggested fix → How to verify.** For each fix, add a regression check to a capture self-test (see "Capture self-check & observability" at the end) so these can't silently recur.

---

## Evidence base (the 6 samples processed 2026-06-08)

| bundle | metadata `game.name` | exe | nb_frames | dur (s) | frames @30fps | dropped | drop % | audio | mouse_raw | mouse_button | key |
|---|---|---|---|---|---|---|---|---|---|---|---|
| kamla/smp1 | `Kamla` | Kamla.exe | 30,078 | 1144.9 | 34,346 | 4,268 | 12.4% | ❌ none | 32,965 | 205 | 1,666 |
| kamla/smp2 | `Kamla` | Kamla.exe | 28,320 | 1081.0 | 32,431 | 4,111 | 12.7% | ❌ none | 0 | 110 | 1,352 |
| kamla/smp3 | `Kamla` | Kamla.exe | 18,419 | 720.8 | 21,624 | 3,205 | 14.8% | ❌ none | 0 | 68 | 1,200 |
| outer_wilds/smp1 | `Outer wild` | OuterWilds.exe | 10,719 | 444.2 | 13,327 | 2,608 | 19.6% | ❌ none | 7,424 | 64 | 582 |
| outer_wilds/smp2 | `Outerworld` | OuterWilds.exe | 14,531 | 584.7 | 17,542 | 3,011 | 17.2% | ❌ none | 0 | 90 | 871 |
| outer_wilds/smp3 | `Outerwild` | OuterWilds.exe | 20,773 | 786.5 | 23,596 | 2,823 | 12.0% | ❌ none | 17,805 | 76 | 666 |

"Dropped" = `round(duration × 30) − nb_frames`. The video container reports `r_frame_rate = 30/1` for all, but the real average is ~24–26 fps. Inter-frame intervals are exact multiples of 33.33 ms (30 fps grid) with gaps of 2–12 missing frames — i.e. frames are *dropped*, not captured at a lower steady rate.

---

# Group A — Timing & frame integrity

## A1 — Video drops 12–20% of frames during capture

**Symptom.** Every video is a 30 fps grid with missing frames; the true average is ~24–26 fps. Drops are *clustered*, not uniform.

**Evidence.** See the table — 12.0%–19.6% of frames dropped on all six. Frame PTS gaps are exact multiples of 33.33 ms (2–12 consecutive frames missing at a time), concentrated in load-heavy regions.

**Suspected root cause.** `app.core.ffmpeg_recorder.FFmpegRecorder` uses **`gdigrab` (GDI BitBlt of the desktop region) + software `libx264 preset=fast CRF 20` at 1080p/30**. Under game load this pipeline can't sustain 30 grabs+encodes/sec, so the grabber/encoder drops input frames:
- `gdigrab` is CPU-bound (per-frame GDI blit + copy of the full region) and competes with the game for CPU.
- Software x264 at 1080p30 `preset=fast` is heavy; if the encoder back-pressures, frames are dropped at the source.
- The capture process likely runs at normal priority, losing CPU to the foregrounded game.

**Impact.** Because drops cluster, any uniform-fps mapping of input events to frames desyncs by seconds mid-clip (we measured up to ~10–22 s of event↔frame drift) even when total duration matches. Lost frames are also lost *pixels* — unrecoverable from the video. (The `translator/` package recovers *sync* via PTS-aware binning, but the missing pixels and the underlying drop are capture-side.)

**Suggested fix (root cause).**
- Switch to a GPU-accelerated capture path: ffmpeg `ddagrab` (DXGI Desktop Duplication) instead of `gdigrab`, or the Windows Graphics Capture API. This removes the CPU blit bottleneck.
- Switch to a hardware encoder when available: `h264_nvenc` / `h264_qsv` / `h264_amf`, falling back to libx264 `preset=veryfast`/`ultrafast` only if no HW encoder exists.
- Force true CFR and surface drops: add `-fps_mode cfr` (or `-vsync cfr`) so output is genuinely constant-rate, and parse ffmpeg's stderr `frame=…/drop=…` so the tool *knows* how many frames it dropped (record it — see A2/D2).
- Raise capture process priority (e.g. `ABOVE_NORMAL`) so the recorder isn't starved.
- Expose a quality/perf preset so weaker machines can drop to 720p or use a faster encoder rather than silently dropping frames.

**How to verify.** Capture a 2-min high-motion session; `ffprobe -select_streams v:0 -show_entries packet=pts_time` and confirm inter-frame intervals are uniform (≈33.33 ms, stddev ≈0) and `round(duration×fps) == nb_frames` (≤0.5% drop). ffmpeg's own `drop=` counter should be ~0.

---

## A2 — Event/video time origin may not be frame-0

**Symptom.** Input timestamps and video frame 0 can be offset by a small constant.

**Evidence.** Event `t` is "µs since `perf_counter()` at the moment ffmpeg is *confirmed running*" (§1.4). That instant is **not** the presentation time of the first encoded frame — there's a startup gap between "process running" and "first frame on disk."

**Suspected root cause.** `app.core.session_engine.SessionEngine.run` sets the anchor when the recorder reports running, not from the video's first-frame PTS. Any latency between the two becomes a fixed offset between the input stream and the video.

**Impact.** A constant lead/lag (sub-second, ~1–3 frames in our trimmed outputs) between inputs and video. Small, but it stacks with A1.

**Suggested fix.** Anchor inputs to the **first frame's PTS** rather than process-start: after recording, read the first frame PTS from the muxed file (`ffprobe`) and store it; or have ffmpeg emit a start timestamp and align to it. Record the chosen anchor explicitly in metadata so downstream can correct it.

**How to verify.** Generate a synchronized marker (e.g. a scripted key press at a known on-screen event) and confirm the event lands on the matching frame.

---

## A3 — Metadata reports nominal 30 fps, not achieved fps

**Symptom.** `metadata.json → video.fps` is `30.0` on every session, but the achieved average is 24–26 fps.

**Evidence.** All six report `fps: 30.0`; measured averages 24.1–26.4. `r_frame_rate` is `30/1` (nominal base rate), which is what gets reported.

**Suspected root cause.** `app.core.ffprobe` reads the *nominal* `r_frame_rate` (or just echoes `RecorderConfig.fps`) instead of computing `nb_frames / duration` (the true average) or reading `avg_frame_rate`.

**Impact.** Any consumer trusting `video.fps = 30` mis-times every event. (Our translator ignores this field and computes real timing from PTS, but the metadata is still wrong.)

**Suggested fix.** Report **both**: `fps_nominal` (target) and `fps_actual = nb_frames/duration` (or `avg_frame_rate`), plus `frames_dropped` (from A1's ffmpeg `drop=` counter). Don't emit a bare `fps` that implies CFR-30.

**How to verify.** `video.fps_actual` should equal `nb_frames/duration` within rounding.

---

# Group B — Input capture

## B1 — Raw mouse motion silently not captured (zero `mouse_raw`)

**Symptom.** Entire sessions record with **no mouse-motion data** while keyboard and mouse *buttons* are captured normally. No warning to the user.

**Evidence.** 3 of 6 sessions (kamla/smp2, kamla/smp3, outer_wilds/smp2) have `mouse_raw = 0` but `mouse_button` and `key` events present. (Prior batch: 4 of 6.) The two mouse subsystems are independent; only the motion one fails.

**Suspected root cause** (`app.core.raw_mouse.RawMouseCapture` + `session_engine`):
- The Raw Input sink (message-only window + `RegisterRawInputDevices(usage=0x02, RIDEV_INPUTSINK)` on a dedicated Win32 thread, §1.7) can fail to initialize (registration returns false, the window/message pump loses a startup race, or the thread dies). `_thread_main` **swallows the exception into `last_error`**, and `session_engine.run` **never checks `last_error` after start** → session proceeds with zero motion, silently.
- Second path: `session_engine._merge_inputs` **drops any raw-mouse record whose `ts_offset_ns` is `None`** instead of falling back to the recorded `ts_monotonic_ns` — so even partially-captured motion can be silently discarded.

**Impact.** **Unrecoverable.** Mouse-look / aim data never reaches disk (the temp `raw_mouse.jsonl` is deleted after merge). These sessions must be re-recorded — they cannot be fixed in post.

**Suggested fix.**
- After starting `RawMouseCapture`, **check `last_error`** and abort/visibly-warn if init failed (don't enter RECORDING with a dead sink).
- In `_merge_inputs`, **fall back to `ts_monotonic_ns`** when `ts_offset_ns` is `None` instead of dropping the record.
- Add a **liveness check**: if the session has keyboard/button activity but **zero** `mouse_raw` after the first N seconds, surface a loud warning ("mouse motion not being captured — restart capture / run as admin"). See B2.
- Make `RegisterRawInputDevices` failures retry, and log the specific Win32 error code.

**How to verify.** Force-fail the sink (e.g. block window creation) and confirm the tool refuses to record / warns, rather than producing a silent zero-motion session. Normal runs must contain `mouse_raw` whenever the cursor moves.

---

## B2 — No detection that an input modality is missing

**Symptom.** The tool will happily finish a session with no keyboard, no mouse motion, or no mouse buttons and report success.

**Evidence.** B1's silent failure; also nothing flags "0 key events" or "0 button events." Our downstream QA had to add these checks (`keyboard_capture` / `mouse_capture` / `mouse_buttons` in `data_quality`).

**Suspected root cause.** `session_engine` has no end-of-session sanity check on `events_by_type`.

**Impact.** Bad sessions ship and are only caught downstream (or not at all).

**Suggested fix.** At session end, assert each expected modality has >0 events (keyboard, mouse motion, mouse buttons for kbd+mouse games). If any is empty, mark the session **not ready** and warn with the specific missing modality. Mid-session, show a small live "keys ✓ / mouse-move ✓ / buttons ✓" indicator so contributors catch it immediately.

**How to verify.** A session that genuinely used all three must pass; a forced single-modality session must be flagged.

---

## B3 — Inconsistent modifier-side reporting (`shift` vs `shift_l`)

**Symptom.** The same physical modifier is reported sometimes as the **generic** token (`shift`, `ctrl`) and sometimes as a **side-specific** token (`ctrl_l`, `alt_l`), even within one game.

**Evidence.** Outer Wilds inputs emit bare `shift` but side-specific `ctrl_l`; Kamla emits `shift`, `ctrl_l`, `alt_l`. This forced a keybind-resolution workaround in the translator (a `shift_l` binding had to also match a bare `shift`).

**Suspected root cause.** `app.core.keyboard_capture.InputCapture` passes pynput key names straight through; pynput's `Key.shift` vs `Key.shift_l`/`Key.shift_r` reporting is inconsistent depending on how Windows delivers the event.

**Impact.** Action resolution can miss a binding (e.g. "L-Shift → up-thrust" not firing for a bare `shift`). Recoverable but fragile.

**Suggested fix.** Normalize modifiers consistently at capture time — resolve the actual side from the Windows scancode/`lParam` (extended-key bit) so you always emit `shift_l`/`shift_r` (and `ctrl_*`/`alt_*`) deterministically. If the side is genuinely unknowable, always emit the generic token — but be consistent.

**How to verify.** Press left vs right shift/ctrl/alt; each must produce a stable, side-correct token every time.

---

## B4 — OS/system keys & non-game input pollute the stream

**Symptom.** Global-hook noise leaks into `inputs.jsonl`: `cmd`/Win, media keys, `print_screen`, `insert`, `caps_lock`, and unmapped `vk_###` codes.

**Evidence.** Kamla inputs contained `cmd`, `vk_97` (numpad), etc. (Our key-normalizer strips these, but they shouldn't be captured as game input in the first place.)

**Suspected root cause.** `InputCapture` uses a **global** pynput hook that sees *all* keystrokes regardless of focus, and emits whatever pynput labels them (including raw `vk_###` for keys it can't map). There's filtering for focus, but not for non-game/system keys.

**Impact.** Pollution downstream; `vk_###` codes are uninterpretable.

**Suggested fix.**
- Map `KeyCode.vk` numpad/extended keys to canonical names (use a `vk` → name table) instead of emitting `vk_97`.
- Optionally drop OS/system keys (Win/cmd, media, lock keys, PrintScreen) at capture, or tag them so downstream can filter without guessing.
- Confirm focus-gating actually suppresses input while the game isn't foreground (see B6).

**How to verify.** Press Win, volume, PrtSc, numpad keys during a session; confirm they're either canonicalized or excluded — no raw `vk_###`.

---

## B5 — Control-byte artifacts from Ctrl+letter

**Symptom.** `Ctrl`+letter combos can emit control characters (U+0001–U+001F) as the `key` value.

**Evidence.** Our normalizer explicitly drops single chars with `ord < 32` (Ctrl+W → U+0017, etc.).

**Suspected root cause.** pynput on Windows reports the control character for Ctrl+letter rather than the base letter; `InputCapture._key_to_str` doesn't sanitize it.

**Impact.** Garbage `key` tokens; the actual letter is obscured. Recoverable but noisy.

**Suggested fix.** When a key event arrives with a control-char payload, map it back to its base letter (`U+0001→a … U+001A→z`) or use the virtual-key code, so `Ctrl+W` records as `ctrl` + `w`.

**How to verify.** Hold Ctrl and press letters; confirm clean `ctrl`+`<letter>` tokens, no control bytes.

---

## B6 — Focus tracking is coarse (5 Hz) and can drop/stick input

**Symptom.** Up to ~200 ms of input around focus changes can be mishandled; quick alt-tabs may be missed; keys held across a focus loss can be cleared.

**Evidence.** `FocusTracker` polls `GetForegroundWindow()` at **5 Hz** (§1.8) and calls `InputCapture.set_enabled(False)` on focus loss, which **clears `_keys_down`**. kamla/smp2 had 6 focus-loss events (frequent alt-tabbing).

**Suspected root cause.** Polling latency + abrupt `keys_down` clearing in `app.core.focus_tracker` / `keyboard_capture.set_enabled`.

**Impact.** Held-key state can desync at focus boundaries (a key "down" before focus loss never gets its "up", or vice-versa). Minor but can corrupt held-state binning near alt-tabs.

**Suggested fix.** Use event-driven focus notifications (`SetWinEventHook` on a dedicated message-pump thread — the same thread can host the Raw Input window) instead of 5 Hz polling. On focus loss, synthesize `up` events for currently-held keys rather than silently clearing, so held-state stays consistent.

**How to verify.** Hold a key, alt-tab out and back; confirm a clean down/up pair and no stuck keys.

---

## B7 — Simultaneous L+R modifier "bleed"

**Symptom.** Both sides of a modifier (e.g. `shift_l` **and** `shift_r`) reported as held in the same instant though only one was pressed.

**Evidence.** Documented capture artifact from prior samples (this batch: 0 occurrences, but it's intermittent). Our binner detects and drops the spurious side and flags the frame.

**Suspected root cause.** Windows/pynput can momentarily report both sides during fast modifier transitions; `InputCapture` doesn't de-bounce.

**Impact.** Spurious "both shifts held" frames; recoverable downstream but indicates capture jitter.

**Suggested fix.** De-bounce modifier pairs at capture: if both sides flip within a few ms, keep the one with a real scancode transition.

**How to verify.** Rapidly tap one shift; confirm only that side is ever reported.

---

# Group C — Video / audio capture

## C1 — No audio track captured at all

**Symptom.** Every `video.mp4` is video-only; no audio stream.

**Evidence.** `has_audio = false` on all six. Delivery guidance expects "mostly gameplay pixels **+ audio**."

**Suspected root cause.** `FFmpegRecorder` invokes `gdigrab` for video with **no audio input** (no WASAPI loopback / `dshow` audio device). gdigrab doesn't capture audio.

**Impact.** If Odyssey wants game audio, every session so far is non-compliant. Unrecoverable in post (audio was never recorded).

**Suggested fix.** Add a system-audio (loopback) input to the ffmpeg command — Windows WASAPI loopback (`-f wasapi` in recent ffmpeg, or a virtual loopback device via `dshow`), mux as AAC. Make it configurable (some captures may intentionally want silence) and report `has_audio` in metadata. Confirm with Odyssey whether audio is required. Note: enabling audio reintroduces A/V-sync handling (independent audio/video clocks) — resample/timestamp audio against the same timeline.

**How to verify.** `ffprobe` shows an audio stream; audio is audible and roughly A/V-synced.

---

## C2 — Exclusive-fullscreen games capture as black; desktop-region over-capture

**Symptom.** `gdigrab` desktop-region capture (§1.5) bypasses the compositor: true exclusive-fullscreen games record black/desktop pixels, and the desktop region can also include overlays/other windows.

**Evidence.** Architectural (§1.5/§2.8). Contributors are merely *told* to use borderless-windowed — nothing enforces it.

**Suspected root cause.** `FFmpegRecorder` grabs a screen *region*, not the game surface; no validation that real game pixels are being captured. Exclusive-fullscreen games bypass the Desktop Window Manager and flip their own buffers to the display, so those pixels never appear in the desktop GDI surface `gdigrab` reads → black/frozen capture. Grabbing a desktop rectangle also records anything else on screen in that region (overlays, notifications, taskbar).

**Impact.** Black or contaminated footage; only discovered after the fact.

**Suggested fix.**
- Add a **black-frame / static-frame heuristic** on the first ~1 s of capture and warn loudly if the region is black or unchanging.
- Use the Windows Graphics Capture API (or ffmpeg `ddagrab`/DXGI Desktop Duplication) to capture the actual composited output / a specific window directly — this both fixes the black-frame case and avoids overlay contamination. (Same capture-path change as A1.)

**How to verify.** Start capture on an exclusive-fullscreen title; tool warns instead of silently recording black.

---

## C3 — Forced `kill()` after 5 s can truncate the video

**Symptom.** If ffmpeg doesn't finalize within 5 s of the `q` shutdown, it's `kill()`ed, leaving a truncated/unfinalized MP4 (missing `moov` atom).

**Evidence.** §1.5 describes graceful `q` then `kill()` after a 5 s timeout.

**Suspected root cause.** Hard timeout in `FFmpegRecorder.stop()`. Finalization (flushing encoder buffers + writing/relocating the `moov` atom, especially with `+faststart`) is not instant for long recordings and can exceed 5 s on a loaded machine or slow disk; killing before the `moov` atom is written yields a file with no index.

**Impact.** Rare, but a killed finalize can yield an unreadable or partially-readable video → whole session lost.

**Suggested fix.**
- Scale the finalize timeout with recording length (or make it generously large, e.g. 30–60 s) instead of a flat 5 s.
- If `kill()` was needed, attempt an `ffmpeg`-based remux/repair and flag the session as suspect.
- Consider recording to a fragmented MP4 (`-movflags +frag_keyframe+empty_moov`) so the file stays valid even if finalization is interrupted (no single trailing `moov` atom to lose).

**How to verify.** Force a slow finalize; confirm the file is still playable or flagged.

---

# Group D — Metadata correctness

## D1 — `game.name` is free-text and frequently mistyped

**Symptom.** The same game is recorded under different, misspelled names.

**Evidence.** Outer Wilds appears as `Outer wild`, `Outerworld`, `Outerwild` across three sessions; the exe is consistently `OuterWilds.exe`. This routed sessions to the wrong output folder / empty keybind until the translator added an exe-name fallback.

**Suspected root cause.** The "Pick the game" UI / session form lets the contributor type a free-form name (`metadata.game.name`); there's no validation or canonicalization against a known title list or the exe.

**Impact.** Wrong delivery paths, broken game→keybind lookup, dedup/grouping errors. Recoverable only with heuristics.

**Suggested fix.** Canonicalize `game.name`: pick from a known-titles dropdown, or derive/normalize from `exe_name` (the reliable signal), or validate against a maintained alias map. Store both the raw display name and a canonical `game_slug`.

**How to verify.** Three captures of the same game produce identical `game_slug` regardless of typed name.

---

## D2 — Metadata omits drop/quality fields needed for trust

**Symptom.** No `frames_dropped`, `fps_actual`, or capture-health fields; consumers can't tell a clean session from a degraded one.

**Evidence.** `metadata.json` has nominal `fps` only (A3) and no drop counter, despite 12–20% drops.

**Suspected root cause.** `session_engine` writes a fixed schema without capture-health telemetry.

**Impact.** Silent quality variance; downstream can't gate.

**Suggested fix.** Add `video.fps_actual`, `video.frames_dropped` (from ffmpeg `drop=`), and an `input_capture.modalities_present` block (keyboard/mouse_motion/mouse_buttons booleans). Also capture `system.keyboard_layout` (`GetKeyboardLayout()`) for international-layout normalization (§2.8).

**How to verify.** Metadata reflects the real drop count and modality presence for a known-degraded vs clean session.

---

# Group E — Robustness & observability (cross-cutting)

## E1 — Background threads swallow errors; engine never checks

**Symptom.** Capture subsystems fail on their own threads and the orchestrator proceeds as if everything is fine.

**Evidence.** `RawMouseCapture._thread_main` stores exceptions in `last_error`; `session_engine.run` never reads it (B1). Likely the same pattern elsewhere (focus/recorder threads).

**Suspected root cause.** No post-start health check / error propagation from worker threads to the engine and UI.

**Impact.** Silent partial captures — data is missing with no indication to the contributor.

**Suggested fix.** Centralize a per-subsystem health/`last_error` poll after start and periodically during recording; any subsystem error → visible warning + mark session not-ready. Log all swallowed exceptions to `humyncapture.log` with context.

**How to verify.** Inject a failure in each subsystem; the engine surfaces it every time.

---

## E2 — No end-of-session self-check gating "ready"

**Symptom.** Sessions are considered done with no validation.

**Evidence.** Nothing validates the written session before it's treated as complete; every defect above ships unnoticed.

**Suspected root cause.** `session_engine` has no post-write validation stage.

**Impact.** Defective sessions are indistinguishable from good ones until reviewed downstream.

**Suggested fix.** Add `app.core.self_check` (the spec-aligned checks from `HumynCapture_Implementation_and_Spec_Diff.md §2.3` plus: frames-dropped under threshold, all modalities present, video readable, A/V present if required). Refuse to mark `ready_for_upload` on failure and show which check failed.

**How to verify.** A clean session passes; a session with any injected defect (dropped frames, missing modality, unreadable video) is blocked with the specific failure named.

---

## Capture self-check & observability harness

Add a post-session validator (and matching live indicators) that asserts, per session:
- `round(duration × fps_actual) == nb_frames` and frames_dropped within threshold (A1/A3).
- `mouse_raw > 0` whenever the game uses the mouse; `key > 0`; `mouse_button` present as expected (B1/B2).
- video stream readable + (if required) audio present (C1/C3).
- `game.name` resolves to a known `game_slug` (D1).
- no raw `vk_###` / control-byte tokens in the stream (B4/B5).

Surface failures in the GUI and in `humyncapture.log`, and gate `ready_for_upload` on them (E2). Each fix above should land with a corresponding assertion here so regressions are caught at capture time, not in post.

---

## Appendix — decompiled module map (where to look)

```
PYZ.pyz_extracted/app/
├── main.pyc                  entry, logging setup
├── core/
│   ├── paths.pyc             %LOCALAPPDATA% layout
│   ├── state.pyc             setup_complete flag, state.json
│   ├── contributor.pyc       HMAC email → c_<hex16>
│   ├── process_watcher.pyc   psutil-based exe/PID resolution
│   ├── ffmpeg_recorder.pyc   gdigrab → libx264/CRF20/30fps/1080p   ← A1, C1, C2, C3
│   ├── ffprobe.pyc           reads codec/width/height/fps from MP4  ← A3
│   ├── keyboard_capture.pyc  pynput global hooks → asyncio queue    ← B3, B4, B5, B7
│   ├── raw_mouse.pyc         Win32 Raw Input on dedicated thread    ← B1, E1
│   ├── focus_tracker.pyc     5 Hz GetForegroundWindow polling       ← B6
│   └── session_engine.pyc    orchestrator; merges + writes session  ← A1, A2, B1, B2, D1, D2, E1, E2
├── ui/
│   ├── main_window.pyc       IDLE/RECORDING modes
│   ├── setup_window.pyc      first-run wizard                       ← D1 (game picker)
│   ├── async_runner.pyc      QThread/asyncio bridge
│   └── style.pyc
└── setup/
    └── installer.pyc         gyan.dev ffmpeg downloader/extractor
```

> Note: several decompiled functions carry `# WARNING: Decompyle incomplete`. Cross-reference module docstrings and the actual on-disk output (the evidence table above) when the bytecode is unclear. Validate every fix against a fresh real capture, not just the decompiled source.
