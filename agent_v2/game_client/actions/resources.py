"""Resource actions — donate, send resources, collect."""

from __future__ import annotations

import logging
from typing import Any

from ..constants import ActionID, ResourceType
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)


class DonateAction(BaseAction):
    """Donate resources to an island project (forest or tradegood).

    Real Ikariam AJAX (from game traffic):
        POST /index.php
        action=IslandScreen  function=donate
        islandId=X  type=resource|tradegood  donation=N
        backgroundView=island  templateView=resource|tradegood
        actionRequest=XXX  ajax=1
    """

    def execute(
        self,
        island_id: int | str,
        donation_type: str,
        amount: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Donate resources to the island.

        Args:
            island_id: Island ID where the donation goes.
            donation_type: "resource" (forest/wood) or "tradegood" (island luxury).
            amount: Amount of resources to donate.

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the donation fails.
        """
        if amount <= 0:
            raise ActionError("Donation amount must be positive", action="donate")

        if donation_type not in ("resource", "tradegood"):
            raise ActionError(
                f"Invalid donation_type: {donation_type!r} (expected 'resource' or 'tradegood')",
                action="donate",
            )

        logger.info(f"Donating {amount} ({donation_type}) to island {island_id}")

        params = {
            "islandId": str(island_id),
            "type": donation_type,
            "donation": str(amount),
            "backgroundView": "island",
            "templateView": donation_type,
        }

        return self._ajax_request(ActionID.DONATE, params)


class SendResourcesAction(BaseAction):
    """Send resources from one city to another via trade ships."""

    def execute(
        self,
        from_city: int,
        to_city: int,
        resources: dict[str, int],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send resources between cities.

        Args:
            from_city: Source city ID.
            to_city: Destination city ID.
            resources: Dictionary mapping resource name to amount.
                       Example: {"wood": 5000, "wine": 2000}

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the resource transport fails.
        """
        total = sum(resources.values())
        if total <= 0:
            raise ActionError("Must send at least some resources", action="send_resources")

        logger.info(f"Sending resources from city {from_city} to city {to_city}: {resources}")

        # TODO: Verify the exact parameter names from game AJAX traffic
        # Transport requires available trade ships
        params: dict[str, Any] = {
            "cityId": from_city,
            "destinationCityId": to_city,
        }

        # Map resource names to the game's expected parameter format
        resource_key_map = {
            "wood": "cargo_resource",
            "wine": "cargo_tradegood1",
            "marble": "cargo_tradegood2",
            "crystal": "cargo_tradegood3",
            "sulfur": "cargo_tradegood4",
        }

        for res_name, amount in resources.items():
            key = resource_key_map.get(res_name)
            if key:
                params[key] = amount
            else:
                logger.warning(f"Unknown resource name for transport: {res_name}")

        return self._ajax_request(ActionID.SEND_RESOURCES, params)


class CollectAction(BaseAction):
    """Collect resources or rewards (e.g., from daily bonus, piracy)."""

    def execute(
        self,
        city_id: int,
        collect_type: str = "daily",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Collect available resources or rewards.

        Args:
            city_id: City to collect in.
            collect_type: Type of collection ("daily", "piracy", etc.).

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the collection fails.
        """
        logger.info(f"Collecting {collect_type} resources in city {city_id}")

        # TODO: Implement collection logic based on collect_type
        # Different collection types use different AJAX actions:
        #   - Daily bonus: specific action endpoint
        #   - Piracy: PiracyScreen action
        #   - Quests: QuestAction
        if collect_type == "piracy":
            params = {"cityId": city_id}
            return self._ajax_request(ActionID.PIRACY, params)

        # TODO: Implement other collection types
        raise ActionError(
            f"Collection type '{collect_type}' not yet implemented",
            action="collect",
        )
