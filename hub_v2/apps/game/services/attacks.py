"""Hostile movements seen by Alerta de Ataques (N-149): a pirate raid is not an attack.

A raid from a Pirate Fortress ("Assalto" on the Brazilian servers) is fought by the target's own Pirate Fortress
crew, not by troops in the city, and no miracle helps against it. Each event is told apart by the game's mission
class when the agent sends it (newer agents) and by the mission text otherwise.
"""
from __future__ import annotations

PIRATE_CLASS_HINTS = ("pira", "raid")
PIRATE_TEXT_PREFIXES = ("assalto", "raid", "pirat")


def event_kind(event: dict) -> str:
    """"pirate" for a pirate raid, "attack" for anything else hostile."""
    event = event if isinstance(event, dict) else {}
    kind = str(event.get("kind") or "").lower()
    if kind in ("pirate", "attack"):
        return kind
    mission_class = str(event.get("mission_class") or "").lower()
    if any(hint in mission_class for hint in PIRATE_CLASS_HINTS):
        return "pirate"
    text = str(event.get("mission") or "").strip().lower()
    return "pirate" if text.startswith(PIRATE_TEXT_PREFIXES) else "attack"


def is_pirate(event: dict) -> bool:
    return event_kind(event) == "pirate"


def summary(state: dict | None) -> dict:
    """Attacks and pirate raids on their way, from base_snapshot.attack_alert_state."""
    state = state if isinstance(state, dict) else {}
    events = [e for e in state.get("events") or [] if isinstance(e, dict)]
    hostile = int(state.get("hostile_count") or 0)
    if "pirate_count" in state:                      # agents from N-149 on count every hostile movement
        pirates = int(state.get("pirate_count") or 0)
    else:
        pirates = sum(1 for e in events if is_pirate(e))
    attacks = max(0, max(hostile, len(events)) - pirates)
    return {"attacks": attacks, "pirates": pirates}
