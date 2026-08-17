"""Split-cascade rulings R1-R3 (Adnaan 2026-08-17) — regression tests.

The rebuild ran ~28h against a planned 7-8h with net queue drain ~= zero,
because splitting was self-perpetuating: 320 roots -> 600 children -> 145
grandchildren. Two compounding mechanisms, both fixed here:

  * the 0.2%-of-clip ratchet (KEEP_GATE_MAX_FRAC) tightened as clips got
    shorter, so a blip a parent KEPT became a cut-trigger in its own child
    purely because the child was shorter (R2), and
  * scanner-found short windows could propose cuts, which combined with the
    40-candidate cap to make each child discover junk its parent's capped
    scan never examined (R1).

These tests pin the behaviour, not the constants: they use literal spans and
durations so that moving a threshold in config.py fails them loudly.
"""
from pipeline import config as C
from pipeline import fix, scanner, validate
from pipeline.scanner import MotionTimeline


def _vlm_window(t0, t1, action_frames=0, ratio=0.05):
    """A high-tier gating VLM window over confirmed-frozen frames."""
    return {"t0": t0, "t1": t1, "labels": ["pause"], "tier": "high",
            "gating": True, "n_samples": 3,
            "inputs": {"action_frames": action_frames},
            "stillness_ratio": ratio}


def _map(dur, windows=(), extra=()):
    """Run the window mapper alone; return (codes, reasons, advisories)."""
    rep = {"duration_s": dur, "vlm": {"windows": list(windows)}}
    aux = {"refined": {}, "extra_windows": list(extra), "afk_windows": []}
    reasons, advisories = [], []
    validate._map_windows(rep, aux, reasons, advisories)
    return [r["code"] for r in reasons], reasons, advisories


def _scanner_window(t0, t1, action_frames=0, label="pause"):
    return {"t0": t0, "t1": t1, "label": label,
            "action_frames": action_frames}


# ---------------------------------------------- R3: mid-clip keep bar 2s -> 5s

def test_3s_mid_clip_freeze_is_kept():
    """(1) A 3s mid-clip freeze on the VLM path is KEPT, not cut.

    Under the superseded rule this cut at every clip length: 3.0 > 2.0.
    """
    codes, _, advisories = _map(600.0, [_vlm_window(200.0, 203.0)])
    assert "CNT_MID_NONGAMEPLAY" not in codes
    assert codes == []
    assert any("frozen blip kept" in a for a in advisories)


def test_6s_mid_clip_freeze_is_cut():
    """(2) A 6s mid-clip freeze still cuts — the bar moved, it did not go
    away. The cut must cover the whole flagged window."""
    codes, reasons, _ = _map(600.0, [_vlm_window(200.0, 206.0)])
    assert codes == ["CNT_MID_NONGAMEPLAY"]
    assert reasons[0]["params"]["cut"] == [200.0, 206.0]
    assert reasons[0]["fixable"] is True


# ------------------------------------- R1: scanner cuts restricted to >5s

def test_scanner_window_under_bar_never_cuts_at_any_duration():
    """(3) A scanner-found window <=5s NEVER proposes a cut, at any clip
    duration. This is the cascade's engine: short scanner finds creating
    child rows, each child re-scanning under the 40-cap and finding more.

    70s is the MIN_CLIP_S floor and 3000s a long parent — under the old
    fractional rule the same 4.5s window cut in both (4.5 > 2.0), and even
    a 1.5s window cut in the short one (1.5 > 0.2% * 70 = 0.14).
    """
    for dur in (70.0, 200.0, 1200.0, 3000.0):
        for span in (0.9, 1.5, 3.0, 4.9, 5.0):
            codes, _, _ = _map(
                dur, extra=[_scanner_window(30.0, 30.0 + span)])
            assert "CNT_MID_NONGAMEPLAY" not in codes, (
                f"scanner window of {span}s cut a {dur}s clip")


def test_scanner_window_over_bar_still_cuts():
    """(4) >5s scanner windows still cut — this is the quality protection
    R1 deliberately keeps. Ledger-wide 95 of 443 scanner windows (21.4%)
    clear this bar, totalling 578.6s of confirmed non-gameplay; without the
    carve-out that footage would ship on a dossier advisory alone."""
    codes, reasons, _ = _map(
        600.0, extra=[_scanner_window(30.0, 36.0, label="loading")])
    assert codes == ["CNT_MID_NONGAMEPLAY"]
    assert reasons[0]["params"]["cut"] == [30.0, 36.0]


def test_scanner_window_under_bar_with_inputs_gates():
    """(5) A short scanner window with inputs inside is GATED, not cut —
    gating creates no child row, so it cannot cascade, but the frozen
    inputs still never ship."""
    codes, reasons, _ = _map(
        600.0, extra=[_scanner_window(30.0, 32.0, action_frames=4)])
    assert codes == ["INP_FROZEN_ACTIONS"]
    assert reasons[0]["params"] == {"t0": 30.0, "t1": 32.0}
    assert reasons[0]["fixable"] is True


def test_sub_4s_freeze_with_inputs_is_gated_end_to_end():
    """(6) The specific gap R1 exists to close, exercised through the real
    scanner rather than a hand-built window.

    A 2s freeze is INVISIBLE to the 4s VLM sweep — it can fall entirely
    between two samples. SCANNER_STATIC_MIN_S=0.8 is what finds it, which
    is why the initial instruction to raise that floor to 5s was
    superseded: at 5s this window does not exist and its inputs ship
    un-gated.
    """
    # 10fps synthetic timeline: motion, then a 2.0s still run, then motion
    diffs = [5.0] * 30 + [0.2] * 20 + [5.0] * 30
    times = [i * 0.1 for i in range(len(diffs) + 1)]
    tl = MotionTimeline(n_frames=len(diffs) + 1, fps=10.0,
                        duration_s=times[-1] + 0.1, times_s=times,
                        diffs=diffs, luma=[100.0] * (len(diffs) + 1))

    wins = scanner.static_windows(tl, ratio=C.STILLNESS_FROZEN_BELOW,
                                  baseline=5.0,
                                  min_s=C.SCANNER_STATIC_MIN_S)
    assert len(wins) == 1, "the configured floor must still find a 2s freeze"
    t0, t1 = wins[0]
    span = t1 - t0
    assert 0.8 <= span < 4.0, (
        f"{span}s window must sit below the 4s VLM sweep interval — that is "
        f"the whole reason this path exists")

    # ...and once found, inputs inside it are gated, not cut
    codes, reasons, _ = _map(
        600.0, extra=[_scanner_window(t0, t1, action_frames=6)])
    assert codes == ["INP_FROZEN_ACTIONS"]


# ------------------------------------------ edge behaviour is NOT in scope

def test_edge_windows_still_trim_at_any_length():
    """(7) R3 changes the MID-clip bar only. Non-gameplay touching clip
    head or tail is still trimmed at any length — including spans far
    under the 5s keep bar, which must NOT be kept just because they are
    short."""
    codes, reasons, _ = _map(600.0, [_vlm_window(0.0, 1.2)])
    assert codes == ["CNT_EDGE_NONGAMEPLAY"]
    assert reasons[0]["params"]["edge"] == "head"

    codes, reasons, _ = _map(600.0, [_vlm_window(588.0, 600.0)])
    assert codes == ["CNT_EDGE_NONGAMEPLAY"]
    assert reasons[0]["params"]["edge"] == "tail"

    # a long head window trims exactly as before
    codes, reasons, _ = _map(600.0, [_vlm_window(0.0, 20.0)])
    assert codes == ["CNT_EDGE_NONGAMEPLAY"]
    assert reasons[0]["params"]["cut_at_s"] == 20.5


# ----------------------------------------------- R2: the ratchet is gone

def test_same_span_same_verdict_across_clip_durations():
    """(8) The load-bearing proof that the ratchet is gone: an identical
    span gets an identical verdict regardless of how long the clip is.

    A 1.5s blip is the exact case that drove the cascade. Under the old
    rule it was KEPT in a 2000s parent (1.5 <= 0.2% * 2000 = 4.0) and CUT
    in the 200s child carved out of it (1.5 > 0.4) — the child re-split
    for no reason but its own shortness. Both paths are checked, because
    both had the ratchet.
    """
    short_dur, long_dur = 200.0, 2000.0

    for span, expect_cut in ((1.5, False), (3.0, False), (6.0, True)):
        vlm_short, _, _ = _map(
            short_dur, [_vlm_window(100.0, 100.0 + span)])
        vlm_long, _, _ = _map(
            long_dur, [_vlm_window(100.0, 100.0 + span)])
        assert vlm_short == vlm_long, (
            f"VLM path: {span}s verdict differs between a {short_dur}s and "
            f"a {long_dur}s clip — the ratchet is still live")
        assert ("CNT_MID_NONGAMEPLAY" in vlm_short) is expect_cut

        scn_short, _, _ = _map(
            short_dur, extra=[_scanner_window(100.0, 100.0 + span)])
        scn_long, _, _ = _map(
            long_dur, extra=[_scanner_window(100.0, 100.0 + span)])
        assert scn_short == scn_long, (
            f"scanner path: {span}s verdict differs between a {short_dur}s "
            f"and a {long_dur}s clip — the ratchet is still live")
        assert ("CNT_MID_NONGAMEPLAY" in scn_short) is expect_cut


def test_keep_gate_frac_is_gone_from_config():
    """R2 removed the constant itself, not just its use — a stray
    reference must not be able to reintroduce the ratchet silently."""
    assert not hasattr(C, "KEEP_GATE_MAX_FRAC")


# ============================================ r-loop 3 consequences of R1-R3
# The rulings are settled; these pin MECHANICAL consequences they did not
# consider, all found by the split-cascade review lane.

def test_long_window_with_short_frozen_run_is_cut_not_kept():
    """r-loop 3: the keep test must consider what actually SHIPS.

    `span` is the refined frozen run; `[cut0, cut1]` is the union with the
    VLM window and is what is delivered (on a keep) or blanked (on a gate).
    Testing only the frozen run meant a non-gameplay stretch of ANY length
    kept so long as its longest still run was under the bar — a 30s cutscene
    built from 3-4s held shots scored span=4.9 and shipped whole, and with
    no inputs inside it produced no reason at all and went straight to
    READY. At the old 2.0s bar the same input cut, so raising the bar is
    what exposed it."""
    rep = {"duration_s": 600.0, "vlm": {"windows": [
        _vlm_window(100.0, 130.0, action_frames=42)]}}
    aux = {"refined": {(100.0, 130.0): (110.0, 114.9)},
           "extra_windows": [], "afk_windows": []}
    reasons, advisories = [], []
    validate._map_windows(rep, aux, reasons, advisories)
    codes = [r["code"] for r in reasons]
    assert codes == ["CNT_MID_NONGAMEPLAY"], (
        "a 30s cutscene must not be KEPT because its longest frozen run "
        "happens to be under the bar")
    assert reasons[0]["params"]["cut"] == [100.0, 130.0]

    # ...and with no inputs it must still not silently ship
    reasons, advisories = [], []
    rep["vlm"]["windows"] = [_vlm_window(100.0, 130.0, action_frames=0)]
    validate._map_windows(rep, aux, reasons, advisories)
    assert [r["code"] for r in reasons] == ["CNT_MID_NONGAMEPLAY"]


def test_short_window_still_keeps_when_both_bounds_clear_the_bar():
    """The complement: the fix above must not turn every keep into a cut."""
    rep = {"duration_s": 600.0, "vlm": {"windows": [
        _vlm_window(100.0, 104.0, action_frames=7)]}}
    aux = {"refined": {(100.0, 104.0): (101.0, 102.5)},
           "extra_windows": [], "afk_windows": []}
    reasons, advisories = [], []
    validate._map_windows(rep, aux, reasons, advisories)
    assert [r["code"] for r in reasons] == ["INP_FROZEN_ACTIONS"]


def test_gate_step_runs_after_the_csv_writers():
    """FIX_KEY_HYGIENE re-resolves input_actions for every row from
    keys|buttons plus the motion flags, and motion-bound semantics (kamla
    `look: mouse`) fire from dx/dy alone — which the gate deliberately
    leaves as captured. Planned BEFORE hygiene, the gate was undone in the
    same pass that applied it."""
    plan = fix.plan_fixes(
        [{"code": "INP_FROZEN_ACTIONS", "blocking": True, "fixable": True,
          "params": {"t0": 10.0, "t1": 12.0}},
         {"code": "INP_OSKEYS", "blocking": True, "fixable": True,
          "params": {"keys": {"cmd": 3}}}],
        game="kamla", has_raw=False)
    ids = [f for f, _ in plan["steps"]]
    assert "FIX_GATE_WINDOW" in ids and "FIX_KEY_HYGIENE" in ids
    assert ids.index("FIX_GATE_WINDOW") > ids.index("FIX_KEY_HYGIENE"), (
        "the gate only blanks, so it must run last among the frames.csv "
        "writers or hygiene repopulates the actions it just cleared")


def test_synthetic_timeline_is_declared_and_acts_on_nothing():
    """scan_video falls back to a uniform grid when the decoded frame count
    disagrees with the packet count. These captures drop 12-20% of frames,
    so wherever drops cluster that grid is seconds off — and its bounds were
    fed to the cutter as real-PTS cut points, removing genuine gameplay
    while leaving the freeze in place (which the child then re-split on)."""
    diffs = [5.0] * 30 + [0.2] * 20 + [5.0] * 30
    times = [i * 0.1 for i in range(len(diffs) + 1)]
    real = MotionTimeline(n_frames=len(diffs) + 1, fps=10.0,
                          duration_s=times[-1] + 0.1, times_s=times,
                          diffs=diffs, luma=[100.0] * (len(diffs) + 1))
    assert real.timing == "real_pts"        # default must stay honest
    synth = MotionTimeline(n_frames=len(diffs) + 1, fps=10.0,
                           duration_s=times[-1] + 0.1, times_s=times,
                           diffs=diffs, luma=[100.0] * (len(diffs) + 1),
                           timing="uniform_fps")
    assert synth.timing != "real_pts"
