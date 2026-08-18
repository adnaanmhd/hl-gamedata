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
    # close would survive exactly there (review finding, 08-16).
    # Row sets are kept PER REQUESTED WINDOW (r-loop 8): the blanked set
    # is their union (behaviour identical), but the destroyed inventory
    # is also recorded per window so split-child propagation can hand
    # each segment only ITS windows' share instead of the full aggregate.
    per_win_rows: list[set[int]] = []
    blank: set[int] = set()
    for t0, t1 in windows:
        mine: set[int] = set()
        for i, r in enumerate(rows):
            t = int(r[ti]) / 1000.0
            if t0 <= t <= t1:
                for k in range(max(i - pad_frames, 0),
                               min(i + pad_frames + 1, len(rows))):
                    mine.add(k)
        per_win_rows.append(mine)
        blank |= mine

    def _spans(idx: set[int]) -> list[list[float]]:
        """Contiguous index runs -> [t_start, t_end] spans (timestamps
        survive blanking — only keys/actions are cleared)."""
        out: list[list] = []
        for i in sorted(idx):
            t = int(rows[i][ti]) / 1000.0
            if out and i == out[-1][2] + 1:
                out[-1][1], out[-1][2] = t, i
            else:
                out.append([t, t, i])
        return [[round(a, 3), round(b, 3)] for a, b, _ in out]

    # per-window destroyed inventory, from the ORIGINAL rows (before the
    # blanking below). Overlapping windows may double-count key_frames
    # across per_window entries; the aggregate `destroyed` below remains
    # the truth for the parent's own _gate_destroyed — per_window exists
    # for split-child attribution (r-loop 8).
    per_window = []
    for (t0, t1), mine in zip(windows, per_win_rows):
        w_acts: set[str] = set()
        w_keys = 0
        for i in mine:
            r = rows[i]
            if r[ai]:
                w_acts.update(a for a in r[ai].split("|") if a)
            if r[ki]:
                w_keys += 1
        per_window.append({"requested": [round(t0, 3), round(t1, 3)],
                           "windows": _spans(mine),
                           "destroyed": {"actions": sorted(w_acts),
                                         "key_frames": w_keys}})
    gated = 0
    # Record the inventory this gate DESTROYS. map_reasons re-tests the
    # recomputed inventory after the fix, and nothing subtracted the rows
    # the pipeline itself emptied -- so a session whose 3rd distinct action
    # only occurs inside a frozen context came back with 2 and was rejected
    # CNT_ACTIONS_FEW (blocking, UNFIXABLE), with coaching.md telling the
    # player to "play actively" for actions WE deleted. Same shape for
    # INP_KEYS_MISSING on a mouse-heavy session whose only key presses fall
    # inside frozen contexts (r-loop 5).
    destroyed_actions: set[str] = set()
    destroyed_key_frames = 0
    for i in blank:
        r = rows[i]
        if r[ki] or r[ai]:
            gated += 1
        if r[ai]:
            destroyed_actions.update(a for a in r[ai].split("|") if a)
        if r[ki]:
            destroyed_key_frames += 1
        r[ki] = ""
        r[ai] = ""
    tmp = session_dir / "frames.csv.tmp"
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(path)                       # atomic (§13)
    # fixlog gets the actually-blanked spans (contiguous index runs)
    return {"gated_frames": gated,
            "windows": _spans(blank),
            "requested": [[round(a, 3), round(b, 3)] for a, b in windows],
            "pad_frames": pad_frames,
            "destroyed": {"actions": sorted(destroyed_actions),
                          "key_frames": destroyed_key_frames},
            "per_window": per_window}
