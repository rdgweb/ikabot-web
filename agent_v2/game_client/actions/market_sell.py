"""Vender para um pedido de compra do mercado geral (N-87).

Tela do jogo (capturada 2026-10-05 na HAVIT s78, somente leitura):

  A lista type=333 mostra quem quer comprar. O link da linha abre
    view=takeOffer&destinationCityId=COMPRADOR&cityId=NOSSA&position=P&type=333&resource=R
  cujo formulario (id=transportForm) e o mesmo da compra, com:
    action=transportOperations  function=sellGoodsAtAnotherBranchOffice
    cityId (nossa cidade)  destinationCityId (cidade do comprador)  position  type=333
    avatar2Name  city2Name  oldView=branchOffice  activeTab=bargain
    tradegoodNPrice / resourcePrice   -> "Preco Minimo": so vende se, na chegada, o
                                         comprador ainda pagar pelo menos isso
    cargo_tradegoodN / cargo_resource -> quanto vender
    transporters, capacity, max_capacity, normalTransportersMax, premiumTransporter...
  O formulario lista todos os recursos que aquele comprador pede naquela cidade.
  O estoque da nossa cidade vem em updateGlobalData.headerData.currentResources.

  A troca so se completa quando os barcos chegam ("primeiro a chegar, primeiro a levar").
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from services.resource_transport import (
    _capacity_step_from_percent,
    _parse_free_transporters,
    _parse_ship_capacity,
)

from ..constants import ActionID, GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .market import MAX_OFFER_PAGES, OFFERS_PER_PAGE, BuyAction
from .market_scan import OFFER_TYPE_BUY, RESOURCE_STRS, MarketScanAction, parse_offer_rows

logger = logging.getLogger(__name__)

_ACTION = "sellGoodsAtAnotherBranchOffice"
_HIDDEN = r'name="{name}"\s+value="([^"]*)"'


def _price_field(resource_idx: int) -> str:
    return "resourcePrice" if int(resource_idx) == 0 else f"tradegood{int(resource_idx)}Price"


def _cargo_field(resource_idx: int) -> str:
    return "cargo_resource" if int(resource_idx) == 0 else f"cargo_tradegood{int(resource_idx)}"


def parse_city_stock(payload: Any) -> dict[int, int]:
    """Stock of the current city from an AJAX response: {resource_idx: amount}."""
    for entry in payload if isinstance(payload, list) else []:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2 and entry[0] == "updateGlobalData":
            header = entry[1].get("headerData") if isinstance(entry[1], dict) else None
            current = header.get("currentResources") if isinstance(header, dict) else None
            if isinstance(current, dict):
                stock: dict[int, int] = {}
                for idx, key in RESOURCE_STRS.items():
                    try:
                        stock[idx] = int(float(current.get(key) or 0))
                    except (TypeError, ValueError):
                        stock[idx] = 0
                return stock
    return {}


class SellToRequestAction(BuyAction):
    """Sell goods to another player's buy request. Posts nothing when dry_run is set."""

    def find_request(self, city_id: int, bo_pos: int, buyer_city_id: int, resource_idx: int) -> dict[str, Any] | None:
        """The buyer city's request for this resource, as listed from our market now."""
        scan = MarketScanAction(self.client)
        resource_str = RESOURCE_STRS.get(int(resource_idx), "resource")
        found = None
        page = 0
        for page in range(MAX_OFFER_PAGES):
            html = scan._listing(city_id, bo_pos, resource_str, OFFER_TYPE_BUY, page * OFFERS_PER_PAGE)
            for row in parse_offer_rows(html):
                if row["city_id"] == int(buyer_city_id) and row["resource_idx"] == int(resource_idx):
                    found = row
                    break
            if found or f"offset={(page + 1) * OFFERS_PER_PAGE}" not in html:
                break
        if page:
            # the game keeps the last page looked at: leave it on the first one
            scan._listing(city_id, bo_pos, resource_str, OFFER_TYPE_BUY, 0)
        return found

    def _take_request(self, city_id: int, bo_pos: int, buyer_city_id: int, resource_str: str) -> tuple[str, dict[int, int]]:
        params = {
            "view": "takeOffer",
            "destinationCityId": buyer_city_id,
            "oldView": "branchOffice",
            "activeTab": "bargain",
            "cityId": city_id,
            "position": bo_pos,
            "type": OFFER_TYPE_BUY,
            "resource": resource_str,
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": "branchOffice",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            stock = parse_city_stock(resp.json())
        except Exception:
            stock = {}
        return self._extract_html_from_response(resp), stock

    def execute(  # type: ignore[override]
        self,
        city_id: int,
        branchoffice_pos: int,
        buyer_city_id: int,
        resource_idx: int,
        amount: int,
        *,
        min_unit_price: int = 0,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Sell up to `amount` to the buyer city's request.

        The amount is limited to what the buyer asks for, to the city stock and to the
        free transporters. Returns what was (or, in dry_run, would be) sent:
        {"amount", "unit_price", "min_unit_price", "ships", "buyer_city", "buyer_player",
         "wanted", "stock", "dry_run"}.
        """
        resource_idx = int(resource_idx)
        resource_str = RESOURCE_STRS.get(resource_idx, "resource")
        request = self.find_request(city_id, branchoffice_pos, buyer_city_id, resource_idx)
        if request is None:
            raise ActionError(
                f"o pedido de compra da cidade {buyer_city_id} nao esta mais na lista do mercado (atendido, retirado ou fora do alcance)",
                action=_ACTION,
            )

        take_html, stock = self._take_request(city_id, branchoffice_pos, buyer_city_id, resource_str)
        prices: dict[str, Any] = {}
        for idx, price in re.findall(r'"tradegood(\d)Price"\s+value="(\d+)"', take_html):
            prices[f"tradegood{idx}Price"] = int(price)
            prices[f"cargo_tradegood{idx}"] = 0
        wood_price = re.search(r'"resourcePrice"\s+value="(\d+)"', take_html)
        if wood_price:
            prices["resourcePrice"] = int(wood_price.group(1))
            prices["cargo_resource"] = 0
        if _price_field(resource_idx) not in prices:
            raise ActionError(
                f"a tela de venda para a cidade {buyer_city_id} nao pede mais este recurso",
                action=_ACTION,
            )

        unit_price = int(prices[_price_field(resource_idx)])     # what the buyer pays right now
        min_unit_price = max(0, int(min_unit_price or 0))
        if min_unit_price and unit_price < min_unit_price:
            raise ActionError(
                f"o comprador paga {unit_price} de ouro por unidade, abaixo do preco minimo aceito ({min_unit_price})",
                action=_ACTION,
            )

        wanted = int(request.get("amount") or 0)
        in_stock = int(stock.get(resource_idx, 0)) if stock else None
        ships_available = _parse_free_transporters(take_html, use_freighters=False)
        ship_capacity = max(1, _parse_ship_capacity(take_html, ships_available, use_freighters=False))
        sell = max(0, int(amount))
        if wanted > 0:
            sell = min(sell, wanted)
        if in_stock is not None:
            sell = min(sell, in_stock)
        sell = min(sell, ships_available * ship_capacity)
        if sell <= 0:
            raise ActionError(
                f"nada a vender: pedido {amount}, o comprador quer {wanted}, estoque na cidade {in_stock}, "
                f"barcos livres {ships_available} x {ship_capacity}",
                action=_ACTION,
            )
        ships = max(1, math.ceil(sell / ship_capacity))

        def _hidden(name: str, default: str) -> str:
            match = re.search(_HIDDEN.format(name=name), take_html)
            return match.group(1) if match else default

        result = {
            "amount": sell, "unit_price": unit_price, "min_unit_price": min_unit_price or unit_price,
            "ships": ships, "buyer_city": request.get("city_name", ""), "buyer_player": request.get("player_name", ""),
            "wanted": wanted, "stock": in_stock, "dry_run": bool(dry_run),
        }
        if dry_run:
            return result

        capacity_step = _capacity_step_from_percent(100)
        params: dict[str, Any] = {
            "cityId": city_id,
            "destinationCityId": buyer_city_id,
            "oldView": "branchOffice",
            "position": branchoffice_pos,
            "avatar2Name": _hidden("avatar2Name", request.get("player_name", "")),
            "city2Name": _hidden("city2Name", request.get("city_name", "")),
            "type": OFFER_TYPE_BUY,
            "activeTab": "bargain",
            "transportDisplayPrice": 0,
            "premiumTransporter": 0,
            "normalTransportersMax": ships_available,
            "capacity": capacity_step,
            "max_capacity": capacity_step,
            "jetPropulsion": 0,
            "transporters": ships,
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": "takeOffer",
            "currentTab": "bargain",
        }
        prices[_cargo_field(resource_idx)] = sell
        prices[_price_field(resource_idx)] = result["min_unit_price"]
        params.update(prices)
        self._ajax_request(ActionID.MARKETPLACE_SELL, params)
        return result
