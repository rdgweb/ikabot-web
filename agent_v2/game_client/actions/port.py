"""Porto: comprar barcos mercantes (transporter) e cargueiros (freighter).

Mapeado por captura real (N-48):
  view=port&activeTab=tabBuyTransporter devolve em updateTemplateData:
    js_transporterCosts / js_freighterCosts  -> custo do PROXIMO barco (ouro)
    js_maxTransporter                         -> maximo de mercantes
    bonusShipTableTransporters                -> quantos ja tem
    js_buyTransporterAction.buttonState       -> enabled/hidden/disabled
  Compra (1 barco por request):
    ?action=CityScreen&function=increaseTransporter&cityId=<id>&position=1
    ?action=CityScreen&function=increaseFreighter&cityId=<id>&position=1

Custo do n-esimo barco: floor(13500 * 1.03**n - 13425).
"""

from __future__ import annotations

import math
import re
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction


def ship_cost(n: int) -> int:
    """Custo em ouro do n-esimo barco (mercante ou cargueiro seguem a mesma curva)."""
    if n <= 0:
        return 0
    return int(math.floor(13500 * (1.03 ** n) - 13425))


def ships_total_cost(start_count: int, amount: int) -> int:
    """Custo total para comprar `amount` barcos a partir de quem ja tem `start_count`."""
    return sum(ship_cost(start_count + i + 1) for i in range(max(0, amount)))


def _to_int(raw: Any) -> int:
    try:
        return int(re.sub(r"[^\d]", "", str(raw or "0")) or 0)
    except Exception:
        return 0


class PortAction(BaseAction):
    """Le o estado do porto e compra barcos de transporte."""

    def get_state(self, *, city_id: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "view": "port",
            "position": "1",
            "activeTab": "tabBuyTransporter",
            "cityId": str(city_id),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "port",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError("Invalid port response", action="port_state") from exc

        td: dict[str, Any] = {}
        gold = 0
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] == "updateTemplateData" and isinstance(item[1], dict):
                td = item[1]
            elif item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token
                gold = _to_int((item[1].get("headerData") or {}).get("gold"))

        def _state(action_key):
            v = td.get(action_key) or {}
            return str(v.get("buttonState") or "")

        return {
            "city_id": int(city_id),
            "gold": gold,
            "transporter_count": _to_int((td.get("bonusShipTableTransporters") or {}).get("text")),
            "transporter_max": _to_int(td.get("js_maxTransporter")),
            "transporter_next_cost": _to_int(td.get("js_transporterCosts")),
            "transporter_buyable": _state("js_buyTransporterAction") == "enabled",
            "freighter_next_cost": _to_int(td.get("js_freighterCosts")),
            "freighter_buyable": _state("js_buyFreighterAction") == "enabled",
        }

    def buy_one(self, *, city_id: int, kind: str, **kwargs: Any) -> dict[str, Any]:
        """Compra 1 barco. kind: 'transporter' (mercante) ou 'freighter' (cargueiro)."""
        func = "increaseFreighter" if kind == "freighter" else "increaseTransporter"
        params = {
            "action": "CityScreen",
            "function": func,
            "cityId": str(city_id),
            "position": "1",
            "activeTab": "tabBuyTransporter",
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "port",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        feedbacks: list[dict[str, Any]] = []
        try:
            payload = resp.json()
        except Exception:
            return {"ok": True, "feedbacks": feedbacks, "raw": resp.text[:300]}
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, list) and len(item) >= 2:
                if item[0] == "updateGlobalData" and isinstance(item[1], dict):
                    token = str(item[1].get("actionRequest") or "").strip()
                    if token:
                        self.client._action_request = token
                elif item[0] == "provideFeedback" and isinstance(item[1], list):
                    feedbacks.extend(e for e in item[1] if isinstance(e, dict))
        return {"ok": True, "feedbacks": feedbacks}
