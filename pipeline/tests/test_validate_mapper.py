"""Reason-mapper unit tests — every §5/§10 gate, driven by synthetic engine
reports (the mapper is pure; the engine itself is exercised in the
integration run over the six local sessions)."""
from pipeline.validate import map_reasons

GOOD_INV = {"rows": 3000, "key_frames": 900, "btn_frames": 200,
            "motion_frames": 1500, "keys_no_action": 0,
            "distinct_actions": 5, "os_keys": {}, "bleed_frames": 0,
            "irregular_pct": 0.0}


def rep(**kw):
    base = {
        "game_title": "Kamla", "duration_s": 120.0, "frames": 3600,
        "fps": 30.0, "qa_status": "PASS", "qa_issues": [],
        "inventory": dict(GOOD_INV),
        "lag": {"summary": "video 10.0ms behind inputs (within 150ms; "
                           "target 50ms); correlation -0.42",
                "frame_sync": "OK (<=100ms vs real PTS)"},
        "audio": {"has_audio": True},
        "vlm": {"samples": [{"t": 1.0, "label": "gameplay"}] * 30,
                "windows": [], "notif_ts": [], "chat_ts": [],
                "combat_ts": [], "game_votes": {"kamla": 25}},
        "verdict": "deliverable",
    }
    base.update(kw)
    return base


def aux(**kw):
    base = {"has_raw": True, "vlm_required": True, "video_active": True,
            "refined": {}, "extra_windows": [], "afk_windows": [],
            "notifs": [], "chats": []}
    base.update(kw)
    return base


def codes(res):
    return [r["code"] for r in res.reasons]


def test_clean_session_bin1():
    res = map_reasons(rep(), aux(), "kamla")
    assert res.bin == 1 and not res.hold_vlm and res.reasons == []


def test_out_of_scope_label_rejects_even_when_engine_deliverable():
    r = rep(game_title="xonotic")
    r["vlm"]["game_votes"] = {"xonotic": 25}
    res = map_reasons(r, aux(), None)
    assert res.bin == 3
    assert codes(res) == ["CNT_WRONG_GAME"]
    assert res.engine_verdict == "deliverable"


def test_tripwire_report_only_per_aug14_ruling(monkeypatch):
    """Adnaan 08-14 (post-plan): VLM game identity gates NOTHING in
    Phase 1 — a unanimous mismatch is a loud advisory, the session
    proceeds. The R1 label-scope reject is separate and still live."""
    r = rep(game_title="Kamla")
    r["vlm"]["game_votes"] = {"Xonotic": 28}
    res = map_reasons(r, aux(), "kamla")
    assert res.bin == 1 and "CNT_WRONG_GAME" not in codes(res)
    assert any("VLM GAME MISMATCH (report-only" in a
               for a in res.advisories)


def test_tripwire_gates_when_flag_restored(monkeypatch):
    from pipeline import config as C_
    monkeypatch.setattr(C_, "VLM_GAME_TRIPWIRE_GATES", True)
    r = rep(game_title="Kamla")
    r["vlm"]["game_votes"] = {"Xonotic": 28}
    res = map_reasons(r, aux(), "kamla")
    assert res.bin == 3 and "CNT_WRONG_GAME" in codes(res)
    r2 = rep(game_title="Kamla")
    r2["vlm"]["game_votes"] = {"Outer Wilds": 28}
    res2 = map_reasons(r2, aux(), "kamla")
    assert res2.bin == 2 and codes(res2) == ["STR_GAME_MISMATCH"]


def test_below_unanimity_mismatch_is_advisory():
    r = rep(game_title="Kamla")
    r["vlm"]["game_votes"] = {"Xonotic": 5, "kamla": 3}
    res = map_reasons(r, aux(), "kamla")
    assert res.bin == 1
    assert any("below the unanimity tripwire" in a for a in res.advisories)


def test_misfiled_folder_reroutes():
    res = map_reasons(rep(game_title="Outer Wilds",
                          vlm={"samples": [{"t": 1, "label": "gameplay"}],
                               "windows": [], "notif_ts": [], "chat_ts": [],
                               "combat_ts": [], "game_votes": {}}),
                      aux(), "kamla")
    assert "STR_GAME_MISMATCH" in codes(res)


def test_short_clip_rejects():
    res = map_reasons(rep(duration_s=50.0), aux(), "kamla")
    assert res.bin == 3 and "CNT_SHORT" in codes(res)


def test_short_clip_rejects_even_without_vlm():
    r = rep(duration_s=50.0)
    r["vlm"] = {"windows": []}          # no samples -> would hold
    res = map_reasons(r, aux(), "kamla")
    assert res.bin == 3 and not res.hold_vlm     # video-independent reject


def test_no_vlm_holds_instead_of_passing():
    r = rep()
    r["vlm"] = {"windows": []}
    res = map_reasons(r, aux(), "kamla")
    assert res.hold_vlm and res.bin is None


def test_vlm_extra_failure_holds():
    res = map_reasons(rep(), aux(vlm_extra_failed=True), "kamla")
    assert res.hold_vlm


def test_fanout_maps_to_inp_fanout():
    r = rep(qa_status="FAIL",
            qa_issues=["FAIL: same-literal action fan-out in 44 frames — "
                       "key(s) emit multiple conditional actions"])
    res = map_reasons(r, aux(), "kamla")
    assert res.bin == 2 and "INP_FANOUT" in codes(res)


def test_frame_sync_exact_phrases_not_confused():
    # trap 1: drift FAIL must map to a blocking code — the engine stores
    # the string WITH its qa-v2 severity prefix (review finding #1), and
    # both forms must map
    for fs in ("FAIL: frame-sync drift: worst row timestamp 3210ms off "
               "real PTS",
               "frame-sync drift: worst row timestamp 3210ms off real PTS"):
        r = rep()
        r["lag"] = {"summary": "", "frame_sync": fs}
        res = map_reasons(r, aux(), "kamla")
        assert "SYN_TS_NOT_PTS" in codes(res), fs
        assert res.bin == 2
    # trap 2: 'cannot verify' is a WARN, never a blocking code
    for fs in ("WARN: cannot verify frame sync (PTS unreadable)",
               "cannot verify frame sync (PTS unreadable)"):
        r2 = rep()
        r2["lag"] = {"summary": "", "frame_sync": fs}
        res2 = map_reasons(r2, aux(), "kamla")
        assert "SYN_TS_NOT_PTS" not in codes(res2), fs
        assert any("unverifiable" in a for a in res2.advisories)
        assert res2.bin == 1
    # the OK form maps to nothing
    r3 = rep()
    r3["lag"] = {"summary": "", "frame_sync": "OK (<=100ms vs real PTS)"}
    assert map_reasons(r3, aux(), "kamla").bin == 1


def test_lag_hard_fail_maps_to_syn_lag_const():
    r = rep(qa_issues=["FAIL: controls-to-video sync: video 200.0ms behind "
                       "inputs (|lag| > 150ms); correlation -0.40"])
    res = map_reasons(r, aux(), "kamla")
    assert "SYN_LAG_CONST" in codes(res)
    lag = next(x for x in res.reasons if x["code"] == "SYN_LAG_CONST")
    assert lag["params"]["lag_ms"] == 200.0 and lag["fixable"]


def test_lag_over_target_within_hard_still_fix():
    r = rep()
    r["lag"]["summary"] = ("video 100.0ms behind inputs (within 150ms; "
                           "target 50ms); correlation -0.42")
    res = map_reasons(r, aux(), "kamla")
    assert "SYN_LAG_CONST" in codes(res) and res.bin == 2


def test_weak_corr_benign_is_advisory():
    r = rep()
    r["lag"]["summary"] = ("correlation too weak to verify alignment "
                           "(|corr|=0.120 < 0.15)")
    res = map_reasons(r, aux(), "kamla")
    assert "SYN_UNMEASURABLE_SUSPECT" not in codes(res)
    assert res.bin == 1


def test_weak_corr_with_visible_action_is_suspect():
    r = rep()
    r["lag"]["summary"] = ("correlation too weak to verify alignment "
                           "(|corr|=0.020 < 0.15)")
    r["inventory"]["motion_frames"] = 1500      # 50% of rows active
    res = map_reasons(r, aux(video_active=True), "kamla")
    assert "SYN_UNMEASURABLE_SUSPECT" in codes(res) and res.bin == 3


def _window(t0, t1, action_frames=0, ratio=0.05, tier="high"):
    return {"t0": t0, "t1": t1, "labels": ["pause"], "tier": tier,
            "gating": True, "n_samples": 3,
            "inputs": {"action_frames": action_frames},
            "stillness_ratio": ratio}


def test_small_frozen_window_with_actions_gates():
    r = rep(duration_s=1200.0)
    r["vlm"]["windows"] = [_window(600.0, 601.5, action_frames=12)]
    res = map_reasons(r, aux(), "kamla")
    assert codes(res) == ["INP_FROZEN_ACTIONS"] and res.bin == 2


def test_short_frozen_window_gates_instead_of_splitting():
    # The real OW case: a 2.0s pause in a 348s clip. This SPLIT until
    # Adnaan's 2026-08-17 R2/R3 rulings — 2.0s was 0.57% of the clip,
    # over the 0.2% ratchet — and splitting it was exactly the behaviour
    # that made the cascade self-perpetuating. The ratchet is gone and the
    # bar is 5s, so the window is kept and its 8 action frames are gated.
    # Full rationale + the supersession live in config.KEEP_GATE_MAX_S.
    r = rep(duration_s=348.2)
    r["vlm"]["windows"] = [_window(109.5, 111.5, action_frames=8)]
    res = map_reasons(r, aux(), "kamla")
    assert codes(res) == ["INP_FROZEN_ACTIONS"] and res.bin == 2
    p = res.reasons[0]["params"]
    assert p["t0"] == 109.5 and p["t1"] == 111.5


def test_long_frozen_window_still_splits():
    # ...and the cut path is intact above the bar: same clip, 7s pause.
    r = rep(duration_s=348.2)
    r["vlm"]["windows"] = [_window(109.5, 116.5, action_frames=8)]
    res = map_reasons(r, aux(), "kamla")
    assert codes(res) == ["CNT_MID_NONGAMEPLAY"]
    cut = res.reasons[0]["params"]["cut"]
    assert cut == [109.5, 116.5] and res.reasons[0]["fixable"]


def test_overlay_over_live_play_is_advisory():
    r = rep(duration_s=300.0)
    r["vlm"]["windows"] = [_window(100, 110, action_frames=50, ratio=0.55)]
    res = map_reasons(r, aux(), "kamla")
    assert res.bin == 1
    assert any("overlay over live play" in a for a in res.advisories)


def test_head_window_trims_or_rejects():
    r = rep(duration_s=300.0)
    r["vlm"]["windows"] = [_window(0.0, 20.0)]
    res = map_reasons(r, aux(), "kamla")
    edge = next(x for x in res.reasons
                if x["code"] == "CNT_EDGE_NONGAMEPLAY")
    assert edge["params"]["edge"] == "head"
    assert edge["params"]["cut_at_s"] == 20.5

    r2 = rep(duration_s=80.0)
    r2["vlm"]["windows"] = [_window(0.0, 20.0)]
    res2 = map_reasons(r2, aux(), "kamla")
    assert "CNT_SHORT" in codes(res2) and res2.bin == 3


def test_full_span_window_rejects():
    r = rep(duration_s=100.0)
    r["vlm"]["windows"] = [_window(0.5, 99.9)]
    res = map_reasons(r, aux(), "kamla")
    assert res.bin == 3
    assert any(x["code"] == "CNT_MID_NONGAMEPLAY" and not x["fixable"]
               for x in res.reasons)


def test_zero_buttons_with_combat_rejects_without_combat_advisory():
    r = rep()
    r["inventory"]["btn_frames"] = 0
    r["vlm"]["combat_ts"] = [10.0, 40.0, 70.0]
    res = map_reasons(r, aux(), "kamla")
    assert "INP_BUTTONS_MISSING" in codes(res) and res.bin == 3

    r2 = rep()
    r2["inventory"]["btn_frames"] = 0
    r2["vlm"]["combat_ts"] = []
    res2 = map_reasons(r2, aux(), "kamla")
    assert "INP_BUTTONS_MISSING" not in codes(res2) and res2.bin == 1
    assert any("benign zero-buttons" in a for a in res2.advisories)


def test_motion_missing_rejects():
    r = rep()
    r["inventory"]["motion_frames"] = 0
    res = map_reasons(r, aux(), "kamla")
    assert "INP_MOTION_MISSING" in codes(res) and res.bin == 3


def test_drops_bands():
    r = rep()
    r["inventory"]["irregular_pct"] = 3.0
    res = map_reasons(r, aux(), "kamla")
    assert "CNT_DROPS" not in codes(res)
    assert any("1-5% band" in a for a in res.advisories)
    r["inventory"]["irregular_pct"] = 7.5
    res = map_reasons(r, aux(), "kamla")
    assert "CNT_DROPS" in codes(res) and res.bin == 3


def test_audio_never_blocks():
    r = rep(audio={"has_audio": False})
    res = map_reasons(r, aux(), "kamla")
    assert res.bin == 1
    assert any("never blocks" in a for a in res.advisories)


def test_actions_few_rejects():
    r = rep()
    r["inventory"]["distinct_actions"] = 2
    res = map_reasons(r, aux(), "kamla")
    assert "CNT_ACTIONS_FEW" in codes(res) and res.bin == 3


def test_notifications_mid_vs_edge_vs_unconfirmed():
    res = map_reasons(rep(), aux(notifs=[
        {"t": 60.0, "confirmed": True, "what": "steam toast"},
        {"t": 1.0, "confirmed": True, "what": "toast"},
        {"t": 90.0, "confirmed": False, "what": "kill feed"}]), "kamla")
    assert "CNT_NOTIF_MID" in codes(res)
    assert "CNT_NOTIF_EDGE" in codes(res)
    assert res.bin == 3
    assert any("unconfirmed" in a for a in res.advisories)


def test_chat_pii_rejects_mid():
    res = map_reasons(rep(), aux(chats=[{"t": 55.0, "confirmed": True,
                                         "what": "player names"}]), "kamla")
    assert "CNT_CHAT_PII" in codes(res) and res.bin == 3


def test_afk_window_cuts():
    res = map_reasons(rep(duration_s=600.0),
                      aux(afk_windows=[(100.0, 145.0)]), "kamla")
    assert "CNT_AFK" in codes(res) and res.bin == 2


def test_scanner_extra_window_gates_between_vlm_samples():
    res = map_reasons(rep(duration_s=1500.0),
                      aux(extra_windows=[{"t0": 500.0, "t1": 501.5,
                                          "label": "pause",
                                          "action_frames": 5}]), "kamla")
    assert codes(res) == ["INP_FROZEN_ACTIONS"]


def test_qa_structural_codes():
    r = rep(qa_status="FAIL", qa_issues=[
        "FAIL: camera columns non-null in 5 rows (input-only session)",
        "FAIL: input_mouse_dx/dy not float-formatted ('0.0' sentinel) in 9 cells",
        "FAIL: 3 frames have input_keys but null input_actions",
        "WARN: only 2 distinct actions"])
    res = map_reasons(r, aux(), "kamla")
    assert set(codes(res)) == {"STR_CAMERA_NONNULL", "STR_SENTINELS",
                               "INP_KEYS_NO_ACTION"}
    assert res.bin == 2


def test_unmapped_qa_fail_never_silently_passes():
    r = rep(qa_status="FAIL", qa_issues=["FAIL: something entirely new"])
    res = map_reasons(r, aux(has_raw=False), "kamla")
    assert res.bin == 3          # blocking, unfixable without raws
    res2 = map_reasons(r, aux(has_raw=True), "kamla")
    assert res2.bin == 2         # retranslate is the universal strong fix


def test_v1_payload_code(tmp_path):
    from pipeline.validate import validate_session
    (tmp_path / "s").mkdir()
    res = validate_session(tmp_path / "s", tmp_path / "d", payload="v1")
    assert [r["code"] for r in res.reasons] == ["ARR_V1_FORMAT"]
    assert res.bin == 2
    assert (tmp_path / "d" / "verdict.json").exists()


def test_tamper_flag():
    res = map_reasons(rep(), aux(tamper="99 rows carry impossible mouse "
                                        "deltas"), "kamla")
    assert "INT_TAMPER" in codes(res) and res.bin == 3


def test_scanner_failure_holds_when_vlm_expected(tmp_path, monkeypatch):
    """Review finding: a scanner failure must never silently drop the
    AFK/black-frozen/notification battery (F5)."""
    from pipeline import scanner, validate
    monkeypatch.setattr(scanner, "available", lambda: False)
    (tmp_path / "frames.csv").write_text(
        "frame_id,timestamp_ms,input_keys,input_actions,"
        "input_mouse_buttons,input_mouse_dx,input_mouse_dy\n"
        "0,0,W,move_up,,0.0,0.0\n")
    a = validate._build_aux(tmp_path, rep(), None, gemini_key="k",
                            gemini_model="m", vlm_expected=True)
    assert a["vlm_extra_failed"]
    a2 = validate._build_aux(tmp_path, rep(), None, gemini_key="",
                             gemini_model="m", vlm_expected=False)
    assert not a2["vlm_extra_failed"]


def test_notif_confirm_runs_without_scanner(tmp_path, monkeypatch):
    from pipeline import scanner, validate, vlm as vlmmod
    monkeypatch.setattr(scanner, "available", lambda: False)
    (tmp_path / "frames.csv").write_text(
        "frame_id,timestamp_ms,input_keys,input_actions,"
        "input_mouse_buttons,input_mouse_dx,input_mouse_dy\n"
        "0,0,W,move_up,,0.0,0.0\n")

    class Grabber:
        def __init__(self, video):
            pass

        def at(self, t):
            import types
            return "frame"

        def jpeg(self, t, width=640):
            return b"jpg"

        def close(self):
            pass

    class Eng:
        FrameGrabber = Grabber

    monkeypatch.setattr(validate, "load_engine", lambda: Eng)
    monkeypatch.setattr(validate, "_corner_jpeg", lambda fr: b"jpg")
    monkeypatch.setattr(vlmmod, "confirm_flag",
                        lambda *a, **k: (True, "steam toast"))
    r = rep()
    r["vlm"]["notif_ts"] = [60.0]
    a = validate._build_aux(tmp_path, r, object(), gemini_key="k",
                            gemini_model="m", vlm_expected=True)
    assert a["notifs"] == [{"t": 60.0, "confirmed": True,
                            "what": "steam toast"}]


def test_keys_missing_conditional_on_video_evidence():
    r = rep()
    r["inventory"]["key_frames"] = 0
    res = map_reasons(r, aux(video_active=True), "kamla")
    assert "INP_KEYS_MISSING" in codes(res) and res.bin == 3
    res2 = map_reasons(r, aux(video_active=False), "kamla")
    assert "INP_KEYS_MISSING" not in codes(res2)
    assert any("near-static" in a for a in res2.advisories)


def test_map_gate_failures_produces_fix_plan_material():
    from pipeline.validate import map_gate_failures
    fails = ["FAIL: camera columns non-null in 3 rows (input-only session)",
             "FAIL: frame-sync drift: worst row timestamp 210ms off real "
             "PTS",
             "FAIL: controls-to-video sync: video 180.0ms behind inputs "
             "(|lag| > 150ms); correlation -0.4"]
    reasons = map_gate_failures(fails, has_raw=False)
    got = {r["code"] for r in reasons}
    assert {"STR_CAMERA_NONNULL", "SYN_TS_NOT_PTS",
            "SYN_LAG_CONST"} <= got
    assert all(r["blocking"] for r in reasons)
    from pipeline import fix
    plan = fix.plan_fixes(reasons, game="kamla", has_raw=False)
    assert plan["steps"], "gate failures must yield a real fix plan"


def test_report_only_tripwire_still_enforces_r1_label_scope():
    """Review-2 #15: the report-only advisory must not skip the R1
    label-scope reject when both conditions hold at once."""
    r = rep(game_title="valorant")
    r["vlm"]["game_votes"] = {"kamla": 28}      # unanimous, mismatch
    res = map_reasons(r, aux(), None)
    assert res.bin == 3 and "CNT_WRONG_GAME" in codes(res)
    assert any("report-only" in a for a in res.advisories)


def test_misfile_reroute_target_is_claimed_not_vlm_guess():
    """Review-2 #6: sub-tripwire VLM noise must not steer the reroute."""
    r = rep(game_title="Kamla")
    r["vlm"]["game_votes"] = {"outer_wilds": 3}     # noise, sub-tripwire
    res = map_reasons(r, aux(), "outer_wilds")      # misfiled folder
    m = next(x for x in res.reasons if x["code"] == "STR_GAME_MISMATCH")
    assert m["params"]["actual"] == "kamla"


# --- dead-black recalibration (Adnaan 2026-08-16) ---------------------------
# Old rule (near-black = luma<16, >50% rejects; plus a baseline<0.3 motion
# arm) mass-false-positived on Kamla, a dark horror game whose legitimate
# scenes average luma 7-16 on the scanner downscale. New rule: dead-black =
# luma < DEAD_BLACK_LUMA_BELOW (5), reject at >= DEAD_BLACK_REJECT_FRAC
# (99.5% — the uniform-black capture-failure signature; tightened from 50%
# the same evening, pre-relaunch); the motion arm is deleted.

def test_dead_black_dark_gameplay_passes():
    """60% of frames at luma 8-15 (torch/smoke Kamla scenes) must PASS —
    exactly the profile the old <16 rule wrongly rejected."""
    from pipeline.validate import _dead_black_check
    luma = [8.0, 11.0, 14.9] * 20 + [100.0] * 40    # 60% in the 8-15 band
    dead, ev = _dead_black_check(luma)
    assert not dead and ev is None


def test_dead_black_capture_failure_rejects():
    from pipeline.validate import _dead_black_check
    dead, ev = _dead_black_check([1.0] * 199 + [50.0])   # exactly 99.5%
    assert dead and "99.5%" in ev and "dead-black" in ev
    assert _dead_black_check([0.0] * 100)[0]             # uniform black


def test_dead_black_boundaries_are_strict():
    from pipeline.validate import _dead_black_check
    # 99.4% dead-black: under the >=99.5% bar — a partial blackout is the
    # mid-clip machinery's job, not a whole-clip reject
    assert not _dead_black_check([1.0] * 994 + [50.0] * 6)[0]
    # a mostly-dark session (75% under 5) passes the whole-clip gate
    assert not _dead_black_check([2.0] * 75 + [100.0] * 25)[0]
    # luma exactly 5.0 is NOT dead-black (strict <)
    assert not _dead_black_check([5.0] * 100)[0]
    assert not _dead_black_check([])[0]


def _stub_scanner(monkeypatch, tl):
    from pipeline import scanner
    import translator.video as V
    monkeypatch.setattr(scanner, "available", lambda: True)
    # stubs mirror the REAL call conventions (scan_video is keyword-only)
    # so a call-form drift at the sole production site (validate.py, inside
    # an except-Exception that degrades to "scanner failed") cannot stay
    # green here while silently dropping the battery in production
    monkeypatch.setattr(scanner, "scan_video",
                        lambda video, *, pts_us=None, timeout_s=3600: tl)
    monkeypatch.setattr(V, "frame_pts", lambda path: None)


_CSV = ("frame_id,timestamp_ms,input_keys,input_actions,"
        "input_mouse_buttons,input_mouse_dx,input_mouse_dy\n"
        "0,0,W,move_up,,0.0,0.0\n")


def test_low_motion_baseline_alone_no_longer_rejects(tmp_path, monkeypatch):
    """The frozen-motion arm (baseline<0.3 over >30s) is DROPPED: a
    near-static bright clip must NOT set black_frozen; it still turns
    video_active off, keeping the INP_KEYS_MISSING interplay (near-static
    + zero keys stays advisory-only)."""
    from pipeline import scanner, validate
    tl = scanner.MotionTimeline(
        n_frames=100, fps=100 / 60.0, duration_s=60.0,
        times_s=[i * 0.6 for i in range(100)],
        diffs=[0.1] * 99, luma=[50.0] * 100)
    _stub_scanner(monkeypatch, tl)
    (tmp_path / "frames.csv").write_text(_CSV)
    a = validate._build_aux(tmp_path, rep(), None, gemini_key="k",
                            gemini_model="m", vlm_expected=False)
    assert "black_frozen" not in a
    assert a["video_active"] is False
    r = rep()
    r["inventory"]["key_frames"] = 0
    res = map_reasons(r, aux(video_active=False, **{
        k: a[k] for k in ("extra_windows", "afk_windows", "notifs",
                          "chats")}), "kamla")
    assert "CNT_BLACK_FROZEN" not in codes(res)
    assert "INP_KEYS_MISSING" not in codes(res)


def test_dead_black_via_build_aux_maps_to_reason(tmp_path, monkeypatch):
    """End-to-end: a genuinely dead-black timeline sets black_frozen with
    the measured %, and the mapper turns it into a blocking unfixable
    CNT_BLACK_FROZEN carrying that evidence."""
    from pipeline import scanner, validate
    tl = scanner.MotionTimeline(
        n_frames=100, fps=100 / 60.0, duration_s=60.0,
        times_s=[i * 0.6 for i in range(100)],
        diffs=[8.0] * 99, luma=[1.0] * 100)
    _stub_scanner(monkeypatch, tl)
    (tmp_path / "frames.csv").write_text(_CSV)
    a = validate._build_aux(tmp_path, rep(), None, gemini_key="k",
                            gemini_model="m", vlm_expected=False)
    assert a.get("black_frozen") is True
    assert "100.0%" in a["black_frozen_evidence"]
    res = map_reasons(rep(), aux(
        black_frozen=True,
        black_frozen_evidence=a["black_frozen_evidence"]), "kamla")
    bf = next(x for x in res.reasons if x["code"] == "CNT_BLACK_FROZEN")
    assert bf["blocking"] and not bf["fixable"] and res.bin == 3
    assert "dead-black" in bf["evidence"]
