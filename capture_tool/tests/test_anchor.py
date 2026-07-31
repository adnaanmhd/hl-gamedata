from unittest.mock import MagicMock, patch

from app.core.finalize.anchor import (
    AnchorResult, ClockPairing, apply_correction, apply_drift_correction,
    compute_anchor_correction, first_frame_pts_wallclock_s, fit_progress_drift,
)


def test_apply_correction_shifts_all_events():
    events = [{"t": 1000, "type": "key"}, {"t": 5000, "type": "mouse_raw"}]
    out = apply_correction(events, correction_us=200)
    assert [e["t"] for e in out] == [800, 4800]


def test_apply_correction_zero_is_noop():
    events = [{"t": 1000, "type": "key"}]
    out = apply_correction(events, correction_us=0)
    assert out == events


def test_apply_correction_skips_events_without_int_t():
    events = [{"type": "focus", "focused": True}, {"t": 1000, "type": "key"}]
    out = apply_correction(events, correction_us=100)
    assert out[0] == {"type": "focus", "focused": True}
    assert out[1]["t"] == 900


def test_capture_health_dict_shape():
    pairing = ClockPairing(wallclock_s=1000.0, monotonic_s=50.0)
    result = AnchorResult(
        method="first_frame_pts_wallclock", correction_us=1234,
        launch_pairing=pairing, frame0_wallclock_s=1000.2, frame0_monotonic_s=50.2)
    d = result.to_capture_health_dict()
    assert d["time_anchor"] == "first_frame_pts_wallclock"
    assert d["correction_applied_us"] == 1234
    assert d["launch_wallclock_s"] == 1000.0
    assert d["frame0_monotonic_s"] == 50.2


def test_unavailable_anchor_has_zero_correction():
    result = AnchorResult(method="unavailable", correction_us=0, launch_pairing=None,
                           frame0_wallclock_s=None, frame0_monotonic_s=None)
    assert result.to_capture_health_dict()["launch_wallclock_s"] is None


def test_first_frame_pts_parses_ffprobe_trailing_comma(tmp_path):
    """Real bug found on Windows: ffprobe's `-of csv=p=0` output for a
    single selected field still came back with a trailing comma
    ("0.066667,"), which float() rejected outright — silently swallowed
    before logging was added, so this failed on every real session without
    any visible reason."""
    fake_result = MagicMock(stdout="0.066667,\n", returncode=0)
    with patch("app.core.finalize.anchor.subprocess.run", return_value=fake_result):
        assert first_frame_pts_wallclock_s(tmp_path / "video.mp4") == 0.066667


def test_compute_anchor_correction_rejects_implausible_result(tmp_path):
    """Real bug: if the video's PTS doesn't survive as wallclock-scale time
    after muxing (e.g. ffmpeg's avoid_negative_ts normalizes it back to ~0),
    frame0_wall comes back tiny/relative instead of epoch-scale, and the
    naive correction would be off by YEARS — silently corrupting every event
    timestamp instead of failing loudly. Must fall back to "unavailable"."""
    launch_pairing = ClockPairing(wallclock_s=1_785_000_000.0, monotonic_s=100.0)
    with patch("app.core.finalize.anchor.first_frame_pts_wallclock_s",
               return_value=0.066667):  # relative-scale, NOT epoch-scale
        result = compute_anchor_correction(
            launch_pairing=launch_pairing, old_anchor_monotonic_s=100.05,
            video_path=tmp_path / "video.mp4")
    assert result.method == "unavailable"
    assert result.correction_us == 0


def test_compute_anchor_correction_accepts_plausible_result(tmp_path):
    """A genuinely small startup gap (sub-second, matching the handoff
    doc's own evidence of a real correctly-anchored capture) must still be
    applied — the safety net must not reject real, valid corrections."""
    launch_pairing = ClockPairing(wallclock_s=1_785_000_000.0, monotonic_s=100.0)
    # frame0 occurs 0.05s (wallclock) after launch -> a small, real gap.
    with patch("app.core.finalize.anchor.first_frame_pts_wallclock_s",
               return_value=1_785_000_000.05):
        result = compute_anchor_correction(
            launch_pairing=launch_pairing, old_anchor_monotonic_s=100.0,
            video_path=tmp_path / "video.mp4")
    assert result.method == "first_frame_pts_wallclock"
    assert result.correction_us == 50_000


def test_progress_based_anchor_is_preferred_and_never_touches_ffprobe(tmp_path):
    """The real A2 fix: when _StderrMonitor captured a progress line, use
    it directly — no ffprobe call, no wallclock assumption at all. Confirmed
    on a real delivery that the ffprobe/wallclock path is unreliable; this
    path must be tried first and must not fall through to it when it has
    everything it needs."""
    launch_pairing = ClockPairing(wallclock_s=1_785_000_000.0, monotonic_s=100.0)
    with patch("app.core.finalize.anchor.first_frame_pts_wallclock_s") as ffprobe_call:
        result = compute_anchor_correction(
            launch_pairing=launch_pairing, old_anchor_monotonic_s=100.0,
            video_path=tmp_path / "video.mp4",
            first_progress_monotonic_s=100.5, first_progress_encoded_s=0.45)
    ffprobe_call.assert_not_called()
    assert result.method == "ffmpeg_progress_time"
    # frame0_monotonic = 100.5 - 0.45 = 100.05; correction = (100.05 - 100.0) * 1e6
    assert result.correction_us == 50_000


def test_progress_based_anchor_falls_back_to_ffprobe_if_implausible(tmp_path):
    launch_pairing = ClockPairing(wallclock_s=1_785_000_000.0, monotonic_s=100.0)
    with patch("app.core.finalize.anchor.first_frame_pts_wallclock_s",
               return_value=None) as ffprobe_call:
        result = compute_anchor_correction(
            launch_pairing=launch_pairing, old_anchor_monotonic_s=100.0,
            video_path=tmp_path / "video.mp4",
            # implausible: encoded_s far exceeds any real elapsed time here
            first_progress_monotonic_s=100.5, first_progress_encoded_s=999_999.0)
    ffprobe_call.assert_called_once()
    assert result.method == "unavailable"


class TestDriftAwareAnchor:
    """Partial-fix #3: a single (monotonic, encoded) sample can only ever
    measure the startup gap. If the video's internal content-time clock and
    real wallclock time diverge over a long recording (e.g. -fps_mode cfr
    duplicating/dropping frames to hold a constant rate the real capture
    can't sustain), a single fixed correction is right at one instant and
    increasingly wrong for the rest of the session. These tests cover the
    multi-sample linear fit that replaces it when enough samples exist."""

    def _drifting_samples(self, true_slope: float, true_intercept: float,
                           n: int = 20, start_m: float = 100.0, step: float = 1.0):
        return [(start_m + i * step, true_slope * (start_m + i * step) + true_intercept)
                for i in range(n)]

    def test_fit_recovers_true_slope_and_intercept(self):
        samples = self._drifting_samples(true_slope=0.9985, true_intercept=-0.05)
        slope, intercept = fit_progress_drift(samples)
        assert abs(slope - 0.9985) < 1e-9
        assert abs(intercept - (-0.05)) < 1e-9

    def test_fit_returns_none_with_too_few_samples(self):
        samples = self._drifting_samples(true_slope=0.9985, true_intercept=0.0, n=3)
        assert fit_progress_drift(samples) is None

    def test_fit_returns_none_when_all_samples_at_same_instant(self):
        assert fit_progress_drift([(100.0, 0.0)] * 10) is None

    def test_compute_anchor_correction_prefers_drift_fit_when_available(self, tmp_path):
        launch_pairing = ClockPairing(wallclock_s=1_785_000_000.0, monotonic_s=100.0)
        samples = self._drifting_samples(true_slope=0.999, true_intercept=-99.9)
        with patch("app.core.finalize.anchor.first_frame_pts_wallclock_s") as ffprobe_call:
            result = compute_anchor_correction(
                launch_pairing=launch_pairing, old_anchor_monotonic_s=100.0,
                video_path=tmp_path / "video.mp4", progress_samples=samples,
                # a single-sample anchor is also available but must be ignored
                # in favor of the drift fit when the fit is usable.
                first_progress_monotonic_s=100.5, first_progress_encoded_s=0.45)
        ffprobe_call.assert_not_called()
        assert result.method == "ffmpeg_progress_time_drift_fit"
        assert abs(result.drift_slope - 0.999) < 1e-9
        assert result.drift_sample_count == len(samples)

    def test_compute_anchor_correction_falls_back_on_implausible_slope(self, tmp_path):
        """A slope wildly off 1.0 (e.g. from garbage/duplicate samples) is
        noise, not a real drift signal — must fall back to the single-sample
        method rather than trust it."""
        launch_pairing = ClockPairing(wallclock_s=1_785_000_000.0, monotonic_s=100.0)
        samples = self._drifting_samples(true_slope=5.0, true_intercept=0.0)
        result = compute_anchor_correction(
            launch_pairing=launch_pairing, old_anchor_monotonic_s=100.0,
            video_path=tmp_path / "video.mp4", progress_samples=samples,
            first_progress_monotonic_s=100.5, first_progress_encoded_s=0.45)
        assert result.method == "ffmpeg_progress_time"

    def test_apply_drift_correction_grows_over_the_session(self):
        """The core behavior this whole fix exists for: the correction for
        an event near the end of a long recording must differ from the
        correction near the start — a constant shift cannot do this."""
        events = [{"t": 1_000_000}, {"t": 500_000_000}]  # 1s and 500s in
        out = apply_drift_correction(
            events, old_anchor_monotonic_s=100.0,
            drift_slope=0.998, drift_intercept=-99.8)
        early_shift = 1_000_000 - out[0]["t"]
        late_shift = 500_000_000 - out[1]["t"]
        assert late_shift > early_shift * 100  # grows with elapsed time, not fixed

    def test_apply_drift_correction_skips_events_without_int_t(self):
        events = [{"type": "focus", "focused": True}]
        out = apply_drift_correction(
            events, old_anchor_monotonic_s=100.0, drift_slope=1.0, drift_intercept=0.0)
        assert out == events
