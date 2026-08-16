"""Isolated-flip tolerance in the dx/dy raw recomputation (Adnaan
2026-08-16): single-frame attribution jitter warns; runs and volume still
block. Pinned against the 08-16 fix-failed loop (5 of 10 rows were
isolated-flip stalemates)."""
import json

from translator.v2 import V2Result, _verify_against_raw

_START = "2026-08-16T10:00:00+00:00"
_COL = {"input_mouse_dx": 0, "input_mouse_dy": 1}


def _run_check(tmp_path, n_rows, flip_idx):
    """CSV claims dx=1 on flip_idx rows; raw has no events at all — every
    flip row is a mismatch. 30fps pts grid."""
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    (raw / "metadata.json").write_text(json.dumps(
        {"recording": {"started_at_utc": _START}}))
    (raw / "inputs.jsonl").write_text("")
    dt_us = 33333
    pts = [i * dt_us for i in range(n_rows)]
    rows = [["1.0", "0.0"] if i in set(flip_idx) else ["0.0", "0.0"]
            for i in range(n_rows)]
    s = {"created_at_utc": _START,
         "duration_seconds": n_rows * dt_us / 1e6}
    r = V2Result("t")
    _verify_against_raw(tmp_path, raw, s, rows, _COL, pts, r)
    return r


def test_isolated_flips_warn_not_fail(tmp_path):
    r = _run_check(tmp_path, 1000, [100, 500])       # 0.2%, runs of 1
    assert r.status == "WARN"
    assert any("isolated dx/dy attribution flips" in i for i in r.issues)
    assert not any(i.startswith("FAIL:") for i in r.issues)


def test_run_of_three_blocks(tmp_path):
    r = _run_check(tmp_path, 1000, [100, 101, 102])  # 0.3% but run=3
    assert r.status == "FAIL"
    assert any("raw recomputation" in i and "max run 3" in i
               for i in r.issues)


def test_volume_over_half_percent_blocks(tmp_path):
    idx = list(range(0, 1200, 200)) + [50, 350, 650, 850, 1050, 1150]
    r = _run_check(tmp_path, 1200, idx)              # 12/1200 = 1%, isolated
    assert r.status == "FAIL"


def test_exactly_half_percent_isolated_warns(tmp_path):
    r = _run_check(tmp_path, 1000, [0, 200, 400, 600, 800])   # 5/1000
    assert r.status == "WARN"                        # block is > 0.5%


def test_clean_rows_still_pass(tmp_path):
    r = _run_check(tmp_path, 300, [])
    assert r.status == "PASS"
    assert any("CSV matches exactly" in i for i in r.issues)
