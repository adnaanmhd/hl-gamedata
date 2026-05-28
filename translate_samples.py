"""Translate HumynCapture session bundles in samples/<game>/sample{N}/ into
the Odyssey Game Data Capture Spec v1 delivery layout.

Source layout (per session):
    samples/<game>/sample{N}/
        video.mp4
        inputs.jsonl
        metadata.json

Output layout (per spec §2.1 + §2.2 + §2.3):
    out/humynlabs/<game-slug>/<mm-dd-yy>/<session-id>/
        session.json     # HumynCapture metadata + canonical fields
        frames.csv       # per-frame inputs (camera columns left null)
        video.mp4        # copy of source
        key_binding.json # literal -> [semantic_action(s)], lowercase snake_case
        session.rrd      # rerun visualization (15% QA — included for all here)
        rrd_creation.py  # script used to produce session.rrd

Design decisions
----------------
- FPS read from metadata.json `video.fps`.
- Frame count comes from ffprobe `nb_frames` so frames.csv length matches the
  delivered video (no video-to-data drift).
- All key/literal tokens are lowercase, matching what HumynCapture writes into
  inputs.jsonl ("ctrl_l", "shift_l", "w", "space"). key_binding.json uses the
  same tokens so frames.csv input_keys ↔ key_binding keys line up 1:1.
- Mouse buttons appear as `mouse_left` / `mouse_right` / `mouse_middle` in BOTH
  the input_mouse_buttons column and key_binding.json (disambiguates from arrow
  keys named "left"/"right").
- Camera columns are left empty (no in-game camera matrix available).
- Self-checks (logged, non-fatal):
    * 70 s minimum video duration
    * Simultaneous left+right modifier (artifact flag)
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"
OUT = ROOT / "out"
VENDOR = "humynlabs"
SPEC_VERSION = "v1"

C2W_COLS = [f"c2w_m{r}{c}" for r in range(4) for c in range(4)]
CAMERA_COLS = ["camera_model", "camera_fx", "camera_fy", "camera_cx", "camera_cy"]
FRAME_COLS = (
    ["frame_id", "timestamp_ms"]
    + C2W_COLS
    + CAMERA_COLS
    + ["input_keys", "input_actions", "input_mouse_buttons", "input_mouse_dx", "input_mouse_dy"]
)

# Modifier pairs that should never both fire on the same frame (artifact flag).
# Mirrors the Slack guidance: "no simultaneous left+right shift - that's an
# artifact flag". Includes ctrl/alt for the same class of issue.
MODIFIER_PAIRS = [("shift_l", "shift_r"), ("ctrl_l", "ctrl_r"), ("alt_l", "alt_r")]


# ---------- Keybinds (lowercase snake_case, semantic -> literal[s]) ----------
#
# Derived directly from the user's controls screenshots:
#  - Kamla:        samples/kamla/Kamla 27-05-2026 18_31_20.png  (4 binds)
#  - Outer Wilds:  samples/outerwilds/screenshots/*.jpeg        (28 binds)
#
# Each value is either a literal token, a list of tokens (each is its own
# alternative binding for the same action), or a {"modifier": ..., "key": ...}
# combo. The translator inverts this into a literal -> [semantic] map for
# key_binding.json on disk.

KEYBINDS: dict[str, dict] = {
    "kamla": {
        "interact": "e",
        "pause_menu": "esc",
        "move_up": "w",
        "move_left": "a",
        "move_down": "s",
        "move_right": "d",
        "look": "mouse",
    },
    "outer_wilds": {
        # General
        "general_pause": "esc",
        "general_cancel": "q",
        "general_confirm": "e",
        "general_primary_interact": "e",
        "general_secondary_interact": "c",
        "general_view_map": "tab",
        "general_flashlight": "f",
        # Movement
        "movement_move_x_axis": ["a", "d"],
        "movement_move_y_axis": ["s", "w"],
        "movement_look_x_axis": "mouse_x",
        "movement_look_y_axis": "mouse_y",
        "movement_jump": "space",
        "movement_jetpack_boost": "space",
        # Equipment
        "equipment_signalscope": "y",
        "equipment_retrieve_scout": "mouse_right",
        "equipment_primary_tool_action": "mouse_left",
        "equipment_secondary_tool_action": "r",
        "equipment_tool_x_axis": ["1", "4"],
        "equipment_tool_y_axis": ["2", "3"],
        # Flight
        "flight_up_thrust": "shift_l",
        "flight_down_thrust": "ctrl_l",
        "flight_roll_mode": "r",
        "flight_lock_on": "mouse_middle",
        "flight_match_velocity": "space",
        "flight_landing_camera": "c",
        "flight_autopilot": "x",
        "flight_cockpit_free_look": "alt_l",
    },
}


# ---------- Token resolution ----------
# A keybind literal value (e.g. "shift_l", "mouse_left") is mapped to one or
# more "held tokens" that the per-frame resolver compares against. We keep this
# lightweight: literals are already lowercase, so the only translation needed
# is mapping the mouse_* family onto the @mouse:* sentinels emitted by the
# event-walker for mouse_button events.

MOUSE_BUTTON_ALIASES = {
    "mouse_left": "left",
    "mouse_right": "right",
    "mouse_middle": "middle",
}


def normalize_key_token(raw: str) -> tuple[str, ...]:
    s = raw.strip().lower()
    if s in MOUSE_BUTTON_ALIASES:
        # Bind to the @mouse:<button> sentinel produced by the event walker.
        return (f"@mouse:{MOUSE_BUTTON_ALIASES[s]}",)
    if s in ("mouse_x", "mouse_y", "mouse"):
        # Mouse motion isn't a discrete press — informational only.
        return ("@unbindable",)
    if s in ("shift", "ctrl", "alt"):
        # Bare modifier matches either side.
        return (s, f"{s}_l", f"{s}_r")
    return (s,)


# ---------- Keybind resolver + inverter ----------

def _collect_binding_targets(value) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []

    def _expand_one(literal: str) -> list[tuple[str, ...]]:
        return [(t,) for t in normalize_key_token(literal)]

    if isinstance(value, str):
        out.extend(_expand_one(value))
    elif isinstance(value, list):
        for item in value:
            out.extend(_collect_binding_targets(item))
    elif isinstance(value, dict):
        mod_lits = value.get("modifier")
        key_lit = value.get("key")
        mod_groups = _collect_binding_targets(mod_lits) if mod_lits else [()]
        key_groups = _collect_binding_targets(key_lit) if key_lit else [()]
        for m in mod_groups:
            for k in key_groups:
                combined = tuple(sorted(set(m + k)))
                if combined:
                    out.append(combined)
    return out


def build_resolver(keybind: dict) -> list[tuple[tuple[str, ...], str]]:
    rules: list[tuple[tuple[str, ...], str]] = []
    for semantic, value in keybind.items():
        for group in _collect_binding_targets(value):
            rules.append((group, semantic))
    return rules


def resolve_actions(held: set[str], rules) -> list[str]:
    fired: dict[str, None] = {}
    for required, semantic in rules:
        if all(t in held for t in required):
            fired[semantic] = None
    return list(fired.keys())


def invert_keybind(keybind: dict) -> dict:
    """Source is semantic -> literal[s]; spec wants literal -> [semantic, ...].
    Combo dicts are encoded as `modifier+key` string keys."""
    inv: dict[str, list[str]] = defaultdict(list)

    def _add(literal: str, semantic: str):
        lit = literal.lower()
        if semantic not in inv[lit]:
            inv[lit].append(semantic)

    for semantic, value in keybind.items():
        if isinstance(value, str):
            _add(value, semantic)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    _add(item, semantic)
                elif isinstance(item, dict):
                    mod = (item.get("modifier") or "").lower()
                    key = (item.get("key") or "").lower()
                    _add(f"{mod}+{key}", semantic)
        elif isinstance(value, dict):
            mod = (value.get("modifier") or "").lower()
            key = (value.get("key") or "").lower()
            _add(f"{mod}+{key}", semantic)
    return {k: inv[k] for k in sorted(inv)}


# ---------- ffprobe ----------

def ffprobe_video(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames,duration",
        "-of", "json", str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    data = json.loads(out)["streams"][0]
    num, den = data["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    return {
        "width": int(data["width"]),
        "height": int(data["height"]),
        "fps": fps,
        "nb_frames": int(data["nb_frames"]),
        "duration_s": float(data["duration"]),
    }


# ---------- Per-session translation ----------

def translate_session(game_key: str, sample_dir: Path, out_dir: Path) -> dict:
    """Process one sample (sample1, sample2, sample3). Returns a stats dict."""
    keybind = KEYBINDS[game_key]
    rules = build_resolver(keybind)

    meta_path = sample_dir / "metadata.json"
    inputs_path = sample_dir / "inputs.jsonl"
    video_path = sample_dir / "video.mp4"
    if not (meta_path.exists() and inputs_path.exists() and video_path.exists()):
        raise FileNotFoundError(f"missing files in {sample_dir}")

    with meta_path.open() as f:
        meta = json.load(f)
    probe = ffprobe_video(video_path)
    fps = probe["fps"]  # use video's actual fps as the authoritative rate
    n_frames = probe["nb_frames"]
    frame_us = 1_000_000.0 / fps

    # --- Walk events, build per-frame state ---
    events: list[dict] = []
    n_wheel = 0
    n_focus = 0
    with inputs_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("t")
            if not isinstance(t, int):
                continue
            etype = ev.get("type")
            if etype == "mouse_wheel":
                n_wheel += 1
                continue
            if etype == "focus":
                n_focus += 1
                continue
            if etype not in ("key", "mouse_button", "mouse_raw"):
                continue
            events.append(ev)
    events.sort(key=lambda e: e["t"])

    keys_press_in_frame: list[set[str]] = [set() for _ in range(n_frames)]
    buttons_press_in_frame: list[set[str]] = [set() for _ in range(n_frames)]
    dx_sum = [0] * n_frames
    dy_sum = [0] * n_frames

    keys_down: set[str] = set()
    buttons_down: set[str] = set()
    cur_frame = 0

    def _flush_held_through(target_frame: int):
        nonlocal cur_frame
        while cur_frame < target_frame and cur_frame < n_frames:
            keys_press_in_frame[cur_frame] |= keys_down
            buttons_press_in_frame[cur_frame] |= buttons_down
            cur_frame += 1

    for ev in events:
        f_idx = int(ev["t"] // frame_us)
        if f_idx >= n_frames:
            # Drop events beyond the video — prevents video/data drift.
            continue
        _flush_held_through(f_idx)
        etype = ev["type"]
        if etype == "key":
            k = (ev.get("key") or "").lower()
            if not k:
                continue
            # Drop ASCII control bytes (U+0001..U+001F): on Windows, pynput
            # emits these alongside the real key when Ctrl+letter is held
            # (e.g. Ctrl+W produces both `ctrl_l` AND ``). The chord is
            # already captured by the real keys; the control byte is noise.
            if len(k) == 1 and ord(k) < 32:
                continue
            keys_press_in_frame[f_idx].add(k)
            if ev.get("action") == "down":
                keys_down.add(k)
            else:
                keys_down.discard(k)
        elif etype == "mouse_button":
            b = (ev.get("button") or "").lower()
            if not b:
                continue
            buttons_press_in_frame[f_idx].add(b)
            if ev.get("action") == "down":
                buttons_down.add(b)
            else:
                buttons_down.discard(b)
        elif etype == "mouse_raw":
            dx_sum[f_idx] += int(ev.get("dx", 0) or 0)
            dy_sum[f_idx] += int(ev.get("dy", 0) or 0)

    _flush_held_through(n_frames)

    # --- Write outputs ---
    out_dir.mkdir(parents=True, exist_ok=True)

    # frames.csv
    bleed_frames: list[int] = []
    frames_path = out_dir / "frames.csv"
    with frames_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FRAME_COLS)
        empty_camera = [""] * (len(C2W_COLS) + len(CAMERA_COLS))
        for f_idx in range(n_frames):
            keys = sorted(keys_press_in_frame[f_idx])
            btns_raw = sorted(buttons_press_in_frame[f_idx])
            # Prefix mouse buttons with mouse_ so the input_mouse_buttons column
            # and key_binding.json share vocabulary.
            btns = [f"mouse_{b}" for b in btns_raw]
            held_tokens = set(keys) | {f"@mouse:{b}" for b in btns_raw}
            actions = resolve_actions(held_tokens, rules) if rules else []
            ts_ms = int(round(f_idx * 1000.0 / fps))
            w.writerow(
                [f_idx, ts_ms]
                + empty_camera
                + ["|".join(keys), "|".join(actions), "|".join(btns), dx_sum[f_idx], dy_sum[f_idx]]
            )
            for a, b in MODIFIER_PAIRS:
                if a in keys and b in keys:
                    bleed_frames.append(f_idx)
                    break

    # key_binding.json
    with (out_dir / "key_binding.json").open("w") as f:
        json.dump(invert_keybind(keybind), f, indent=2)

    # session.json — passthrough + canonical fields
    canonical = {
        "spec_version": SPEC_VERSION,
        "vendor": VENDOR,
        "game": meta.get("game", {}).get("name"),
        "session_id": meta.get("session_id"),
        "video_fps": fps,
        "video_resolution": [probe["width"], probe["height"]],
        "video_duration_s": probe["duration_s"],
        "frame_count": n_frames,
        "created_at_utc": meta.get("recording", {}).get("started_at_utc"),
    }
    session_doc = {"canonical": canonical, "humyncapture_metadata": meta}
    with (out_dir / "session.json").open("w") as f:
        json.dump(session_doc, f, indent=2)

    # video.mp4 — copy (or hardlink-ish; just copy to keep it simple)
    shutil.copy2(video_path, out_dir / "video.mp4")

    return {
        "game": game_key,
        "session_id": meta.get("session_id"),
        "out_dir": out_dir,
        "frames": n_frames,
        "events": len(events),
        "dropped_wheel": n_wheel,
        "focus_events": n_focus,
        "rules": len(rules),
        "duration_s": probe["duration_s"],
        "fps": fps,
        "bleed_frames": bleed_frames,
        "under_70s": probe["duration_s"] < 70.0,
    }


# ---------- Driver ----------

def upload_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%m-%d-%y")


def discover_sessions() -> list[tuple[str, Path]]:
    """Return (game_key, sample_dir) for every sampleN folder that contains all
    three HumynCapture files. Games map to a slug per spec §2.1 (lowercase).
    """
    found: list[tuple[str, Path]] = []
    game_dirs = {
        "kamla": SAMPLES / "kamla",
        "outer_wilds": SAMPLES / "outerwilds",
    }
    for game_key, gdir in game_dirs.items():
        if not gdir.exists():
            continue
        for sample in sorted(gdir.glob("sample*")):
            if not sample.is_dir():
                continue
            if all((sample / n).exists() for n in ("video.mp4", "inputs.jsonl", "metadata.json")):
                found.append((game_key, sample))
    return found


def main(argv: list[str]) -> int:
    sessions = discover_sessions()
    if not sessions:
        print("No translatable sessions found under samples/.")
        return 1
    date = upload_date_str()
    summary: list[dict] = []
    for game_key, sample_dir in sessions:
        # Look up session_id ahead of time so we can build the output path.
        with (sample_dir / "metadata.json").open() as f:
            sid = json.load(f).get("session_id")
        if not sid:
            print(f"Skipping {sample_dir}: metadata.json has no session_id")
            continue
        game_slug = game_key  # kamla / outer_wilds — already a clean slug
        out_dir = OUT / VENDOR / game_slug / date / sid
        print(f"-> {game_slug}/{sample_dir.name} -> {out_dir.relative_to(ROOT)}")
        try:
            stats = translate_session(game_key, sample_dir, out_dir)
        except Exception as e:
            print(f"  FAILED: {e!r}")
            continue
        summary.append(stats)
        flags = []
        if stats["under_70s"]:
            flags.append("UNDER_70S")
        if stats["bleed_frames"]:
            flags.append(f"L+R_BLEED@{len(stats['bleed_frames'])}frames")
        flag_s = ("  [" + ",".join(flags) + "]") if flags else ""
        print(
            f"   frames={stats['frames']}  events={stats['events']}  "
            f"dur={stats['duration_s']:.1f}s  fps={stats['fps']:.3f}  "
            f"rules={stats['rules']}{flag_s}"
        )

    if not summary:
        return 2

    # Build session list for the rerun batch step.
    session_dirs = [s["out_dir"] for s in summary]
    print("\nGenerating session.rrd via rerun (rrd_creation.py) for every session…")
    # rrd_creation.py is the same script for all sessions per spec §2.3 (it's
    # the code used to produce the .rrd). We write it into each session dir
    # and invoke it once per session.
    rrd_script = ROOT / "rrd_creation.py"
    if not rrd_script.exists():
        print(f"  WARN: {rrd_script} missing — skipping .rrd generation")
    else:
        for sd in session_dirs:
            shutil.copy2(rrd_script, sd / "rrd_creation.py")
            try:
                subprocess.run(
                    [sys.executable, str(sd / "rrd_creation.py"), "--session-dir", str(sd)],
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                print(f"  rrd generation failed for {sd}: {e}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
