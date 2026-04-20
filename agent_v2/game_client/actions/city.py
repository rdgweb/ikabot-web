"""City and building actions — build, upgrade, demolish."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import parse_qsl, urlparse

from ..constants import ActionID, BUILDING_TYPES
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)


def _extract_change_view_html(response_text: str, *, action: str) -> str:
    try:
        data = json.loads(response_text)
    except Exception as exc:
        raise ActionError(f"Invalid {action} response", action=action) from exc

    VIEW_CMDS = {"changeView", "loadContent", "updateTemplate", "updateTemplateData"}
    all_cmds = []
    for cmd in data:
        if not isinstance(cmd, (list, tuple)) or len(cmd) < 2:
            continue
        cmd_name = cmd[0]
        all_cmds.append(cmd_name)
        if cmd_name not in VIEW_CMDS:
            continue
        payload = cmd[1]
        # List-of-pairs format: [["selector", "<html>", ...], ...]
        if isinstance(payload, (list, tuple)):
            for candidate in reversed(payload):
                if isinstance(candidate, str) and "<" in candidate:
                    return candidate
                if isinstance(candidate, (list, tuple)):
                    for sub in candidate:
                        if isinstance(sub, str) and "<" in sub:
                            return sub
        # String payload
        if isinstance(payload, str) and "<" in payload:
            return payload
        # Dict payload: {"id": "...", "html": "<...>"} (updateTemplateData format)
        if isinstance(payload, dict):
            for key in ("html", "content", "template"):
                val = payload.get(key)
                if isinstance(val, str) and "<" in val:
                    return val

    # Dump full response for diagnosis
    logger.warning("%s no HTML in view cmds. Commands: %s", action, all_cmds)
    logger.warning("  raw response (first 1000): %r", response_text[:1000])
    for cmd in data:
        if isinstance(cmd, (list, tuple)) and len(cmd) >= 1 and cmd[0] in VIEW_CMDS:
            logger.warning("  %s len=%d repr: %r", cmd[0], len(cmd), cmd[:5])
    raise ActionError(f"{action} response missing changeView HTML — commands: {all_cmds}", action=action)


def _parse_href_params(href: str) -> dict[str, Any]:
    query = urlparse(href).query or href.lstrip("?")
    parsed = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        parsed[key] = value
    return parsed


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

    def _resolve_build_href(self, city_id: int, position: int, building_type: str) -> dict[str, Any]:
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
            "GET",
            self.client._server_url,
            params=params,
            headers=dict(self.client.session.headers) | {
                "Accept": "text/plain, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        # Check for locked position before attempting HTML extraction.
        # The game returns updateTemplateData '' + provideFeedback type=11 for locked slots.
        try:
            _resp_data = json.loads(resp.text)
        except Exception:
            _resp_data = []
        for _cmd in _resp_data:
            if isinstance(_cmd, (list, tuple)) and len(_cmd) >= 2 and _cmd[0] == "updateGlobalData":
                _bg = ((_cmd[1] or {}).get("backgroundData") or {}) if isinstance(_cmd[1], dict) else {}
                _locked = _bg.get("lockedPosition") or {}
                if str(position) in _locked:
                    raise ActionError(
                        f"position_locked:{position}:{_locked[str(position)]}",
                        action="build",
                    )
                break

        html = _extract_change_view_html(resp.text, action="build")

        # Capture the new actionRequest the server embedded in this response so
        # the caller can use it for the next request without a separate refresh.
        data = json.loads(resp.text)
        for cmd in data:
            if isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == "updateGlobalData":
                new_ar = (cmd[1] or {}).get("actionRequest") if isinstance(cmd[1], dict) else None
                if new_ar:
                    self.client._action_request = new_ar
                    logger.info("buildingGround AR updated: %s...", new_ar[:8])
                else:
                    logger.warning("buildingGround updateGlobalData found but no actionRequest in: %s", cmd[1])
                break

        matches = re.findall(
            r'<li class="building ([^"]+)">[\s\S]*?<a[^>]+href="([^"]+)"[^>]+data-building="(\d+)"',
            html,
            re.IGNORECASE,
        )
        logger.info("buildingGround matches for pos=%s: %s", position, matches[:10])
        wanted = self._normalize_building_type(building_type)

        for candidate, href, building_id in matches:
            if self._normalize_building_type(candidate.strip()) == wanted:
                # Log 400 chars of HTML around the matched buildingId to understand JS/onclick structure
                _idx = html.find(f"buildingId={building_id}")
                _ctx = html[max(0, _idx-200):_idx+200] if _idx >= 0 else ""
                logger.info("HTML context around buildingId=%s: %r", building_id, _ctx)
                href_params = _parse_href_params(href)
                href_params.setdefault("action", ActionID.BUILD)
                href_params.setdefault("cityId", str(city_id))
                href_params.setdefault("position", str(position))
                href_params.setdefault("building", str(building_id))
                return href_params

        # No matches: slot is occupied or construction queue is full.
        # Never fall back to hardcoded IDs — they diverge from game values and cause
        # silent wrong-building submissions.
        raise ActionError(
            f"Building option not available for slot pos={position}: {building_type}"
            f" — queue may be full or slot occupied",
            action="build",
        )

    def execute(
        self,
        city_id: int,
        building_type: str,
        position: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build a new building using the exact button href from buildingGround."""
        building_type = self._normalize_building_type(building_type)
        build_params = self._resolve_build_href(city_id, position, building_type)
        # The game embeds the pre-buildingGround AR in each build button href.
        # _resolve_build_href updates client._action_request from updateGlobalData,
        # but that AR is for subsequent navigation — the build itself needs the href AR.
        href_ar = build_params.pop("actionRequest", None)
        action_name = build_params.pop("action", ActionID.BUILD)
        if href_ar:
            self.client._action_request = href_ar
        logger.info(
            "Building %s at pos %s city %s with params=%s",
            building_type,
            position,
            city_id,
            build_params,
        )
        return self._ajax_request(action_name, build_params)


class UpgradeAction(BaseAction):
    """Upgrade an existing building to the next level."""

    def _resolve_upgrade_href(
        self,
        city_id: int,
        building_position: int,
        template_view: str,
    ) -> dict[str, Any]:
        params = {
            "view": template_view,
            "cityId": str(city_id),
            "position": str(building_position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "ajax": "1",
        }
        resp = self.client._request(
            "GET",
            self.client._server_url,
            params=params,
            headers=dict(self.client.session.headers) | {
                "Accept": "text/plain, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        html = _extract_change_view_html(resp.text, action="upgrade")
        match = re.search(
            r'<a[^>]+id="js_buildingUpgradeButton"[^>]+href="([^"]+)"',
            html,
            re.IGNORECASE,
        )
        if not match:
            raise ActionError("Upgrade button href not found", action="upgrade")
        return _parse_href_params(match.group(1))

    def execute(
        self,
        city_id: int,
        building_position: int,
        current_level: int | None = None,
        template_view: str = "city",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Upgrade a building using the exact upgrade button href from the page."""
        logger.info(f"Upgrading building at position {building_position} in city {city_id}")
        upgrade_params = self._resolve_upgrade_href(city_id, building_position, template_view)
        href_level = upgrade_params.get("level")
        if current_level is not None and href_level and str(href_level) != str(current_level):
            logger.warning(
                "Upgrade href level mismatch for city=%s pos=%s: href=%s current=%s",
                city_id,
                building_position,
                href_level,
                current_level,
            )
        explicit_ar = upgrade_params.pop("actionRequest", "")
        action_name = upgrade_params.pop("action", ActionID.UPGRADE_BUILDING)
        old_ar = self.client._action_request
        if explicit_ar:
            self.client._action_request = explicit_ar
        try:
            # UpgradeExistingBuilding uses GET (ajaxHandlerCall with href in game UI)
            if action_name == "UpgradeExistingBuilding":
                upgrade_params["action"] = action_name
                upgrade_params["actionRequest"] = self.client._action_request
                upgrade_params["ajax"] = "1"
                return self.client._ajax_get(action_name, upgrade_params)
            return self._ajax_request(action_name, upgrade_params)
        finally:
            self.client._action_request = old_ar


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

        params = {
            "cityId": city_id,
            "position": building_position,
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": template_view,
            "building": template_view,
        }

        return self._ajax_request(ActionID.DEMOLISH, params)
