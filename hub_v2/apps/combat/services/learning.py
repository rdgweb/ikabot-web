"""What the simulator learns from the checks (N-110).

The checks say how far the simulator was from each real combat. Here the two numbers that
scale the damage of each side are searched so that, over all the checks, the predicted losses
and the predicted winner come closest to what happened. The result is a proposal: it shows the
score before and after, and the simulator only starts using it when someone approves it on the
screen. Approval can be taken back.
"""
from __future__ import annotations

import logging
import time

from django.utils import timezone

from .checks import simulate_check
from .replay import compare

logger = logging.getLogger(__name__)

MIN_CHECKS = 8                              # fewer combats than this say too little to change what the simulator recommends
COARSE = tuple(range(-60, 61, 20))          # damage of a side, in percent of what the simulator uses today: first by 20...
FINE = (-10, 0, 10)                         # ...then by 10 around the best
LIMITS = (-60, 60)
MAX_CHECKS = 150                            # the newest ones; each one is simulated for every candidate
WINNER_WEIGHT = 1.0                         # a wrong winner costs as much as getting every loss of one side wrong
_CACHE = {"at": 0.0, "value": (0.0, 0.0)}
CACHE_SECONDS = 60


def _cost(checks: list, attacker_pct: float, defender_pct: float) -> tuple[float, dict]:
    """How wrong the simulator is over the checks with this adjustment (lower is better), and the score it gives."""
    winner_ok = winner_known = attacker_error = defender_error = 0.0
    for check in checks:
        guess = (check.inputs_json or {}).get("assumptions") or {}
        predicted = simulate_check(check.inputs_json, guess, attacker_pct=attacker_pct, defender_pct=defender_pct,
                                   real_rounds=int((check.real_json or {}).get("rounds") or 0))
        real = {
            "winner": (check.real_json or {}).get("winner") or "",
            "rounds": int((check.real_json or {}).get("rounds") or 0),
            "losses": {side: {int(unit): int(amount) for unit, amount in ((check.real_json or {}).get("losses", {}).get(side) or {}).items()}
                       for side in ("attacker", "defender")},
        }
        score = compare(real, predicted)
        winner_ok += 1 if score["winner_ok"] else 0
        winner_known += 1 if score["winner_known"] else 0
        attacker_error += score["attacker_error"]
        defender_error += score["defender_error"]
    total = max(1, len(checks))
    board = {
        "winner_pct": round(100 * winner_ok / winner_known) if winner_known else 0,
        "attacker_hit": round(100 * (1 - attacker_error / total)),
        "defender_hit": round(100 * (1 - defender_error / total)),
    }
    wrong_winner = (1 - winner_ok / winner_known) if winner_known else 0.0
    return (attacker_error + defender_error) / total + WINNER_WEIGHT * wrong_winner, board


def propose() -> dict:
    """Search the adjustment that fits the checks best and store it as the proposal."""
    from apps.combat.models import SimulationCheck, SimulatorCalibration

    # the adjustment is used by the land recommendation only, so it is learned from land combats only (N-113)
    checks = [check for check in SimulationCheck.objects.order_by("-created_at")[:MAX_CHECKS] if not (check.inputs_json or {}).get("naval")]
    proposal = {"samples": len(checks), "computed_at": timezone.now().isoformat(), "attacker_pct": 0, "defender_pct": 0, "before": {}, "after": {}, "better": False}
    if checks:
        base_cost, before = _cost(checks, 0, 0)
        best = (base_cost, 0, 0, before)
        tried = {(0, 0)}

        def consider(attacker_pct: int, defender_pct: int) -> None:
            nonlocal best
            if (attacker_pct, defender_pct) in tried or not (LIMITS[0] <= attacker_pct <= LIMITS[1] and LIMITS[0] <= defender_pct <= LIMITS[1]):
                return
            tried.add((attacker_pct, defender_pct))
            cost, board = _cost(checks, attacker_pct, defender_pct)
            # among equals, the smallest change from what the simulator does today
            if cost < best[0] - 1e-9 or (abs(cost - best[0]) <= 1e-9 and abs(attacker_pct) + abs(defender_pct) < abs(best[1]) + abs(best[2])):
                best = (cost, attacker_pct, defender_pct, board)

        for attacker_pct in COARSE:
            for defender_pct in COARSE:
                consider(attacker_pct, defender_pct)
        centre = (best[1], best[2])
        for attacker_step in FINE:
            for defender_step in FINE:
                consider(centre[0] + attacker_step, centre[1] + defender_step)
        proposal.update({"attacker_pct": best[1], "defender_pct": best[2], "before": before, "after": best[3], "better": best[0] < base_cost - 1e-9})
    row = SimulatorCalibration.load()
    row.proposed_json = proposal
    row.save(update_fields=["proposed_json", "updated_at"])
    return proposal


def can_approve(proposal: dict) -> bool:
    return bool(proposal) and int(proposal.get("samples") or 0) >= MIN_CHECKS and bool(proposal.get("better"))


def approve(username: str = "") -> bool:
    """Put the stored proposal into use. False when it may not be approved (too few combats, or no gain)."""
    from apps.combat.models import SimulatorCalibration

    row = SimulatorCalibration.load()
    proposal = row.proposed_json or {}
    if not can_approve(proposal):
        return False
    row.approved_json = {
        "attacker_pct": proposal["attacker_pct"], "defender_pct": proposal["defender_pct"], "samples": proposal["samples"],
        "before": proposal.get("before") or {}, "after": proposal.get("after") or {},
        "approved_at": timezone.now().isoformat(), "approved_by": username,
    }
    row.save(update_fields=["approved_json", "updated_at"])
    _CACHE["at"] = 0.0
    return True


def revoke() -> None:
    from apps.combat.models import SimulatorCalibration

    row = SimulatorCalibration.load()
    row.approved_json = {}
    row.save(update_fields=["approved_json", "updated_at"])
    _CACHE["at"] = 0.0


def active_bonus() -> tuple[float, float]:
    """(attacker, defender) damage adjustment in use, in percent; (0, 0) when nothing was approved."""
    if time.monotonic() - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["value"]
    value = (0.0, 0.0)
    try:
        from apps.combat.models import SimulatorCalibration

        approved = (SimulatorCalibration.objects.filter(pk=1).values_list("approved_json", flat=True).first() or {})
        value = (float(approved.get("attacker_pct") or 0), float(approved.get("defender_pct") or 0))
    except Exception:  # noqa: BLE001 - without the table (or the database) the simulator works as it always did
        logger.debug("No simulator calibration available", exc_info=True)
    _CACHE.update({"at": time.monotonic(), "value": value})
    return value
