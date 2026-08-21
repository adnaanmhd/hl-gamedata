# Sample Analysis Playbook

How to analyze any new HumynCapture sample end-to-end and reach a deliverable /
fix-in-post / re-record verdict. Written from the June→August 2026 sample
history; every check here exists because something once failed it.

**Automated:** `tools/analyze_sample.py` runs §0–§8 of this playbook on
**delivered v2 sessions** in one command (structural QA, inventory, lag,
audio, a Gemini VLM sweep for the §5 video-content checks, review artifacts,
confidence-tiered verdict, per-session + batch reports, verdict-coded exit):

```bash
GEMINI_API_KEY=… uv run --with numpy --with opencv-python-headless \
    python tools/analyze_sample.py <session-dir> […] [--raw-root <dir>]
```

HIGH-confidence detections gate the verdict; LOW-confidence ones are advisory
with filmstrips/crops rendered for a human eyeball. Raw bundles / v1
deliveries / zips still follow the manual steps below (§0 tells them apart).

Run everything from the repo root. `$S` = path to one session directory.

```bash
cd /Users/adnaan/Documents/hl-gamedata
S=<session-dir>
```

---

## 0. Identify what you received

```bash
ls "$S"    # or unzip first: Drive zips may split one folder across -001/-002 parts
```

| Contents | It is | Note |
|---|---|---|
| `video.mp4 inputs.jsonl metadata.json [keybind.json]` | **raw bundle** | run `translate-v2` (see CLAUDE.md autonomous task), then analyze the output |
| v2 files (no `key_binding.json`, flat 16-field session.json) | **v2 delivery** | analyze directly |
| + `key_binding.json`, session.json has nested `canonical`/`spec_version: v1` | **v1 delivery — obsolete** | vendor ran the superseded `translate` command; needs v1→v2 conversion before delivery |
| + `qa.txt` / `translation_report.json` | vendor ran our pipeline | verify every claim independently anyway |

Received a zip that duplicates an existing folder? `md5 -q` each file pair first —
if identical, the prior analysis stands, done.

**Always check game identity** — session IDs have lied before (08-12: `_kamla_`
sessions that were actually Xonotic):

```bash
python3 -c "import json;s=json.load(open('$S/session.json'));print(s.get('game_title') or s['canonical']['game'])"
# raw bundle / v1: also check metadata game.name + game.exe_name; exe_name is the truth
```

---

## 1. Structural QA (spec §1.5)

```bash
PYTHONPATH=. uv run --with numpy --with opencv-python-headless \
    python -m translator qa-v2 "$S" [--raw-root <dir-with-raw-bundles>]
```

Covers: session.json fields/consistency, 36-col header, rows==frame_count,
frame_id sequence, monotonic timestamps, camera nulls, v2 tokens, float dx/dy
sentinels, keys→actions coverage, **same-literal fan-out** (the 07-27 customer
complaint; needs the game in `translator/keybinds.py` KEYBINDS), ≥70s, video↔csv
↔session.json agreement, frame-sync vs real PTS (≤100ms), controls-to-video
sync, and (with `--raw-root`) the independent off-by-one recomputation.
FAIL = blocking. Known acceptable WARN: irregular frame spacing on old (June)
captures.

Spec nit qa-v2 is looser on: §1.5.2 wants `timestamp_ms[-1]` within **1** frame
interval of `duration_ms` (qa-v2 allows 4). Check it when the tail matters.

---

## 2. Content inventory (one python pass over frames.csv)

```bash
python3 - <<EOF
import csv, collections
rows=list(csv.DictReader(open('$S/frames.csv',newline='')))
keys=collections.Counter(); acts=collections.Counter(); btns=collections.Counter()
kf=bf=mot=kna=0
ts=[int(r['timestamp_ms']) for r in rows]
for r in rows:
    ks=[t for t in (r['input_keys'] or '').split('|') if t]
    a=[x for x in (r['input_actions'] or '').split('|') if x]
    bs=[b for b in (r['input_mouse_buttons'] or '').split('|') if b]
    for t in ks: keys[t]+=1
    for x in a: acts[x]+=1
    for b in bs: btns[b]+=1
    kf+=bool(ks); bf+=bool(bs); kna+=bool(ks and not a)
    mot+=(r['input_mouse_dx'] not in ('','0.0','0') or r['input_mouse_dy'] not in ('','0.0','0'))
print('rows',len(rows),'| key-frames',kf,'| btn-frames',bf,'| motion',mot,'| keys-no-action',kna)
print('keys',dict(keys)); print('buttons',dict(btns) or 'NONE'); print('actions',dict(acts))
d=[b-a for a,b in zip(ts,ts[1:])]; med=sorted(d)[len(d)//2]
odd=[(i,x) for i,x in enumerate(d) if abs(x-med)>0.2*med]
print('median dt',med,'ms | irregular intervals',len(odd),odd[:6])
print('ts[-1]',ts[-1])
EOF
```

Read off:
- **Missing modality** — keyboard / mouse motion / mouse buttons entirely absent
  → cross-check against video (§5) before concluding; if real → **re-record**
  (unrecoverable; locked rule).
- **≥3 distinct actions**, OS-key pollution (Cmd/F-keys/locks/media → must be
  stripped), L+R modifier bleed, keys-with-no-action (must be 0 in v2).
- **Irregular intervals = dropped frames.** Expected gap = 1000/fps ms; flag
  >20% off median. June tool: 12–20% of intervals. 08-10+ builds: 0–1. The holes
  are unfixable in post; what must be true is timestamps = real PTS (then sync
  survives). Also eyeball `ts[-1]` vs `duration_ms` (§1 nit).
- **Multi-action rows**: legit = genuinely concurrent inputs. Fan-out (one key →
  its alternative meanings listed together) = the 07-27 complaint. For games
  with a context table (Outer Wilds) translate-v2 gates this; qa-v2 FAILs it.

---

## 3. Controls-to-video lag

qa-v2 already measures it (client's own action_video_grounding, vendored in
`translator/sync.py`). Standalone (e.g. v1 CSV):

```bash
uv run --with numpy --with opencv-python-headless python - <<EOF
import csv,sys; sys.path.insert(0,'.')
from translator import sync
rows=list(csv.DictReader(open('$S/frames.csv',newline='')))
meta={"input_mouse_convention":{"maps_to":"camera_look_velocity","dx_positive":"right",
      "dx_negative":"left","dy_positive":"down","dy_negative":"up"}}
mdx,mdy=sync.motion_track('$S/video.mp4')
adx,ady=sync.input_track_from_rows([r['input_mouse_dx'] for r in rows],
                                   [r['input_mouse_dy'] for r in rows],meta)
est=sync.estimate_lag(mdx,mdy,adx,ady)
print(sync.verdict(est, <fps>))
EOF
```

Gates: |lag| ≤150ms hard, ≤50ms target; measurable needs active ≥2% and
|corr| ≥0.15. **Negative correlation is EXPECTED** (scene flow opposes camera —
do not report as a bug; client agreed 07-21). History: June captures needed
+151/+208/−38ms corrections (clock-anchor defect, `translation_report.json`
records applied shifts); 08-10+ builds measure ~0–33ms raw. 33ms = 1 frame @30
— fine.

---

## 4. Video probe

```bash
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,avg_frame_rate,nb_frames -of csv=p=0 "$S/video.mp4"
ffmpeg -i "$S/video.mp4" -map 0:a -af volumedetect -f null - 2>&1 | grep -E "mean_volume|max_volume"
```

- **Audio track present + real signal?** June captures had none (client
  complaint); 08-10+ have AAC. Missing audio = capture-side, flag.
- nb_frames must equal CSV rows. Resolution must equal `record_*_px`. Spec has
  **no minimum resolution** (verified: schema minimum is 1px) — 720p is legal.
- fps changes between builds (30 → 60 on 08-12); nothing assumes a rate.

---

## 5. Watch the video (this catches what numbers can't)

Contact sheet + first/last frames:

```bash
T=$(mktemp -d)
ffmpeg -loglevel error -i "$S/video.mp4" -vf "fps=1/8,drawtext=text='%{pts\:hms}':fontcolor=yellow:fontsize=28:x=8:y=8:box=1:boxcolor=black@0.6,scale=256:-1,tile=6x4" "$T/sheet_%02d.jpg" -y
ffmpeg -loglevel error -i "$S/video.mp4" -vf "select='lt(n\,3)+gt(n\,<rows-4>)',scale=320:180,tile=6x1" -vsync 0 -frames:v 1 "$T/edges.png" -y
```

Then zoom any suspicious window at 1.5–2fps
(`-ss <t> -t <len> ... fps=2,...,tile=6x6`). Look for, in rough frequency order:

1. **Non-gameplay segments**: menus, loading screens, match-end scoreboards,
   pause overlays, cutscenes — at start/end (client guideline; the implicit 5s
   trim only removes so much) **and mid-clip** (both 08-10 Xonotic samples had
   match-end → loading → menu-with-cursor transitions mid-clip; one had a 5s
   mid-match PAUSE).
2. **Inputs during frozen contexts** — cross-reference: for each non-gameplay
   window found, dump keys/actions in that frame range (python slice of rows).
   Gameplay actions emitted while paused/loading/in-menu (`fire` on a menu
   click, `jump` during loading) = the customer's context complaint → gate or
   trim before delivery.
3. **Modality cross-check**: if buttons/keys/motion are missing in data, look
   for evidence in video (ammo counter dropping + muzzle flashes with zero
   button events = capture failure, 08-12 repro; ZERO fire in video = maybe
   genuine).
4. **Desktop notifications**: June OW videos had 4 burned-in Steam friend
   popups (bottom-right). Beware false positives — FPS kill feeds sit in the
   same corner with green text; always eyeball hits at full crop before
   claiming. Capture-side; contributors must enable DND / disable overlays.
5. **Chat**: typed message letters should be stripped as unbound (translator
   does); the chat text itself is burned into video — privacy/cleanliness
   judgment call.

For OW specifically: `translator/context.py` classifies frames
(cockpit/suit/dialogue/model/map/pause) and translate-v2 gates actions;
ambiguous suit-Space runs print their chosen labels — **review each against
video** (tap-vs-hold heuristic matched only 3/7 manual labels).

---

## 6. Verdict framework

**Deliverable** = qa-v2 PASS or WARN-only-known, all three modalities present,
lag within target (or corrected + re-verified), no un-gated frozen-context
actions, no non-gameplay at start/end, content clean. Then package per spec
§1.1.1: `humynlabs/<mm-dd-yyyy UTC upload date>/<game>/<session-id>/`.

**Fix in post** (tools exist): frame desync → PTS-aware re-bin; constant lag →
translate-v2 auto-corrects; fan-out / context actions → context gating
(`tools/fix_actions_from_v2.py` pattern works from delivered files, no raws);
non-gameplay head/tail → `tools/retrim_v2_session.py` (lossless keyframe cut,
rebases CSV+session.json, regens rrd); v1 delivery → mechanical v1→v2
conversion (actions already resolved; raws not needed).

**Re-record**: any missing input modality (motion or buttons), or unusable
content. Never fabricate data.

**Always report even when unfixable**: capture-side items (notifications, no
audio, drops, HUD) with timestamps, so the vendor/contributor can fix the setup.

---

## 7. Known history — what to suspect first

| Symptom | Prior cause | Status |
|---|---|---|
| Events lag video by seconds, drifting | uniform-grid binning over dropped frames | fixed — PTS-aware binning |
| Constant lag, whole session | capture clock anchored at "ffmpeg confirmed" not first frame | fixed in 08-10 tool; translate-v2 auto-corrects old |
| 12–20% irregular intervals | old capture tool drops | fixed in 08-10 tool |
| Mouse dx/dy all blank | raw-input init silently fails | unrecoverable → re-record |
| Buttons empty but video shows firing | same defect class, buttons only (08-12) | unrecoverable → re-record + vendor bug report |
| One key → several actions each frame | no context resolution | fixed for OW; new multi-bound games need a context table |
| `general_confirm|general_primary_interact` etc. in old deliveries | pre-07-31 outputs | superseded by `v2_out_actions_fixed` |
| Session-id game ≠ actual game | capture tool game selector | check `exe_name`; flag to vendor |
| v1-format delivery | vendor ran superseded `translate` | convert; remind vendor `translate-v2` |
| Raw bundles missing / in temp dirs | vendor processes in `/var/folders/...T/` | ask them to archive raws immediately (June raws are lost forever) |
| Kamla LMB/RMB no action | unbound; vendor never answered 07-17 query | still open |

---

## 8. Report template

Per session: format vintage · game (verified via exe) · frames/fps/length ·
drops (irregular-interval count) · frame-sync (PTS) · lag (ms, corr, shift
applied?) · audio · modalities · #actions · content findings (timestamped) ·
**verdict** (deliverable / fix-in-post + which fix / re-record + why).

Batch-level: feedback-compliance table (each past client complaint → status in
this batch), capture-side flags for the vendor, open questions for the client,
and what only the user can decide (upload, vendor comms, scope questions like
"is a third game title in scope?").
