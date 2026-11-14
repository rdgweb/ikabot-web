"""The best army against one barbarian village, chosen by the simulator (N-182).

The field of the village has a number of slots per line; what does not fit waits in the reserve.
So the battle is shortened by what fills each slot, not by sending more. For each line the
planner tries the units the town has (filling the line, and once more to replace who falls),
the cooks that hold the morale up, and enough in reserve not to look outnumbered. Every
candidate is fought in the simulator, with the account's upgrades, the village's defenders and
wall, and the adjustment learned from the real barbarian battles. The best one:

  1. wins;  2. loses the least (by the cost of the units);  3. ends in the fewest rounds;
  4. ends with the morale highest;  5. sends the fewest units.

It also says what training more of some unit would gain (fewer rounds, no losses).
"""
from __future__ import annotations

import itertools
import logging
import math

logger = logging.getLogger(__name__)

# which units may hold each line, in the order they are tried
LINE_UNITS = {
    "principal": (303, 308, 319),
    "flancos": (302, 315),
    "longa": (304, 313, 301),
    "artilharia": (305, 306, 307),
    "bomb": (309,),
    "aa": (312,),
}
COOK, DOCTOR = 310, 311
COOK_OPTIONS = (0, 5, 10, 20, 30)
MORALE_FLOOR = 40               # an army whose morale ends below this was close to fleeing
TRAINABLE = (303, 308, 302, 304, 305, 309, 312, 310)
# barbarian names as the village screen shows them -> the game's ids
BARBARIAN_NAMES = (("clube", 320), ("faca", 321), ("aríete", 324), ("ariete", 324), ("catapulta", 325), ("viking", 316))


def defenders_from_names(defenders) -> tuple[dict[int, int], list[str]]:
    """{id: count} from the village screen's [(name, count)]; the names it does not know are returned apart."""
    out: dict[int, int] = {}
    unknown = []
    for name, qty in defenders or []:
        low = str(name).lower()
        uid = next((i for key, i in BARBARIAN_NAMES if key in low), None)
        if uid is None:
            unknown.append(str(name))
            continue
        out[uid] = out.get(uid, 0) + int(qty or 0)
    return out, unknown


def field_for_level(level: int) -> int:
    """The village's field, as the reports drew it: three front slots up to level 9, five from 10 on."""
    return 1 if int(level or 0) < 10 else 5


def _unit_cost(uid: int) -> float:
    """What losing one unit costs, roughly: its upkeep weighs its price and its training time."""
    from apps.espionage.services.unit_stats import UNIT_STATS

    unit = UNIT_STATS.get(int(uid)) or {}
    return float(unit.get("upkeep") or 1) * float(unit.get("size") or 1)


def _fill(line: str, uid: int, field_level: int, waves: int) -> int:
    from apps.espionage.services.battle_land import _line_capacity, _unit_size

    return max(1, (_line_capacity(field_level, line) // max(1, _unit_size(uid)))) * waves


def _key(sim: dict, army: dict[int, int]) -> tuple:
    morale_end = int((sim.get("morale") or {}).get("attacker") or 0)
    lost = sum(_unit_cost(u) * q for u, q in (sim.get("attacker_losses") or {}).items())
    return (0 if sim.get("winner") == "attacker" else 1, round(lost, 2), int(sim.get("rounds") or 99),
            -morale_end, sum(army.values()))


def _candidates(available: dict[int, int], field_level: int, defender_total: int):
    from apps.espionage.services.battle_land import OUTNUMBERED_BY

    per_line = {}
    for line, units in LINE_UNITS.items():
        options = [None]
        for uid in units:
            have = int(available.get(uid, 0) or 0)
            if have <= 0:
                continue
            for waves in (1, 2):
                options.append((uid, min(have, _fill(line, uid, field_level, waves))))
        per_line[line] = list(dict.fromkeys(options))
    cooks = [c for c in COOK_OPTIONS if c <= int(available.get(COOK, 0) or 0)]
    for combo in itertools.product(*per_line.values()):
        army: dict[int, int] = {}
        for item in combo:
            if item:
                army[item[0]] = army.get(item[0], 0) + item[1]
        if not any(u in army for u in LINE_UNITS["principal"] + LINE_UNITS["flancos"]):
            continue
        for c in cooks:
            out = dict(army)
            if c:
                out[COOK] = c
            # enough in the reserve not to look outnumbered (the morale loses 5 every round for it)
            need = math.ceil(defender_total / OUTNUMBERED_BY) + 1 - sum(out.values())
            if need > 0:
                for uid in (315, 303, 302, 301):
                    free = int(available.get(uid, 0) or 0) - out.get(uid, 0)
                    take = min(free, need)
                    if take > 0:
                        out[uid] = out.get(uid, 0) + take
                        need -= take
                    if need <= 0:
                        break
            yield out


def plan(available: dict[int, int], defenders: dict[int, int], *, wall_level: int = 0, field_level: int = 5,
         upgrades: dict[int, dict] | None = None, explain_training: bool = True) -> dict:
    """The best army for this village from what the town has, with the simulation that chose it."""
    from apps.espionage.services.battle_land import simulate_land_battle

    available = {int(u): int(q) for u, q in (available or {}).items() if int(q or 0) > 0}
    defenders = {int(u): int(q) for u, q in (defenders or {}).items() if int(q or 0) > 0}
    if not defenders:
        # a village always has defenders: without them there is nothing to plan against
        return {"can_win": False, "army": {}, "tried": 0, "note": "Sem os defensores da vila não há o que simular."}
    upgrades = {int(u): v for u, v in (upgrades or {}).items()}

    def fight(army):
        return simulate_land_battle(dict(army), dict(defenders), attacker_upgrades=upgrades, town_hall_level=field_level,
                                    wall_level=wall_level, max_rounds=12)

    best = None
    tried = 0
    for army in _candidates(available, field_level, sum(defenders.values())):
        sim = fight(army)
        tried += 1
        key = _key(sim, army)
        if best is None or key < best[0]:
            best = (key, army, sim)
    if best is None:
        return {"can_win": False, "army": {}, "tried": 0, "note": "A cidade não tem tropas para a linha de frente."}
    key, army, sim = best
    result = {
        "can_win": sim.get("winner") == "attacker",
        "army": army,
        "tried": tried,
        "prediction": _summary(sim),
    }
    if result["can_win"] and int((sim.get("morale") or {}).get("attacker") or 0) < MORALE_FLOOR:
        result["note"] = f"Vence, mas a moral termina em {(sim.get('morale') or {}).get('attacker')}%: perto de fugir."
    if explain_training:
        result["training"] = _what_training_gains(available, defenders, wall_level, field_level, upgrades, key, army)
    return result


def _summary(sim: dict) -> dict:
    return {
        "winner": sim.get("winner"), "rounds": sim.get("rounds"),
        "morale_end": (sim.get("morale") or {}).get("attacker"),
        "attacker_losses": {int(u): int(q) for u, q in (sim.get("attacker_losses") or {}).items()},
        "wall_sections_left": sim.get("wall_sections_left"), "wall_sections": sim.get("wall_sections"),
    }


def _what_training_gains(available, defenders, wall_level, field_level, upgrades, best_key, best_army) -> list[dict]:
    """For each unit that can hold a line: the best army with that line full of it (one or two waves). When that is
    better than what the town can send today, how many more of it the town would need."""
    from apps.espionage.services.battle_land import simulate_land_battle

    out = []
    for uid in TRAINABLE:
        line = next((ln for ln, units in LINE_UNITS.items() if uid in units), None)
        if uid == COOK:
            options = [{**best_army, COOK: c} for c in (10, 20, 30) if c > best_army.get(COOK, 0)]
        elif line:
            base = {u: q for u, q in best_army.items() if u not in LINE_UNITS[line]}
            options = [{**base, uid: _fill(line, uid, field_level, w)} for w in (1, 2)]
        else:
            continue
        for army in options:
            need = army.get(uid, 0) - int(available.get(uid, 0) or 0)
            if need <= 0:
                continue
            sim = simulate_land_battle(dict(army), dict(defenders), attacker_upgrades=upgrades, town_hall_level=field_level,
                                       wall_level=wall_level, max_rounds=12)
            key = _key(sim, army)
            if key[:4] < best_key[:4]:
                out.append({"unit": uid, "more": need, **_summary(sim)})
                break
    return out


def battle_state(round_html: str) -> dict | None:
    """Where a battle stands at the end of its last round, read from the round capture: who is alive on each side,
    the attacker's morale, the wall sections still up, and per unit of the attacker its ammunition and hit points left."""
    from .replay import _end
    from .report_parser import parse_rounds

    rounds = parse_rounds(round_html or "")
    if not rounds:
        return None
    last = rounds[-1]
    attacker = _end(last["attacker"])
    defender = {u: q for u, q in _end(last["defender"]).items() if u != 314 and q > 0}
    wall = last.get("wall") or {}
    standing = int(wall.get("standing") or 0) if wall else 0
    try:
        morale = int(((last.get("morale") or {}).get("attacker") or [{}])[0].get("morale"))
    except (TypeError, ValueError, IndexError):
        morale = None
    ammo: dict[int, list] = {}
    hp: dict[int, list] = {}
    for slots in (last["attacker"].get("zones") or {}).values():
        for slot in slots or []:
            if not slot or not slot.get("left"):
                continue
            if slot.get("ammo") is not None:
                ammo.setdefault(slot["unit"], []).append(int(slot["ammo"]))
            if slot.get("hp") is not None:
                hp.setdefault(slot["unit"], []).append(int(slot["hp"]))
    return {
        "round": int(last.get("round") or 0), "attacker": {u: q for u, q in attacker.items() if q > 0}, "defender": defender,
        "wall_standing": standing, "wall_level": int(wall.get("level") or 0) if wall else 0, "morale": morale,
        "ammo_pct": {u: sum(v) / len(v) for u, v in ammo.items()}, "hp_pct": {u: sum(v) / len(v) for u, v in hp.items()},
    }


def reinforce(state: dict, available: dict[int, int], *, wall_level: int = 0, field_level: int = 5,
              upgrades: dict[int, dict] | None = None) -> dict:
    """The battle taken up from its real state, without and with each army the town could still send (joining the
    next round). Returns the best one when it is better than sending nothing: winning instead of losing, or fewer
    losses, or fewer rounds."""
    from apps.espionage.services.battle_land import simulate_land_battle

    available = {int(u): int(q) for u, q in (available or {}).items() if int(q or 0) > 0}
    upgrades = {int(u): v for u, v in (upgrades or {}).items()}
    attacker = {int(u): int(q) for u, q in (state.get("attacker") or {}).items()}
    defender = {int(u): int(q) for u, q in (state.get("defender") or {}).items()}
    if not attacker or not (defender or state.get("wall_standing")):
        return {"send": {}, "note": "A batalha já está decidida."}
    wall = int(state.get("wall_level") or wall_level or 0)

    def fight(extra):
        return simulate_land_battle(
            dict(attacker), dict(defender), attacker_upgrades=upgrades, town_hall_level=field_level, wall_level=wall,
            max_rounds=12, attacker_start_morale=state.get("morale"), wall_sections_standing=state.get("wall_standing"),
            attacker_ammo_pct=state.get("ammo_pct") or None, attacker_hp_pct=state.get("hp_pct") or None,
            attacker_reinforcements_by_round={2: dict(extra)} if extra else None)

    without = fight({})
    best = (_key(without, {}), {}, without)
    for army in _candidates(available, field_level, sum(defender.values())):
        sim = fight(army)
        key = _key(sim, army)
        if key < best[0]:
            best = (key, army, sim)
    key, army, sim = best
    return {"send": army, "with": _summary(sim), "without": _summary(without), "state_round": state.get("round")}
