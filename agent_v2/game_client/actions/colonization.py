"""Colonization actions."""

from __future__ import annotations

import re
from html import unescape
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction

_RESOURCE_KEY_MAP = {
    "wood": "cargo_resource",
    "wine": "cargo_tradegood1",
    "marble": "cargo_tradegood2",
    "crystal": "cargo_tradegood3",
    "sulfur": "cargo_tradegood4",
}


def _extract_hidden_inputs(html: str) -> dict[str, str]:
    hidden: dict[str, str] = {}
    for match in re.finditer(
        r'<input[^>]+type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
        html,
        flags=re.IGNORECASE,
    ):
        hidden[unescape(match.group(1))] = unescape(match.group(2))
    return hidden


def _walk_strings(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            found.extend(_walk_strings(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_strings(value))
    return found


def _extract_colonization_html(data: Any) -> str:
    for text in _walk_strings(data):
        if "startColonization" in text or "cargo_people" in text:
            return text
    return ""


def _extract_numeric_input_value(html: str, field_name: str, default: int = 0) -> int:
    pattern = (
        r'<input[^>]+name=["\']%s["\'][^>]*value=["\']([^"\']*)["\']'
        % re.escape(field_name)
    )
    match = re.search(pattern, html, flags=re.IGNORECASE)
    if not match:
        return default
    try:
        return int(str(match.group(1) or "0").replace(".", "").replace(",", ""))
    except Exception:
        return default


def _extract_duration_seconds(text: str) -> int:
    total = 0
    for amount, unit in re.findall(r'(\d+)\s*([DdHhMmSs]|min)', text):
        value = int(amount)
        unit_l = unit.lower()
        if unit_l == "d":
            total += value * 86400
        elif unit_l == "h":
            total += value * 3600
        elif unit_l in {"m", "min"}:
            total += value * 60
        elif unit_l == "s":
            total += value
    return total


def _extract_colonization_eta(html: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", " ", html)
    loading_match = re.search(r"Tempo de carregamento:\s*([^/]+?)\s*/", compact, flags=re.IGNORECASE)
    travel_match = re.search(r"Duração da viagem:\s*([^<]+?)(?:\s+\d{2}\.\d{2}\.\d{4}|\s+Destino|$)", compact, flags=re.IGNORECASE)
    arrival_match = re.search(r"(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})", compact)
    destination_match = re.search(r"Destino\s+([^<]+?)(?:\s{2,}|$)", compact, flags=re.IGNORECASE)
    loading_text = (loading_match.group(1).strip() if loading_match else "")
    travel_text = (travel_match.group(1).strip() if travel_match else "")
    arrival_text = (arrival_match.group(1).strip() if arrival_match else "")
    destination_name = (destination_match.group(1).strip() if destination_match else "")
    return {
        "loading_time_text": loading_text,
        "loading_time_seconds": _extract_duration_seconds(loading_text),
        "travel_time_text": travel_text,
        "travel_time_seconds": _extract_duration_seconds(travel_text),
        "arrival_at_text": arrival_text,
        "destination_name": destination_name,
    }


def _extract_colonization_eta_v2(html: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", " ", html)
    loading_match = (
        re.search(r'id=["\']loadingTime["\'][^>]*>\s*([^<]+?)\s*<', html, flags=re.IGNORECASE)
        or re.search(r'class=["\'][^"\']*loadingTime[^"\']*["\'][^>]*>\s*([^<]+?)\s*<', html, flags=re.IGNORECASE)
        or re.search(r'"loadingTime"\s*:\s*"([^"]+)"', html, flags=re.IGNORECASE)
        or re.search(r"Tempo de carregamento:\s*([^/]+?)\s*/", compact, flags=re.IGNORECASE)
    )
    travel_match = (
        re.search(r'id=["\']journeyTime["\'][^>]*>\s*([^<]+?)\s*<', html, flags=re.IGNORECASE)
        or re.search(r'class=["\'][^"\']*journeyTime[^"\']*["\'][^>]*>\s*([^<]+?)\s*<', html, flags=re.IGNORECASE)
        or re.search(r'"journeyTime"\s*:\s*"([^"]+)"', html, flags=re.IGNORECASE)
        or re.search(r"Dura..o da viagem:\s*([^<]+?)(?:\s+\d{2}\.\d{2}\.\d{4}|\s+Destino|$)", compact, flags=re.IGNORECASE)
        or re.search(r"Duração da viagem:\s*([^<]+?)(?:\s+\d{2}\.\d{2}\.\d{4}|\s+Destino|$)", compact, flags=re.IGNORECASE)
    )
    arrival_match = (
        re.search(r'id=["\']arrival["\'][^>]*>\s*([^<]+?)\s*<', html, flags=re.IGNORECASE)
        or re.search(r"(\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2}:\d{2})", compact)
    )
    destination_match = (
        re.search(r'<li[^>]+class=["\'][^"\']*journeyTarget[^"\']*["\'][^>]*>[\s\S]*?<span[^>]*>\s*Destino\s*</span>\s*([^<]+?)\s*<', html, flags=re.IGNORECASE)
        or re.search(r'id=["\']destination["\'][^>]*>\s*([^<]+?)\s*<', html, flags=re.IGNORECASE)
        or re.search(r'class=["\'][^"\']*destination[^"\']*["\'][^>]*>\s*([^<]+?)\s*<', html, flags=re.IGNORECASE)
        or re.search(r"Destino\s+([^<]+?)(?:\s{2,}|$)", compact, flags=re.IGNORECASE)
    )
    loading_text = (loading_match.group(1).strip() if loading_match else "")
    travel_text = (travel_match.group(1).strip() if travel_match else "")
    arrival_text = (arrival_match.group(1).strip() if arrival_match else "")
    destination_name = (destination_match.group(1).strip() if destination_match else "")
    if loading_text == "-":
        loading_text = "0s"
    return {
        "loading_time_text": loading_text,
        "loading_time_seconds": _extract_duration_seconds(loading_text),
        "travel_time_text": travel_text,
        "travel_time_seconds": _extract_duration_seconds(travel_text),
        "arrival_at_text": arrival_text,
        "destination_name": destination_name,
    }


class ColonizationPreviewAction(BaseAction):
    """Fetch colonization form metadata for one island slot."""

    def execute(
        self,
        source_city_id: int | str,
        island_id: int | str,
        position: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = {
            "view": "colonize",
            "cityId": str(source_city_id),
            "currentCityId": str(source_city_id),
            "islandId": str(island_id),
            "position": str(position),
            "backgroundView": "island",
            "oldBackgroundView": "city",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request(
            "GET",
            self.client._server_url,
            params=params,
            headers=dict(GAME_AJAX_HEADERS),
            timeout=30,
        )
        try:
            data = resp.json()
        except Exception as exc:
            raise ActionError(f"Failed to parse colonization preview response: {exc}", action="colonize_preview")
        html = _extract_colonization_html(data)
        hidden = _extract_hidden_inputs(html)
        if hidden.get("function") != "startColonization":
            raise ActionError("Colonization form not found", action="colonize_preview")

        eta = _extract_colonization_eta_v2(html)
        return {
            "source_city_id": str(source_city_id),
            "island_id": str(island_id),
            "position": int(position),
            "action": hidden.get("action", "transportOperations"),
            "function": hidden.get("function", "startColonization"),
            "cargo_people": int(hidden.get("cargo_people") or 0),
            "cargo_gold": int(hidden.get("cargo_gold") or 0),
            "desired_position": int(hidden.get("desiredPosition") or position),
            "capacity": _extract_numeric_input_value(html, "capacity"),
            "max_capacity": _extract_numeric_input_value(html, "max_capacity"),
            "transporters": _extract_numeric_input_value(html, "transporters"),
            "capacity_per_transport": _extract_numeric_input_value(html, "capacityPerTransport"),
            "hidden_fields": hidden,
            "html": html,
            **eta,
        }


class StartColonizationAction(BaseAction):
    """Start founding a colony on one empty island slot."""

    def execute(
        self,
        source_city_id: int | str,
        island_id: int | str,
        position: int,
        resources: dict[str, int] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        preview = ColonizationPreviewAction(self.client).execute(
            source_city_id=source_city_id,
            island_id=island_id,
            position=position,
        )
        hidden = dict(preview.get("hidden_fields") or {})
        if hidden.get("function") != "startColonization":
            raise ActionError("Colonization form missing startColonization function", action="colonize")

        payload: dict[str, Any] = {
            "action": hidden.get("action", "transportOperations"),
            "function": hidden.get("function", "startColonization"),
            "islandId": hidden.get("islandId", str(island_id)),
            "desiredPosition": hidden.get("desiredPosition", str(position)),
            "cargo_people": hidden.get("cargo_people", "40"),
            "cargo_gold": hidden.get("cargo_gold", "9000"),
            "cityId": str(source_city_id),
            "currentCityId": str(source_city_id),
            "backgroundView": "island",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }

        requested_resources = resources or {}
        for key in _RESOURCE_KEY_MAP.values():
            payload[key] = "0"
        for resource_name, field_name in _RESOURCE_KEY_MAP.items():
            if resource_name in requested_resources:
                payload[field_name] = str(max(0, int(requested_resources[resource_name])))

        data = self.client._ajax(
            "transportOperations&function=startColonization",
            payload,
        )

        feedback = [msg.get("text", "") for msg in data.get("feedback", []) if isinstance(msg, dict)]
        return {
            "ok": True,
            "source_city_id": str(source_city_id),
            "island_id": str(island_id),
            "position": int(position),
            "feedback": feedback,
            "preview": {
                "capacity": preview.get("capacity", 0),
                "max_capacity": preview.get("max_capacity", 0),
                "transporters": preview.get("transporters", 0),
                "loading_time_text": preview.get("loading_time_text", ""),
                "loading_time_seconds": int(preview.get("loading_time_seconds") or 0),
                "travel_time_text": preview.get("travel_time_text", ""),
                "travel_time_seconds": int(preview.get("travel_time_seconds") or 0),
                "arrival_at_text": preview.get("arrival_at_text", ""),
                "destination_name": preview.get("destination_name", ""),
            },
        }
