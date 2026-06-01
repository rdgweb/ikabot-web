"""Military actions — train troops, send troops, attack."""

from __future__ import annotations

import logging
import re
from typing import Any

from ..constants import ActionID
from ..exceptions import ActionError
from ..unit_stats import UNIT_STATS
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


def _parse_duration_seconds(text: str) -> int:
    raw = str(text or "").strip()
    if not raw:
        return 0
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([dhms])", raw.lower()):
        ivalue = int(value)
        if unit == "d":
            total += ivalue * 86400
        elif unit == "h":
            total += ivalue * 3600
        elif unit == "m":
            total += ivalue * 60
        elif unit == "s":
            total += ivalue
    return total


def _parse_training_queue(html: str, building_type: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    class_prefix = "fleet" if building_type == "fleet" else "army"

    active_match = re.search(
        rf'id="unitBuildCountDown">([^<]+)</div>.*?class="{class_prefix} [^"]* s(\d+)".*?'
        r'class="unitcounttextlabel">(\d+)</div>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if active_match:
        entries.append({
            "status": "active",
            "remaining_text": active_match.group(1).strip(),
            "remaining_seconds": _parse_duration_seconds(active_match.group(1)),
            "unit_id": int(active_match.group(2)),
            "quantity": int(active_match.group(3)),
        })

    waiting_pattern = re.compile(
        rf'Em espera\s*-\s*<span[^>]*>([^<]+)</span>.*?class="{class_prefix} [^"]* s(\d+)".*?'
        r'class="unitcounttextlabel">(\d+)</div>',
        re.DOTALL | re.IGNORECASE,
    )
    for remaining_text, unit_id, quantity in waiting_pattern.findall(html):
        entries.append({
            "status": "waiting",
            "remaining_text": str(remaining_text or "").strip(),
            "remaining_seconds": _parse_duration_seconds(remaining_text),
            "unit_id": int(unit_id),
            "quantity": int(quantity),
        })
    return entries


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
        training_queue = _parse_training_queue(html, building_type)

        return {
            "units": units,
            "garrison_land": garrison_land,
            "garrison_land_max": garrison_land_max,
            "garrison_sea": garrison_sea,
            "garrison_sea_max": garrison_sea_max,
            "occupied": occupied,
            "training_queue": training_queue,
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


class FetchBlockadeViewAction(BaseAction):
    """Fetch blockade form to get fleet travel time BEFORE sending.

    Same pattern as fetch_plunder_view — reads transportJourneyTime from missionController JS.

    URL: GET view=blockade&isMission=1&destinationCityId=X&currentIslandId=Y
    """

    def execute(self, from_city_id: int, to_city_id: int, island_id: int) -> dict[str, Any]:
        # Navigate to source city first so the game knows which fleet to use
        self.client._request("GET", self.client._server_url, params={
            "view": "city",
            "cityId": int(from_city_id),
            "backgroundView": "city",
            "actionRequest": self.client._action_request,
            "ajax": "0",
        }, timeout=15)

        resp = self.client._request(
            "GET",
            self.client._server_url,
            params={
                "view": "blockade",
                "isMission": "1",
                "destinationCityId": int(to_city_id),
                "backgroundView": "island",
                "currentIslandId": int(island_id),
                "templateView": "cityDetails",
                "actionRequest": self.client._action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        try:
            data = resp.json()
        except Exception:
            return {"travel_seconds": 0}

        if data and isinstance(data[0], list) and len(data[0]) > 1:
            gd = data[0][1]
            if isinstance(gd, dict) and "actionRequest" in gd:
                self.client._action_request = gd["actionRequest"]

        html = ""
        for entry in data:
            if isinstance(entry, list) and len(entry) > 1 and entry[0] in ("changeHTML", "changeView"):
                items = entry[1] if isinstance(entry[1], list) else [entry[1]]
                for item in items:
                    if isinstance(item, str) and len(item) > 100:
                        html = item
                        break

        # Parse travel time from missionController(freeTrans, capacity, transportJourneyTime, ...)
        travel_seconds = 0
        mc_m = re.search(
            r"new missionController\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)",
            html
        )
        if mc_m:
            try:
                travel_seconds = int(float(mc_m.group(3)))
            except (ValueError, TypeError):
                pass

        return {"travel_seconds": travel_seconds, "html": html}


class PlunderLandAction(BaseAction):
    """Send an army to plunder a player city (land raid).

    API confirmed from live traffic:
      GET  view=plunder&isMission=1&destinationCityId=X&currentIslandId=Y  → form with travel time
      POST action=transportOperations&function=sendArmyPlunderLand          → dispatches army
    """

    def fetch_plunder_view(
        self,
        from_city_id: int,
        to_city_id: int,
        island_id: int,
    ) -> dict[str, Any]:
        """Phase 1: fetch plunder view to get travel time and available transport capacity."""
        resp = self.client._request(
            "GET",
            self.client._server_url,
            params={
                "view": "plunder",
                "isMission": "1",
                "destinationCityId": int(to_city_id),
                "backgroundView": "island",
                "currentIslandId": int(island_id),
                "templateView": "cityDetails",
                "actionRequest": self.client._action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        try:
            data = resp.json()
        except Exception:
            return {"travel_seconds": 0, "html": ""}

        if data and isinstance(data[0], list) and len(data[0]) > 1:
            gd = data[0][1]
            if isinstance(gd, dict) and "actionRequest" in gd:
                self.client._action_request = gd["actionRequest"]

        html = ""
        for entry in data:
            if isinstance(entry, list) and len(entry) > 1 and entry[0] in ("changeHTML", "changeView"):
                for item in (entry[1] if isinstance(entry[1], list) else [entry[1]]):
                    if isinstance(item, str) and len(item) > 50:
                        html = item
                        break

        # Parse travel time from missionController instantiation:
        # new missionController(freeTrans, transporterCapacity, transportJourneyTime, ...)
        # transportJourneyTime is the 3rd argument (index 2), in seconds.
        travel_seconds = 0
        transporter_capacity = 500  # default
        mc_m = re.search(
            r"new missionController\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)",
            html
        )
        if mc_m:
            try:
                transporter_capacity = int(float(mc_m.group(2)))
                travel_seconds = int(float(mc_m.group(3)))
            except (ValueError, TypeError):
                pass

        # Fallback: look for explicit duration strings
        if not travel_seconds:
            travel_seconds = _parse_duration_seconds(html)

        return {
            "travel_seconds":       travel_seconds,
            "transporter_capacity": transporter_capacity,
            "html":                 html,
        }

    def execute(
        self,
        from_city_id: int,
        to_city_id: int,
        island_id: int,
        units: dict[int, int],
        transporters: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send army to plunder. Returns eta_seconds (one-way travel time).

        Args:
            from_city_id: Source city (where troops are stationed).
            to_city_id: Target city to plunder.
            island_id: Island ID of the target city.
            units: {unit_id: qty} — troops to send.
            transporters: Number of merchant ships (201) to carry loot back.
        """
        if not units or not any(int(q) > 0 for q in units.values()):
            raise ActionError("No units specified for plunder", action="plunder")

        # Phase 0a: SWITCH active city to the source city via header changeCurrentCity.
        # Sem isso, o jogo manda tropas da cidade da sessão (que pode ser outra)
        # e ignora from_city_id do payload.
        try:
            from services.resource_transport import change_current_city
            change_current_city(self.client, int(from_city_id))
        except Exception:
            pass

        # Phase 0b: navigate to target island to set form context.
        try:
            self.client._request(
                "GET",
                self.client._server_url,
                params={"view": "island", "islandId": int(island_id), "ajax": "1"},
                timeout=20,
            )
        except Exception:
            pass

        # Phase 1: get travel time
        view_data = self.fetch_plunder_view(from_city_id, to_city_id, island_id)
        travel_seconds = view_data.get("travel_seconds", 0)

        # Phase 2: dispatch army
        payload: dict[str, Any] = {
            "action": "transportOperations",
            "function": "sendArmyPlunderLand",
            "islandId": int(island_id),
            "destinationCityId": int(to_city_id),
            "transporter": int(transporters),
            "barbarianVillage": "0",
            "backgroundView": "island",
            "currentIslandId": int(island_id),
            "templateView": "plunder",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        for uid, qty in units.items():
            if int(qty) > 0:
                u = UNIT_STATS.get(int(uid), {})
                upkeep = u.get("upkeep", 0) if u else 0
                payload[f"cargo_army_{uid}_upkeep"] = upkeep
                payload[f"cargo_army_{uid}"] = int(qty)

        logger.info(
            "PlunderLand from=%s to=%s island=%s units=%s transporters=%s",
            from_city_id, to_city_id, island_id, units, transporters,
        )
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
                            raise ActionError(f"Game error: {fb.get('text', '')}", action="plunder")
        except ActionError:
            raise
        except Exception:
            pass

        return {"ok": True, "travel_seconds": travel_seconds}


class BlockadeFleetAction(BaseAction):
    """Send a fleet to blockade a player's port.

    API confirmed from live traffic:
      POST action=transportOperations&function=sendFleetOnBlockade

    FUTURE HOOK: after land raid completes, call fleet back via revolt/recall.
    """

    def execute(
        self,
        from_city_id: int,
        to_city_id: int,
        island_id: int,
        fleet_units: dict[int, int],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send fleet to blockade target city's port.

        Args:
            fleet_units: {ship_unit_id: qty} — naval units to send.
        """
        if not fleet_units or not any(int(q) > 0 for q in fleet_units.values()):
            raise ActionError("No fleet units specified for blockade", action="blockade")

        payload: dict[str, Any] = {
            "action": "transportOperations",
            "function": "sendFleetOnBlockade",
            "islandId": int(island_id),
            "destinationCityId": int(to_city_id),
            "backgroundView": "island",
            "currentIslandId": int(island_id),
            "templateView": "blockade",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        for uid, qty in fleet_units.items():
            if int(qty) > 0:
                u = UNIT_STATS.get(int(uid), {})
                upkeep = u.get("upkeep", 0) if u else 0
                payload[f"cargo_fleet_{uid}_upkeep"] = upkeep
                payload[f"cargo_fleet_{uid}"] = int(qty)

        logger.info(
            "BlockadeFleet from=%s to=%s island=%s fleet=%s",
            from_city_id, to_city_id, island_id, fleet_units,
        )
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
                            raise ActionError(f"Game error: {fb.get('text', '')}", action="blockade")
        except ActionError:
            raise
        except Exception:
            pass

        # HOOK: future — track blockade job_id so raid runner can recall fleet after plunder
        return {"ok": True, "blockade_active": True}


class RecallBlockadeFleetAction(BaseAction):
    """Recall fleet from an occupied port — two-step process.

    Confirmed API from live traffic:

    Step 1 — Abort port occupation:
      POST action=transportOperations&function=abortPortOccupation
           &targetCityId={enemy_city_id}&eventId=0

    Step 2 — Get eventId of stranded fleet:
      GET view=relatedCities&cityId={enemy_city_id}
      Parse: eventId from abortFleetOperation links

    Step 3 — Recall fleet:
      POST action=transportOperations&function=abortFleetOperation
           &eventId={event_id}&cityId={enemy_city_id}
    """

    def execute(
        self,
        source_city_id: int,
        enemy_city_id: int,
        **kwargs,
    ) -> dict:
        """Abort blockade and recall fleet from enemy city.

        Returns: {"ok": bool, "steps": list[str]}
        """
        steps = []

        # Step 1: abort port occupation
        payload1 = {
            "action": "transportOperations",
            "function": "abortPortOccupation",
            "targetCityId": int(enemy_city_id),
            "eventId": 0,
            "oldView": "militaryAdvisor",
            "activeTab": "militaryMovements",
            "backgroundView": "city",
            "currentCityId": int(source_city_id),
            "templateView": "militaryAdvisor",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp1 = self.client._request("POST", self.client._server_url, data=payload1, timeout=30)
        try:
            d1 = resp1.json()
            if d1 and isinstance(d1[0], list) and len(d1[0]) > 1:
                ar = d1[0][1].get("actionRequest")
                if ar:
                    self.client._action_request = ar
            steps.append("abort_occupation")
        except Exception as e:
            return {"ok": False, "steps": steps, "error": str(e)}

        import time as _time
        _time.sleep(2)

        # Step 2: get eventId of fleet at enemy city
        resp2 = self.client._request(
            "GET", self.client._server_url,
            params={
                "view": "relatedCities",
                "cityId": int(enemy_city_id),
                "backgroundView": "city",
                "currentCityId": int(enemy_city_id),
                "actionRequest": self.client._action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        event_id = 0
        try:
            d2 = resp2.json()
            if d2 and isinstance(d2[0], list) and len(d2[0]) > 1:
                ar = d2[0][1].get("actionRequest")
                if ar:
                    self.client._action_request = ar
            html = ""
            for entry in d2:
                if isinstance(entry, list) and entry[0] in ("changeHTML", "changeView"):
                    items = entry[1] if isinstance(entry[1], list) else [entry[1]]
                    for item in items:
                        if isinstance(item, str) and len(item) > 50:
                            html = item
                            break
            # Parse eventId from abortFleetOperation link
            m = re.search(r"abortFleetOperation[^&]*&?eventId=(\d+)", html)
            if m:
                event_id = int(m.group(1))
                steps.append(f"found_event_id={event_id}")
        except Exception as e:
            return {"ok": False, "steps": steps, "error": f"get_event_id: {e}"}

        if not event_id:
            return {"ok": False, "steps": steps, "error": "eventId not found — fleet may already be returning"}

        # Step 3: recall fleet
        payload3 = {
            "action": "transportOperations",
            "function": "abortFleetOperation",
            "eventId": event_id,
            "cityId": int(enemy_city_id),
            "backgroundView": "city",
            "currentCityId": int(enemy_city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp3 = self.client._request("POST", self.client._server_url, data=payload3, timeout=30)
        try:
            d3 = resp3.json()
            if d3 and isinstance(d3[0], list) and len(d3[0]) > 1:
                ar = d3[0][1].get("actionRequest")
                if ar:
                    self.client._action_request = ar
            for entry in d3:
                if isinstance(entry, list) and entry[0] == "provideFeedback":
                    for fb in (entry[1] or []):
                        if isinstance(fb, dict) and fb.get("type") == 11:
                            return {"ok": False, "steps": steps, "error": fb.get("text", "")}
            steps.append("fleet_recalled")
        except Exception as e:
            return {"ok": False, "steps": steps, "error": f"abort_fleet: {e}"}

        return {"ok": True, "steps": steps}


class FetchMilitaryAdvisorAction(BaseAction):
    """Fetch military advisor state — movements, battles, occupied ports.

    Confirmed API from live traffic:
      GET view=militaryAdvisor → returns HTML + updateTemplateData with:
        js_MilitaryMovementsCombatsInProgress  — {addClass:"invisible"} or HTML when battle active
        js_MilitaryMovementsFleetMovementsTable — active movements HTML table
        js_MilitaryMovementsOccupiedPortsTable  — occupied ports (blockades)

    scatteredUnitsSidebar contains:
        <tr><td>Tempo de chegada: DD.MM.YYYY H:MM:SS</td></tr><tr><td>Barcos de guerra: N</td></tr>
    """

    def execute(self, city_id: int, **kwargs) -> dict:
        # Movimentos ativos vêm em viewScriptParams.militaryAndFleetMovements
        # (JSON limpo dentro do changeView), não no updateTemplateData.
        resp = self.client._request(
            "GET",
            self.client._server_url,
            params={
                "view": "militaryAdvisor",
                "oldView": "militaryAdvisor",
                "cityId": int(city_id),
                "actionRequest": self.client._action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        try:
            data = resp.json()
        except Exception:
            return {}

        if data and isinstance(data[0], list) and len(data[0]) > 1:
            gd = data[0][1]
            if isinstance(gd, dict) and "actionRequest" in gd:
                self.client._action_request = gd["actionRequest"]

        template = {}
        sidebar_html = ""
        movements_json: list[dict] = []
        for entry in data:
            if not isinstance(entry, list) or len(entry) < 2:
                continue
            if entry[0] == "updateTemplateData" and isinstance(entry[1], dict):
                template = entry[1]
            elif entry[0] == "changeView":
                # entry[1] = [view_name, html_string, view_script_params]
                if isinstance(entry[1], list) and len(entry[1]) >= 3:
                    vsp = entry[1][2]
                    if isinstance(vsp, dict):
                        # viewScriptParams pode estar aninhado
                        ms = vsp.get("viewScriptParams") or vsp
                        if isinstance(ms, dict):
                            mfm = ms.get("militaryAndFleetMovements") or []
                            if isinstance(mfm, list):
                                movements_json = mfm
                    # sidebar HTML
                    if isinstance(entry[1][1], str) and "scatteredUnitsSidebar" in entry[1][1]:
                        sidebar_html = entry[1][1]
            elif entry[0] == "changeHTML":
                items = entry[1] if isinstance(entry[1], list) else [entry[1]]
                for item in items:
                    if isinstance(item, str) and "scatteredUnitsSidebar" in item:
                        sidebar_html = item
                        break

        # Parse battle state from template
        combat_raw = template.get("js_MilitaryMovementsCombatsInProgress") or {}
        has_active_battle = not (isinstance(combat_raw, dict) and combat_raw.get("addClass") == "invisible")

        # Parse movement table — pode vir como string, lista, ou dict {html:..., addClass:...}
        def _flatten(v) -> str:
            if isinstance(v, str): return v
            if isinstance(v, list):
                return "\n".join(_flatten(it) for it in v)
            if isinstance(v, dict):
                return "\n".join(_flatten(it) for it in v.values())
            return ""
        movements_html = _flatten(template.get("js_MilitaryMovementsFleetMovementsTable"))
        occupied_ports_html = _flatten(template.get("js_MilitaryMovementsOccupiedPortsTable"))
        occupied_cities_html = _flatten(template.get("js_MilitaryMovementsOccupiedCitiesTable"))

        # Debug: log template keys disponíveis para diagnose
        logger.info(
            "MilitaryAdvisor keys: %s | movements_html=%d bytes",
            list(template.keys())[:20], len(movements_html),
        )

        # Check if port is actually occupied (not just empty table)
        port_occupied = bool(
            occupied_ports_html
            and "Você não tem portos ocupados" not in occupied_ports_html
            and "<tr>" in occupied_ports_html
            and '<td colspan="5">' not in occupied_ports_html
        )

        # Parse ETA from scatteredUnitsSidebar
        eta_timestamp = None
        ships_moving = 0
        if sidebar_html:
            for m in re.finditer(
                r"Tempo de chegada:\s*(\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2}:\d{2})",
                sidebar_html,
            ):
                eta_timestamp = m.group(1).strip()
                break
            m_ships = re.search(r"Barcos de guerra:\s*(\d+)", sidebar_html)
            if m_ships:
                ships_moving = int(m_ships.group(1))

        # Parse active movement missions from fleet movements table
        movements = []
        if movements_html:
            for mission_m in re.finditer(r'data-mission["\s]*=["\s]*([a-z_]+)', movements_html, re.IGNORECASE):
                movements.append(mission_m.group(1))

        # Parse movements from JSON (militaryAndFleetMovements in viewScriptParams).
        # Estrutura: {event:{id,mission,missionText,...}, eventTime, fleet:{ships:[]}, army:{units:[]}, resources:[], origin:{}, target:{}}
        def _qty(s):
            try:
                return int(str(s).replace(".","").replace(",",""))
            except Exception:
                return 0
        movement_details: list[dict] = []
        for ev in movements_json:
            if not isinstance(ev, dict):
                continue
            event = ev.get("event") or {}
            cargo: dict[str, int] = {}
            fleet: dict[str, int] = {}
            troops: dict[str, int] = {}
            for r in (ev.get("resources") or []):
                cls = (r.get("cssClass") or "").split()
                # cssClass = "resource_icon NAME"
                name = cls[-1] if cls else ""
                if name and name != "resource_icon":
                    cargo[name] = _qty(r.get("amount"))
            for s in ((ev.get("fleet") or {}).get("ships") or []):
                cls = s.get("cssClass") or ""
                if cls.startswith("ship_"):
                    fleet[cls[5:]] = _qty(s.get("amount"))
            for u in ((ev.get("army") or {}).get("units") or []):
                cls = u.get("cssClass") or ""
                if cls:
                    troops[cls] = _qty(u.get("amount"))
            movement_details.append({
                "event_id":      event.get("id"),
                "mission":       event.get("missionIconClass") or "",
                "mission_text":  event.get("missionText") or "",
                "is_own":        bool(ev.get("isOwnArmyOrFleet")),
                "is_returning":  bool(event.get("isReturning")) or bool(event.get("isFleetReturning")),
                "event_time":    ev.get("eventTime"),
                "event_date":    ev.get("eventDate"),
                "origin":        ev.get("origin") or {},
                "target":        ev.get("target") or {},
                "cargo":         cargo,
                "fleet":         fleet,
                "troops":        troops,
            })

        # Fallback: parse HTML if JSON empty (legacy code path)
        if not movement_details and movements_html:
            # Cada bloco fleetInfo<id> aparece em um <div> próprio; dividimos pelos delimitadores
            blocks = re.split(r"fleetInfo(\d+)", movements_html)
            # blocks = [head, id1, content1, id2, content2, ...]
            for i in range(1, len(blocks), 2):
                fleet_id = blocks[i]
                content = blocks[i+1] if i+1 < len(blocks) else ""
                cargo: dict[str, int] = {}
                fleet: dict[str, int] = {}
                troops: dict[str, int] = {}
                # Cada ícone é um <div class="... CLASSES ..." title="N">
                for icon_m in re.finditer(
                    r'class="unit_detail_icon[^"]*"\s+title="([\d.,]+)"',
                    content,
                ):
                    # Re-extrai a class para inspecionar tipo
                    cls_m = re.search(
                        r'class="(unit_detail_icon[^"]*)"\s+title="' + re.escape(icon_m.group(1)) + '"',
                        content[icon_m.start():icon_m.end()+50],
                    )
                    if not cls_m:
                        continue
                    classes = cls_m.group(1).split()
                    try:
                        qty = int(icon_m.group(1).replace(".", "").replace(",", ""))
                    except Exception:
                        continue
                    # resource_icon X
                    if "resource_icon" in classes:
                        # próxima class após resource_icon = nome do recurso
                        idx = classes.index("resource_icon")
                        if idx + 1 < len(classes):
                            cargo[classes[idx + 1]] = qty
                        continue
                    # ship_X
                    ship_cls = next((c for c in classes if c.startswith("ship_")), "")
                    if ship_cls:
                        fleet[ship_cls[len("ship_"):]] = qty
                        continue
                    # Tropa: última class que NÃO é layout (floatleft, icon40, bold, center)
                    layout = {"unit_detail_icon", "floatleft", "icon40", "bold", "center"}
                    troop_cls = next((c for c in reversed(classes) if c not in layout), "")
                    if troop_cls:
                        troops[troop_cls] = qty

                if cargo or fleet or troops:
                    movement_details.append({
                        "fleet_id": fleet_id,
                        "cargo":   cargo,
                        "fleet":   fleet,
                        "troops":  troops,
                    })

        return {
            "has_active_battle":   has_active_battle,
            "port_occupied":       port_occupied,
            "eta_timestamp":       eta_timestamp,   # "DD.MM.YYYY H:MM:SS" string
            "ships_moving":        ships_moving,
            "movements":           movements,        # list of mission types
            "movement_details":    movement_details, # [{fleet_id, cargo, fleet, troops}]
            "movements_html":      movements_html,
            "occupied_ports_html": occupied_ports_html,
        }


class FetchCombatReportsAction(BaseAction):
    """Fetch latest combat reports from militaryAdvisorCombatList.

    Returns list of recent combat report entries with:
      combat_id, date, rounds, city_name, owner_name, result (green=victory, red=defeat)
    """

    def execute(self, city_id: int, limit: int = 10, **kwargs) -> list[dict]:
        resp = self.client._request(
            "GET",
            self.client._server_url,
            params={
                "view": "militaryAdvisorCombatList",
                "activeTab": "tab_militaryAdvisorCombatList",
                "backgroundView": "city",
                "currentCityId": int(city_id),
                "actionRequest": self.client._action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        try:
            data = resp.json()
        except Exception:
            return []

        if data and isinstance(data[0], list) and len(data[0]) > 1:
            gd = data[0][1]
            if isinstance(gd, dict) and "actionRequest" in gd:
                self.client._action_request = gd["actionRequest"]

        html = ""
        for entry in data:
            if isinstance(entry, list) and entry[0] in ("changeHTML", "changeView"):
                items = entry[1] if isinstance(entry[1], list) else [entry[1]]
                for item in items:
                    if isinstance(item, str) and "combatList" in item:
                        html = item
                        break

        reports = []
        # Each <tr class="green"> or <tr class="red"> is a combat report
        for row_m in re.finditer(
            r'<tr class="(green|red)[^"]*"[^>]*>(.*?)</tr>',
            html, re.DOTALL
        ):
            result_class = row_m.group(1)
            row_html = row_m.group(0)

            # combatId
            cid_m = re.search(r"combatId=(\d+)", row_html)
            if not cid_m:
                continue
            combat_id = int(cid_m.group(1))

            # date
            date_m = re.search(r'class="date"[^>]*>\s*([^<]+)<', row_html)
            date_str = date_m.group(1).strip() if date_m else ""

            # rounds
            rounds_m = re.search(r'class="right"[^>]*>\s*(\d+)<', row_html)
            rounds = int(rounds_m.group(1)) if rounds_m else 0

            # city_name
            city_m = re.search(r'href="\?view=island&cityId=(\d+)"[^>]*>([^<]+)<', row_html)
            city_name = city_m.group(2).strip() if city_m else ""
            city_id_target = int(city_m.group(1)) if city_m else 0

            # owner_name
            owner_m = re.search(r'href="\?view=avatarProfile&avatarId=\d+"[^>]*>([^<]+)<', row_html)
            owner_name = owner_m.group(1).strip() if owner_m else ""

            reports.append({
                "combat_id":      combat_id,
                "result":         "victory" if result_class == "green" else "defeat",
                "date":           date_str,
                "rounds":         rounds,
                "city_id_target": city_id_target,
                "city_name":      city_name,
                "owner_name":     owner_name,
            })

            if len(reports) >= limit:
                break

        return reports


class FetchCombatReportDetailAction(BaseAction):
    """Fetch full combat report for a specific combatId.

    Returns: attacker, defender, loot dict, winner, losers, units table HTML.
    """

    def execute(self, city_id: int, combat_id: int, **kwargs) -> dict:
        resp = self.client._request(
            "GET",
            self.client._server_url,
            params={
                "view": "militaryAdvisorReportView",
                "combatId": int(combat_id),
                "activeTab": "combatReports",
                "backgroundView": "city",
                "currentCityId": int(city_id),
                "templateView": "militaryAdvisorCombatList",
                "currentTab": "tab_militaryAdvisorCombatList",
                "actionRequest": self.client._action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        try:
            data = resp.json()
        except Exception:
            return {}

        if data and isinstance(data[0], list) and len(data[0]) > 1:
            gd = data[0][1]
            if isinstance(gd, dict) and "actionRequest" in gd:
                self.client._action_request = gd["actionRequest"]

        # Estrutura: changeView body é lista [view_name, html, ...]. O 1º item é
        # o nome da view (curto); o 2º+ é o HTML real. Pegamos o maior item que
        # contém marcadores HTML — evita o bug de salvar só "militaryAdvisorReportView".
        html = ""
        for entry in data:
            if not (isinstance(entry, list) and entry[0] in ("changeHTML", "changeView")):
                continue
            items = entry[1] if isinstance(entry[1], list) else [entry[1]]
            for item in items:
                if isinstance(item, str) and len(item) > 200 and ("<div" in item or "<table" in item):
                    if "troopsReport" in item or "militaryAdvisorReportView" in item or 'class="winners' in item:
                        if len(item) > len(html):
                            html = item

        # Parse attacker / defender — <span> contém <b><a> etc, então capturamos
        # tudo até </span> e depois extraímos só o nome principal.
        def _name_from_span(span_inner: str) -> str:
            # "BlackShadow701 de <b><a>...</a></b>" → "BlackShadow701"
            # ou "<a>Melkor545[PLC]</a> de <b><a>...</a></b>" → "Melkor545[PLC]"
            first_a = re.search(r'<a[^>]*>([^<]+)</a>', span_inner)
            if first_a:
                return first_a.group(1).strip()
            return re.sub(r'<[^>]+>', '', span_inner).split(" de ")[0].strip()

        att_m = re.search(r'class="attacker[^"]*"[^>]*>.*?<span>([\s\S]*?)</span>', html, re.DOTALL)
        def_m = re.search(r'class="defender[^"]*"[^>]*>.*?<span>([\s\S]*?)</span>', html, re.DOTALL)
        attacker_name = _name_from_span(att_m.group(1)) if att_m else ""
        defender_name = _name_from_span(def_m.group(1)) if def_m else ""

        # Parse winner / loser
        winner_m = re.search(r'class="winners headline"[^>]*>.*?Vencedores:\s*<br\s*/>([^<]+)<', html, re.DOTALL)
        loser_m  = re.search(r'class="losers headline"[^>]*>.*?Perdedores:\s*<br\s*/>([^<]+)<', html, re.DOTALL)

        # Parse loot
        loot: dict[str, int] = {}
        loot_m = re.search(r'recursos foram roubados.*?<ul[^>]*>(.*?)</ul>', html, re.DOTALL)
        if loot_m:
            loot_html = loot_m.group(1)
            res_map = {
                "Materiais de construção": "wood",
                "Vinho": "wine",
                "Mármore": "marble",
                "Cristal": "glass",
                "Enxofre": "sulfur",
            }
            for li_m in re.finditer(r'<li[^>]*>\s*([\d.]+)\s*<img[^>]+title="([^"]+)"', loot_html):
                qty_raw = li_m.group(1).replace(".", "").replace(",", "")
                title   = li_m.group(2).strip()
                key     = res_map.get(title, title)
                try:
                    loot[key] = int(qty_raw)
                except ValueError:
                    pass

        # Parse date from header
        date_m = re.search(r'<span class="date">\(([^)]+)\)</span>', html)
        date_str = date_m.group(1) if date_m else ""

        # Defeat message
        lost_m = re.search(r'dado como perdido', html, re.IGNORECASE)

        return {
            "combat_id":  combat_id,
            "date":       date_str,
            "attacker":   attacker_name,
            "defender":   defender_name,
            "winner":     winner_m.group(1).strip() if winner_m else "",
            "loser":      loser_m.group(1).strip() if loser_m else "",
            "loot":       loot,
            "total_loot": sum(loot.values()),
            "army_lost":  bool(lost_m),
            "html":       html,
        }


class FetchCombatDetailedReportAction(BaseAction):
    """Fetch detailed per-round combat report — iterates all rounds.

    URL: view=militaryAdvisorDetailedReportView&combatRound=N&detailedCombatId=X

    Returns: {
        "total_rounds": N,
        "rounds": [{"round": N, "html": "..."}],
        "combined_html": "<all rounds concatenated>",
        "attacker_losses": {unit_id: count},
        "defender_losses": {unit_id: count},
    }
    Slot pattern: id="slot{N}_{field}_{slot}" class="slot army_small normal s{unit_id}"
                  <div class="number center"> {qty} (-{lost})
    """

    def execute(self, city_id: int, combat_id: int, max_rounds: int = 100, **kwargs) -> dict:
        """Itera todos os rounds reais. max_rounds=100 é só guarda de loop infinito —
        para quando todos os N rounds (descobertos do HTML "Round X / N") foram vistos."""
        rounds_html: list[dict] = []
        attacker_losses: dict[str, int] = {}
        defender_losses: dict[str, int] = {}
        seen_rounds: set[int] = set()
        total_rounds_known = 0  # descoberto no primeiro response

        for round_num in range(max_rounds):
            resp = self.client._request(
                "GET",
                self.client._server_url,
                params={
                    "view": "militaryAdvisorDetailedReportView",
                    "combatRound": round_num,
                    "detailedCombatId": int(combat_id),
                    "activeTab": "combatReports",
                    "backgroundView": "city",
                    "currentCityId": int(city_id),
                    "actionRequest": self.client._action_request,
                    "ajax": "1",
                },
                timeout=30,
            )
            try:
                data = resp.json()
            except Exception:
                break

            if data and isinstance(data[0], list) and len(data[0]) > 1:
                gd = data[0][1]
                if isinstance(gd, dict) and "actionRequest" in gd:
                    self.client._action_request = gd["actionRequest"]

            # Mesmo bug do summary: 1º item de changeView é só o nome da view.
            # HTML real está em items[1+]. Pegamos o maior item com tags HTML.
            html = ""
            for entry in data:
                if not (isinstance(entry, list) and entry[0] in ("changeHTML", "changeView")):
                    continue
                items = entry[1] if isinstance(entry[1], list) else [entry[1]]
                for item in items:
                    if isinstance(item, str) and len(item) > 200 and ("<div" in item or "<table" in item):
                        if "militaryAdvisorDetailedReportView" in item or "battlefield" in item or 'id="slot' in item:
                            if len(item) > len(html):
                                html = item

            if not html or "battlefield" not in html:
                break

            # Parse total rounds from "Round N / M"
            total_m = re.search(r"Round\s*<br\s*/>\s*(\d+)\s*/\s*(\d+)", html)
            if total_m:
                current_round = int(total_m.group(1))
                total_rounds_known = int(total_m.group(2))
            else:
                current_round = round_num + 1

            # Servidor pode retornar mesmo round se combatRound for inválido (fallback).
            # Pulamos duplicados pra não somar perdas duas vezes.
            if current_round in seen_rounds:
                # Já vimos todos os rounds reais → sair.
                if total_rounds_known > 0 and len(seen_rounds) >= total_rounds_known:
                    break
                continue
            seen_rounds.add(current_round)

            # Parse slot losses: class="slot army_small normal s{unit_id}"
            # followed by <div class="number center"> N (-lost)
            ATTACKER_FIELD = "11"
            DEFENDER_FIELD = "12"
            for slot_m in re.finditer(
                r'id="slot(\d+)_\d+_\d+"\s+class="slot[^"]*\bs(\d+)\b[^"]*"'
                r'.*?<div class="number center">\s*([\d,]+)\s*\(-([\d,]+)\)',
                html, re.DOTALL
            ):
                side   = slot_m.group(1)
                uid    = slot_m.group(2)
                lost   = int(slot_m.group(4).replace(",", ""))
                if lost > 0:
                    target = attacker_losses if side == ATTACKER_FIELD else defender_losses
                    target[uid] = target.get(uid, 0) + lost

            rounds_html.append({"round": current_round, "html": html})

            # Vimos todos os rounds reais? Encerra.
            if total_rounds_known > 0 and len(seen_rounds) >= total_rounds_known:
                break

        combined = "\n<!-- ROUND SEPARATOR -->\n".join(r["html"] for r in rounds_html)
        return {
            "total_rounds":     len(rounds_html),
            "rounds":           rounds_html,
            "combined_html":    combined,
            "attacker_losses":  attacker_losses,
            "defender_losses":  defender_losses,
        }


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
