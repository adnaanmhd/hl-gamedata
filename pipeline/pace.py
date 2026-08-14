"""Pace math (plan §11.3/§14): needed hours/day per game against the
Aug 24 23:59 IST deadline, trailing 24 h delivery rate, projection, alarm.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import config as C


@dataclass
class PaceStatus:
    need_per_game: dict[str, float] = field(default_factory=dict)
    need_total: float = 0.0
    trailing_24h: float = 0.0
    days_left: float = 0.0
    projected_finish: date | None = None      # None = never at current rate
    alarm: bool = False
    min_per_player_day: float = 0.0


def compute(delivered_by_game: dict[str, float], trailing_24h: float,
            now_ist: datetime, *, n_players: int = 150) -> PaceStatus:
    """delivered_by_game: delivered hours per game (post-trim, R10)."""
    p = PaceStatus(trailing_24h=trailing_24h)
    seconds_left = (C.DEADLINE_IST - now_ist).total_seconds()
    p.days_left = max(seconds_left / 86400.0, 1e-6)
    remaining_total = 0.0
    for g in C.GAMES:
        remaining = max(0.0, C.TARGET_HOURS_PER_GAME
                        - delivered_by_game.get(g, 0.0))
        remaining_total += remaining
        p.need_per_game[g] = remaining / p.days_left
    p.need_total = remaining_total / p.days_left
    if remaining_total <= 0:
        p.projected_finish = now_ist.date()
        p.alarm = False
        return p
    if trailing_24h > 0:
        p.projected_finish = (
            now_ist + timedelta(days=remaining_total / trailing_24h)).date()
    else:
        p.projected_finish = None
    p.alarm = (p.need_total > trailing_24h * C.PACE_ALARM_FACTOR
               or p.projected_finish is None
               or p.projected_finish > C.DEADLINE_IST.date())
    if n_players > 0:
        p.min_per_player_day = p.need_total * 60.0 / n_players
    return p
