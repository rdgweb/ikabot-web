"""Marketplace actions — create sell offers, buy from offers.

Action parameter reference (verified from live traffic on s78-br):

  Create/update own sell offer:
    GET  view=branchOfficeOwnOffers (to read current price limits per resource)
    POST action=CityScreen&function=updateOffers
         cityId, position (Branch Office slot), resourceTradeType=444,
         resource / tradegoodN (amount), resourcePrice / tradegoodNPrice,
         backgroundView=city, currentCityId, templateView=branchOfficeOwnOffers,
         currentTab=tab_branchOfficeOwnOffers
    NOTE: price limits are DYNAMIC (server-specific, change over time).
          The branchOfficeOwnOffers tab shows a "Limites" column with
          min-max per resource as "<td>MIN - MAX</td>" table cells.
          Confirmed limits on s78-br (snapshot 2026-04-20):
            Madeira  (resource):   21 – 50
            Vinho    (tradegood1):  6 – 16
            Mármore  (tradegood2):  5 – 15
            Cristal  (tradegood3): 11 – 32
            Enxofre  (tradegood4):  7 – 18
          Setting amount=0 clears the offer regardless of price.
          Setting amount>0 with price out of range returns type=11 error.

  Buy from another player's offer:
    Two-step:
      1. GET view=takeOffer&...  → HTML with price inputs
      2. POST action=transportOperations&function=buyGoodsAtAnotherBranchOffice
         cityId (seller), destinationCityId (buyer), position (buyer's BO slot),
         type (from offer link), cargo_resource / cargo_tradegoodN, ships, prices

  Sell to another player's buy offer:
    POST action=transportOperations&function=sellGoodsAtAnotherBranchOffice
    Same transport-operation structure; cargo set to what we're sending.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from ...services.resource_transport import (
    _capacity_step_from_percent,
    _parse_free_transporters,
    _parse_ship_capacity,
)
from ..constants import ActionID, GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)

# Resource index → Ikariam AJAX resource string
_IDX_TO_RESOURCE_STR = {0: "resource", 1: "1", 2: "2", 3: "3", 4: "4"}


class CreateOfferAction(BaseAction):
    """Create (or update) a sell offer on the player's own Branch Office."""

    def execute(
        self,
        city_id: int,
        branchoffice_pos: int,
        resource_idx: int,
        amount: int,
        unit_price: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Post a sell offer on the Branch Office.

        Args:
            city_id: Seller's city ID.
            branchoffice_pos: Branch Office building slot in the city.
            resource_idx: 0=wood, 1=wine, 2=marble, 3=crystal, 4=sulfur.
            amount: Number of units to list for sale.
            unit_price: Price per unit in gold. Must be within server limits
                        (limits are dynamic — fetched live from the game).
                        Pass 0 to auto-select the midpoint of the valid range.

        Returns:
            Parsed AJAX response.
        """
        if amount <= 0:
            raise ActionError("Sell amount must be positive", action="updateOffers")
        if resource_idx not in range(5):
            raise ActionError(f"Invalid resource_idx={resource_idx}", action="updateOffers")

        # Fetch current price limits — they are server-specific and change over time.
        limits, current_offer_state = self.get_market_context(city_id, branchoffice_pos)
        min_price, max_price = limits[resource_idx]

        if unit_price <= 0:
            # Auto-select midpoint if caller didn't specify a price
            unit_price = (min_price + max_price) // 2
            logger.info("Auto-selected price=%s (mid of %s–%s)", unit_price, min_price, max_price)
        elif not (min_price <= unit_price <= max_price):
            raise ActionError(
                f"Price {unit_price} out of server range [{min_price}–{max_price}] "
                f"for resource_idx={resource_idx}",
                action="updateOffers",
            )

        logger.info(
            "Creating sell offer city=%s res=%s amount=%s price=%s (limits %s–%s)",
            city_id, resource_idx, amount, unit_price, min_price, max_price,
        )

        # Build params with amount=0 (clear) for all resources except the target.
        # When amount=0, the price field is ignored by the server.
        current_offer_state = self._normalize_offer_state(current_offer_state, limits)
        params: dict[str, Any] = {
            "cityId": city_id,
            "position": branchoffice_pos,
            "resourceTradeType": "444",
            "resource": str(current_offer_state.get("resource", 0)),
            "resourcePrice": str(current_offer_state.get("resourcePrice", limits[0][0])),
            "tradegood1TradeType": "444",
            "tradegood1": str(current_offer_state.get("tradegood1", 0)),
            "tradegood1Price": str(current_offer_state.get("tradegood1Price", limits[1][0])),
            "tradegood2TradeType": "444",
            "tradegood2": str(current_offer_state.get("tradegood2", 0)),
            "tradegood2Price": str(current_offer_state.get("tradegood2Price", limits[2][0])),
            "tradegood3TradeType": "444",
            "tradegood3": str(current_offer_state.get("tradegood3", 0)),
            "tradegood3Price": str(current_offer_state.get("tradegood3Price", limits[3][0])),
            "tradegood4TradeType": "444",
            "tradegood4": str(current_offer_state.get("tradegood4", 0)),
            "tradegood4Price": str(current_offer_state.get("tradegood4Price", limits[4][0])),
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": "branchOfficeOwnOffers",
            "currentTab": "tab_branchOfficeOwnOffers",
        }

        # Override the target resource with actual amount and price
        if resource_idx == 0:
            params["resource"] = str(amount)
            params["resourcePrice"] = str(unit_price)
        else:
            params[f"tradegood{resource_idx}"] = str(amount)
            params[f"tradegood{resource_idx}Price"] = str(unit_price)

        result = self._ajax_request(ActionID.MARKETPLACE_UPDATE_OFFERS, params)
        if isinstance(result, dict):
            result["used_unit_price"] = unit_price
            result["price_min"] = min_price
            result["price_max"] = max_price
        logger.info("updateOffers response: %s", result)
        return result

    def get_market_context(
        self, city_id: int, branchoffice_pos: int
    ) -> tuple[list[tuple[int, int]], dict[str, int]]:
        html = self._get_own_offers_html(city_id, branchoffice_pos)
        limits = self._parse_price_limits(html)
        current_state = self._parse_current_offer_state(html, limits)
        return limits, current_state

    def get_price_limits(
        self, city_id: int, branchoffice_pos: int
    ) -> list[tuple[int, int]]:
        """Fetch current price limits per resource from the Branch Office own-offers tab.

        Returns a list of (min_price, max_price) tuples indexed by resource_idx:
          [0]=wood, [1]=wine, [2]=marble, [3]=crystal, [4]=sulfur

        Raises:
            ActionError: If the limits cannot be retrieved.
        """
        html = self._get_own_offers_html(city_id, branchoffice_pos)
        return self._parse_price_limits(html)

    def _get_own_offers_html(self, city_id: int, branchoffice_pos: int) -> str:
        resp = self.client._request(
            "GET",
            self.client._server_url,
            params={
                "view": "branchOfficeOwnOffers",
                "activeTab": "tab_branchOfficeOwnOffers",
                "cityId": city_id,
                "position": branchoffice_pos,
                "backgroundView": "city",
                "currentCityId": city_id,
                "templateView": "branchOfficeOwnOffers",
                "currentTab": "tab_branchOfficeOwnOffers",
                "actionRequest": self.client._action_request,
                "ajax": "1",
            },
            headers=GAME_AJAX_HEADERS,
        )
        try:
            data = resp.json()
        except Exception:
            raise ActionError("Could not parse price limits response", action="get_price_limits")

        html = ""
        for entry in data:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2 and entry[0] == "changeView":
                payload = entry[1]
                if isinstance(payload, (list, tuple)):
                        for item in payload:
                            if isinstance(item, str) and len(item) > 100:
                                html = item
                                break
        if not html:
            raise ActionError("Could not extract own offers HTML", action="get_price_limits")
        return html

    def _parse_price_limits(self, html: str) -> list[tuple[int, int]]:

        # Parse <td>MIN - MAX</td> rows (one per resource in form order)
        limit_cells = re.findall(r"<td>(\d+)\s+-\s+(\d+)</td>", html)
        if len(limit_cells) < 5:
            raise ActionError(
                f"Expected 5 price limit rows, found {len(limit_cells)}",
                action="get_price_limits",
            )

        limits = [(int(mn), int(mx)) for mn, mx in limit_cells[:5]]
        logger.info("Price limits fetched: %s", limits)
        return limits

    def _parse_current_offer_state(self, html: str, limits: list[tuple[int, int]]) -> dict[str, int]:
        state = {
            "resource": 0,
            "resourcePrice": limits[0][0],
            "tradegood1": 0,
            "tradegood1Price": limits[1][0],
            "tradegood2": 0,
            "tradegood2Price": limits[2][0],
            "tradegood3": 0,
            "tradegood3Price": limits[3][0],
            "tradegood4": 0,
            "tradegood4Price": limits[4][0],
        }
        for match in re.finditer(r'name="([^"]+)"[^>]*value="([^"]*)"', html):
            name = str(match.group(1) or "").strip()
            if name not in state:
                continue
            try:
                state[name] = int(str(match.group(2) or "0").strip() or 0)
            except ValueError:
                continue
        return state

    def _normalize_offer_state(self, current_state: dict[str, int], limits: list[tuple[int, int]]) -> dict[str, int]:
        normalized = dict(current_state)
        field_pairs = (
            ("resource", "resourcePrice", 0),
            ("tradegood1", "tradegood1Price", 1),
            ("tradegood2", "tradegood2Price", 2),
            ("tradegood3", "tradegood3Price", 3),
            ("tradegood4", "tradegood4Price", 4),
        )
        for amount_field, price_field, idx in field_pairs:
            amount = int(normalized.get(amount_field, 0) or 0)
            minimum, maximum = limits[idx]
            if amount <= 0:
                normalized[price_field] = minimum
                continue
            raw_price = int(normalized.get(price_field, minimum) or minimum)
            clamped = min(max(raw_price, minimum), maximum)
            if clamped != raw_price:
                logger.info(
                    "Adjusted preserved offer price %s from %s to %s within [%s-%s]",
                    price_field,
                    raw_price,
                    clamped,
                    minimum,
                    maximum,
                )
            normalized[price_field] = clamped
        return normalized


class BuyAction(BaseAction):
    """Buy a resource from another player's Branch Office offer.

    This action requires three HTTP round-trips:
      1. Load buyer's Branch Office listing (filtered by resource) to locate the offer.
      2. Load the takeOffer page to retrieve per-resource transport prices.
      3. POST buyGoodsAtAnotherBranchOffice with cargo + ship info.
    """

    def execute(
        self,
        buyer_city_id: int,
        buyer_branchoffice_pos: int,
        seller_city_id: int,
        seller_branchoffice_pos: int,
        resource_idx: int,
        amount: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Buy resources from a specific seller's Branch Office.

        Args:
            buyer_city_id: City ID where purchased goods will be delivered.
            buyer_branchoffice_pos: Buyer's Branch Office slot (for POST).
            seller_city_id: City ID where the sell offer is posted.
            seller_branchoffice_pos: Seller's Branch Office slot.
            resource_idx: 0=wood, 1=wine, 2=marble, 3=crystal, 4=sulfur.
            amount: Amount to buy.

        Returns:
            Parsed AJAX response from the final purchase POST.

        Raises:
            ActionError: If the offer is not found or the purchase fails.
        """
        resource_str = _IDX_TO_RESOURCE_STR.get(resource_idx, "resource")

        logger.info(
            "Buying res=%s amount=%s from city=%s (seller_bo=%s) → city=%s",
            resource_idx, amount, seller_city_id, seller_branchoffice_pos, buyer_city_id,
        )

        # Step 1: Scrape buyer's Branch Office listing to find the seller's offer
        branch_html = self._get_branch_office_html(
            buyer_city_id, buyer_branchoffice_pos, resource_str
        )
        offer = self._find_offer_in_listing(branch_html, seller_city_id, resource_str)
        if offer is None:
            raise ActionError(
                f"Offer from seller city {seller_city_id} (res={resource_str}) "
                f"not found in Branch Office listing",
                action="buyGoodsAtAnotherBranchOffice",
            )

        # Step 2: Load the takeOffer page to get transport price inputs
        take_html = self._get_take_offer_html(
            seller_city_id=offer["city_id"],
            buyer_city_id=buyer_city_id,
            buyer_bo_pos=buyer_branchoffice_pos,
            offer_type=offer["type"],
            resource_str=resource_str,
        )

        # Step 3: Extract per-resource prices from takeOffer HTML
        prices: dict[str, Any] = {}
        for m in re.findall(r'"tradegood(\d)Price"\s+value="(\d+)"', take_html):
            prices[f"tradegood{m[0]}Price"] = int(m[1])
            prices[f"cargo_tradegood{m[0]}"] = 0
        m = re.search(r'"resourcePrice"\s+value="(\d+)"', take_html)
        if m:
            prices["resourcePrice"] = int(m.group(1))
            prices["cargo_resource"] = 0

        # Set the cargo being purchased
        if resource_idx == 0:
            prices["cargo_resource"] = amount
        else:
            prices[f"cargo_tradegood{resource_idx}"] = amount

        ships_available = _parse_free_transporters(take_html, use_freighters=False)
        ship_capacity = _parse_ship_capacity(
            take_html,
            ships_available,
            use_freighters=False,
        )
        capacity_step = _capacity_step_from_percent(100)
        ships = max(1, math.ceil(amount / max(1, ship_capacity)))
        if ships > ships_available:
            raise ActionError(
                (
                    "Not enough free transporters for market purchase: "
                    f"need {ships}, have {ships_available} "
                    f"(amount={amount}, ship_capacity={ship_capacity})"
                ),
                action="buyGoodsAtAnotherBranchOffice",
                server_errors=["Os seus barcos de comércio não têm espaço suficiente!"],
            )

        # Step 4: POST the purchase
        params: dict[str, Any] = {
            # The buy POST keeps the same city direction as the takeOffer view:
            # cityId/currentCityId stay on the buyer context, destinationCityId is the seller.
            "cityId": offer.get("buyer_city_id", buyer_city_id),
            "destinationCityId": offer["city_id"],
            "oldView": "branchOffice",
            "position": offer.get("buyer_bo_pos", buyer_branchoffice_pos),
            "avatar2Name": offer.get("player_name", ""),
            "city2Name": offer.get("city_name", ""),
            "type": offer["type"],
            "activeTab": "bargain",
            "transportDisplayPrice": 0,
            "premiumTransporter": 0,
            "normalTransportersMax": ships_available,
            "capacity": capacity_step,
            "max_capacity": capacity_step,
            "jetPropulsion": 0,
            "transporters": ships,
            "backgroundView": "city",
            "currentCityId": offer.get("buyer_city_id", buyer_city_id),
            "templateView": "takeOffer",
            "currentTab": "bargain",
        }
        params.update(prices)

        return self._ajax_request(ActionID.MARKETPLACE_BUY, params)

    # ── HTML scraping helpers ───────────────────────────────────────────────

    def _get_branch_office_html(
        self, city_id: int, bo_pos: int, resource_str: str
    ) -> str:
        """POST to the Branch Office AJAX endpoint; return the HTML fragment."""
        params = {
            "view": "branchOffice",
            "cityId": city_id,
            "position": bo_pos,
            "currentCityId": city_id,
            "activeTab": "bargain",
            "type": "444",
            "searchResource": resource_str,
            "range": 24,
            "backgroundView": "city",
            "templateView": "branchOffice",
            "currentTab": "bargain",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request(
            "POST",
            self.client._server_url,
            data=params,
            headers=GAME_AJAX_HEADERS,
        )
        html = self._extract_html_from_response(resp)
        if not html:
            logger.warning("Could not parse branchOffice HTML from response")
        return html

    def _find_offer_in_listing(
        self, html: str, seller_city_id: int, resource_str: str
    ) -> dict | None:
        """Search Branch Office listing HTML for an offer from seller_city_id.

        Ikariam takeOffer href structure (confirmed from live HTML):
          href="?view=takeOffer
                &destinationCityId=SELLER_CITY   ← seller's city
                &oldView=branchOffice&activeTab=bargain
                &cityId=BUYER_CITY               ← buyer's Branch Office city
                &position=BUYER_BO_POS           ← buyer's BO position
                &type=TYPE&resource=RES"
        """
        pattern = (
            r'class="short_text80">(.*?)\s*<br'  # city_name (seller's city name)
            r'[\s\S]{0,100}?\((.*?)\)'            # player_name
            r'[\s\S]*?'
            r'href="\?view=takeOffer'
            r'&destinationCityId=(\d+)'           # group 3 = seller's city ID
            r'&oldView=branchOffice&activeTab=bargain'
            r'&cityId=(\d+)'                      # group 4 = buyer's city ID
            r'&position=(\d+)'                    # group 5 = buyer's BO position
            r'&type=(\d+)'                        # group 6 = offer type
            r'&resource=(\w+)"'                   # group 7 = resource string
        )
        for m in re.finditer(pattern, html, re.DOTALL):
            city_name, player_name, dest_city_id, city_id, position, offer_type, resource = (
                m.groups()
            )
            # dest_city_id is the seller's city; city_id is the buyer's city
            if str(dest_city_id) == str(seller_city_id) and resource == resource_str:
                return {
                    "city_name": city_name.strip(),
                    "player_name": player_name.strip(),
                    "city_id": int(dest_city_id),       # seller's city
                    "buyer_city_id": int(city_id),      # buyer's city
                    "buyer_bo_pos": int(position),      # buyer's BO position
                    "type": int(offer_type),
                    "resource": resource,
                }
        return None

    def _get_take_offer_html(
        self,
        seller_city_id: int,
        buyer_city_id: int,
        buyer_bo_pos: int,
        offer_type: int,
        resource_str: str,
    ) -> str:
        """Fetch the takeOffer dialog HTML (contains transport price inputs).

        Uses the same parameters as the takeOffer href in the listing:
          cityId=buyer_city, destinationCityId=seller_city, position=buyer_bo_pos
        """
        params = {
            "view": "takeOffer",
            "destinationCityId": seller_city_id,   # seller's city
            "oldView": "branchOffice",
            "activeTab": "bargain",
            "cityId": buyer_city_id,               # buyer's city
            "position": buyer_bo_pos,              # buyer's BO position
            "type": offer_type,
            "resource": resource_str,
            "backgroundView": "city",
            "currentCityId": buyer_city_id,
            "templateView": "branchOffice",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request(
            "POST",
            self.client._server_url,
            data=params,
            headers=GAME_AJAX_HEADERS,
        )
        html = self._extract_html_from_response(resp)
        if not html:
            logger.warning("Could not parse takeOffer HTML from response")
        return html

    def _extract_html_from_response(self, resp: Any) -> str:
        """Extract the first large HTML string from a changeView AJAX response."""
        try:
            data = resp.json()
            for entry in data:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2 and entry[0] == "changeView":
                    payload = entry[1]
                    if isinstance(payload, (list, tuple)):
                        for item in payload:
                            if isinstance(item, str) and len(item) > 100:
                                return item
        except Exception:
            pass
        return ""


class GetOffersAction(BaseAction):
    """Fetch all sell offers available at a Branch Office for a given resource."""

    def execute(
        self,
        buyer_city_id: int,
        buyer_branchoffice_pos: int,
        resource_idx: int,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Return a list of available sell offers sorted cheapest-first.

        Each item: {seller_city_id, seller_bo_pos, offer_type, amount, price_per_unit, resource_str}
        """
        resource_str = _IDX_TO_RESOURCE_STR.get(resource_idx, "resource")
        html = self._get_branch_office_html(buyer_city_id, buyer_branchoffice_pos, resource_str)
        return self._parse_all_offers(html, resource_str)

    def _get_branch_office_html(self, city_id: int, bo_pos: int, resource_str: str) -> str:
        params = {
            "view": "branchOffice",
            "cityId": city_id,
            "position": bo_pos,
            "currentCityId": city_id,
            "activeTab": "bargain",
            "type": "444",
            "searchResource": resource_str,
            "range": 24,
            "backgroundView": "city",
            "templateView": "branchOffice",
            "currentTab": "bargain",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        html = self._extract_html_from_response(resp)
        if not html:
            logger.warning("GetOffers: could not parse branchOffice HTML")
        return html

    def _extract_html_from_response(self, resp: Any) -> str:
        """Extract the first large HTML string from a changeView-style AJAX response."""
        try:
            data = resp.json()
            for entry in data:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2 and entry[0] == "changeView":
                    payload = entry[1]
                    if isinstance(payload, (list, tuple)):
                        for item in payload:
                            if isinstance(item, str) and len(item) > 100:
                                return item
        except Exception:
            pass
        return ""

    def _parse_all_offers(self, html: str, resource_str: str) -> list[dict[str, Any]]:
        """Parse offer rows from Branch Office listing HTML.

        Each row looks like:
          <td ...>AMOUNT</td>
          <td ...>PRICE</td>
          ...href="?view=takeOffer&...&destinationCityId=SELLER&cityId=BUYER&position=BUYER_POS&type=TYPE&resource=RES"
        """
        offers: list[dict[str, Any]] = []
        # Match each offer block: amount, price, then the href
        # takeOffer href: destinationCityId=SELLER_CITY, cityId=BUYER_CITY
        pattern = re.compile(
            r'class="[^"]*amount[^"]*"[^>]*>\s*([\d\s.]+)\s*<'
            r'|class="[^"]*price[^"]*"[^>]*>\s*([\d\s.]+)\s*<'
            r'|href="\?view=takeOffer[^"]*&destinationCityId=(\d+)[^"]*&type=(\d+)&resource=(\w+)"',
            re.IGNORECASE,
        )
        amount = price = None
        for m in pattern.finditer(html):
            if m.group(1) is not None:
                try:
                    amount = int(m.group(1).replace(" ", "").replace(".", ""))
                except ValueError:
                    amount = None
            elif m.group(2) is not None:
                try:
                    price = int(m.group(2).replace(" ", "").replace(".", ""))
                except ValueError:
                    price = None
            elif m.group(5) == resource_str and m.group(3):
                if amount is not None and price is not None:
                    offers.append({
                        "seller_city_id": int(m.group(3)),   # destinationCityId = seller
                        "offer_type": int(m.group(4)),
                        "amount": amount,
                        "price_per_unit": price,
                        "resource_str": resource_str,
                    })
                amount = price = None

        offers.sort(key=lambda o: o["price_per_unit"])
        logger.info("GetOffers: %d offers for %s", len(offers), resource_str)
        return offers


class SellAction(BaseAction):
    """Sell goods to another player's buy offer on the Branch Office.

    NOTE: For internal market use, CreateOfferAction is preferred (we create
    our own offer and the buyer comes to us). SellAction is for the case where
    an external buy order already exists and we want to fulfil it.
    """

    def execute(
        self,
        city_id: int,
        branchoffice_pos: int,
        destination_city_id: int,
        resource_idx: int,
        amount: int,
        price: int,
        player_name: str = "",
        dest_city_name: str = "",
        offer_type: str = "333",
        ships_available: int = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Sell to an existing buy offer from another player.

        Args:
            city_id: Seller's city.
            branchoffice_pos: Seller's Branch Office slot.
            destination_city_id: Buyer's city (where goods will go).
            resource_idx: Resource type (0–4).
            amount: Units to sell.
            price: Price per unit.
            player_name: Buyer's player name (from offer listing).
            dest_city_name: Buyer's city name (from offer listing).
            offer_type: Offer type identifier from the listing (usually "333").
            ships_available: Number of ships currently available.

        Returns:
            Parsed AJAX response.
        """
        ships = max(1, math.ceil(amount / 5))

        params: dict[str, Any] = {
            "cityId": city_id,
            "destinationCityId": destination_city_id,
            "oldView": "branchOffice",
            "position": branchoffice_pos,
            "avatar2Name": player_name,
            "city2Name": dest_city_name,
            "type": offer_type,
            "activeTab": "bargain",
            "transportDisplayPrice": "0",
            "premiumTransporter": "0",
            "normalTransportersMax": ships_available,
            "capacity": "5",
            "max_capacity": "5",
            "jetPropulsion": "0",
            "transporters": str(ships),
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": "takeOffer",
            "currentTab": "bargain",
        }
        if resource_idx == 0:
            params["cargo_resource"] = amount
            params["resourcePrice"] = price
        else:
            params[f"cargo_tradegood{resource_idx}"] = amount
            params[f"tradegood{resource_idx}Price"] = price

        return self._ajax_request(ActionID.MARKETPLACE_SELL, params)
