"""
Canonical game registry — fix for issue D1 (free-text `game.name`).

Before this module existed, the "Pick the game" UI let contributors type a
free-form name, which produced `Outer wild` / `Outerworld` / `Outerwild` for
the same title across sessions (see HumynCapture_Capture_Tool_Issues.md D1).
That broke game -> keybind lookup and delivery-folder grouping until the
translator added an exe-name fallback.

Fix: the UI now offers a dropdown of these known titles (never free text),
and the exe name — not the typed string — is the source of truth for the
canonical slug. `resolve_game()` is the single place that turns "whatever the
contributor picked / whatever process we attached to" into
`(slug, canonical_title)`. Both the raw display name AND the canonical slug
are stored in metadata (see SessionEngine._build_metadata /
SessionMetadata.game_display_name vs. game_slug) so d1's failure mode is
detectable even for sessions recorded before a game was added here.

Slugs here are intentionally the same strings translator/keybinds.py uses
(`kamla`, `outer_wilds`) — the capture tool's native v2 writer
(app/core/finalize/v2_writer.py) and the post-hoc translator must agree on
one slug per game.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GameEntry:
    slug: str
    title: str
    exe_name: str  # canonical exe basename, e.g. "OuterWilds.exe"


# The dropdown shown in the UI is exactly this list, in this order — no
# free-text entry. Add new titles here (and to translator/keybinds.py's
# KEYBINDS + translator/v2.py's GAME_TITLES/LOCALIZATIONS) together.
KNOWN_GAMES: list[GameEntry] = [
    GameEntry(slug="kamla", title="Kamla", exe_name="Kamla.exe"),
    GameEntry(slug="outer_wilds", title="Outer Wilds", exe_name="OuterWilds.exe"),
]

_BY_SLUG = {g.slug: g for g in KNOWN_GAMES}
_EXE_TO_SLUG = {g.exe_name.lower(): g.slug for g in KNOWN_GAMES}


def _collapse(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_COLLAPSED_TITLES = {_collapse(g.title): g.slug for g in KNOWN_GAMES}


def dropdown_titles() -> list[str]:
    """Titles for the UI's game picker, plus the escape hatch for an
    unlisted game (still canonicalized off the exe name, never free text)."""
    return [g.title for g in KNOWN_GAMES] + ["Other (detect from exe)"]


def resolve_game(picked_title: str | None, exe_name: str | None) -> tuple[str, str]:
    """Return (game_slug, canonical_title).

    exe_name is authoritative when it matches a known game — it's the
    reliable signal (D1); the contributor's dropdown pick is the fallback
    for display purposes only, and is itself constrained to KNOWN_GAMES so
    it can never introduce a new misspelling.

    Unknown games (neither the exe nor the pick matches a known title) get a
    slug derived from the exe basename so at least repeat sessions of the
    same unlisted game collide on the same slug instead of scattering.
    """
    if exe_name:
        exe_base = re.sub(r"\.exe$", "", exe_name, flags=re.IGNORECASE)
        slug = _EXE_TO_SLUG.get(exe_name.strip().lower())
        if slug:
            return slug, _BY_SLUG[slug].title
    else:
        exe_base = None

    if picked_title:
        slug = _COLLAPSED_TITLES.get(_collapse(picked_title))
        if slug:
            return slug, _BY_SLUG[slug].title

    # Unlisted game: derive a stable slug from the exe name (never from the
    # free-text pick) so re-recordings of the same unlisted title agree.
    fallback_source = exe_base or picked_title or "unknown_game"
    slug = _collapse(fallback_source) or "unknown_game"
    title = exe_base or picked_title or "Unknown Game"
    return slug, title
