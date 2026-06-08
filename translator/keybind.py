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


def _binding_groups(value) -> list[tuple[list[tuple[str, ...]], bool]]:
    """Flatten one keybind value into (groups, is_motion).

    A group is a list of alt-sets; ALL alt-sets must be satisfied (AND), each
    alt-set is satisfied if ANY of its tokens is held (OR). is_motion marks a
    mouse-motion bind (fires on dx/dy, not on a held key).
    """
    out: list[tuple[list[tuple[str, ...]], bool]] = []
    if isinstance(value, str):
        tok = normalize_literal(value)
        if is_motion_literal(tok):
            out.append(([], True))
        else:
            out.append(([_alts(tok)], False))
    elif isinstance(value, list):
        for item in value:
            out.extend(_binding_groups(item))
    elif isinstance(value, dict):
        mod = value.get("modifier")
        key = value.get("key")
        groups: list[tuple[str, ...]] = []
        if mod:
            groups.append(_alts(normalize_literal(mod)))
        if key:
            t = normalize_literal(key)
            if is_motion_literal(t):
                out.append(([], True))
                return out
            groups.append(_alts(t))
        if groups:
            out.append((groups, False))
    return out


def build_resolver(keybind: dict):
    """Return list of (groups, is_motion, semantic) rules."""
    rules = []
    for semantic, value in keybind.items():
        for groups, is_motion in _binding_groups(value):
            rules.append((groups, is_motion, semantic))
    return rules


def resolve_actions(held: set[str], moving: bool, rules) -> list[str]:
    """Semantic actions active this frame, in keybind insertion order, deduped."""
    fired: dict[str, None] = {}
    for groups, is_motion, semantic in rules:
        if is_motion:
            if moving:
                fired[semantic] = None
        elif groups and all(any(t in held for t in alt) for alt in groups):
            fired[semantic] = None
    return list(fired.keys())


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
