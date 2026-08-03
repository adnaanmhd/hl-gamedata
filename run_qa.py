#!/usr/bin/env python3
"""Standalone runner for qa_checks.py against a local session folder.

Usage:
    uv run --with numpy run_qa.py <session_folder>
"""
import csv
import json
import sys
from pathlib import Path

# qa_checks.py must sit alongside this script
sys.path.insert(0, str(Path(__file__).parent))
from qa_checks import QAConfig, QALevel, run_checks  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: uv run --with numpy {Path(__file__).name} <session_folder>")
        sys.exit(1)

    folder = Path(sys.argv[1]).expanduser().resolve()
    if not folder.is_dir():
        print(f"Not a directory: {folder}")
        sys.exit(1)

    # --- frames.csv ---
    csv_path = folder / "frames.csv"
    if not csv_path.exists():
        print(f"No frames.csv found in {folder}")
        sys.exit(1)

    with csv_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows):,} rows from {csv_path.name}")

    # --- session.json for metadata ---
    session_json = folder / "session.json"
    vendor, game, task_id = "unknown", "unknown", folder.name
    if session_json.exists():
        meta = json.loads(session_json.read_text())
        canon = meta.get("canonical", {})
        vendor  = canon.get("vendor", vendor)
        game    = canon.get("game", game)
        task_id = canon.get("session_id", task_id)

    print(f"Vendor: {vendor}  |  Game: {game}  |  Task: {task_id}\n")

    # --- video ---
    video_path = folder / "video.mp4"

    # --- run checks ---
    # Camera pose/intrinsics are intentionally not captured (input-only
    # capture, no engine-side camera plugin) — skip the checks that depend
    # on that data rather than have every session FAIL on missing columns
    # that were never supposed to be there. See qa_checks.py's own
    # docstring: "Pass a QAConfig to skip or downgrade checks for data that
    # is not provided (e.g. no camera matrices)" — this is exactly that case.
    qa_config = QAConfig(
        camera_matrix=QALevel.SKIP,
        camera_intrinsics=QALevel.SKIP,
        camera_orientation=QALevel.SKIP,
        camera_stationary=QALevel.SKIP,
        camera_spin=QALevel.SKIP,
    )
    result = run_checks(rows, vendor=vendor, task_id=task_id, game=game,
                        qa_config=qa_config, video_path=video_path)

    # --- report ---
    status_icon = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}.get(result.status, "?")
    print(f"Status : {status_icon} {result.status}")
    print(f"Frames : {result.frame_count:,}")
    print(f"FPS    : {result.fps:.1f}")
    print(f"Length : {result.footage_hours * 3600:.1f}s  ({result.footage_hours:.4f}h)")

    if result.issues:
        print(f"\nIssues ({len(result.issues)}):")
        for issue in result.issues:
            print(f"  {issue}")
    else:
        print("\nNo issues found.")


if __name__ == "__main__":
    main()
