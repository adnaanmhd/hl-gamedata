"""Regression coverage for list_likely_games matching the real shipped app.

Real bug: this function's bytecode never decompiled (same as the UI files),
so it was reinvented from scratch with a small exclusion list AND a
dedup-by-name step the real implementation never has. A real game (Outer
Wilds) went missing from the GUI dropdown as a result. Restored verbatim
from pycdas disassembly of the shipped exe; these tests lock in the parts
that differ from what a naive reimplementation would do.
"""
from unittest.mock import MagicMock, patch

from app.core import process_watcher as pw


def _fake_proc(pid, name, create_time, exe=""):
    p = MagicMock()
    p.info = {"pid": pid, "name": name, "create_time": create_time, "exe": exe}
    return p


def test_does_not_deduplicate_by_name():
    """The real implementation returns one entry PER PROCESS, never
    collapsing multiple same-named processes into one — a naive
    reimplementation dropping "duplicates" would be the bug this guards."""
    procs = [
        _fake_proc(100, "OuterWilds.exe", 0),
        _fake_proc(101, "OuterWilds.exe", 0),
    ]
    with patch.object(pw.time, "time", return_value=100.0), \
         patch.object(pw.psutil, "process_iter", return_value=procs):
        out = pw.list_likely_games(min_uptime_sec=5.0)
    assert len(out) == 2
    assert {p["pid"] for p in out} == {100, 101}


def test_excludes_only_the_real_skip_list_not_arbitrary_games():
    procs = [
        _fake_proc(1, "steam.exe", 0),          # in the real skip list
        _fake_proc(2, "OuterWilds.exe", 0),     # a real game, must survive
    ]
    with patch.object(pw.time, "time", return_value=100.0), \
         patch.object(pw.psutil, "process_iter", return_value=procs):
        out = pw.list_likely_games(min_uptime_sec=5.0)
    names = {p["name"] for p in out}
    assert names == {"OuterWilds.exe"}


def test_respects_min_uptime():
    procs = [_fake_proc(1, "OuterWilds.exe", create_time=99.0)]  # 1s old
    with patch.object(pw.time, "time", return_value=100.0), \
         patch.object(pw.psutil, "process_iter", return_value=procs):
        out = pw.list_likely_games(min_uptime_sec=5.0)
    assert out == []


def test_gamey_processes_sort_first_regardless_of_uptime():
    procs = [
        _fake_proc(1, "Launcher.exe", create_time=0.0,
                   exe=r"C:\Some\App\Launcher.exe"),  # older, not gamey
        _fake_proc(2, "OuterWilds.exe", create_time=50.0,
                   exe=r"C:\Program Files\SteamApps\common\Outer Wilds\OuterWilds.exe"),
    ]
    with patch.object(pw.time, "time", return_value=100.0), \
         patch.object(pw.psutil, "process_iter", return_value=procs):
        out = pw.list_likely_games(min_uptime_sec=5.0)
    assert out[0]["name"] == "OuterWilds.exe"
    assert out[0]["is_gamey"] is True


def test_one_unreadable_process_does_not_empty_the_whole_list():
    good = _fake_proc(1, "OuterWilds.exe", create_time=0.0)
    bad = MagicMock()
    bad.info = MagicMock(__getitem__=MagicMock(side_effect=RuntimeError("boom")))
    with patch.object(pw.time, "time", return_value=100.0), \
         patch.object(pw.psutil, "process_iter", return_value=[bad, good]):
        out = pw.list_likely_games(min_uptime_sec=5.0)
    assert [p["name"] for p in out] == ["OuterWilds.exe"]
