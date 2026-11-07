"""N-172: how long a trip between two islands takes, the way the game counts it.

The game divides distance × a base time by the speed of the slowest unit, with a floor. Measured on real trips:
- ships: a merchant ship (speed 60) loaded in full takes 600 s per map unit, 300 s within the same island;
- troops: twice the ships' base — hoplites and mortars (slowest 60) took 20 365 s for 16,97 units (1 200 s per unit).
"""
from __future__ import annotations

import math

from .unit_stats import UNIT_STATS

JOURNEY_BASE = 36_000        # ships: seconds × speed per map unit (600 s at speed 60)
TROOPS_JOURNEY_BASE = 72_000  # troops: 1 200 s per map unit at speed 60
MIN_JOURNEY = 300
LAND_DEFAULT_SPEED = 60


def island_distance(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.dist(a, b)


def _slowest(unit_ids) -> int | None:
    speeds = [int(UNIT_STATS[int(uid)].get("speed") or 0) for uid in unit_ids if int(uid) in UNIT_STATS]
    speeds = [s for s in speeds if s > 0]
    return min(speeds) if speeds else None


def journey_seconds(distance: float, speed: int, base: int = JOURNEY_BASE) -> int:
    if speed <= 0:
        return 0
    return max(MIN_JOURNEY, math.ceil(distance * base / speed))


def troops_journey(distance: float, unit_ids) -> int:
    """Troops go at their slowest unit, on the troops' base."""
    return journey_seconds(distance, _slowest(unit_ids) or LAND_DEFAULT_SPEED, TROOPS_JOURNEY_BASE)


def fleet_journey(distance: float, ship_ids) -> int:
    slowest = _slowest(ship_ids)
    return journey_seconds(distance, slowest) if slowest else 0


def human(seconds: int) -> str:
    if seconds <= 0:
        return "?"
    hours, minutes = divmod(round(seconds / 60), 60)
    if not hours:
        return f"{minutes}min"
    return f"{hours}h{minutes:02d}" if minutes else f"{hours}h"
