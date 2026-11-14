"""The simulator learns the barbarian village by itself (N-182).

Every battle against a barbarian village the hub keeps is fought again in the simulator, round
by round. The two numbers that scale the damage of each side are searched so that the number of
rounds, the attacker's morale in each round, the wall after the first round, the winner and the
losses come closest to what happened. The round capture tells everything that went in: the
units in each slot, the upgrades of each side, the wall, the morale and its reasons.

This adjustment is kept apart from the general one (N-110, approved on the screen): it is only
used against barbarian villages, and it is put into use by itself whenever it fits every
barbarian battle better than the one in use. Each change is stored with the score before and
after.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from django.utils import timezone

logger = logging.getLogger(__name__)

SETTING_KEY = "simulator_barbarian_calibration"
COARSE = tuple(range(-60, 61, 20))
FINE = (-10, -5, 0, 5, 10)
LIMITS = (-80, 80)
MAX_SAMPLES = 80
CACHE_SECONDS = 60
_CACHE = {"at": 0.0, "value": None}
_LOCK = threading.Lock()


def is_barbarian_village(defender_units) -> bool:
    """The defenders are the barbarians of a village (their units, their wall), no player in it."""
    from apps.espionage.services.battle_land import is_barbarian_village as _is

    return _is(defender_units)


def active() -> dict:
    """The adjustment in use against barbarian villages ({} when none was learned yet)."""
    if _CACHE["value"] is not None and time.monotonic() - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["value"]
    value: dict = {}
    try:
        from apps.settings_app.utils import get_setting

        value = json.loads(get_setting(SETTING_KEY, "") or "{}") or {}
    except Exception:  # noqa: BLE001 - without the setting the simulator works as it always did
        logger.debug("No barbarian calibration available", exc_info=True)
    _CACHE.update({"at": time.monotonic(), "value": value})
    return value


def active_bonus() -> tuple[float, float]:
    value = active()
    return float(value.get("attacker_pct") or 0), float(value.get("defender_pct") or 0)


def _real_rounds(rounds: list) -> dict:
    """Per round, what the capture shows: the attacker's morale and the wall still standing."""
    morale, wall, wall_hp, hp = [], [], [], []
    for rd in rounds:
        try:
            morale.append(int(((rd.get("morale") or {}).get("attacker") or [{}])[0].get("morale")))
        except (TypeError, ValueError, IndexError):
            morale.append(None)
        wall.append(int((rd.get("wall") or {}).get("standing") or 0) if rd.get("wall") else None)
        sections = (rd.get("wall") or {}).get("hp") if rd.get("wall") else None
        wall_hp.append(sum(int(x or 0) for x in sections) / len(sections) if sections else None)
        units: dict[int, list] = {}
        for slots in ((rd.get("attacker") or {}).get("zones") or {}).values():
            for slot in slots or []:
                if slot and slot.get("left") and slot.get("hp") is not None:
                    units.setdefault(int(slot["unit"]), []).append(int(slot["hp"]))
        hp.append({u: sum(v) / len(v) for u, v in units.items()})
    return {"morale": morale, "wall": wall, "wall_hp": wall_hp, "hp": hp}


def samples() -> list[dict]:
    """The barbarian battles the hub keeps, ready to be fought again."""
    from apps.combat.models import SimulationCheck

    from .checks import normalize as _normalize
    from .report_parser import parse_rounds

    out = []
    checks = (SimulationCheck.objects.filter(report__isnull=False).select_related("report")
              .order_by("-created_at")[:MAX_SAMPLES * 3])
    for check in checks:
        inputs = check.inputs_json or {}
        if inputs.get("naval") or not is_barbarian_village(inputs.get("defender")):
            continue
        rounds = parse_rounds(check.report.detailed_html or "")
        if not rounds:
            continue
        guess = inputs.get("assumptions") or {}
        upgrades = (guess.get("upgrades") or {})
        out.append({
            "id": check.pk,
            "inputs": _normalize(inputs),
            "wall_level": int(guess.get("wall_level") or 0),
            "attacker_upgrades": {int(u): v for u, v in (upgrades.get("attacker") or guess.get("own_upgrades") or {}).items()},
            "defender_upgrades": {int(u): v for u, v in (upgrades.get("defender") or {}).items()},
            "real": {**(check.real_json or {}), **_real_rounds(rounds)},
        })
        if len(out) >= MAX_SAMPLES:
            break
    out.extend(_ongoing_samples())
    return out


def _ongoing_samples() -> list[dict]:
    """Barbarian battles still being fought: the rounds already played teach the round-by-round numbers (morale, the
    wall's and the troops' hit points); who wins and in how many rounds is not known yet, so it does not count."""
    from apps.combat.models import CombatReport

    from .checks import assumptions, normalize
    from .replay import battle_inputs, simulable
    from .report_parser import parse_rounds

    out = []
    for report in (CombatReport.objects.filter(finished=False).exclude(detailed_html="").select_related("game_account")
                   .order_by("-updated_at")[:10]):
        rounds = parse_rounds(report.detailed_html or "")
        if not rounds:
            continue
        inputs = battle_inputs(rounds)
        if inputs.get("naval") or not is_barbarian_village(inputs.get("defender")) or not simulable(inputs):
            continue
        guess = assumptions(report, rounds, inputs)
        upgrades = guess.get("upgrades") or {}
        out.append({
            "id": f"r{report.pk}", "partial": True, "inputs": normalize(inputs), "wall_level": int(guess.get("wall_level") or 0),
            "attacker_upgrades": {int(u): v for u, v in (upgrades.get("attacker") or guess.get("own_upgrades") or {}).items()},
            "defender_upgrades": {int(u): v for u, v in (upgrades.get("defender") or {}).items()},
            "real": {"rounds": len(rounds), **_real_rounds(rounds)},
        })
    return out


def simulate_sample(sample: dict, attacker_pct: float, defender_pct: float, wall_armor_pct: float = 100.0) -> dict:
    from apps.espionage.services.battle_land import simulate_land_battle

    inputs = sample["inputs"]
    return simulate_land_battle(
        dict(inputs["attacker"]), dict(inputs["defender"]),
        attacker_upgrades=sample["attacker_upgrades"], defender_upgrades=sample["defender_upgrades"],
        town_hall_level=inputs["field_level"],
        wall_level=sample["wall_level"] if inputs.get("wall_segments") else 0,
        max_rounds=12, calibrated=False, wall_armor_pct=wall_armor_pct,
        attacker_damage_bonus_pct=attacker_pct, defender_damage_bonus_pct=defender_pct,
        attacker_reinforcements_by_round=(inputs.get("reinforcements") or {}).get("attacker") or None,
        defender_reinforcements_by_round=(inputs.get("reinforcements") or {}).get("defender") or None,
    )


def _sim_rounds(result: dict) -> dict:
    morale, wall, wall_hp, hp = [], [], [], []
    for rd in (result.get("details") or {}).get("rounds") or []:
        trace = ((rd.get("morale") or {}).get("attacker") or {})
        morale.append(trace.get("morale") if isinstance(trace, dict) else None)
        wall.append(rd.get("wall_sections"))
        sections = rd.get("wall_hp_pct")
        wall_hp.append(sum(sections) / len(sections) if sections else None)
        hp.append({int(u): v for u, v in (rd.get("attacker_hp_pct") or {}).items()})
    return {"morale": morale, "wall": wall, "wall_hp": wall_hp, "hp": hp}


def sample_cost(sample: dict, result: dict) -> dict:
    """How far one battle fought again is from the real one; lower is better."""
    from .replay import loss_error

    real = sample["real"]
    sim_rounds = _sim_rounds(result)
    winner_wrong = 1.0 if real.get("winner") and real["winner"] != result.get("winner") else 0.0
    rounds_gap = int(result.get("rounds") or 0) - int(real.get("rounds") or 0)
    pairs = [(r, s) for r, s in zip(real.get("morale") or [], sim_rounds["morale"]) if r is not None and s is not None]
    morale_gap = sum(abs(int(r) - int(s)) for r, s in pairs) / len(pairs) if pairs else 0.0
    wall_gap = 0.0
    if real.get("wall") and real["wall"][0] is not None and sim_rounds["wall"] and sim_rounds["wall"][0] is not None:
        wall_gap = abs(int(real["wall"][0]) - int(sim_rounds["wall"][0])) / 5.0
    wall_hp_pairs = [(r, s) for r, s in zip(real.get("wall_hp") or [], sim_rounds["wall_hp"]) if r is not None and s is not None]
    wall_hp_gap = sum(abs(r - s) for r, s in wall_hp_pairs) / len(wall_hp_pairs) if wall_hp_pairs else 0.0
    hp_gaps = []
    for r_units, s_units in zip(real.get("hp") or [], sim_rounds["hp"]):
        for uid, value in (r_units or {}).items():
            if uid in (s_units or {}):
                hp_gaps.append(abs(value - s_units[uid]))
    hp_gap = sum(hp_gaps) / len(hp_gaps) if hp_gaps else 0.0
    losses = real.get("losses") or {}
    attacker_error = loss_error({int(u): int(q) for u, q in (losses.get("attacker") or {}).items()},
                                {int(u): int(q) for u, q in (result.get("attacker_losses") or {}).items()})
    defender_error = loss_error({int(u): int(q) for u, q in (losses.get("defender") or {}).items()},
                                {int(u): int(q) for u, q in (result.get("defender_losses") or {}).items()})
    if sample.get("partial"):
        # a battle still being fought: only what its rounds already show
        winner_wrong, rounds_gap, attacker_error, defender_error = 0.0, 0, 0.0, 0.0
    cost = (2 * winner_wrong + abs(rounds_gap) + morale_gap / 10 + wall_gap + wall_hp_gap / 25 + hp_gap / 25
            + attacker_error + defender_error)
    return {"cost": cost, "winner_wrong": winner_wrong, "rounds_gap": rounds_gap, "morale_gap": round(morale_gap, 1),
            "wall_hp_gap": round(wall_hp_gap, 1), "hp_gap": round(hp_gap, 1),
            "wall_gap": wall_gap, "attacker_error": attacker_error, "defender_error": defender_error}


WALL_ARMOR_OPTIONS = (40.0, 55.0, 70.0, 85.0, 100.0)


def score(sample_list: list[dict], attacker_pct: float, defender_pct: float, wall_armor_pct: float = 100.0) -> dict:
    rows = [sample_cost(s, simulate_sample(s, attacker_pct, defender_pct, wall_armor_pct)) for s in sample_list]
    if not rows:
        return {"cost": 0.0, "samples": 0}
    n = len(rows)
    return {
        "cost": round(sum(r["cost"] for r in rows) / n, 4),
        "samples": n,
        "winner_ok": sum(1 for r in rows if not r["winner_wrong"]),
        "rounds_exact": sum(1 for r in rows if r["rounds_gap"] == 0),
        "rounds_gap_mean": round(sum(abs(r["rounds_gap"]) for r in rows) / n, 2),
        "morale_gap_mean": round(sum(r["morale_gap"] for r in rows) / n, 1),
        "wall_hp_gap_mean": round(sum(r["wall_hp_gap"] for r in rows) / n, 1),
        "hp_gap_mean": round(sum(r["hp_gap"] for r in rows) / n, 1),
        "attacker_error": round(sum(r["attacker_error"] for r in rows) / n, 3),
        "defender_error": round(sum(r["defender_error"] for r in rows) / n, 3),
    }


def learn(*, save: bool = True) -> dict:
    """Search the adjustment that fits the barbarian battles best (damage of each side against troops, armour of the
    village's wall); put it into use when it is better than the one in use."""
    with _LOCK:
        sample_list = samples()
        current = active()
        cur = (float(current.get("attacker_pct") or 0), float(current.get("defender_pct") or 0),
               float(current.get("wall_armor_pct") or 100))
        before = score(sample_list, *cur)
        best = (before["cost"], cur, before)
        tried = {cur}

        def consider(a: float, d: float, w: float) -> None:
            nonlocal best
            key = (a, d, w)
            if key in tried or not (LIMITS[0] <= a <= LIMITS[1] and LIMITS[0] <= d <= LIMITS[1]):
                return
            tried.add(key)
            board = score(sample_list, a, d, w)
            change = abs(a) + abs(d) + abs(100 - w) / 2
            best_change = abs(best[1][0]) + abs(best[1][1]) + abs(100 - best[1][2]) / 2
            if board["cost"] < best[0] - 1e-9 or (abs(board["cost"] - best[0]) <= 1e-9 and change < best_change):
                best = (board["cost"], key, board)

        if sample_list:
            for w in WALL_ARMOR_OPTIONS:
                for a in COARSE:
                    for d in COARSE:
                        consider(float(a), float(d), w)
            centre = best[1]
            for step_a in FINE:
                for step_d in FINE:
                    consider(centre[0] + step_a, centre[1] + step_d, centre[2])
        better = bool(sample_list) and best[0] < before["cost"] - 1e-9
        result = {"samples": len(sample_list), "attacker_pct": best[1][0], "defender_pct": best[1][1],
                  "wall_armor_pct": best[1][2], "before": before, "after": best[2], "better": better,
                  "computed_at": timezone.now().isoformat()}
        if save and better:
            from apps.settings_app.utils import set_setting

            history = list(current.get("history") or [])[-9:]
            if current:
                history.append({k: current.get(k) for k in ("attacker_pct", "defender_pct", "wall_armor_pct", "samples", "computed_at")})
            set_setting(SETTING_KEY, json.dumps({**result, "history": history}, ensure_ascii=False))
            _CACHE.update({"at": 0.0, "value": None})
            logger.info("Barbarian calibration updated: %s",
                        {k: result[k] for k in ("samples", "attacker_pct", "defender_pct", "wall_armor_pct")})
        return result


def learn_in_background() -> None:
    """After a new barbarian battle is checked: learn without holding up whoever brought the report."""
    def run() -> None:
        from django.db import connection

        try:
            learn()
        except Exception:  # noqa: BLE001 - learning never stands in the way of keeping a report
            logger.warning("Could not learn from the barbarian battles", exc_info=True)
        finally:
            connection.close()

    threading.Thread(target=run, name="barbarian-learning", daemon=True).start()
