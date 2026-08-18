"""r-loop 9 — translator hardening (D1a: #3 + #16).

Same theme as r-loop 8's C1: an untrusted player-supplied metadata value
reaching an unguarded operation. Four shapes still crashed untyped out of
translate_bundle_v2 (kind='session', both attempts burned, unattributable
fixlog line): screen dims of Infinity/1e999 (OverflowError past _px's two
arms), a parseable-but-extreme started_at_utc (OverflowError from the
datetime arithmetic AFTER the full trim+bin wall-clock), a numeric
session_id (TypeError in the Path join), and a numeric exe_name (TypeError
in game_key_from_name's re.sub).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

# v2 accessed by attribute so the fail-first scratch run fails PER TEST
from translator import v2
from translator import video as V

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")

_INFO = V.VideoInfo(width=320, height=180, fps=30.0, frame_count=30,
                    duration_s=1.0, has_audio=False, codec="h264")
_GOOD_REC = {"started_at_utc": "2026-08-14T10:00:00Z"}


# ---------------------------- #3: _px lets OverflowError escape on inf

@pytest.mark.parametrize("dim", [float("inf"), float("-inf"), 1e999],
                         ids=["inf", "neg_inf", "1e999"])
def test_infinite_screen_dims_degrade_to_the_probed_size(dim):
    """json.loads accepts Infinity and 1e999; int() on either raised
    OverflowError straight past _px's (TypeError, ValueError) arms — the
    exact class raw_int closed in r-loop 8."""
    s = v2.build_session_json(
        slug="kamla", session_id="s",
        meta={"recording": dict(_GOOD_REC),
              "system": {"screen_width": dim, "screen_height": dim}},
        info=_INFO, head_cut_s=0.0)
    assert (s["screen_width_px"], s["screen_height_px"]) == (320, 180)


# ------------------- #16: extreme-but-parseable started_at_utc is typed

def test_out_of_range_started_at_raises_naming_the_field():
    """'9999-12-31T23:59:59Z' parses fine; the + timedelta arithmetic then
    raised OverflowError('date value out of range') untyped."""
    with pytest.raises(v2.BundleError) as e:
        v2.build_session_json(
            slug="kamla", session_id="s",
            meta={"recording": {"started_at_utc": "9999-12-31T23:59:59Z"}},
            info=_INFO, head_cut_s=10.0)
    assert "recording.started_at_utc" in str(e.value)
    assert "out of range" in str(e.value)


# ---------------- #16: numeric session_id / exe_name degrade, never raise

def _bundle(tmp_path, meta: dict):
    p = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x180:rate=30:duration=1",
         "-pix_fmt", "yuv420p", str(p)], check=True, capture_output=True)
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "video.mp4").write_bytes(p.read_bytes())
    (d / "inputs.jsonl").write_text("")
    (d / "metadata.json").write_text(json.dumps(meta))
    return d


@needs_ffmpeg
def test_numeric_session_id_falls_back_to_the_folder_name(tmp_path):
    """A numeric session_id crashed the out_dir Path join untyped; the
    bundle folder name is the established fallback identity."""
    d = _bundle(tmp_path, {"session_id": 12345,
                           "game": {"name": "Kamla"},
                           "recording": dict(_GOOD_REC)})
    rep = v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    assert rep["session"] == "bundle"
    s = json.loads((Path(rep["out_dir"]) / "session.json").read_text())
    assert s["session_id"] == "bundle"


@needs_ffmpeg
def test_numeric_exe_name_is_ignored_not_crashed(tmp_path):
    """A numeric exe_name reached keybinds.game_key_from_name's re.sub and
    crashed untyped; non-str is treated as absent — the game name alone
    still resolves the slug."""
    d = _bundle(tmp_path, {"game": {"name": "Kamla", "exe_name": 123},
                           "recording": dict(_GOOD_REC)})
    rep = v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                                 head_s=0.0, tail_s=0.0, lag_correct=False)
    assert "/kamla/" in rep["out_dir"].replace("\\", "/")
