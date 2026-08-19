"""r-loop 17 fixes (K-set, R8_IMPLEMENTATION_PLAN §0) — translator side.

Each test cites the iteration-17 finding it pins (r17 #N, findings of
record in R17_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 7ad7b71 (session scratchpad), per plan §0/§1.
"""
from __future__ import annotations

# v2 accessed by attribute so the fail-first scratch run fails PER TEST
from translator import v2


# ------- r17 #3 (K3): apply_context_to_rows' _active degrades on junk
# ------- motion cells


def _ctx_rows():
    """v1-canonical rows (last five = keys/actions/btns/dx/dy) over
    custom semantics OUTSIDE the OW allowed-table, so context gating
    no-ops and the motion axis is the only variable under test."""
    return [
        # junk dx (locale decimal) + junk dy (stringified None): the
        # STR_SENTINELS population arriving from a foreign no-sidecar
        # delivery — a junk cell is not motion
        ["0", "0", "e", "", "", "1,5", "None"],
        # numeric control: real motion fires the motion-bound semantic
        ["1", "33", "e", "", "", "7.0", "0.0"],
        # zero control: numeric zero is not motion
        ["2", "67", "e", "", "", "0.0", "0.0"],
    ]


def test_apply_context_degrades_on_junk_motion_cells():
    """r17 #3 (K3): _active did a bare float(v) on delivered CSV cells
    and raised 'ValueError: could not convert string to float: 1,5' out
    of FIX_ACTIONS_CONTEXT — which plan_fixes structurally orders
    BEFORE the FIX_SENTINELS step that repairs those very cells, so
    both attempts burned on a crash whose cure sat later in the same
    plan (the r12 #6 shape re-opened): wrongful terminal reject, two
    paid VLM sweeps. Guarded like fix.py's _moving (review-2 #7): a
    junk cell is not motion; keys still resolve on the poisoned row."""
    from translator.keybind import build_resolver
    rules = build_resolver({"k3_interact": "e", "k3_look": "mouse"})
    rows = _ctx_rows()
    summary = v2.apply_context_to_rows(
        rows, ["on_foot"] * len(rows), "outer_wilds", rules, 30.0)
    assert summary["frames_changed"] >= 1
    acts = [r[-4].split("|") for r in rows]
    assert "k3_interact" in acts[0], \
        "keys must still resolve on the junk-cell row"
    assert "k3_look" not in acts[0], "a junk cell is not motion"
    assert "k3_look" in acts[1], "numeric control: real motion fires"
    assert "k3_look" not in acts[2], "numeric zero is not motion"
