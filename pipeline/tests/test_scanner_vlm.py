import io
import json
import shutil
import subprocess
import urllib.error

import pytest

from pipeline import scanner, vlm
from pipeline.scanner import MotionTimeline


def _tl(diffs, times=None, luma=None):
    n = len(diffs) + 1
    times = times or [i * 0.1 for i in range(n)]
    return MotionTimeline(n_frames=n, fps=10.0, duration_s=times[-1] + 0.1,
                         times_s=times, diffs=diffs,
                         luma=luma or [100.0] * n)


def test_static_windows_finds_run():
    diffs = [5.0] * 20 + [0.5] * 15 + [5.0] * 20
    tl = _tl(diffs)
    wins = scanner.static_windows(tl, ratio=0.4, baseline=5.0, min_s=1.0)
    assert len(wins) == 1
    t0, t1 = wins[0]
    assert abs(t0 - 2.0) < 0.11 and abs(t1 - 3.5) < 0.11


def test_static_windows_needs_min_length():
    diffs = [5.0] * 20 + [0.5] * 3 + [5.0] * 20
    tl = _tl(diffs)
    assert scanner.static_windows(tl, ratio=0.4, baseline=5.0,
                                  min_s=1.0) == []


def test_refine_window_tightens_bounds():
    diffs = [5.0] * 30 + [0.2] * 10 + [5.0] * 30
    tl = _tl(diffs)
    r = scanner.refine_window(tl, 2.6, 4.4, ratio=0.4, baseline=5.0)
    assert r is not None
    w0, w1 = r
    assert abs(w0 - 3.0) < 0.15 and abs(w1 - 4.0) < 0.15


def test_refine_window_none_over_moving_frames():
    tl = _tl([5.0] * 60)
    assert scanner.refine_window(tl, 2.0, 4.0, ratio=0.4,
                                 baseline=5.0) is None


def test_window_motion_stays_inside_span():
    diffs = [0.0] * 50 + [50.0] * 10
    tl = _tl(diffs)
    m = tl.window_motion(1.0, 4.0)     # entirely inside the still part
    assert m == 0.0


def test_baseline_prefers_gameplay_times_falls_back_to_p75():
    diffs = [1.0] * 50 + [9.0] * 50
    tl = _tl(diffs)
    assert tl.baseline([8.0, 9.0]) > 5.0        # gameplay probes in the 9s
    assert tl.baseline([]) >= 1.0               # p75 fallback


def test_zero_input_runs():
    ts = [i * 100 for i in range(100)]          # 100ms per row
    active = [True] * 20 + [False] * 60 + [True] * 20
    runs = scanner.zero_input_runs(ts, active, min_s=3.0)
    assert len(runs) == 1
    t0, t1 = runs[0]
    assert abs(t0 - 2.0) < 0.15 and abs(t1 - 8.0) < 0.15
    assert scanner.zero_input_runs(ts, active, min_s=10.0) == []


@pytest.mark.skipif(not (scanner.available() and shutil.which("ffmpeg")),
                    reason="needs numpy + ffmpeg")
def test_scan_video_detects_still_span(tmp_path):
    # 2s noise + 2s frozen frame + 2s noise, 10fps
    v = tmp_path / "v.mp4"
    cmd = ("ffmpeg -v error "
           "-f lavfi -i 'testsrc2=size=320x180:rate=10:duration=2' "
           "-f lavfi -i 'color=c=blue:size=320x180:rate=10:duration=2' "
           "-f lavfi -i 'testsrc2=size=320x180:rate=10:duration=2' "
           "-filter_complex '[0:v][1:v][2:v]concat=n=3:v=1[out]' "
           f"-map '[out]' -pix_fmt yuv420p {v}")
    subprocess.run(cmd, shell=True, check=True)
    tl = scanner.scan_video(v)
    assert tl.n_frames > 50
    base = tl.baseline([])
    wins = scanner.static_windows(tl, ratio=0.4, baseline=base, min_s=1.0)
    assert any(1.5 < t0 < 2.6 and 3.4 < t1 < 4.6 for t0, t1 in wins), wins


# ------------------------------------------------------------------- vlm

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_response(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_generate_retries_429_with_retry_after(monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_post(url, headers, body, timeout_s=180):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(
                url, 429, "rate", {"Retry-After": "1"}, io.BytesIO(b""))
        return _ok_response("hello")

    monkeypatch.setattr(vlm, "_post", fake_post)
    monkeypatch.setattr(vlm.time, "sleep", lambda s: sleeps.append(s))
    assert vlm.generate("k", "m", [{"text": "x"}]) == "hello"
    assert calls["n"] == 3
    assert sleeps == [1.0, 1.0]        # honored Retry-After


def test_generate_gives_up_after_max_tries(monkeypatch):
    def fake_post(url, headers, body, timeout_s=180):
        raise urllib.error.HTTPError(url, 429, "rate", {}, io.BytesIO(b""))

    monkeypatch.setattr(vlm, "_post", fake_post)
    monkeypatch.setattr(vlm.time, "sleep", lambda s: None)
    with pytest.raises(vlm.VLMError):
        vlm.generate("k", "m", [{"text": "x"}])


def test_generate_safety_block_raises(monkeypatch):
    monkeypatch.setattr(vlm, "_post",
                        lambda *a, **k: {"candidates": []})
    with pytest.raises(vlm.VLMError):
        vlm.generate("k", "m", [{"text": "x"}])


def test_generate_hard_client_error_no_retry(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, headers, body, timeout_s=180):
        calls["n"] += 1
        raise urllib.error.HTTPError(url, 403, "forbidden", {},
                                     io.BytesIO(b""))

    monkeypatch.setattr(vlm, "_post", fake_post)
    with pytest.raises(vlm.VLMError):
        vlm.generate("k", "m", [{"text": "x"}])
    assert calls["n"] == 1


def test_confirm_flag_parses(monkeypatch):
    monkeypatch.setattr(
        vlm, "generate",
        lambda *a, **k: json.dumps({"confirmed": "true",
                                    "what": "steam toast"}))
    ok, what = vlm.confirm_flag("k", "m", b"jpg", "notification")
    assert ok and what == "steam toast"
    monkeypatch.setattr(
        vlm, "generate",
        lambda *a, **k: json.dumps({"confirmed": False, "what": "killfeed"}))
    ok, _ = vlm.confirm_flag("k", "m", b"jpg", "notification")
    assert not ok
