"""City and building actions — build, upgrade, demolish."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..constants import ActionID, BUILDING_TYPES
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)


class BuildAction(BaseAction):
    """Build a new building in an empty city slot."""

    @staticmethod
    def _normalize_building_type(building_type: str) -> str:
        key = str(building_type or "").strip().split()[0]
        aliases = {
            "hideout": "safehouse",
            "governorsResidence": "palaceColony",
            "chronos_forge": "chronosForge",
            "marketplace": "branchOffice",
        }
        return aliases.get(key, key)

    def _resolve_building_id(self, city_id: int, position: int, building_type: str) -> int:
        params = {
            "view": "buildingGround",
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request(
            "POST",
            self.client._server_url,
            data=params,
            headers=dict(self.client.session.headers) | {
                "Accept": "text/plain, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        try:
            data = json.loads(resp.text)
            html = data[1][1][1]
        except Exception as exc:
            raise ActionError("Invalid buildingGround response", action="build") from exc

        matches = re.findall(
            r'<li class="building ([^"]+)">[\s\S]*?buildingId=(\d+)&',
            html,
            re.IGNORECASE,
        )
        wanted = self._normalize_building_type(building_type)
        for candidate, building_id in matches:
            if self._normalize_building_type(candidate.strip()) == wanted:
                return int(building_id)

        if building_type in BUILDING_TYPES:
            return BUILDING_TYPES[building_type]
        raise ActionError(f"Building option not available for slot: {building_type}", action="build")

    def execute(
        self,
        city_id: int,
        building_type: str,
        position: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build a new building.

        Args:
            city_id: Target city ID.
            building_type: Building type name (must be in BUILDING_TYPES).
            position: City slot position to build in.

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the building type is unknown or the action fails.
        """
        building_type = self._normalize_building_type(building_type)
        building_id = self._resolve_building_id(city_id, position, building_type)
        logger.info(f"Building {building_type} at position {position} in city {city_id}")

        params = {
            "cityId": city_id,
            "position": position,
            "building": building_id,
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": "buildingGround",
        }

        return self._ajax_request(ActionID.BUILD, params)


class UpgradeAction(BaseAction):
    """Upgrade an existing building to the next level."""

    def execute(
        self,
        city_id: int,
        building_position: int,
        current_level: int | None = None,
        template_view: str = "city",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Upgrade a building.

        Args:
            city_id: Target city ID.
            building_position: Position of the building to upgrade.

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the upgrade action fails.
        """
        logger.info(f"Upgrading building at position {building_position} in city {city_id}")

        # TODO: Verify the exact parameter names from game AJAX traffic
        params = {
            "cityId": city_id,
            "position": building_position,
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": template_view,
            "activeTab": "tabSendTransporter",
        }
        if current_level is not None:
            params["level"] = current_level

        return self._ajax_request(ActionID.UPGRADE_BUILDING, params)


class DemolishAction(BaseAction):
    """Demolish (downgrade) an existing building by one level."""

    def execute(
        self,
        city_id: int,
        building_position: int,
        template_view: str = "city",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Demolish a building level.

        Args:
            city_id: Target city ID.
            building_position: Position of the building to demolish.

        Returns:
            Parsed AJAX response.

        Raises:
            ActionError: If the demolish action fails.
        """
        logger.info(f"Demolishing building at position {building_position} in city {city_id}")

        # TODO: Verify the exact parameter names from game AJAX traffic
        params = {
            "cityId": city_id,
            "position": building_position,
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": template_view,
        }

        return self._ajax_request(ActionID.DEMOLISH, params)
