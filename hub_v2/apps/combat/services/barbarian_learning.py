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
MAX_SAMPLES = 80                # the overall adjustment (kept for the history)
ENGINE = "n197"                 # the simulator's rules the adjustments were learned on (N-197 changed them)
MAX_PER_BAND = 150              # N-197: battles per kind of village; one kind never pushes the other out
LEARN_EVERY_SECONDS = 1800      # N-197: at most one search each half hour (each battle used to start one)
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


VIKING = 316
CLUB_MAN = 320
BANDS = ("club", "viking")
UNIT_BAND = {320: "club", 316: "viking", 321: "viking"}     # N-197: the knife men come with the vikings


def _counts(defender_units) -> dict[int, int]:
    return {int(u): int(q or 0) for u, q in (defender_units or {}).items() if str(u).lstrip("-").isdigit() and int(q or 0) > 0}


def pure_band(defender_units) -> str | None:
    """N-197: "club" or "viking" when the village has only one of them; None for a village with both (it is not used to
    learn a kind, only to check the result)."""
    units = _counts(defender_units)
    has_club = any(UNIT_BAND.get(u) == "club" for u in units)
    has_viking = any(UNIT_BAND.get(u) == "viking" for u in units)
    if has_club and has_viking:
        return None
    return "viking" if has_viking else "club"


def majority_band(defender_units) -> str:
    units = _counts(defender_units)
    club = sum(q for u, q in units.items() if UNIT_BAND.get(u) == "club")
    viking = sum(q for u, q in units.items() if UNIT_BAND.get(u) == "viking")
    return "viking" if viking > club else "club"


def unit_adjustment(defender_units) -> tuple[dict[int, tuple[float, float]], float | None]:
    """N-197: {barbarian unit: (our damage on it, its own damage), in percent} with what was learned for its kind, and
    the wall's armour of the village's main kind. A village with club men and vikings gets each one's own (one number
    for the whole village was what the simulator misjudged there)."""
    bands = active().get("bands") or {}
    main = majority_band(defender_units)
    out: dict[int, tuple[float, float]] = {}
    for uid in _counts(defender_units):
        band = bands.get(UNIT_BAND.get(uid, main)) or {}
        if band.get("attacker_pct") is not None and band.get("engine") == ENGINE:
            out[uid] = (float(band.get("attacker_pct") or 0), float(band.get("defender_pct") or 0))
    main_band = bands.get(main) or {}
    wall = main_band.get("wall_armor_pct") if main_band.get("engine") == ENGINE else None
    return out, (float(wall) if wall is not None else None)


def band_of(defender_units) -> str:
    """N-194 (17/11): the kind of village: club men and rams (levels 1-19) or with vikings (20 on). One adjustment did
    not fit both: against the club men our troops hit about half of what the vikings' adjustment says."""
    units = {int(u) for u, q in (defender_units or {}).items() if str(u).lstrip("-").isdigit() and int(q or 0) > 0}
    return "viking" if VIKING in units else "club"


def _band_value(defender_units=None) -> dict:
    value = active()
    if defender_units is not None:
        band = (value.get("bands") or {}).get(band_of(defender_units))
        if isinstance(band, dict) and band.get("attacker_pct") is not None:
            return band
    return value


def active_bonus(defender_units=None) -> tuple[float, float]:
    value = _band_value(defender_units)
    return float(value.get("attacker_pct") or 0), float(value.get("defender_pct") or 0)


def active_wall_armor(defender_units=None) -> float | None:
    value = _band_value(defender_units).get("wall_armor_pct")
    return float(value) if value is not None else None


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
    per_band: dict[str, int] = {}
    checks = (SimulationCheck.objects.filter(report__isnull=False).select_related("report")
              .order_by("-created_at")[:MAX_PER_BAND * 8])
    for check in checks:
        inputs = check.inputs_json or {}
        if inputs.get("naval") or not is_barbarian_village(inputs.get("defender")):
            continue
        kind = pure_band(inputs.get("defender")) or "mixed"
        if per_band.get(kind, 0) >= MAX_PER_BAND:
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
        per_band[kind] = per_band.get(kind, 0) + 1
        if all(per_band.get(k, 0) >= MAX_PER_BAND for k in ("club", "viking", "mixed")):
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


LOSS_FLOOR = 20               # losses are measured against at least this many units: a battle without losses foreseen
                              # with one is not "everything wrong" (it pulled the barbarians' damage down, N-182)


def loss_error(real: dict, predicted: dict) -> float:
    """How far the predicted losses of a side are from the real ones, 0 (the same) to 1 (nothing alike)."""
    units = set(real) | set(predicted)
    if not units:
        return 0.0
    gap = sum(abs(int(real.get(unit, 0)) - int(predicted.get(unit, 0))) for unit in units)
    scale = max(sum(int(v) for v in real.values()), sum(int(v) for v in predicted.values()), LOSS_FLOOR)
    return round(min(1.0, gap / scale), 4)


def sample_cost(sample: dict, result: dict) -> dict:
    """How far one battle fought again is from the real one; lower is better."""
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
    # the attacker's losses weigh three times: they are what a farm has to train again
    cost = (2 * winner_wrong + abs(rounds_gap) + morale_gap / 10 + wall_gap + wall_hp_gap / 25 + hp_gap / 25
            + 3 * attacker_error + defender_error)
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
    village's wall), for all of them and (N-194) for each kind of village; put it into use when it is better than the
    one in use."""
    with _LOCK:
        everything = samples()
        # N-197: each kind is learned from the villages that have only it and used unit by unit (a village with both
        # takes each one's own); the one number for every village is no longer used against them
        result = {"better": False, "samples": len(everything)}
        bands, changed = {}, False
        for band in BANDS:
            subset = [s for s in everything if pure_band((s.get("inputs") or {}).get("defender")) == band]
            if not subset:
                continue
            had = ((active().get("bands") or {}).get(band)) or {}
            if had.get("engine") != ENGINE:
                had = {}                        # learned on the simulator before N-197: it does not hold any more
            found = _learn(subset, had or active(), save=False)
            keep = found if (found["better"] or not had) else {**had, "samples": len(subset)}
            changed = changed or found["better"] or not had
            bands[band] = {**{k: keep.get(k) for k in ("samples", "attacker_pct", "defender_pct", "wall_armor_pct", "before",
                                                        "after", "computed_at")}, "engine": ENGINE}
        if save and (result["better"] or changed):
            from apps.settings_app.utils import set_setting

            current = active()
            base = result if result["better"] else {**current, "samples": len(everything)}
            history = list(current.get("history") or [])[-9:]
            if current:
                history.append({k: current.get(k) for k in ("attacker_pct", "defender_pct", "wall_armor_pct", "samples", "computed_at")})
            set_setting(SETTING_KEY, json.dumps({**{k: v for k, v in base.items() if k not in ("bands", "history")},
                                                 "bands": bands, "history": history}, ensure_ascii=False))
            _CACHE.update({"at": 0.0, "value": None})
            logger.info("Barbarian calibration updated: %s", {b: (v["attacker_pct"], v["defender_pct"]) for b, v in bands.items()})
        result["bands"] = bands
        return result


def _learn(sample_list: list[dict], current: dict, *, save: bool = False) -> dict:
    """The search itself, over these samples, from the adjustment `current`."""
    if True:
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
            # then walk from the best: small steps of each side's damage and of the wall's armour while it gets better
            for _ in range(8):
                centre = best[1]
                for step_a in FINE:
                    for step_d in FINE:
                        consider(centre[0] + step_a, centre[1] + step_d, centre[2])
                for step_w in (-15.0, -5.0, 5.0, 15.0):
                    if 20.0 <= centre[2] + step_w <= 130.0:
                        consider(centre[0], centre[1], centre[2] + step_w)
                if best[1] == centre:
                    break
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
    """After a new barbarian battle is checked: learn without holding up whoever brought the report (N-197: at most
    once each LEARN_EVERY_SECONDS, there are several battles an hour and each search fights hundreds again)."""
    try:
        from django.core.cache import cache

        if not cache.add("barbarian-learning-recent", 1, LEARN_EVERY_SECONDS):
            return
    except Exception:  # noqa: BLE001 - without the cache it learns every time, as before
        pass

    def run() -> None:
        from django.db import connection

        try:
            learn()
        except Exception:  # noqa: BLE001 - learning never stands in the way of keeping a report
            logger.warning("Could not learn from the barbarian battles", exc_info=True)
        finally:
            connection.close()

    threading.Thread(target=run, name="barbarian-learning", daemon=True).start()
