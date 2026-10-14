"""Checking the simulator against the combats the hub keeps (N-110).

Every land combat that is over is fought again in the simulator, with what the report tells
and, for what it does not tell (the level of the wall, the upgrades of the enemy), the same
guesses the recommendation makes today. The prediction is stored beside what happened.
"""
from __future__ import annotations

import logging

from .replay import battle_inputs, compare, real_outcome, simulable, simulate
from .report_parser import parse_rounds, parse_summary

logger = logging.getLogger(__name__)

UNKNOWN_WALL_LEVEL = 15         # what the simulator assumes when nothing is known about the city


def _own_side(report, rounds: list) -> str:
    own = (getattr(report.game_account, "name", "") or "").strip().lower()
    if not own or not rounds:
        return ""
    for side in ("attacker", "defender"):
        names = (rounds[0][side].get("name") or "").lower()
        if any(part.strip().split("[")[0] == own for part in names.split(",")):
            return side
    return ""


def assumptions(report, rounds: list, inputs: dict) -> dict:
    """What the report does not tell and had to be taken from elsewhere, or guessed."""
    from .intel import side_upgrades, wall_level

    found = {"wall_level": 0, "wall_source": "", "own_side": _own_side(report, rounds), "own_upgrades": {}}
    # N-112: the report shows the upgrades of every army in it
    shown = {side: side_upgrades(rounds, side) for side in ("attacker", "defender")}
    if shown["attacker"] or shown["defender"]:
        found["upgrades"] = {side: {str(unit): levels for unit, levels in units.items()} for side, units in shown.items()}
    if inputs.get("wall_segments"):
        found["wall_level"], found["wall_source"] = UNKNOWN_WALL_LEVEL, "padrão"
        level = wall_level(rounds)
        if level:
            found["wall_level"], found["wall_source"] = level, "relatório"
        elif report.target_city_id and report.game_account_id:
            try:
                from apps.worldintel.models import WorldDumpCity

                city = (WorldDumpCity.objects
                        .filter(game_city_id=report.target_city_id, dump__game_account__server_id=report.game_account.server_id)
                        .order_by("-dump__captured_at").first())
                if city and city.level:
                    found["wall_level"], found["wall_source"] = max(1, int(int(city.level) * 0.8)), f"nível da cidade ({city.level})"
            except Exception:  # noqa: BLE001 - the guess is optional
                logger.debug("No city level for the combat %s", report.combat_id, exc_info=True)
    if found["own_side"] and report.game_account_id:
        try:
            snapshot = report.game_account.snapshot
            upgrades = (snapshot.base_snapshot or {}).get("unit_improvements") or {}
            found["own_upgrades"] = {str(unit): value for unit, value in upgrades.items() if str(unit).isdigit() and isinstance(value, dict)}
        except Exception:  # noqa: BLE001 - an account without snapshot has no upgrades to give
            pass
    return found


def simulate_check(inputs: dict, guess: dict, *, attacker_pct: float = 0.0, defender_pct: float = 0.0, real_rounds: int = 0) -> dict:
    """The simulator on the inputs of a check, with the same guesses, and a damage adjustment on top.

    Many real combats stop before one side is gone (an army recalled, a retreat, a log saved
    midway), and the simulator fights to the end. So the winner and the number of rounds come
    from the whole simulated combat, and the losses from the same number of rounds the real one
    lasted (`real_rounds`): that is the only fair thing to put beside the real losses.
    """
    upgrades = {int(unit): value for unit, value in (guess.get("own_upgrades") or {}).items()}
    sides = {"attacker": upgrades if guess.get("own_side") == "attacker" else None, "defender": upgrades if guess.get("own_side") == "defender" else None}
    for side, units in (guess.get("upgrades") or {}).items():
        if units:           # what the report itself shows, when it does (N-112)
            sides[side] = {int(unit): value for unit, value in units.items()}
    options = {
        "wall_level": int(guess.get("wall_level") or 0),
        "attacker_upgrades": sides["attacker"],
        "defender_upgrades": sides["defender"],
        "attacker_damage_bonus_pct": attacker_pct,
        "defender_damage_bonus_pct": defender_pct,
    }
    whole = simulate(normalize(inputs), **options)
    if real_rounds and whole["rounds"] > real_rounds:
        whole["losses"] = simulate(normalize(inputs), max_rounds=int(real_rounds), **options)["losses"]
    return whole


def normalize(inputs: dict) -> dict:
    """Inputs as the simulator wants them: stored JSON turns the unit ids into text."""
    units = lambda raw: {int(unit): int(amount) for unit, amount in (raw or {}).items()}   # noqa: E731
    return {
        **({"naval": True} if inputs.get("naval") else {}),
        "field_level": int(inputs.get("field_level") or 1),
        "wall_segments": int(inputs.get("wall_segments") or 0),
        "attacker": units(inputs.get("attacker")),
        "defender": units(inputs.get("defender")),
        "reinforcements": {
            side: {int(number): units(joined) for number, joined in ((inputs.get("reinforcements") or {}).get(side) or {}).items()}
            for side in ("attacker", "defender")
        },
    }


def check_report(report):
    """Fight a finished report again and store the comparison. Returns the check, or None when there is nothing to compare."""
    from apps.combat.models import SimulationCheck

    if not report.finished:
        return None
    rounds = parse_rounds(report.detailed_html)
    if not rounds:
        return None
    inputs = battle_inputs(rounds)
    if not simulable(inputs):
        return None
    guess = assumptions(report, rounds, inputs)
    real = real_outcome(rounds, parse_summary(report.summary_html))
    predicted = simulate_check(inputs, guess, real_rounds=real["rounds"])
    score = compare(real, predicted)
    check, _created = SimulationCheck.objects.update_or_create(
        report=report,
        defaults={
            "inputs_json": {**inputs, "assumptions": guess},
            "real_json": real,
            "predicted_json": predicted,
            "winner_ok": score["winner_ok"],
            "rounds_gap": score["rounds_gap"],
            "attacker_error": score["attacker_error"],
            "defender_error": score["defender_error"],
        },
    )
    return check


def add_sample(*, source: str, external_id: str, title: str, fought_at: str, inputs: dict, real: dict, wall_level: int | None = None,
               upgrades: dict | None = None):
    """A combat read from outside the hub (a public battle log), kept as a check like the others.

    `inputs` and `real` come in the shape replay builds them. Returns the check, or None when the
    simulator has no numbers for some unit of it.
    """
    from apps.combat.models import SimulationCheck

    inputs = normalize(inputs)
    if not simulable(inputs):
        return None
    guess = {"wall_level": 0, "wall_source": "", "own_side": "", "own_upgrades": {}}
    if inputs.get("wall_segments"):
        guess["wall_level"], guess["wall_source"] = (int(wall_level), "relatório") if wall_level else (UNKNOWN_WALL_LEVEL, "padrão")
    if upgrades:
        guess["upgrades"] = {
            side: {str(unit): {"offensive": int(levels[0]), "defensive": int(levels[1])} for unit, levels in (upgrades.get(side) or {}).items()}
            for side in ("attacker", "defender")
        }
    real = {
        "winner": real.get("winner") or "", "rounds": int(real.get("rounds") or 0), "fled": real.get("fled") or "",
        "losses": {side: {int(unit): int(amount) for unit, amount in ((real.get("losses") or {}).get(side) or {}).items()} for side in ("attacker", "defender")},
    }
    predicted = simulate_check(inputs, guess, real_rounds=real["rounds"])
    score = compare(real, predicted)
    check, _created = SimulationCheck.objects.update_or_create(
        source=source, external_id=external_id,
        defaults={
            "report": None, "title": title[:200], "fought_at": fought_at[:32],
            "inputs_json": {**inputs, "assumptions": guess}, "real_json": real, "predicted_json": predicted,
            "winner_ok": score["winner_ok"], "rounds_gap": score["rounds_gap"],
            "attacker_error": score["attacker_error"], "defender_error": score["defender_error"],
        },
    )
    return check


def recheck_sample(check):
    """A combat read from outside, fought again with the simulator as it is now (its inputs are kept)."""
    inputs = normalize(check.inputs_json)
    guess = (check.inputs_json or {}).get("assumptions") or {}
    # stored JSON turns the unit ids into text; the losses are compared by unit, so they go back to numbers
    real = {
        **(check.real_json or {}),
        "losses": {side: {int(unit): int(amount) for unit, amount in (((check.real_json or {}).get("losses") or {}).get(side) or {}).items()}
                   for side in ("attacker", "defender")},
    }
    predicted = simulate_check(inputs, guess, real_rounds=int(real.get("rounds") or 0))
    score = compare(real, predicted)
    check.predicted_json = predicted
    check.winner_ok, check.rounds_gap = score["winner_ok"], score["rounds_gap"]
    check.attacker_error, check.defender_error = score["attacker_error"], score["defender_error"]
    check.save(update_fields=["predicted_json", "winner_ok", "rounds_gap", "attacker_error", "defender_error", "updated_at"])
    return check


def check_safely(report) -> None:
    try:
        check_report(report)
    except Exception:  # noqa: BLE001 - the check never stands in the way of keeping or telling a report
        logger.warning("Could not check the simulator against the combat %s", report.combat_id, exc_info=True)


KINDS = (("wall", "Só muralha"), ("mixed", "Muralha e defensores"), ("open", "Campo aberto"), ("sea", "No mar"))


def kind_of(check) -> str:
    """What the attacker met: only the wall, the wall with troops behind it, troops in the open, or a fleet."""
    inputs = check.inputs_json or {}
    if inputs.get("naval"):
        return "sea"
    if inputs.get("wall_segments"):
        return "mixed" if inputs.get("defender") else "wall"
    return "open"


def by_kind(checks) -> list:
    """The score of each kind of combat, to see where the simulator is furthest from the game."""
    rows = list(checks)
    groups = []
    for key, label in KINDS:
        members = [row for row in rows if kind_of(row) == key]
        if members:
            groups.append({"key": key, "label": label, **scoreboard(members)})
    return groups


def scoreboard(checks=None) -> dict:
    """How the simulator did over the checks: winner right, how far the losses and the rounds were."""
    from apps.combat.models import SimulationCheck

    rows = list(checks if checks is not None else SimulationCheck.objects.all())
    total = len(rows)
    if not total:
        return {"total": 0, "winner_pct": 0, "winner_known": 0, "attacker_hit": 0, "defender_hit": 0, "rounds_ok": 0, "own": 0}
    # a report that names no winner does not count for or against the winner score
    decided = [row for row in rows if (row.real_json or {}).get("winner")]
    return {
        "total": total,
        "own": sum(1 for row in rows if row.source == "jogo"),
        "winner_known": len(decided),
        "winner_pct": round(100 * sum(1 for row in decided if row.winner_ok) / len(decided)) if decided else 0,
        # "acerto" reads better than an error: 100% = the same losses
        "attacker_hit": round(100 * (1 - sum(row.attacker_error for row in rows) / total)),
        "defender_hit": round(100 * (1 - sum(row.defender_error for row in rows) / total)),
        "rounds_ok": round(100 * sum(1 for row in rows if row.rounds_gap == 0) / total),
    }
