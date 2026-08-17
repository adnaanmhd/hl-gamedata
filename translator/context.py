"""Per-frame game-context classification from the session video.

Outer Wilds binds one physical key to several mode-dependent semantic actions
(Space = jump / jetpack boost / match velocity, E = confirm / interact, ...).
The capture bundle carries no game state, but the game's screen-space HUD
overlays identify the active mode per frame: they render at fixed pixel
positions, so small grayscale templates matched at their home position are
near-binary signals. Contexts:

    on_foot     default (village, campfire, scout tool raised, ...)
    suit        spacesuit worn (fuel/O2 gauge + GRAVITY readout)
    dialogue    conversation box open (controls frozen; E/Enter advance)
    model_ship  remote-piloting the model ship ("Reset" / thrust prompts)
    cockpit     buckled at the ship controls ("Unbuckle" prompt; includes the
                fullscreen landing-camera view via gap-filling)
    map         solar-system map ("Close Map" prompt)
    pause_menu  PAUSED screen

Priority when overlays coexist (low -> high):
    on_foot < suit < dialogue < model_ship < cockpit < map < pause_menu
model/cockpit outrank dialogue deliberately: when their control prompts are
visible the controls are live, and bright low-saturation pixels in the text
band (e.g. the model ship's metallic parts) must not read as dialogue.

Templates live in translator/templates/<game>/ as 640x360-space crops; their
home position is encoded in the filename-keyed table below. Validated
frame-by-frame against the 2026-06-06 sessions (every gated key press and
every segment visually reviewed).
"""
from __future__ import annotations

from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:          # translator core must work without opencv/numpy
    cv2 = None
    np = None

TEMPLATE_DIR = Path(__file__).parent / "templates"

# name -> (x0, y0) home position of the template crop in 640x360 space
_OW_TEMPLATE_POS = {
    "unbuckle": (555, 29), "gravity": (84, 240), "reset": (571, 29),
    "hthrust": (522, 41), "closemap": (555, 29), "paused": (283, 123),
}
_PAD = 10          # search window around the home position
_TH = dict(paused=0.80, closemap=0.80, unbuckle=0.80, reset=0.78, hthrust=0.85,
           gravity=0.80, dlg_col=0.12, dlg_frac=0.0015)

CONTEXT_GAMES = {"outer_wilds"}


def available() -> bool:
    return cv2 is not None


def _close_binary(b, gap):
    """Fill 0-runs shorter than `gap` between 1s (bridge brief detector dips)."""
    b = b.copy()
    prev_one = -1
    for i in range(len(b)):
        if b[i]:
            if prev_one >= 0 and 0 < i - prev_one - 1 <= gap:
                b[prev_one + 1:i] = True
            prev_one = i
    return b


def _open_binary(b, min_run):
    """Drop 1-runs shorter than min_run (kill single-frame false positives)."""
    b = b.copy()
    n, i = len(b), 0
    while i < n:
        if b[i]:
            j = i
            while j < n and b[j]:
                j += 1
            if j - i < min_run:
                b[i:j] = False
            i = j
        else:
            i += 1
    return b


def _load_templates(game: str):
    tdir = TEMPLATE_DIR / game
    out = {}
    for name, (x0, y0) in _OW_TEMPLATE_POS.items():
        img = cv2.imread(str(tdir / f"{name}.png"), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(tdir / f"{name}.png")
        out[name] = (img, x0, y0)
    return out


def _frame_scores(fr, templates):
    """Template NCC scores + dialogue-text-band features for one BGR frame."""
    fr = cv2.resize(fr, (640, 360), interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    s = {}
    for name, (tpl, x0, y0) in templates.items():
        th, tw = tpl.shape
        sx0, sy0 = max(0, x0 - _PAD), max(0, y0 - _PAD)
        win = g[sy0:min(360, y0 + th + _PAD), sx0:min(640, x0 + tw + _PAD)]
        s[name] = float(cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED).max())
    # dialogue: bright LOW-SATURATION (white) text in the center-bottom band;
    # the saturation mask rejects thrust flames / campfires (bright but orange)
    hsv = cv2.cvtColor(fr[225:310, 95:505], cv2.COLOR_BGR2HSV)
    bright = (g[225:310, 95:505] > 205) & (hsv[..., 1] < 70)
    s["dlg_frac"] = float(bright.mean())
    s["dlg_colfrac"] = float((bright.sum(axis=0) >= 2).mean())
    return s


def classify_video(video_path, fps: float, game: str = "outer_wilds") -> list[str]:
    """Per-frame context labels for every frame of `video_path`."""
    if not available():
        raise RuntimeError("context classification needs numpy + opencv "
                           "(--with numpy --with opencv-python-headless)")
    templates = _load_templates(game)
    cap = cv2.VideoCapture(str(video_path))
    rows = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        rows.append(_frame_scores(fr, templates))
    cap.release()
    n = len(rows)
    if not n:
        # a video that opens but decodes zero frames must degrade like any
        # other length mismatch (callers skip gating with a warning), not
        # crash on col["paused"] (r-loop 1)
        return []
    col = {k: np.array([r[k] for r in rows]) for k in rows[0]}
    sec = lambda t: max(1, int(round(t * fps)))

    paused = _close_binary(col["paused"] > _TH["paused"], sec(0.7))
    mapv = _close_binary(col["closemap"] > _TH["closemap"], sec(1.0))
    dlg = (col["dlg_colfrac"] > _TH["dlg_col"]) & (col["dlg_frac"] > _TH["dlg_frac"])
    dlg = _open_binary(_close_binary(dlg, sec(2.0)), sec(0.4))
    ckpt = _close_binary(col["unbuckle"] > _TH["unbuckle"], sec(2.0))
    model = _close_binary((col["reset"] > _TH["reset"])
                          | (col["hthrust"] > _TH["hthrust"]), sec(5.0))
    model = _open_binary(model, sec(0.7))
    suit = _open_binary(_close_binary(col["gravity"] > _TH["gravity"], sec(2.0)),
                        sec(0.7))

    # fullscreen landing-camera view drops the "Unbuckle" prompt but the player
    # is still buckled: fill gaps between cockpit runs unless an exclusive
    # overlay (map/pause/dialogue/model/suit) claims the frames
    ckpt_fill = _close_binary(ckpt, sec(40.0))
    ckpt = ckpt | (ckpt_fill & ~mapv & ~paused & ~dlg & ~model & ~suit)

    ctx = np.full(n, "on_foot", dtype=object)
    ctx[suit] = "suit"
    ctx[dlg] = "dialogue"
    ctx[model] = "model_ship"
    ctx[ckpt] = "cockpit"
    ctx[mapv] = "map"
    ctx[paused] = "pause_menu"
    return list(ctx)


def segments(ctx: list[str]) -> list[tuple[int, int, str]]:
    """(start, end_exclusive, label) runs of a per-frame context track."""
    segs, start = [], 0
    for i in range(1, len(ctx) + 1):
        if i == len(ctx) or ctx[i] != ctx[start]:
            segs.append((start, i, ctx[start]))
            start = i
    return segs
