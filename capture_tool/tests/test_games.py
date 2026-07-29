from app.core.games import resolve_game


def test_exe_name_is_authoritative_over_typos():
    for typo in ("Outer wild", "Outerworld", "Outerwild", "outer wilds", ""):
        slug, title = resolve_game(typo, "OuterWilds.exe")
        assert slug == "outer_wilds"
        assert title == "Outer Wilds"


def test_kamla_exe_resolves():
    slug, title = resolve_game(None, "Kamla.exe")
    assert slug == "kamla"
    assert title == "Kamla"


def test_dropdown_pick_used_when_no_exe_match():
    slug, title = resolve_game("Outer Wilds", None)
    assert slug == "outer_wilds"
    assert title == "Outer Wilds"


def test_unknown_game_gets_stable_slug_from_exe():
    slug1, title1 = resolve_game("Some Typo Name", "MysteryGame.exe")
    slug2, title2 = resolve_game("Another Typo", "MysteryGame.exe")
    assert slug1 == slug2  # repeat sessions of the same unlisted game collide
    assert title1 == title2 == "MysteryGame"


def test_case_and_spacing_insensitive_exe_match():
    slug, _ = resolve_game(None, "outerwilds.exe")
    assert slug == "outer_wilds"
