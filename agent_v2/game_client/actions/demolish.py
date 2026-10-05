"""Demolir edificio -- reduzir niveis ou demolir completamente (N-57).

Fluxo real do jogo (capturado 2026-09-23 na BlackShadow701 s78, so GET das
telas de confirmacao; nada foi demolido na captura):

  Reduzir 1 nivel (sem captcha):
    tela  GET  view=buildings_demolition&cityId=C&position=P&level=L&templatePosition=P&ajax=1
    acao  POST action=CityScreen&function=demolishBuilding&level=L&cityId=C&position=P
               &backgroundView=city&currentCityId=C&actionRequest=T&ajax=1
    No nivel 0 o edificio e destruido e a posicao volta a ficar livre.

  Demolir completamente (com captcha):
    tela  GET  view=completeBuildingDemolition&cityId=C&position=P&level=L&templatePosition=P&ajax=1
               -> <img id="js_captchaImage" src="data:image/png;base64,...">
    acao  POST action=CityScreen, function=completeDemolishBuilding, level, cityId,
               position, captcha=<texto da imagem>

  level = nivel atual do edificio.

demolish_refusal() e a trava: so se demole o edificio que o usuario viu, no
nivel que ele viu. Qualquer diferenca no jogo cancela o item.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)

NOT_DEMOLISHABLE = {"townHall"}

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_CAPTCHA_SRC = re.compile(r"data:image\\?/png;base64,([A-Za-z0-9+/=\\]+)")


def building_name(raw: Any) -> str:
    """Building key of a city position as the game sends it ("empty" for a free spot)."""
    name = str(raw or "").replace("constructionSite", "").strip()
    return "empty" if (not name or "buildingGround" in name) else name


def position_level(pos: Any) -> int:
    try:
        return int((pos or {}).get("level") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def demolish_refusal(pos: Any, expected_building: str, expected_level: int) -> str:
    """Why the building at this position must NOT be demolished ("" = go ahead)."""
    if not isinstance(pos, dict) or pos.get("buildingId") is None:
        return "posicao_vazia"
    raw = str(pos.get("building") or "")
    name = building_name(raw)
    if name == "empty":
        return "posicao_vazia"
    if name != str(expected_building or ""):
        return "edificio_diferente"
    if name in NOT_DEMOLISHABLE:
        return "nao_demolivel"
    if position_level(pos) != int(expected_level):
        return "nivel_diferente"
    if "constructionSite" in raw or pos.get("completed") or pos.get("inConstructionList"):
        return "em_obra"
    if pos.get("isBusy"):
        return "ocupado"
    return ""


def extract_captcha_png(text: str) -> bytes | None:
    """PNG of the captcha embedded in the confirmation window (None = no image)."""
    match = _CAPTCHA_SRC.search(text or "")
    if not match:
        return None
    try:
        decoded = base64.b64decode(match.group(1).replace("\\", ""))
    except Exception:
        return None
    return decoded if decoded[:8] == _PNG_MAGIC and len(decoded) > 8 else None


class DemolishBuildingAction(BaseAction):
    """The game's demolish requests. Callers verify the result by re-reading the city."""

    def _base(self, city_id: int | str) -> dict[str, str]:
        return {
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }

    def _send(self, method: str, **kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        """Request outside client._request: the confirmation window carries a captcha
        on purpose, which the generic captcha detection would treat as an error."""
        self.client._enforce_delay()
        resp = self.client.session.request(
            method, self.client._server_url, headers=dict(GAME_AJAX_HEADERS), timeout=30, **kwargs
        )
        text = resp.text
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError(f"Resposta invalida do jogo: {exc}", action="demolish")
        feedbacks: list[dict[str, Any]] = []
        for item in payload if isinstance(payload, list) else []:
            if not (isinstance(item, list) and len(item) >= 2):
                continue
            if item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token
            elif item[0] == "provideFeedback":
                feedbacks.extend(fb for fb in (item[1] or []) if isinstance(fb, dict))
        return text, feedbacks

    def confirmation(self, city_id: int | str, position: int, level: int, *, complete: bool = False) -> dict[str, Any]:
        """Open the game's confirmation window (read-only). Never demolishes."""
        view = "completeBuildingDemolition" if complete else "buildings_demolition"
        text, _ = self._send("GET", params={
            "view": view,
            "cityId": str(city_id),
            "position": str(position),
            "level": str(level),
            "templatePosition": str(position),
            **self._base(city_id),
        })
        function = "completeDemolishBuilding" if complete else "demolishBuilding"
        return {
            "window": function in text,
            "captcha_image": extract_captcha_png(text) if complete else None,
        }

    def one_level(self, city_id: int | str, position: int, level: int) -> list[dict[str, Any]]:
        """Take ONE level off the building (level = its current level)."""
        logger.info("Demolish: one level at position %s of city %s (level %s)", position, city_id, level)
        _, feedbacks = self._send("POST", data={
            "action": "CityScreen",
            "function": "demolishBuilding",
            "level": str(level),
            "cityId": str(city_id),
            "position": str(position),
            **self._base(city_id),
        })
        return feedbacks

    def complete(self, city_id: int | str, position: int, level: int, captcha: str) -> list[dict[str, Any]]:
        """Demolish the whole building with the solved captcha."""
        logger.info("Demolish: complete at position %s of city %s (level %s)", position, city_id, level)
        _, feedbacks = self._send("POST", data={
            "action": "CityScreen",
            "function": "completeDemolishBuilding",
            "level": str(level),
            "cityId": str(city_id),
            "position": str(position),
            "captcha": str(captcha),
            **self._base(city_id),
        })
        return feedbacks
