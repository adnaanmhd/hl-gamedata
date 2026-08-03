from app.core.ffprobe import _parse_rational


def test_parse_rational_fraction():
    assert _parse_rational("30/1") == 30.0
    assert _parse_rational("24000/1001") is not None and abs(_parse_rational("24000/1001") - 23.976) < 0.01


def test_parse_rational_plain_number():
    assert _parse_rational("25") == 25.0


def test_parse_rational_none_or_empty():
    assert _parse_rational(None) is None
    assert _parse_rational("") is None


def test_parse_rational_zero_denominator():
    assert _parse_rational("30/0") is None
