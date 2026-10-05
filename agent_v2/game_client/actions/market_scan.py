"""Varredura do mercado geral -- o que esta em oferta ao alcance de um mercado (N-84).

Tela do jogo (capturada 2026-10-05 na HAVIT s78, Branch Office nivel 18, somente leitura):

  POST view=branchOffice&activeTab=bargain&cityId=C&position=P
       &type=444|333&searchResource=resource|1|2|3|4&range=R&offset=O

  type=444 lista quem VENDE (ofertas para comprar); type=333 lista quem quer COMPRAR.
  A lista vem 10 por pagina, da mais barata para a mais cara; offset escolhe a pagina.
  O jogo grava o ultimo range e a ultima pagina. O range vai de 1 a nivel/2 e NAO sobe
  sozinho quando o mercado evolui; um valor acima do maximo e IGNORADO (fica o salvo),
  entao o maximo e lido do seletor da tela e pedido exatamente (fetch_at_max_range).

  Linha de oferta (a lista de quem quer comprar, type=333, nao tem a 2a coluna):
    <td class="short_text80">Cidade <br/>(Jogador)</td>
    <td>bens por minuto</td>
    <td>quantidade <div class="tooltip">quantidade</div></td>
    <td><img alt="recurso"></td>
    <td>preco <img class="icon_gold"/> por peca</td>
    <td>distancia</td>
    <td><a href="?view=takeOffer&destinationCityId=CIDADE&...&type=444&resource=resource"></a></td>
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..parsers.numbers import parse_game_int
from .market import OFFERS_PER_PAGE, GetOffersAction, fetch_at_max_range, parse_range_state  # noqa: F401

logger = logging.getLogger(__name__)

OFFER_TYPE_SELL = 444   # other players selling: what can be bought
OFFER_TYPE_BUY = 333    # other players asking to buy
RESOURCE_STRS = {0: "resource", 1: "1", 2: "2", 3: "3", 4: "4"}
RESOURCE_IDX = {value: key for key, value in RESOURCE_STRS.items()}

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_LINK = re.compile(r"view=takeOffer(?:&amp;|&)destinationCityId=(\d+).*?(?:&amp;|&)type=(\d+)(?:&amp;|&)resource=(\w+)", re.S)
_NAME = re.compile(r"^\s*(.*?)\s*<br\s*/?>\s*\((.*)\)\s*$", re.S)
_TOOLTIP = re.compile(r"<div[^>]*class=\"tooltip\"[^>]*>.*?</div>", re.S)
_TAG = re.compile(r"<[^>]+>")
_NUMBER = re.compile(r"\d[\d.,]*")   # never across spaces: "33.000 33.000" is two numbers


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", fragment or "")).strip()


def _first_int(fragment: str) -> int:
    match = _NUMBER.search(_text(_TOOLTIP.sub("", fragment or "")))
    return parse_game_int(match.group(0), 0) if match else 0


def parse_offer_rows(html: str) -> list[dict[str, Any]]:
    """Offers of one listing page, in the order the game shows them."""
    offers: list[dict[str, Any]] = []
    for row in _ROW.findall(html or ""):
        link = _LINK.search(row)
        if not link:
            continue
        cells = _CELL.findall(row)
        if len(cells) < 6:
            continue
        # counted from the end: buy requests have no "goods per minute" column
        name = _NAME.match(cells[0])
        offers.append({
            "city_id": int(link.group(1)),
            "city_name": _text(name.group(1)) if name else _text(cells[0]),
            "player_name": _text(name.group(2)) if name else "",
            "goods_per_minute": _first_int(cells[-6]) if len(cells) >= 7 else 0,
            "amount": _first_int(cells[-5]),
            "unit_price": _first_int(cells[-3]),
            "distance": _first_int(cells[-2]),
            "offer_type": int(link.group(2)),
            "resource_idx": RESOURCE_IDX.get(link.group(3), 0),
        })
    return offers


class MarketScanAction(GetOffersAction):
    """Read what is on offer within reach of a Branch Office. Never buys or sells."""

    def _listing(self, city_id: int, bo_pos: int, resource_str: str, offer_type: int, offset: int) -> str:
        def fetch(range_value: int | None) -> str:
            params = {
                "view": "branchOffice",
                "cityId": city_id,
                "position": bo_pos,
                "currentCityId": city_id,
                "activeTab": "bargain",
                "type": str(offer_type),
                "searchResource": resource_str,
                "offset": int(offset),
                "backgroundView": "city",
                "templateView": "branchOffice",
                "currentTab": "bargain",
                "actionRequest": self.client._action_request,
                "ajax": "1",
            }
            if range_value:
                params["range"] = int(range_value)
            resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
            return self._extract_html_from_response(resp)

        return fetch_at_max_range(self, city_id, fetch)

    def search(
        self,
        city_id: int,
        bo_pos: int,
        resource_idx: int,
        *,
        offer_type: int = OFFER_TYPE_SELL,
        max_pages: int = 5,
    ) -> dict[str, Any]:
        """Every offer of one resource this market reaches at its maximum range.

        Returns {"offers", "max_range", "pages", "truncated"}; truncated means the
        listing still had pages after max_pages.
        """
        resource_str = RESOURCE_STRS.get(int(resource_idx), "resource")
        offers: list[dict[str, Any]] = []
        max_range = 0
        pages = 0
        truncated = False
        for page in range(max(1, int(max_pages))):
            html = self._listing(city_id, bo_pos, resource_str, offer_type, page * OFFERS_PER_PAGE)
            pages += 1
            if page == 0:
                max_range = parse_range_state(html)["selected"]   # the range actually searched
            offers.extend(parse_offer_rows(html))
            has_next = f"offset={(page + 1) * OFFERS_PER_PAGE}" in html
            if not has_next:
                break
            truncated = page == max(1, int(max_pages)) - 1
        if pages > 1:
            # the game keeps the last page looked at: leave it on the first one
            self._listing(city_id, bo_pos, resource_str, offer_type, 0)
        return {"offers": offers, "max_range": max_range, "pages": pages, "truncated": truncated}

    def _saved_state(self, city_id: int, bo_pos: int) -> dict[str, int]:
        """The market as the game has it saved (no search parameters sent)."""
        params = {
            "view": "branchOffice",
            "cityId": city_id,
            "position": bo_pos,
            "currentCityId": city_id,
            "activeTab": "bargain",
            "backgroundView": "city",
            "templateView": "branchOffice",
            "currentTab": "bargain",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        return parse_range_state(self._extract_html_from_response(resp))

    def sync_range(self, city_id: int, bo_pos: int) -> dict[str, int]:
        """Make the saved range follow the building level.

        The game keeps the range chosen when the market was smaller. Reads the saved
        one and, when it is below the maximum, runs one search at exactly the maximum
        (which the game then saves). Returns {"before", "max", "changed"}; changed
        is only set when the game confirmed the new range.
        """
        state = self._saved_state(city_id, bo_pos)
        before, maximum = state["selected"], state["max"]
        changed = False
        if maximum and before != maximum:
            self.__dict__.setdefault("_max_ranges", {})[int(city_id)] = maximum
            html = self._listing(city_id, bo_pos, "resource", OFFER_TYPE_SELL, 0)
            changed = parse_range_state(html)["selected"] == maximum
        return {"before": before, "max": maximum, "changed": int(changed)}
