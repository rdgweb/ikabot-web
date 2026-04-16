"""Marketplace actions — create sell offers, buy from offers.

Action parameter reference (verified from ikabot upstream source):

  Create/update own sell offer:
    action=CityScreen&function=updateOffers
    cityId, position (Branch Office slot), resourceTradeType=444,
    resource / tradegoodN (amount), resourcePrice / tradegoodNPrice,
    backgroundView=city, currentCityId, templateView=branchOfficeOwnOffers,
    currentTab=tab_branchOfficeOwnOffers

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
            unit_price: Price per unit in gold.

        Returns:
            Parsed AJAX response.
        """
        if amount <= 0:
            raise ActionError("Sell amount must be positive", action="updateOffers")
        if unit_price <= 0:
            raise ActionError("Sell price must be positive", action="updateOffers")
        if resource_idx not in range(5):
            raise ActionError(f"Invalid resource_idx={resource_idx}", action="updateOffers")

        logger.info(
            "Creating sell offer city=%s res=%s amount=%s price=%s",
            city_id, resource_idx, amount, unit_price,
        )

        # Base params — all resources default to 0 / placeholder price
        params: dict[str, Any] = {
            "cityId": city_id,
            "position": branchoffice_pos,
            "resourceTradeType": "444",
            "resource": "0",
            "resourcePrice": "10",
            "tradegood1TradeType": "444",
            "tradegood1": "0",
            "tradegood1Price": "11",
            "tradegood2TradeType": "444",
            "tradegood2": "0",
            "tradegood2Price": "12",
            "tradegood3TradeType": "444",
            "tradegood3": "0",
            "tradegood3Price": "17",
            "tradegood4TradeType": "444",
            "tradegood4": "0",
            "tradegood4Price": "5",
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": "branchOfficeOwnOffers",
            "currentTab": "tab_branchOfficeOwnOffers",
        }

        # Override the target resource
        if resource_idx == 0:
            params["resource"] = str(amount)
            params["resourcePrice"] = str(unit_price)
        else:
            params[f"tradegood{resource_idx}"] = str(amount)
            params[f"tradegood{resource_idx}Price"] = str(unit_price)

        return self._ajax_request(ActionID.MARKETPLACE_UPDATE_OFFERS, params)


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
            seller_bo_pos=offer["position"],
            offer_type=offer["type"],
            resource_str=resource_str,
            buyer_city_id=buyer_city_id,
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

        # Estimate ships needed (Ikariam default ship capacity = 5 units per ship)
        ship_capacity = 5
        ships = max(1, math.ceil(amount / ship_capacity))

        # Step 4: POST the purchase
        params: dict[str, Any] = {
            "cityId": offer["city_id"],         # seller's city
            "destinationCityId": buyer_city_id,  # buyer's city (where goods arrive)
            "oldView": "branchOffice",
            "position": buyer_branchoffice_pos,  # buyer's BO slot
            "avatar2Name": offer.get("player_name", ""),
            "city2Name": offer.get("city_name", ""),
            "type": offer["type"],
            "activeTab": "bargain",
            "transportDisplayPrice": 0,
            "premiumTransporter": 0,
            "normalTransportersMax": ships,
            "capacity": 5,
            "max_capacity": 5,
            "jetPropulsion": 0,
            "transporters": ships,
            "backgroundView": "city",
            "currentCityId": offer["city_id"],
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
        try:
            data = resp.json()
            return data[1][1][1]
        except (ValueError, IndexError, TypeError, KeyError):
            logger.warning("Could not parse branchOffice HTML from response")
            return ""

    def _find_offer_in_listing(
        self, html: str, seller_city_id: int, resource_str: str
    ) -> dict | None:
        """Search Branch Office listing HTML for an offer from seller_city_id."""
        # Each offer row has a link like:
        # href="?view=takeOffer&destinationCityId=D&oldView=branchOffice&activeTab=bargain
        #       &cityId=C&position=P&type=T&resource=R"
        pattern = (
            r'class="short_text80">(.*?)\s*<br'  # city_name
            r'[\s\S]{0,100}?\((.*?)\)'            # player_name
            r'[\s\S]*?'
            r'href="\?view=takeOffer'
            r'&destinationCityId=(\d+)'
            r'&oldView=branchOffice&activeTab=bargain'
            r'&cityId=(\d+)'
            r'&position=(\d+)'
            r'&type=(\d+)'
            r'&resource=(\w+)"'
        )
        for m in re.finditer(pattern, html, re.DOTALL):
            city_name, player_name, dest_city_id, city_id, position, offer_type, resource = (
                m.groups()
            )
            if str(city_id) == str(seller_city_id) and resource == resource_str:
                return {
                    "city_name": city_name.strip(),
                    "player_name": player_name.strip(),
                    "destination_city_id": int(dest_city_id),
                    "city_id": int(city_id),
                    "position": int(position),
                    "type": int(offer_type),
                    "resource": resource,
                }
        return None

    def _get_take_offer_html(
        self,
        seller_city_id: int,
        seller_bo_pos: int,
        offer_type: int,
        resource_str: str,
        buyer_city_id: int,
    ) -> str:
        """Fetch the takeOffer dialog HTML (contains transport price inputs)."""
        params = {
            "view": "takeOffer",
            "destinationCityId": buyer_city_id,
            "oldView": "branchOffice",
            "activeTab": "bargain",
            "cityId": seller_city_id,
            "position": seller_bo_pos,
            "type": offer_type,
            "resource": resource_str,
            "backgroundView": "city",
            "currentCityId": seller_city_id,
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
        try:
            data = resp.json()
            return data[1][1][1]
        except (ValueError, IndexError, TypeError, KeyError):
            logger.warning("Could not parse takeOffer HTML from response")
            return ""


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
