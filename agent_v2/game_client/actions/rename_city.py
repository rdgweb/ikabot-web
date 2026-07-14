"""Renomear cidade (N-49).

Endpoint real (townHall): POST action=CityScreen function=rename
cityId=<id> position=0 name=<novo nome> (max 15 chars).
"""

from __future__ import annotations

from typing import Any

from ..constants import GAME_AJAX_HEADERS
from .base_action import BaseAction


class RenameCityAction(BaseAction):
    def execute(self, *, city_id: int, name: str, **kwargs: Any) -> dict[str, Any]:
        new_name = str(name or "").strip()[:15]
        if not new_name:
            return {"ok": False, "error": "empty_name"}
        params = {
            "action": "CityScreen",
            "function": "rename",
            "cityId": str(city_id),
            "position": "0",
            "name": new_name,
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "townHall",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        feedbacks: list[dict[str, Any]] = []
        try:
            payload = resp.json()
        except Exception:
            return {"ok": True, "name": new_name, "feedbacks": feedbacks}
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, list) and len(item) >= 2:
                if item[0] == "updateGlobalData" and isinstance(item[1], dict):
                    token = str(item[1].get("actionRequest") or "").strip()
                    if token:
                        self.client._action_request = token
                elif item[0] == "provideFeedback" and isinstance(item[1], list):
                    feedbacks.extend(e for e in item[1] if isinstance(e, dict))
        return {"ok": True, "name": new_name, "feedbacks": feedbacks}
