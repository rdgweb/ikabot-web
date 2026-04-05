"""Temple miracle actions."""

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
