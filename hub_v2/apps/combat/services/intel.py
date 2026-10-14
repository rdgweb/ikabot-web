"""What the combat reports reveal about the others, kept for the next simulation (N-112).

A report shows the level of the wall it was fought against and, for every army in it, the
upgrades of each unit. Until now the simulator guessed both (the wall from the level of the
city). Here they are kept when a report is read, and given back to whoever simulates an
attack on that city or against that player.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def player_key(name: str) -> str:
    """A player as a key: lower case, without the alliance tag the game writes after the name."""
    return (name or "").split("[")[0].strip().lower()


def _own_names(report) -> set:
    return {player_key(getattr(report.game_account, "name", "") or "")} - {""} if report.game_account_id else set()


def side_upgrades(rounds: list, side: str) -> dict:
    """{unit: {"offensive", "defensive"}} for one side, the highest level any round shows."""
    found: dict[int, dict] = {}
    for item in rounds:
        for unit, levels in (item[side].get("upgrades") or {}).items():
            current = found.setdefault(int(unit), {"offensive": 0, "defensive": 0})
            current["offensive"] = max(current["offensive"], int(levels[0]))
            current["defensive"] = max(current["defensive"], int(levels[1]))
    return found


def wall_level(rounds: list) -> int:
    """The level of the wall the combat was fought against, 0 when no round shows one."""
    return max((int((item.get("wall") or {}).get("level") or 0) for item in rounds), default=0)


def learn_from(report, rounds: list) -> int:
    """Keep what the rounds of a report show about a wall and about the players. Returns how many things were kept."""
    from apps.combat.models import BattleIntel

    if not report.game_account_id or not rounds:
        return 0
    server_id = report.game_account.server_id
    own = _own_names(report)
    kept = 0

    level = wall_level(rounds)
    if level and report.target_city_id:
        BattleIntel.objects.update_or_create(
            server_id=server_id, kind="wall", key=str(report.target_city_id),
            defaults={"label": (report.target_city_name or "")[:160], "value_json": {"level": level}, "seen_at": report.combat_date, "report": report},
        )
        kept += 1

    players: dict[str, dict] = {}
    labels: dict[str, str] = {}
    for item in rounds:
        for side in ("attacker", "defender"):
            for name, units in (item[side].get("players") or {}).items():
                key = player_key(name)
                if not key or key in own:
                    continue
                labels[key] = name
                for unit, levels in units.items():
                    current = players.setdefault(key, {}).setdefault(str(unit), [0, 0])
                    current[0], current[1] = max(current[0], int(levels[0])), max(current[1], int(levels[1]))
    for key, units in players.items():
        row = BattleIntel.objects.filter(server_id=server_id, kind="upgrades", key=key).first()
        merged = dict((row.value_json if row else {}) or {})
        for unit, levels in units.items():
            before = merged.get(unit) or [0, 0]
            merged[unit] = [max(int(before[0]), levels[0]), max(int(before[1]), levels[1])]     # an upgrade is never lost
        BattleIntel.objects.update_or_create(
            server_id=server_id, kind="upgrades", key=key,
            defaults={"label": labels[key][:160], "value_json": merged, "seen_at": report.combat_date, "report": report},
        )
        kept += 1
    return kept


def learn_safely(report, rounds: list) -> None:
    try:
        learn_from(report, rounds)
    except Exception:  # noqa: BLE001 - what a report teaches never stands in the way of keeping it
        logger.warning("Could not keep what the combat %s shows", report.combat_id, exc_info=True)


def known_wall_level(server_id: str, city_id) -> int | None:
    """The level of the wall of a city, as the last combat fought there showed it. None when unknown."""
    from apps.combat.models import BattleIntel

    if not server_id or not city_id:
        return None
    row = BattleIntel.objects.filter(server_id=server_id, kind="wall", key=str(city_id)).first()
    level = int((row.value_json or {}).get("level") or 0) if row else 0
    return level or None


def known_upgrades(server_id: str, player_name: str) -> dict:
    """{unit: {"offensive", "defensive"}} of a player, as the combats against him showed. {} when unknown."""
    from apps.combat.models import BattleIntel

    key = player_key(player_name)
    if not server_id or not key:
        return {}
    row = BattleIntel.objects.filter(server_id=server_id, kind="upgrades", key=key).first()
    return {
        int(unit): {"offensive": int(levels[0]), "defensive": int(levels[1])}
        for unit, levels in ((row.value_json if row else {}) or {}).items()
        if str(unit).isdigit() and isinstance(levels, list) and len(levels) == 2
    }


def merge_upgrades(*sources: dict) -> dict:
    """Several things known about the same player, the highest level of each kept."""
    merged: dict[int, dict] = {}
    for source in sources:
        for unit, levels in (source or {}).items():
            current = merged.setdefault(int(unit), {"offensive": 0, "defensive": 0})
            current["offensive"] = max(current["offensive"], int((levels or {}).get("offensive") or 0))
            current["defensive"] = max(current["defensive"], int((levels or {}).get("defensive") or 0))
    return merged
