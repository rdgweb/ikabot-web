"""Recursos premium: inventario de itens + negociante premium.

Mapeado por captura real (N-32, conta HAVIT):

Inventario (view=inventory): o HTML embute "inventory": [ {..}, .. ] com, por item:
  itemId, type, name, desc, comment, count, categoryId, cssClass,
  activationUrl (?action=InventoryAction&function=activateItem&itemId=..),
  canBeUsedFromInventory, requireCity, requireGod, canBeActivated, securityQuestion.
Ativacao: POST action=InventoryAction function=activateItem itemId=<id>
  (+ itemTargetCity quando requireCity, + itemTargetGod quando requireGod).

Negociante premium (view=premiumTrader): troca um recurso por outro pagando
Ambrosia. Form id=trader -> POST action=Premium function=trade com
send<resource>/<resource> + diff<resource> + displayedPrice + position + cityId.

IMPORTANTE: este modulo apenas LE e PREPARA. As funcoes de execucao existem
mas nao sao chamadas por nenhum runner automatico — uso e sob confirmacao
explicita (ver N-32).
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction

RESOURCE_KEYS = ("resource", "wine", "marble", "crystal", "sulfur")


def _clean(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(raw or ""))
    text = text.replace("&nbsp;", " ").replace("\\/", "/").replace("\\", "")
    return re.sub(r"\s+", " ", text).strip()


class PremiumInventoryAction(BaseAction):
    """Le o inventario de itens premium do jogador."""

    def get_inventory(self, *, city_id: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "view": "inventory",
            "cityId": str(city_id),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        raw = resp.text
        # atualizar token
        for item in (json.loads(raw) if raw.strip().startswith("[") else []):
            if isinstance(item, list) and len(item) >= 2 and item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token

        match = re.search(r'"inventory":\s*(\[\{.*?\}\])', raw, re.DOTALL)
        if not match:
            return {"city_id": int(city_id), "items": []}

        try:
            raw_items = json.loads(match.group(1))
        except Exception as exc:
            raise ActionError("Failed to parse inventory json", action="premium_inventory") from exc

        items = []
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            items.append({
                "item_id": int(it.get("itemId") or 0),
                "type": str(it.get("type") or ""),
                "name": _clean(it.get("name") or ""),
                "comment": _clean(it.get("comment") or ""),
                "description": _clean(it.get("desc") or ""),
                "amount_in_item": int(it.get("amountInThisItem") or 0),
                "count": int(str(it.get("count") or "0").strip() or 0),
                "category_id": int(it.get("categoryId") or 0),
                "css_class": str(it.get("cssClass") or ""),
                "activation_url": str(it.get("activationUrl") or ""),
                "can_use_from_inventory": bool(it.get("canBeUsedFromInventory")),
                "can_be_activated": bool(it.get("canBeActivated")),
                "require_city": bool(it.get("requireCity")),
                "require_god": bool(it.get("requireGod")),
                "security_question": _clean(it.get("securityQuestion") or ""),
            })
        return {"city_id": int(city_id), "items": items}

    def activate_item(self, *, item_id: int, city_id: int, target_city_id: int | None = None,
                      target_god: int | None = None, **kwargs: Any) -> dict[str, Any]:
        """Ativa um item do inventario. NAO e chamado por runner automatico."""
        params = {
            "action": "InventoryAction",
            "function": "activateItem",
            "itemId": str(int(item_id)),
            "cityId": str(city_id),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        if target_city_id is not None:
            params["itemTargetCity"] = str(int(target_city_id))
        if target_god is not None:
            params["itemTargetGod"] = str(int(target_god))
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        feedbacks: list[dict[str, Any]] = []
        try:
            payload = resp.json()
        except Exception:
            return {"ok": True, "feedbacks": feedbacks, "raw": resp.text[:400]}
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, list) and len(item) >= 2:
                if item[0] == "updateGlobalData" and isinstance(item[1], dict):
                    token = str(item[1].get("actionRequest") or "").strip()
                    if token:
                        self.client._action_request = token
                elif item[0] == "provideFeedback" and isinstance(item[1], list):
                    feedbacks.extend(e for e in item[1] if isinstance(e, dict))
        return {"ok": True, "feedbacks": feedbacks}


class PremiumTraderAction(BaseAction):
    """Le o estado do negociante premium (troca de recursos por Ambrosia)."""

    def get_state(self, *, city_id: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "view": "premiumTrader",
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
            raise ActionError("Invalid premiumTrader response", action="premium_trader") from exc

        td: dict[str, Any] = {}
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, list) and len(item) >= 2:
                if item[0] == "updateTemplateData" and isinstance(item[1], dict):
                    td = item[1]
                elif item[0] == "updateGlobalData" and isinstance(item[1], dict):
                    token = str(item[1].get("actionRequest") or "").strip()
                    if token:
                        self.client._action_request = token

        def _int(raw: Any) -> int:
            return int(re.sub(r"[^\d]", "", str(raw or "0")) or 0)

        stock = {
            "resource": _int(td.get("js_start_resource")),
            "wine": _int(td.get("js_start_wine")),
            "marble": _int(td.get("js_start_marble")),
            "crystal": _int(td.get("js_start_crystal")),
            "sulfur": _int(td.get("js_start_sulfur")),
        }
        ambrosia_available = _int((td.get("js_available_premium_trader") or {}).get("text"))
        price = _int((td.get("js_displayedPrice") or {}).get("value"))
        return {
            "city_id": int(td.get("js_cityId", {}).get("value") or city_id),
            "ambrosia_available": ambrosia_available,
            "trade_price_ambrosia": price,
            "stock": stock,
        }

    def trade(self, *, city_id: int, send: dict[str, int], receive: dict[str, int],
              displayed_price: int, position: int = 0, **kwargs: Any) -> dict[str, Any]:
        """Executa uma troca no negociante premium. NAO e chamado por runner automatico."""
        params: dict[str, str] = {
            "action": "Premium",
            "function": "trade",
            "cityId": str(city_id),
            "position": str(int(position)),
            "displayedPrice": str(int(displayed_price)),
            "oldView": "",
            "wineOut": "0",
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        for key in RESOURCE_KEYS:
            params[f"send{key}"] = str(int(send.get(key, 0)))
            params[key] = str(int(receive.get(key, 0)))
            params[f"diff{key}"] = str(int(receive.get(key, 0)) - int(send.get(key, 0)))
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception:
            return {"ok": True, "raw": resp.text[:400]}
        feedbacks = []
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "provideFeedback" and isinstance(item[1], list):
                feedbacks.extend(e for e in item[1] if isinstance(e, dict))
        return {"ok": True, "feedbacks": feedbacks}
