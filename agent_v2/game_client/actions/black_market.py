"""Black Market actions — list and buy military units.

Action parameter reference (verified from live traffic on s78-br, 2026-05-22):

  View Black Market building (to sell):
    GET view=blackMarket&cityId=X&position=Y&activeTab=tabOfferUnits
        &backgroundView=city&currentCityId=X&templateView=blackMarket
        &actionRequest=AR&ajax=1
    Returns HTML with:
      - Unit dropdown: <select name="offerUnit"> with unit_id values and name text
      - Per-unit price limits shown as "MIN - MAX" in the form
      - Available amount per unit
      - Offer slots: "Oferecer Unidades X/8"

  Add a sell offer:
    POST action=BlackMarketAction&function=addOffer
         cityId=X
         position=Y               ← Black Market building position
         offerUnit={unit_id}      ← unit type ID from dropdown
         unitAmount={qty}
         offerResource=5          ← 5=gold; can also be other resource
         unitPrice={price}
         activeTab=tabOfferUnits
         backgroundView=city
         currentCityId=X
         templateView=blackMarket
         actionRequest=AR
         ajax=1

  View my offers:
    GET view=blackMarket&cityId=X&position=Y&activeTab=tabMyOffers
        &backgroundView=city&currentCityId=X&templateView=blackMarket
        &actionRequest=AR&ajax=1
    Returns table with: unit_icon, quantity, price, tax, net_profit, cancel_button

  Cancel an offer:
    POST action=BlackMarketAction&function=cancelOffer
         cityId=X&position=Y&offerId={id}&actionRequest=AR&ajax=1

  Browse available offers via Branch Office ("Troca de Soldados"):
    GET view=branchOfficeSoldier&activeTab=tab_branchOfficeSoldier
        &cityId={buyer_city}&position={bo_pos}
        &backgroundView=city&currentCityId={buyer_city}
        &templateView=branchOffice&currentTab=tab_branchOffice
        &actionRequest=AR&ajax=1
    Filter by type=444 (maritime) or type=111 (terrestrial)
    Returns: [{seller_city, seller_name, unit_id, amount, price_per_unit, distance}]

  View purchase page (takeOffer):
    GET view=takeOffer&destinationCityId={seller_city}
        &oldView=branchOffice&activeTab=tradePartners
        &cityId={buyer_city}&position={bo_pos}
        &type={444|111}&partnerOfferType=222
        &backgroundView=city&currentCityId={buyer_city}
        &templateView=branchOfficeSoldier&currentTab=tradePartners
        &actionRequest=AR&ajax=1
    Returns: transport slider, unit details, ships available

  Execute purchase:
    POST action=transportOperations
         function=buyMilitaryAtAnotherBlackMarket
         cityId={buyer_city}
         destinationCityId={seller_city}
         oldView=branchOffice
         position={bo_pos}               ← buyer's Branch Office position
         avatar2Name={seller_avatar}
         city2Name={seller_city_name}
         type={444|111}
         activeTab=tradePartners
         transportDisplayPrice=0
         premiumTransporter=0
         normalTransportersMax={max_ships}
         {unit_id}Price={price}          ← e.g. 22758Price=4800
         cargo_{unit_id}={qty}           ← e.g. cargo_22758=16
         capacity=5
         max_capacity=5
         jetPropulsion=0
         transporters={num_ships}
         backgroundView=city
         currentCityId={buyer_city}
         templateView=takeOffer
         currentTab=tradePartners
         actionRequest=AR
         ajax=1

  Unit type categories:
    type=444 → maritime fleet units (IDs ~200-299 range OR server-specific)
    type=111 → terrestrial troop units (IDs ~300-399 range OR server-specific)
    offerResource=5 → gold (ouro)

  Tax:
    Base 1% tax deducted from seller's proceeds.
    Alliance membership: +4 offer slots (base 8, alliance 12).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)

# Offer resource codes
OFFER_RESOURCE_GOLD = 5

# Unit category type codes for Branch Office filter
UNIT_TYPE_MARITIME = 444
UNIT_TYPE_TERRESTRIAL = 111
LISTING_TYPE_MARITIME = 111
LISTING_TYPE_TERRESTRIAL = 222


class GetBlackMarketStateAction(BaseAction):
    """Fetch the Black Market building state: available units to sell, price limits, offer slots.

    The game returns unit data in updateTemplateData.js_offerAbleUnits (JSON response),
    not in the HTML select (which is populated by JS after page load).
    Price limits (minLimit/maxLimit) require a second GET with offerUnit=<id> selected.
    """

    def execute(
        self,
        city_id: int,
        position: int,
        unit_id: int | None = None,
        offer_resource: int = OFFER_RESOURCE_GOLD,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return Black Market state.

        If unit_id is given, also fetches price limits for that unit.

        Returns:
            {
              "units": [{unit_id, name, disabled, price_min, price_max, default_price}],
              "offer_slots_used": int,
              "offer_slots_total": int,
              "tax_percent": int,
              "selected_unit": {unit_id, price_min, price_max, default_price, default_amount} | None,
            }
        """
        params = {
            "view": "blackMarket",
            "cityId": city_id,
            "position": position,
            "activeTab": "tabOfferUnits",
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": "blackMarket",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        if unit_id:
            params["offerUnit"] = unit_id
        if offer_resource is not None:
            params["offerResource"] = offer_resource

        resp = self.client._request("GET", self.client._server_url, params=params, headers=GAME_AJAX_HEADERS)
        try:
            data = resp.json()
        except Exception:
            return self._empty_state()

        # Update actionRequest from response
        for entry in data:
            if isinstance(entry, (list, tuple)) and entry and entry[0] == "updateGlobalData":
                d = entry[1]
                if isinstance(d, dict) and d.get("actionRequest"):
                    self.client._action_request = d["actionRequest"]
                break

        # Parse units from updateTemplateData.js_offerAbleUnits (populated JSON, not HTML)
        template_data: dict = {}
        for entry in data:
            if isinstance(entry, (list, tuple)) and entry and entry[0] == "updateTemplateData":
                if isinstance(entry[1], dict):
                    template_data = entry[1]
                break

        units = self._parse_units_from_template(template_data.get("js_offerAbleUnits", ""))

        # Slot count from template
        slots_used = _parse_num(str(template_data.get("openOffers", "0")))
        slots_total = int(template_data.get("maxOffers", 8) or 8)

        # Tax
        tax = int(template_data.get("currentTax", 1) or 1)

        bm_runtime = self._parse_black_market_runtime(template_data)
        price_key = "gold" if int(offer_resource) == OFFER_RESOURCE_GOLD else "res"
        available_counts = bm_runtime.get("units", {})
        min_price_map = (bm_runtime.get("minPrice", {}) or {}).get(price_key, {}) or {}
        max_price_map = (bm_runtime.get("maxPrice", {}) or {}).get(price_key, {}) or {}

        for u in units:
            uid = int(u.get("unit_id", 0) or 0)
            if uid in min_price_map:
                u["price_min"] = int(min_price_map[uid])
            if uid in max_price_map:
                u["price_max"] = int(max_price_map[uid])
            u["available_amount"] = int(available_counts.get(uid, 0) or 0)

        # Per-unit limits when unit_id was specified
        selected_unit = None
        if unit_id:
            selected_price_min = int(min_price_map.get(int(unit_id), 0) or 0)
            selected_price_max = int(max_price_map.get(int(unit_id), 0) or 0)
            raw_default_price = _parse_num(str(
                (template_data.get("js_unitPrice") or {}).get("value", 0)
                if isinstance(template_data.get("js_unitPrice"), dict)
                else 0
            ))
            selected_unit = {
                "unit_id": unit_id,
                "price_min": selected_price_min or _parse_num(str(template_data.get("minLimit", 1))),
                "price_max": selected_price_max or _parse_num(str(template_data.get("maxLimit", 9999))),
                "default_price": raw_default_price or selected_price_min or _parse_num(str(template_data.get("minLimit", 1))),
                "default_amount": _parse_num(str(
                    (template_data.get("js_unitAmount") or {}).get("value", 0)
                    if isinstance(template_data.get("js_unitAmount"), dict)
                    else 0
                )),
                "available_amount": int(available_counts.get(int(unit_id), 0) or 0),
                "offer_resource": int(offer_resource),
            }
            # Also patch this unit's limits into the units list
            for u in units:
                if u["unit_id"] == unit_id:
                    u["price_min"] = selected_unit["price_min"]
                    u["price_max"] = selected_unit["price_max"]
                    u["available_amount"] = selected_unit["available_amount"]
                    break

        # Fallback: also parse from HTML changeView for slots if template gave 0
        if slots_used == 0 and slots_total == 8:
            html = _extract_html(resp)
            slot_m = re.search(r"Oferecer Unidades\s*(\d+)\s*/\s*(\d+)", html)
            if slot_m:
                slots_used = int(slot_m.group(1))
                slots_total = int(slot_m.group(2))

        raw_text = ""
        try:
            raw_text = resp.text or ""
        except Exception:
            raw_text = ""

        market_blocked = False
        cooldown_active = False
        cooldown_timestamp = 0
        blocked_m = re.search(r'isMarketBlocked\\":(\d+)', raw_text)
        if blocked_m:
            market_blocked = blocked_m.group(1) == "1"
        cooldown_m = re.search(r'cooldownActive\\":(true|false)', raw_text, re.IGNORECASE)
        if cooldown_m:
            cooldown_active = cooldown_m.group(1).lower() == "true"
        cooldown_ts_m = re.search(r'cooldownTimeStamp\\":(\d+)', raw_text)
        if cooldown_ts_m:
            cooldown_timestamp = int(cooldown_ts_m.group(1))

        return {
            "units": units,
            "offer_slots_used": slots_used,
            "offer_slots_total": slots_total,
            "tax_percent": tax,
            "selected_unit": selected_unit,
            "market_blocked": market_blocked,
            "cooldown_active": cooldown_active,
            "cooldown_timestamp": cooldown_timestamp,
            "own_offers": self._parse_own_offers_from_template(template_data),
        }

    def _parse_units_from_template(self, units_html: str) -> list[dict]:
        """Parse unit list from updateTemplateData.js_offerAbleUnits HTML snippet."""
        units = []
        if not units_html:
            return units
        for m in re.finditer(r'<option([^>]*)value="(\d+)"[^>]*>([^<]+)</option>', units_html):
            attrs, uid, name = m.group(1), int(m.group(2)), m.group(3).strip()
            disabled = "disabled" in attrs
            units.append({
                "unit_id": uid,
                "name": name,
                "disabled": disabled,
                "price_min": 1,
                "price_max": 9999,
                "default_price": 0,
            })
        return units

    def _empty_state(self) -> dict[str, Any]:
        return {"units": [], "offer_slots_used": 0, "offer_slots_total": 8, "tax_percent": 1, "selected_unit": None}

    def _parse_black_market_runtime(self, template_data: dict[str, Any]) -> dict[str, Any]:
        load_js = template_data.get("load_js")
        if not isinstance(load_js, dict):
            return {}
        raw = load_js.get("params")
        if not raw:
            return {}
        try:
            data = json.loads(str(raw))
        except Exception:
            return {}

        def _int_map(raw_map: Any) -> dict[int, int]:
            if not isinstance(raw_map, dict):
                return {}
            out: dict[int, int] = {}
            for key, value in raw_map.items():
                try:
                    out[int(key)] = int(value)
                except (TypeError, ValueError):
                    continue
            return out

        runtime: dict[str, Any] = {
            "units": _int_map(data.get("units")),
            "minPrice": {},
            "maxPrice": {},
        }
        for bucket_name in ("minPrice", "maxPrice"):
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            runtime[bucket_name] = {
                "res": _int_map(bucket.get("res")),
                "gold": _int_map(bucket.get("gold")),
            }
        return runtime

    def _parse_own_offers_from_template(self, template_data: dict[str, Any]) -> list[dict[str, Any]]:
        raw_offers = template_data.get("ownOffers")
        if not isinstance(raw_offers, list):
            return []
        offers: list[dict[str, Any]] = []
        for item in raw_offers:
            if not isinstance(item, dict):
                continue
            try:
                offer_id_raw = item.get("offerId") or item.get("id") or item.get("offer_id")
                offers.append({
                    "offer_id": int(offer_id_raw) if offer_id_raw else None,
                    "unit_id": int(item.get("unitId", 0) or 0),
                    "amount": int(item.get("amount", 0) or 0),
                    "price": int(item.get("price", 0) or 0),
                    "tax": int(item.get("tax", 0) or 0),
                    "net_profit": int(item.get("gain", 0) or 0),
                    "resource_name": str(item.get("resName") or ""),
                })
            except (TypeError, ValueError):
                continue
        return offers


class AddBlackMarketOfferAction(BaseAction):
    """List units for sale on the Black Market."""

    def execute(
        self,
        city_id: int,
        position: int,
        unit_id: int,
        amount: int,
        unit_price: int,
        offer_resource: int = OFFER_RESOURCE_GOLD,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Add a sell offer.

        Args:
            city_id: Seller's city ID.
            position: Black Market building position.
            unit_id: Unit type ID (from GetBlackMarketStateAction).
            amount: Number of units to offer.
            unit_price: Price per unit.
            offer_resource: Resource to accept (5=gold).
        """
        return self._ajax_request(
            "BlackMarketAction&function=addOffer",
            {
                "cityId": city_id,
                "position": position,
                "offerUnit": unit_id,
                "unitAmount": amount,
                "offerResource": offer_resource,
                "unitPrice": unit_price,
                "activeTab": "tabOfferUnits",
                "backgroundView": "city",
                "currentCityId": city_id,
                "templateView": "blackMarket",
                "currentTab": "tabOfferUnits",
            },
        )


class GetMyBlackMarketOffersAction(BaseAction):
    """Fetch the seller's current active offers."""

    def execute(self, city_id: int, position: int, **kwargs: Any) -> list[dict[str, Any]]:
        """Return list of active offers.

        Each item: {offer_id, unit_id, amount, price, tax, net_profit}
        """
        params = {
            "view": "blackMarket",
            "cityId": city_id,
            "position": position,
            "activeTab": "tabMyOffers",
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": "blackMarket",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("GET", self.client._server_url, params=params, headers=GAME_AJAX_HEADERS)

        json_offers: list[dict] = []
        try:
            data = resp.json()
            for entry in data:
                if isinstance(entry, (list, tuple)) and entry and entry[0] == "updateTemplateData" and isinstance(entry[1], dict):
                    json_offers = GetBlackMarketStateAction(self.client)._parse_own_offers_from_template(entry[1])
                    break
        except Exception:
            pass

        html = _extract_html(resp)
        html_offers = self._parse_my_offers(html)

        # Prefer JSON data but supplement offer_id from HTML cancel-button links
        if json_offers and html_offers:
            html_by_unit: dict[int, dict] = {}
            for o in html_offers:
                uid = int(o.get("unit_id") or 0)
                if uid and uid not in html_by_unit:
                    html_by_unit[uid] = o
            for offer in json_offers:
                if not offer.get("offer_id"):
                    html_match = html_by_unit.get(int(offer.get("unit_id") or 0))
                    if html_match and html_match.get("offer_id"):
                        offer["offer_id"] = html_match["offer_id"]
            return json_offers

        return json_offers or html_offers

    def _parse_my_offers(self, html: str) -> list[dict[str, Any]]:
        offers: list[dict] = []
        for row_html in re.split(r'<tr', html)[1:]:
            unit_m = re.search(r'unit[_-](\d+)|\bs(\d+)\b', row_html, re.IGNORECASE)
            if not unit_m:
                continue
            offer_id_m = re.search(r'offerId=(\d+)', row_html, re.IGNORECASE)
            # Extract numeric values from td cells (skip cells with only tags)
            cell_nums: list[int] = []
            for cell in re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL):
                text = re.sub(r'<[^>]+>', '', cell).strip()
                if re.match(r'^[\d.,\s]+$', text) and text:
                    cell_nums.append(_parse_num(text))
            try:
                offers.append({
                    "offer_id": int(offer_id_m.group(1)) if offer_id_m else None,
                    "unit_id": next(int(group) for group in unit_m.groups() if group),
                    "amount": cell_nums[0] if len(cell_nums) > 0 else 0,
                    "price": cell_nums[1] if len(cell_nums) > 1 else 0,
                    "tax": cell_nums[2] if len(cell_nums) > 2 else 0,
                    "net_profit": cell_nums[3] if len(cell_nums) > 3 else 0,
                })
            except (StopIteration, ValueError, IndexError):
                continue
        return offers


class CancelBlackMarketOfferAction(BaseAction):
    """Remove an active Black Market offer (returns units to garrison)."""

    def execute(self, city_id: int, position: int, offer_id: int, **kwargs: Any) -> dict[str, Any]:
        # Warm the correct Black Market tab first. The game rejects direct removeOffer
        # calls in some cases unless the session has loaded "As Minhas Ofertas".
        GetMyBlackMarketOffersAction(self.client).execute(city_id=city_id, position=position)

        if not self.client._action_request:
            raise ActionError(
                "No actionRequest token available — fetch a page first",
                action="BlackMarketAction&function=removeOffer",
            )

        params = {
            "action": "BlackMarketAction",
            "function": "removeOffer",
            "actionRequest": self.client._action_request,
            "ajax": "1",
            "cityId": city_id,
            "position": position,
            "offerId": offer_id,
            "activeTab": "tabMyOffers",
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": "blackMarket",
        }
        return self.client._ajax_get("BlackMarketAction&function=removeOffer", params)


class GetAvailableUnitOffersAction(BaseAction):
    """Fetch available unit offers from other players via Branch Office 'Troca de Soldados' tab."""

    def execute(
        self,
        buyer_city_id: int,
        bo_position: int,
        unit_category: int = 0,
        market_range: int | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Return all available unit offers.

        Args:
            buyer_city_id: Buyer's city ID.
            bo_position: Branch Office building position.
            unit_category: UNIT_TYPE_MARITIME (444) or UNIT_TYPE_TERRESTRIAL (111) or 0 for all.

        Returns:
            [{seller_city_id, seller_city_name, seller_avatar, unit_id, amount, price_per_unit, distance}]
        """
        params = {
            "view": "branchOfficeSoldier",
            "activeTab": "tradePartners",
            "cityId": buyer_city_id,
            "position": bo_position,
            "backgroundView": "city",
            "currentCityId": buyer_city_id,
            "templateView": "branchOfficeSoldier",
            "currentTab": "tab_branchOfficeSoldier",
            "searchResource": "all",
            "currency": "all",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        if unit_category:
            params["type"] = self._listing_type_for_unit_category(unit_category)
        if market_range:
            params["range"] = market_range

        resp = self.client._request("GET", self.client._server_url, params=params, headers=GAME_AJAX_HEADERS)
        html = _extract_html(resp)
        return self._parse_offers(html)

    def _parse_offers(self, html: str) -> list[dict[str, Any]]:
        offers: list[dict] = []
        row_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
        link_re = re.compile(r'href="\?view=takeOffer&destinationCityId=(\d+)([^"]*)"', re.IGNORECASE | re.DOTALL)
        type_re = re.compile(r"[?&]type=(\d+)", re.IGNORECASE)
        seller_re = re.compile(
            r'<td[^>]*class="short_text80"[^>]*>\s*([^<(]+?)<br>\s*\(([^)<]+)\)',
            re.IGNORECASE | re.DOTALL,
        )
        cell_re = re.compile(r"<td\b[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
        amount_re = re.compile(r'<td[^>]*class="amount"[^>]*>\s*([\d.,\s]+)', re.IGNORECASE)
        price_re = re.compile(r'<td[^>]*class="costs"[^>]*>\s*([\d.,\s]+)', re.IGNORECASE)
        unit_id_re = re.compile(r'name="cargo_(\d+)"|name="(\d+)Price"|unit_(\d+)|\bs(\d+)\b', re.IGNORECASE)

        for section in row_re.findall(html):
            link_m = link_re.search(section)
            if not link_m:
                continue
            unit_m = unit_id_re.search(section)
            price_m = price_re.search(section)
            amount_m = amount_re.search(section)
            seller_m = seller_re.search(section)
            type_m = type_re.search(link_m.group(2) or "")
            cells = cell_re.findall(section)
            try:
                unit_id = next(int(group) for group in unit_m.groups() if group) if unit_m else 0
                amount = _parse_num(amount_m.group(1)) if amount_m else 0
                price = _parse_num(price_m.group(1)) if price_m else 0
                if (amount <= 0 or price <= 0) and len(cells) >= 5:
                    if amount <= 0:
                        amount = _extract_leading_num(cells[2])
                    if price <= 0:
                        price = _extract_leading_num(cells[4])
                offers.append({
                    "seller_city_id": int(link_m.group(1)),
                    "seller_city_name": _url_unquote(seller_m.group(1)).strip() if seller_m else "",
                    "seller_avatar": _url_unquote(seller_m.group(2)).strip() if seller_m else "",
                    "unit_category": _unit_category_from_id(unit_id),
                    "unit_id": unit_id,
                    "amount": amount,
                    "price_per_unit": price,
                })
            except (StopIteration, ValueError, AttributeError, IndexError):
                continue

        offers.sort(key=lambda o: o["price_per_unit"])
        return offers

    @staticmethod
    def _listing_type_for_unit_category(unit_category: int) -> int:
        if unit_category == UNIT_TYPE_MARITIME:
            return LISTING_TYPE_MARITIME
        if unit_category == UNIT_TYPE_TERRESTRIAL:
            return LISTING_TYPE_TERRESTRIAL
        return unit_category


class BuyUnitsAction(BaseAction):
    """Execute a military unit purchase via Branch Office."""

    def execute(
        self,
        buyer_city_id: int,
        bo_position: int,
        seller_city_id: int,
        seller_avatar: str,
        seller_city_name: str,
        unit_id: int,
        quantity: int,
        unit_price: int,
        unit_category: int = UNIT_TYPE_MARITIME,
        num_transporters: int = 1,
        offer_key: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Buy units from a seller's Black Market offer.

        Args:
            buyer_city_id: Buyer's city ID.
            bo_position: Buyer's Branch Office position.
            seller_city_id: Seller's city ID.
            seller_avatar: Seller's avatar/player name.
            seller_city_name: Seller's city name.
            unit_id: Unit type ID (from offer).
            quantity: Number of units to buy.
            unit_price: Price per unit.
            unit_category: UNIT_TYPE_MARITIME (444) or UNIT_TYPE_TERRESTRIAL (111).
            num_transporters: Number of transport ships needed.
        """
        # Need to navigate to the city first to get fresh actionRequest
        offer_param_key = int(offer_key or unit_id)
        params = {
            "action": "transportOperations",
            "function": "buyMilitaryAtAnotherBlackMarket",
            "cityId": buyer_city_id,
            "destinationCityId": seller_city_id,
            "oldView": "branchOffice",
            "position": bo_position,
            "avatar2Name": seller_avatar,
            "city2Name": seller_city_name,
            "type": unit_category,
            "activeTab": "tradePartners",
            "transportDisplayPrice": 0,
            "premiumTransporter": 0,
            "normalTransportersMax": num_transporters,
            f"{offer_param_key}Price": unit_price,
            f"cargo_{offer_param_key}": quantity,
            "capacity": 5,
            "max_capacity": 5,
            "jetPropulsion": 0,
            "transporters": num_transporters,
            "backgroundView": "city",
            "currentCityId": buyer_city_id,
            "templateView": "takeOffer",
            "currentTab": "tradePartners",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            data = resp.json()
        except ValueError:
            raise ActionError("BuyUnits: invalid JSON response", action="buyMilitaryAtAnotherBlackMarket")

        parsed = self.client.ajax_parser.parse_response(data)
        if parsed.get("new_action_request"):
            self.client._action_request = parsed["new_action_request"]
        if parsed.get("errors"):
            raise ActionError(
                f"BuyUnits server errors: {parsed['errors']}",
                action="buyMilitaryAtAnotherBlackMarket",
                server_errors=parsed["errors"],
            )
        return parsed

    def get_takeOffer_details(
        self,
        buyer_city_id: int,
        bo_position: int,
        seller_city_id: int,
        unit_category: int,
    ) -> dict[str, Any]:
        """Fetch the takeOffer page to get transport details and confirm unit/price info.

        Returns:
            {
              "units": [{unit_id, amount, price}],
              "max_transporters": int,
              "eta_minutes": int,
            }
        """
        params = {
            "view": "takeOffer",
            "destinationCityId": seller_city_id,
            "oldView": "branchOffice",
            "activeTab": "tradePartners",
            "cityId": buyer_city_id,
            "position": bo_position,
            "type": unit_category,
            "partnerOfferType": 222,
            "resource": "all",
            "currency": "all",
            "backgroundView": "city",
            "currentCityId": buyer_city_id,
            "templateView": "branchOfficeSoldier",
            "currentTab": "tradePartners",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("GET", self.client._server_url, params=params, headers=GAME_AJAX_HEADERS)
        try:
            data = resp.json()
        except ValueError:
            html = _extract_html(resp)
        else:
            parsed = self.client.ajax_parser.parse_response(data)
            if parsed.get("new_action_request"):
                self.client._action_request = parsed["new_action_request"]
            html = _extract_html_from_data(data)
        return self._parse_take_offer_live(html)

    def _parse_take_offer(self, html: str) -> dict[str, Any]:
        """Parse takeOffer page for unit IDs, amounts, prices, and transport capacity."""
        units: list[dict] = []

        # Parse unit rows: each has unit_id in image src and amount/price in table cells
        unit_row_re = re.compile(
            r'unit_(\d+)[^"]*"'           # unit ID from image src
            r'.*?(\d[\d.,\s]*)\s*</td>'   # amount
            r'.*?([\d.,\s]+)\s*(?:por\s+pe|</td>)',  # price
            re.DOTALL,
        )
        for m in unit_row_re.finditer(html):
            try:
                units.append({
                    "unit_id": int(m.group(1)),
                    "amount": _parse_num(m.group(2)),
                    "price": _parse_num(m.group(3)),
                })
            except (ValueError, IndexError):
                continue

        # Parse max transporters
        max_ships = 0
        ships_m = re.search(r'normalTransportersMax[^\d]*(\d+)', html)
        if ships_m:
            max_ships = int(ships_m.group(1))

        # Parse ETA
        eta_m = re.search(r'Dura[çc][aã]o da viagem:\s*(\d+)m', html, re.IGNORECASE)
        eta_minutes = int(eta_m.group(1)) if eta_m else 0

        return {
            "units": units,
            "max_transporters": max_ships,
            "eta_minutes": eta_minutes,
        }

    def _parse_take_offer_live(self, html: str) -> dict[str, Any]:
        """Parse the live takeOffer markup used by the current game client."""
        units: list[dict] = []

        row_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
        amount_re = re.compile(r'<td[^>]*class="amount"[^>]*>\s*([\d.,\s]+)', re.IGNORECASE)
        price_re = re.compile(r'<td[^>]*class="costs"[^>]*>\s*([\d.,\s]+)', re.IGNORECASE)
        offer_key_re = re.compile(r'name="cargo_(\d+)"|name="(\d+)Price"', re.IGNORECASE)
        visual_unit_re = re.compile(r'\bs(\d{3,4})\b|unit_(\d{3,4})', re.IGNORECASE)

        for row in row_re.findall(html):
            offer_m = offer_key_re.search(row)
            visual_m = visual_unit_re.search(row)
            if not offer_m and not visual_m:
                continue
            try:
                offer_key = next((int(group) for group in offer_m.groups() if group), 0) if offer_m else 0
                visual_unit_id = next((int(group) for group in visual_m.groups() if group), 0) if visual_m else 0
                amount_match = amount_re.search(row)
                price_match = price_re.search(row)
                units.append({
                    "unit_id": visual_unit_id or offer_key,
                    "offer_key": offer_key or visual_unit_id,
                    "amount": _parse_num(amount_match.group(1)) if amount_match else 0,
                    "price": _parse_num(price_match.group(1)) if price_match else 0,
                })
            except (StopIteration, ValueError, AttributeError, IndexError):
                continue

        max_ships = 0
        ships_m = re.search(r'name="normalTransportersMax"[^>]*value="(\d+)"', html)
        if ships_m:
            max_ships = int(ships_m.group(1))

        eta_m = re.search(r'journeyDuration\s*[:=]\s*(\d+)', html, re.IGNORECASE)
        eta_minutes = int(round(int(eta_m.group(1)) / 60)) if eta_m else 0

        seller_avatar_m = re.search(r'name="avatar2Name"\s+value="([^"]*)"', html)
        seller_city_m = re.search(r'name="city2Name"\s+value="([^"]*)"', html)

        return {
            "units": units,
            "max_transporters": max_ships,
            "eta_minutes": eta_minutes,
            "seller_avatar": seller_avatar_m.group(1).strip() if seller_avatar_m else "",
            "seller_city_name": seller_city_m.group(1).strip() if seller_city_m else "",
        }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _unit_category_from_id(unit_id: int) -> int:
    """Derive unit category from unit_id range: 200-299 = maritime (444), 300-399 = terrestrial (111)."""
    if 200 <= unit_id < 300:
        return UNIT_TYPE_MARITIME
    if 300 <= unit_id < 400:
        return UNIT_TYPE_TERRESTRIAL
    return 0


def _extract_html(resp: Any) -> str:
    """Extract the first large HTML string from a changeView/changeAjax AJAX response."""
    try:
        data = resp.json()
        for entry in data:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                if entry[0] in ("changeView", "changeAjaxResponse"):
                    payload = entry[1]
                    if isinstance(payload, (list, tuple)):
                        for item in payload:
                            if isinstance(item, str) and len(item) > 50:
                                return item
                    elif isinstance(payload, str) and len(payload) > 50:
                        return payload
    except Exception:
        pass
    # Fallback: return raw text
    try:
        return resp.text
    except Exception:
        return ""


def _extract_html_from_data(data: Any) -> str:
    try:
        for entry in data:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                if entry[0] in ("changeView", "changeAjaxResponse"):
                    payload = entry[1]
                    if isinstance(payload, (list, tuple)):
                        for item in payload:
                            if isinstance(item, str) and len(item) > 50:
                                return item
                    elif isinstance(payload, str) and len(payload) > 50:
                        return payload
    except Exception:
        pass
    return ""


def _parse_num(raw: str) -> int:
    """Parse a number string that may contain dots/spaces as thousands separators."""
    try:
        return int(str(raw).replace(".", "").replace(",", "").replace(" ", "").strip())
    except (ValueError, AttributeError):
        return 0


def _url_unquote(raw: str) -> str:
    try:
        from urllib.parse import unquote_plus

        return unquote_plus(raw)
    except Exception:
        return raw


def _strip_html(raw: str) -> str:
    try:
        text = re.sub(r"<[^>]+>", " ", raw or "")
        return re.sub(r"\s+", " ", text).strip()
    except Exception:
        return str(raw or "")


def _extract_leading_num(raw: str) -> int:
    try:
        match = re.search(r"^\s*([\d.,\s]+)", raw or "", re.IGNORECASE | re.DOTALL)
        if not match:
            return 0
        return _parse_num(match.group(1))
    except Exception:
        return 0
