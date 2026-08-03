from app.core.health import (
    SubsystemIssue,
    check_modalities,
    find_bad_tokens,
    run_self_check,
)


def test_check_modalities_flags_missing():
    result = check_modalities({"key": 12, "mouse_button": 3})
    assert result == {"keyboard": True, "mouse_motion": False, "mouse_buttons": True}


def test_find_bad_tokens_catches_vk_and_control_bytes():
    seen = {"w", "a", "vk_97", "\x17", "shift_l"}
    bad = find_bad_tokens(seen)
    assert bad == {"vk_97", "\x17"}


def _base_kwargs(**overrides):
    kwargs = dict(
        events_by_type={"key": 10, "mouse_raw": 10, "mouse_button": 5},
        all_keys_seen={"w", "a", "shift_l"},
        frame_count=1000,
        frames_dropped=5,
        game_slug="outer_wilds",
        game_slug_is_known=True,
        video_readable=True,
        subsystem_issues=[],
        sync_status="PASS",
    )
    kwargs.update(overrides)
    return kwargs


def test_clean_session_passes():
    result = run_self_check(**_base_kwargs())
    assert result.ready_for_upload
    assert result.failures == []


def test_missing_modality_fails_and_names_it():
    result = run_self_check(**_base_kwargs(events_by_type={"key": 10, "mouse_button": 5}))
    assert not result.ready_for_upload
    assert any("mouse_motion" in f for f in result.failures)


def test_subsystem_failure_blocks_upload():
    result = run_self_check(**_base_kwargs(
        subsystem_issues=[SubsystemIssue(name="raw_mouse_motion", error="init failed")]))
    assert not result.ready_for_upload
    assert any("raw_mouse_motion" in f for f in result.failures)


def test_excessive_frame_drop_fails():
    result = run_self_check(**_base_kwargs(frame_count=1000, frames_dropped=200))
    assert not result.ready_for_upload
    assert any("frames_dropped" in f for f in result.failures)


def test_minor_frame_drop_only_warns():
    result = run_self_check(**_base_kwargs(frame_count=1000, frames_dropped=10))
    assert result.ready_for_upload
    assert any("frames_dropped" in w for w in result.warnings)


def test_sync_fail_blocks_upload():
    result = run_self_check(**_base_kwargs(sync_status="FAIL"))
    assert not result.ready_for_upload


def test_sync_warn_does_not_block():
    result = run_self_check(**_base_kwargs(sync_status="WARN"))
    assert result.ready_for_upload
    assert result.warnings


def test_unknown_game_only_warns():
    result = run_self_check(**_base_kwargs(game_slug_is_known=False, game_slug="mystery"))
    assert result.ready_for_upload
    assert any("mystery" in w for w in result.warnings)


def test_bad_tokens_fail():
    result = run_self_check(**_base_kwargs(all_keys_seen={"w", "vk_12"}))
    assert not result.ready_for_upload
