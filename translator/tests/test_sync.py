"""Tests for the vendored action_video_grounding sync measurement."""
import pytest

np = pytest.importorskip("numpy")

from translator import sync


def _tracks(lag_frames: int, n: int = 2000, seed: int = 7):
    """Synthetic pair: scene motion = -input delayed by `lag_frames` frames
    (first-person: world moves opposite the camera, response after the input).
    Negative lag_frames == video behind inputs, matching the client's sign."""
    rng = np.random.default_rng(seed)
    action = rng.standard_normal(n)
    delay = -lag_frames
    motion = np.zeros(n)
    if delay >= 0:
        motion[delay:] = -action[: n - delay]
    else:
        motion[:delay] = -action[-delay:]
    motion += 0.05 * rng.standard_normal(n)
    return action, motion


@pytest.mark.parametrize("true_lag", [0, -4, -61, 12])
def test_estimator_recovers_known_lag(true_lag):
    action, motion = _tracks(true_lag)
    zeros = np.zeros_like(action)
    est = sync.estimate_lag(motion, zeros, action, zeros)
    assert est.lag_frames == true_lag
    assert est.correlation < -0.8          # anti-correlated by construction
    assert est.active_fraction > 0.9


def test_estimator_gates_inactive_input():
    n = 2000
    est = sync.estimate_lag(np.random.default_rng(0).standard_normal(n),
                            np.zeros(n), np.zeros(n), np.zeros(n))
    assert est.active_fraction == 0.0
    status, _ = sync.verdict(est, fps=30.0)
    assert status == "SKIP"


def test_verdict_gates():
    fps = 30.0
    ok = sync.LagEstimate(lag_frames=-1, correlation=-0.5,
                          analyzed_frames=3600, active_fraction=0.2)
    assert sync.verdict(ok, fps)[0] == "PASS"
    weak = sync.LagEstimate(lag_frames=-1, correlation=-0.05,
                            analyzed_frames=3600, active_fraction=0.2)
    assert sync.verdict(weak, fps)[0] == "WARN"
    late = sync.LagEstimate(lag_frames=-61, correlation=-0.5,
                            analyzed_frames=3600, active_fraction=0.2)
    assert sync.verdict(late, fps)[0] == "FAIL"
    assert "behind" in sync.verdict(late, fps)[1]


def test_convention_signs():
    standard = {"input_mouse_convention": {
        "maps_to": "camera_look_velocity",
        "dx_positive": "right", "dy_positive": "down"}}
    assert sync.convention_signs(standard) == (1.0, 1.0)
    flipped = {"input_mouse_convention": {
        "maps_to": "camera_look_velocity",
        "dx_positive": "left", "dy_positive": "up"}}
    assert sync.convention_signs(flipped) == (-1.0, -1.0)
    assert sync.convention_signs(None) == (1.0, 1.0)
    assert sync.convention_signs({"input_mouse_convention": {
        "maps_to": "cursor_position", "dx_positive": "left"}}) == (1.0, 1.0)


def test_input_track_from_rows_blank_cells():
    dx, dy = sync.input_track_from_rows(["1.0", "", "-3.0"],
                                        ["", "2.0", "0.0"], None)
    assert dx.tolist() == [1.0, 0.0, -3.0]
    assert dy.tolist() == [0.0, 2.0, 0.0]
