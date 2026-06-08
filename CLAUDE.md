# CLAUDE.md — hl-gamedata

Project: HumynLabs (vendor) captures gameplay with `HumynCapture.exe` and delivers
sessions to **Odyssey** (client) in the **Game Data Capture Spec v1** format. The
`translator/` package converts raw capture bundles into that delivery format.

---

## ⚡ Autonomous task: translate new sample(s)

**Trigger.** Whenever the user provides new HumynCapture sample folder(s) — or
points you at a directory containing them — and asks to translate / process /
convert / "run the bundle on" them, **run the whole pipeline yourself and report
results. Do NOT ask the user to run commands, and do NOT hand-roll any binning.**

A raw sample bundle is one folder per session containing:
`video.mp4`, `inputs.jsonl`, `metadata.json`, and optionally `keybind.json`
(`{semantic: literal}`; if absent, the built-in keybind is used for Kamla /
Outer Wilds).

### Steps to run (in order, without stopping for confirmation)

1. **Locate the bundles.** Use the path(s) the user gave; if they gave a parent
   dir, treat each subfolder that has `video.mp4` + `inputs.jsonl` + `metadata.json`
   as one bundle.
2. **Translate** (this applies the implicit 5s head/tail trim + builds everything):
   ```bash
   cd /Users/adnaan/Documents/hl-gamedata
   PYTHONPATH=. uv run --with rerun-sdk --with numpy \
       python -m translator translate <bundle-dir> [<bundle-dir> …] --out out/
   ```
3. **QA the outputs:**
   ```bash
   PYTHONPATH=. python -m translator qa out/humynlabs/*/*/*/
   ```
   (Or QA the specific session dirs that step 2 printed.)
4. **Report back** a concise summary (see “What to report”). Then stop —
   delivery/upload is the user’s call unless they say otherwise.

### What the translate step does per session (so you can explain/verify)

Trim 5s head+tail losslessly (stream copy, snap start to keyframe) → rebase
events → **PTS-aware binning**: place each event onto the REAL video frame it
occurred during (binary-search the event time into actual per-frame PTS), exactly
`frame_count` rows. This **auto-corrects frame sync** — the capture videos are a
30fps grid with ~12–20% dropped frames, so the old uniform `floor(t/frame_µs)`
grid desynced events by seconds; falls back to uniform fps only if PTS is
unreadable (→ `frame_timing: uniform_fps` + warning). Then clean keys (lowercase
snake_case, strip control bytes + OS/system keys) → sum mouse dx/dy (blank if no
`mouse_raw`) → resolve `input_actions` → drop spurious side of any L+R modifier
bleed → write `frames.csv`, `key_binding.json` (literal→[semantic], full
coverage), `session.json` (+`data_quality`: `keyboard_capture`/`mouse_capture`
(motion)/`mouse_buttons`/`frame_timing`/…), `session.rrd` + `rrd_creation.py`.

### Decisions already locked — do NOT re-ask these

- **5s head/tail trim is implicit** on every new sample (lossless, snap-to-keyframe). On by default.
- **PTS-aware binning is the default** — auto-corrects frame sync from dropped frames; uniform-fps is only a flagged fallback.
- **Frame-sync FAIL** (QA reports interior timestamp drift) must be corrected (ensure PTS readable, re-translate) or **loudly flagged** — never shipped silently.
- **Flag any missing input modality** — keyboard / mouse motion / mouse buttons (QA warns; `data_quality` records each).
- **Lowercase snake_case** key vocabulary in both `frames.csv` and `key_binding.json`.
- **Strip OS/system keys aggressively** (cmd, media, print_screen, vk_###, caps/num/scroll lock, F-keys unless bound) + control bytes.
- **Input bleed** → drop the spurious modifier side (keep the bound side) + flag.
- **Missing mouse motion** (`mouse_raw` absent) → leave dx/dy blank, flag `mouse_capture: missing`; it is **unrecoverable** — recommend re-recording, never fabricate.
- **Regenerate `session.rrd`** via rerun.
- **Camera columns stay null** (out of scope).

### Only pause to ask the user if

- The game is unknown (no `keybind.json` AND not Kamla/Outer Wilds) → ask for the keybind, or add it to `translator/keybinds.py`.
- A bundle is missing required files, or `translate` errors out.
Otherwise proceed end-to-end.

### What to report (concise, no raw command dumps)

A per-session table + verdict:

| session | frames | clip len | keyboard | mouse (motion/btn) | actions | bleed | sync | QA |
|---|---|---|---|---|---|---|---|---|

Then:
- **Deliverable** sessions (QA pass/warn-only, needed input modalities present) vs
  **needs re-record** (missing mouse motion) vs **failed**.
- **Flag missing inputs** (keyboard / mouse motion / mouse buttons) and **frame
  sync** (corrected `real_pts`, or **FAILED** with worst drift).
- Any warnings that are **capture-side** and can't be fixed in post: clip <70s,
  no audio track, suspected HUD/loading/app-toggling.
- Output location (`out/humynlabs/<game>/<mm-dd-yy>/<session-id>/`).

---

## Delivery requirements (validated by `translator qa`)

Both games covered · ≥3 distinct actions/game · no frame drift (rows == video
frames) · no video↔data drift · **frame sync OK (per-row timestamp matches real
frame PTS ≤100ms — catches dropped-frame desync)** · no input bleed ·
frames.csv↔key_binding name/case match + full coverage · OS-key pollution
stripped · **no missing input modality (keyboard / mouse motion / mouse buttons)**
· ≥70s clip · clean gameplay (minimal HUD, no app-toggling/loading at start/end).
Camera columns null.

## Notes

- Use the `translator/` package only; `translate_samples.py` / `translate_sessions.py`
  are superseded (wrong fps, no QA) — don’t use them.
- The client’s own `qa_checks.py` / `run_qa.py` spuriously FAIL on empty camera
  columns and miss the off-by-one — `translator qa` is the source of truth here.
- `HumynCapture.exe` can silently fail to capture mouse (raw-input init); that’s
  why some sessions arrive without `mouse_raw`. See
  `HumynCapture_Implementation_and_Spec_Diff.md`.
- `HumynCapture.exe` also **drops ~12–20% of frames** (videos are a 30fps grid
  with gaps). PTS-aware binning corrects the resulting event↔frame desync in
  post; the cleaner long-term fix is capture-side. `translator qa` flags any
  residual frame-sync drift.
- Run tests after touching the package: `PYTHONPATH=. uv run --with pytest pytest translator/tests -q`.
