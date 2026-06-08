---
name: translate
description: Translate new HumynCapture sample bundle(s) into the Odyssey Game Data Capture v1 delivery format and QA them (incl. frame-sync + missing-input checks), fully autonomously. Use when the user provides raw capture folders (each with video.mp4 + inputs.jsonl + metadata.json [+ keybind.json]) to process/convert/"run the bundle on". Accepts a path to one bundle or a parent directory of bundles as the argument.
---

# /translate — autonomous sample translation

Run the entire pipeline yourself and report results. **Do not** ask the user to
run commands, and **do not** hand-roll binning — always use the `translator/`
package. Repo root: `/Users/adnaan/Documents/hl-gamedata`.

## 1. Resolve input

The argument is a path to a sample bundle, or a directory containing several.
- A **bundle** = a folder with `video.mp4` + `inputs.jsonl` + `metadata.json`
  (optional `keybind.json`, `{semantic: literal}`).
- If a parent dir is given, treat every qualifying subfolder as a bundle.
- If **no argument** is given, ask the user for the path (one short question).

## 2. Translate (implicit 5s lossless trim + PTS-aware binning + builds all files)

```bash
cd /Users/adnaan/Documents/hl-gamedata
PYTHONPATH=. uv run --with rerun-sdk --with numpy \
    python -m translator translate <bundle-dir> [<bundle-dir> …] --out out/
```

Per session: 5s head/tail lossless trim (stream-copy, snap start to keyframe) →
rebase events → **PTS-aware binning**: place each event onto the REAL video frame
that was on screen when it happened (binary-search the event time into the actual
per-frame presentation timestamps). This **auto-corrects frame sync** — the
capture videos are a 30 fps grid with ~12–20% **dropped frames**, so the old
uniform `floor(t/frame_µs)` grid desynced events by *seconds* wherever drops
cluster. Falls back to uniform fps only if PTS can't be read (→ `frame_timing:
uniform_fps` + a warning). rows == frame count → clean keys (lowercase
snake_case, strip control bytes + OS/system keys) → sum mouse dx/dy (blank if no
`mouse_raw`) → resolve actions → drop spurious L+R modifier + flag → write
`frames.csv`, `key_binding.json` (literal→[semantic], full coverage),
`session.json` (+`data_quality`: `keyboard_capture` / `mouse_capture` (motion) /
`mouse_buttons` / `frame_timing` / `input_bleed_frames` / `distinct_actions`),
`session.rrd` + `rrd_creation.py`.

## 3. QA the outputs

```bash
PYTHONPATH=. python -m translator qa out/humynlabs/*/*/*/
```
(or the specific session dirs printed by step 2).

QA validates the basics (no frame drift, no bleed, no OS-key pollution, key
coverage, ≥3 actions, ≥70s) **plus**:
- **Frame-sync check** — every row's `timestamp_ms` must match the real frame
  PTS (≤100 ms). This **FAILs on interior drift** that the endpoint-span check
  misses (the symptom of dropped-frame desync).
- **Missing-input check** — warns if **keyboard**, **mouse motion**, or **mouse
  buttons** are entirely absent.

## 4. Frame sync: check → correct → flag

1. **Check.** Step 3's QA frame-sync check is the gate. After translate, every
   session should report `frame_timing: real_pts` and pass the QA sync check.
2. **Correct.** PTS-aware binning in step 2 corrects sync automatically. If QA
   reports a **frame-sync FAIL** — or translate printed `frame PTS count != …
   fell back to uniform-fps binning` — the correction didn't apply. Fix it:
   - Verify PTS is readable and complete:
     `ffprobe -v error -select_streams v:0 -show_entries packet=pts_time -of csv=p=0 <video>`
     — the count must equal `frame_count`.
   - If counts differ / PTS is unreadable, that's the cause: re-mux or re-probe
     the video, then re-run `translate`, then re-QA until the sync check passes.
3. **Flag.** If sync still can't be corrected, **flag it loudly** in the report
   (frame-sync = **FAILED**, with the worst per-row drift). Never ship a session
   with failed frame sync silently.

## 5. Report (concise — no raw command dumps)

Per-session table, then verdicts:

| session | frames | clip len | keyboard | mouse (motion / btn) | actions | bleed | sync | QA |
|---|---|---|---|---|---|---|---|---|

- **Deliverable** (QA pass/warn-only, all needed input modalities present) vs
  **needs re-record** vs **failed**.
- **Flag missing inputs** explicitly: keyboard missing · mouse **motion** missing
  (unrecoverable — recommend re-record, never fabricate) · mouse **buttons**
  missing. "Mouse missing" specifically means *motion* (`mouse_raw`) unless noted.
- **Flag frame sync**: `corrected (real_pts)` or **FAILED** (with worst drift).
- Capture-side warnings that can't be fixed in post: clip <70s, no audio, HUD/loading.
- Output path: `out/humynlabs/<game>/<mm-dd-yy>/<session-id>/`.

Then stop — delivery/upload is the user's call unless they ask.

## Locked decisions — never re-ask

5s trim implicit & on · **PTS-aware binning is default (auto-corrects frame sync
from dropped frames; uniform-fps only as a flagged fallback)** · **frame-sync
FAIL must be corrected or loudly flagged, never shipped silently** · **flag any
missing input modality (keyboard / mouse motion / mouse buttons)** · lowercase
snake_case keys · strip OS keys aggressively · drop spurious modifier on bleed ·
missing mouse motion → blank + flag + recommend re-record (never fabricate) ·
regenerate rrd · camera columns null.

## Only pause to ask if

- Game is unknown (no `keybind.json` and not Kamla/Outer Wilds) → ask for the
  keybind or add it to `translator/keybinds.py`.
- A bundle is missing required files or `translate` errors.

See `CLAUDE.md` and the project memory for full context.
