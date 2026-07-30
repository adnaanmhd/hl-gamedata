"""
Native v2 finalize pipeline — fix for issue "Native v2 emission" (§4/§6 of
HumynCapture_V2_Fix_Handoff.md) and the E2 self-check gate.

Before this module existed, HumynCapture wrote a v1-shaped raw bundle
(video.mp4 + inputs.jsonl + metadata.json) and a *separate* translator run
(on a different machine, hours or days later) did the trim -> bin -> v2-write
-> QA pipeline. That's the "external translator step" the handoff doc's
deliverable explicitly says to eliminate: "a contributor records a session
and the tool natively emits a spec-v2 delivery folder... no external
translator step".

Rather than re-implement trim/bin/v2-write/QA a second time (duplicating —
and risking drifting from — already-tested logic), this module calls
straight into the `translator` package, which the handoff doc itself names
as "Reference implementation of both writer and validator" (§4) and "the
in-tool self-test" source (§6.4, §7). The only genuinely new work here is:
  1. Apply the A2 anchor correction to the raw events BEFORE handing them to
     the translator (translator has no concept of "capture clock anchor
     bug" — that bug lived in this tool, and correcting it here means
     `translate_bundle_v2(..., lag_correct=False)` should measure ~0 lag
     with NO post-hoc correction, satisfying the handoff's #1 acceptance
     criterion).
  2. Run the C2 black-frame heuristic (translator has no visual check).
  3. Fold the v2 QA result together with capture-time subsystem health
     (app.core.health) into the single `ready_for_upload` gate (E2).

This requires the sibling `translator/` package to be importable
(`PYTHONPATH=<repo root>` — see capture_tool/README.md's packaging note for
how this is wired into the PyInstaller build).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.finalize import blackframe
from app.core.finalize.anchor import AnchorResult, apply_correction
from app.core.health import SubsystemIssue, run_self_check


@dataclass
class FinalizeResult:
    out_dir: Path
    ready_for_upload: bool
    qa_status: str            # "PASS" | "WARN" | "FAIL"
    qa_issues: list[str]
    self_check_failures: list[str]
    self_check_warnings: list[str]
    data_quality: dict[str, Any]
    sync_report: dict[str, Any]


def _load_translator():
    """Deferred import — keeps `translator`'s numpy/opencv-python-headless
    dependency optional for code paths (tests, --help) that never finalize
    a session. Raises a clear error if the sibling package isn't on the
    path; see capture_tool/README.md."""
    try:
        from translator import v2 as translator_v2
    except ImportError as e:
        raise RuntimeError(
            "the sibling `translator` package is not importable — native "
            "v2 finalize requires it on PYTHONPATH (see capture_tool/README.md)"
        ) from e
    return translator_v2


def run_finalize(
    *,
    raw_session_dir: Path,
    out_root: Path,
    anchor: AnchorResult,
    game_slug: str,
    game_slug_is_known: bool,
    subsystem_issues: list[SubsystemIssue],
    frames_dropped: int,
    require_audio: bool = False,
    has_audio: bool = False,
) -> FinalizeResult:
    """The finalize pass described in §6 of the handoff doc:
        trim -> re-anchor & bin -> write v2 files -> self-check
    (trim/bin/write are `translate_bundle_v2`; self-check is this function's
    second half).
    """
    # Real bug found on Windows: translate_bundle_v2/check_session_v2 call
    # into translator/{trim,video,rrd}.py, which invoke bare "ffmpeg"/
    # "ffprobe" (correct for translator's own CLI usage, not for this
    # packaged app). Without the bundled ffmpeg on PATH, every such call
    # raised `FileNotFoundError: [WinError 2] The system cannot find the
    # file specified`. See app.core.paths.ensure_ffmpeg_on_path docstring.
    from app.core.paths import ensure_ffmpeg_on_path
    ensure_ffmpeg_on_path()

    translator_v2 = _load_translator()

    # --- A2: apply the anchor correction to the raw events BEFORE binning. ---
    inputs_path = raw_session_dir / "inputs.jsonl"
    raw_lines = [json.loads(l) for l in inputs_path.read_text().splitlines() if l.strip()]
    corrected = apply_correction(raw_lines, anchor.correction_us)
    inputs_path.write_text("\n".join(json.dumps(e) for e in corrected) + ("\n" if corrected else ""))

    meta_path = raw_session_dir / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta.setdefault("capture_health", {}).update(anchor.to_capture_health_dict())
    meta_path.write_text(json.dumps(meta, indent=2))

    # --- trim -> re-anchor & bin -> write v2 files. lag_correct=False is ---
    # deliberate: we want to MEASURE whether A2 actually fixed the root
    # cause, not paper over a regression the way the old post-hoc rescue did.
    result = translator_v2.translate_bundle_v2(
        raw_session_dir, out_root, lag_correct=False, make_rrd=True)
    out_dir = Path(result["out_dir"])

    # --- independent full v2 contract + sync + off-by-one check. ---
    qa = translator_v2.check_session_v2(out_dir, raw_bundle=raw_session_dir)

    looks_black, black_fraction = blackframe.detect_black_intro(out_dir / "video.mp4")
    if looks_black:
        qa.fail(f"capture region is black for {black_fraction:.0%} of the "
                f"sampled intro — see issue C2 (exclusive-fullscreen bypasses "
                f"composition, or the capture rect is wrong)")

    dq = result["data_quality"]
    sync_status = dq["controls_video_sync"]
    # translate_bundle_v2's own dq field is a human string ("ok"/"unverified"/
    # "corrected ...ms"), not the PASS/WARN/FAIL verdict — pull that from the
    # qa result's sync line instead so health.run_self_check gets the real gate.
    sync_verdict = next(
        (line.split(":", 1)[0] for line in qa.issues
         if "controls-to-video sync" in line), None)

    # No direct "all keys seen" list comes back from translate_bundle_v2;
    # stripped_keys (unbound-but-observed tokens) is the best available
    # proxy for a regression scan — bound tokens went through the same
    # capture-time normalization (keyboard_capture.py) and can't be bad.
    all_keys_seen: set[str] = set(result.get("stripped_keys", {}))
    events_by_type = {
        "key": 1 if dq["keyboard_capture"] == "ok" else 0,
        "mouse_raw": 1 if dq["mouse_capture"] == "ok" else 0,
        "mouse_button": 1 if dq["mouse_buttons"] == "ok" else 0,
    }

    self_check = run_self_check(
        events_by_type=events_by_type,
        all_keys_seen=all_keys_seen,
        frame_count=result["frames"],
        frames_dropped=frames_dropped,
        game_slug=game_slug,
        game_slug_is_known=game_slug_is_known,
        video_readable=True,  # translate_bundle_v2 would have raised otherwise
        subsystem_issues=subsystem_issues,
        sync_status=sync_verdict,
        require_audio=require_audio,
        has_audio=has_audio,
    )

    ready = self_check.ready_for_upload and qa.status != "FAIL"
    return FinalizeResult(
        out_dir=out_dir,
        ready_for_upload=ready,
        qa_status=qa.status,
        qa_issues=qa.issues,
        self_check_failures=self_check.failures,
        self_check_warnings=self_check.warnings + result.get("warnings", []),
        data_quality=dq,
        sync_report=result.get("sync", {}),
    )
