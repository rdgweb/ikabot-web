"""A real combat fought again in the simulator (N-110).

From the report the hub keeps, what each side brought is read (the army at the start of the
first round, what joined later, whether there was a wall, how big the field was) and given to
the simulator. What it predicts is put beside what happened. Nothing here changes what the
simulator recommends: this is the measure that any later adjustment has to improve.
"""
from __future__ import annotations

from .report_parser import parse_rounds, parse_summary, unit_name

WALL = 314
SIMULATED_KINDS = range(301, 330)       # the simulator knows land units only


def _start(side: dict) -> dict[int, int]:
    """What the side had when the round began: every slot (left + fallen) and the reserve."""
    units: dict[int, int] = {}
    for slots in (side.get("zones") or {}).values():
        for slot in slots:
            if slot:
                units[slot["unit"]] = units.get(slot["unit"], 0) + slot["left"] + slot["lost"]
    for item in side.get("reserve") or []:
        units[item["unit"]] = units.get(item["unit"], 0) + item["left"]
    return units


def _end(side: dict) -> dict[int, int]:
    units: dict[int, int] = {}
    for slots in (side.get("zones") or {}).values():
        for slot in slots:
            if slot:
                units[slot["unit"]] = units.get(slot["unit"], 0) + slot["left"]
    for item in side.get("reserve") or []:
        units[item["unit"]] = units.get(item["unit"], 0) + item["left"]
    return units


def field_level(first_round: dict) -> int:
    """The size of the field, in the levels the simulator's table uses, from the slots the game drew."""
    zones = first_round["attacker"].get("zones") or {}
    front, artillery = len(zones.get("main") or []), len(zones.get("artillery") or [])
    if front <= 3:
        return 1
    if front <= 5:
        return 5
    if artillery <= 3:
        return 10
    return 17 if artillery == 4 else 25


def battle_inputs(rounds: list) -> dict:
    """What the simulator needs to fight this combat again, read from its rounds."""
    first = rounds[0]
    inputs = {"field_level": field_level(first), "wall_segments": 0, "attacker": {}, "defender": {}, "reinforcements": {"attacker": {}, "defender": {}}}
    for side in ("attacker", "defender"):
        start = _start(first[side])
        if side == "defender":
            inputs["wall_segments"] = sum(1 for slot in (first[side]["zones"].get("main") or []) if slot and slot["unit"] == WALL)
        inputs[side] = {unit: amount for unit, amount in start.items() if unit != WALL and amount > 0}
        # whoever shows up at the start of a later round and was not there at the end of the one before, joined
        for before, after in zip(rounds, rounds[1:]):
            ended, began = _end(before[side]), _start(after[side])
            joined = {unit: began[unit] - ended.get(unit, 0) for unit in began if unit != WALL and began[unit] - ended.get(unit, 0) > 0}
            if joined:
                inputs["reinforcements"][side][after["round"]] = joined
    return inputs


def real_outcome(rounds: list, summary: dict | None) -> dict:
    """What happened: who won, in how many rounds, what each side lost (the wall is not a unit here)."""
    losses = {"attacker": {}, "defender": {}}
    if summary and summary.get("units"):
        for item in summary["units"]:
            for side in losses:
                if item[side] and item[side]["lost"] and item["unit"] != WALL:
                    losses[side][item["unit"]] = item[side]["lost"]
    else:
        for parsed in rounds:
            for side in losses:
                for slots in (parsed[side].get("zones") or {}).values():
                    for slot in slots:
                        if slot and slot["lost"] and slot["unit"] != WALL:
                            losses[side][slot["unit"]] = losses[side].get(slot["unit"], 0) + slot["lost"]
    winner = ""
    if summary and summary.get("winners"):
        names = (summary.get("attacker") or {}).get("name") or (rounds[0]["attacker"].get("name") if rounds else "")
        first_winner = summary["winners"].split(",")[0].strip().lower()
        winner = "attacker" if names and first_winner and first_winner in names.lower() else "defender"
    return {"winner": winner, "rounds": rounds[-1]["round"] if rounds else 0, "losses": losses}


def simulate(inputs: dict, *, wall_level: int = 0, attacker_upgrades: dict | None = None, defender_upgrades: dict | None = None,
             attacker_damage_bonus_pct: float = 0.0, defender_damage_bonus_pct: float = 0.0, max_rounds: int = 40) -> dict:
    """What the simulator says about these armies."""
    from apps.espionage.services.battle_land import simulate_land_battle

    result = simulate_land_battle(
        attacker_units=dict(inputs["attacker"]),
        defender_units=dict(inputs["defender"]),
        attacker_upgrades=attacker_upgrades or {},
        defender_upgrades=defender_upgrades or {},
        town_hall_level=inputs["field_level"],
        wall_level=wall_level if inputs.get("wall_segments") else 0,
        max_rounds=max_rounds,
        attacker_damage_bonus_pct=attacker_damage_bonus_pct,
        defender_damage_bonus_pct=defender_damage_bonus_pct,
        attacker_reinforcements_by_round=inputs["reinforcements"]["attacker"] or None,
        defender_reinforcements_by_round=inputs["reinforcements"]["defender"] or None,
        calibrated=False,               # the check measures the simulator itself; any adjustment is given explicitly
    )
    return {
        "winner": result.get("winner") or "",
        "rounds": int(result.get("rounds") or 0),
        "losses": {
            "attacker": {int(unit): int(amount) for unit, amount in (result.get("attacker_losses") or {}).items()},
            "defender": {int(unit): int(amount) for unit, amount in (result.get("defender_losses") or {}).items()},
        },
    }


def loss_error(real: dict, predicted: dict) -> float:
    """How far the predicted losses of a side are from the real ones, 0 (the same) to 1 (nothing alike)."""
    units = set(real) | set(predicted)
    if not units:
        return 0.0
    gap = sum(abs(int(real.get(unit, 0)) - int(predicted.get(unit, 0))) for unit in units)
    scale = max(sum(int(value) for value in real.values()), sum(int(value) for value in predicted.values()), 1)
    return round(min(1.0, gap / scale), 4)


def compare(real: dict, predicted: dict) -> dict:
    return {
        # a report that ends without naming a winner (an army recalled, a log saved midway) has none to get right
        "winner_known": bool(real["winner"]),
        "winner_ok": bool(real["winner"]) and real["winner"] == predicted["winner"],
        "rounds_gap": predicted["rounds"] - real["rounds"],
        "attacker_error": loss_error(real["losses"]["attacker"], predicted["losses"]["attacker"]),
        "defender_error": loss_error(real["losses"]["defender"], predicted["losses"]["defender"]),
    }


def simulable(inputs: dict) -> bool:
    """There is a fight, and every unit in it is one the simulator has numbers for."""
    from apps.espionage.services.unit_stats import UNIT_STATS

    units = {int(unit) for side in ("attacker", "defender") for unit in inputs[side]}
    for joined in (inputs.get("reinforcements") or {}).values():
        for wave in joined.values():
            units |= {int(unit) for unit in wave}
    if any(unit not in UNIT_STATS for unit in units):
        return False
    return bool(inputs["attacker"]) and (bool(inputs["defender"]) or bool(inputs.get("wall_segments")))


def replay_pages(detailed_html: str, summary_html: str, **simulate_options) -> dict | None:
    """Everything at once for a stored report; None when there is no field to read."""
    rounds = parse_rounds(detailed_html)
    if not rounds or rounds[0].get("naval"):
        return None
    inputs = battle_inputs(rounds)
    if not simulable(inputs):
        return None
    real = real_outcome(rounds, parse_summary(summary_html))
    predicted = simulate(inputs, **simulate_options)
    return {"inputs": inputs, "real": real, "predicted": predicted, "score": compare(real, predicted)}


def describe_units(units: dict) -> str:
    return ", ".join(f"{unit_name(unit)} {amount}" for unit, amount in sorted(units.items(), key=lambda item: -item[1])) or "nada"
