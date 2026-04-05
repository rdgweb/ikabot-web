"""Marketplace actions — buy offers, sell resources."""

from __future__ import annotations

import logging
from typing import Any

from ..constants import ActionID
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)


class BuyAction(BaseAction):
    """Buy a resource offer from the marketplace."""

    def execute(
        self,
        city_id: int,
        offer_id: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Buy a marketplace offer.

        Args:
            city_id: City to receive the purchased resources.
            offer_id: Marketplace offer ID to purchase.

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the purchase fails.
        """
        logger.info(f"Buying marketplace offer {offer_id} for city {city_id}")

        # TODO: Verify the exact parameter names from game AJAX traffic
        # Marketplace purchases require available trade ships for transport
        params = {
            "cityId": city_id,
            "offerId": offer_id,
        }

        return self._ajax_request(ActionID.MARKETPLACE_BUY, params)


class SellAction(BaseAction):
    """Create a sell offer on the marketplace."""

    def execute(
        self,
        city_id: int,
        resource_type: int,
        amount: int,
        price: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a marketplace sell offer.

        Args:
            city_id: City to sell resources from.
            resource_type: Resource type index (see ResourceType enum).
            amount: Amount of resources to sell.
            price: Price per unit in gold.

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the sell offer creation fails.
        """
        if amount <= 0:
            raise ActionError("Sell amount must be positive", action="sell")
        if price <= 0:
            raise ActionError("Sell price must be positive", action="sell")

        logger.info(
            f"Creating sell offer in city {city_id}: "
            f"type={resource_type}, amount={amount}, price={price}"
        )

        # TODO: Verify the exact parameter names from game AJAX traffic
        params = {
            "cityId": city_id,
            "resourceType": resource_type,
            "amount": amount,
            "price": price,
        }

        return self._ajax_request(ActionID.MARKETPLACE_SELL, params)
