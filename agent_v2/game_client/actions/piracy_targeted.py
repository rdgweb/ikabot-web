"""Targeted piracy helpers: ranking and piracy raid."""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any

from ..constants import ActionID, GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction


def _extract_js_params(response_data: list) -> dict[str, Any]:
    for entry in response_data:
        if not (isinstance(entry, list) and len(entry) >= 2):
            continue
        if entry[0] != "updateTemplateData":
            continue
        template_data = entry[1]
        if not isinstance(template_data, dict):
            continue
        load_js = template_data.get("load_js") or {}
        params_str = load_js.get("params") if isinstance(load_js, dict) else ""
        if not params_str:
            continue
        try:
            return json.loads(params_str)
        except (TypeError, ValueError):
            continue
    return {}


def _update_action_request_from_json_response(client, data: Any) -> None:
    if not isinstance(data, list):
        return
    for entry in data:
        if isinstance(entry, list) and len(entry) >= 2 and entry[0] == "updateGlobalData" and isinstance(entry[1], dict):
            token = entry[1].get("actionRequest")
            if token:
                client._action_request = str(token)
                return


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


def _extract_feedback(parsed: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in parsed.get("feedback", []) or []:
        if isinstance(item, dict) and item.get("text"):
            out.append(str(item["text"]))
    return out


class PiracyHighscoreAction(BaseAction):
    """Read the highscore strip rendered inside the pirate fortress."""

    def execute(self, city_id: int | str, **kwargs: Any) -> dict[str, Any]:
        params = {
            "view": "pirateFortress",
            "cityId": str(city_id),
            "position": "17",
            "activeTab": "tabRanking",
            "backgroundView": "city",
            "currentCityId": str(city_id),
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
            raise ActionError(f"Failed to parse piracy ranking response: {exc}", action="piracy_highscore")

        _update_action_request_from_json_response(self.client, data)
        js = _extract_js_params(data)
        raw_entries = js.get("highscore") or []
        entries: list[dict[str, Any]] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            score_text = str(item.get("capturePoints") or "0").strip()
            entries.append({
                "place": int(item.get("place") or 0),
                "score_text": score_text,
                "score": int(score_text.replace(".", "").replace(",", "")),
                "player_name": str(item.get("name") or "").strip(),
                "target_city_id": str(item.get("cityId") or ""),
                "target_island_id": str(item.get("islandId") or ""),
                "avatar_id": str(item.get("avatarId") or ""),
                "distance": float(item.get("distance") or 0),
            })

        if not entries:
            raise ActionError("Failed to parse piracy ranking entries", action="piracy_highscore")

        return {
            "city_id": str(city_id),
            "entries": entries,
            "highscore_time_left": int(js.get("highscoreTimeLeft") or 0),
            "self_avatar_id": str(js.get("avatarId") or ""),
        }


class PiracyRaidPreviewAction(BaseAction):
    """Open the targeted pirate raid modal for one enemy city."""

    def execute(
        self,
        source_city_id: int | str,
        destination_city_id: int | str,
        destination_island_id: int | str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = {
            "view": "piracyRaid",
            "isMission": "1",
            "destinationCityId": str(destination_city_id),
            "islandId": str(destination_island_id),
            "backgroundView": "island",
            "currentIslandId": str(destination_island_id),
            "templateView": "cityDetails",
            "cityId": str(source_city_id),
            "currentCityId": str(source_city_id),
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
        html = resp.text
        if "piracyRaid" not in html and "PiracyScreen" not in html and "assalto" not in html.lower():
            raise ActionError("Piracy raid preview did not render the expected modal", action="piracy_raid_preview")

        return {
            "source_city_id": str(source_city_id),
            "destination_city_id": str(destination_city_id),
            "destination_island_id": str(destination_island_id),
            "html": html,
        }


class PiracyRaidAction(BaseAction):
    """Send a targeted pirate raid against one city."""

    def execute(
        self,
        source_city_id: int | str,
        destination_city_id: int | str,
        destination_island_id: int | str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        PiracyRaidPreviewAction(self.client).execute(
            source_city_id=source_city_id,
            destination_city_id=destination_city_id,
            destination_island_id=destination_island_id,
        )
        payload = {
            "action": ActionID.PIRACY,
            "function": "raid",
            "destinationCityId": str(destination_city_id),
            "destinationIslandId": str(destination_island_id),
            "cityId": str(source_city_id),
            "currentCityId": str(source_city_id),
            "backgroundView": "island",
            "currentIslandId": str(destination_island_id),
            "templateView": "piracyRaid",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        data = self.client._ajax("PiracyScreen&function=raid", payload)
        feedback = _extract_feedback(data)
        serialized = json.dumps(data, ensure_ascii=False)
        success = any("executada" in msg.lower() or "executed" in msg.lower() for msg in feedback)
        if not success:
            success = "reload" not in serialized.lower()
        time_remaining = 0
        try:
            state = self.client.get_piracy_state(source_city_id)
            time_remaining = int(state.get("time_remaining") or 0)
            success = success and bool(state.get("mission_active"))
        except Exception:
            pass
        return {
            "ok": success,
            "source_city_id": str(source_city_id),
            "destination_city_id": str(destination_city_id),
            "destination_island_id": str(destination_island_id),
            "feedback": feedback,
            "time_remaining": time_remaining,
        }
