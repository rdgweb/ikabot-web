"""Island actions — fetch island data by ID, coordinates, or area region."""
from __future__ import annotations

import json
import re
from typing import Any

RESOURCE_NAMES = {0: "Madeira", 1: "Vinho", 2: "Mármore", 3: "Cristal", 4: "Enxofre"}


class IslandActions:
    """Mixin for island-related game client actions."""

    def fetch_island_by_id(self, island_id: str | int) -> dict[str, Any]:
        """Fetch island data by island ID."""
        resp = self._request(
            "GET", self._server_url,
            params={"view": "island", "islandId": str(island_id)},
            timeout=30,
        )
        return _parse_island_html(resp.text)

    def fetch_island_by_coords(self, x: int, y: int) -> dict[str, Any]:
        """Fetch island data by world coordinates."""
        resp = self._request(
            "GET", self._server_url,
            params={"view": "island", "xcoord": str(x), "ycoord": str(y)},
            timeout=30,
        )
        return _parse_island_html(resp.text)

    def fetch_island_by_city_id(self, city_id: str | int) -> dict[str, Any]:
        """Fetch island data using a visible city on that island as context.

        This works for foreign cities as well, unlike ``get_city_island_id`` which
        relies on the city page and is mainly suited for own cities.
        """
        resp = self._request(
            "GET", self._server_url,
            params={"view": "island", "cityId": str(city_id)},
            timeout=30,
        )
        return _parse_island_html(resp.text)

    def fetch_island_area(
        self, x_min: int, y_min: int, x_max: int, y_max: int
    ) -> list[dict[str, Any]]:
        """Fetch all islands within a coordinate bounding box via WorldMap API.

        Returns list of lightweight island dicts with id, name, coords, and city count.
        Full island detail (with cities/players) requires fetch_island_by_id per island.
        """
        resp = self._request(
            "GET", self._server_url,
            params={
                "action": "WorldMap",
                "function": "getJSONArea",
                "x_min": str(x_min),
                "x_max": str(x_max),
                "y_min": str(y_min),
                "y_max": str(y_max),
                "ajax": "1",
            },
            timeout=30,
        )
        islands: list[dict[str, Any]] = []
        try:
            data = resp.json()
            area = data.get("data", {})
            for x_str, row in area.items():
                if not isinstance(row, dict):
                    continue
                for y_str, island_arr in row.items():
                    if not isinstance(island_arr, list) or len(island_arr) < 8:
                        continue
                    island_id = island_arr[0]
                    if not island_id:
                        continue
                    resource_type = island_arr[2] if len(island_arr) > 2 else 0
                    islands.append({
                        "island_id": str(island_id),
                        "name": str(island_arr[1] or ""),
                        "resource_type": int(resource_type) if resource_type else 0,
                        "resource_name": RESOURCE_NAMES.get(int(resource_type) if resource_type else 0, ""),
                        "miracle_type": int(island_arr[3]) if len(island_arr) > 3 and island_arr[3] else 0,
                        "wood_level": int(island_arr[6]) if len(island_arr) > 6 and island_arr[6] else 0,
                        "city_count": int(island_arr[7]) if len(island_arr) > 7 and island_arr[7] else 0,
                        "x": int(x_str),
                        "y": int(y_str),
                    })
        except Exception:
            pass
        return islands


def _parse_island_html(html: str) -> dict[str, Any]:
    """Parse island data from the game's island view HTML.

    Ikariam embeds island JSON as: ajax.Responder, ([[...]]));
    The island dict is at json[1][1].
    """
    try:
        m = re.search(r'ajax\.Responder,\s*(\[\[[\S\s]*?\]\])\s*\)', html)
        if not m:
            m = re.search(r'Responder,\s*(\[\[[\s\S]*?\]\])\)', html)
        if not m:
            return {}

        island_data = json.loads(m.group(1))[1][1]

        def _safe_int(v: Any, default: int = 0) -> int:
            try:
                return int(str(v).replace(".", "").replace(",", ""))
            except Exception:
                return default

        cities: list[dict[str, Any]] = []
        for idx, city in enumerate(island_data.get("cities", []), start=1):
            if not isinstance(city, dict):
                continue
            city_type = str(city.get("type") or "empty")
            infos = city.get("infos") or {}
            in_fight = isinstance(infos, dict) and infos.get("armyAction") == "fight"
            actions = city.get("actions") or []
            cities.append({
                "id": str(city.get("id") or ""),
                "position": int(city.get("position") or idx),
                "name": str(city.get("name") or ""),
                "owner_id": str(city.get("ownerId") or ""),
                "owner_name": str(city.get("ownerName") or ""),
                "ally_id": str(city.get("ownerAllyId") or ""),
                "ally_tag": str(city.get("ownerAllyTag") or ""),
                "level": int(city.get("level") or 0),
                "type": city_type,
                "state": str(city.get("state") or ""),
                "in_fight": in_fight,
                "has_treaties": bool(city.get("hasTreaties")),
                "view_able": int(city.get("viewAble") or 0),
                "infested_by_plague": bool(city.get("infestedByPlague")),
                "actions": actions if isinstance(actions, (list, dict)) else [],
            })

        resource_type = island_data.get("tradegood", 0)
        resource_type_int = int(resource_type) if resource_type is not None else 0
        wonder_type = int(island_data.get("wonder") or 0)

        # avatarScores: {owner_id: {place, building_score_main, research_score_main, army_score_main, trader_score_secondary}}
        avatar_scores: dict[str, dict[str, Any]] = {}
        raw_scores = island_data.get("avatarScores") or {}
        if isinstance(raw_scores, dict):
            for pid, score in raw_scores.items():
                if not isinstance(score, dict):
                    continue
                # building/research/army scores come multiplied by 100 in the game API
                avatar_scores[str(pid)] = {
                    "avatar_id": str(score.get("avatar_id") or pid),
                    "place": _safe_int(score.get("place")),
                    "building_score": _safe_int(score.get("building_score_main")) // 100,
                    "research_score": _safe_int(score.get("research_score_main")) // 100,
                    "army_score": _safe_int(score.get("army_score_main")) // 100,
                    "trader_score": _safe_int(score.get("trader_score_secondary")),
                }

        # Parse barbarian village data
        barb_raw = island_data.get("barbarians") or {}
        barbarians: dict = {}
        if barb_raw and isinstance(barb_raw, dict):
            resources_raw = barb_raw.get("resources") or []
            barbarians = {
                "level": int(barb_raw.get("level") or 0),
                "gold": _safe_int(barb_raw.get("gold")),
                "resources": [_safe_int(r) for r in (resources_raw if isinstance(resources_raw, list) else [])],
                "total_resources": sum(_safe_int(r) for r in (resources_raw if isinstance(resources_raw, list) else [])),
                "troops": barb_raw.get("troops") or [],
                "destroyed": int(barb_raw.get("destroyed") or 0) == 1,
                "cooldown_seconds": int(barb_raw.get("cooldown") or 0),
            }

        return {
            "island_id": str(island_data.get("id") or ""),
            "name": str(island_data.get("name") or ""),
            "x": int(island_data.get("xCoord") or 0),
            "y": int(island_data.get("yCoord") or 0),
            "resource_type": resource_type_int,
            "resource_name": RESOURCE_NAMES.get(resource_type_int, ""),
            "resource_level": _safe_int(island_data.get("tradegoodLevel")),
            "wood_level": _safe_int(island_data.get("resourceLevel")),
            "miracle_type": wonder_type,
            "miracle_name": str(island_data.get("wonderName") or ""),
            "miracle_level": _safe_int(island_data.get("wonderLevel")),
            "helios_built": bool(island_data.get("isHeliosTowerBuilt")),
            "cities": cities,
            "avatar_scores": avatar_scores,
            "barbarians": barbarians,
        }
    except Exception:
        return {}
