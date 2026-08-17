"""FIX_GATE_WINDOW — blank inputs inside a kept frozen window (plan §11, F1).

Blanks `input_keys` AND `input_actions` for every frame whose timestamp
falls inside a gated window: keeping keys while blanking actions would
violate spec §1.5.5's keys→actions coupling. `input_mouse_dx/dy` and
`input_mouse_buttons` stay as captured — raw facts, spec-legal; the client
complaint targets semantic actions during frozen contexts.
"""
from __future__ import annotations

import csv
from pathlib import Path

from translator.v2 import V2_FRAME_COLS

from . import config as C


def gate_windows(session_dir: Path,
                 windows: list[tuple[float, float]],
                 pad_frames: int = C.GATE_PAD_FRAMES) -> dict:
    """Blank keys+actions for rows with timestamp_ms/1000 in any window,
    padded pad_frames beyond each side (Adnaan 08-16: the recheck's
    scanner re-draws window boundaries +-1 frame, so an exact gate left
    one action frame outside->inside forever — the fix-failed loop).

    Returns {"gated_frames": n, "windows": [actually-blanked spans...],
    "requested": [as-detected...], "pad_frames": p} for the fixlog."""
    session_dir = Path(session_dir)
    path = session_dir / "frames.csv"
    with path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    assert header == V2_FRAME_COLS, "gate needs a v2 frames.csv"
    col = {c: i for i, c in enumerate(header)}
    ki, ai = col["input_keys"], col["input_actions"]
    ti = col["timestamp_ms"]

    # pad in ROW units, never seconds: these videos drop 12-20% of frames,
    # and a dropped frame at a window boundary makes any seconds-based pad
    # shorter than pad_frames real rows — the loop this pad exists to
    # close would survive exactly there (review finding, 08-16)
    blank: set[int] = set()
    for i, r in enumerate(rows):
        t = int(r[ti]) / 1000.0
        if any(t0 <= t <= t1 for t0, t1 in windows):
            for k in range(max(i - pad_frames, 0),
                           min(i + pad_frames + 1, len(rows))):
                blank.add(k)
    gated = 0
    for i in blank:
        r = rows[i]
        if r[ki] or r[ai]:
            gated += 1
        r[ki] = ""
        r[ai] = ""
    tmp = session_dir / "frames.csv.tmp"
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(path)                       # atomic (§13)
    # fixlog gets the actually-blanked spans (contiguous index runs)
    spans = []
    for i in sorted(blank):
        t = int(rows[i][ti]) / 1000.0
        if spans and i == spans[-1][2] + 1:
            spans[-1][1], spans[-1][2] = t, i
        else:
            spans.append([t, t, i])
    applied = [[round(a, 3), round(b, 3)] for a, b, _ in spans]
    return {"gated_frames": gated,
            "windows": applied,
            "requested": [[round(a, 3), round(b, 3)] for a, b in windows],
            "pad_frames": pad_frames}
