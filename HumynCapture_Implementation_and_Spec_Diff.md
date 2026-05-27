# HumynCapture — Technical Implementation & Spec Gap Analysis

**Subject tool:** `HumynCapture.exe` (47 MB PE32+ GUI, Windows x86-64)
**Reference spec:** *Game Data Capture Spec v1 — Odyssey Confidential* (`game_data_capture_spec_v1__1_.pdf`)
**Sample data location:** `game-data/<Game Name N> - Inputs.jsonl` + `<Game Name N> - KeyBind.json`
**Existing post-processor:** `translate_sessions.py` (used today to convert raw output to spec format)

The .exe is a **PyInstaller bundle** of a Python 3.12 + PySide6 app. The Python code was extracted via `pyinstxtractor` and `pycdc` from the `app/` package inside `PYZ.pyz`. Functions whose decompilation was incomplete (`# WARNING: Decompyle incomplete`) have been cross-referenced against module docstrings (still intact) and the shape of the actual data the tool produces on disk.

This document has two parts:

1. **Part 1 — Current implementation:** what HumynCapture does today, module by module.
2. **Part 2 — Spec diff & migration plan:** what must change to emit data in the canonical format from §1 of the spec, plus the R2 upload and `.rrd` QA workflow from §2.

> Camera capture (`c2w_m##`, `camera_*`) is out of scope per user direction: HumynCapture has no way to obtain in-game camera matrices, and that gap is the responsibility of a separate in-game integration (mod / RenderDoc hook / SDK). The doc therefore treats camera columns as null and focuses on aligning the input + video + metadata side with the spec.

---

## Part 1 — Current Implementation

### 1.1 Process model & entry point

```
HumynCapture.exe (PyInstaller stub)
  └─ embedded Python 3.12 + PySide6
       └─ app.main:main()
            ├─ _setup_logging  → %LOCALAPPDATA%\HumynCapture\logs\humyncapture.log
            ├─ app.core.state.is_setup_complete()  → first-run gate
            ├─ app.ui.setup_window.SetupWizard     (only on first run)
            └─ app.ui.main_window.MainWindow       (the always-on UI)
```

Logging is configured before any other app module imports, so every later `log.info()` lands in the same rotating file (5 MB × 3, `RotatingFileHandler`).

### 1.2 On-disk layout

Everything lives under `%LOCALAPPDATA%\HumynCapture\` (from `app.core.paths`):

```
%LOCALAPPDATA%\HumynCapture\
├── ffmpeg\
│   └── ffmpeg-<release>\bin\{ffmpeg.exe, ffprobe.exe}   # bundled binary
├── sessions\
│   └── <session_id>\
│       ├── video.mp4
│       ├── inputs.jsonl
│       ├── metadata.json
│       └── keybind.json              # only if user provided one
├── logs\humyncapture.log
└── state.json                        # {"setup_complete": true}
```

The app never touches `%APPDATA%` or any system-wide config — clean uninstall is `rd /s %LOCALAPPDATA%\HumynCapture`.

### 1.3 First-run setup (`app.setup.installer`)

On first launch the SetupWizard runs `install_ffmpeg()`:

1. Downloads `https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip` (~110 MB) via `urllib.request` with a `HumynCapture/0.1` UA.
2. Extracts into `%LOCALAPPDATA%\HumynCapture\ffmpeg\`.
3. Verifies `ffmpeg.exe` is reachable; if not, raises with the first 20 extracted paths so the user sees what went wrong.
4. Sets `state.json` → `{"setup_complete": true}`.

The gyan.dev "essentials" build is statically-linked (no extra DLLs), which is why the .exe ships without bundling ffmpeg itself.

### 1.4 Session orchestration (`app.core.session_engine.SessionEngine`)

One `SessionEngine` instance per recording. The async pipeline (`async def run(meta)`):

```
locate game window (5s poll)
  → start ffmpeg recorder       (anchor = perf_counter() at start)
  → start InputCapture          (pynput hooks)
  → start AsyncRawMouseCapture  (Win32 Raw Input thread)
  → start FocusTracker          (5 Hz GetForegroundWindow polling)
  → await (game exits) OR (user clicks stop) OR (cancel flag)
  → stop everything
  → merge events  → inputs.jsonl
  → ffprobe video → metadata.json
  → return SessionResult
```

Notable design choices baked into the engine:

- **Time anchor.** All event timestamps are microseconds since `time.perf_counter()` at the moment `ffmpeg` is confirmed running. The docstring explicitly avoids `time.monotonic()` because on Windows it has ~15.6 ms resolution and would quantize timestamps. The anchor's wall-clock value is recorded in `metadata.json` as `recording.anchor_monotonic_us`.
- **Window discovery.** `_get_window_for_pid()` enumerates *all* visible top-level windows and picks the largest one whose owning PID matches the game *or* whose exe basename matches `<game>.exe`. This catches launcher-spawns-child architectures (Unreal/Unity) and ignores Steam overlay helper windows.
- **Capture rect.** Uses the *client* rect (not full window rect) so title bars/borders aren't captured. Both dimensions are forced even (libx264 + yuv420p require it).
- **Slug.** `session_id = <slugified_game_name>_<UTC timestamp YYYY-MM-DDTHH-MM-SSZ>`.

### 1.5 Video capture (`app.core.ffmpeg_recorder.FFmpegRecorder`)

Single ffmpeg subprocess invoked with `gdigrab` on the desktop region — *not* `gdigrab hwnd=N`. The docstring explains why: `hwnd=` mode `BitBlt`s the GDI surface, which for DirectX/Vulkan/OpenGL games is decoupled from the rendered frame, giving blank/desktop pixels. Desktop-region capture reads actual screen pixels.

Default `RecorderConfig`: **1920×1080, 30 fps, libx264, preset=fast, CRF 20, yuv420p, `-movflags +faststart`.** CRF 20 is near-lossless for game footage; the comment notes "5–15 GB/hr depending on motion" — the v0 default favors quality over file size.

The scale filter is:

```
scale='min(W,iw)':'min(H,ih)':force_original_aspect_ratio=decrease,
pad=W:H:(ow-iw)/2:(oh-ih)/2:black
```

So smaller windows get letter/pillarboxed up to the target resolution; larger windows are scaled down preserving aspect.

Shutdown is graceful: sending `q` to ffmpeg's stdin lets it finalize the moov atom; only after a 5 s timeout does it `kill()` (truncated file).

> **Implication for the spec.** This mode does NOT capture true exclusive-fullscreen games (the desktop compositor is bypassed). HumynCapture's docs tell contributors to use borderless-windowed.

### 1.6 Keyboard + mouse-button capture (`app.core.keyboard_capture.InputCapture`)

Uses **pynput** (Windows low-level global hook) on background threads. Hooks see input regardless of which window has focus.

Emits to an `asyncio.Queue`:

```jsonc
{"t": 41245204, "type": "key",          "key": "space",  "action": "down"}
{"t": 41405251, "type": "key",          "key": "space",  "action": "up"}
{"t": 4796448,  "type": "mouse_button", "button": "left","action": "up"}
{"t": 1058212431, "type": "mouse_wheel", "dy": 1}
```

- `t` is microseconds since the perf-counter anchor.
- Repeat-press suppression: `_keys_down` set; a key already-down won't emit another `down` event.
- `_button_to_str` maps `pynput.mouse.Button.{left,right,middle}` to strings; extra buttons (x1/x2) come through with whatever `name` pynput exposes.
- When `FocusTracker` says the game lost focus, `set_enabled(False)` clears `_keys_down` and drops further events until focus returns.

### 1.7 Raw mouse delta capture (`app.core.raw_mouse.RawMouseCapture`)

This is the most subtle module. Most AAA games lock the OS cursor and read mouse motion directly from HID via DirectInput or Raw Input. Pynput's hooks only see cursor *position* — which the game isn't using — so dx/dy show up as ~0.

The fix: register HumynCapture itself as a **Raw Input sink**, on a dedicated Win32 thread with a message-only window:

```
WNDCLASSEXW("HumynCaptureRawInput")
  → CreateWindowExW(HWND_MESSAGE, ...)
  → RegisterRawInputDevices(usagePage=0x01, usage=0x02, flags=RIDEV_INPUTSINK)
  → GetMessage/Translate/Dispatch loop
      → WM_INPUT → GetRawInputData → decode RAWMOUSE
          → write JSONL line: {ts_offset_ns, ts_monotonic_ns, dx, dy, buttons[], wheel}
```

Key facts:

- `dx`/`dy` are **raw HID counts**, not pixels — they're what the game sees. Conversion to "pixels at user's pointer-speed" is explicitly deferred to downstream consumers.
- `MOUSE_MOVE_ABSOLUTE` (pen/digitizer) is ignored.
- This file is written by the capture thread to a *temporary* `raw_mouse.jsonl`; `SessionEngine._merge_inputs` re-times each event (using `set_t_zero(monotonic_ns)` from the ffmpeg start) into the unified `inputs.jsonl` with the canonical schema `{"t": <us>, "type":"mouse_raw", "dx":…, "dy":…}`.

### 1.8 Focus tracking (`app.core.focus_tracker.FocusTracker`)

Polls `GetForegroundWindow()` + `GetWindowThreadProcessId()` at 5 Hz. When the foreground PID stops matching the game's PID:

- Emits a `{"t":…, "type":"focus", "focused": false}` event into the same queue.
- Calls `InputCapture.set_enabled(False)`.

The 200 ms latency is intentional — `SetWinEventHook` would need a Win32 message pump on the same thread, which doesn't compose with the asyncio engine.

### 1.9 Process watching (`app.core.process_watcher`)

- `find_pid_by_exe()` — `psutil.process_iter`, case-insensitive name match, pick the most-recently-started.
- `list_likely_games()` — heuristic dropdown source for the "Pick the game" UI.
- `wait_for_exit()` — async polling on `psutil.Process.is_running()`.

### 1.10 Contributor identity (`app.core.contributor`)

Emails are never stored. A hardcoded HMAC secret (`humyn-labs-contributor-id-v1`) is used:

```
contributor_id = "c_" + HMAC-SHA256(secret, email.lower()).hexdigest()[:16]
```

The docstring is candid that this is *only* casual-leak prevention — extracting the secret from the .exe is trivial. The spec doesn't discuss contributor identity at all, so this is informational.

### 1.11 Output files (per session) — current schema

**`inputs.jsonl`** — one JSON per line, sorted by `t` (microseconds since anchor). Event types observed in real samples (`Hollow Knight 1`):

| type           | example                                                                | count in sample |
|----------------|------------------------------------------------------------------------|-----------------|
| `key`          | `{"t":…,"type":"key","key":"space","action":"down"}`                   | 3 276           |
| `mouse_raw`    | `{"t":…,"type":"mouse_raw","dx":-1,"dy":-2}`                           | 4 140           |
| `mouse_button` | `{"t":…,"type":"mouse_button","button":"left","action":"down"}`        | 426             |
| `focus`        | `{"t":…,"type":"focus","focused":false}`                               | 5               |
| `mouse_wheel`  | `{"t":…,"type":"mouse_wheel","dy":1}`                                  | 1               |

**`metadata.json`** — single object:

```jsonc
{
  "schema_version": 1,
  "session_id": "hollow_knight_2026-05-07T18-32-15Z",
  "player":  { "contributor_id": "c_…", "skill_level": "intermediate" },
  "game":    { "name": "Hollow Knight", "exe_name": "hollow_knight.exe",
               "pid_at_capture": 14328 },
  "session": { "role": "...", "objective_task": "..." },
  "recording": { "started_at_utc": "...Z", "ended_at_utc": "...Z",
                 "duration_seconds": 1820.456, "anchor_monotonic_us": ... },
  "video":   { "filename":"video.mp4", "codec":"h264", "width":1920, "height":1080,
               "fps":30.0, "size_bytes":... },
  "input_capture": { "filename":"inputs.jsonl", "events_total": N,
                     "events_by_type": {...} },
  "system":  { "os":"Windows 10", "humyncapture_version":"0.1.0",
               "screen_width":1920, "screen_height":1080, "screen_refresh_hz":144 }
}
```

**`keybind.json`** — **vendor / contributor authored**, NOT generated by the tool. The 110 files in `game-data/` are user-supplied and follow `{semantic_action: literal_key}`, exactly the *inverse* of the spec direction. Example (`Hollow Knight 1 - KeyBind.json`):

```json
{ "jump": "Space", "attack": "[", "dash": "LShift", ... }
```

Combo bindings appear as `{"modifier":"Control","key":"Q"}`. Multi-binding alternatives appear as lists (e.g. PoE's `"target_switch": ["MouseScrollUp","MouseScrollDown"]`).

### 1.12 The interim post-processor — `translate_sessions.py`

This script (run on macOS, outside the .exe) already does the canonical-format conversion for the data you currently have:

- Walks `game-data/* - Inputs.jsonl` + matching `KeyBind.json`.
- Bins events into 30 fps frames (33.333 ms wide) → `frames.csv`.
- Drops `mouse_wheel` and `focus` event types.
- Tracks rolling held-state for keys/buttons; emits each frame's pipe-delimited `input_keys` and `input_mouse_buttons`.
- Resolves `input_actions` by checking which keybind rules (with full case-folding + alias table) fire for the current held-set.
- Sums `mouse_raw.dx/dy` per frame.
- Inverts the vendor keybind to spec-style `{literal: [semantic, ...]}` and writes `key_binding.json`.
- Emits flat output: `<translated>/<session-slug>/{frames.csv, key_binding.json}`.

This is effectively the *spec-conformance layer* implemented in user-space. The rest of this doc explains how to fold it into the tool.

---

## Part 2 — Spec Diff & Migration Plan

### 2.1 Side-by-side gap table

| Topic | Spec v1 requires | HumynCapture today | Gap |
|---|---|---|---|
| **Session folder name** | `<vendor>/<game>/<mm-dd-yy>/<session-id>/` on R2 | local `%LOCALAPPDATA%\…\sessions\<session_id>\` | Need an upload step + path naming. |
| **`session.json`** | Metadata sidecar, required | Emits `metadata.json` (richer schema, different filename) | **Rename + align schema fields.** |
| **`frames.csv`** | Per-frame, one row per video frame, fixed column set | Not emitted at all (raw event stream instead) | **Build per-frame binner into the tool** (= move `translate_sessions.py` inside). |
| **`video.mp4`** | Required, screen recording | ✓ Already correct (1080p / 30 fps / H.264 yuv420p faststart) | None. |
| **`key_binding.json`** | `{literal_key: semantic_action}` | Vendor-supplied as `{semantic: literal}`; tool just copies it (or doesn't have it) | **Invert + canonicalize at write time.** |
| **`input_keys`** | Pipe-delimited active keys per frame, e.g. `W\|Shift\|+` | Per-event `key`/`action` only | **Compute held-state per frame.** Normalize names. |
| **`input_actions`** | Pipe-delimited semantic actions per frame | Not present | **Resolve from keybind at write time.** |
| **`input_mouse_buttons`** | Pipe-delimited active buttons per frame | Per-event `button`/`action` only | **Compute held-state per frame.** |
| **`input_mouse_dx/dy`** | Float per frame, accumulated | Per-event raw HID deltas | **Sum per frame.** |
| **`mouse_wheel` events** | No column in spec | Captured and stored | Drop during binning (translate script does this). |
| **`focus` events** | No column in spec | Captured and stored | Either drop or surface as a derived column (out of spec). |
| **Camera columns** (`c2w_m##`, `camera_*`) | Required *if* session has camera; nullable otherwise | Never captured | **Write as null** (per user direction). Sidebar: see §2.5. |
| **Coordinate system** | RFU world, RUB row-major C2W | N/A | N/A while camera is out of scope. |
| **15% QA `.rrd`** | `session.rrd` + `rrd_creation.py` for random 15% | Not produced | **Add a QA-mode emitter.** |
| **R2 upload** | Cloudflare R2 with vendor creds | Not implemented | **Add upload subsystem** (or document an external pipeline). |
| **Output format** | Parquet preferred, CSV accepted; identical columns | Custom JSONL + JSON | Pick CSV (lowest dep) or Parquet (better for downstream); both possible with `pyarrow` or `csv` stdlib. |

### 2.2 Concrete code changes inside HumynCapture

Below is the proposed module-level plan. File paths use the same `app/core` layout the tool already has.

#### 2.2.1 New module — `app.core.frame_binner`

Owns the conversion that `translate_sessions.py` does today. The module's surface area should be:

```python
class FrameBinner:
    def __init__(self, *, fps: int, anchor_monotonic_ns: int,
                 keybind: dict | None): ...

    def feed(self, event: dict) -> None:
        # event is one of the existing emitted dicts from
        # InputCapture / RawMouseCapture / FocusTracker
        ...

    def finalize(self, last_event_t_us: int) -> Iterator[dict]:
        # yields one dict per frame, with the spec's column set
        ...
```

Implementation notes lifted from the existing script (and the lessons baked into it):

- **Frame width:** `FRAME_US = 1_000_000 / FPS`. Match `RecorderConfig.fps` (currently 30).
- **Held-state tracking:** maintain `keys_down: set[str]` and `buttons_down: set[str]`. A key is "active during frame `f`" if it was down at any moment in that frame's window (including down-then-up within the same frame).
- **Name normalization:** apply the `KEY_ALIASES` table from `translate_sessions.py` *at binning time*, not in the keybind file. That way the on-disk `frames.csv.input_keys` already uses the same vocabulary the spec example shows (`W|Shift|+`).
- **Mouse-button held-state** is symmetric. Note pynput-emitted names (`left`/`right`/`middle`/`x1`/`x2`) need to be passed through unchanged so they line up with the keybind's `MouseLeft` / `MouseMiddle` after alias resolution (`KEY_ALIASES` already maps `mouseleft → @mouse:left`).
- **Action resolution:** flatten the vendor keybind into `[(required_token_tuple, semantic)]` rules once on construction. For each frame, compute `held_tokens = keys ∪ {@mouse:b for b in buttons}` and fire any rule whose `required` set is a subset. Preserve insertion order (a `dict` deduper does this in 3.7+).
- **Frame stride:** the existing script computes `ts_ms = round(f_idx * 1000 / FPS)` which matches the spec's "milliseconds from start of capture". Keep that.
- **Tail handling:** the loop in `translate_sessions.py` extends held-state through frames with no events. Replicate exactly — otherwise a single key held across hundreds of frames would only show up on the press and release frames.

#### 2.2.2 Rewrite of `SessionEngine._merge_inputs` and the write step

Currently `_merge_inputs` produces `inputs.jsonl`. The new flow should be:

1. Drain the queue + the raw-mouse temp file (same merge logic).
2. Pipe the merged events through `FrameBinner` in order.
3. Write `frames.csv` directly (or `frames.parquet` if we adopt Parquet — see §2.4).
4. *Optionally* keep `inputs.jsonl` behind a debug flag — it's useful for re-binning at a different FPS later. Recommendation: keep it, gated by a setting like `RecorderConfig.keep_raw_inputs = False` (off by default to reduce session size).

The number of video frames must match the binner's frame count. After `ffprobe` confirms the video length, snap `n_frames = round(video_duration_s * fps)` and write exactly that many rows. If the binner found more events past the last video frame, sum/clamp into the final frame; if fewer, pad with empty rows.

#### 2.2.3 `metadata.json` → `session.json`

The spec calls the metadata sidecar `session.json` and doesn't prescribe its schema. The pragmatic move is:

- Rename `metadata.json` → `session.json`.
- Keep every field already there — it's all useful — but add a top-level `canonical_format` block so consumers can detect spec compliance:

```jsonc
"canonical_format": {
  "spec_version": "v1",
  "frame_source": "frames.csv",
  "fps": 30,
  "n_frames": 54614,
  "has_camera": false,
  "has_inputs": true,
  "keybinding_source": "key_binding.json"
}
```

#### 2.2.4 `key_binding.json` direction

The vendor file (semantic → literal) lives in the contributor's preferences and is *required input*. The output file (`key_binding.json` in the session folder) must be the inverted view (literal → semantic) per spec § 1.4.1.

`translate_sessions.invert_keybind` already does this and handles the three vendor shapes (string, list, combo dict). Lift it directly into a new helper `app.core.keybind_translator.invert(vendor_keybind: dict) -> dict[str, list[str]]`. Combo bindings (`{"modifier":"Control","key":"Q"}`) keep the `"Control+Q"` joined-key form the script uses.

Open question: the spec says `key_binding.json` "should be tuned to the session players' individual game settings." HumynCapture doesn't have a UI for users to import/customize their bindings — today it consumes a `KeyBind.json` the vendor brings. Add a SetupWizard step (or per-session form) where the contributor picks/imports their keybinding file.

#### 2.2.5 Coordinate system & camera columns

Per user direction, this is **out of scope** for HumynCapture itself. The binner should still emit the camera column names with empty values so `frames.csv` is column-identical for downstream consumers (the spec explicitly says "Camera columns are null for sessions without camera capture — our viewer and data tables hide them automatically").

Concretely: `c2w_m00..c2w_m33`, `camera_model`, `camera_fx`, `camera_fy`, `camera_cx`, `camera_cy`, `camera_radial_k1..k6`, `camera_tangential_p1,p2` all present as empty CSV cells or `null` parquet values.

If a future in-game integration is added, it should write camera samples into a sidecar JSONL aligned to the same `perf_counter` anchor as the input capture; the binner can then resample to per-frame C2W matrices and apply the §4 conversion (RUB-based, det(R) = +1 self-check from §1.7).

### 2.3 Self-check before submission

The spec § 1.7 lists six checks. The non-camera ones we *can* enforce:

- Every frame row has `frame_id` strictly increasing from 0.
- `timestamp_ms[i+1] - timestamp_ms[i] ∈ {33, 34}` for 30 fps (round-off pattern).
- Number of rows matches `ffprobe` frame count.

Add an `app.core.self_check` module that runs after `frames.csv` is written and refuses to mark the session as `ready_for_upload` if any check fails. Surface failures in the GUI with file names + first failing row.

### 2.4 Output format: CSV vs Parquet

The spec accepts both ("identical column names and conventions"). Recommendations:

- **Default to CSV.** Zero extra dependency, simpler to inspect, matches what the existing `translate_sessions.py` already emits. The tool is already pulling in `csv` (stdlib).
- **Add Parquet as a build-time option.** `pyarrow` is large (~50 MB) and would balloon the installer. If parquet ships, gate it behind a "Compressed output" toggle in the SetupWizard rather than always.
- Either way, write column dtypes explicitly: int for `frame_id`/`timestamp_ms`, float for camera & mouse deltas, string for the rest.

### 2.5 Upload path — Cloudflare R2 (§ 2.1 of spec)

Spec: `<vendor>/<game>/<mm-dd-yy>(of upload)/<session-id>/`.

Recommended new module `app.upload.r2`:

- Config (per vendor): R2 account ID, bucket, access key ID, secret access key. Stored encrypted in `state.json` via DPAPI (`win32crypt.CryptProtectData`); never in plaintext.
- Use `boto3` with R2's S3-compatible endpoint (`https://<account>.r2.cloudflarestorage.com`).
- On a successful session + self-check pass, queue the session folder for upload. UI shows a per-file progress bar.
- Use `multipart_chunksize=8 MiB`, `max_concurrency=4`. R2's egress is free; throughput-wise the bottleneck is the contributor's upload bandwidth, so don't push too aggressively (saturating the line interferes with subsequent game sessions).
- Naming: the date is the upload date (UTC), not the recording date — match the spec wording. The session-id stays whatever `SessionEngine` minted at record time.
- Idempotency: if a prefix exists on R2, skip-or-resume based on a small `MANIFEST.json` we upload first listing expected files + sha256s.

Open product question: do we want contributors to be able to delete a session before upload (e.g. "I died in the first 10 s, scrap it")? Today the engine doesn't expose that. If yes, gate the upload behind an explicit "Submit" action; if no, auto-upload as soon as self-check passes.

### 2.6 15% Quality-Assurance `.rrd` workflow (§ 2.3 of spec)

Spec: a random 15 % of every batch must include `session.rrd` + `rrd_creation.py`.

Plan:

1. **Pick the sample.** When uploading a batch (or when sessions reach a configurable cap, e.g. 50 sessions before a flush), tag a random 15 % as "QA". Use a deterministic RNG seeded on the batch ID so the choice is reproducible.
2. **Generate `.rrd`.** Add a new module `app.qa.rrd_emitter` that depends on the `rerun-sdk` Python package. It re-reads `frames.csv` + `video.mp4` and logs:
   - `video` stream as a frame-indexed asset (`rr.log("video", rr.ImageBytes(...))` per decoded frame — or use `rr.log_video_asset` if the rerun version supports it).
   - `inputs/keys`, `inputs/actions`, `inputs/mouse_dx`, etc. as `rr.TextLog` or `rr.Scalar` columns aligned to frame index via `rr.set_time_sequence("frame_id", f)`.
   - When/if camera arrives: `rr.log("camera", rr.Pinhole(...))` and `rr.log("camera", rr.Transform3D(...))` from the C2W matrix.
3. **Emit `rrd_creation.py`.** Don't reverse-engineer the script — *generate it from a template* that lives next to the emitter, substituting in the session ID and the column list at render time. The output is small and human-readable, satisfying the spec's "script/code used to generate those .rrd files" requirement.
4. **Storage.** The `.rrd` lands in the same session folder; the upload pipeline detects its presence and uploads it.

Dependency footprint: `rerun-sdk` is ~80 MB and pulls in numpy. To avoid bloating every install, ship it only in a "QA build" or lazy-import / pip-install on first QA run. The simplest acceptable v1 is: ship rerun-sdk only with the QA flavor of the installer (a separate downloadable bundle).

### 2.7 Prioritized action list

| # | Change | Effort | Blocking for spec compliance? |
|---|---|---|---|
| 1 | Build `FrameBinner` and emit `frames.csv` per session | M | **Yes — core requirement.** |
| 2 | Emit `key_binding.json` in inverted (literal→semantic) form | S | **Yes.** |
| 3 | Rename `metadata.json` → `session.json`; add `canonical_format` block | XS | **Yes.** |
| 4 | Run self-check (§ 2.3) and gate "ready" state | S | Strongly recommended. |
| 5 | Stop bundling the raw `inputs.jsonl` by default (or move under `_debug/`) | XS | No, but keeps spec-clean. |
| 6 | Add R2 upload subsystem with vendor credential storage | L | **Yes — required by § 2.1.** |
| 7 | Add QA-mode `.rrd` emitter + 15 % sampler | M | **Yes — required by § 2.3.** |
| 8 | Add UI for contributor to import their keybind file | S | Implicit requirement (§ 1.4.1 tuning to player). |
| 9 | (Optional) Camera capture via in-game mod/SDK | XL | Only needed for games where Odyssey wants camera. |

XS = <1 day, S = 1–2 days, M = 3–5 days, L = ~2 weeks, XL = multi-week.

### 2.8 Risks & open questions

- **`mouse_wheel`.** Currently captured, currently dropped on translation. The spec has no column for it. If models trained downstream eventually want scroll input, propose adding `input_mouse_wheel` to spec v2.
- **`focus`.** Same story. Could be surfaced as a derived `input_focus` boolean column without breaking spec consumers (extra columns are allowed by parquet/CSV-schemas-as-superset conventions); confirm with downstream.
- **Frame-count drift.** ffmpeg occasionally drops a frame under load; the binner-vs-video frame count must match. Decide policy: (a) pad binner to match `ffprobe -count_frames`, or (b) truncate `frames.csv` to match. Recommend (a) since input data is what we'd lose with (b).
- **Per-game key normalization.** `translate_sessions.KEY_ALIASES` is hand-curated and English-keyboard-centric. International keyboard layouts (AZERTY, DVORAK, IME-driven) will mis-resolve. Mitigation: capture `GetKeyboardLayout()` into `session.json.system.keyboard_layout` and let downstream normalize.
- **Contributor identity in the spec.** Spec doesn't mention `contributor_id`. We're emitting one regardless; confirm it's OK to keep in `session.json` (it's useful for downstream dedup and abuse handling).
- **Exclusive-fullscreen games.** `gdigrab desktop` does not capture them. The current docs say "use borderless"; we should add a runtime check (compare desktop pixels to a black-frame heuristic for the first second of capture) and warn loudly if every captured frame is black.

---

## Appendix A — File map of the extracted bundle

```
PYZ.pyz_extracted/app/
├── main.pyc                         entry, logging setup
├── core/
│   ├── paths.pyc                    %LOCALAPPDATA% layout
│   ├── state.pyc                    setup_complete flag, state.json
│   ├── contributor.pyc              HMAC email → c_<hex16>
│   ├── process_watcher.pyc          psutil-based exe/PID resolution
│   ├── ffmpeg_recorder.pyc          gdigrab → libx264/CRF20/30fps/1080p
│   ├── ffprobe.pyc                  reads codec/width/height/fps from MP4
│   ├── keyboard_capture.pyc         pynput global hooks → asyncio queue
│   ├── raw_mouse.pyc                Win32 Raw Input on dedicated thread
│   ├── focus_tracker.pyc            5 Hz GetForegroundWindow polling
│   └── session_engine.pyc           orchestrator; merges + writes session
├── ui/
│   ├── main_window.pyc              IDLE/RECORDING modes; uses AsyncRunner
│   ├── setup_window.pyc             first-run wizard
│   ├── async_runner.pyc             QThread/asyncio bridge
│   └── style.pyc                    QSS stylesheet
└── setup/
    └── installer.pyc                gyan.dev ffmpeg downloader/extractor
```

## Appendix B — Quick reference: spec column set HumynCapture must produce

```
frame_id, timestamp_ms,
c2w_m00..c2w_m33  (16 cols, all empty for now),
camera_model, camera_fx, camera_fy, camera_cx, camera_cy  (empty),
camera_radial_k1..k6, camera_tangential_p1, p2            (empty / omitted),
input_keys, input_actions, input_mouse_buttons,
input_mouse_dx, input_mouse_dy
```

Required (per spec, non-camera): `frame_id`, `timestamp_ms`. All others non-required at the spec level, but the binner should always emit `input_*` columns since they're the whole point of HumynCapture's existence.
