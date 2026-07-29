import json

from app.core.session_engine import SessionEngine, _slugify


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
