from app.core.finalize.anchor import AnchorResult, ClockPairing, apply_correction


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
