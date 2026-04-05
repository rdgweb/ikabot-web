"""Military actions — train troops, send troops, attack."""

from __future__ import annotations

import logging
from typing import Any

from ..constants import ActionID
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)


class TrainAction(BaseAction):
    """Train military units at barracks or shipyard."""

    def execute(
        self,
        city_id: int,
        units: dict[str, int],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Train units in a city.

        Args:
            city_id: City where the barracks/shipyard is located.
            units: Dictionary mapping unit type name to quantity.
                   Example: {"hoplite": 50, "slinger": 100}

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the training action fails.
        """
        if not units:
            raise ActionError("No units specified for training", action="train")

        logger.info(f"Training units in city {city_id}: {units}")

        # TODO: Map unit type names to internal game IDs
        # TODO: Verify the exact parameter format from game AJAX traffic
        # The game typically expects parameters like:
        #   unit_type_1=count&unit_type_2=count&...
        params: dict[str, Any] = {
            "cityId": city_id,
        }

        # TODO: Add unit parameters in the game's expected format
        for unit_type, count in units.items():
            params[unit_type] = count

        return self._ajax_request(ActionID.TRAIN_UNITS, params)


class AttackAction(BaseAction):
    """Send an attack against another city."""

    def execute(
        self,
        from_city: int,
        target_city: int,
        units: dict[str, int],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Launch an attack.

        Args:
            from_city: Source city ID.
            target_city: Target city ID to attack.
            units: Dictionary mapping unit type name to quantity to send.

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the attack action fails.
        """
        if not units:
            raise ActionError("No units specified for attack", action="attack")

        logger.info(f"Attacking city {target_city} from city {from_city} with {units}")

        # TODO: Verify the exact parameter names and attack flow
        # Attacks may require multiple steps (choose units → confirm → send)
        params: dict[str, Any] = {
            "cityId": from_city,
            "targetCityId": target_city,
            "type": "attack",
        }

        # TODO: Add unit parameters in the game's expected format
        for unit_type, count in units.items():
            params[unit_type] = count

        return self._ajax_request(ActionID.DEPLOY_FLEET, params)


class SendTroopsAction(BaseAction):
    """Send troops to garrison another city (friendly)."""

    def execute(
        self,
        from_city: int,
        to_city: int,
        units: dict[str, int],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send troops to garrison a friendly city.

        Args:
            from_city: Source city ID.
            to_city: Destination city ID (own or ally).
            units: Dictionary mapping unit type name to quantity.

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the troop send action fails.
        """
        if not units:
            raise ActionError("No units specified for deployment", action="send_troops")

        logger.info(f"Sending troops from city {from_city} to city {to_city}: {units}")

        # TODO: Verify the exact parameter names from game AJAX traffic
        params: dict[str, Any] = {
            "cityId": from_city,
            "targetCityId": to_city,
            "type": "garrison",
        }

        for unit_type, count in units.items():
            params[unit_type] = count

        return self._ajax_request(ActionID.SEND_TROOPS, params)
