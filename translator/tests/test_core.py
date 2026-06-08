"""Unit tests for the translator core (run: `uv run --with pytest pytest translator`)."""
from __future__ import annotations

from dataclasses import dataclass

from translator import keys as K
from translator.binner import bin_session, FRAME_COLS
from translator.keybind import build_resolver, bound_literals, invert_keybind, resolve_actions
from translator.keybinds import game_key_from_name
from translator.trim import plan_cuts, rebase_events


@dataclass
class FakeVideo:
    fps: float = 100.0
    frame_count: int = 10
    width: int = 1920
    height: int = 1080
    duration_s: float = 0.1
    has_audio: bool = False
    codec: str = "h264"

    @property
    def frame_us(self):
        return 1_000_000.0 / self.fps


def _col(rows, name):
    i = FRAME_COLS.index(name)
    return [r[i] for r in rows]


def test_no_off_by_one():
    # frame_us = 10_000us. Event at t=4_014_853-ish equivalents:
    # key down at t=25_000 -> frame floor(25000/10000)=2 (NOT 3).
    kb = {"forward": "w"}
    rules = build_resolver(kb)
    events = [
        {"t": 25_000, "type": "key", "key": "w", "action": "down"},
        {"t": 55_000, "type": "key", "key": "w", "action": "up"},
    ]
    rows, stats = bin_session(events, FakeVideo(), kb, rules, bound_literals(kb))
    keys = _col(rows, "input_keys")
    assert keys[1] == "" and keys[2] == "w", keys  # starts at frame 2, not 3
    assert keys[5] == "w"  # held through up-frame (down@2..up@5)


def test_rows_equal_frame_count():
    rows, _ = bin_session([], FakeVideo(frame_count=7), {}, [], frozenset())
    assert len(rows) == 7
    assert _col(rows, "frame_id") == list(range(7))


def test_strip_os_keys_and_control_bytes():
    assert K.normalize_event_key("\x17") is None          # Ctrl+W artifact
    assert K.normalize_event_key("cmd") is None
    assert K.normalize_event_key("media_volume_up") is None
    assert K.normalize_event_key("print_screen") is None
    assert K.normalize_event_key("vk_255") is None
    assert K.normalize_event_key("caps_lock") is None     # aggressive
    assert K.normalize_event_key("caps_lock", bound=frozenset({"caps_lock"})) == "caps_lock"
    assert K.normalize_event_key("W") == "w"
    assert K.normalize_event_key("LCtrl") == "ctrl_l"


def test_input_bleed_dropped_and_flagged():
    kb = {"thrust": "shift_l"}  # left bound, right is the artifact
    rules = build_resolver(kb)
    events = [
        {"t": 5_000, "type": "key", "key": "shift_l", "action": "down"},
        {"t": 5_001, "type": "key", "key": "shift_r", "action": "down"},
    ]
    rows, stats = bin_session(events, FakeVideo(), kb, rules, bound_literals(kb))
    assert "shift_r" not in _col(rows, "input_keys")[0]
    assert "shift_l" in _col(rows, "input_keys")[0]
    assert stats.bleed_frames  # flagged


def test_keybind_invert_and_full_coverage():
    kb = {"jump": "Space", "down_thrust": "LCtrl"}
    inv = invert_keybind(kb, also_cover={"m"})  # 'm' seen in input_keys but unbound
    assert inv["space"] == ["jump"]
    assert inv["ctrl_l"] == ["down_thrust"]
    assert inv["m"] == []           # covered, empty (spec §2.2)
    assert "LCtrl" not in inv       # no titlecase junk


def test_action_resolution_with_motion():
    kb = {"forward": "w", "look": "mouse"}
    rules = build_resolver(kb)
    events = [
        {"t": 5_000, "type": "key", "key": "w", "action": "down"},
        {"t": 5_500, "type": "mouse_raw", "dx": 3, "dy": 0},
    ]
    rows, _ = bin_session(events, FakeVideo(), kb, rules, bound_literals(kb))
    acts = _col(rows, "input_actions")[0].split("|")
    assert "forward" in acts and "look" in acts


def test_missing_mouse_is_blank_not_zero():
    rows, stats = bin_session(
        [{"t": 5_000, "type": "key", "key": "w", "action": "down"}],
        FakeVideo(), {}, [], frozenset())
    assert _col(rows, "input_mouse_dx")[0] == ""   # no mouse_raw -> blank
    assert stats.has_mouse_motion is False


def test_game_name_detection_tolerates_typos_and_exe():
    # exact + spaced display names
    assert game_key_from_name("Kamla") == "kamla"
    assert game_key_from_name("Outer Wilds") == "outer_wilds"
    assert game_key_from_name("Outer wild") == "outer_wilds"   # substring, collapsed
    assert game_key_from_name("Outerwild") == "outer_wilds"    # missing space
    # 'Outerworld' is a different word -> only the exe name saves it
    assert game_key_from_name("Outerworld") is None
    assert game_key_from_name("Outerworld", "OuterWilds.exe") == "outer_wilds"
    assert game_key_from_name("Whatever", "Kamla.exe") == "kamla"
    assert game_key_from_name("Totally Unknown", "Unknown.exe") is None


def test_side_specific_modifier_bind_matches_bare_capture():
    # keybind says L-Shift, but the capture only reports the side-ambiguous "shift"
    kb = {"up_thrust": "shift_l"}
    rules = build_resolver(kb)
    events = [{"t": 5_000, "type": "key", "key": "shift", "action": "down"}]
    rows, _ = bin_session(events, FakeVideo(), kb, rules, bound_literals(kb))
    assert "up_thrust" in _col(rows, "input_actions")[0]
    # a bind on shift_l must NOT fire for the opposite side
    events_r = [{"t": 5_000, "type": "key", "key": "shift_r", "action": "down"}]
    rows_r, _ = bin_session(events_r, FakeVideo(), kb, rules, bound_literals(kb))
    assert "up_thrust" not in _col(rows_r, "input_actions")[0]


def test_pts_aware_binning_places_events_on_real_frames():
    # 6 real frames, but a 30 ms stall (3 frames dropped) after frame 2:
    # frames shown at 0,10,20, [gap], 60,70,80 ms.
    pts = [0, 10_000, 20_000, 60_000, 70_000, 80_000]
    vid = FakeVideo(fps=1_000_000 / 15_000, frame_count=6, duration_s=0.090)
    kb = {"fwd": "w"}
    rules = build_resolver(kb)
    # Event at 45 ms: frame 2 is still on screen (next frame starts at 60 ms).
    # A uniform 6/0.09 grid (15 ms/frame) would wrongly bin it to frame 3.
    ev = [{"t": 45_000, "type": "key", "key": "w", "action": "down"}]
    rows, stats = bin_session(ev, vid, kb, rules, bound_literals(kb), frame_pts_us=pts)
    keys = _col(rows, "input_keys")
    assert stats.frame_timing == "real_pts"
    assert keys[1] == "" and keys[2] == "w" and keys[3] == "w"   # placed @2, carries
    assert _col(rows, "timestamp_ms") == [0, 10, 20, 60, 70, 80]  # real PTS, not uniform


def test_pts_binning_falls_back_on_count_mismatch():
    kb = {"fwd": "w"}
    rules = build_resolver(kb)
    ev = [{"t": 25_000, "type": "key", "key": "w", "action": "down"}]
    # pts length (2) != frame_count (10) -> must fall back to uniform fps
    rows, stats = bin_session(ev, FakeVideo(), kb, rules, bound_literals(kb),
                              frame_pts_us=[0, 10_000])
    assert stats.frame_timing == "uniform_fps"
    assert _col(rows, "input_keys")[2] == "w"   # floor(25000/10000) = frame 2


def test_input_modality_presence_flags():
    kb = {"fwd": "w", "fire": "mouse_left"}
    rules = build_resolver(kb)
    # keyboard only: a key, no mouse motion, no buttons
    rows, stats = bin_session(
        [{"t": 5_000, "type": "key", "key": "w", "action": "down"}],
        FakeVideo(), kb, rules, bound_literals(kb))
    assert stats.has_keyboard and not stats.has_mouse_motion and not stats.has_mouse_buttons
    # mouse buttons only: a click, no keys, no motion
    rows, stats = bin_session(
        [{"t": 5_000, "type": "mouse_button", "button": "left", "action": "down"}],
        FakeVideo(), kb, rules, bound_literals(kb))
    assert stats.has_mouse_buttons and not stats.has_keyboard and not stats.has_mouse_motion
    # mouse motion present
    rows, stats = bin_session(
        [{"t": 5_000, "type": "mouse_raw", "dx": 2, "dy": -1}],
        FakeVideo(), kb, rules, bound_literals(kb))
    assert stats.has_mouse_motion and not stats.has_keyboard and not stats.has_mouse_buttons


def test_trim_snaps_to_keyframe():
    kf = [0.0, 1.7, 3.97, 6.0, 8.1, 60.0, 62.0, 64.0]
    head, end = plan_cuts(kf, duration=64.0, head_s=5.0, tail_s=5.0)
    assert head == 6.0          # first keyframe >= 5 (lossless start)
    assert end == 59.0          # duration - 5 (tail needs no keyframe)
    assert head in kf


def test_rebase_events_drops_outside_window():
    evs = [{"t": 1_000_000, "type": "key"}, {"t": 6_000_000, "type": "key"},
           {"t": 9_000_000, "type": "key"}]
    out = rebase_events(evs, head_cut_s=5.0, new_duration_s=3.0)
    assert [e["t"] for e in out] == [1_000_000]  # only 6s event kept, rebased to 1s
