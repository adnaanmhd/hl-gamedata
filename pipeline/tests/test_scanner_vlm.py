import io
import json
import shutil
import subprocess
import urllib.error

import pytest

from pipeline import config as C
from pipeline import scanner, vlm
from pipeline.scanner import MotionTimeline

MODEL = "gemini-3.7-flash"          # rung-0 model of record (R13/R23)


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


class _Router:
    """fake _post that answers per (endpoint, model, key) rule and counts
    every POST by endpoint/model/key."""

    def __init__(self, rule):
        self.rule = rule                     # fn(ep, model, key) -> text
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, url, headers, body, timeout_s=180):
        ep = "vertex" if "aiplatform" in url else "genlang"
        model = url.split("/models/")[1].split(":")[0]
        key = headers.get("x-goog-api-key") or \
            (url.partition("?key=")[2] if "?key=" in url else "")
        self.calls.append((ep, model, key))
        out = self.rule(ep, model, key)
        if isinstance(out, Exception):
            raise out
        return _ok_response(out)

    def n(self, ep=None, model=None, key=None):
        return sum(1 for e, m, k in self.calls
                   if (ep is None or e == ep)
                   and (model is None or m == model)
                   and (key is None or k == key))


def _http(code, headers=None):
    return urllib.error.HTTPError("u", code, "x", headers or {},
                                  io.BytesIO(b""))


@pytest.mark.parametrize("flag", [False, True])
def test_generate_retries_429_with_retry_after(monkeypatch, flag):
    monkeypatch.setattr(C, "VLM_FAILOVER_ENABLED", flag)
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
    assert vlm.generate("k", MODEL, [{"text": "x"}]) == "hello"
    assert calls["n"] == 3
    assert sleeps == [1.0, 1.0]        # honored Retry-After
    assert vlm._rung == 0              # primary answered — no step-down


@pytest.mark.parametrize("flag", [False, True])
def test_generate_gives_up_after_max_tries(monkeypatch, flag):
    monkeypatch.setattr(C, "VLM_FAILOVER_ENABLED", flag)
    r = _Router(lambda ep, m, k: _http(429))
    monkeypatch.setattr(vlm, "_post", r)
    monkeypatch.setattr(vlm.time, "sleep", lambda s: None)
    with pytest.raises(vlm.VLMError):
        vlm.generate("k", MODEL, [{"text": "x"}])
    # every rung exhausted its full §13 ladder on every allowed endpoint
    per_rung = C.VLM_MAX_TRIES * (2 if flag else 1)
    assert len(r.calls) == per_rung * len(C.VLM_MODEL_LADDER)


@pytest.mark.parametrize("flag", [False, True])
def test_generate_safety_block_raises_no_failover_no_ladder(monkeypatch,
                                                            flag):
    """Engine contract row 7 / R23: the endpoint ANSWERED — a safety block
    must not shop the refusal to another endpoint or model."""
    monkeypatch.setattr(C, "VLM_FAILOVER_ENABLED", flag)
    calls = {"n": 0}

    def fake_post(url, headers, body, timeout_s=180):
        calls["n"] += 1
        return {"candidates": []}

    monkeypatch.setattr(vlm, "_post", fake_post)
    with pytest.raises(vlm.VLMError):
        vlm.generate("k", MODEL, [{"text": "x"}])
    assert calls["n"] == 1             # exactly one POST — no second look
    assert vlm._rung == 0


def test_generate_hard_client_error_no_retry_collapses_rungs(monkeypatch):
    """403 burns no retries at any rung (plan §10a): flag off = one POST
    per model rung, straight to VLMError."""
    r = _Router(lambda ep, m, k: _http(403))
    monkeypatch.setattr(vlm, "_post", r)
    with pytest.raises(vlm.VLMError):
        vlm.generate("k", MODEL, [{"text": "x"}])
    assert len(r.calls) == len(C.VLM_MODEL_LADDER)
    assert r.n(ep="vertex") == 0       # failover dark — vertex never tried


# --------------------------------------------- R21 endpoint failover tests

def test_failover_primary_exhausts_secondary_succeeds_and_sticks(
        monkeypatch):
    monkeypatch.setattr(C, "VLM_FAILOVER_ENABLED", True)
    r = _Router(lambda ep, m, k: _http(429) if ep == "genlang" else "ok")
    monkeypatch.setattr(vlm, "_post", r)
    monkeypatch.setattr(vlm.time, "sleep", lambda s: None)
    assert vlm.generate("k", MODEL, [{"text": "x"}]) == "ok"
    assert r.n(ep="genlang") == C.VLM_MAX_TRIES
    assert r.n(ep="vertex") == 1
    assert vlm._which == 1 and vlm._rung == 0
    assert vlm.generate("k", MODEL, [{"text": "x"}]) == "ok"
    assert r.n(ep="genlang") == C.VLM_MAX_TRIES     # stickiness: unchanged
    assert r.n(ep="vertex") == 2


def test_failover_both_fail_raises_vlmerror_no_key_leak(monkeypatch):
    monkeypatch.setattr(C, "VLM_FAILOVER_ENABLED", True)
    r = _Router(lambda ep, m, k: _http(403 if ep == "genlang" else 404))
    monkeypatch.setattr(vlm, "_post", r)
    with pytest.raises(vlm.VLMError) as ei:
        vlm.generate("sekrit", MODEL, [{"text": "x"}])
    # hard errors burn no retries: 2 POSTs per model rung
    assert len(r.calls) == 2 * len(C.VLM_MODEL_LADDER)
    msg = str(ei.value)
    assert "key=" not in msg and "sekrit" not in msg
    assert "https://" not in msg               # tags only, never URLs
    assert "vertex" in msg or "genlang" in msg


def test_primary_healthy_secondary_never_called(monkeypatch):
    monkeypatch.setattr(C, "VLM_FAILOVER_ENABLED", True)
    r = _Router(lambda ep, m, k: "fine")
    monkeypatch.setattr(vlm, "_post", r)
    assert vlm.generate("k", MODEL, [{"text": "x"}]) == "fine"
    assert r.n(ep="vertex") == 0
    assert vlm._which == 0


def test_403_switches_without_burning_retries(monkeypatch):
    monkeypatch.setattr(C, "VLM_FAILOVER_ENABLED", True)
    r = _Router(lambda ep, m, k: _http(403) if ep == "genlang" else "ok")
    monkeypatch.setattr(vlm, "_post", r)
    assert vlm.generate("k", MODEL, [{"text": "x"}]) == "ok"
    assert r.n(ep="genlang") == 1 and r.n(ep="vertex") == 1
    assert vlm._rung == 0              # endpoint switch, not a rung step


def test_sticky_vertex_fails_back_to_genlang(monkeypatch):
    monkeypatch.setattr(C, "VLM_FAILOVER_ENABLED", True)
    monkeypatch.setattr(vlm, "_which", 1)
    r = _Router(lambda ep, m, k: _http(429) if ep == "vertex" else "back")
    monkeypatch.setattr(vlm, "_post", r)
    monkeypatch.setattr(vlm.time, "sleep", lambda s: None)
    assert vlm.generate("k", MODEL, [{"text": "x"}]) == "back"
    assert r.n(ep="vertex") == C.VLM_MAX_TRIES      # tried sticky first
    assert r.n(ep="genlang") == 1
    assert vlm._which == 0


# --------------------------------------------------- R23 quota ladder tests

def test_ladder_steps_down_on_429_exhaustion_and_sticks(monkeypatch):
    r = _Router(lambda ep, m, k: _http(429) if m == MODEL else "downshift")
    monkeypatch.setattr(vlm, "_post", r)
    monkeypatch.setattr(vlm.time, "sleep", lambda s: None)
    vlm.begin_session()
    assert vlm.generate("k", MODEL, [{"text": "x"}]) == "downshift"
    assert r.n(model=MODEL) == C.VLM_MAX_TRIES
    assert r.n(model="gemini-3.5-flash") == 1
    assert vlm._rung == 1
    assert vlm.session_models() == [{"rung": 1, "key": "current",
                                     "model": "gemini-3.5-flash",
                                     "endpoint": "genlang"}]
    # second call starts AT the sticky rung — 3.7 is not re-paid
    assert vlm.generate("k", MODEL, [{"text": "x"}]) == "downshift"
    assert r.n(model=MODEL) == C.VLM_MAX_TRIES      # unchanged
    assert r.n(model="gemini-3.5-flash") == 2


def test_prev_key_rung_fires_only_below_model_rungs(monkeypatch):
    monkeypatch.setattr(vlm, "_prev_key_cache", "prevkey")
    r = _Router(lambda ep, m, k: "rescue" if k == "prevkey" else _http(429))
    monkeypatch.setattr(vlm, "_post", r)
    monkeypatch.setattr(vlm.time, "sleep", lambda s: None)
    vlm.begin_session()
    assert vlm.generate("k", MODEL, [{"text": "x"}]) == "rescue"
    # all three model rungs exhausted on the current key first
    assert r.n(key="k") == C.VLM_MAX_TRIES * len(C.VLM_MODEL_LADDER)
    assert r.n(key="prevkey") == 1
    assert r.calls[-1][1] == MODEL     # prev-key rung runs the rung-0 model
    assert vlm._rung == 3
    assert vlm.session_models()[-1] == {"rung": 3, "key": "prev",
                                        "model": MODEL,
                                        "endpoint": "genlang"}


def test_prev_key_unarmed_goes_to_hold(monkeypatch):
    r = _Router(lambda ep, m, k: _http(429))
    monkeypatch.setattr(vlm, "_post", r)
    monkeypatch.setattr(vlm.time, "sleep", lambda s: None)
    with pytest.raises(vlm.VLMError):
        vlm.generate("k", MODEL, [{"text": "x"}])
    assert r.n(key="k") == C.VLM_MAX_TRIES * len(C.VLM_MODEL_LADDER)
    assert all(k == "k" for _, _, k in r.calls)     # no phantom prev key


def test_injected_rung_start_skips_upper_rungs(monkeypatch):
    """The worker seed: starting at an injected rung must not re-pay the
    upper rungs' discovery ladders (R23 run-level stickiness)."""
    monkeypatch.setattr(vlm, "_rung", 2)
    r = _Router(lambda ep, m, k: "cheap")
    monkeypatch.setattr(vlm, "_post", r)
    vlm.begin_session()
    assert vlm.generate("k", MODEL, [{"text": "x"}]) == "cheap"
    assert r.calls == [("genlang", C.VLM_MODEL_LADDER[2], "k")]
    assert vlm._rung == 2


def test_models_used_lands_in_verdict_metrics():
    from pipeline.validate import _metrics
    used = [{"rung": 1, "key": "current", "model": "gemini-3.5-flash",
             "endpoint": "genlang"}]
    m = _metrics({}, {"models_used": used})
    assert m["models_used"] == used
    assert _metrics({}, {})["models_used"] == []


def test_batch_message_fallback_line():
    from datetime import datetime
    from pipeline import reports
    base = dict(batch_no=7, finished_ist=datetime(2026, 8, 15, 21, 0,
                                                  tzinfo=C.IST),
                duration_min=12, delivered=3, total=4, auto_fixed=0,
                rejected=1, hours_delta=0.5)
    quiet = reports.build_batch_message(reports.BatchStats(**base), None)
    assert "fallback" not in quiet
    loud = reports.build_batch_message(
        reports.BatchStats(**base, on_fallback=2), None)
    assert "2 on fallback model" in loud.splitlines()[-1]


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
