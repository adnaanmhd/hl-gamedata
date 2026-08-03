"""
End-to-end exercise of the native v2 finalize pipeline (app.core.finalize.
pipeline.run_finalize) against a SYNTHETIC capture — not a real game, but a
real ffmpeg-encoded video + a real translator.v2.translate_bundle_v2 +
translator.v2.check_session_v2 run, on this machine.

This is the strongest validation available without Windows/a real GPU/a
real game: it proves the "native v2 emission" wiring (session_engine ->
finalize.pipeline -> translator.v2) actually produces a spec-v2-shaped
delivery folder and that the self-check mechanism runs to completion,
end to end. It does NOT validate anything Windows-only (ddagrab, raw input,
WASAPI, SetWinEventHook) — those have no macOS equivalent to exercise.

Skipped automatically if ffmpeg/ffprobe aren't on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app.core.finalize.anchor import AnchorResult
from app.core.finalize.pipeline import run_finalize
from app.core.health import SubsystemIssue

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH")

DURATION_S = 85  # survives the translator's 5s head + 5s tail trim with margin


def _make_synthetic_video(path) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", f"testsrc=duration={DURATION_S}:size=640x360:rate=30",
        "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def _make_synthetic_inputs(path) -> None:
    events = []
    t_us = 0
    down = False
    while t_us < DURATION_S * 1_000_000:
        events.append({"t": t_us, "type": "key", "key": "w",
                        "action": "down" if not down else "up"})
        down = not down
        events.append({"t": t_us, "type": "mouse_raw",
                        "dx": 5 if (t_us // 100_000) % 2 == 0 else -5, "dy": 2})
        if (t_us // 1_000_000) % 5 == 0:
            events.append({"t": t_us, "type": "mouse_button",
                            "button": "left", "action": "down"})
        t_us += 100_000  # every 100ms
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _make_synthetic_metadata(path, session_id: str) -> None:
    started = datetime.now(timezone.utc)
    ended = started + timedelta(seconds=DURATION_S)
    meta = {
        "schema_version": 2,
        "session_id": session_id,
        "player": {"contributor_id": "c_deadbeefdeadbeef", "skill_level": "expert"},
        "game": {"name": "Outer Wilds", "slug": "outer_wilds",
                  "exe_name": "OuterWilds.exe", "pid_at_capture": 1234},
        "session": {"role": "player", "objective_task": "synthetic validation run"},
        "recording": {"started_at_utc": started.isoformat().replace("+00:00", "Z"),
                       "ended_at_utc": ended.isoformat().replace("+00:00", "Z"),
                       "duration_seconds": DURATION_S},
        "video": {"filename": "video.mp4"},
        "input_capture": {"filename": "inputs.jsonl"},
        "system": {"os": "test-harness"},
    }
    path.write_text(json.dumps(meta, indent=2))


def test_native_finalize_produces_valid_v2_delivery(tmp_path):
    session_id = "2026-01-01T00-00-00Z_outer_wilds_c_deadbeefdeadbeef"
    raw_dir = tmp_path / f"{session_id}_raw"
    raw_dir.mkdir()
    out_root = tmp_path / "sessions"

    _make_synthetic_video(raw_dir / "video.mp4")
    _make_synthetic_inputs(raw_dir / "inputs.jsonl")
    _make_synthetic_metadata(raw_dir / "metadata.json", session_id)

    anchor = AnchorResult(method="first_frame_pts_wallclock", correction_us=0,
                           launch_pairing=None, frame0_wallclock_s=None,
                           frame0_monotonic_s=None)

    result = run_finalize(
        raw_session_dir=raw_dir,
        out_root=out_root,
        anchor=anchor,
        game_slug="outer_wilds",
        game_slug_is_known=True,
        subsystem_issues=[],
        frames_dropped=0,
    )

    # --- v2 layout contract (handoff doc §4.1) ---
    for fname in ("session.json", "frames.csv", "video.mp4", "session.rrd", "rrd_creation.py"):
        assert (result.out_dir / fname).exists(), f"missing {fname}"
    assert not (result.out_dir / "key_binding.json").exists(), \
        "key_binding.json must not exist in a v2 delivery"

    session = json.loads((result.out_dir / "session.json").read_text())
    for field in ("vendor_name", "game_title", "session_id", "created_at_utc",
                   "ended_at_utc", "duration_ms", "fps", "frame_count",
                   "record_width_px", "record_height_px", "localization",
                   "platform", "input_mouse_convention"):
        assert field in session, f"session.json missing {field}"
    assert session["game_title"] == "Outer Wilds"

    with (result.out_dir / "frames.csv").open() as f:
        header = f.readline().strip().split(",")
    assert header[:2] == ["frame_id", "timestamp_ms"]
    assert "input_actions" in header and "input_mouse_dx" in header

    # --- self-check ran and produced a real verdict, not a crash ---
    assert result.qa_status in ("PASS", "WARN", "FAIL")
    print(f"\n[finalize integration] qa_status={result.qa_status}")
    for issue in result.qa_issues:
        print(f"[finalize integration] qa: {issue}")
    for f in result.self_check_failures:
        print(f"[finalize integration] self-check FAIL: {f}")
    for w in result.self_check_warnings:
        print(f"[finalize integration] self-check WARN: {w}")

    # movement_move_y_axis binds 'w' for outer_wilds -> our synthetic W-key
    # presses must have resolved to a real action, proving the native
    # writer's keybind resolution (same path a real capture would use) works.
    assert "movement_move_y_axis" in result.data_quality["distinct_actions"]
