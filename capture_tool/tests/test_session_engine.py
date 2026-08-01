import asyncio
import json

from app.core.health import SubsystemIssue, SubsystemMonitor
from app.core.session_engine import SessionEngine, _clamp_rect_to_bounds, _slugify


class _FakeDeadSubsystem:
    def __init__(self):
        self.last_error = "boom"

    def is_alive(self):
        return False


class _FakeHealthySubsystem:
    last_error = None

    def is_alive(self):
        return True


def test_poll_health_stops_recording_when_a_subsystem_dies_mid_session():
    """Real gap fixed here: a subsystem dying mid-session (confirmed on real
    hardware) used to only be logged — recording carried on for however
    much longer the user kept playing, silently missing that modality.
    _poll_health must now set subsystem_failed so run() stops immediately."""
    async def scenario():
        monitor = SubsystemMonitor()
        monitor.register("raw_mouse_motion", _FakeDeadSubsystem())
        engine = SessionEngine()
        subsystem_failed = asyncio.Event()
        task = asyncio.create_task(engine._poll_health(monitor, subsystem_failed))
        await asyncio.wait_for(subsystem_failed.wait(), timeout=2.0)
        task.cancel()
        return subsystem_failed.is_set()

    assert asyncio.run(scenario())


def test_poll_health_does_not_fire_for_a_healthy_session():
    async def scenario():
        monitor = SubsystemMonitor()
        monitor.register("raw_mouse_motion", _FakeHealthySubsystem())
        engine = SessionEngine()
        subsystem_failed = asyncio.Event()
        task = asyncio.create_task(engine._poll_health(monitor, subsystem_failed))
        await asyncio.sleep(1.2)  # a couple of the tightened 0.5s poll ticks
        task.cancel()
        return subsystem_failed.is_set()

    assert asyncio.run(scenario()) is False


def test_clamp_rect_shrinks_window_that_overflows_monitor_edge():
    """Real bug found on Windows: a windowed-mode game running at exactly
    the monitor's native resolution has its client area pushed a few pixels
    past the monitor edge by the title bar/border. Exact numbers from a
    real ffmpeg failure — its error message reports the virtual desktop as
    two CORNER POINTS, (-1920,0)-(3840,2160): a second monitor (width 1920)
    sits left of the primary, so the true virtual-desktop origin/size is
    vx=-1920, vw=3840-(-1920)=5760. Client rect (11, 45, 3840, 2160) then
    overflows the primary monitor's right edge (at x=3840) by exactly 11px
    — gdigrab hard-failed instead of clipping that sliver off."""
    x, y, w, h = _clamp_rect_to_bounds(
        x=11, y=45, w=3840, h=2160, vx=-1920, vy=0, vw=5760, vh=2160)
    assert (x, y) == (11, 45)
    assert (w, h) == (3829, 2115)  # shrunk by exactly the 11px/45px overflow
    # The clamped rect must never extend past the real desktop bounds.
    assert x + w <= -1920 + 5760
    assert y + h <= 0 + 2160


def test_clamp_rect_is_noop_when_fully_inside_bounds():
    x, y, w, h = _clamp_rect_to_bounds(
        x=100, y=100, w=800, h=600, vx=0, vy=0, vw=1920, vh=1080)
    assert (x, y, w, h) == (100, 100, 800, 600)


def test_clamp_rect_handles_window_left_of_primary_monitor():
    """A window mostly on a monitor placed left of the primary (negative
    x) must clamp against the true virtual-desktop left edge, not 0."""
    x, y, w, h = _clamp_rect_to_bounds(
        x=-1920, y=0, w=2000, h=1080, vx=-1920, vy=0, vw=3840, vh=2160)
    assert (x, y, w, h) == (-1920, 0, 2000, 1080)  # fits, no clamp needed


def test_clamp_rect_never_returns_negative_size():
    """A window entirely outside the virtual desktop must clamp to a zero
    (not negative) size — the caller treats <= 0 as a hard failure, never
    a crash from a negative width reaching ffmpeg."""
    x, y, w, h = _clamp_rect_to_bounds(
        x=5000, y=5000, w=800, h=600, vx=0, vy=0, vw=1920, vh=1080)
    assert w == 0 and h == 0


def test_slugify_basic():
    assert _slugify("Outer Wilds") == "outer_wilds"
    assert _slugify("  Kamla!! ") == "kamla"


def test_slugify_empty_falls_back_to_game():
    assert _slugify("!!!") == "game"


def test_slugify_truncates_to_30_chars():
    long_name = "a" * 50
    assert len(_slugify(long_name)) == 30


def test_merge_inputs_sorts_and_counts(tmp_path):
    engine = SessionEngine()
    queue_events = [
        {"t": 5000, "type": "key", "key": "w", "action": "down"},
        {"t": 1000, "type": "mouse_button", "button": "left", "action": "down"},
    ]
    raw_mouse_path = tmp_path / "raw_mouse.jsonl"
    raw_mouse_path.write_text(
        json.dumps({"ts_offset_ns": 2_000_000, "ts_monotonic_ns": 999,
                    "dx": 3, "dy": -2, "buttons": [], "wheel": None}) + "\n")
    out_path = tmp_path / "inputs.jsonl"

    total, by_type = engine._merge_inputs(queue_events, raw_mouse_path, out_path)

    assert total == 3
    assert by_type == {"key": 1, "mouse_button": 1, "mouse_raw": 1}
    written = [json.loads(l) for l in out_path.read_text().splitlines()]
    ts = [e["t"] for e in written]
    assert ts == sorted(ts)  # merged in time order


def test_merge_inputs_b1_fallback_when_ts_offset_missing(tmp_path):
    """B1 fix: a raw-mouse record with ts_offset_ns=None must NOT be dropped
    — it should fall back to a ts_monotonic_ns-derived offset instead."""
    engine = SessionEngine()
    raw_mouse_path = tmp_path / "raw_mouse.jsonl"
    records = [
        {"ts_offset_ns": None, "ts_monotonic_ns": 1_000_000_000,
         "dx": 1, "dy": 1, "buttons": [], "wheel": None},
        {"ts_offset_ns": None, "ts_monotonic_ns": 1_000_500_000,
         "dx": 2, "dy": 2, "buttons": [], "wheel": None},
    ]
    raw_mouse_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    out_path = tmp_path / "inputs.jsonl"

    total, by_type = engine._merge_inputs([], raw_mouse_path, out_path)

    assert total == 2  # neither record was dropped
    assert by_type == {"mouse_raw": 2}
    written = [json.loads(l) for l in out_path.read_text().splitlines()]
    assert written[0]["t"] == 0          # first record anchors at t=0
    assert written[1]["t"] == 500        # +500us later, per ts_monotonic_ns delta


def test_merge_inputs_empty_raw_mouse_file(tmp_path):
    engine = SessionEngine()
    raw_mouse_path = tmp_path / "raw_mouse.jsonl"
    raw_mouse_path.write_text("")
    out_path = tmp_path / "inputs.jsonl"
    total, by_type = engine._merge_inputs(
        [{"t": 1, "type": "key", "key": "w", "action": "down"}], raw_mouse_path, out_path)
    assert total == 1
    assert by_type == {"key": 1}
