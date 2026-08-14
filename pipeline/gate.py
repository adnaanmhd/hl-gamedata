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


def gate_windows(session_dir: Path,
                 windows: list[tuple[float, float]]) -> dict:
    """Blank keys+actions for rows with timestamp_ms/1000 in any window.

    Returns {"gated_frames": n, "windows": [...]} for the fixlog."""
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

    gated = 0
    for r in rows:
        t = int(r[ti]) / 1000.0
        if any(t0 <= t <= t1 for t0, t1 in windows):
            if r[ki] or r[ai]:
                gated += 1
            r[ki] = ""
            r[ai] = ""
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return {"gated_frames": gated,
            "windows": [[round(a, 3), round(b, 3)] for a, b in windows]}
