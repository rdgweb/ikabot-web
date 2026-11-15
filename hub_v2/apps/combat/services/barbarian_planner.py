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
    """The village's field, as the reports drew it: three front slots up to level 9, five from 10 on, seven (three of
    artillery, two on each flank) from 30 on."""
    level = int(level or 0)
    if level < 10:
        return 1
    return 5 if level < 30 else 10


TAX_PER_CITIZEN = 3.0          # gold a citizen pays per hour (town hall: 2.299 citizens -> +6.899)
GROWTH_PER_HAPPINESS = 0.02    # citizens grown per hour for each point of happiness (town hall: 408 -> 8,19/h)
CITIZEN_VALUE = TAX_PER_CITIZEN / GROWTH_PER_HAPPINESS
# a citizen lost with each unit: each missing citizen adds one point of happiness, so it grows back at 2% an hour,
# ~50 h on average, without paying its tax all that time (150 gold); the town's own numbers replace these when known
EXPOSURE = 0.01                # what having a unit away costs, as a share of its price (it is not home to defend)
ROUND_MINUTES = 15
MAX_ROUNDS = 20                # a battle the simulator has not decided by then is taken as not won
SAME_ISLAND_SECONDS_AT_60 = 585   # town -> its own island's village at speed 60, as seen (9:45 for steam giants and mortars;
                                  # 6:30 for hoplites at 90; a bombardier at 30 doubles it)


def travel_minutes(army: dict) -> float:
    """Minutes the army takes from the town to its island's village: the slowest unit sets the pace."""
    from apps.espionage.services.unit_stats import UNIT_STATS

    speeds = [int((UNIT_STATS.get(int(u)) or {}).get("speed") or 60) for u, q in (army or {}).items() if int(q or 0) > 0]
    slowest = min(speeds) if speeds else 60
    return SAME_ISLAND_SECONDS_AT_60 * 60 / max(1, slowest) / 60


def citizen_value(population: dict | None = None) -> float:
    """What a citizen lost costs the town, in gold: its tax for as long as it takes to grow back, from the town hall
    (gold per citizen, growth per point of happiness) when the job read it."""
    pop = population or {}
    tax = TAX_PER_CITIZEN
    rate = GROWTH_PER_HAPPINESS
    try:
        if int(pop.get("citizens") or 0) > 0 and int(pop.get("citizens_gold") or 0) > 0:
            tax = float(pop["citizens_gold"]) / float(pop["citizens"])
        if float(pop.get("happiness") or 0) > 50 and float(pop.get("growth") or 0) > 0:
            rate = float(pop["growth"]) / float(pop["happiness"])
    except (TypeError, ValueError, ZeroDivisionError):
        return CITIZEN_VALUE
    if not (0.5 <= tax <= 20 and 0.005 <= rate <= 0.1):
        return CITIZEN_VALUE
    return round(tax / rate, 1)


CITIZENS_KEPT = 0.2            # a sustainable farm leaves this share of the town's free citizens untouched


def sustainable_citizens(population: dict | None, cycle_minutes: float | None) -> int | None:
    """How many citizens a cycle can take for ever: the town grows 2% of its happiness an hour, and each citizen
    missing adds a point of happiness; the farm can keep at most (1 - CITIZENS_KEPT) of the free citizens of a full
    town missing. None when the town hall was not read. (The agent's farm uses the same rule.)"""
    pop = population or {}
    if not pop.get("max") or not cycle_minutes:
        return None
    happiness, growth = float(pop.get("happiness") or 0), float(pop.get("growth") or 0)
    rate = growth / happiness if happiness > 50 and growth > 0 else GROWTH_PER_HAPPINESS
    room = max(0, int(pop.get("max") or 0) - int(pop.get("occupied") or 0))
    happiness_full = max(0.0, happiness - room)
    citizens_full = int(pop.get("citizens") or 0) + room
    return int(rate * (happiness_full + (1 - CITIZENS_KEPT) * citizens_full) * float(cycle_minutes) / 60)


def unit_citizens(uid: int) -> int:
    """Citizens one unit takes, as the town's barracks showed (1 when not read)."""
    try:
        return max(1, int((_FARM.get("unit_citizens") or {}).get(int(uid)) or 1))
    except (TypeError, ValueError):
        return 1


def unit_price(uid: int) -> float:
    """What training one unit again costs: its price (base, from the game) and its citizens."""
    from core.catalogs import TRAINING_UNITS

    citizens = unit_citizens(uid) * float(_FARM.get("citizen_value") or CITIZEN_VALUE)
    for unit in TRAINING_UNITS.get("troops", []):
        if int(unit["id"]) == int(uid):
            return float(sum(int(unit.get(k) or 0) for k in ("wood", "wine", "marble", "crystal", "sulfur"))) + citizens
    return 100.0


def _unit_cost(uid: int) -> float:
    return unit_price(uid)


def _fill(line: str, uid: int, field_level: int, waves: int) -> int:
    from apps.espionage.services.battle_land import _line_capacity, _unit_size

    return max(1, (_line_capacity(field_level, line) // max(1, _unit_size(uid)))) * waves


_FARM = {"train_seconds": {}, "cycle_minutes": 0.0, "citizen_value": CITIZEN_VALUE, "unit_citizens": {},
         "free_citizens": None}     # set by plan()/reinforce() for the call in course


def set_town(population: dict | None = None, unit_citizens_read: dict | None = None) -> None:
    """The town the army leaves from: what its citizens are worth and how many each unit takes."""
    pop = population or {}
    _FARM.update(citizen_value=citizen_value(pop),
                 unit_citizens={int(u): int(c) for u, c in (unit_citizens_read or {}).items() if str(u).isdigit() and int(c or 0) > 0},
                 free_citizens=int(pop["citizens"]) if str(pop.get("citizens") or "").isdigit() else None)


def _key(sim: dict, army: dict[int, int], round_value: float = 0.0) -> tuple:
    """For a farm, in resources: what has to be trained again, what each round of battle holds the next attack back
    (the village is rebuilt only after it), the time the barracks needs to train the losses again when it is longer
    than a cycle, and a little for every unit away from home. Winning comes first."""
    morale_end = int((sim.get("morale") or {}).get("attacker") or 0)
    losses = sim.get("attacker_losses") or {}
    lost = sum(unit_price(u) * q for u, q in losses.items())
    away = EXPOSURE * sum(unit_price(u) * q for u, q in (army or {}).items())
    risk = 0 if morale_end >= MORALE_FLOOR else (MORALE_FLOOR - morale_end) * 100
    free = _FARM.get("free_citizens")
    if free is not None:
        # more citizens lost than the town has free: the barracks cannot train them again until it grows them back
        short = sum(unit_citizens(u) * q for u, q in losses.items()) - int(free)
        if short > 0:
            risk += short * float(_FARM.get("citizen_value") or CITIZEN_VALUE)
    train_minutes = sum(float(_FARM["train_seconds"].get(int(u)) or 0) * q for u, q in losses.items()) / 60
    late = max(0.0, train_minutes - float(_FARM["cycle_minutes"] or 0)) if _FARM["cycle_minutes"] else 0.0
    # the way there holds the battle (and the village's rebuild) back like a round does
    late += travel_minutes(army) if not _FARM.get("joining") else 0.0
    cost = lost + int(sim.get("rounds") or 12) * round_value + away + risk + late * (round_value / ROUND_MINUTES)
    # a battle still open when the simulation stops is not won: the game goes on (the barbarians wait behind the wall)
    won = sim.get("winner") == "attacker" and not sim.get("undecided")
    return (0 if won else 1, round(cost, 1), -morale_end)


def round_value(loot: float, cycle_minutes: float) -> float:
    """What one more round of battle costs a farm: the share of a cycle's loot it delays."""
    if not loot or not cycle_minutes:
        return 0.0
    if float(cycle_minutes) < 60:
        # an attack called back and sent again is not a cycle (15/11: 18 minutes made each round worth 150 thousand)
        cycle_minutes = 180
    return float(loot) * ROUND_MINUTES / float(cycle_minutes)


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
            yield dict(out)
            # and the same with enough in the reserve not to look outnumbered (-5 of morale each round): the
            # simulator says whether that is worth it (the cooks may already cover it)
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


RESERVE_UNITS = (303, 302, 315, 301)   # cheap bodies that wait in the reserve and take the front's place as it falls
SEARCH_STEPS = 25                      # rounds of the walk from the best army of the grid


def _steps(field_level: int) -> dict[int, list[int]]:
    """How much of each unit one step of the walk adds or takes: half a wave and a whole wave of its line."""
    out: dict[int, list[int]] = {COOK: [5, 10]}
    for line, units in LINE_UNITS.items():
        for uid in units:
            wave = _fill(line, uid, field_level, 1)
            out[uid] = sorted({max(1, wave // 2), wave})
    return out


def _improve(best: tuple, available: dict[int, int], field_level: int, fight, per_round: float) -> tuple[tuple, int]:
    """From the best army of the grid, take or add half a wave or a wave of each unit (the reserve's hoplites,
    carabineers, mortars, cooks...) while the farm's cost falls. The grid fills each line with one unit and the reserve
    only to the count that does not look outnumbered; the best army is often between those (15/11, level 34: 233
    hoplites in the reserve and the carabineers together, which the grid never tried)."""
    steps = _steps(field_level)
    tried = 0
    for _ in range(SEARCH_STEPS):
        key, army, _sim = best
        moved = False
        for uid in sorted(set(army) | {u for u in steps if int(available.get(u, 0) or 0) > 0}):
            have = int(available.get(uid, 0) or 0)
            for step in steps.get(uid) or [max(1, have // 10)]:
                for sign in (1, -1):
                    qty = army.get(uid, 0) + sign * step
                    if qty < 0 or qty > have or qty == army.get(uid, 0):
                        continue
                    trial = {u: q for u, q in army.items() if u != uid}
                    if qty:
                        trial[uid] = qty
                    if not any(u in trial for u in LINE_UNITS["principal"] + LINE_UNITS["flancos"]):
                        continue
                    sim = fight(trial)
                    tried += 1
                    trial_key = _key(sim, trial, per_round)
                    if trial_key < best[0]:
                        best = (trial_key, trial, sim)
                        moved = True
        if not moved:
            break
    return best, tried


def plan(available: dict[int, int], defenders: dict[int, int], *, wall_level: int = 0, field_level: int = 5,
         upgrades: dict[int, dict] | None = None, explain_training: bool = True, per_round: float = 0.0,
         train_seconds: dict | None = None, cycle_minutes: float = 0.0, population: dict | None = None,
         unit_citizens_read: dict | None = None) -> dict:
    """The best army for this village from what the town has, with the simulation that chose it."""
    from apps.espionage.services.battle_land import simulate_land_battle

    available = {int(u): int(q) for u, q in (available or {}).items() if int(q or 0) > 0}
    defenders = {int(u): int(q) for u, q in (defenders or {}).items() if int(q or 0) > 0}
    if not defenders:
        # a village always has defenders: without them there is nothing to plan against
        return {"can_win": False, "army": {}, "tried": 0, "note": "Sem os defensores da vila não há o que simular."}
    upgrades = {int(u): v for u, v in (upgrades or {}).items()}
    _FARM.update(train_seconds={int(u): float(v) for u, v in (train_seconds or {}).items()}, cycle_minutes=float(cycle_minutes or 0),
                 joining=False)
    set_town(population, unit_citizens_read)

    def fight(army):
        return simulate_land_battle(dict(army), dict(defenders), attacker_upgrades=upgrades, town_hall_level=field_level,
                                    wall_level=wall_level, max_rounds=MAX_ROUNDS)

    best = None
    tried = 0
    for army in _candidates(available, field_level, sum(defenders.values())):
        sim = fight(army)
        tried += 1
        key = _key(sim, army, per_round)
        if best is None or key < best[0]:
            best = (key, army, sim)
    if best is None:
        return {"can_win": False, "army": {}, "tried": 0, "note": "A cidade não tem tropas para a linha de frente."}
    best, walked = _improve(best, available, field_level, fight, per_round)
    tried += walked
    key, army, sim = best
    result = {
        "can_win": sim.get("winner") == "attacker" and not sim.get("undecided"),
        "army": army,
        "tried": tried,
        "prediction": {**_summary(sim), "travel_minutes": round(travel_minutes(army), 1)},
    }
    if result["can_win"] and int((sim.get("morale") or {}).get("attacker") or 0) < MORALE_FLOOR:
        result["note"] = f"Vence, mas a moral termina em {(sim.get('morale') or {}).get('attacker')}%: perto de fugir."
    if explain_training:
        result["training"] = _what_training_gains(available, defenders, wall_level, field_level, upgrades, key, army, per_round)
    return result


def _summary(sim: dict) -> dict:
    lost = {int(u): int(q) for u, q in (sim.get("attacker_losses") or {}).items()}
    return {
        "winner": "undecided" if sim.get("undecided") else sim.get("winner"), "rounds": sim.get("rounds"), "losses_cost": round(sum(unit_price(u) * q for u, q in lost.items())),
        "citizens_lost": sum(unit_citizens(u) * q for u, q in lost.items()),
        "morale_end": (sim.get("morale") or {}).get("attacker"),
        "attacker_losses": {int(u): int(q) for u, q in (sim.get("attacker_losses") or {}).items()},
        "wall_sections_left": sim.get("wall_sections_left"), "wall_sections": sim.get("wall_sections"),
    }


def _what_training_gains(available, defenders, wall_level, field_level, upgrades, best_key, best_army, per_round=0.0) -> list[dict]:
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
                                       wall_level=wall_level, max_rounds=MAX_ROUNDS)
            key = _key(sim, army, per_round)
            if key[:2] < best_key[:2]:
                out.append({"unit": uid, "more": need, **_summary(sim)})
                break
    return out


def battle_state(round_html: str) -> dict | None:
    """Where a battle stands at the end of its last round, read from the round capture: who is alive on each side,
    the attacker's morale, the wall sections still up, and per unit of the attacker its ammunition and hit points left."""
    from .replay import _end, field_level
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
        "field_level": field_level(rounds[0]),          # the field the game drew for this battle
    }


MIN_GAIN = 0.30               # a reinforcement is sent only when it saves at least this share of what the battle costs:
                              # the army should leave nearly right, the reinforcement is for when the battle goes wrong


def reinforce(state: dict, available: dict[int, int], *, wall_level: int = 0, field_level: int = 5,
              upgrades: dict[int, dict] | None = None, en_route: dict[int, int] | None = None,
              per_round: float = 0.0, train_seconds: dict | None = None, cycle_minutes: float = 0.0,
              population: dict | None = None, unit_citizens_read: dict | None = None) -> dict:
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
    # a reinforcement joins a battle already going: its way there does not hold the battle back
    _FARM.update(train_seconds={int(u): float(v) for u, v in (train_seconds or {}).items()}, cycle_minutes=float(cycle_minutes or 0),
                 joining=True)
    set_town(population, unit_citizens_read)

    def fight(extra):
        return simulate_land_battle(
            dict(attacker), dict(defender), attacker_upgrades=upgrades, town_hall_level=field_level, wall_level=wall,
            max_rounds=MAX_ROUNDS, attacker_start_morale=state.get("morale"), wall_sections_standing=state.get("wall_standing"),
            attacker_ammo_pct=state.get("ammo_pct") or None, attacker_hp_pct=state.get("hp_pct") or None,
            attacker_reinforcements_by_round={2: joined(extra)} if joined(extra) else None)

    on_way = {int(u): int(q) for u, q in (en_route or {}).items() if int(q or 0) > 0}

    def joined(extra):
        out = dict(on_way)
        for u, q in (extra or {}).items():
            out[u] = out.get(u, 0) + q
        return out

    without = fight({})
    base_key = _key(without, on_way, per_round)
    best = (base_key, {}, without)
    for army in _candidates(available, field_level, sum(defender.values())):
        sim = fight(army)
        key = _key(sim, joined(army), per_round)
        if key < best[0]:
            best = (key, army, sim)
    key, army, sim = best
    # a reinforcement must turn a loss into a win, or save a clear share of what the battle costs; with one already on
    # its way, only turning a loss into a win
    turns_it = key[0] < base_key[0]
    saves = key[1] <= base_key[1] * (1 - MIN_GAIN) and not on_way
    if army and not (turns_it or saves):
        army, sim = {}, without
    return {"send": army, "with": _summary(sim), "without": _summary(without), "state_round": state.get("round"),
            "en_route": {str(u): q for u, q in on_way.items()}}
