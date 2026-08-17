"""r-loop 3 — qa-v2 must FAIL, never crash.

PIPELINE_CONTINUOUS_DESIGN.md §12 states the invariant for this module:
"malformed session.json / frames.csv yields FAIL verdicts, never checker
crashes". A crash is not a harmless difference — it escapes analyze() into
the driver, which writes QUARANTINED ("validation crashed") plus a Telegram
alert. That is a TERMINAL state with no automatic re-entry, and until r-loop
3 its media was never reclaimed either. So every crash here converts an
actionable, often FIXABLE reject into a manual queue entry plus leaked disk.

The r-loop-1 hardening covered numeric session.json fields and r-loop-2
covered container types and row shape; these are the paths both passes
stopped short of.
"""
import json
import shutil
import subprocess

import pytest

from translator.v2 import V2_FRAME_COLS, check_session_v2

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None
                                or shutil.which("ffprobe") is None,
                                reason="needs ffmpeg/ffprobe")


@pytest.fixture(scope="module")
def tiny_mp4(tmp_path_factory):
    """A REAL 2-frame mp4. The checker probes the video, so a stub file
    makes ffprobe fail and masks the defect under test."""
    p = tmp_path_factory.mktemp("vid") / "tiny.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=black:s=320x180:r=30:d=0.1",
         "-pix_fmt", "yuv420p", str(p)], check=True, capture_output=True)
    return p.read_bytes()

_SJ = {
    "vendor_name": "humynlabs", "game_title": "Kamla", "session_id": "s",
    "created_at_utc": "2026-08-14T10:00:00Z",
    "ended_at_utc": "2026-08-14T10:02:00Z", "duration_ms": 120000,
    "duration_seconds": 120.0, "fps": 30.0, "frame_count": 2,
    "record_width_px": 1920, "record_height_px": 1080,
    "screen_width_px": 1920, "screen_height_px": 1080,
    "localization": "en-US", "platform": "pc",
    "input_mouse_convention": {},
}


def _session(tmp_path, *, sj=None, frames_bytes=None, video=b"x"):
    d = tmp_path / "sess"
    d.mkdir(parents=True)
    (d / "video.mp4").write_bytes(video)
    for f in ("session.rrd", "rrd_creation.py"):
        (d / f).write_bytes(b"x")
    (d / "session.json").write_text(json.dumps({**_SJ, **(sj or {})}))
    n = len(V2_FRAME_COLS)
    body = (",".join(V2_FRAME_COLS) + "\n"
            + ",".join(["0"] * n) + "\n"
            + ",".join(["1"] * n) + "\n")
    (d / "frames.csv").write_bytes(frames_bytes
                                   if frames_bytes is not None
                                   else body.encode())
    return d


def test_non_string_game_title_fails_not_crashes(tmp_path, tiny_mp4):
    """game_title was checked for PRESENCE only and then handed to
    keybinds' `.lower()`. A list/number/null raised AttributeError with NO
    reason recorded at all, so a session whose every structural check had
    passed became "validation crashed" instead of a typed reject."""
    for n, bad in enumerate((["Kamla"], 42, None, {"name": "Kamla"})):
        res = check_session_v2(_session(tmp_path / f"t{n}",
                                        sj={"game_title": bad},
                                        video=tiny_mp4))
        assert res.status == "FAIL"
        assert any("game_title" in i for i in res.issues), res.issues


def test_non_utf8_frames_csv_fails_not_crashes(tmp_path):
    """The session.json read 20 lines earlier was guarded; this one was
    not. A frames.csv exported in cp1252 — one accented character or smart
    quote in a key token is enough — raised UnicodeDecodeError. rclone
    copies the bytes faithfully and ingest md5-verifies only video.mp4, so
    it arrives intact and crashes the final gate."""
    n = len(V2_FRAME_COLS)
    good = (",".join(V2_FRAME_COLS) + "\n" + ",".join(["0"] * n) + "\n")
    blob = bytearray(good.encode())
    blob[-3] = 0xFF                     # invalid UTF-8
    res = check_session_v2(_session(tmp_path, frames_bytes=bytes(blob)))
    assert res.status == "FAIL"
    assert any("unreadable" in i or "header" in i for i in res.issues), \
        res.issues


def test_empty_frames_csv_fails_not_crashes(tmp_path):
    res = check_session_v2(_session(tmp_path, frames_bytes=b""))
    assert res.status == "FAIL"


def test_malformed_dxdy_cell_fails_without_crashing_the_sync_measure(
        tmp_path, tiny_mp4):
    """The checker already FAILs a non-float dx/dy cell, then handed those
    same raw cells to sync.input_track_from_rows whose bare float() raised
    ValueError and destroyed the verdict — turning a fixable STR_SENTINELS
    reject into a quarantine."""
    n = len(V2_FRAME_COLS)
    col = {c: i for i, c in enumerate(V2_FRAME_COLS)}
    row = ["0"] * n
    row[col["input_mouse_dx"]] = "abc"
    body = (",".join(V2_FRAME_COLS) + "\n" + ",".join(row) + "\n").encode()
    res = check_session_v2(_session(tmp_path, frames_bytes=body,
                                    video=tiny_mp4))
    assert res.status == "FAIL"          # and crucially: it RETURNED
    assert any("float-formatted" in i for i in res.issues), res.issues


def test_qa_hardening_fail_strings_map_to_fixable_reasons():
    """r-loop-1/2 added FAIL strings that matched no needle in
    validate._QA_STR_MAP, so they fell through to QA_FAIL_UNMAPPED — which
    is blocking AND UNFIXABLE when has_raw is False (split children always,
    and any zip carrying only out/). Those sessions were REJECTED without a
    single fix attempt, even though FIX_SESSIONJSON_REWRITE recomputes
    exactly the fields the FAILs describe."""
    from pipeline.validate import _map_qa_issues
    for msg, want in (
            ("session.json numeric fields malformed (non-numeric type)",
             "STR_SJ_INVALID"),
            ("session.json timestamps unparseable", "STR_SJ_INVALID"),
            ("session.json timestamps mix naive and aware",
             "STR_SJ_INVALID"),
            ("session.json game_title not a string: ['Kamla']",
             "STR_SJ_INVALID"),
            ("timestamp_ms column unparseable (non-integer or short row)",
             "STR_TS_NONMONO")):
        reasons = []
        _map_qa_issues([f"FAIL: {msg}"], reasons, has_raw=False)
        assert [r["code"] for r in reasons] == [want], msg
        assert reasons[0]["fixable"] is True, (
            f"{msg!r} must be fixable — it is repairable from the video, "
            f"and unfixable means rejected with zero fix attempts")


def test_ragged_rows_stay_unmapped_because_no_fix_clears_them():
    """The complement: mapping a defect to a fix that CANNOT clear it burns
    both attempts and two paid VLM sweeps before rejecting anyway. Lost
    columns are genuinely unfixable without the raw sidecars, so
    QA_FAIL_UNMAPPED (fixable only when has_raw, via retranslate) is the
    correct answer."""
    from pipeline.validate import _map_qa_issues
    reasons = []
    _map_qa_issues(["FAIL: frames.csv has 3 short/ragged row(s) "
                    "(first at row 7: 2 of 36 columns)"],
                   reasons, has_raw=False)
    assert [r["code"] for r in reasons] == ["QA_FAIL_UNMAPPED"]
    assert reasons[0]["fixable"] is False
    reasons = []
    _map_qa_issues(["FAIL: frames.csv has 3 short/ragged row(s)"],
                   reasons, has_raw=True)
    assert reasons[0]["fixable"] is True     # retranslate from sidecars

    # "frame_id column unparseable" belongs to the same class (r-loop 4):
    # STR_ROWS_MISMATCH plans FIX_ROWS_SURGERY, which only truncates or
    # appends up to 2 TAIL rows and never rewrites a frame_id cell, so it
    # no-ops and spends both attempts plus two paid VLM sweeps reaching the
    # same reject — with a worse, untyped operator-facing reason.
    reasons = []
    _map_qa_issues(["FAIL: frame_id column unparseable (non-integer or "
                    "short row)"], reasons, has_raw=False)
    assert [r["code"] for r in reasons] == ["QA_FAIL_UNMAPPED"]
