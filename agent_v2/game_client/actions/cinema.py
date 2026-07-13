"""Cineteatro (cinema) actions.

Mapeado por captura real (N-29): view=cinema devolve em updateTemplateData:
  - rewards: [{key, bonusId, available, nextAvailable, bonusText, title}, ...]
    (51=producao +10%/12h, 52=luxo +10%/12h, 53=favor, 54=ambrosia)
  - videoID: id da sessao atual (muda a cada abertura da tela)
A coleta e um POST action=AdVideoRewardAction function=RequestBonus com o
bonusId da recompensa e o videoId da tela.
"""

from __future__ import annotations

import re
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction


class CinemaAction(BaseAction):
    """Le o estado do cineteatro e coleta as recompensas disponiveis."""

    def get_state(self, *, city_id: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "view": "cinema",
            "cityId": str(city_id),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError("Invalid cinema response", action="cinema_state") from exc

        template_data: dict[str, Any] = {}
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] == "updateTemplateData" and isinstance(item[1], dict):
                template_data = item[1]
            elif item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token

        rewards = []
        for entry in template_data.get("rewards") or []:
            if not isinstance(entry, dict):
                continue
            rewards.append({
                "key": str(entry.get("key") or ""),
                "bonus_id": int(entry.get("bonusId") or 0),
                "available": bool(entry.get("available")),
                "next_available": int(entry.get("nextAvailable") or 0),
                "title": str(entry.get("title") or ""),
                "bonus_text": re.sub(r"<[^>]+>", "", str(entry.get("bonusText") or "")).strip(),
            })

        return {
            "city_id": int(city_id),
            "video_id": int(template_data.get("videoID") or 0),
            "rewards": rewards,
            "has_template": bool(template_data),
        }

    def _call(self, *, city_id: int, function: str, extra: dict[str, str] | None = None) -> Any:
        params = {
            "view": "noViewChange",
            "action": "AdVideoRewardAction",
            "function": function,
            "cityId": str(city_id),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        params.update(extra or {})
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception:
            return None
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token
        return payload

    def claim(self, *, city_id: int, bonus_id: int, video_id: int, **kwargs: Any) -> dict[str, Any]:
        """Fluxo real do cineteatro (mapeado do cinema.js):
        watchVideo(videoId) -> requestBonus(bonusId, videoId).
        A recompensa so e concedida apos o watchVideo registrar a sessao.
        """
        self._call(city_id=city_id, function="watchVideo", extra={"videoId": str(int(video_id))})
        payload = self._call(
            city_id=city_id,
            function="requestBonus",
            extra={"bonusId": str(int(bonus_id)), "videoId": str(int(video_id))},
        )
        feedbacks: list[dict[str, Any]] = []
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "provideFeedback" and isinstance(item[1], list):
                for entry in item[1]:
                    if isinstance(entry, dict):
                        feedbacks.append(entry)
        return {
            "ok": True,
            "feedbacks": feedbacks,
            "payload_names": [i[0] for i in payload if isinstance(i, list) and i] if isinstance(payload, list) else [],
        }
