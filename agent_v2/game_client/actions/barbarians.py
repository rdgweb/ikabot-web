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


# N-179: what the village yields (gold goes straight to the town; the resources need merchant ships, 500 each),
# from the Ikariam wiki table (level: resources). Levels in between are interpolated.
LOOT_BY_LEVEL = {1: 500, 2: 575, 3: 725, 4: 950, 5: 1_250, 6: 1_625, 7: 1_995, 8: 2_500, 9: 3_100,
                 32: 89_600, 41: 214_850, 49: 434_250, 50: 495_150}
SHIP_CAPACITY = 500


def loot_ships_for_level(level: int) -> int:
    """Merchant ships to bring back the resources of a village of that level (a little more, to be safe)."""
    level = max(1, min(50, int(level or 1)))
    points = sorted(LOOT_BY_LEVEL.items())
    for (l1, r1), (l2, r2) in zip(points, points[1:]):
        if l1 <= level <= l2:
            loot = r1 + (r2 - r1) * (level - l1) / (l2 - l1) if l2 > l1 else r1
            return max(1, math.ceil(loot * 1.1 / SHIP_CAPACITY))
    return max(1, math.ceil(points[-1][1] * 1.1 / SHIP_CAPACITY))


def open_attack_view(client, from_city_id: int | str, island_id: int | str) -> dict[str, Any]:
    """The screen the game opens for "Roubar" on the village, read for its numbers: free merchant ships, ship
    capacity, journey time, and the upkeep and weight of each unit (troops of the same island weigh 0)."""
    import re

    resp = client._request("GET", client._server_url, params={
        "view": "plunder", "destinationCityId": "0", "barbarianVillage": "1", "islandId": str(island_id),
        "backgroundView": "island", "currentIslandId": str(island_id), "currentCityId": str(from_city_id),
        "templateView": "island", "actionRequest": client._action_request, "ajax": "1",
    }, headers=dict(GAME_AJAX_HEADERS), timeout=30)
    data = resp.json()
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
    if not html:
        raise ActionError(f"Tela de ataque aos bárbaros não abriu: {'; '.join(feedback) or 'sem resposta'}",
                          action="attackBarbarianVillage")
    args = re.search(r"new missionController\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", html)
    units = {}
    for m in re.finditer(r'sliders\["slider_(\d+)"\];\s*s\.upkeep\s*=\s*([\d.]+);\s*s\.weight\s*=\s*([\d.]+);'
                         r'(?:\s*s\.unitJourneyTime\s*=\s*([\d.]+))?', html):
        units[int(m.group(1))] = {"upkeep": m.group(2), "weight": float(m.group(3)), "journey": int(float(m.group(4) or 0))}
    upkeep_fields = {int(m.group(1)): m.group(2) for m in re.finditer(r'name="cargo_army_(\d+)_upkeep"[^>]*value="([^"]*)"', html)}
    upkeep_fields.update({int(m.group(2)): m.group(1) for m in re.finditer(r'value="([^"]*)"[^>]*name="cargo_army_(\d+)_upkeep"', html)})
    return {
        "free_ships": int(float(args.group(1))) if args else 0,
        "ship_capacity": int(float(args.group(2))) if args else SHIP_CAPACITY,
        "journey_seconds": int(float(args.group(3))) if args else 0,
        "units": units, "upkeep": upkeep_fields,
    }


class AttackBarbarianVillageAction(BaseAction):
    """Send troops (and merchant ships for the loot) to the barbarian village of an island, the way the game's own
    "Roubar" form does (N-179, checked on the game): from the town that is the current one, with
    function=attackBarbarianVillage, destinationCityId=0, the island, the ships and each unit with its upkeep.
    The same call collects what was left while the village is being rebuilt.
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
        if not troops or not any(int(q) > 0 for q in troops.values()):
            raise ActionError("No troops specified for barbarian attack", action="attackBarbarianVillage")
        try:
            from services.resource_transport import change_current_city

            change_current_city(self.client, int(from_city_id))
        except Exception as exc:  # noqa: BLE001 - the screen below tells if the town is not the right one
            logger.warning("Could not switch to city %s before the barbarian attack: %s", from_city_id, exc)

        view = open_attack_view(self.client, from_city_id, island_id)
        missing = [uid for uid in troops if int(uid) not in view["units"]]
        if missing:
            raise ActionError(f"Tropas fora da tela de ataque desta cidade: {missing}", action="attackBarbarianVillage")
        capacity = max(1, view["ship_capacity"])
        weight = sum(view["units"][int(uid)]["weight"] * int(qty) for uid, qty in troops.items())
        troop_ships = math.ceil(weight / capacity) if weight else 0
        wanted = max(troop_ships, int(transporters or 0) + int(extra_ships or 0), 1 if not troop_ships else 0)
        ships = min(view["free_ships"], wanted)
        if ships < troop_ships:
            raise ActionError(f"Faltam navios para as tropas: precisa {troop_ships}, livres {view['free_ships']}",
                              action="attackBarbarianVillage")

        payload: dict[str, Any] = {
            "action": "transportOperations",
            "function": "attackBarbarianVillage",
            "actionRequest": self.client._action_request,
            "islandId": str(island_id),
            "destinationCityId": "0",
            "transporter": str(ships),
            "backgroundView": "island",
            "currentIslandId": str(island_id),
            "templateView": "plunder",
            "ajax": "1",
        }
        for uid, qty in troops.items():
            if int(qty) > 0:
                payload[f"cargo_army_{int(uid)}"] = int(qty)
                payload[f"cargo_army_{int(uid)}_upkeep"] = view["upkeep"].get(int(uid)) or view["units"][int(uid)]["upkeep"]
        logger.info("Barbarian attack: city=%s island=%s troops=%s ships=%s", from_city_id, island_id, troops, ships)
        resp = self.client._request("POST", self.client._server_url, data=payload, headers=dict(GAME_AJAX_HEADERS), timeout=30)

        error_msg = ""
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = []
        for entry in data if isinstance(data, list) else []:
            if not (isinstance(entry, list) and len(entry) > 1):
                continue
            if isinstance(entry[1], dict) and entry[1].get("actionRequest"):
                self.client._action_request = str(entry[1]["actionRequest"])
            if entry[0] == "provideFeedback":
                for fb in entry[1] or []:
                    if isinstance(fb, dict) and int(fb.get("type", 0)) == 11:
                        error_msg = str(fb.get("text", "game error"))
        if error_msg:
            raise ActionError(error_msg, action="attackBarbarianVillage")
        return {"ok": True, "transporters": ships, "troops": troops, "error": "",
                "free_ships": view["free_ships"], "journey_seconds": view["journey_seconds"]}
