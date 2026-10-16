"""N-141: Templo de Poseidon or Templo de Hermes when a shipment takes most of the account's ships.

The hub sends the organization's rule with each shipment job (`job["miracle_rules"]`):
speed_on_shipments, ship_share (% of the account's ships) and speed_miracles (names as the game writes them).
A miracle works for every city of the account, so any temple with one of them available will do; it is asked
through the usual Ativar Milagre job (ac=11), at most once every few hours per account from this agent.
"""
from __future__ import annotations

import time
from typing import Any

ASK_AGAIN_SECONDS = 6 * 60 * 60
_asked_at: dict[str, float] = {}


def speed_miracle_city(snapshot: dict[str, Any] | None, names: list[str]) -> tuple[str, str] | None:
    """(city_id, miracle name) of a temple whose speed miracle can be activated now, from the snapshot."""
    cities = (snapshot or {}).get("cities") if isinstance(snapshot, dict) else None
    for wanted in names:
        for city in cities if isinstance(cities, list) else []:
            if not isinstance(city, dict):
                continue
            state = city.get("miracle_state") or {}
            name = str(state.get("wonder_name") or "").split(" (")[0].strip()
            if name == wanted and str(state.get("activate_state") or "").lower() == "enabled":
                return str(city.get("id")), name
    return None


def ship_share_reached(rules: dict[str, Any], ships_used: int, max_ships: int) -> bool:
    if not rules.get("speed_on_shipments") or ships_used <= 0 or max_ships <= 0:
        return False
    try:
        share = max(1, min(100, int(rules.get("ship_share") or 50)))
    except (TypeError, ValueError):
        share = 50
    return ships_used * 100 >= share * max_ships


def maybe_ask_speed_miracle(runner, job: dict[str, Any], snapshot: dict[str, Any] | None, ships_used: int,
                            *, freighters: bool = False) -> bool:
    """Spawn Ativar Milagre for Poseidon/Hermes when the shipment uses at least the rule's share of the ships."""
    rules = job.get("miracle_rules") or {}
    jid = job["job_id"]
    ga_id = str(job.get("game_account_id") or "")
    if not rules or not ga_id:
        return False
    base = (snapshot.get("base_snapshot") or {}) if isinstance(snapshot, dict) else {}
    try:
        max_ships = int(base.get("max_freighters" if freighters else "max_transporters") or 0)
    except (TypeError, ValueError):
        max_ships = 0
    if not ship_share_reached(rules, ships_used, max_ships):
        return False
    if time.time() - _asked_at.get(ga_id, 0) < ASK_AGAIN_SECONDS:
        return False
    found = speed_miracle_city(snapshot, list(rules.get("speed_miracles") or []))
    if found is None:
        return False
    city_id, name = found
    try:
        runner.hub.spawn_job(jid, action_code=11, inputs={"city": city_id, "reason": f"envio com {ships_used} de {max_ships} navios"})
    except Exception as exc:  # noqa: BLE001 - the shipment is done; the miracle is a bonus
        runner.log(jid, "warn", f"Milagre de velocidade não pedido: {exc}")
        return False
    _asked_at[ga_id] = time.time()
    runner.log(jid, "info", f"✨ {name} pedido: o envio usa {ships_used} de {max_ships} navios")
    return True
