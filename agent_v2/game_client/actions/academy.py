"""Academy actions: scientist allocation and crystal experiments."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        text = str(raw or "").strip()
        if not text:
            return default
        digits = re.sub(r"[^\d-]", "", text)
        return int(digits or default)
    except Exception:
        return default


def _clean_html_text(raw: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(raw or ""))
    text = text.replace("&nbsp;", " ").replace("\\/", "/").replace("\\n", " ").replace("\\", "")
    return re.sub(r"\s+", " ", text).strip()


def _parse_duration_seconds(raw: str) -> int:
    text = _clean_html_text(raw).lower()
    if not text:
        return 0
    total = 0
    matched = False
    patterns = (
        (r"(\d+)\s*d", 86400),
        (r"(\d+)\s*h", 3600),
        (r"(\d+)\s*m", 60),
        (r"(\d+)\s*s", 1),
    )
    for pattern, factor in patterns:
        found = re.search(pattern, text)
        if not found:
            continue
        matched = True
        total += int(found.group(1)) * factor
    return total if matched else 0


class AcademyAction(BaseAction):
    """Interact with academy sliders and crystal experiments."""

    def get_state(self, *, city_id: int, position: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "view": "academy",
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "academy",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError("Invalid academy response", action="academy_state") from exc
        return self._parse_state_payload(payload)

    def set_scientists(self, *, city_id: int, position: int, scientists: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "action": "IslandScreen",
            "function": "workerPlan",
            "screen": "academy",
            "position": str(position),
            "cityId": str(city_id),
            "s": str(max(0, int(scientists))),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "academy",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        return self._parse_generic_payload(resp)

    def buy_research(
        self,
        *,
        city_id: int,
        position: int,
        use_athena_scroll: bool = False,
        pay_with_ambrosia: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = {
            "action": "CityScreen",
            "function": "buyResearch",
            "useAthenasScroll": "1" if use_athena_scroll else "0",
            "payWithAmbrosia": "1" if pay_with_ambrosia else "0",
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "academy",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        return self._parse_generic_payload(resp)

    @staticmethod
    def _normalize_payload(payload: Any) -> list[Any]:
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], list):
            first = payload[0]
            if first and isinstance(first[0], list):
                return first
        return payload if isinstance(payload, list) else []

    def _parse_generic_payload(self, resp) -> dict[str, Any]:
        try:
            payload = resp.json()
        except Exception:
            return {"ok": True}
        updates = self._normalize_payload(payload)
        feedbacks = []
        for item in updates:
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token
            elif item[0] == "provideFeedback":
                payload = item[1]
                if isinstance(payload, list):
                    for entry in payload:
                        if isinstance(entry, dict):
                            feedbacks.append(entry)
        return {"ok": True, "payload": updates, "feedbacks": feedbacks}

    def _parse_state_payload(self, payload: Any) -> dict[str, Any]:
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
            elif name == "changeView" and isinstance(data, list) and len(data) >= 2 and data[0] == "academy":
                html = str(data[1] or "")
            elif name == "updateGlobalData" and isinstance(data, dict):
                global_data = data
                token = str(data.get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token

        if not template_data:
            raise ActionError("Academy template data missing", action="academy_state")

        background_data = global_data.get("backgroundData") or {}
        header_data = global_data.get("headerData") or {}
        current_resources = header_data.get("currentResources") or {}

        slider = (((template_data.get("js_AcademySlider") or {}).get("slider")) or {})
        callback_data = slider.get("callback_data") or {}

        city_id = _to_int(template_data.get("cityId") or background_data.get("id") or 0, 0)
        city_name = str(background_data.get("name") or "").strip()
        island_name = str(background_data.get("islandName") or "").strip()

        current_scientists = _to_int(template_data.get("valueWorkers") or slider.get("ini_value") or 0, 0)
        max_scientists = _to_int(slider.get("max_value") or current_scientists, current_scientists)
        citizens = _to_int(template_data.get("valueCitizens") or callback_data.get("startCitizens") or 0, 0)
        cost_per_scientist = _to_int(callback_data.get("costPerScientist") or 0, 0)
        work_costs = _to_int(template_data.get("valueWorkCosts") or 0, 0)
        basic_production_text = str((template_data.get("js_academy_research_tooltip_basic_production") or {}).get("text") or "").strip()
        total_production = _to_int((template_data.get("js_academy_research_tooltip_total_production") or {}).get("text") or 0, 0)

        crystal_amount = _to_int(current_resources.get("3") or 0, 0)
        research_points_gain = 0
        crystal_cost = 0
        discounted_crystal_cost = 0
        use_scroll_available = False
        experiment_available = False
        cooldown_text = ""

        if html:
            gain_match = re.search(r"Rendimento cient[ií]fico:\s*</span>\s*<span>([\d.]+)</span>", html, re.I)
            if gain_match:
                research_points_gain = _to_int(gain_match.group(1), 0)

            cost_matches = re.findall(r"<span>([\d.]+)</span>\s*<img[^>]+cdn1e/417b4059940b2ae2680c070a197d8c", html, re.I)
            if cost_matches:
                crystal_cost = _to_int(cost_matches[0], 0)
                if len(cost_matches) >= 3:
                    discounted_crystal_cost = _to_int(cost_matches[2], 0)
            discounted_match = re.search(r'class="discounted-cost">([\d.]+)</span>', html, re.I)
            if discounted_match:
                discounted_crystal_cost = _to_int(discounted_match.group(1), discounted_crystal_cost)

            experiment_available = "conductExperiment(0)" in html
            use_scroll_available = "conductExperiment(1)" in html

            cooldown_patterns = (
                r"precisam de ([^<]+?) para organizar o laborat[oó]rio",
                r"Tempo restante[^:]*:\s*([^<]+)",
                r"cooldown[^>]*>\s*([^<]+)",
            )
            for pattern in cooldown_patterns:
                match = re.search(pattern, html, re.I)
                if match:
                    cooldown_text = _clean_html_text(match.group(1))
                    if cooldown_text:
                        break

        cooldown_seconds = _parse_duration_seconds(cooldown_text)
        if not experiment_available and not cooldown_text and crystal_cost and crystal_amount < crystal_cost:
            cooldown_text = "Cristal insuficiente"

        experiment_reason = ""
        if experiment_available:
            experiment_reason = "Disponivel"
        elif cooldown_text:
            experiment_reason = cooldown_text
        elif crystal_cost and crystal_amount < crystal_cost:
            experiment_reason = "Cristal insuficiente"
        else:
            experiment_reason = "Experimento indisponivel"

        return {
            "city_id": city_id,
            "city_name": city_name,
            "island_name": island_name,
            "scientists": {
                "current": current_scientists,
                "max": max_scientists,
                "citizens": citizens,
                "cost_per_scientist": cost_per_scientist,
                "work_costs": work_costs,
                "basic_production_text": basic_production_text,
                "total_production": total_production,
            },
            "experiment": {
                "available": experiment_available,
                "reason": experiment_reason,
                "research_points_gain": research_points_gain,
                "crystal_cost": crystal_cost,
                "discounted_crystal_cost": discounted_crystal_cost,
                "use_scroll_available": use_scroll_available,
                "cooldown_text": cooldown_text,
                "cooldown_seconds": cooldown_seconds,
            },
            "resources": {
                "crystal": crystal_amount,
                "gold": _to_int(header_data.get("gold") or 0, 0),
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
