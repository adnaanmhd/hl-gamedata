"""trim() mixed two clocks (r-loop 6 major).

`V.keyframe_times` returns ABSOLUTE container timestamps; `V.frame_pts`,
the raw event stream and `head_s` are all source-RELATIVE (first frame ==
0). `trim()` planned the cut on the absolute list and returned the
absolute value as `head_cut_s`, so `rebase_events` shifted every event by
the container's `start_time` too much.

Real captures carry 0.035-0.048 s there — 1.0 to 1.5 frames at 30 fps.
Nothing downstream could see it: `build_session_json` advances
`created_at_utc` by the SAME wrong number, so qa-v2's raw re-bin agrees
with the delivered rows, and 35 ms sits under the 50 ms sync target so
the lag corrector never fires. Measured end-to-end on a real capture
before the fix: reported 8.368034 s, actual content offset 8.333333 s
(source frame 250), delta +1.041 frames. After: delta exactly 0 on three
real captures.

`cutter.py` and `tools/retrim_v2_session.py` already rebased correctly —
trim.py was the one path that did not.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from translator import trim as T
from translator import video as V


@dataclass
class FakeInfo:
    fps: float = 30.0
    frame_count: int = 1500
    width: int = 1920
    height: int = 1080
    duration_s: float = 50.0
    has_audio: bool = False


def _run_trim(monkeypatch, tmp_path, first_abs: float):
    """Drive trim() with a real capture's shape: 30 fps, 50 s, ~8.33 s GOP,
    and a container start_time of `first_abs`."""
    kf_rel = [0.0, 8.333333, 16.666667, 25.0, 33.333333, 41.666667]
    monkeypatch.setattr(V, "first_pts_abs", lambda p: first_abs)
    monkeypatch.setattr(V, "keyframe_times",
                        lambda p: [k + first_abs for k in kf_rel])
    monkeypatch.setattr(V, "probe", lambda p: FakeInfo())
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(cmd)

        class _P:
            returncode = 0
        return _P()
    monkeypatch.setattr(T.subprocess, "run", fake_run)
    res = T.trim(Path("src.mp4"), tmp_path / "out.mp4")
    cmd = seen[0]
    ss = float(cmd[cmd.index("-ss") + 1])
    to = float(cmd[cmd.index("-to") + 1])
    return res, ss, to


def test_head_cut_is_relative_and_ffmpeg_gets_absolute(monkeypatch,
                                                       tmp_path):
    """The exact shape of the real capture that was measured."""
    first_abs = 0.034701
    res, ss, to = _run_trim(monkeypatch, tmp_path, first_abs)
    # returned on the SAME clock as frame_pts and the raw events
    assert abs(res.head_cut_s - 8.333333) < 1e-6, \
        f"head_cut_s {res.head_cut_s} is not the relative offset"
    assert abs(res.end_cut_s - 45.0) < 1e-6
    # ffmpeg is handed the container clock (measured: -ss is absolute)
    assert abs(ss - (8.333333 + first_abs)) < 1e-6
    assert abs(to - (45.0 + first_abs)) < 1e-6


def test_zero_start_time_files_are_unchanged(monkeypatch, tmp_path):
    """A file whose first packet is at 0 must behave exactly as before —
    the fix is a no-op wherever the two clocks already coincided."""
    res, ss, to = _run_trim(monkeypatch, tmp_path, 0.0)
    assert abs(res.head_cut_s - 8.333333) < 1e-6
    assert abs(ss - 8.333333) < 1e-6
    assert abs(to - 45.0) < 1e-6


def test_the_event_shift_matches_the_video_cut(monkeypatch, tmp_path):
    """The property that actually ships: an event recorded at the instant
    the trimmed video begins must land on new frame 0, not one frame
    early. Before the fix it was shifted 34.7 ms too far."""
    first_abs = 0.034701
    res, _ss, _to = _run_trim(monkeypatch, tmp_path, first_abs)
    # the video's new frame 0 is source-relative 8.333333 s
    at_cut = {"t": 8_333_333, "type": "key", "key": "w", "action": "down"}
    later = {"t": 9_333_333, "type": "key", "key": "w", "action": "up"}
    out = T.rebase_events([at_cut, later], res.head_cut_s, res.new_duration_s)
    kept = [e for e in out if e.get("action") == "down"]
    assert kept and kept[0]["t"] == 0        # exactly on new frame 0
    assert [e["t"] for e in out if e.get("action") == "up"] == [1_000_000]


def test_first_pts_abs_is_one_implementation(monkeypatch):
    """cutter.py had its own private copy; both needed it and only one had
    it. Keep them the same function so they cannot drift apart."""
    from pipeline import cutter
    monkeypatch.setattr(V, "first_pts_abs", lambda p: 1.25)
    assert cutter._first_pts_abs(Path("x.mp4")) == 1.25
