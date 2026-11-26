"""N-140: one's own troops and warships on their way between cities.

While they travel they are in no city's garrison, so a move between one's own cities looked like a loss on the panel.
The attack alert (701) reads the military advisor and keeps the own movements in attack_alert_state.own_in_transit;
what has not arrived yet at a given moment counts as "in transit" — once it lands it is in the garrison again, so it
never counts twice.
"""
from __future__ import annotations

from datetime import datetime

from django.utils import timezone


def _si(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def units_in_transit(base: dict | None, at: datetime | None = None) -> dict:
    """{"troops", "ships", "movements"} still travelling at `at` (now by default)."""
    state = (base or {}).get("attack_alert_state") if isinstance(base, dict) else None
    items = (state or {}).get("own_in_transit") if isinstance(state, dict) else None
    moment = int((at or timezone.now()).timestamp())
    movements = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        arrives_at = _si(item.get("arrives_at"))
        if not arrives_at or arrives_at <= moment:
            continue
        troops, ships = _si(item.get("troops")), _si(item.get("ships"))
        if troops or ships:
            movements.append({**item, "troops": troops, "ships": ships, "left_seconds": arrives_at - moment})
    return {
        "troops": sum(item["troops"] for item in movements),
        "ships": sum(item["ships"] for item in movements),
        "movements": movements,
    }
