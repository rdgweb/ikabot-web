"""Spy / Safehouse game actions.

API reference (captured 2026-05-07 from s78-br):

  Get safehouse state (GET):
    view=safehouse&cityId=<id>&position=<pos>
    &backgroundView=city&currentCityId=<id>&actionRequest=<token>&ajax=1

  Safehouse HTML contains:
    - spy count (available, defending, in use, training)
    - active missions per spy (spy ID, target, countdown)
    - training queue

  Send spy mission (POST):
    action=Espionage&function=sendSpy
    destinationCityId=<target_city_id>
    cityId=<island_id>          ← island id, NOT source city
    islandId=<island_id>
    spies[<source_city_id>][agents]=N
    spies[<source_city_id>][decoys]=M
    spies[<source_city_id>][missionId]=X
    backgroundView=island&actionRequest=<token>&ajax=1

  Mission IDs and risk/success data (from missionData in sendSpy form):
    1:  Enviar espião (basic scout)      riskBefore=30 riskAfter=5 riskPerSpy=3 success=60%
    3:  Nível de pesquisa                riskBefore=35 riskAfter=10 riskPerSpy=4 success=50%
    5:  Inspecionar armazém              riskBefore=60 riskAfter=15 riskPerSpy=6 success=30%
    6:  Guarnição militar                riskBefore=70 riskAfter=20 riskPerSpy=6 success=55%
    7:  Tropas e frotas                  riskBefore=50 riskAfter=22 riskPerSpy=2 success=20%
    8:  Chamar espião (recall)           riskBefore=0  riskAfter=0  riskPerSpy=1 success=95%
    10: Observar comunicação             riskBefore=90 riskAfter=26 riskPerSpy=5 success=40%
    21: Ver estado                       riskBefore=40 riskAfter=25 riskPerSpy=7 success=50%
    23: Espiar produção militar          riskBefore=65 riskAfter=15 riskPerSpy=6 success=45%
    24: Espiar cargo na aliança          riskBefore=60 riskAfter=15 riskPerSpy=6 success=50%
    25: Espiar forma de governo          riskBefore=30 riskAfter=5  riskPerSpy=4 success=60%
    26: Espiar invenções                 riskBefore=55 riskAfter=10 riskPerSpy=4 success=40%
    27: Espiar colônias                  riskBefore=30 riskAfter=5  riskPerSpy=3 success=45%

  Risk formula (approximate):
    actual_detection_risk = riskBefore - riskPerSpy * agents_sent
    Minimum risk = riskAfter

  Train spy (POST):
    action=Espionage&function=buildSpy
    count=N&cityId=<id>&position=<pos>
    Cost: 150 gold + 53 crystal per spy, ~3m34s each

  Get spy reports (GET):
    view=safehouse&activeTab=tabReports&cityId=<id>&position=<pos>&ajax=1
    Returns HTML table with report rows (id="message{reportId}") and
    hidden body rows (id="tbl_mail{reportId}") containing full report data.

  Delete report (GET/POST):
    action=Espionage&function=deleteReport
    reportId=<id>&offset=0&cityId=<id>&position=<pos>&ajax=1

  Mark as read:
    POST action=Espionage function=markReportAsRead reportId=<id> cityId=<id>

  Recall spy:
    action=Espionage&function=abortInvasion&cityId=<id>&position=<pos>&spy=<spy_id>
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)

# Known mission data (from live capture 2026-05-07)
MISSION_DATA: dict[int, dict[str, Any]] = {
    1:  {"name": "Enviar espião",             "risk_before": 30, "risk_after": 5,  "risk_per_spy": 3, "success": 60},
    3:  {"name": "Nível de pesquisa",          "risk_before": 35, "risk_after": 10, "risk_per_spy": 4, "success": 50},
    5:  {"name": "Inspecionar armazém",        "risk_before": 60, "risk_after": 15, "risk_per_spy": 6, "success": 30},
    6:  {"name": "Guarnição militar",          "risk_before": 70, "risk_after": 20, "risk_per_spy": 6, "success": 55},
    7:  {"name": "Tropas e frotas",            "risk_before": 50, "risk_after": 22, "risk_per_spy": 2, "success": 20},
    8:  {"name": "Chamar espião",              "risk_before": 0,  "risk_after": 0,  "risk_per_spy": 1, "success": 95},
    10: {"name": "Observar comunicação",       "risk_before": 90, "risk_after": 26, "risk_per_spy": 5, "success": 40},
    21: {"name": "Ver estado",                 "risk_before": 40, "risk_after": 25, "risk_per_spy": 7, "success": 50},
    23: {"name": "Produção militar",           "risk_before": 65, "risk_after": 15, "risk_per_spy": 6, "success": 45},
    24: {"name": "Cargo na aliança",           "risk_before": 60, "risk_after": 15, "risk_per_spy": 6, "success": 50},
    25: {"name": "Forma de governo",           "risk_before": 30, "risk_after": 5,  "risk_per_spy": 4, "success": 60},
    26: {"name": "Invenções",                  "risk_before": 55, "risk_after": 10, "risk_per_spy": 4, "success": 40},
    27: {"name": "Colônias",                   "risk_before": 30, "risk_after": 5,  "risk_per_spy": 3, "success": 45},
}


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"\s+", " ", text).strip()


def compute_agents_for_success(mission_id: int, target_success_pct: int = 80) -> int:
    """Calculate agents needed to reach target_success_pct% success probability.

    Real game formula: success = 1 - (1 - base_success/100)^N
    Solving for N: N = log(1 - target/100) / log(1 - base/100)

    NOTE: More agents = higher success BUT also higher minimum detection risk
    (minimumRisk = N * riskPerSpy). There is no way to reduce risk below this minimum.
    The actual detection risk also depends on target city level + free spies.
    """
    mdata = MISSION_DATA.get(mission_id, {})
    base_success = mdata.get("success", 60)
    if base_success <= 0:
        return 1
    if base_success >= target_success_pct:
        return 1

    p_base = base_success / 100.0
    p_target = target_success_pct / 100.0
    if p_base >= 1.0:
        return 1

    try:
        import math as _m
        n = _m.log(1 - p_target) / _m.log(1 - p_base)
        return max(1, math.ceil(n))
    except (ValueError, ZeroDivisionError):
        return 1


def compute_agents_for_success_dynamic(mission_data: dict, target_success_pct: int = 80) -> int:
    """Same as above but using live mission data for a specific target."""
    base_success = mission_data.get("success_chance", 60)
    if base_success <= 0:
        return 1
    if base_success >= target_success_pct:
        return 1
    p_base = base_success / 100.0
    p_target = target_success_pct / 100.0
    if p_base >= 1.0:
        return 1
    try:
        import math as _m
        n = _m.log(1 - p_target) / _m.log(1 - p_base)
        return max(1, math.ceil(n))
    except (ValueError, ZeroDivisionError):
        return 1


# Keep old name for compatibility — was completely wrong, now delegates to success-based
def compute_agents_for_risk(mission_id: int, max_detection_risk: int) -> int:
    """Deprecated: use compute_agents_for_success. Returns 1 agent as safe default."""
    return 1


def compute_agents_for_risk_dynamic(mission_data: dict, max_detection_risk: int) -> int:
    """Deprecated: use compute_agents_for_success_dynamic. Returns 1 agent as safe default."""
    return 1


class SpySafehouseAction(BaseAction):
    """Read the safehouse state: spy counts, active missions, training queue."""

    def execute(self, city_id: int | str, position: int = 19, **kwargs: Any) -> dict[str, Any]:
        params = {
            "view": "safehouse",
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("GET", self.client._server_url, params=params, headers=dict(GAME_AJAX_HEADERS))
        try:
            data = resp.json()
        except Exception as exc:
            raise ActionError(f"Failed to parse safehouse response: {exc}", action="safehouse")
        for _e in data:
            if isinstance(_e, list) and _e[0] == "updateGlobalData" and isinstance(_e[1], dict):
                _ar = _e[1].get("actionRequest")
                if _ar:
                    self.client._action_request = str(_ar)
                break

        html = ""
        for entry in data:
            if isinstance(entry, list) and entry[0] == "changeView" and isinstance(entry[1], list) and len(entry[1]) >= 2:
                html = entry[1][1]
                break

        return self._parse_safehouse_state(html, data)

    def _parse_safehouse_state(self, html: str, data: list) -> dict[str, Any]:
        """Parse spy counts and active missions from safehouse HTML."""
        state: dict[str, Any] = {
            "total_spies": 0,
            "available_spies": 0,
            "defending_spies": 0,
            "in_use_spies": 0,
            "training_count": 0,
            "active_missions": [],
        }

        # Parse spy_stats_content section
        m = re.search(r"Pode treinar (\d+)", html)
        if m:
            state["total_spies"] = int(m.group(1))
        m2 = re.search(r"(\d+)\s+espera[mn]? por treino", html)
        if m2:
            state["training_count"] = int(m2.group(1))
        m3 = re.search(r"(\d+)\s+est[aã]o trabalhando na defesa", html)
        if m3:
            state["defending_spies"] = int(m3.group(1))
        m4 = re.search(r"(\d+)\s+est[aã]o em uso", html)
        if m4:
            state["in_use_spies"] = int(m4.group(1))

        # "Trabalhando na defesa" = pool disponível para missões (ficam em casa na defesa
        # quando não estão em missão). available = total - em_uso - em_treinamento
        state["available_spies"] = max(
            0,
            state["total_spies"] - state["in_use_spies"] - state["training_count"]
        )

        # Parse active spy missions (SpyCountDown{spy_id} pattern)
        spy_ids = re.findall(r'SpyCountDown(\d+)', html)
        countdowns = re.findall(r'enddate:\s*(\d+)', html)
        abort_links = re.findall(
            r'function=abortInvasion&cityId=(\d+)&position=(\d+)&spy=(\d+)', html
        )
        for i, spy_id in enumerate(spy_ids):
            mission: dict[str, Any] = {"spy_id": spy_id}
            if i < len(countdowns):
                mission["return_timestamp"] = int(countdowns[i])
            if i < len(abort_links):
                mission["source_city_id"] = abort_links[i][0]
                mission["position"] = abort_links[i][1]
            state["active_missions"].append(mission)

        return state


# Map Portuguese resource names → standard keys for data_json
_RESOURCE_NAME_MAP = {
    "material de construção": "wood", "madeira": "wood",
    "vinho": "wine",
    "mármore": "marble",
    "cristal": "crystal",
    "enxofre": "sulfur",
    "ouro": "gold",
    "população": "population",
    "cidadãos livres": "free_citizens",
}


def _parse_number(s: str) -> int:
    """Parse Portuguese number string like '5.876' → 5876."""
    try:
        return int(re.sub(r"[.\s]", "", s.replace(",", ".")))
    except (ValueError, TypeError):
        return 0


def _normalise_report_data(raw_pairs: list) -> dict:
    """Convert raw (name, value) table rows to structured JSON usable for automation.

    Example output for mission 5 (warehouse):
        {"wood": 5876, "wine": 34, "marble": 557, "crystal": 0, "sulfur": 0}
    Example output for mission 6 (garrison):
        {"swordsman": 120, "slinger": 0, ...}
    """
    result: dict = {}
    for name, value in raw_pairs:
        key = _RESOURCE_NAME_MAP.get(name.lower().strip())
        if key:
            result[key] = _parse_number(value)
        else:
            # Store unknown fields with normalised key
            key_clean = re.sub(r"[^a-z0-9_]", "_", name.lower().strip())[:40]
            if key_clean:
                result[key_clean] = _parse_number(value) if re.search(r"\d", value) else value
    return result


class SpyMissionDataAction(BaseAction):
    """Fetch real-time mission risk/success data for a specific target city."""

    def execute(
        self,
        source_city_id: int | str,
        target_city_id: int | str,
        island_id: int | str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Fetch live missionData for a target city.

        Returns dict with mission_id → {name, risk_before, risk_after, risk_per_spy,
        success_chance, executable, completion_time, gold_cost}.
        Also returns target info: city_level, safehouse_level, is_inactive, free_spies.
        """
        params = {
            "view": "sendSpy",
            "isMission": "1",
            "destinationCityId": str(target_city_id),
            "islandId": str(island_id),
            "backgroundView": "island",
            "currentIslandId": str(island_id),
            "templateView": "cityDetails",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("GET", self.client._server_url, params=params, headers=dict(GAME_AJAX_HEADERS))
        try:
            data = resp.json()
        except Exception as exc:
            raise ActionError(f"Failed to fetch spy mission data: {exc}", action="sendSpy")

        for _e in data:
            if isinstance(_e, list) and _e[0] == "updateGlobalData" and isinstance(_e[1], dict):
                _ar = _e[1].get("actionRequest")
                if _ar:
                    self.client._action_request = str(_ar)
                break

        js_params: dict[str, Any] = {}
        for entry in data:
            if isinstance(entry, list) and entry[0] == "updateTemplateData" and isinstance(entry[1], dict):
                lj = entry[1].get("load_js") or {}
                if isinstance(lj, dict):
                    params_str = lj.get("params", "")
                    if params_str:
                        try:
                            js_params = json.loads(params_str)
                        except Exception:
                            pass
                break

        if not js_params:
            return {"missions": {}, "target": {}}

        target_info = {
            "city_level": js_params.get("targetCityLevel", 0),
            "safehouse_level": js_params.get("targetSafehouseLevel", 0),
            "is_inactive": bool(js_params.get("isTargetInactive", False)),
            "free_spies": js_params.get("targetFreeSpies", 0),
        }

        missions: dict[int, dict[str, Any]] = {}
        raw_missions = js_params.get("missionData", {})
        for mid_str, mdata in raw_missions.items():
            if not isinstance(mdata, dict) or "name" not in mdata:
                continue
            mid = int(mid_str)
            missions[mid] = {
                "name": mdata.get("name", ""),
                "risk_before": mdata.get("riskBefore", 0),
                "risk_after": mdata.get("riskAfter", 0),
                "risk_per_spy": mdata.get("riskPerSpy", 1) or 1,
                "success_chance": mdata.get("successChance", 0),
                "executable": bool(mdata.get("executableMission", False)),
                "completion_time": mdata.get("completionTime", 0),
                "gold_cost": mdata.get("gold", 0),
            }

        logger.info(
            "SpyMissionData: target=%s level=%d inactive=%s missions=%d",
            target_city_id, target_info["city_level"], target_info["is_inactive"], len(missions),
        )
        return {"missions": missions, "target": target_info}


def compute_agents_for_risk_dynamic(mission_data: dict, max_detection_risk: int) -> int:
    """Calculate agents needed given live mission data for a specific target."""
    risk_before = mission_data.get("risk_before", 0)
    risk_after = mission_data.get("risk_after", 0)
    risk_per_spy = max(1, mission_data.get("risk_per_spy", 1))

    if risk_before <= max_detection_risk:
        return 1
    needed = math.ceil((risk_before - max_detection_risk) / risk_per_spy)
    max_needed = math.ceil((risk_before - risk_after) / risk_per_spy)
    return max(1, min(needed, max(1, max_needed)))


class SpySendAction(BaseAction):
    """Send spies on a mission."""

    def execute(
        self,
        source_city_id: int | str,
        target_city_id: int | str,
        island_id: int | str,
        mission_id: int = 1,
        agents: int = 1,
        decoys: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send spy mission.

        Args:
            source_city_id: City with safehouse.
            target_city_id: City to spy on.
            island_id: Island ID of the target city.
            mission_id: Mission type (see MISSION_DATA).
            agents: Number of spy agents.
            decoys: Number of decoy spies.

        Returns:
            {"success": bool, "message": str}
        """
        params = {
            "action": "Espionage",
            "function": "sendSpy",
            "destinationCityId": str(target_city_id),
            "cityId": str(island_id),
            "islandId": str(island_id),
            f"spies[{source_city_id}][agents]": str(agents),
            f"spies[{source_city_id}][decoys]": str(decoys),
            f"spies[{source_city_id}][missionId]": str(mission_id),
            "backgroundView": "island",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=dict(GAME_AJAX_HEADERS))
        try:
            data = resp.json()
        except Exception as exc:
            raise ActionError(f"Failed to parse sendSpy response: {exc}", action="sendSpy")
        for _e in data:
            if isinstance(_e, list) and _e[0] == "updateGlobalData" and isinstance(_e[1], dict):
                _ar = _e[1].get("actionRequest")
                if _ar:
                    self.client._action_request = str(_ar)
                break

        success = False
        message = ""
        for entry in data:
            if isinstance(entry, list) and entry[0] == "provideFeedback":
                for fb in (entry[1] or []):
                    if isinstance(fb, dict):
                        message = fb.get("text", "")
                        if fb.get("type") == 10:
                            success = True

        mission_name = MISSION_DATA.get(mission_id, {}).get("name", f"missão {mission_id}")
        if not message:
            success = True  # silent success (no error feedback)
            message = f"{mission_name} enviada"

        logger.info(
            "SpySend: source=%s target=%s mission=%s agents=%d decoys=%d success=%s",
            source_city_id, target_city_id, mission_id, agents, decoys, success,
        )
        return {"success": success, "message": message}


class SpyTrainAction(BaseAction):
    """Train spies at the safehouse."""

    def execute(
        self,
        city_id: int | str,
        count: int = 1,
        position: int = 19,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Train spies. Cost: 150 gold + 53 crystal each, ~3m34s per spy."""
        params = {
            "action": "Espionage",
            "function": "buildSpy",
            "count": str(count),
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=dict(GAME_AJAX_HEADERS))
        try:
            data = resp.json()
            for _e in data:
                if isinstance(_e, list) and _e[0] == "updateGlobalData" and isinstance(_e[1], dict):
                    _ar = _e[1].get("actionRequest")
                    if _ar:
                        self.client._action_request = str(_ar)
                    break
        except Exception:
            pass
        logger.info("SpyTrain: city=%s count=%d", city_id, count)
        return {"success": True, "count": count}


class SpyReportsAction(BaseAction):
    """Read and parse spy reports from the safehouse."""

    def execute(
        self,
        city_id: int | str,
        position: int = 19,
        tab: str = "tabReports",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Fetch spy reports.

        Args:
            city_id: City with safehouse.
            position: Building position of safehouse (default 19).
            tab: "tabReports" for new reports, "tabArchive" for archived.

        Returns:
            List of report dicts.
        """
        params = {
            "view": "safehouse",
            "activeTab": tab,
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("GET", self.client._server_url, params=params, headers=dict(GAME_AJAX_HEADERS))
        try:
            data = resp.json()
        except Exception as exc:
            raise ActionError(f"Failed to parse spy reports response: {exc}", action=tab)
        for _e in data:
            if isinstance(_e, list) and _e[0] == "updateGlobalData" and isinstance(_e[1], dict):
                _ar = _e[1].get("actionRequest")
                if _ar:
                    self.client._action_request = str(_ar)
                break

        html = ""
        for entry in data:
            if isinstance(entry, list) and entry[0] == "changeView" and isinstance(entry[1], list) and len(entry[1]) >= 2:
                html = entry[1][1]
                break

        return self._parse_reports(html)

    def _parse_reports(self, html: str) -> list[dict[str, Any]]:
        """Parse report rows and their hidden body content."""
        reports = []

        # Find all report header rows: id="message{id}"
        header_pattern = re.compile(
            r'id="message(\d+)"[^>]*>(.*?)(?=id="tbl_mail\d+"|id="message\d+"|</table>)',
            re.DOTALL,
        )
        body_pattern = re.compile(
            r'id="tbl_mail(\d+)"[^>]*>(.*?)(?=id="tbl_mail\d+"|id="message\d+"|</table>)',
            re.DOTALL,
        )

        header_map: dict[str, str] = {}
        for m in header_pattern.finditer(html):
            header_map[m.group(1)] = m.group(2)

        body_map: dict[str, str] = {}
        for m in body_pattern.finditer(html):
            body_map[m.group(1)] = m.group(2)

        for report_id, header_html in header_map.items():
            report: dict[str, Any] = {"report_id": report_id}

            # Is it unread?
            report["unread"] = 'value="unread"' in header_html or 'class="espionageReports bold"' in html[max(0, html.find(f'id="message{report_id}"')):html.find(f'id="message{report_id}"')+200]

            # Target owner
            m = re.search(r'class="targetOwner[^"]*"[^>]*>\s*([^<\n]+)', header_html)
            report["target_owner"] = _strip_html(m.group(1)) if m else ""

            # Target city + coords
            m2 = re.search(r'selectCity=(\d+)[^>]*>([^<]+)', header_html)
            if m2:
                report["target_city_id"] = m2.group(1)
                report["target_city_name"] = _strip_html(m2.group(2))
            coords = re.search(r'\[(\d+)\s*:\s*(\d+)\]', header_html)
            if coords:
                report["target_x"] = int(coords.group(1))
                report["target_y"] = int(coords.group(2))

            # Mission subject
            m3 = re.search(r'class="subject[^"]*"[^>]*>([^<\n]+)', header_html)
            report["subject"] = _strip_html(m3.group(1)) if m3 else ""

            # Result status (from img title)
            m4 = re.search(r'class="resultImage".*?title="([^"]+)"', header_html, re.DOTALL)
            report["result_status"] = m4.group(1) if m4 else ""

            # Agents lost/sent
            m5 = re.search(r'class="lostAgents"[^>]*>([^<]+)', header_html)
            if m5:
                agent_text = m5.group(1).strip()
                parts = agent_text.split("/")
                report["agents_lost"] = int(parts[0].strip().replace(".", "")) if len(parts) > 0 and parts[0].strip().isdigit() else 0
                report["agents_sent"] = int(parts[1].strip().replace(".", "")) if len(parts) > 1 and parts[1].strip().isdigit() else 0

            # Date
            m6 = re.search(r'class="date[^"]*"[^>]*>([^<]+)', header_html)
            report["date_str"] = m6.group(1).strip() if m6 else ""

            # Report body
            body_html = body_map.get(report_id, "")
            if body_html:
                # Parse status
                m_status = re.search(r'class="status".*?<td>([^<]+)</td>', body_html, re.DOTALL)
                report["status"] = _strip_html(m_status.group(1)) if m_status else ""

                # Parse report text (may contain tables with resources, troops, etc.)
                m_report = re.search(r'class="report">([\s\S]*?)(?:</td>|</tr>)', body_html)
                if m_report:
                    report_content = m_report.group(1)
                    report["report_html"] = report_content.strip()
                    report["report_text"] = _strip_html(report_content)

                # Parse structured data from tables (resources, troops, gold, etc.)
                # data_json is usable for automation: {"wood": 5876, "wine": 34, ...}
                if "resourcesTable" in body_html or "reportTable" in body_html or "unitTable" in body_html:
                    rows = re.findall(r'<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>', body_html, re.DOTALL)
                    raw_pairs = [(_strip_html(r[0]), _strip_html(r[1])) for r in rows if _strip_html(r[0]) and _strip_html(r[1])]
                    report["data_table"] = raw_pairs
                    # Normalise to structured dict for systematic use
                    report["data_json"] = _normalise_report_data(raw_pairs)

            reports.append(report)

        return reports


class SpyDeleteReportAction(BaseAction):
    """Delete a spy report."""

    def execute(self, city_id: int | str, report_id: int | str, position: int = 19, **kwargs: Any) -> dict:
        params = {
            "action": "Espionage",
            "function": "deleteReport",
            "reportId": str(report_id),
            "offset": "0",
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        self.client._request("GET", self.client._server_url, params=params, headers=dict(GAME_AJAX_HEADERS))
        return {"success": True}
