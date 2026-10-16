"""Temple miracle actions.

Temple (city building):
  view=temple, cityId, position                         -> priests, conversion, miracle text/state (JSON)
  action=CityScreen&function=activateWonder             -> activate the island miracle
  action=CityScreen&function=assignPriests, priests=N   -> set the priests (N-141; form "setPriests" of the temple)

Island miracle (N-141):
  view=wonder, islandId                                  -> level, next level need, donated, island faith, upgrade in
                                                            progress, and the table "Outros jogadores nesta ilha"
                                                            (player, city, donated, priests, conversion, island share,
                                                            link to the city's temple)
Source: captures of BlackShadow701 (s78-br), April and 16/10/2026.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction


class MiracleAction(BaseAction):
    """Inspect and activate the island miracle from a city's temple."""

    def get_temple_state(self, *, city_id: int, position: int, **kwargs: Any) -> dict[str, Any]:
        payload = self._fetch_temple_payload(city_id=city_id, position=position)
        return self._parse_temple_payload(payload, requested_position=position)

    def activate_miracle(self, *, city_id: int, position: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "action": "CityScreen",
            "function": "activateWonder",
            "position": str(position),
            "cityId": str(city_id),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "temple",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError("Invalid miracle activation response", action="activate_miracle") from exc
        self._raise_if_error(payload)
        return self._parse_temple_payload(payload, requested_position=position)

    def set_priests(self, *, city_id: int, position: int, priests: int, **kwargs: Any) -> Any:
        """Assign priests in a city's temple (the temple's "Designar Sacerdotes" form)."""
        params = {
            "action": "CityScreen",
            "function": "assignPriests",
            "cityId": str(city_id),
            "position": str(position),
            "priests": str(max(0, int(priests))),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "temple",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError("Invalid assign priests response", action="assign_priests") from exc
        self._raise_if_error(payload)
        self._remember_action_request(payload)
        return payload

    def get_wonder_state(self, *, island_id: int, city_id: int = 0, **kwargs: Any) -> dict[str, Any]:
        """The island miracle: level, what the next level needs, faith and who takes part."""
        params = {
            "view": "wonder",
            "islandId": str(island_id),
            "backgroundView": "island",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        if city_id:
            params["cityId"] = str(city_id)
            params["currentCityId"] = str(city_id)
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError("Invalid island miracle response", action="wonder_state") from exc
        self._remember_action_request(payload)
        return self.parse_wonder_payload(payload, island_id=island_id)

    def _remember_action_request(self, payload: Any) -> None:
        for item in self._normalize_payload(payload):
            if isinstance(item, list) and len(item) >= 2 and item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token

    @classmethod
    def parse_wonder_payload(cls, payload: Any, *, island_id: int = 0) -> dict[str, Any]:
        template: dict[str, Any] = {}
        html = ""
        for item in cls._normalize_payload(payload):
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] == "updateTemplateData" and isinstance(item[1], dict):
                template = item[1]
            elif item[0] == "changeView" and isinstance(item[1], list) and len(item[1]) >= 2 and item[1][0] == "wonder":
                html = str(item[1][1] or "")
        if not template and not html:
            raise ActionError("Island miracle data missing", action="wonder_state")

        def number(value: Any) -> int:
            digits = re.sub(r"[^\d]", "", str(value or ""))
            return int(digits or "0")

        in_progress = False
        load_js = template.get("load_js") or {}
        try:
            import json as _json

            in_progress = bool(_json.loads(load_js.get("params") or "{}").get("inProgress"))
        except Exception:
            in_progress = False
        max_reached_class = (template.get("maxLevelReached") or {})
        progress = re.search(r'id="js_wonderUpgradeProgress"[^>]*title="([\d.,]+)%', html)
        return {
            "island_id": int(island_id or 0),
            "level": number(template.get("wonderLevel") or template.get("currentWonderLevel")),
            "next_level": number(template.get("nextWonderLevel")),
            "next_level_need": number(template.get("js_donateNextLevel")),
            "donated": number(template.get("js_donatedResources")),
            "max_level_reached": "removeClass" in max_reached_class,
            "belief_level": number(template.get("wonderLevelDisplay")),
            "faith_percent": cls._extract_percent_value(cls._clean_html_text(template.get("wonderBeliefInfo2") or template.get("wonderBeliefTooltip") or "")),
            "upgrading": in_progress,
            "upgrade_percent": cls._extract_decimal(progress.group(1)) if progress else 0.0,
            "participants": cls._parse_participants(html),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def _parse_participants(cls, html: str) -> list[dict[str, Any]]:
        """Rows of "Outros jogadores nesta ilha": a player's later cities leave the name blank (same player)."""
        start = html.find("Outros jogadores nesta ilha")
        if start < 0:
            return []
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html[start:], re.S)
        participants: list[dict[str, Any]] = []
        last_name, last_avatar = "", 0
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if len(cells) < 6:
                continue
            avatar = re.search(r"avatarId=(\d+)", cells[0])
            name = cls._clean_html_text(re.sub(r"&nbsp;", " ", cells[0]))
            avatar_id = int(avatar.group(1)) if avatar else 0
            if not name and avatar_id == last_avatar:
                name = last_name
            last_name, last_avatar = name, avatar_id
            temple = re.search(r"cityId=(\d+)&(?:amp;)?position=(\d+)", row)
            participants.append({
                "player": name,
                "avatar_id": avatar_id,
                "city_name": cls._clean_html_text(cells[1]),
                "city_id": int(temple.group(1)) if temple else 0,
                "temple_position": int(temple.group(2)) if temple else 0,
                "donated": int(re.sub(r"[^\d]", "", cls._clean_html_text(cells[2])) or "0"),
                "priests": int(re.sub(r"[^\d]", "", cls._clean_html_text(cells[3])) or "0"),
                "conversion_percent": cls._extract_percent_value(cls._clean_html_text(cells[4])),
                "island_share_percent": cls._extract_percent_value(cls._clean_html_text(cells[5])),
            })
        return participants

    def _fetch_temple_payload(self, *, city_id: int, position: int) -> Any:
        params = {
            "view": "temple",
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            return resp.json()
        except Exception as exc:
            raise ActionError("Invalid temple miracle response", action="temple_state") from exc

    @staticmethod
    def _normalize_payload(payload: Any) -> list[Any]:
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], list):
            inner = payload[0]
            if inner and isinstance(inner[0], list):
                return inner
        return payload if isinstance(payload, list) else []

    def _raise_if_error(self, payload: Any) -> None:
        updates = self._normalize_payload(payload)
        for item in updates:
            if not isinstance(item, list) or not item:
                continue
            if item[0] != "error":
                continue
            raw = item[1][0] if isinstance(item[1], list) and item[1] else item[1]
            text = self._clean_html_text(raw)
            raise ActionError(text or "Miracle activation failed", action="activate_miracle")

    def _parse_temple_payload(self, payload: Any, *, requested_position: int = 0) -> dict[str, Any]:
        updates = self._normalize_payload(payload)
        template_data: dict[str, Any] = {}
        html = ""
        global_data: dict[str, Any] = {}

        for item in updates:
            if not isinstance(item, list) or len(item) < 2:
                continue
            name = item[0]
            data = item[1]
            if name == "updateTemplateData" and isinstance(data, dict):
                template_data = data
            elif name == "changeView" and isinstance(data, list) and len(data) >= 2 and data[0] == "temple":
                html = str(data[1] or "")
            elif name == "updateGlobalData" and isinstance(data, dict):
                global_data = data
                token = str(data.get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token

        if not template_data:
            raise ActionError("Temple miracle template data missing", action="temple_state")

        background_data = global_data.get("backgroundData") or {}
        city_id = int(background_data.get("id") or 0)
        city_name = str(background_data.get("name") or "")
        island_id = int(background_data.get("islandId") or 0)
        island_name = str(background_data.get("islandName") or "")
        x = int(background_data.get("islandXCoord") or 0) if str(background_data.get("islandXCoord") or "").isdigit() else 0
        y = int(background_data.get("islandYCoord") or 0) if str(background_data.get("islandYCoord") or "").isdigit() else 0

        temple_position = 0
        temple_level = 0
        for building in background_data.get("position") or []:
            if str(building.get("building") or "") != "temple":
                continue
            temple_position = int(building.get("position") or 0)
            temple_level = int(building.get("level") or 0)
            break

        activate_button = template_data.get("js_WonderActivateButton") or {}
        view_button = template_data.get("js_WonderViewButton") or {}
        belief_text = str(template_data.get("wonderBeliefInfo2") or template_data.get("wonderBeliefTooltip") or "")

        slider_data = (((template_data.get("js_TempleSlider") or {}).get("slider")) or {})
        callback_data = slider_data.get("callback_data") or {}

        island_conversion = self._extract_percent(html, r'id="conversionIsland">([\d.,]+)')
        own_conversion_display = self._extract_percent(html, r'id="ownConversion">([\d.,]+)')
        priests = self._extract_int(html, r'id="valuePriests">([\d.]+)')
        citizens = self._extract_int(html, r'id="valueCitizens">([\d.]+)')
        max_priests = int(slider_data.get("max_value") or 0)

        cooldown_text = self._clean_html_text(template_data.get("js_WonderTextCooldownText") or "")
        duration_text = self._clean_html_text(template_data.get("js_WonderTextDurationText") or "")
        effect_text = self._clean_html_text(template_data.get("js_WonderTextEffect") or "")
        description = self._clean_html_text(template_data.get("js_WonderTextDesc") or "")
        wonder_name = self._clean_html_text(template_data.get("js_WonderTextHead") or "")

        return {
            "city_id": city_id,
            "city_name": city_name,
            "island_id": island_id,
            "island_name": island_name,
            "x": x,
            "y": y,
            "temple_position": temple_position or int(requested_position or 0),
            "temple_level": temple_level,
            "wonder_name": wonder_name,
            "wonder_description": description,
            "wonder_effect": effect_text,
            "duration_text": duration_text,
            "cooldown_text": cooldown_text,
            "belief_level": int(template_data.get("wonderLevelDisplay") or 0),
            "belief_percent": self._extract_percent_value(belief_text),
            "belief_text": self._clean_html_text(belief_text),
            "activate_state": str(activate_button.get("buttonState") or ""),
            "activate_href": str(activate_button.get("href") or ""),
            "view_state": str(view_button.get("buttonState") or ""),
            "view_href": str(view_button.get("href") or ""),
            "priests": priests,
            "citizens": citizens,
            "max_priests": max_priests,
            "own_conversion_percent": own_conversion_display,
            "island_conversion_percent": island_conversion,
            # N-141: what converting a share of the city takes
            "citizens_per_priest": int(callback_data.get("citizens_per_priest") or 0),
            "max_inhabitants": int(callback_data.get("max_inhabitants") or 0),
            "max_citizens_on_island": int(callback_data.get("max_citizens_on_island") or 0),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _clean_html_text(raw: Any) -> str:
        text = re.sub(r"<[^>]+>", " ", str(raw or ""))
        text = text.replace("&nbsp;", " ").replace("\\/", "/").replace("\\n", " ").replace("\\", "")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _extract_int(html: str, pattern: str) -> int:
        match = re.search(pattern, html)
        if not match:
            return 0
        digits = re.sub(r"[^\d]", "", match.group(1))
        return int(digits or "0")

    @staticmethod
    def _extract_percent(html: str, pattern: str) -> float:
        match = re.search(pattern, html)
        if not match:
            return 0.0
        return MiracleAction._extract_decimal(match.group(1), default=0.0)

    @staticmethod
    def _extract_percent_value(text: str) -> float:
        match = re.search(r"([\d.,]+)\s*%", str(text or ""))
        if not match:
            return 0.0
        return MiracleAction._extract_decimal(match.group(1), default=0.0)

    @staticmethod
    def _extract_decimal(raw: Any, default: float = 0.0) -> float:
        text = str(raw or "").strip().replace(".", "").replace(",", ".")
        try:
            return float(text)
        except Exception:
            return float(default)
