"""Keybind handling.

Vendor keybind is `{semantic_action: literal | [literals] | {modifier,key}}`
(the capture tool / contributor supplies it). We need two things:

  1. an action *resolver* — given the keys/buttons/motion active in a frame,
     which semantic actions fired (frames.csv `input_actions`).
  2. the spec key_binding.json — `{literal: [semantic, ...]}`, canonical
     lowercase, with FULL COVERAGE of every literal seen in input_keys
     (spec §2.2), and no junk entries.
"""
from __future__ import annotations

from collections import defaultdict

from .keys import normalize_literal, is_motion_literal

# A modifier literal matches any of these held tokens. A bare "shift" bind
# matches either side; a side-specific "shift_l" bind also matches the bare,
# side-ambiguous "shift" the capture sometimes emits (so a keybind on L-Shift
# still resolves when inputs.jsonl only reports "shift").
_MOD_ALTS = {
    "shift": ("shift", "shift_l", "shift_r"),
    "shift_l": ("shift_l", "shift"),
    "shift_r": ("shift_r", "shift"),
    "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
    "ctrl_l": ("ctrl_l", "ctrl"),
    "ctrl_r": ("ctrl_r", "ctrl"),
    "alt": ("alt", "alt_l", "alt_r", "alt_gr"),
    "alt_l": ("alt_l", "alt"),
    "alt_r": ("alt_r", "alt"),
}


def _alts(token: str) -> tuple[str, ...]:
    return _MOD_ALTS.get(token, (token,))


# motion-bind literal -> which mouse axis fires it
_MOTION_AXES = {"mouse": "any", "mouse_x": "x", "mouse_y": "y"}


def _binding_groups(value) -> list[tuple[list[tuple[str, ...]], str | None]]:
    """Flatten one keybind value into (groups, motion_axis).

    A group is a list of alt-sets; ALL alt-sets must be satisfied (AND), each
    alt-set is satisfied if ANY of its tokens is held (OR). motion_axis is
    'x'/'y'/'any' for a mouse-motion bind (fires on dx/dy, not a held key),
    None for a key bind.
    """
    out: list[tuple[list[tuple[str, ...]], str | None]] = []
    if isinstance(value, str):
        tok = normalize_literal(value)
        # an empty normalized token (whitespace, or normalize_literal's ""
        # for junk) must never become a group: "" in bound_literals would
        # defeat resolve_keybind's parsed-but-unusable fallback (r-loop 8)
        if not tok:
            pass
        elif is_motion_literal(tok):
            out.append(([], _MOTION_AXES[tok]))
        else:
            out.append(([_alts(tok)], None))
    elif isinstance(value, list):
        for item in value:
            out.extend(_binding_groups(item))
    elif isinstance(value, dict):
        mod = value.get("modifier")
        key = value.get("key")
        groups: list[tuple[str, ...]] = []
        if mod:
            m = normalize_literal(mod)
            # a modifier that normalizes empty (a VK number) is dropped
            # ALONE — the key-only group still binds (r-loop 8)
            if m:
                groups.append(_alts(m))
        if "key" in value:
            # PRESENCE gates this path, not truthiness: a present-but-falsy
            # key ("" / 0 / null) used to slip past `if key:` and emit the
            # bare-modifier group the rule below forbids (r-loop 9). The
            # modifier-only fallthrough survives ONLY for a genuinely
            # ABSENT key field.
            t = normalize_literal(key)
            if not t:
                # a key that normalizes empty makes the WHOLE binding
                # unusable — emitting the bare modifier group would fire
                # the semantic on the modifier alone (r-loop 8)
                return out
            if is_motion_literal(t):
                out.append(([], _MOTION_AXES[t]))
                return out
            groups.append(_alts(t))
        if groups:
            out.append((groups, None))
    return out


def build_resolver(keybind: dict):
    """Return list of (groups, motion_axis, semantic) rules."""
    rules = []
    for semantic, value in keybind.items():
        for groups, motion_axis in _binding_groups(value):
            rules.append((groups, motion_axis, semantic))
    return rules


def resolve_actions(held: set[str], motion, rules, *,
                    context: str | None = None,
                    allowed: dict[str, frozenset[str]] | None = None,
                    credited_out: set[str] | None = None):
    """Resolve one frame -> (actions, dead_literals).

    `motion` is (dx_active, dy_active); a bare bool means both axes (legacy).
    `context`/`allowed` enable per-game context gating: a rule whose semantic
    has an `allowed` context-set fires only when `context` is in it. A held
    literal whose satisfied rules were ALL blocked by context gating is
    returned in dead_literals — the key does nothing in this game mode, so the
    caller can strip it from input_keys (v2 "unbound in this context" rule).
    With context=None nothing is gated and dead_literals is always empty.

    `credited_out`, when a set is passed, receives the CREDITED literals:
    held tokens appearing in a group of a rule whose FULL group set was
    satisfied and that fired (ctx_ok). A bound token NOT in this set did
    nothing this frame — the {modifier, key} combo-half case: both halves
    are in bound_literals, but a half held alone satisfies no rule, so
    keeping it ships a key with null actions and violates the delivery
    invariant (r15 #5, RULED 2026-08-19: strip-and-count at the writer).
    The accounting existed for the context path; this exposes it to the
    no-context path too. Motion-axis rules credit no literals (their lits
    set is empty), so keys are never stripped for lacking motion.

    Actions keep keybind insertion order, deduped. Note a literal may fire >1
    semantic in one context (Space in suit: jump + jetpack_boost); the caller
    collapses those runs (collapse_ambiguous_runs) using press evidence.
    """
    if isinstance(motion, bool):
        motion = (motion, motion)
    dx_active, dy_active = motion
    fired: dict[str, None] = {}
    credited: set[str] = set()
    blocked: set[str] = set()
    for groups, motion_axis, semantic in rules:
        if motion_axis is not None:
            satisfied = (dx_active if motion_axis == "x" else
                         dy_active if motion_axis == "y" else
                         dx_active or dy_active)
            lits: set[str] = set()
        else:
            satisfied = bool(groups) and all(any(t in held for t in alt)
                                             for alt in groups)
            lits = {t for alt in groups for t in alt if t in held}
        if not satisfied:
            continue
        ctx_ok = (context is None or allowed is None
                  or semantic not in allowed
                  or context in allowed[semantic])
        if ctx_ok:
            fired[semantic] = None
            credited |= lits
        else:
            blocked |= lits
    if credited_out is not None:
        credited_out |= credited
    return list(fired.keys()), blocked - credited


def collapse_ambiguous_runs(per_frame: list[list[str]], pair: tuple[str, str],
                            fps: float, *, tap_s: float = 0.30,
                            overrides: dict[int, str] | None = None) -> dict[int, str]:
    """Pick one of an exclusive action pair per contiguous run of frames.

    `pair` = (tap_action, hold_action), e.g. (movement_jump,
    movement_jetpack_boost) for suit Space. Runs where both fired are
    collapsed: run shorter than `tap_s` -> tap_action, else hold_action —
    UNLESS `overrides` maps the run's start frame to an explicit action
    (video-verified label). Returns {frame_index: chosen_action} and edits
    per_frame in place.
    """
    tap, hold = pair
    chosen: dict[int, str] = {}
    i, n = 0, len(per_frame)
    while i < n:
        if tap in per_frame[i] and hold in per_frame[i]:
            j = i
            while j < n and tap in per_frame[j] and hold in per_frame[j]:
                j += 1
            pick = (overrides or {}).get(i)
            if pick is None:
                pick = tap if (j - i) < tap_s * fps else hold
            drop = hold if pick == tap else tap
            for k in range(i, j):
                per_frame[k] = [a for a in per_frame[k] if a != drop]
                chosen[k] = pick
            i = j
        else:
            i += 1
    return chosen


def bound_literals(keybind: dict) -> frozenset[str]:
    """Canonical tokens that are explicitly bound — these survive OS-key stripping."""
    toks: set[str] = set()
    for _, value in keybind.items():
        for groups, _is_motion in _binding_groups(value):
            for alt in groups:
                toks.update(alt)
    return frozenset(toks)


def invert_keybind(keybind: dict, *, also_cover: set[str] | None = None) -> dict:
    """Vendor `{semantic: literal(s)}` -> spec `{literal: [semantic, ...]}`.

    Canonical lowercase. Combos encoded as `mod+key`. `also_cover` adds any
    extra literals seen in input_keys with an empty list, giving full coverage
    per spec §2.2 without inventing semantics.
    """
    inv: dict[str, list[str]] = defaultdict(list)

    def add(literal: str, semantic: str):
        lit = normalize_literal(literal)
        if semantic not in inv[lit]:
            inv[lit].append(semantic)

    for semantic, value in keybind.items():
        if isinstance(value, str):
            add(value, semantic)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add(item, semantic)
                elif isinstance(item, dict):
                    mod = normalize_literal(item.get("modifier", ""))
                    key = normalize_literal(item.get("key", ""))
                    add(f"{mod}+{key}", semantic)
        elif isinstance(value, dict):
            mod = normalize_literal(value.get("modifier", ""))
            key = normalize_literal(value.get("key", ""))
            add(f"{mod}+{key}", semantic)

    for lit in (also_cover or set()):
        inv.setdefault(normalize_literal(lit), [])

    return {k: inv[k] for k in sorted(inv)}
