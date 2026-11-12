"""Barbarian village attack actions.

API reference (based on upstream ikabot + route inspection):

  Attack barbarian village (POST):
    action=transportOperations
    function=attackBarbarianVillage
    barbarianVillage=1
    islandId={island_id}
    transporter={num_cargo_ships}
    cargo_army_{unit_id}={quantity}
    actionRequest={ar}&ajax=1

  Barbarian state comes from the island view HTML (view=island&islandId=X).
  Island JSON embeds a `barbarians` dict with:
    level       int  — current barbarian level (scales over time/defeats)
    gold        int  — gold in the village
    resources   list — [wood, luxury, marble, crystal, sulfur]
    troops      list — troop type names guarding the village
    destroyed   int  — 1 = village defeated and being looted, 0 = active
    cooldown    int  — seconds until village respawns (when destroyed==1)

Unit weights (capacity per cargo ship slot, from ikabot upstream):
    Light infantry (302/303/304): 0.3 each
    Heavy (305/306/307/311):      1.0 each
    Siege / air (308/309):        2.0 each
    Steam Giant (310):            6.0

Cargo ship capacity: 5 weight units each.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)

# ── Unit weight table (capacity cost per cargo ship slot) ──
UNIT_WEIGHTS: dict[int, float] = {
    302: 0.3,
    303: 0.3,
    304: 0.3,
    305: 1.0,
    306: 1.0,
    307: 1.0,
    308: 2.0,
    309: 2.0,
    310: 6.0,
    311: 1.0,
}
CARGO_SHIP_CAPACITY = 5.0  # weight units per ship

# ── Predefined schematics (troop composition per barb level range) ──
# Matching upstream ikabot autoBarbarians DEFAULT_SCHEMATICS["WITHOUT_HEPHAESTUS"]
# Format: sorted list of (min_level, troops_dict) — highest min_level that fits is chosen
ATTACK_SCHEMATICS: list[tuple[int, dict[int, int]]] = [
    (30, {302: 300, 304: 147, 305: 24, 307: 18, 308: 300, 310: 5, 311: 10}),
    (20, {302: 60, 304: 70, 305: 12, 307: 12, 308: 100, 309: 30, 310: 5}),
    (10, {302: 60, 304: 35, 305: 12, 308: 50}),
    (1,  {302: 90, 304: 21}),
]

# Looting schematics (lighter, faster — just enough to collect resources)
LOOT_SCHEMATICS: list[tuple[int, dict[int, int]]] = [
    (30, {302: 10, 303: 10}),
    (20, {302: 5, 303: 5}),
    (10, {302: 3, 303: 3}),
    (1,  {302: 1}),
]


def get_schematic(barb_level: int, table: list[tuple[int, dict[int, int]]]) -> dict[int, int]:
    """Return the schematic for the given barbarian level."""
    for min_level, troops in sorted(table, reverse=True):
        if barb_level >= min_level:
            return dict(troops)
    return dict(table[-1][1])  # fallback to lowest tier


def calculate_transporters(troops: dict[int, int], extra: int = 0) -> int:
    """Calculate minimum cargo ships needed for the given troop composition."""
    total_weight = sum(UNIT_WEIGHTS.get(uid, 1.0) * qty for uid, qty in troops.items())
    return max(1, math.ceil(total_weight / CARGO_SHIP_CAPACITY) + extra)


def read_attack_view(client, from_city_id: int | str, island_id: int | str) -> list[dict[str, Any]]:
    """N-179: open (only read) the screens the game shows before an attack on the barbarians, to learn what it
    expects: the form fields, the unit weights and the ships' capacity. Nothing is sent."""
    import re

    variants = {
        "village": {"view": "barbarianVillage", "islandId": str(island_id), "destinationCityId": "0",
                    "backgroundView": "island", "currentIslandId": str(island_id)},
        "plunder": {"view": "plunder", "barbarianVillage": "1", "islandId": str(island_id), "destinationCityId": "0",
                    "backgroundView": "island", "currentIslandId": str(island_id), "templateView": "island"},
    }
    out = []
    try:
        page = client._request("GET", client._server_url, params={"view": "island", "islandId": str(island_id)}, timeout=30).text
        raw = re.search(r'"barbarians"\s*:\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', page)
        out.append({"view": "island_raw_barbarians", "raw": raw.group(1)[:1200] if raw else ""})
        token = client.page_parser.extract_action_request(page)
        if token:
            client._action_request = token
    except Exception as exc:  # noqa: BLE001
        out.append({"view": "island_raw_barbarians", "error": str(exc)[:200]})
    for name, params in variants.items():
        try:
            resp = client._request("GET", client._server_url, params={**params, "currentCityId": str(from_city_id),
                                                                       "actionRequest": client._action_request, "ajax": "1"},
                                   headers=dict(GAME_AJAX_HEADERS), timeout=30)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 - a diagnosis reports what failed
            out.append({"view": name, "error": str(exc)[:200]})
            continue
        html, feedback = "", []
        for entry in data if isinstance(data, list) else []:
            if not (isinstance(entry, list) and len(entry) > 1):
                continue
            if isinstance(entry[1], dict) and entry[1].get("actionRequest"):
                client._action_request = str(entry[1]["actionRequest"])
            if entry[0] in ("changeView", "changeHTML"):
                for item in entry[1] if isinstance(entry[1], list) else [entry[1]]:
                    if isinstance(item, str) and len(item) > len(html):
                        html = item
            if entry[0] == "provideFeedback":
                feedback = [str(fb.get("text") or "") for fb in entry[1] or [] if isinstance(fb, dict)]
        controller = re.search(r"new missionController\(([^)]*)\)", html)
        weights = {m.group(1): m.group(2) for m in re.finditer(
            r'slider_(\d+)"?\s*\);[\s\S]{0,400}?weight\s*=\s*([\d.]+)', html)}
        hidden = {m.group(1): m.group(2)[:40] for m in re.finditer(
            r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html)}
        hidden.update({m.group(2): m.group(1)[:40] for m in re.finditer(
            r'<input[^>]*value="([^"]*)"[^>]*type="hidden"[^>]*name="([^"]+)"', html)})
        hidden.pop("actionRequest", None)          # a session token never goes to the logs
        out.append({
            "view": name, "html": len(html), "feedback": feedback, "hidden": hidden,
            "weight_snippets": [re.sub(r"\s+", " ", m.group(0)) for m in re.finditer(
                r"[\s\S]{0,160}[Ww]eight[\"']?\s*[:=]\s*[\d.]+[\s\S]{0,40}", html)][:6],
            "controller": controller.group(1)[:200] if controller else "",
            "weights": weights, "units": sorted(set(re.findall(r"cargo_army_(\d+)", html))),
            "fields": sorted(set(re.findall(r'name="([a-zA-Z_]+)"', html)))[:40],
            "functions": sorted(set(re.findall(r'function["\']?\s*[:=]\s*["\']([a-zA-Z]+)', html)))[:10],
        })
    return out


class AttackBarbarianVillageAction(BaseAction):
    """Send troops to attack (or loot) the barbarian village on an island.

    Uses:
        action=transportOperations&function=attackBarbarianVillage

    Both the initial attack and the loot phase use the same endpoint.
    When barbarians.destroyed==1, the "attack" just collects the remaining resources.
    """

    def execute(
        self,
        from_city_id: int | str,
        island_id: int | str,
        troops: dict[int, int],
        transporters: int | None = None,
        extra_ships: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send the attack.

        Args:
            from_city_id: City where troops depart from.
            island_id: Island containing the barbarian village.
            troops: {unit_id: quantity} mapping.
            transporters: Override cargo ship count. Auto-calculated if None.
            extra_ships: Additional ships beyond the auto-calculated minimum.

        Returns:
            {"ok": bool, "transporters": int, "troops": dict}
        """
        if not troops or not any(q > 0 for q in troops.values()):
            raise ActionError("No troops specified for barbarian attack", action="attackBarbarianVillage")

        if transporters is None:
            transporters = calculate_transporters(troops, extra_ships)

        payload: dict[str, Any] = {
            "action": "transportOperations",
            "function": "attackBarbarianVillage",
            "barbarianVillage": "1",
            "islandId": str(island_id),
            "transporter": str(transporters),
            "backgroundView": "island",
            "currentCityId": str(from_city_id),
            "cityId": str(from_city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        for uid, qty in troops.items():
            if int(qty) > 0:
                payload[f"cargo_army_{uid}"] = int(qty)

        logger.info(
            "Barbarian attack: city=%s island=%s troops=%s ships=%s",
            from_city_id, island_id, troops, transporters,
        )

        resp = self.client._request(
            "POST", self.client._server_url,
            data=payload,
            headers=dict(GAME_AJAX_HEADERS),
            timeout=30,
        )

        ok = True
        error_msg = ""
        try:
            data = resp.json()
            # Update actionRequest
            for entry in data:
                if isinstance(entry, list) and len(entry) > 1:
                    if entry[0] == "updateGlobalData" and isinstance(entry[1], dict):
                        ar = entry[1].get("actionRequest")
                        if ar:
                            self.client._action_request = str(ar)
            # Check for error feedback
            for entry in data:
                if isinstance(entry, list) and entry[0] == "provideFeedback":
                    for fb in (entry[1] or []):
                        if isinstance(fb, dict) and int(fb.get("type", 0)) == 11:
                            ok = False
                            error_msg = str(fb.get("text", "game error"))
                            raise ActionError(error_msg, action="attackBarbarianVillage")
        except ActionError:
            raise
        except Exception as exc:
            logger.warning("Failed to parse barbarian attack response: %s", exc)

        return {"ok": ok, "transporters": transporters, "troops": troops, "error": error_msg}
