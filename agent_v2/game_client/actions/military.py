"""Military actions — train troops, send troops, attack."""

from __future__ import annotations

import logging
import re
from typing import Any

from ..constants import ActionID
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)


def _parse_barracks_units(template_data: dict, building_type: str) -> list[dict[str, Any]]:
    """Parse unit list from updateTemplateData AJAX response.

    Args:
        template_data: The [2][1] entry of the AJAX response (updateTemplateData).
        building_type: "troops" (barracks) or "fleet" (shipyard).

    Returns:
        List of unit dicts with id, name, img, current_count, costs, train_time_seconds.
    """
    units = []
    slot = 1
    while True:
        name_key = f"js_barracksUnitName{slot}"
        if name_key not in template_data:
            break

        name = str(template_data.get(name_key, {}).get("text") or "").strip()
        if not name:
            slot += 1
            continue

        # Unit ID from help link href (?view=unitdescription&unitId=XXX)
        href = str(template_data.get(f"js_barracksUnitHelp{slot}", {}).get("href") or "")
        uid_m = re.search(r"unitId=(\d+)", href)
        unit_id = int(uid_m.group(1)) if uid_m else 0

        img = str(template_data.get(f"js_barracksUnitHelpPic{slot}", {}).get("src") or "")
        current = int(template_data.get(f"js_barracksUnitUnitsAvailable{slot}", {}).get("text") or 0)
        costs_html = str(template_data.get(f"js_barracksCosts{slot}", {}).get("text") or "")
        slider = template_data.get(f"js_barracksSlider{slot}", {}).get("slider") or {}
        try:
            max_build = int(slider.get("max_value") or 0)
        except Exception:
            max_build = 0

        # Parse resource costs from HTML
        costs: dict[str, int] = {}
        for rm in re.finditer(r'class="(\w+)"[^>]*>(?:[^<]*<[^>]+>)*([^<]*)', costs_html):
            key, val_raw = rm.group(1), rm.group(2).strip().replace(".", "").replace(",", "")
            if key in ("citizens", "wood", "wine", "marble", "glass", "sulfur", "upkeep"):
                try:
                    costs[key] = int(val_raw)
                except ValueError:
                    pass

        # Training time per unit — format "Xm Ys" or "Xh Ym Zs"
        time_m = re.search(r'class="time"[^>]*>.*?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?', costs_html, re.DOTALL)
        if time_m:
            h = int(time_m.group(1) or 0)
            m = int(time_m.group(2) or 0)
            s = int(time_m.group(3) or 0)
            train_time = h * 3600 + m * 60 + s
        else:
            train_time = 0

        units.append({
            "slot": slot,
            "unit_id": unit_id,
            "name": name,
            "img_url": img,
            "type": building_type,
            "current_count": current,
            "max_build": max_build,
            "citizens": costs.get("citizens", 0),
            "wood": costs.get("wood", 0),
            "wine": costs.get("wine", 0),
            "marble": costs.get("marble", 0),
            "crystal": costs.get("glass", 0),
            "sulfur": costs.get("sulfur", 0),
            "upkeep": costs.get("upkeep", 0),
            "train_time_seconds": train_time,
        })
        slot += 1

    return units


def _parse_city_military_counts(html: str, building_type: str) -> dict[int, int]:
    """Parse stationed troop/fleet counts from cityMilitary HTML."""
    counts: dict[int, int] = {}
    table_matches = re.findall(
        r'<table[^>]+class="[^"]*militaryList[^"]*"[^>]*>(.*?)</table>',
        html,
        re.DOTALL,
    )
    for table_html in table_matches:
        headers = re.findall(
            r'<th[^>]*>\s*<div[^>]+class="(?:army|fleet)\s+(s\d+)"[^>]*>.*?'
            r'<div[^>]+class="tooltip"[^>]*>(.*?)</div>',
            table_html,
            re.DOTALL,
        )
        count_row_match = re.search(
            r'<tr[^>]+class="count"[^>]*>(.*?)</tr>',
            table_html,
            re.DOTALL,
        )
        if not headers or not count_row_match:
            continue
        count_cells = re.findall(
            r"<td[^>]*>(.*?)</td>",
            count_row_match.group(1),
            re.DOTALL,
        )
        if len(count_cells) < len(headers) + 1:
            continue
        for (css_class, _unit_name), td_content in zip(headers, count_cells[1:]):
            raw = re.sub(r"<[^>]+>", "", td_content).strip().replace("\xa0", "")
            if not raw or raw == "-":
                continue
            try:
                amount = int(raw.replace(",", "").replace(".", ""))
                unit_id = int(css_class[1:])
            except Exception:
                continue
            if amount <= 0:
                continue
            if building_type == "fleet" and not (200 <= unit_id < 300):
                continue
            if building_type == "troops" and not (300 <= unit_id < 400):
                continue
            counts[unit_id] = counts.get(unit_id, 0) + amount
        if counts:
            break
    return counts


class FetchBarracksStateAction(BaseAction):
    """Fetch unit list and garrison state from barracks or shipyard."""

    def execute(
        self,
        city_id: int,
        position: int,
        building_type: str = "troops",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Fetch barracks/shipyard state including unit costs and current counts.

        Args:
            city_id: City ID.
            position: Building position (slot index).
            building_type: "troops" for barracks, "fleet" for shipyard.

        Returns:
            Dict with units list, garrison_land, garrison_sea, garrison_land_max,
            garrison_sea_max, occupied (bool).
        """
        view = "barracks" if building_type == "troops" else "shipyard"
        resp = self.client._request(
            "GET",
            self.client._server_url,
            params={
                "view": view,
                "cityId": city_id,
                "position": position,
                "currentCityId": city_id,
                "backgroundView": "city",
                "actionRequest": self.client._action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        data = resp.json()

        # Update actionRequest from response
        if data and isinstance(data[0], list) and len(data[0]) > 1:
            global_data = data[0][1]
            if isinstance(global_data, dict) and "actionRequest" in global_data:
                self.client._action_request = global_data["actionRequest"]

        template_data = data[2][1] if len(data) > 2 and len(data[2]) > 1 else {}
        html = data[1][1][1] if len(data) > 1 and len(data[1]) > 1 and len(data[1][1]) > 1 else ""

        # Parse garrison limits from city military HTML
        garrison_land = 0
        garrison_land_max = 0
        garrison_sea = 0
        garrison_sea_max = 0
        occupied = False

        gl_m = re.search(r'js_GarrisonLand">(\d+)<', html)
        glm_m = re.search(r'js_TownHallGarrisonLimitLand">(\d+)<', html)
        gs_m = re.search(r'js_GarrisonSea">(\d+)<', html)
        gsm_m = re.search(r'js_TownHallGarrisonLimitSea">(\d+)<', html)
        if gl_m:
            garrison_land = int(gl_m.group(1))
        if glm_m:
            garrison_land_max = int(glm_m.group(1))
        if gs_m:
            garrison_sea = int(gs_m.group(1))
        if gsm_m:
            garrison_sea_max = int(gsm_m.group(1))
        # Check occupation: the notice div must itself contain red_box, not just any red_box on page
        occ_m = re.search(r'id="js_barracksOccupyNotice"[^>]*>([\s\S]{0,500}?)</div>', html)
        if occ_m and "red_box" in occ_m.group(0):
            occupied = True

        units = _parse_barracks_units(template_data, building_type)

        return {
            "units": units,
            "garrison_land": garrison_land,
            "garrison_land_max": garrison_land_max,
            "garrison_sea": garrison_sea,
            "garrison_sea_max": garrison_sea_max,
            "occupied": occupied,
        }


class FetchStationedUnitsAction(BaseAction):
    """Fetch stationed troop or fleet counts from cityMilitary."""

    def execute(
        self,
        city_id: int,
        building_type: str = "troops",
        **kwargs: Any,
    ) -> dict[str, Any]:
        active_tab = "tabShips" if building_type == "fleet" else "tabUnits"
        current_tab = "multiTab2" if building_type == "fleet" else "multiTab1"
        resp = self.client._request(
            "GET",
            self.client._server_url,
            params={
                "view": "cityMilitary",
                "activeTab": active_tab,
                "cityId": city_id,
                "backgroundView": "city",
                "currentCityId": city_id,
                "currentTab": current_tab,
                "actionRequest": self.client._action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        data = resp.json()
        html = data[1][1][1] if len(data) > 1 and len(data[1]) > 1 and len(data[1][1]) > 1 else ""
        return {
            "counts": _parse_city_military_counts(html, building_type),
            "building_type": building_type,
        }


class TrainAction(BaseAction):
    """Train military units at barracks or shipyard.

    Confirmed API (from live traffic):
      Barracks:  POST action=BuildUnits, cityId=X, position=Y, <unit_id>=<qty>
      Shipyard:  POST action=BuildShips, cityId=X, position=Y, <unit_id>=<qty>
    """

    def execute(
        self,
        city_id: int,
        position: int,
        units: dict[int, int],
        building_type: str = "troops",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Train units.

        Args:
            city_id: City with the barracks/shipyard.
            position: Building slot position.
            units: {unit_id: quantity} — numeric unit IDs confirmed from game HTML.
            building_type: "troops" (barracks) or "fleet" (shipyard).
        """
        if not units or not any(int(q) > 0 for q in units.values()):
            raise ActionError("No units to train", action="train")

        action = "BuildUnits" if building_type == "troops" else "BuildShips"
        payload: dict[str, Any] = {
            "action": action,
            "cityId": city_id,
            "position": position,
            "actionRequest": self.client._action_request,
        }
        for unit_id, qty in units.items():
            if int(qty) > 0:
                payload[str(unit_id)] = int(qty)

        logger.info("Train %s city=%s pos=%s units=%s", building_type, city_id, position, units)
        resp = self.client._request("POST", self.client._server_url, data=payload, timeout=30)

        # Update actionRequest
        try:
            resp_data = resp.json()
            if resp_data and isinstance(resp_data[0], list) and len(resp_data[0]) > 1:
                ar = resp_data[0][1].get("actionRequest")
                if ar:
                    self.client._action_request = ar
            # Check for error feedback
            for entry in resp_data:
                if isinstance(entry, list) and entry[0] == "provideFeedback":
                    for fb in (entry[1] or []):
                        if isinstance(fb, dict):
                            if int(fb.get("type", 0)) == 11:
                                raise ActionError(f"Game error: {fb.get('text', 'unknown')}", action="train")
        except ActionError:
            raise
        except Exception:
            pass

        return {"ok": True, "units_trained": units}


class StationAction(BaseAction):
    """Send troops/fleet to garrison another city.

    Two-step flow (confirmed from live game HTML):
    1. GET view=deployment to validate ship capacity and get unit weights/journey times
    2. POST transportOperations&function=deployArmy with cargo_army_{id}=qty format
    """

    def fetch_deployment_state(
        self,
        from_city_id: int,
        to_city_id: int,
        deployment_type: str = "army",
    ) -> dict[str, Any]:
        """Phase 1: fetch available units, ship capacity and journey time."""
        resp = self.client._request(
            "POST",
            self.client._server_url,
            data={
                "view": "deployment",
                "deploymentType": deployment_type,
                "destinationCityId": to_city_id,
                "backgroundView": "city",
                "currentCityId": from_city_id,
                "actionRequest": self.client._action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        data = resp.json()
        # Update AR
        if data and isinstance(data[0], list) and len(data[0]) > 1:
            global_data = data[0][1]
            if isinstance(global_data, dict) and "actionRequest" in global_data:
                self.client._action_request = global_data["actionRequest"]

        # Extract unit journey times and weights from JS sliders in HTML
        html = ""
        for entry in data:
            if isinstance(entry, list) and entry[0] == "changeView":
                for item in (entry[1] or []):
                    if isinstance(item, str) and len(item) > 100:
                        html = item
                        break

        unit_info: dict[int, dict[str, Any]] = {}
        for m in re.finditer(
            r'slider_(\d+)"?\s*\);\s*(?:.*?\n)*?.*?weight\s*=\s*([\d.]+).*?'
            r'unitJourneyTime\s*=\s*([\d.]+)',
            html, re.DOTALL
        ):
            uid = int(m.group(1))
            unit_info[uid] = {
                "weight": float(m.group(2)),
                "journey_seconds": int(float(m.group(3))),
            }

        # Simpler fallback: iterate slider definitions
        if not unit_info:
            for m in re.finditer(r'cargo_army_(\d+)', html):
                uid = int(m.group(1))
                if uid not in unit_info:
                    unit_info[uid] = {"weight": 1.0, "journey_seconds": 0}
            for m in re.finditer(r's\.weight\s*=\s*([\d.]+)', html):
                pass  # weight per unit, order may not match

        return {"unit_info": unit_info, "html": html}

    def execute(
        self,
        from_city_id: int,
        to_city_id: int,
        units: dict[int, int],
        scope: str = "troops",
        to_island_id: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Station units. Returns eta_seconds for reschedule."""
        if not units or not any(int(q) > 0 for q in units.values()):
            raise ActionError("No units to station", action="station")

        scope = str(scope or "troops").strip().lower() or "troops"
        is_fleet = scope == "fleet"
        deployment_type = "fleet" if is_fleet else "army"

        # Phase 1: validate capacity and get journey time
        state = self.fetch_deployment_state(from_city_id, to_city_id, deployment_type)
        unit_info = state.get("unit_info", {})

        # Compute max journey time (slowest unit sets convoy speed)
        eta_seconds = 0
        for uid, qty in units.items():
            if int(qty) > 0 and uid in unit_info:
                eta_seconds = max(eta_seconds, unit_info[uid].get("journey_seconds", 0))

        # Phase 2: actual deployment
        prefix = "cargo_fleet_" if is_fleet else "cargo_army_"
        payload: dict[str, Any] = {
            "action": "transportOperations",
            "function": "deployFleet" if is_fleet else "deployArmy",
            "deploymentType": deployment_type,
            "backgroundView": "city",
            "currentCityId": from_city_id,
            "destinationCityId": to_city_id,
            "islandId": int(to_island_id or 0),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        for uid, qty in units.items():
            if int(qty) > 0:
                payload[f"{prefix}{uid}"] = int(qty)

        logger.info("Station %s from=%s to=%s units=%s eta=%ss", scope, from_city_id, to_city_id, units, eta_seconds)
        resp = self.client._request("POST", self.client._server_url, data=payload, timeout=30)

        try:
            resp_data = resp.json()
            if resp_data and isinstance(resp_data[0], list) and len(resp_data[0]) > 1:
                gd = resp_data[0][1]
                if isinstance(gd, dict) and "actionRequest" in gd:
                    self.client._action_request = gd["actionRequest"]
            for entry in resp_data:
                if isinstance(entry, list) and entry[0] == "provideFeedback":
                    for fb in (entry[1] or []):
                        if isinstance(fb, dict) and int(fb.get("type", 0)) == 11:
                            raise ActionError(f"Game error: {fb.get('text', '')}", action="station")
        except ActionError:
            raise
        except Exception:
            pass

        return {"ok": True, "from": from_city_id, "to": to_city_id, "scope": scope, "eta_seconds": eta_seconds}


class AttackAction(BaseAction):
    """Send an attack against another city."""

    def execute(
        self,
        from_city: int,
        target_city: int,
        units: dict[str, int],
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not units:
            raise ActionError("No units specified for attack", action="attack")
        params: dict[str, Any] = {
            "from_city": from_city,
            "target_city": target_city,
        }
        for unit_type, count in units.items():
            params[unit_type] = count
        return self._ajax_request(ActionID.ATTACK, params)


class SendTroopsAction(BaseAction):
    """Send troops to reinforce or garrison."""

    def execute(
        self,
        source_city: int,
        target_city: int,
        units: dict[str, int],
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not units:
            raise ActionError("No units to send", action="send_troops")
        params: dict[str, Any] = {
            "source_city": source_city,
            "target_city": target_city,
        }
        for unit_type, count in units.items():
            params[unit_type] = count
        return self._ajax_request(ActionID.SEND_TROOPS, params)
