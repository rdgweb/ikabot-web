"""Shrine actions - inspect and operate the Shrine of Olympus."""

from __future__ import annotations

import logging
import re
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)


class ShrineAction(BaseAction):
    """Low-level Shrine of Olympus requests."""

    def get_state(
        self,
        *,
        city_id: int,
        position: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = {
            "view": "shrineOfOlympus",
            "cityId": str(city_id),
            "position": str(position),
            "activeTab": "tabOverview",
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "shrineOfOlympus",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("GET", self.client._server_url, params=params)
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError("Invalid shrine overview response", action="shrine_state") from exc
        return self._parse_state_payload(payload)

    def get_favor(
        self,
        *,
        city_id: int,
        position: int,
        **kwargs: Any,
    ) -> int:
        return int(self.get_state(city_id=city_id, position=position).get("current_favor") or 0)

    def activate_god(
        self,
        *,
        city_id: int,
        position: int,
        god_id: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = {
            "action": "DonateFavorToGod",
            "godId": str(god_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "shrineOfOlympus",
            "currentTab": "tabOverview",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request(
            "POST",
            self.client._server_url,
            data=params,
            headers=GAME_AJAX_HEADERS,
        )
        try:
            return resp.json()
        except Exception:
            logger.debug("Shrine activation returned non-JSON payload")
            return {"ok": True}

    def get_full_page(
        self,
        *,
        city_id: int,
        position: int,
        **kwargs: Any,
    ) -> str:
        params = {
            "view": "shrineOfOlympus",
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "shrineOfOlympus",
        }
        resp = self.client._request("GET", self.client._server_url, params=params)
        return resp.text

    @staticmethod
    def _parse_state_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, list):
            raise ActionError("Unexpected shrine payload shape", action="shrine_state")

        template_data: dict[str, Any] = {}
        change_view_html = ""
        background_data: dict[str, Any] = {}
        for item in payload:
            if not isinstance(item, list) or len(item) < 2:
                continue
            name = item[0]
            data = item[1]
            if name == "updateTemplateData" and isinstance(data, dict):
                template_data = data
            elif name == "changeView" and isinstance(data, list) and len(data) >= 2 and data[0] == "shrineOfOlympus":
                change_view_html = str(data[1] or "")
            elif name in {"updateBackgroundData", "updateGlobalData"} and isinstance(data, dict):
                if name == "updateBackgroundData":
                    background_data = data
                elif not background_data:
                    background_data = data.get("backgroundData") or {}

        if not template_data:
            raise ActionError("Shrine template data missing", action="shrine_state")

        current_favor = int(template_data.get("currentFavor") or 0)
        researched_gods = sorted(
            int(god_id)
            for god_id in (template_data.get("researchedGods") or [])
            if str(god_id).isdigit()
        )

        gods: dict[int, dict[str, Any]] = {}
        css_names = {
            1: "pan",
            2: "dionysus",
            3: "tyche",
            4: "plutus",
            5: "theia",
            6: "hephaestos",
        }
        for god_id, css_name in css_names.items():
            prefix = f"shrineOfOlympus .god_{css_name}"
            gods[god_id] = {
                "researched": god_id in researched_gods,
                "grace_period": template_data.get(f"{prefix} .gracePeriod"),
                "progress": int(template_data.get(f"{prefix} .progressbar .text") or 0),
                "progress_width": ((template_data.get(f"{prefix} .bar") or {}).get("style") or {}).get("width", ""),
                "progress_visible": (((template_data.get(f'{prefix} .progressbar') or {}).get('style') or {}).get('display') != "none"),
            }

        worship_assignments: list[dict[str, Any]] = []
        for key, value in template_data.items():
            match = re.match(r"shrineOfOlympus \.gods\.worshiping li\[data-number=(\d+)\] \.god$", str(key))
            if not match or not isinstance(value, dict):
                continue
            data_number = int(match.group(1))
            css_class = str(value.get("class") or "")
            god_css = next((part for part in css_class.split() if part.startswith("god_")), "")
            worship_assignments.append(
                {
                    "slot": data_number,
                    "god_css": god_css,
                    "start_time": template_data.get(f"shrineOfOlympus .gods.worshiping li[data-number={data_number}] .startTime"),
                    "generated_resources": template_data.get(f"shrineOfOlympus .gods.worshiping li[data-number={data_number}] .generatedResources"),
                    "current_bonus": template_data.get(f"shrineOfOlympus .gods.worshiping li[data-number={data_number}] .currentBonus"),
                }
            )

        building_level = 0
        max_cities_next_level = 0
        maximum_bonus_next_level = 0
        additional_cities_possible = int(template_data.get("numberOfAdditionalCitiesPossible") or 0)
        if change_view_html:
            level_match = re.search(r'<li class="showLevel">[\s\S]*?(\d+)\s*</li>', change_view_html)
            next_cities_match = re.search(r"Número máximo de cidades:\s*</b>\s*(\d+)", change_view_html)
            next_bonus_match = re.search(r"Bênção máxima:\s*</b>\s*(\d+)%", change_view_html)
            building_level = int(level_match.group(1)) if level_match else 0
            max_cities_next_level = int(next_cities_match.group(1)) if next_cities_match else 0
            maximum_bonus_next_level = int(next_bonus_match.group(1)) if next_bonus_match else 0

        city_name = ""
        island_name = ""
        if background_data:
            city_name = str(background_data.get("name") or "")
            island_name = str(background_data.get("islandName") or "")

        return {
            "city_id": int(template_data.get("cityId") or 0),
            "position": int(template_data.get("position") or 0),
            "city_name": city_name,
            "island_name": island_name,
            "current_favor": current_favor,
            "researched_gods": researched_gods,
            "building_level": building_level,
            "max_cities_next_level": max_cities_next_level,
            "maximum_bonus_next_level": maximum_bonus_next_level,
            "additional_cities_possible": additional_cities_possible,
            "gods": gods,
            "worship_assignments": worship_assignments,
            "raw_template_data": template_data,
        }
