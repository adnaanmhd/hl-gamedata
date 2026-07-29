# HumynCapture — reconstructed + fixed source

This directory is the reconstructed, fixed source of `HumynCapture.exe` (the
Windows PySide6 capture tool), produced per
[`HumynCapture_V2_Fix_Handoff.md`](../HumynCapture_V2_Fix_Handoff.md) and
[`HumynCapture_Capture_Tool_Issues.md`](../HumynCapture_Capture_Tool_Issues.md).

**How it was produced.** `HumynCapture.exe` had no source available, so it
was reverse-engineered: extracted with `pyinstxtractor-ng`, decompiled with
`pycdc` (built from source, Python 3.12 bytecode support), then hand-repaired
where decompilation was incomplete (async functions, closures, and a few
Win32-struct-heavy methods came back as `# WARNING: Decompyle incomplete`) —
cross-referenced against the two issues docs above, which already documented
each defect's exact root cause and code location from an earlier pass over
the same binary.

**Read this before trusting any specific claim below**: every fix here was
written and unit-tested on macOS, with no Windows machine, no GPU, no real
game, and no ffmpeg build with `ddagrab`/hardware-encoder support actually
available to exercise. See "Verified vs. unverified" — don't ship this
straight to a release build without the acceptance protocol in handoff §7.

## Status by defect

| id | defect | file(s) | status |
|---|---|---|---|
| A2 | input/video clock anchor | `app/core/finalize/anchor.py`, `ffmpeg_recorder.py` | **Logic verified** (unit + synthetic e2e). Real |lag| ≤ 50ms **not** verified — needs a real game + `action_video_grounding.py` |
| — | native v2 emission | `app/core/finalize/pipeline.py`, `session_engine.py` | **Verified end-to-end** against a synthetic capture — see below |
| B1 | raw mouse silent failure | `raw_mouse.py`, `session_engine._merge_inputs` | Fallback/retry logic **unit-verified**. Win32 Raw Input registration itself **unverified** (no Windows) |
| B2/E1/E2 | health checks, self-check gate | `health.py`, `session_engine.py` | **Unit-verified** (12 tests) |
| D1 | canonical game_title | `games.py` | **Unit-verified** |
| A1 | capture path (ddagrab/HW encoder) | `ffmpeg_recorder.py` | **Unverified** — no Windows GPU/ddagrab build to test against |
| A3/D2 | real fps + health metadata | `ffprobe.py`, `session_engine.py` | Parsing logic **unit-verified** |
| C1 | WASAPI loopback audio | `ffmpeg_recorder.py` | **Unverified**, and per the handoff doc, needs Odyssey confirmation before enabling (`RecorderConfig.audio_enabled` defaults `False`) |
| C2 | black-frame / exclusive-fullscreen | `finalize/blackframe.py` | ffmpeg `blackdetect` wiring **smoke-tested** manually; not tested against a real exclusive-fullscreen title |
| B3/B4/B5/B7 | modifier side, vk pollution, ctrl bytes, L+R bleed | `keyboard_capture.py` | **Unit-verified** for the pure logic. The `pynput.keyboard.Listener(win32_event_filter=...)` wiring that feeds it real scancodes is **unverified** — pynput's Win32 backend can't run on macOS |
| B6 | focus tracking (event-driven) | `focus_tracker.py` | `SetWinEventHook` usage **unverified** (Windows-only API) |
| C3 | finalize timeout / fragmented mp4 | `ffmpeg_recorder.py` | Timeout-scaling logic **unit-testable in principle**, not covered by a test; remux-repair path **unverified** (needs a real truncated fragmented MP4) |

## Verified vs. unverified — what "verified" means here

**42 pytest tests, all passing** (`capture_tool/tests/`), on macOS with
`pynput`/`PySide6`/`psutil`/`numpy`/`opencv-python-headless`/`rerun-sdk`
installed:

- Every module **imports cleanly** (`app.core.*`, `app.ui.*`, `app.main`),
  including the full PySide6 GUI tree (`QT_QPA_PLATFORM=offscreen`) — this
  is real evidence the reconstruction is syntactically and structurally
  sound, not a guess.
- Pure logic is unit-tested: B3's scancode-based L/R modifier resolution,
  B5's control-byte-to-letter mapping, B7's debounce window, B1's
  `ts_monotonic_ns` fallback (previously-dropped records now survive), D1's
  exe-name-is-authoritative resolution, the full B2/E1/E2 self-check gate
  (12 cases), A2's timestamp-correction arithmetic, A3's fps-rational
  parsing.
- **`test_finalize_integration.py` runs the real, un-mocked finalize
  pipeline** — `app.core.finalize.pipeline.run_finalize` calling straight
  into the *actual* `translator.v2.translate_bundle_v2` +
  `translator.v2.check_session_v2` — against a synthetic ffmpeg-encoded
  video + fabricated `inputs.jsonl`/`metadata.json`. Last run:
  `qa_status=WARN` (the only warnings are frame-spacing jitter and
  "sync correlation too weak" — both *expected* for a synthetic video with
  no real correlation between its motion and the fake mouse deltas), an
  **exact off-by-one match on all 2152 rows** independently re-binned from
  raw events, and the synthetic "w" key press correctly resolving to
  `movement_move_y_axis` through Outer Wilds' real keybind table. This
  proves the trim -> anchor-correct -> bin -> write-v2 -> QA pipeline is
  wired correctly end to end.

**What is categorically NOT verified**, because nothing in this environment
can exercise it:

- Any real Win32 API call (`RegisterRawInputDevices`, `SetWinEventHook`,
  `EnumWindows`, `ddagrab`, WASAPI) — these are `sys.platform == "win32"`-
  gated and were never executed, only read for correctness against
  documented Win32 semantics (e.g. AT scancode set-1: LShift=0x2A,
  RShift=0x36, Ctrl/Alt sides via the extended-key bit — this is standard,
  driver-level Windows behavior, not vendor-specific guessing, but it has
  not been fired against a real keyboard hook).
- Whether `ddagrab`/`h264_nvenc`/`h264_qsv`/`h264_amf` are actually present
  and correctly invoked by the bundled ffmpeg on a target machine (A1).
- The handoff doc's actual acceptance protocol (§7): a real capture of
  Kamla/Outer Wilds on strong/weak machines, `action_video_grounding.py`
  reporting `|lag| ≤ 50ms` with no post-correction, forced-failure drills
  (kill ffmpeg mid-record, block the raw input window, run an
  exclusive-fullscreen title). **None of this can run without a Windows
  box, the actual games, and a GPU.**
- `pynput`'s `win32_event_filter` field names (`data.vkCode`/`.scanCode`/
  `.flags`) are used per pynput's documented Windows backend contract, but
  were never exercised against a live hook (pynput's Win32 backend simply
  doesn't run on macOS).

## Design deviations from the handoff doc (and why)

- **B6 uses its own message-pump thread** rather than sharing raw_mouse.py's
  window thread (the doc's "the same thread can host the Raw Input window"
  suggestion). Two threads is simpler to reason about/test independently;
  consolidating them is a legitimate follow-up optimization, not a
  correctness requirement.
- **C3 drops `+faststart` in favor of `+frag_keyframe+empty_moov+
  default_base_moof`** unconditionally, not just as a kill fallback — a
  fragmented MP4 is valid without needing the "clean stop" path at all,
  which is a strictly stronger crash-safety guarantee than "faststart, with
  a repair attempt if killed." The tradeoff is losing true zero-remux
  streamability; delivery already goes through the trim step (a remux)
  regardless, so this costs nothing in the current pipeline.
- **The v2 writer is not a from-scratch reimplementation** — it's
  `app.core.finalize.pipeline` calling directly into
  `translator.v2.translate_bundle_v2` / `check_session_v2`, per the handoff
  doc's own instruction that `translator/v2.py` is "the reference
  implementation of both writer and validator." This avoids a second,
  divergence-prone copy of trim/bin/write/QA logic.

## Building the .exe

**This must be done on a real Windows machine.** PyInstaller packages for
whatever OS it runs on — it does not cross-compile. Running `pyinstaller` on
this Mac would produce a macOS binary, not a `.exe`; I haven't run this build
myself for that reason. The steps below are correct as far as static
analysis of this source tree goes, but — like everything else in this
package — treat the *first* real build as a verification step, not a
formality: watch for `ModuleNotFoundError` on first launch of the built exe,
which means a hidden-import is missing, and add it to `HumynCapture.spec`.

1. **Windows 10/11 machine**, Python **3.12** (matches the original bundle;
   `ctypes.wintypes` layouts and struct packing in `raw_mouse.py` /
   `focus_tracker.py` were written against 3.12's ABI).
2. From the **repo root** (`hl-gamedata/`, one level above this directory —
   the build needs to see both `capture_tool/` and `translator/`):
   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\activate
   pip install -r capture_tool\requirements.txt
   pip install pyinstaller pyinstaller-hooks-contrib
   ```
   `pyinstaller-hooks-contrib` supplies maintained hooks for PySide6 and
   pynput (Qt plugins, platform backends) so you don't have to hand-enumerate
   every DLL/plugin the way `HumynCapture.spec`'s `hiddenimports` does for
   the `translator` package.
3. Build:
   ```powershell
   cd capture_tool
   pyinstaller HumynCapture.spec
   ```
   Output: `capture_tool\dist\HumynCapture\HumynCapture.exe` (onedir, not
   onefile — matches the original bundle's loose-files-next-to-the-exe
   layout, which is easier to debug if a plugin/DLL is missing than
   unpacking a single-file bundle every launch).
4. **First-run smoke test** (still on Windows, before trusting it with a
   real capture): launch the exe, let the setup wizard download ffmpeg
   (`app/setup/installer.py`, unchanged from the original — pulls
   `gyan.dev`'s essentials build), then run a short recording of anything
   (even a windowed desktop app) and confirm `session.json` + `frames.csv` +
   `video.mp4` + `session.rrd` land under
   `%LOCALAPPDATA%\HumynCapture\sessions\<id>\` and
   `humyncapture.log` has no `ERROR`/`CRITICAL` lines.
5. Only after step 4 passes, move to the handoff doc's real acceptance
   protocol (§7): actual Kamla/Outer Wilds captures,
   `action_video_grounding.py` against them, forced-failure drills. None of
   that is satisfied by a clean build — a clean build only proves the code
   runs, not that A2's ≤50ms target or A1's frame-drop fix actually hold on
   real hardware.

`numpy` + `opencv-python-headless` (already in `requirements.txt`) must ship
in the bundle for the in-tool sync self-test (finalize step §6 of the
handoff doc) to run; their absence degrades that one check to a warning, not
a crash — see `translator/sync.py:available()`.

## Running the tests

```bash
cd capture_tool
pip install -r requirements.txt pytest
QT_QPA_PLATFORM=offscreen PYTHONPATH=.:.. python3 -m pytest tests/ -v
```

(`PYTHONPATH` needs both this directory for `app.*` and the repo root for
`translator.*`; `tests/conftest.py` also inserts both automatically.)
