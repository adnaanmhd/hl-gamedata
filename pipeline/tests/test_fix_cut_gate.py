"""Step-3 tests: fix planning, gate §1.5.5 compliance, cutter segments,
mechanical CSV fixes. The synthetic-session fixtures build a real (tiny)
video so probe/PTS paths run for real; ffmpeg-less environments skip those."""
import csv
import json
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from pipeline import cutter, fix, gate
from translator.v2 import V2_FRAME_COLS

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


# ------------------------------------------------------------- plan_fixes

def _r(code, blocking=True, fixable=True, params=None):
    return {"code": code, "blocking": blocking, "fixable": fixable,
            "params": params or {}, "evidence": ""}


def test_plan_retranslate_supersedes_csv_fixes():
    plan = fix.plan_fixes(
        [_r("SYN_LAG_CONST", params={"lag_ms": 100}),
         _r("INP_OSKEYS")],
        game="kamla", has_raw=True)
    ids = [s[0] for s in plan["steps"]]
    assert "FIX_RETRANSLATE" in ids
    assert "FIX_LAGSHIFT_CSV" not in ids
    # hygiene isn't cleared by retranslate in the plan builder only when
    # queued independently; OS-keys ride along with the re-translate
    assert plan["unfixable"] == []


def test_plan_no_raw_uses_csv_level_fixes():
    plan = fix.plan_fixes(
        [_r("SYN_LAG_CONST", params={"lag_ms": 100}),
         _r("SYN_TS_NOT_PTS")],
        game="kamla", has_raw=False)
    ids = [s[0] for s in plan["steps"]]
    assert "FIX_LAGSHIFT_CSV" in ids and "FIX_TSREPAIR_PTS" in ids
    assert ids[-1] == "FIX_SESSIONJSON_RECOMPUTE"


def test_plan_ow_fanout_context_fix_kamla_unfixable():
    plan = fix.plan_fixes([_r("INP_FANOUT")], game="outer_wilds",
                          has_raw=False)
    assert [s[0] for s in plan["steps"]][0] == "FIX_ACTIONS_CONTEXT"
    plan2 = fix.plan_fixes([_r("INP_FANOUT")], game="kamla", has_raw=False)
    assert plan2["steps"] == [] and plan2["unfixable"] == ["INP_FANOUT"]


def test_plan_cut_short_circuits_rest():
    plan = fix.plan_fixes(
        [_r("CNT_MID_NONGAMEPLAY", params={"cut": [100.0, 110.0]}),
         _r("INP_OSKEYS")],
        game="kamla", has_raw=False)
    assert plan["steps"][-1][0] == "FIX_CUT_SEGMENTS"


def test_plan_head_edge_retrims():
    plan = fix.plan_fixes(
        [_r("CNT_EDGE_NONGAMEPLAY", params={"edge": "head",
                                            "cut_at_s": 12.5})],
        game="kamla", has_raw=False)
    assert plan["steps"][0] == ("FIX_RETRIM_HEAD", {"head_s": 12.5})


def test_plan_gate_window():
    plan = fix.plan_fixes(
        [_r("INP_FROZEN_ACTIONS", params={"t0": 60.0, "t1": 61.5})],
        game="outer_wilds", has_raw=False)
    assert ("FIX_GATE_WINDOW", {"windows": [(60.0, 61.5)]}) in plan["steps"]


def test_plan_nonblocking_ignored_unfixable_recorded():
    plan = fix.plan_fixes(
        [_r("CNT_AUDIO_MISSING", blocking=False),
         _r("CNT_SHORT", fixable=False)],
        game="kamla", has_raw=True)
    assert plan["steps"] == []
    assert plan["unfixable"] == ["CNT_SHORT"]


# ------------------------------------------------------------ shift math

def test_shift_input_rows_moves_later_and_blanks_edges():
    rows = [[str(i), "k" + str(i)] for i in range(5)]
    out = fix.shift_input_rows(rows, 2, [1], {1: ""})
    assert [r[1] for r in out] == ["", "", "k0", "k1", "k2"]
    out2 = fix.shift_input_rows(rows, -1, [1], {1: ""})
    assert [r[1] for r in out2] == ["k1", "k2", "k3", "k4", ""]
    assert [r[0] for r in out] == [str(i) for i in range(5)]  # ids untouched


# ------------------------------------------------------- synthetic session

def _make_video(path, seconds, fps=10):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc2=size=320x180:rate={fps}:duration={seconds}",
         "-g", "20", "-pix_fmt", "yuv420p", "-y", str(path)], check=True)


def _make_session(tmp_path, seconds=150, fps=10, name="synthetic"):
    """A qa-v2-shaped synthetic session: real video, CSV rows on real PTS,
    Kamla keybinds, sparse mouse motion (sync check SKIPs, not FAILs)."""
    from translator import video as V
    d = tmp_path / name
    d.mkdir()
    _make_video(d / "video.mp4", seconds, fps)
    info = V.probe(d / "video.mp4")
    pts = V.frame_pts(d / "video.mp4")
    assert len(pts) == info.frame_count
    cam_null = [""] * 29
    keys_cycle = [("W", "movement_move"), ("A", "movement_move"),
                  ("S", "movement_move"), ("E", "interact")]
    rows = []
    for i in range(info.frame_count):
        k, a = keys_cycle[(i // 40) % 4]
        if k == "W":
            a = "move_up"
        elif k == "A":
            a = "move_left"
        elif k == "S":
            a = "move_down"
        else:
            a = "interact"
        dx = "7.0" if i % 200 == 0 else "0.0"
        btn = "Left" if i % 300 == 5 else ""
        rows.append([str(i), str(int(round(pts[i] / 1000)))] + cam_null
                    + [k, a, btn, dx, "0.0"])
    with (d / "frames.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(V2_FRAME_COLS)
        w.writerows(rows)
    created = datetime(2026, 8, 14, 10, 0, 0, tzinfo=timezone.utc)
    (d / "session.json").write_text(json.dumps({
        "vendor_name": "humynlabs", "game_title": "Kamla",
        "session_id": name,
        "created_at_utc": created.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        "ended_at_utc": "x", "duration_ms": 0, "duration_seconds": 0,
        "fps": 0, "frame_count": 0, "record_width_px": 0,
        "record_height_px": 0, "screen_width_px": 320,
        "screen_height_px": 180, "localization": "en-IN", "platform": "PC",
        "input_mouse_convention": {
            "maps_to": "camera_look_velocity", "dx_positive": "right",
            "dx_negative": "left", "dy_positive": "down",
            "dy_negative": "up"}}))
    fix.fix_sessionjson_recompute(d, "kamla")
    from translator import rrd as rrdmod
    rrdmod.write_script(d)
    (d / "session.rrd").touch()
    return d


needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")


@needs_ffmpeg
def test_synthetic_session_passes_qa(tmp_path):
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80)
    r = check_session_v2(d)
    assert r.status != "FAIL", r.issues


@needs_ffmpeg
def test_gate_windows_blanks_keys_and_actions(tmp_path):
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80)
    res = gate.gate_windows(d, [(30.0, 32.0)])
    assert res["gated_frames"] > 0
    with (d / "frames.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        t = int(r["timestamp_ms"]) / 1000.0
        if 30.0 <= t <= 32.0:
            # §1.5.5: keys and actions blanked TOGETHER; raw dx/dy + buttons
            # stay as captured (F1)
            assert r["input_keys"] == "" and r["input_actions"] == ""
            assert r["input_mouse_dx"] != ""
        elif t < 29.5 or t > 32.5:
            assert r["input_keys"] != ""
    assert check_session_v2(d).status != "FAIL"


def test_complement_windows():
    assert cutter.complement_windows([(10.0, 20.0)], 100.0) == \
        [(0.0, 10.0), (20.0, 100.0)]
    assert cutter.complement_windows([(0.0, 5.0), (4.0, 8.0)], 10.0) == \
        [(8.0, 10.0)]
    assert cutter.complement_windows([], 10.0) == [(0.0, 10.0)]


@needs_ffmpeg
def test_cutter_synthetic_mid_pause(tmp_path):
    """§18 step-3 acceptance: synthetic mid-clip pause -> segments that each
    pass qa-v2 and clear the 70 s bar, with ids -pN and recomputed
    session.json."""
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=150)
    keep = cutter.complement_windows([(71.0, 73.0)], 150.0)
    res = cutter.cut_segments(d, keep, tmp_path / "out")
    assert len(res["segments"]) == 2, res
    for i, seg in enumerate(res["segments"], 1):
        assert seg["id"].endswith(f"-p{i}")
        assert seg["duration_s"] >= 70.0
        sdir = tmp_path / "out" / seg["id"]
        r = check_session_v2(sdir)
        assert r.status != "FAIL", (seg["id"], r.issues)
        s = json.loads((sdir / "session.json").read_text())
        assert s["session_id"] == seg["id"]
        assert s["frame_count"] == seg["frames"]
        with (sdir / "frames.csv").open(newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == seg["frames"]
        assert rows[0]["frame_id"] == "0"
        assert int(rows[0]["timestamp_ms"]) <= 200
    # second segment's created_at is offset by its source position
    s1 = json.loads((tmp_path / "out" / res["segments"][0]["id"] /
                     "session.json").read_text())
    s2 = json.loads((tmp_path / "out" / res["segments"][1]["id"] /
                     "session.json").read_text())
    c1 = datetime.fromisoformat(s1["created_at_utc"].replace("Z", "+00:00"))
    c2 = datetime.fromisoformat(s2["created_at_utc"].replace("Z", "+00:00"))
    assert 70.0 <= (c2 - c1).total_seconds() <= 80.0


@needs_ffmpeg
def test_cutter_drops_short_segment(tmp_path):
    d = _make_session(tmp_path, seconds=150)
    keep = cutter.complement_windows([(40.0, 45.0)], 150.0)  # 40s + 105s
    res = cutter.cut_segments(d, keep, tmp_path / "out")
    assert len(res["segments"]) == 1
    assert res["dropped"] and "minimum" in res["dropped"][0]["why"]


@needs_ffmpeg
def test_mechanical_fixes(tmp_path):
    from translator.v2 import check_session_v2
    d = _make_session(tmp_path, seconds=80)
    # break things: camera cell, sentinel, extra tail row
    header, rows = fix._read_csv(d)
    rows[5][10] = "0.7"
    rows[6][-2] = ""
    rows.append(list(rows[-1]))
    fix._write_csv(d, header, rows)
    assert fix.fix_camera_null(d).startswith("nulled 1")
    fix.fix_rows_surgery(d)
    fix.fix_sentinels(d)
    fix.fix_sessionjson_recompute(d, "kamla")
    r = check_session_v2(d)
    assert r.status != "FAIL", r.issues


@needs_ffmpeg
def test_apply_fixes_writes_fixlog_and_stops_on_cut(tmp_path):
    d = _make_session(tmp_path, seconds=150)
    dossier = tmp_path / "dossier"
    plan = {"steps": [("FIX_CUT_SEGMENTS", {"cut": [(71.0, 73.0)]}),
                      ("FIX_KEY_HYGIENE", {})],
            "unfixable": []}
    out = fix.apply_fixes(d, plan, game="kamla", dossier_dir=dossier,
                          split_root=tmp_path / "kids")
    assert out["error"] is None
    assert out["children"] and len(out["children"]["segments"]) == 2
    log = json.loads((dossier / "fixlog.json").read_text())
    fixes = [f["fix"] for f in log[-1]["fixes"]]
    assert fixes == ["FIX_CUT_SEGMENTS"]      # hygiene never ran (children)


@needs_ffmpeg
def test_key_hygiene_strips_and_reresolves(tmp_path):
    d = _make_session(tmp_path, seconds=80)
    header, rows = fix._read_csv(d)
    ki = header.index("input_keys")
    ai = header.index("input_actions")
    rows[10][ki] = "W|PrintScreen|LShift|RShift"
    rows[10][ai] = "move_up"
    fix._write_csv(d, header, rows)
    fix.fix_key_hygiene(d, "kamla")
    _, rows2 = fix._read_csv(d)
    toks = rows2[10][ki].split("|")
    assert "PrintScreen" not in toks              # OS key stripped
    assert not {"LShift", "RShift"} <= set(toks)  # bleed resolved
    assert "W" in toks and rows2[10][ai] == "move_up"


@needs_ffmpeg
def test_remux_repairs_readable_file(tmp_path):
    d = _make_session(tmp_path, seconds=80)
    assert "remux" in fix.fix_remux(d)
    from translator import video as V
    assert V.probe(d / "video.mp4").frame_count > 0


def test_plan_drops_gate_when_retrim_planned():
    plan = fix.plan_fixes(
        [_r("CNT_EDGE_NONGAMEPLAY", params={"edge": "head",
                                            "cut_at_s": 12.5}),
         _r("INP_FROZEN_ACTIONS", params={"t0": 60.0, "t1": 61.5})],
        game="outer_wilds", has_raw=False)
    ids = [s[0] for s in plan["steps"]]
    assert "FIX_RETRIM_HEAD" in ids
    assert "FIX_GATE_WINDOW" not in ids     # pre-trim coords are stale


def test_plan_rows_surgery_precedes_cut():
    plan = fix.plan_fixes(
        [_r("CNT_MID_NONGAMEPLAY", params={"cut": [100.0, 110.0]}),
         _r("STR_ROWS_MISMATCH")],
        game="kamla", has_raw=False)
    ids = [s[0] for s in plan["steps"]]
    assert ids.index("FIX_ROWS_SURGERY") < ids.index("FIX_CUT_SEGMENTS")
