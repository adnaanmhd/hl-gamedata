"""r-loop 8 — translator crash classes (C1).

One theme, four sites: an untrusted player-supplied sidecar value reaching
an unguarded operation and crashing the whole translate/QA path instead of
degrading (raw_int, held-state carry, keybind literals) or failing with a
typed, field-naming error (the raw-only metadata path).
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

# v2 accessed by attribute (not `from … import BundleError`) so that the
# fail-first scratch run fails PER TEST instead of at collection
from translator import keys as K
from translator import v2
from translator import video as V
from translator.keybind import (bound_literals, build_resolver,
                                invert_keybind, resolve_actions)
from translator.trim import rebase_events

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


# ------------------- C1a: held-state carry survives container identities

@pytest.mark.parametrize("bad", [65, ["w"], {"k": "w"}, None])
@pytest.mark.parametrize("etype,field", [("key", "key"),
                                         ("mouse_button", "button")])
def test_rebase_survives_container_identities_before_the_cut(bad, etype,
                                                             field):
    """A list/dict key or button is unhashable; using it as a held-state
    dict key raised TypeError out of rebase_events and crashed every
    translate/retranslate whose sidecar had one such event before the head
    cut. The binner drops non-str identities anyway — nothing is lost."""
    events = [
        {"t": 1_000_000, "type": etype, field: bad, "action": "down"},
        {"t": 6_000_000, "type": "key", "key": "w", "action": "down"},
    ]
    out = rebase_events(events, head_cut_s=5.0, new_duration_s=10.0)
    assert [e.get("key") for e in out] == ["w"]


def test_str_key_held_across_the_cut_is_still_re_pressed():
    """The r-loop-4 carry itself must survive the guard: a str key still
    held at the cut is re-pressed at t=0."""
    events = [{"t": 4_500_000, "type": "key", "key": "w", "action": "down"}]
    out = rebase_events(events, head_cut_s=5.0, new_duration_s=10.0)
    assert len(out) == 1
    assert out[0]["t"] == 0 and out[0]["action"] == "down"
    assert out[0]["key"] == "w"


# --------------------------- C1c: non-str keybind literals degrade, never raise

@pytest.mark.parametrize("bad", [65, None, ["w"], {"k": "w"}])
def test_normalize_literal_type_guards_non_str(bad):
    assert K.normalize_literal(bad) == ""


def test_vk_number_modifier_degrades_to_a_key_only_bind():
    """{"modifier": 16, "key": "w"} — the VK modifier is dropped ALONE;
    the key half still binds and resolves."""
    kb = {"crouch": {"modifier": 16, "key": "w"}}
    assert "w" in bound_literals(kb)
    acts, _ = resolve_actions({"w"}, (False, False), build_resolver(kb))
    assert acts == ["crouch"]


def test_vk_number_key_makes_the_whole_binding_unusable():
    """A key that normalizes empty must emit NOTHING — a bare modifier
    group would fire the semantic on the modifier alone, and an empty ""
    token in bound_literals would defeat the r-loop-7 parsed-but-unusable
    fallback."""
    assert bound_literals({"a": {"modifier": "ctrl", "key": 87}}) \
        == frozenset()
    assert build_resolver({"a": {"modifier": "ctrl", "key": 87}}) == []
    # whitespace-only str literal: same rule, straight branch
    assert bound_literals({"b": "   "}) == frozenset()


def test_invert_keybind_survives_a_vk_number_modifier():
    inv = invert_keybind({"a": {"modifier": 16, "key": "w"}})  # no raise
    assert isinstance(inv, dict)


# ----------------------- C1d: the raw-only metadata path fails attributably

_INFO = V.VideoInfo(width=320, height=180, fps=30.0, frame_count=30,
                    duration_s=1.0, has_audio=False, codec="h264")
_GOOD_REC = {"started_at_utc": "2026-08-14T10:00:00Z"}


@pytest.mark.parametrize("meta", [
    {"recording": ["not", "a", "dict"]},
    {"recording": {}},
    {"recording": {"started_at_utc": None}},
    {"recording": {"started_at_utc": 1723600000}},
    {"recording": {"started_at_utc": "10/08/2026 15:34"}},
])
def test_unusable_started_at_raises_naming_the_field(meta):
    """Pre-fix these raised bare AttributeError/ValueError (kind='session',
    both attempts burned, unattributable fixlog line)."""
    with pytest.raises(v2.BundleError) as e:
        v2.build_session_json(slug="kamla", session_id="s", meta=meta,
                           info=_INFO, head_cut_s=0.0)
    assert "recording.started_at_utc" in str(e.value)


@pytest.mark.parametrize("system", [
    ["x"],
    "junk",
    {"screen_width": "1920x1080", "screen_height": "1080p"},
])
def test_junk_system_block_degrades_to_the_probed_size(system):
    s = v2.build_session_json(
        slug="kamla", session_id="s",
        meta={"recording": dict(_GOOD_REC), "system": system},
        info=_INFO, head_cut_s=0.0)
    assert (s["screen_width_px"], s["screen_height_px"]) == (320, 180)


def test_numeric_screen_dims_still_cast():
    """Control: the tolerant cast keeps accepting what the old int() did,
    plus float-shaped strings."""
    s = v2.build_session_json(
        slug="kamla", session_id="s",
        meta={"recording": dict(_GOOD_REC),
              "system": {"screen_width": "1920", "screen_height": 1080.0}},
        info=_INFO, head_cut_s=0.0)
    assert (s["screen_width_px"], s["screen_height_px"]) == (1920, 1080)


def _bundle(tmp_path, meta_bytes: bytes, video: bytes = b"x"):
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "video.mp4").write_bytes(video)
    (d / "inputs.jsonl").write_text("")
    (d / "metadata.json").write_bytes(meta_bytes)
    return d


def test_truncated_metadata_raises_bundle_error(tmp_path):
    """The read happens before any video work, so a stub video suffices."""
    d = _bundle(tmp_path, b'{"recording": {"start')
    with pytest.raises(v2.BundleError) as e:
        v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                            lag_correct=False)
    assert "metadata.json unreadable" in str(e.value)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg/ffprobe")
def test_whole_file_array_metadata_fails_naming_the_field(tmp_path):
    """A metadata.json that is a JSON array coerces to {} and the translate
    proceeds to the field check, which names what is missing."""
    p = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x180:rate=30:duration=1",
         "-pix_fmt", "yuv420p", str(p)], check=True, capture_output=True)
    d = _bundle(tmp_path, json.dumps([1, 2]).encode(),
                video=p.read_bytes())
    with pytest.raises(v2.BundleError) as e:
        v2.translate_bundle_v2(d, tmp_path / "out", make_rrd=False,
                            head_s=0.0, tail_s=0.0, lag_correct=False)
    assert "recording.started_at_utc" in str(e.value)
