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
    If captcha required: the capture response carries the captcha. Since
    2026-09 the image comes embedded as a base64 data URI in
    updateTemplateData.js_captchaImage.src (N-83; same change as ikabot
    upstream #440). The old GET action=Options&function=createCaptcha now
    answers a JSON "reload" instead of the PNG.

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

import base64
import json
import logging
import re
import time
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


def _response_has_captcha_signal(response_text: str, response_data: Any) -> bool:
    """Detect captcha in both raw AJAX text and decoded JSON fragments."""
    combined = response_text or ""
    try:
        combined += json.dumps(response_data)
    except (TypeError, ValueError):
        pass
    return any(
        token in combined
        for token in (
            "function=createCaptcha",
            "createCaptcha",
            "js_captchaImage",
            "captchaNeeded",
            "showCaptcha",
        )
    )


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_DATA_URI_PNG = re.compile(r"data:image/png;base64,([A-Za-z0-9+/=]+)")
_RAW_DATA_URI_PNG = re.compile(r"data:image\\?/png;base64,([A-Za-z0-9+/=\\]+)")


def is_png_bytes(image: Any) -> bool:
    """True only for a non-empty PNG byte string (what the captcha solvers expect)."""
    return isinstance(image, (bytes, bytearray)) and len(image) > len(_PNG_MAGIC) and bytes(image[:8]) == _PNG_MAGIC


def _decode_png(b64: str) -> bytes | None:
    try:
        decoded = base64.b64decode(b64)
    except Exception:
        return None
    return decoded if is_png_bytes(decoded) else None


def extract_captcha_image(response_text: str, response_data: Any = None) -> bytes | None:
    """PNG bytes of the piracy captcha embedded in the capture response (None = not there)."""
    if isinstance(response_data, list):
        for entry in response_data:
            if not (isinstance(entry, list) and len(entry) >= 2 and entry[0] == "updateTemplateData"):
                continue
            image = entry[1].get("js_captchaImage") if isinstance(entry[1], dict) else None
            src = image.get("src") if isinstance(image, dict) else None
            match = _DATA_URI_PNG.search(src) if isinstance(src, str) else None
            decoded = _decode_png(match.group(1)) if match else None
            if decoded:
                return decoded
    # Fallback: the data URI straight out of the raw (JSON-escaped) text.
    match = _RAW_DATA_URI_PNG.search(response_text or "")
    if match:
        return _decode_png(match.group(1).replace("\\", ""))
    return None


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
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("GET", self.client._server_url, params=params, headers=dict(GAME_AJAX_HEADERS))
        try:
            data = resp.json()
        except Exception as exc:
            raise ActionError(f"Failed to parse piracy state response: {exc}", action="pirateFortress")

        # Update action request token from response (same as _ajax_get does internally)
        for entry in data:
            if isinstance(entry, list) and entry[0] == "updateGlobalData" and isinstance(entry[1], dict):
                new_ar = entry[1].get("actionRequest")
                if new_ar:
                    self.client._action_request = str(new_ar)
                break

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
    """Start a piracy mission (function=capture) with captcha solving.

    Flow (per ikabot reference implementation):
    1. POST function=capture
    2. If the response asks for a captcha:
       a. Check "showPirateFortressShip":1 → crew still in town (captcha blocked start)
       b. Take the PNG embedded in the response (js_captchaImage); only when it is
          missing, try the old createCaptcha endpoint — accepted only if it really
          answers a PNG
       c. Solve via the hub (ikabotapi, Telegram/manual fallback)
       d. Re-POST with captchaNeeded=1&captcha=ANSWER
       e. Check showPirateFortressShip:0 → success
       With no image or no solution, POST capture again for a fresh captcha.
    3. If no captcha OR showPirateFortressShip:0 → crew departed → success
    """

    _MAX_CAPTCHA_RETRIES = 5
    _CAPTCHA_RETRY_WAIT = 5

    def _post_capture(self, params: dict[str, str]) -> tuple[str, Any]:
        """POST the capture directly (bypassing _request captcha detection: handled here)."""
        resp = self.client.session.post(
            self.client._server_url,
            data={**params, "actionRequest": self.client._action_request},
            headers=dict(GAME_AJAX_HEADERS),
            timeout=30,
        )
        resp_text = resp.text
        ar_m = re.search(r'"actionRequest"\s*:\s*"([a-f0-9]+)"', resp_text)
        if ar_m:
            self.client._action_request = ar_m.group(1)
        try:
            return resp_text, resp.json()
        except Exception:
            return resp_text, None

    def _legacy_captcha_image(self) -> bytes | None:
        """Old createCaptcha endpoint; None unless it really answers a PNG."""
        try:
            img_resp = self.client.session.get(
                self.client._server_url,
                params={"action": "Options", "function": "createCaptcha"},
                timeout=20,
            )
        except Exception as exc:
            logger.warning("Piracy: legacy captcha endpoint failed: %s", exc)
            return None
        return bytes(img_resp.content) if is_png_bytes(img_resp.content) else None

    def _solve_captcha(self, image: bytes, game_account_id: str) -> str:
        result = self.client.hub.create_captcha_challenge(
            "pirate",
            base64.b64encode(image).decode("ascii"),
            game_account_id=game_account_id,
        )
        solution = str(result.get("solution") or "").strip().upper()
        if not solution and result.get("challenge_id"):
            challenge_id = result["challenge_id"]
            logger.info("Piracy: aguardando resolucao do captcha #%d (Telegram/manual)", challenge_id)
            solution = self.client.hub.poll_captcha_solution(
                challenge_id, timeout_sec=120, interval=10
            ).strip().upper()
        return solution

    def execute(
        self,
        city_id: int | str,
        building_level: int,
        game_account_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        base_params = {
            "action": ActionID.PIRACY,
            "function": "capture",
            "buildingLevel": str(building_level),
            "view": "pirateFortress",
            "cityId": str(city_id),
            "position": str(PIRACY_POSITION),
            "activeTab": "tabBootyQuest",
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "pirateFortress",
            "ajax": "1",
        }

        resp_text, data = self._post_capture(base_params)
        if data is None:
            raise ActionError("Failed to parse piracy mission response", action="capture")

        # Captcha solve loop
        for attempt in range(self._MAX_CAPTCHA_RETRIES):
            if not _response_has_captcha_signal(resp_text, data):
                break  # No captcha in this response

            js = _extract_js_params(data)
            ship_flag = js.get("showPirateFortressShip")
            mission_started = int(js.get("ongoingMissionTimeRemaining") or 0) > 0 or (
                ship_flag is not None and str(ship_flag) != "1"
            )
            if mission_started:
                # Ship departed — mission started, captcha in response is for next attempt
                logger.info("Piracy: ship departed despite captcha in response (no action needed)")
                break

            # Captcha blocked the mission start — need to solve
            logger.info("Piracy: captcha required (attempt %d/%d)", attempt + 1, self._MAX_CAPTCHA_RETRIES)

            image = extract_captcha_image(resp_text, data) or self._legacy_captcha_image()
            solution = ""
            if image is None:
                # Never hand the solver something that is not the image (N-83).
                logger.warning("Piracy: captcha pedido mas sem imagem PNG na resposta; pedindo um novo")
            else:
                try:
                    solution = self._solve_captcha(image, game_account_id)
                except Exception as exc:
                    logger.warning("Piracy: captcha challenge falhou: %s", exc)
                if solution:
                    logger.info("Piracy: captcha solution = %s", solution)
                else:
                    logger.warning("Piracy: empty captcha solution, asking for a fresh captcha")

            if not solution:
                time.sleep(self._CAPTCHA_RETRY_WAIT)
                # A new capture brings a brand new captcha instead of the stale one.
                resp_text, fresh = self._post_capture(base_params)
                if fresh is None:
                    break
                data = fresh
                continue

            # Re-POST with solution
            resp_text, answered = self._post_capture({**base_params, "captchaNeeded": "1", "captcha": solution})
            if answered is None:
                break
            data = answered

        # Parse final result
        js_final = _extract_js_params(data)
        time_remaining = int(js_final.get("ongoingMissionTimeRemaining") or 0)
        ship_flag = js_final.get("showPirateFortressShip")
        has_ship_flag = ship_flag is not None
        ship_in_port_final = str(ship_flag) == "1"

        success = time_remaining > 0 or (has_ship_flag and not ship_in_port_final)
        message = ""
        for entry in data:
            if isinstance(entry, list) and entry[0] == "provideFeedback":
                for fb in (entry[1] or []):
                    if isinstance(fb, dict) and fb.get("text"):
                        message = fb["text"]

        if not success and _response_has_captcha_signal(resp_text, data):
            raise CaptchaRequiredError(
                "Piracy captcha was requested but not solved",
                captcha_data={"type": "pirate"},
            )

        if success and time_remaining <= 0:
            try:
                state = PiracyStateAction(self.client).execute(city_id)
                time_remaining = int(state.get("time_remaining") or 0)
                success = time_remaining > 0
            except Exception as exc:
                logger.warning("Piracy: failed to confirm started mission: %s", exc)

        return {
            "success": success,
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
