# translator — HumynCapture → Odyssey delivery bundle (spec v1)

Converts raw HumynCapture sessions into the canonical Game Data Capture format,
correctly and reproducibly. Replaces the old `translate_samples.py` /
`translate_sessions.py` scripts.

## Install / run

Pure stdlib except `ffmpeg`/`ffprobe` (on PATH) and `rerun-sdk` (only for the
`.rrd` QA step). From the repo root:

```bash
# raw capture bundle(s) -> delivery (applies the implicit 5s head/tail trim)
PYTHONPATH=. uv run --with rerun-sdk --with numpy python -m translator translate \
    path/to/<session-id>/ --out out/

# rebuild an already-delivered session in place (no trim)
PYTHONPATH=. uv run --with rerun-sdk --with numpy python -m translator reprocess \
    "Game Samples"/<game>/<session-id>/

# validate against spec + guidelines
PYTHONPATH=. python -m translator qa "Game Samples"/<game>/<session-id>/

# tests
PYTHONPATH=. uv run --with pytest pytest translator/tests -q
```

## Input (raw capture bundle)

```
<session-id>/
  video.mp4        # screen recording
  inputs.jsonl     # event stream: {t(µs), type: key|mouse_button|mouse_raw|...}
  metadata.json    # HumynCapture metadata (game name, recording, system, …)
  keybind.json     # contributor keybind {semantic: literal(s)}  (optional;
                   # falls back to translator/keybinds.py for known games)
```

## Output (delivery bundle — matches `Game Samples/`)

```
out/humynlabs/<game>/<mm-dd-yy>/<session-id>/
  session.json      # {canonical, humyncapture_metadata}; canonical.data_quality added
  frames.csv        # one row per video frame, spec column set
  video.mp4         # trimmed (new) / copied (reprocess)
  key_binding.json  # {literal: [semantic, …]}, canonical lowercase, full coverage
  session.rrd       # rerun QA visualization (spec §2.3)
  rrd_creation.py   # exact script used to build the .rrd
```

## Guarantees (the client findings this fixes)

| Requirement | How it's enforced |
|---|---|
| No off-by-one frame attribution | event → `floor(t / frame_µs)` (the frame whose window contains it) |
| No frame drift | rows == ffprobe frame count, exactly |
| No video↔data drift | fps = `frame_count / duration` (real timeline, **not** the nominal `r_frame_rate`) |
| No input bleed | simultaneous L+R modifier → spurious side dropped (keeps the bound side), frame flagged |
| frames.csv ↔ key_binding match | one lowercase snake_case vocabulary everywhere; key_binding has full coverage of `input_keys` (spec §2.2) |
| No OS-key pollution | control bytes + cmd/win, media, print_screen, vk_###, caps/num/scroll lock, F-keys stripped (unless bound) |
| ≥3 actions/game, ≥70 s, both games | QA checks (`translator qa`) |
| Clip hygiene | implicit **lossless 5 s head/tail trim** on every new sample (`-c copy`, all streams intact, snap-to-keyframe start) |

Missing mouse capture (no `mouse_raw` in source) is **unrecoverable**: dx/dy are
left blank and the session is flagged `mouse_capture: missing` in
`session.json` + QA. Such sessions must be re-recorded.

## Capture-side notes (cannot be fixed in post)

HUD/tutorial-free pixels, gameplay audio, no app-toggling/loading menus, and the
raw-mouse listener actually starting are recording-time concerns. See the memory
notes / `HumynCapture_Implementation_and_Spec_Diff.md`.
