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
| A1 | capture path (ddagrab/HW encoder) | `ffmpeg_recorder.py` | **Real-hardware bug found and fixed** (see below) — otherwise unverified end-to-end (no ddagrab capture actually exercised yet) |
| A3/D2 | real fps + health metadata | `ffprobe.py`, `session_engine.py` | Parsing logic **unit-verified** |
| C1 | WASAPI loopback audio | `ffmpeg_recorder.py` | **Unverified**, and per the handoff doc, needs Odyssey confirmation before enabling (`RecorderConfig.audio_enabled` defaults `False`) |
| C2 | black-frame / exclusive-fullscreen | `finalize/blackframe.py` | ffmpeg `blackdetect` wiring **smoke-tested** manually; not tested against a real exclusive-fullscreen title |
| B3/B4/B5/B7 | modifier side, vk pollution, ctrl bytes, L+R bleed | `keyboard_capture.py` | **Unit-verified** for the pure logic. The `pynput.keyboard.Listener(win32_event_filter=...)` wiring that feeds it real scancodes is **unverified** — pynput's Win32 backend can't run on macOS |
| B6 | focus tracking (event-driven) | `focus_tracker.py` | `SetWinEventHook` usage **unverified** (Windows-only API) |
| C3 | finalize timeout / fragmented mp4 | `ffmpeg_recorder.py` | Timeout-scaling logic **unit-testable in principle**, not covered by a test; remux-repair path **unverified** (needs a real truncated fragmented MP4) |

## Partial fixes for the real-hardware sync FAIL and frame-drop WARN (v0.8.0)

A real delivery (`kamla`, 9m14s, `backend=ddagrab`, `encoder=h264_qsv`) still
came back with `qa_status: FAIL` — `controls-to-video sync: video 900.0ms
behind inputs`, `correlation -0.24` — AND `frames_dropped 243/16663 (1.5%)`,
even though this session used the fast GPU capture + hardware encoder path,
not the CPU-only gdigrab/libx264 fallback this tool was mostly hardened
against. Two independent partial fixes below; **both are UNVERIFIED on real
hardware** (no Windows/Intel GPU here) — same disclaimer as everywhere else
in this file.

**Sync (drift-aware anchor).** The existing A2 anchor (`anchor.py`) measures
a SINGLE `(monotonic, encoded)` sample near the start of the recording and
applies one constant correction for the whole session. That can only ever
fix a fixed startup gap — it cannot track drift that grows over the rest of
a long recording. `-fps_mode cfr` forces a constant *output* rate by
duplicating/dropping frames when the real capture can't sustain exactly
`cfg.fps` (the same mechanism behind `frames_dropped`); every duplicate/drop
nudges the video's internal content-time further from real wallclock time.
`_StderrMonitor` now keeps EVERY progress sample from the whole recording
(not just the first), and `anchor.fit_progress_drift` fits a line through
them (`encoded_s = slope * monotonic_s + intercept`). A slope measurably
different from 1.0 is direct evidence of exactly this drift; when the fit is
usable (≥8 samples, slope in a plausible 0.5–1.5 range), the new
`apply_drift_correction` applies that full affine map per-event instead of
one constant shift — falls back to the old single-sample method otherwise.
Sanity-checked with a synthetic 0.15%/s drift: the old method would leave a
500s-in event fully uncorrected, the new fit corrects it by ~750ms — the
same order of magnitude as the real session's 900ms FAIL. Covered by 8 new
tests in `test_anchor.py`.

**Frame drops (QSV zero-copy).** The `ddagrab`+`h264_qsv` pipeline currently
round-trips every frame GPU→CPU (`hwdownload`+software `scale`/`pad`
filters) →GPU (`h264_qsv` re-uploads internally to encode) — that CPU-bound
work runs on every single frame and competes with the game for CPU, a
plausible mechanical cause of encoder-backpressure drops. When capture dims
already match the target (no scale/pad needed — the common native-resolution
case) and a real preflight (`_qsv_zerocopy_opens`, same pattern as
`_ddagrab_opens`/`_encoder_opens`) confirms it, frames now stay in GPU memory
the whole way via `hwmap=derive_device=qsv,format=qsv`, skipping `hwdownload`
and the forced `-pix_fmt yuv420p` (which would otherwise silently reintroduce
the round trip). Falls back to the existing CPU-roundtrip path on any
preflight failure, mismatched dims, or a non-QSV encoder. Covered by 6 new
tests in `test_ffmpeg_recorder.py`.

Neither fix has been run against a real ddagrab+QSV capture yet — the sync
fix's math is sanity-checked synthetically above; the zero-copy path's
actual effect on drop rate can only be confirmed on the same real machine
that produced the 243/16663 number.

## Issue #2 — camera pose/intrinsics data (v0.9.0)

Every real delivery checked so far had `frames.csv`'s camera columns
(`c2w_m00`..`m33`, `camera_fx/fy/cx/cy`, distortion coefficients) 100% empty
— not corrupted, never captured: HumynCapture only sees screen pixels + OS
input, never the game engine's real camera. Fixing this needed something
that runs INSIDE the game process, which is a different kind of fix than
everything else in this repo:

- **`unity_plugin/CameraLogger/`** — a BepInEx plugin (game confirmed Unity
  Mono build via a real on-machine check: `MonoBleedingEdge\` present, no
  `GameAssembly.dll`) that samples `Camera.main`'s transform every frame and
  writes it to `%LOCALAPPDATA%\HumynCapture\camera_bridge\<pid>.jsonl`,
  keyed by the game's own pid (already recorded in `metadata.json` as
  `game.pid_at_capture` — the only handshake that works given HumynCapture
  attaches to an already-running game rather than launching it). Designed
  to be game-agnostic: the same compiled DLL should work unmodified for any
  other Mono-build Unity title, only the injection setup repeats per game
  (IL2CPP titles need a different BepInEx variant — see
  `unity_plugin/README.md`'s per-title checklist).
  **UNVERIFIED** — no Unity/BepInEx/.NET toolchain available here to
  compile or inject it for real.
- **`app/core/finalize/camera_bridge.py`** — reads that log, converts
  Unity's position+quaternion into the delivery's camera-to-world matrix
  format (no axis conversion — Unity's own left-handed X-right/Y-up/
  Z-forward convention already matches the client's spec), derives
  `fx=fy`/`cx`/`cy` from the logged FOV (satisfies the client's own stated
  acceptance criterion, spec §4.3#8: "camera_intrinsics parameters, fx =
  fy"), and patches `frames.csv` in place for every frame with a
  close-enough-in-time sample (frames with no close sample are left blank,
  never guessed at). **Unit-tested — 17 passing tests**
  (`test_camera_bridge.py`), this half is trustworthy independent of the
  plugin.
- Wired into `finalize/pipeline.py`: runs automatically after
  `translate_bundle_v2` if a camera log exists for the session's pid;
  silently no-ops (columns stay blank, exactly as today) if it doesn't —
  never blocks finalize for a title without the plugin installed.

**Next real step**: run `unity_plugin/README.md`'s injection checklist
against the real `Kamla.exe` to confirm BepInEx actually loads the plugin,
then a real recording to confirm `camera_bridge.py` correctly merges its
output — this cannot be verified further without that hardware.

## ddagrab: listed and correctly invoked, still needed a real preflight

Immediate follow-up to the detection/invocation fix above — same lesson as
A1's hardware-encoder fix, just for ddagrab this time. Real error from an
actual machine, with the *fixed* invocation already in place:
```
[Parsed_ddagrab_0] Selected output not supported
[Parsed_ddagrab_0] Failed to configure output pad on Parsed_ddagrab_0
Error opening input: Generic error in an external library
```
The filter was correctly detected (`-filters` lists it) and correctly
invoked (proper lavfi syntax) — and it still failed to actually open.
`output_idx=0` doesn't reliably map to a usable DXGI output on every GPU/
monitor configuration (hybrid-GPU laptops and certain multi-monitor setups
are the likely cause here). "Listed" and "opens" are different questions
for ddagrab exactly the way they were for `h264_nvenc`.

Added `_ddagrab_opens()` — a real preflight (tiny 64x64, 3-frame synthetic
capture to `-f null -`) — and `_probe_ddagrab_support()` now requires both
the filter being listed AND that preflight succeeding before committing to
ddagrab, falling back to `gdigrab` otherwise. Covered by 3 new tests.

## ddagrab was never actually usable — wrong detection AND wrong invocation

Found while answering "can we add exclusive-fullscreen support" — checked
whether `ddagrab` (the A1/C2 fix's preferred capture path, which genuinely
can capture exclusive fullscreen via DXGI Desktop Duplication) was actually
working as designed, and it wasn't, for two compounding reasons:

1. `_probe_ddagrab_support` checked `ffmpeg -devices` for the string
   "ddagrab". **`ddagrab` is an avfilter SOURCE, not an avdevice** — it's
   listed under `-filters`, never `-devices` (confirmed against ffmpeg's
   own filter docs). This means detection returned `False` on **every**
   machine, including ones whose bundled ffmpeg build genuinely supports
   ddagrab — silently forcing the `gdigrab` fallback (and its C2
   exclusive-fullscreen black-capture limitation) universally, not just on
   machines that actually lack it.
2. Even with detection fixed, the invocation itself (`-f ddagrab -framerate
   N -i 0`) uses device-style syntax that doesn't correspond to how the
   filter works at all. The real syntax is `-f lavfi -i "ddagrab=
   output_idx=0:framerate=N:video_size=WxH:offset_x=X:offset_y=Y"` — cropping
   is a native parameter of the filter itself, so the separate `crop=`
   filter this code also added was unnecessary.

Fixed both: `_probe_ddagrab_support` now checks `-filters`; `_build_command`
builds the correct lavfi filter string. `ddagrab` has been a built-in
ffmpeg filter (uses only Windows DXGI APIs, no external library) since
ffmpeg 6.0, and the bundled build here is 8.1.2 — so this machine's ffmpeg
almost certainly does support it, meaning this bug was likely the entire
reason gdigrab was being used at all in this project's testing so far, not
a genuine hardware limitation. **Not yet confirmed with a real recording**
— need one more test session to see `capture_health.backend: "ddagrab"`
and confirm it actually captures exclusive fullscreen correctly. Covered by
4 new tests in `test_ffmpeg_recorder.py` mocking the subprocess/filter
listing (the detection and command-building logic, not real ffmpeg
execution, which needs Windows either way).

## Windowed mode at native resolution overflows the monitor edge

Found the moment the user switched Outer Wilds from exclusive Fullscreen to
Windowed (to fix the black-capture C2 issue) — new failure: `ffmpeg exited
immediately`, `Capture area (11,45),(3851,2205) extends outside window area
(-1920,0),(3840,2160)`. Windows adds a title bar (~45px) and border (~11px)
on top of a window's client area — a game running "windowed" at exactly the
monitor's native resolution (3840x2160) ends up with its whole window
(client + decorations) a few pixels taller/wider than the monitor itself.
`gdigrab`'s `-i desktop` source is the full virtual desktop (which includes
negative coordinates here — a second monitor sits left of the primary), and
it hard-fails rather than clip when the requested capture region extends
past it.

Fixed by clamping the capture rect to the real virtual desktop bounds
(`GetSystemMetrics(SM_XVIRTUALSCREEN/SM_YVIRTUALSCREEN/SM_CXVIRTUALSCREEN/
SM_CYVIRTUALSCREEN)`) before handing it to ffmpeg, in
`SessionEngine._get_window_screen_rect`. The clamping math itself
(`_clamp_rect_to_bounds`) is a pure function taking bounds as plain
integers, specifically so it's unit-testable without any Win32 call —
covered by 4 tests in `test_session_engine.py`, including the exact
real numbers from this failure (a window overflowing a monitor's right edge
when a second monitor sits to its left, which is why the virtual desktop's
left edge is negative here).

## Caught before shipping: `\r` vs `\n` would have undermined the A2 fix above

Self-review, not user testing, caught this one — worth being explicit about
since everything else in this log has come from real hardware. ffmpeg's
live `-stats` progress output is `\r`-terminated (it overwrites one
terminal line repeatedly), not `\n`-terminated. `_StderrMonitor._run()`
used `iter(stream.readline, b"")`, which only splits on `\n` — it would
have silently buffered several `\r`-separated stats updates together and
only captured `time.perf_counter()` once an unrelated real `\n` eventually
arrived, adding unpredictable delay to the exact timestamp the new A2 fix's
sub-50ms precision depends on. Rewrote `_run()` to read raw bytes and split
on either `\r` or `\n`, so each stats update is timestamped the instant it
arrives. Covered by a new test (`test_splits_on_carriage_return_not_just_
newline`) using `\r`-only stream bytes with no `\n` at all, which would
have hung/misbehaved under the old `readline()`-based implementation.

## A2's real fix: the ffprobe/wallclock approach is replaced, not just guarded

The previous round's implausibility guard did its job — it caught the
broken assumption and refused to apply a multi-year-wrong correction — but
that meant A2's actual sync bug was still **unfixed in practice** on every
real session (`time_anchor: "unavailable"`, `correction_applied_us: 0`,
QA `FAIL: controls-to-video sync self-test FAILED (|lag| > 50ms target)`
every time). Confirmed directly from a real log: `frame0_wall=0.066667`
against `launch_wallclock=1785439320.11...` — the muxed file's frame0 PTS
is relative/near-zero, not epoch-scale, regardless of
`-use_wallclock_as_timestamps 1`. ffmpeg's own `-avoid_negative_ts` output
normalization resets it, and there's no flag used here that prevents that.

Rather than fight the container's timestamp normalization, A2 now uses the
handoff doc's other suggested approach: **parse ffmpeg's own live `-stats`
progress output.** `_StderrMonitor` (already tailing stderr for `frame=`/
`drop=` since A1) now also captures, on the first `time=` line ffmpeg
prints, the pair `(time.perf_counter() at the instant we read that line,
the time= value itself)`. Since `time=T` at real monotonic instant `M`
means "the video's own t=0 occurred at `M - T`" — true for *any* progress
line, algebraically — this never depends on what the container's PTS
becomes after muxing. No wallclock assumption, nothing to read back from a
file, nothing for a muxer to normalize away.

`compute_anchor_correction` now tries this first (`method:
"ffmpeg_progress_time"`), only falling back to the old ffprobe/wallclock
path (with its implausibility guard still in place) if `_StderrMonitor`
never captured a progress line at all. Covered by 4 new tests in
`test_ffmpeg_recorder.py` (progress-line parsing) and 2 new tests in
`test_anchor.py` (preference + fallback behavior).

**This is the real fix for the client's original P0 complaint** — everything
before this was either reconstructing the tool or catching a broken
assumption safely. Still needs a real recording to confirm the actual
measured `|lag|` finally lands under the 50ms target; that's the next
thing to check once this is rebuilt.

## Finalize step wasn't offloaded to a thread either

Same class of bug as `recorder.start_async`/`stop_async` a few rounds back:
`compute_anchor_correction` and `run_finalize` were both called
synchronously inside `SessionEngine.run`'s coroutine, not via
`asyncio.to_thread`. `run_finalize` in particular decodes and runs dense
optical flow over up to 3600 frames for the sync self-test, then does an
independent second pass inside `check_session_v2` — genuinely CPU-heavy,
legitimately slow on a long recording. Wrapping it in `to_thread` doesn't
make that work faster (it's real work, not overhead), but keeps the event
loop free instead of blocking it for the whole duration, consistent with
every other heavy call in this method. Not covered by a new test (same
reasoning as the earlier recorder fix — this is a threading-hygiene
change, not new logic).

## A2 anchor: ffprobe parsing bug, plus a guard against a deeper assumption

Diagnosable directly from `humyncapture.log` once the earlier logging fix
landed — exactly the payoff that fix was for:

```
first_frame_pts_wallclock_s failed: could not convert string to float: '0.066667,'
```

ffprobe actually succeeded; `-of csv=p=0` with a single selected field still
emits a **trailing comma** on this ffprobe build (`"0.066667,"`, not
`"0.066667"`), and `float()` rejected it outright. Fixed by taking the first
comma-separated field instead of assuming the line is a bare number.

That surfaced a second, deeper concern: `0.066667` is a *relative* number
(~2 frames in at 30fps), not wallclock/epoch-scale (`time.time()` is
~1.78 billion right now) — meaning the assumption this whole anchor strategy
depends on (`-use_wallclock_as_timestamps 1` survives into the muxed file's
PTS) may not hold once ffmpeg's output muxing normalizes timestamps to
start near 0, which is default behavior for most containers
(`avoid_negative_ts`). If that's what's happening, the naive correction
would be off by **years**, silently corrupting every event timestamp instead
of failing loudly. Added a sanity guard in `compute_anchor_correction`:
corrections beyond a wide, generous margin (10s — the handoff doc's own
evidence puts a real correctly-anchored gap at sub-second/a few frames) are
rejected as `"unavailable"` rather than applied, with a warning logged
explaining why. Covered by 4 new tests in `tests/test_anchor.py`.

**Still open**: whether the underlying assumption is actually broken (in
which case A2 needs a different strategy — e.g. parsing ffmpeg's own
`-progress` stderr output for the real first-frame instant, the handoff
doc's alternative option) can only be confirmed once the parsing fix lands
and a real correction number comes back. If `humyncapture.log` still shows
`time_anchor: "unavailable"` with a warning about an implausible
correction after rebuilding, that confirms the deeper issue and the anchor
strategy itself needs to change.

## session.rrd was never actually being generated

Found by inspecting a real finalized delivery: `rrd_creation.py` was there,
`session.rrd` was not — a required v2 delivery file silently missing, with
no error shown to the user. Two separate bugs in `translator/rrd.py`
(pre-existing, shared code — not something in `capture_tool/` itself):

1. `write_script()` wrote the script with `Path.write_text(RRD_SCRIPT)` —
   no `encoding="utf-8"`. The script contains "§" and "—"; without an
   explicit encoding, Windows defaults to something like cp1252, which CAN
   represent both characters but as bytes that are **not valid UTF-8** —
   confirmed on the real delivered file, which read back as "�" mojibake
   everywhere those characters appeared.
2. `generate()`'s only code path shells out to `[sys.executable, script,
   "--session-dir", ...]` to actually produce `session.rrd`. Inside a
   frozen PyInstaller exe, **`sys.executable` is the frozen exe itself**,
   not a Python interpreter — that subprocess call doesn't run the script,
   it tries to relaunch HumynCapture. `check=True` should have made this
   raise loudly, but nothing did; either way, `session.rrd` never got
   produced.

Fixed `write_script()` to specify `encoding="utf-8"` explicitly, and added
`generate(..., in_process=True)`, which imports and calls the written
script's `log_session()` directly — no subprocess, no dependence on a
Python interpreter existing on disk. `capture_tool/app/core/finalize/
pipeline.py` now passes `rrd_in_process=True`. Also added `rerun-sdk` to
`capture_tool/requirements.txt`, which the frozen build needs to actually
import `rerun` for this — it was missing entirely; the subprocess bug had
been masking that gap too, since it also would have failed for that reason.
Covered by 2 new tests in `translator/tests/test_rrd.py`.

**Why nothing caught this earlier**: every test here runs under a normal
Python interpreter, where `sys.executable` genuinely is a Python
interpreter — the subprocess call "works" in every test environment and
only breaks specifically inside a frozen exe. This is a second case (after
the `HumynCapture.spec` `REPO_ROOT` bug) of something no amount of testing
on this machine could have caught; it needed an actual delivered file to
inspect.

## Recent-sessions list was looking in the wrong place

Found while helping locate a real session's finalized output: I initially
told the user the finalized delivery sits as a flat sibling of `<session_id>
_raw\` — wrong. `translator.v2.translate_bundle_v2` (called from
`finalize/pipeline.py`) writes it nested under `<SESSIONS_DIR>/<vendor>/
<mm-dd-yyyy>/<game_slug>/<session_id>/` — that's `translator/v2.py`'s own
layout, not something decided in this package. `MainWindow.
_refresh_sessions_list` only globbed `SESSIONS_DIR`'s top level, so in
practice it found nothing but the `<vendor>\` folder itself (e.g.
"humynlabs") and would have listed that folder as if it were a session,
while every real finalized session nested inside it stayed invisible in
the "Recent sessions" list. Fixed to `SESSIONS_DIR.rglob("session.json")`,
which finds them regardless of nesting depth. Also fixed `paths.py`'s
layout docstring, which documented the same wrong flat assumption. Covered
by `tests/test_main_window_sessions_list.py`.

## `list_likely_games` restored verbatim (real game missing from dropdown)

Same root cause as the UI reconstruction below, different file:
`process_watcher.list_likely_games`'s bytecode never decompiled either, so
it had been reinvented from scratch — a small ~20-entry exclusion list, and
(the actual bug) **deduplicating results down to one entry per exe name**,
which the real implementation never does. A real running game (Outer
Wilds) went missing from the "Game .exe" dropdown as a result. Restored
verbatim from `pycdas` disassembly instead of continuing to guess: the real
exclusion list has ~90 entries (launchers, overlays, anti-cheat/DRM
helpers), there's no dedup, and an `is_gamey` heuristic (exe path under
`steamapps`/`epic games`/etc.) affects sort order only, never exclusion.
Also kept the defensive fix from the same investigation: one unreadable
process (`psutil` raising something other than `NoSuchProcess`/
`AccessDenied` — plausible for anti-cheat-protected processes) no longer
aborts the whole scan and returns an empty list. Covered by 5 new tests in
`tests/test_process_watcher.py`.

## UI reconstruction pass (prompted by comparing against the real shipped exe)

`app/ui/main_window.py`, `setup_window.py`, `async_runner.py`, and
`style.py` all had class bodies that failed to decompile entirely (pynput's
async/closure-heavy code isn't unique here — PySide6 GUI code hit the same
`pycdc` gaps). The versions previously in this repo were written from
scratch against only each module's docstring, and — once actually compared
side-by-side against a real recording of the shipped exe — visibly didn't
match: different window size, no status bar, no live process-list refresh,
no recording timer, wrong color palette (blue accent instead of the real
orange), no card/section-title/primary/danger style classes, different
method and attribute names throughout, and a completely different `_on_done`
flow in the setup wizard (real app has separate `done_signal`/`error_signal`
paths, not one combined handler).

All four files are now reconstructed from `pycdas` bytecode disassembly of
the shipped exe (Names/Constants tables + manual opcode trace per method),
which — unlike `pycdc`'s failed decompilation — recovers the real structure
exactly. Verified by rendering both `MainWindow` states and `SetupWizard`
off-screen (`QT_QPA_PLATFORM=offscreen`) and inspecting the actual pixels,
not just that the code imports.

Two real integration bugs surfaced *by doing this comparison*, independent
of anything Windows-specific:
- `session_engine.py` emitted a `"recording"` stage name; the real UI's
  timer-start logic checks for `"playing"` — these never decompiled either,
  so this string was invented when session_engine.py was first written and
  never matched what the UI (once its real behavior was known) expects.
  Fixed to `"playing"`.
- `process_watcher.list_likely_games()` returned `list[str]`; the real
  `_refresh_running_games` indexes into each item as `proc['name']`/
  `proc['pid']` and uses the exe name as the combo box's itemData (not the
  display label, which includes the PID). Fixed to return `list[dict]`.

Kept as deliberate deviations from the original (not gaps): the game field
is a dropdown (D1 fix — the original's free-text `QLineEdit` here is
literally the bug D1 exists to fix), and the session-finished dialog now
includes QA status/self-check failures (E2 fix — the original had no
self-check at all). Both are commented in `main_window.py` where they occur.

## Real-hardware findings (from actual Windows testing)

- **`_get_window_screen_rect` produced a 0x0 capture rect.** ffmpeg failed
  with `Unable to parse "video_size" option value "0x0"`. Root cause:
  `_get_window_screen_rect` (and `_get_window_for_pid`'s candidate scoring)
  called `GetClientRect`/`ClientToScreen` without checking their return
  values, and never validated the result. A minimized window's client rect
  is *legitimately* `(0,0,0,0)` in Win32 (not a failure return), so a
  minimized or not-yet-rendered game window silently produced a zero-size
  rect that only surfaced as a cryptic native ffmpeg error two layers away.
  Also fixed a related bug in `_get_window_for_pid`: it skipped any window
  with an empty title outright, but many real fullscreen/borderless game
  windows legitimately have no title text — that could leave a tiny/hidden
  helper window as the only remaining candidate. Both functions now check
  `IsIconic`/zero-area and raise a clear, actionable error ("the game window
  is minimized — restore it into view...") instead of passing a degenerate
  rect through to ffmpeg. **Not covered by a test** — both functions are
  `sys.platform == "win32"`-gated with no Windows environment here to
  exercise the real Win32 calls; this is source-reviewed, not test-verified,
  same caveat as the session-engine threading fix two rounds ago.
  Every finalized session failed at "Writing delivery files..." with
  `the sibling 'translator' package is not importable`. Root cause was in
  the spec file, not the fixed source: `SPECPATH` is *already* the directory
  containing the `.spec` file (`.../hl-gamedata/capture_tool`), not the
  file's own path — the spec computed `REPO_ROOT = Path(SPECPATH).parent.
  parent`, going one level past the actual repo root where `translator/`
  lives, so it was silently never added to PyInstaller's analysis path,
  never bundled, and `import translator` failed at runtime exactly as
  `_load_translator()`'s error handling was designed to report. Fixed to a
  single `.parent`. **This can't be caught by the pytest suite** (it's a
  PyInstaller packaging concern, not application logic) — only a real build
  + real session exercises it, which is exactly what caught it.

- **A1, encoder-open vs. encoder-listed.** First real recording on a Windows
  box with an older NVIDIA driver failed the whole session: `h264_nvenc` was
  listed in `ffmpeg -encoders` (compiled in) but failed to *open*
  ("Driver does not support the required nvenc API version. Required: 13.1
  Found: 12.2"), and `_detect_hw_encoder`'s original name-string check had no
  way to know that ahead of time. Fixed in `ffmpeg_recorder.py`:
  `_encoder_opens()` now runs a real preflight encode (tiny synthetic lavfi
  input through the candidate encoder to `-f null -`) before committing to
  it, falling through `h264_nvenc -> h264_qsv -> h264_amf -> libx264` on the
  first one that actually opens. Covered by
  `tests/test_ffmpeg_recorder.py` (4 tests, mocking the subprocess calls).
  This is the first defect in this package to get feedback from a real
  machine — treat every other "unverified" row above as *equally* likely to
  have a gap like this until it's actually run.

- **B2/E1/E2 self-check, pynput `Listener.wait()` API misuse.** First real
  keyboard/mouse capture failed with `AbstractListener.wait() takes 1
  positional argument but 2 were given`, surfaced correctly by the B2 health
  gate as a subsystem warning (that part worked) but the underlying cause was
  a real bug in `keyboard_capture.py`: pynput's `Listener.wait()` takes **no
  arguments** — it has no built-in timeout support. The code (both the
  original version here and an earlier attempted fix in this same file) called
  it as `.wait(timeout=3)`, which is a `TypeError`, not a timeout. This was
  almost certainly the actual cause of an earlier vague "keyboard and mouse
  input not recognized, timeout" report, misdiagnosed at the time as a
  possible AV/EDR or elevation issue since both subsystems failed together —
  they failed together because the exception fired before either listener's
  readiness could be checked at all, not because of an external block. Fixed
  by polling pynput's documented public `.running` attribute with our own
  timeout loop (`_wait_listener_running`) instead of relying on `.wait()`.
  Covered by `tests/test_input_capture_start.py` (3 tests, using a fake
  listener with only a `.running` attribute and deliberately no `.wait()`, so
  a regression back to calling `.wait(timeout=...)` fails loudly here too).

- **B4, OS/system keys not actually filtered.** A real `inputs.jsonl`
  capture had `cmd` and `print_screen` as its first two events — the module
  docstring explicitly claims these are "dropped at capture instead of
  leaking into inputs.jsonl", but they weren't. Root cause: `_OS_SYSTEM_VKS`
  only guards the path where pynput hands back a `KeyCode` with a numeric
  `.vk` — but Win/cmd, PrintScreen, lock keys, and media keys arrive from
  pynput as **named `Key` enum members** (`Key.cmd`, `Key.print_screen`, …),
  which have no `.vk` at all, so `_key_to_str`'s final fallback
  (`return key.name`) returned them completely unfiltered. Added
  `_OS_SYSTEM_KEY_NAMES` (name-keyed, mirroring `_OS_SYSTEM_VKS`) and checked
  it in that fallback branch. Covered by 4 new tests in
  `tests/test_keyboard_capture.py`, using a fake object with only a `.name`
  attribute (not real `pynput.keyboard.Key.print_screen`/`.media_volume_mute`
  — pynput's per-platform backend doesn't define every Windows-only member
  on macOS, which is exactly the kind of platform gap this whole package is
  full of).

- **Native v2 finalize, `[WinError 2] The system cannot find the file
  specified`.** `translator/{trim,video,rrd}.py` invoke bare `"ffmpeg"`/
  `"ffprobe"` (correct for the translator package's own CLI/dev usage, where
  a system ffmpeg on PATH is the right assumption) — but the packaged app
  has no system-wide ffmpeg on an end-user Windows machine, only the copy
  the setup wizard downloads under `%LOCALAPPDATA%\HumynCapture\ffmpeg\`.
  Every `translator` call during finalize (trim, probe, rrd generation) was
  raising this uncaught. Fixed with `app.core.paths.ensure_ffmpeg_on_path()`
  — prepends the bundled binary's directory to `PATH` — called at the top of
  `finalize/pipeline.run_finalize()`, before any `translator` call. Covered
  by `tests/test_paths.py` (2 tests).

- **Session-start/-stop UI stall ("glitching when recording starts").**
  `SessionEngine.run()` called `recorder.start(...)` and `recorder.stop()`
  — the plain **synchronous** methods — directly inside an `async def`,
  even though `FFmpegRecorder` already defines `start_async`/`stop_async`
  wrappers (`asyncio.to_thread`) for exactly this. `start()` now also runs
  the A1 encoder preflight (up to 3 sequential subprocess calls, up to 10s
  each) before launching ffmpeg, and `stop()` can block up to
  `MAX_FINALIZE_TIMEOUT_S` (120s) — called synchronously, either blocks the
  single AsyncRunner event-loop thread for its full duration, starving every
  other coroutine scheduled on it (status/progress signals, the input-queue
  drain task) for that whole window. Fixed by switching both call sites in
  `session_engine.py` to `await recorder.start_async(...)` /
  `await recorder.stop_async()`. **Not covered by a test** — `SessionEngine.
  run()` has enough remaining Windows-only surface (window-finding, all four
  input/health subsystems) that mocking just this slice wasn't worth it
  given everything else already unverified in this method; this fix is
  code-reviewed, not test-covered.

## Verified vs. unverified — what "verified" means here

**81 pytest tests, all passing** (`capture_tool/tests/`), on macOS with
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
