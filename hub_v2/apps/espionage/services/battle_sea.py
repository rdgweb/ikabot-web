"""Sea battle simulation (N-113).

The game fights at sea on the same field it uses on land, with other names on the lines and no
wall, and the morale follows the same rule (the tenders feed it instead of the cooks). So the
engine is the one of the land battles (`battle_land`), given the field of the sea and the ships.
"""
from __future__ import annotations

from typing import Any

from .battle_land import FIELD_SLOTS, SEA_FIELDS, simulate_land_battle
from .unit_stats import UNIT_STATS

DEFAULT_ROUNDS = 40                 # sea battles go on for long: 33 rounds in one of the logs read
WARSHIPS = {210, 211, 212, 213, 214, 215, 216, 217, 218, 219, 220, 221, 222, 223, 224, 225}
FRONT_SHIPS = (216, 225, 223, 211, 221, 210)


def sea_field(port_level: int | None) -> int:
    """The field the game draws for the port (or shipyard) of the city attacked, by its level."""
    level = int(port_level or 0)
    if level >= 29:
        return 105
    if level >= 22:
        return 104
    if level >= 15:
        return 103
    if level >= 8:
        return 102
    return 101


def sea_field_from_slots(first_strike_slots: int) -> int:
    """The same field read from a report: each size has its own number of first-strike slots (1 to 5)."""
    return SEA_FIELDS[max(1, min(len(SEA_FIELDS), int(first_strike_slots or 1))) - 1]


def is_fleet(units: dict | None) -> bool:
    return any(int(unit) in WARSHIPS and int(amount or 0) > 0 for unit, amount in (units or {}).items())


def simulate_sea_battle(
    attacker_units: dict[int, int],
    defender_units: dict[int, int],
    *,
    attacker_upgrades: dict[int, dict] | None = None,
    defender_upgrades: dict[int, dict] | None = None,
    port_level: int | None = None,
    field: int | None = None,
    max_rounds: int = DEFAULT_ROUNDS,
    attacker_damage_bonus_pct: float = 0.0,
    defender_damage_bonus_pct: float = 0.0,
    attacker_reinforcements_by_round: dict[int, dict[int, int]] | None = None,
    defender_reinforcements_by_round: dict[int, dict[int, int]] | None = None,
    morale: bool = True,
) -> dict[str, Any]:
    """Two fleets on the field of a port. `field` (101-105) wins over `port_level` when both are given."""
    key = field if field in SEA_FIELDS else sea_field(port_level)
    result = simulate_land_battle(
        attacker_units,
        defender_units,
        attacker_upgrades=attacker_upgrades,
        defender_upgrades=defender_upgrades,
        wall_level=0,
        max_rounds=max_rounds,
        attacker_damage_bonus_pct=attacker_damage_bonus_pct,
        defender_damage_bonus_pct=defender_damage_bonus_pct,
        attacker_reinforcements_by_round=attacker_reinforcements_by_round,
        defender_reinforcements_by_round=defender_reinforcements_by_round,
        calibrated=False,           # the adjustment approved on the screen was learned from land combats
        morale=morale,
        field_key=key,
    )
    result["naval"] = True
    return result


def _ships_only(units: dict | None) -> dict[int, int]:
    return {int(unit): int(amount) for unit, amount in (units or {}).items() if int(unit) in UNIT_STATS and int(unit) in WARSHIPS and int(amount or 0) > 0}


def recommend_fleet(
    available: dict[int, int],
    enemy_fleet: dict[int, int] | None,
    *,
    first_guess: dict[int, int] | None = None,
    attacker_upgrades: dict[int, dict] | None = None,
    defender_upgrades: dict[int, dict] | None = None,
    port_level: int | None = None,
) -> dict[str, Any]:
    """The smallest of a few fleets that the simulator says wins: a first guess, then more of everything, then all.

    Returns {"can_win", "recommended", "simulation"}. When nothing wins, `recommended` is the
    first guess (what was sent before there was a simulator) and `can_win` is False.
    """
    available = _ships_only(available)
    enemy = _ships_only(enemy_fleet)
    guess = {unit: min(amount, available.get(unit, 0)) for unit, amount in _ships_only(first_guess).items() if available.get(unit, 0) > 0}
    if not available:
        return {"can_win": False, "recommended": {}, "simulation": None}
    if not enemy:
        # nobody to fight: what the first guess says, or a couple of front ships to hold the port
        if not guess:
            for unit in FRONT_SHIPS:
                if available.get(unit, 0) > 0:
                    guess = {unit: min(2, available[unit])}
                    break
        return {"can_win": True, "recommended": guess, "simulation": None}

    def scaled(factor: float) -> dict[int, int]:
        """The first guess with more of each ship it has, up to what there is."""
        return {unit: min(available[unit], int(round(amount * factor))) for unit, amount in guess.items()}

    candidates = []
    if guess:
        candidates += [guess, scaled(1.5), scaled(2.0), scaled(3.0)]
    candidates.append(dict(available))
    seen: list[dict] = []
    last = None
    for fleet in candidates:
        if not fleet or fleet in seen:
            continue
        seen.append(fleet)
        last = simulate_sea_battle(fleet, enemy, attacker_upgrades=attacker_upgrades, defender_upgrades=defender_upgrades, port_level=port_level)
        if last["winner"] == "attacker":
            return {"can_win": True, "recommended": fleet, "simulation": last}
    return {"can_win": False, "recommended": guess or dict(available), "simulation": last}


__all__ = ["FIELD_SLOTS", "SEA_FIELDS", "is_fleet", "recommend_fleet", "sea_field", "sea_field_from_slots", "simulate_sea_battle"]
