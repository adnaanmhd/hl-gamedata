"""r-loop 8 — the confirmed findings (C1-C9).

Three blockers (the retranslate guard killing split children; the host
carve-out re-running partially-applied plans; the daily send's missing
durable counted record), a four-finding seal-semantics cluster, ops-surface
majors, and the STR_SJ_INVALID no-op rewrite. The recurring theme is the
same as loops 4/6/7: most of the serious ones are regressions from earlier
fixes' own blind spots.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import fix as fixmod


# ---------------------- C1d: failed raw translate cleans up its temp tree

def test_failed_raw_translate_does_not_leak_the_temp_tree(tmp_path,
                                                          monkeypatch):
    """The `_translated/` cleanup was success-path-only, so each FAILED
    attempt left a video-sized temp copy inside the working copy — twice
    per session, against a media cap that counts sessions as its bytes
    bound."""
    work = tmp_path / "s"
    work.mkdir()

    def boom(bundle_dir, out_root, **kw):
        d = Path(out_root) / "humynlabs" / "d" / "g" / "s"
        d.mkdir(parents=True)
        (d / "video.mp4").write_bytes(b"v" * 4096)
        raise ValueError("metadata exploded mid-translate")

    monkeypatch.setattr(fixmod, "translate_bundle_v2", boom)
    out = fixmod.apply_fixes(work, {"steps": [("FIX_TRANSLATE_RAW", {})]},
                             game="kamla", dossier_dir=tmp_path / "d")
    assert out["error"]
    assert not (work / "_translated").exists(), \
        "a failed attempt must not leak the video-sized temp tree"
    log = json.loads((tmp_path / "d" / "fixlog.json").read_text())
    assert log[-1]["fixes"][0]["ok"] is False
