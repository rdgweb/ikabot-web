"""Piracy / Pirate Fortress game actions.

API reference (captured 2026-05-06 from s78-br):

  Get state (GET):
    view=pirateFortress&cityId=<id>&position=17&activeTab=tabBootyQuest
    &backgroundView=city&currentCityId=<id>&actionRequest=<token>&ajax=1

  State is in templateData.load_js.params (JSON string):
    buildingLevel        int   — pirate fortress level
    capturePoints        str   — current unspent capture points
    crewPoints           str   — current crew strength (spent points)
    basicCrewPoints      int   — base crew points
    completeCrewPoints   int   — total crew strength
    crewConversionFactor int   — points needed per crew unit (e.g. 10)
    ongoingMissionTimeRemaining  int  — seconds until ship returns (0 = in port)
    pirateCaptureLevels  list  — available missions (by buildingLevel):
      buildingLevel  int    — min fortress level to unlock
      duration       int    — seconds
      gold           int    — reward gold
      capturePoints  int    — reward capture points
      name           str    — mission name (in game language)
      picActive      str    — filename for active ship image
      picInactive    str    — filename for inactive ship image

  Start mission (POST):
    action=PiracyScreen&function=capture
    buildingLevel=<mission_level>
    view=pirateFortress&cityId=<id>&position=17
    activeTab=tabBootyQuest&backgroundView=city&currentCityId=<id>
    actionRequest=<token>&ajax=1

    Response: provideFeedback "Sua ordem foi executada." on success
    If captcha required: createCaptcha entry present in response

  Convert points (POST):
    action=PiracyScreen&function=convert
    crewPoints=<amount>
    view=pirateFortress&cityId=<id>&position=17
    activeTab=tabCrew&backgroundView=city&currentCityId=<id>
    actionRequest=<token>&ajax=1

Notes:
  - position=17 is the pirate fortress building position on the city map
  - Images served at: https://s{N}-{lang}.ikariam.gameforge.com/skin/piracy/{filename}
  - All missions require fortress_level >= buildingLevel to be available
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..constants import ActionID, GAME_AJAX_HEADERS
from ..exceptions import ActionError, CaptchaRequiredError
from .base_action import BaseAction

logger = logging.getLogger(__name__)

PIRACY_POSITION = 17


def _extract_js_params(response_data: list) -> dict[str, Any]:
    """Extract the piracy JS params dict from the AJAX response."""
    for entry in response_data:
        if not (isinstance(entry, list) and len(entry) >= 2):
            continue
        if entry[0] != "updateTemplateData":
            continue
        td = entry[1]
        if not isinstance(td, dict):
            continue
        lj = td.get("load_js") or {}
        params_str = lj.get("params") if isinstance(lj, dict) else ""
        if params_str:
            try:
                return json.loads(params_str)
            except (json.JSONDecodeError, TypeError):
                pass
    return {}


class PiracyStateAction(BaseAction):
    """Read the pirate fortress state for a city."""

    def execute(self, city_id: int | str, **kwargs: Any) -> dict[str, Any]:
        """Fetch piracy state.

        Returns:
            {
                "fortress_level": int,
                "capture_points": int,
                "crew_points": int,
                "complete_crew_points": int,
                "conversion_factor": int,
                "conversion_time_base": int,
                "conversion_time_per_unit": int,
                "mission_active": bool,
                "time_remaining": int,   # seconds until ship returns
                "missions": list[dict],  # pirateCaptureLevels
            }
        """
        params = {
            "view": "pirateFortress",
            "cityId": str(city_id),
            "position": str(PIRACY_POSITION),
            "activeTab": "tabBootyQuest",
            "backgroundView": "city",
            "currentCityId": str(city_id),
        }
        headers = dict(GAME_AJAX_HEADERS)
        resp = self.client._request("GET", self.client._server_url, params={
            **params,
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }, headers=headers)
        # Update action request token from response
        self.client._update_action_request(resp.text)
        try:
            data = resp.json()
        except Exception as exc:
            raise ActionError(f"Failed to parse piracy state response: {exc}", action="pirateFortress")

        js = _extract_js_params(data)
        if not js:
            raise ActionError("No piracy JS params in response", action="pirateFortress")

        time_remaining = int(js.get("ongoingMissionTimeRemaining") or 0)
        return {
            "fortress_level": int(js.get("buildingLevel") or 0),
            "capture_points": int(js.get("capturePoints") or 0),
            "crew_points": int(js.get("crewPoints") or 0),
            "basic_crew_points": int(js.get("basicCrewPoints") or 0),
            "complete_crew_points": int(js.get("completeCrewPoints") or 0),
            "conversion_factor": int(js.get("crewConversionFactor") or 10),
            "conversion_time_base": int(js.get("crewConversionTimeBase") or 0),
            "conversion_time_per_unit": int(js.get("crewConversionTimePerUnit") or 0),
            "mission_active": time_remaining > 0,
            "time_remaining": time_remaining,
            "missions": js.get("pirateCaptureLevels") or [],
        }


class PiracyMissionAction(BaseAction):
    """Start a piracy mission (function=capture)."""

    def execute(
        self,
        city_id: int | str,
        building_level: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Start a piracy mission.

        Args:
            city_id: City with pirate fortress.
            building_level: Mission level (1,3,5,7,9,11,13,15,17).

        Returns:
            {
                "success": bool,
                "captcha_required": bool,
                "time_remaining": int,  # from new state
                "message": str,
            }
        """
        params = {
            "buildingLevel": str(building_level),
            "view": "pirateFortress",
            "cityId": str(city_id),
            "position": str(PIRACY_POSITION),
            "activeTab": "tabBootyQuest",
            "backgroundView": "city",
            "currentCityId": str(city_id),
        }
        data = self._ajax_request(f"{ActionID.PIRACY}&function=capture", params)

        # Check for captcha
        captcha_required = any(
            isinstance(e, list) and e[0] in ("createCaptcha", "provideCaptcha")
            for e in data
        )
        if captcha_required:
            raise CaptchaRequiredError(
                "Piracy mission requires captcha",
                captcha_type="pirate",
                city_id=int(city_id),
            )

        # Check feedback
        success = False
        message = ""
        for entry in data:
            if isinstance(entry, list) and entry[0] == "provideFeedback":
                feedback_list = entry[1] or []
                for fb in feedback_list:
                    if isinstance(fb, dict):
                        message = fb.get("text", "")
                        if fb.get("type") == 10:
                            success = True

        # Get new state
        js = _extract_js_params(data)
        time_remaining = int(js.get("ongoingMissionTimeRemaining") or 0)

        return {
            "success": success or (not message),  # silent success if no error
            "captcha_required": False,
            "time_remaining": time_remaining,
            "message": message,
        }


class PiracyConvertAction(BaseAction):
    """Convert capture points to crew strength (function=convert)."""

    def execute(
        self,
        city_id: int | str,
        crew_points: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Convert capture points to crew strength.

        Args:
            city_id: City with pirate fortress.
            crew_points: Amount of crew units to create.
        """
        params = {
            "crewPoints": str(crew_points),
            "view": "pirateFortress",
            "cityId": str(city_id),
            "position": str(PIRACY_POSITION),
            "activeTab": "tabCrew",
            "backgroundView": "city",
            "currentCityId": str(city_id),
        }
        data = self._ajax_request(f"{ActionID.PIRACY}&function=convert", params)
        js = _extract_js_params(data)
        return {
            "capture_points": int(js.get("capturePoints") or 0),
            "crew_points": int(js.get("crewPoints") or 0),
            "complete_crew_points": int(js.get("completeCrewPoints") or 0),
        }
