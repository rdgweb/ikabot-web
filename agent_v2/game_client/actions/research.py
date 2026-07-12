"""Research advisor actions."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction


RESEARCH_BRANCH_LABELS = {
    "seafaring": "Navegacao Maritima",
    "economy": "Economia",
    "knowledge": "Ciencia",
    "military": "Militar",
    "mythology": "Mitologia",
}


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        text = str(raw or "").strip()
        if not text:
            return default
        digits = re.sub(r"[^\d]", "", text)
        return int(digits or default)
    except Exception:
        return default


def _parse_eta(progressbar: Any) -> tuple[int, str]:
    if not isinstance(progressbar, dict):
        return 0, ""
    payload = progressbar.get("progressbar") or {}
    if not isinstance(payload, dict):
        return 0, ""
    enddate = _to_int(payload.get("enddate"), 0)
    currentdate = _to_int(payload.get("currentdate"), 0)
    if not enddate or not currentdate:
        return 0, ""
    seconds = max(0, enddate - currentdate)
    iso = datetime.fromtimestamp(enddate, tz=timezone.utc).isoformat() if enddate else ""
    return seconds, iso


def _parse_research_type(raw: Any) -> str:
    if isinstance(raw, dict):
        raw = raw.get("ajaxrequest") or raw.get("href") or ""
    parsed = urlparse(str(raw or ""))
    params = parse_qs(parsed.query)
    value = (params.get("researchType") or params.get("type") or [""])[0]
    return str(value or "").strip().lower()


class ResearchAction(BaseAction):
    """Read and claim technologies from the research advisor."""

    def get_state(self, *, city_id: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "view": "researchAdvisor",
            "oldView": "updateGlobalData",
            "cityId": str(city_id),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "researchAdvisor",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError("Invalid research advisor response", action="research_state") from exc
        return self._parse_state_payload(payload)

    def discover(self, *, city_id: int, research_type: str, **kwargs: Any) -> dict[str, Any]:
        params = {
            "action": "Advisor",
            "function": "doResearch",
            "type": str(research_type),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "researchAdvisor",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception:
            return {"ok": True, "feedbacks": []}
        feedbacks: list[dict[str, Any]] = []
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token
            elif item[0] == "provideFeedback" and isinstance(item[1], list):
                for entry in item[1]:
                    if isinstance(entry, dict):
                        feedbacks.append(entry)
        return {"ok": True, "payload": payload, "feedbacks": feedbacks}

    @classmethod
    def _parse_state_payload(cls, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, list):
            raise ActionError("Unexpected research payload shape", action="research_state")

        template_data: dict[str, Any] = {}
        background_data: dict[str, Any] = {}
        change_view_html = ""

        for item in payload:
            if not isinstance(item, list) or len(item) < 2:
                continue
            name = item[0]
            data = item[1]
            if name == "updateTemplateData" and isinstance(data, dict):
                template_data = data
            elif name == "updateGlobalData" and isinstance(data, dict):
                token = str(data.get("actionRequest") or "").strip()
                if token:
                    # keep token fresh for following actions
                    pass
                background_data = data.get("backgroundData") or {}
            elif name == "changeView" and isinstance(data, list) and len(data) >= 2 and data[0] == "researchAdvisor":
                change_view_html = str(data[1] or "")

        if not template_data:
            raise ActionError("Research template data missing", action="research_state")

        branches = []
        current_type = ""
        for idx in range(0, 8):
            type_info = template_data.get(f"js_researchAdvisorChangeResearchType{idx}")
            branch_name = str(template_data.get(f"js_researchAdvisorChangeResearchTypeTxt{idx}") or "").strip()
            if not branch_name and not type_info:
                continue
            research_type = _parse_research_type(type_info)
            active_class = str(((template_data.get(f"js_researchAdvisorTypeActiveClass{idx}") or {}).get("class")) or "")
            next_name = str(template_data.get(f"js_researchAdvisorNextResearchName{idx}") or "").strip()
            tooltip_name = str(template_data.get(f"js_researchAdvisorNextResearchTooltipName{idx}") or "").strip()
            short_desc = str(template_data.get(f"js_researchAdvisorNextResearchTooltipShortDesc{idx}") or "").strip()
            cost_entry = template_data.get(f"js_researchAdvisorNextResearchCost{idx}") or {}
            cost_text = str(cost_entry.get("text") or cost_entry or "").strip()
            progress_text_entry = template_data.get(f"js_researchAdvisorProgressTxt{idx}") or {}
            progress_text = str(progress_text_entry.get("text") or progress_text_entry or "").strip()
            eta_seconds, eta_end_at = _parse_eta(template_data.get(f"js_researchAdvisorProgressbar{idx}") or {})
            max_reached = "maximo atingido" in progress_text.lower() or "máximo atingido" in progress_text.lower() or not next_name
            ready = bool(next_name) and not max_reached and eta_seconds <= 0
            active = "active" in active_class.lower()
            if active:
                current_type = research_type
            next_level_match = re.search(r"\((\d+)\)\s*$", next_name)
            branches.append({
                "index": idx,
                "research_type": research_type,
                "branch_name": branch_name or RESEARCH_BRANCH_LABELS.get(research_type, research_type or f"Pesquisa {idx+1}"),
                "next_name": next_name,
                "tooltip_name": tooltip_name,
                "short_desc": short_desc,
                "cost": _to_int(cost_text, 0),
                "cost_text": cost_text,
                "next_level": _to_int(next_level_match.group(1), 0) if next_level_match else 0,
                "eta_seconds": eta_seconds,
                "eta_end_at": eta_end_at,
                "ready": ready,
                "max_reached": max_reached,
                "active": active,
                "progress_text": progress_text,
            })

        city_name = str(background_data.get("name") or "").strip()
        island_name = str(background_data.get("islandName") or "").strip()
        city_id_value = _to_int(background_data.get("id") or template_data.get("cityId") or 0, 0)

        current_label = str(template_data.get("js_researchAdvisorCurrResearchType") or "").strip()
        research_href = ((template_data.get("js_researchAdvisorConservationLink") or {}).get("href")) or ""
        conservation_type = _parse_research_type(research_href)
        if conservation_type and not current_type:
            current_type = conservation_type

        return {
            "city_id": city_id_value,
            "city_name": city_name,
            "island_name": island_name,
            "current_research_type": current_type,
            "current_research_label": current_label or RESEARCH_BRANCH_LABELS.get(current_type, ""),
            "conservation_type": conservation_type,
            "branches": branches,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "html_excerpt_present": bool(change_view_html),
        }
