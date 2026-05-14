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
    islandId=<island_id>
    currentIslandId=<island_id>
    spies[<source_city_id>][agents]=N    ← literal brackets, NOT %5B/%5D
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
import unicodedata
from typing import Any
from urllib.parse import urlencode

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


def _parse_feedback_entries(data: list) -> tuple[bool, str]:
    success = False
    message = ""
    for entry in data:
        if not (isinstance(entry, list) and entry and entry[0] == "provideFeedback"):
            continue
        for fb in (entry[1] or []):
            if not isinstance(fb, dict):
                continue
            message = _strip_html(str(fb.get("text", "") or ""))
            if fb.get("type") == 10:
                success = True
    return success, message


def _normalise_mission_name(mission_id: int | None, subject: str = "") -> str:
    if mission_id in MISSION_DATA:
        return str(MISSION_DATA[mission_id]["name"])
    inferred = _infer_mission_id(subject) if subject else None
    if inferred in MISSION_DATA:
        return str(MISSION_DATA[inferred]["name"])
    return ""


def compute_spy_risks(live_params: dict, mission_id: int, agents: int, decoys: int) -> dict[str, float]:
    """Exact replication of the game's JS risk/success formula.

    Verified against live game: 3 agents + 1 decoy → agentRisk=29%, decoyRisk=22%, success=67% ✓

    Key insight: basicRisk = freeSpies*5 - cityLevel*2
    (higher city level = LOWER risk, more enemy free spies = HIGHER risk)
    """
    md = live_params.get("missionData", {}).get(str(mission_id), {})
    if not md:
        return {"agents": agents, "decoys": decoys, "agent_risk": 100.0, "decoy_risk": 100.0, "success": 0.0}

    target_level = float(live_params.get("targetCityLevel", 0))
    free_spies = float(live_params.get("targetFreeSpies", 0))
    is_inactive = bool(live_params.get("isTargetInactive", False))
    remaining_risk = float(live_params.get("remainingRisk", 0))
    gov_factor = float(live_params.get("targetGovFactor", 20))
    safehouse_level = float(live_params.get("targetSafehouseLevel", 0))

    min_chance = float(live_params.get("cEspionageMinChance", 5))
    max_chance = float(live_params.get("cEspionageMaxChance", 95))
    max_success_cap = float(live_params.get("cEspionageMaxSuccessChance", 99))
    risk_red_inactive = float(live_params.get("cSpyRiskReductionFactorInactivity", 10))
    risk_red_no_safe = float(live_params.get("cSpyRiskReductionFactorNoSafehouse", 2))
    decoy_risk_red = float(live_params.get("cEspionageDecoyRiskREduction", 10))
    min_decoy_risk = float(live_params.get("cEspionageMinDecoyRisk", 2))
    basic_decoy_risk_k = float(live_params.get("cEspionageBasicDecoyRisk", 5))

    risk_before = float(md.get("riskBefore", 0))
    risk_per_spy = float(md.get("riskPerSpy", 3))
    base_success = float(md.get("successChance", 60))

    # Core formula (JS: basicRisk = targetFreeSpies*5 - targetCityLevel*2)
    basic_risk = free_spies * 5 - target_level * 2
    mission_risk = basic_risk + risk_before + remaining_risk
    min_risk = max(min_chance, agents * risk_per_spy)

    if is_inactive:
        mission_risk /= risk_red_inactive
        min_risk /= risk_red_inactive
    elif safehouse_level == 0:
        mission_risk /= risk_red_no_safe
        min_risk /= risk_red_no_safe

    # Agent detection risk (decoys reduce the missionRisk component)
    agent_risk_raw = gov_factor + max(min_risk, mission_risk - decoys * decoy_risk_red)
    agent_risk = max(min_chance, min(max_chance, round(agent_risk_raw * 10) / 10))

    # Decoy detection risk
    decoy_risk_raw = gov_factor + max(decoys * min_decoy_risk, mission_risk / 4 - 50 + decoys * basic_decoy_risk_k)
    decoy_risk = max(min_chance, min(max_chance, round(decoy_risk_raw)))

    # Success probability
    import math as _math
    try:
        base_chance = min(round((1 - (1 - base_success / 100) ** agents) * 100), max_success_cap)
    except Exception:
        base_chance = 0
    final_success = max(min_chance, min(max_chance, round((100 - agent_risk) * base_chance / 100)))
    if agents == 0:
        final_success = 0.0

    return {
        "agents": agents,
        "decoys": decoys,
        "agent_risk": agent_risk,
        "decoy_risk": decoy_risk,
        "success": final_success,
        "min_risk": min_risk,
    }


def find_optimal_agents_decoys(
    live_params: dict,
    mission_id: int,
    max_agent_risk: float,
    max_decoy_risk: float,
    min_success: float,
    available: int,
) -> dict | None:
    """Find optimal (agents, decoys) that meets user's thresholds.

    Tries all combinations up to available spies. Among valid combinations,
    it treats near-maximum success as a plateau and returns the cheapest
    combination in that plateau, then lower risk as a final tie-breaker.
    Returns None if no combination meets the thresholds.
    """
    candidates: list[dict] = []

    max_agents = min(available, 28)
    for agents in range(1, max_agents + 1):
        for decoys in range(0, min(available - agents + 1, 15)):
            r = compute_spy_risks(live_params, mission_id, agents, decoys)
            if r["agent_risk"] > max_agent_risk:
                continue
            if r["decoy_risk"] > max_decoy_risk:
                continue
            if r["success"] < min_success:
                continue
            candidates.append(r)

    if not candidates:
        return None

    max_success = max(float(c["success"]) for c in candidates)
    success_floor = max(float(min_success), max_success - 5.0)
    plateau = [c for c in candidates if float(c["success"]) >= success_floor]
    return min(
        plateau,
        key=lambda c: (
            int(c["agents"]) + int(c.get("decoys") or 0),
            float(c["agent_risk"]) + float(c["decoy_risk"]) * 0.2,
            -float(c["success"]),
        ),
    )


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

        state = self._parse_safehouse_state(html, data)
        state["_raw_html"] = html  # stored for debug logging by runner
        return state

    def _parse_safehouse_state(self, html: str, data: list) -> dict[str, Any]:
        """Parse spy counts and active missions from safehouse HTML."""
        state: dict[str, Any] = {
            "total_spies": 0,
            "available_spies": 0,
            "defending_spies": 0,
            "in_use_spies": 0,
            "training_count": 0,
            "active_missions": [],
            "target_groups": [],
        }

        # Parse spy_stats_content section
        # "Pode treinar N" = remaining training capacity (NOT the trained spy count)
        m = re.search(r"Pode treinar (\d+)", html)
        spy_capacity = int(m.group(1)) if m else 0
        state["spy_capacity"] = spy_capacity

        # The list item "N esperam por treino" is an available training slot in this view,
        # not an active training queue. Keep training_count at 0 unless the game exposes
        # a real countdown/queue marker in this page.
        m3 = re.search(r"(\d+)\s+est[aã]o trabalhando na defesa", html)
        if m3:
            state["defending_spies"] = int(m3.group(1))
        m4 = re.search(r"(\d+)\s+est[aã]o em uso", html)
        if m4:
            state["in_use_spies"] = int(m4.group(1))

        # Spies "trabalhando na defesa" = trained spies at home, available to send on missions
        # Spies "em uso" = currently on missions (not available)
        # "Pode treinar N" = remaining capacity, NOT the trained count
        state["total_spies"] = state["defending_spies"] + state["in_use_spies"]
        state["available_spies"] = state["defending_spies"]

        state["stationed_by_city"] = {}
        state["in_transit_by_city"] = {}

        mission_idx = html.find('class="spyinfo"')
        if mission_idx >= 0:
            mission_idx = max(0, html.rfind("<div", 0, mission_idx))
        if mission_idx < 0:
            mission_idx = html.find("EspiÃƒÂ£o em MissÃƒÂ£o")
        seen_spy_ids: set[str] = set()
        if mission_idx >= 0:
            mission_section = html[mission_idx:mission_idx + 30000]
            for block in re.findall(r'<div class="spyinfo">([\s\S]{0,5000}?)</div>\s*</div>', mission_section):
                block_text = re.sub(r"\s+", " ", _strip_html(block)).strip()
                owner = ""
                owner_m = re.search(r'<li[^>]*class="user"[^>]*>[\s\S]*?<a[^>]*>([^<]+)</a>', block)
                if owner_m:
                    owner = _strip_html(owner_m.group(1))

                city_name = ""
                city_m = re.search(r'<li[^>]*class="city"[^>]*>[\s\S]*?<a[^>]*>([^<]+)</a>', block)
                if city_m:
                    city_name = _strip_html(city_m.group(1))
                    city_name = re.sub(r"\s*\(\s*\d+\s*:\s*\d+\s*\)\s*$", "", city_name).strip()

                coords_m = re.search(r"\[(\d+)\s*:\s*(\d+)\]", block)
                x = int(coords_m.group(1)) if coords_m else None
                y = int(coords_m.group(2)) if coords_m else None

                if owner and (not city_name or city_name.lower() in {"retirar", "retreat"}):
                    city_text_m = re.search(
                        rf"^{re.escape(owner)}\s+(.+?)\s+\[(\d+)\s*:\s*(\d+)\]",
                        block_text,
                    )
                    if city_text_m:
                        city_name = city_text_m.group(1).strip()
                        x = int(city_text_m.group(2))
                        y = int(city_text_m.group(3))

                target_city_id = ""
                for pattern in (
                    r"targetCityId=(\d+)",
                    r"destinationCityId=(\d+)",
                    r"selectCity=(\d+)",
                ):
                    target_city_m = re.search(pattern, block)
                    if target_city_m:
                        target_city_id = target_city_m.group(1)
                        break

                count = 0
                stationed_m = re.search(r'(\d+)\s+est[aÃƒÂ£]o em uso', block)
                if not stationed_m:
                    stationed_m = re.search(r'(\d+)\s+est\S*o em uso', block_text, re.IGNORECASE)
                if stationed_m:
                    count = int(stationed_m.group(1))

                # Detect stationed spies — multiple encodings of "esperam novas ordens"
                _block_lower = block.lower()
                waiting = (
                    "esperam novas ordens" in _block_lower
                    or "esperam novas" in _block_lower
                    or "novas ordens" in _block_lower
                    or "waiting for orders" in _block_lower
                    or "awaiting orders" in _block_lower
                    # Latin-1 mis-decode of UTF-8
                    or "esperam novas ordens" in block
                    or re.search(r"esperam\s+novas\s+ordens", block, re.IGNORECASE) is not None
                )
                travelling = ("a caminho" in _block_lower or "SpyCountDown" in block) and not waiting

                status = ""
                status_m = re.search(r'<span[^>]*class="missionTxt"[^>]*>([\s\S]*?)</span>', block)
                if status_m:
                    status = _strip_html(status_m.group(1))
                elif waiting:
                    status = "Os teus espiÃµes esperam novas ordens."

                spy_id = ""
                spy_id_m = re.search(r"spy=(\d+)", block)
                if spy_id_m:
                    spy_id = spy_id_m.group(1)
                else:
                    spy_countdown_m = re.search(r"SpyCountDown(\d+)", block)
                    if spy_countdown_m:
                        spy_id = spy_countdown_m.group(1)

                return_ts = None
                ts_m = re.search(rf"SpyCountDown{re.escape(spy_id)}[\s\S]*?enddate:\s*(\d+)", mission_section) if spy_id else None
                if not ts_m:
                    ts_m = re.search(r"enddate:\s*(\d+)", block)
                if ts_m:
                    return_ts = int(ts_m.group(1))

                mission_view_url = ""
                mission_view_m = re.search(r'href="([^"]*view=spyMissions[^"]*targetCityId=\d+[^"]*)"', block)
                if mission_view_m:
                    mission_view_url = mission_view_m.group(1)

                retreat_url = ""
                retreat_m = re.search(r'href="([^"]*retreat=1[^"]*)"', block)
                if retreat_m:
                    retreat_url = retreat_m.group(1)

                abort_url = ""
                abort_m = re.search(r'href="([^"]*abortInvasion[^"]*)"', block)
                if abort_m:
                    abort_url = abort_m.group(1)

                if not target_city_id:
                    for candidate_url in (mission_view_url, retreat_url, abort_url):
                        if not candidate_url:
                            continue
                        for pattern in (
                            r"targetCityId=(\d+)",
                            r"destinationCityId=(\d+)",
                            r"selectCity=(\d+)",
                        ):
                            target_city_m = re.search(pattern, candidate_url)
                            if target_city_m:
                                target_city_id = target_city_m.group(1)
                                break
                        if target_city_id:
                            break

                if not any([owner, city_name, target_city_id, count, spy_id, status]):
                    continue

                group = {
                    "spy_id": spy_id,
                    "owner": owner,
                    "city_name": city_name,
                    "target_city_id": target_city_id,
                    "x": x,
                    "y": y,
                    "count_in_use": count,
                    "status": status,
                    "is_waiting": waiting,
                    "is_travelling": travelling and not waiting,
                    "return_timestamp": return_ts,
                    "mission_view_url": mission_view_url,
                    "retreat_url": retreat_url,
                    "abort_url": abort_url,
                }
                state["target_groups"].append(group)

                city_key = city_name or target_city_id or owner or "unknown"
                if waiting:
                    state["stationed_by_city"][city_key] = state["stationed_by_city"].get(city_key, 0) + count
                elif travelling:
                    state["in_transit_by_city"][city_key] = state["in_transit_by_city"].get(city_key, 0) + count

                if spy_id and spy_id not in seen_spy_ids:
                    seen_spy_ids.add(spy_id)
                    mission: dict[str, Any] = {
                        "spy_id": spy_id,
                        "target_city_id": target_city_id,
                        "city_name": city_name,
                        "owner": owner,
                        "status": status,
                        "count_in_use": count,
                        "is_waiting": waiting,
                        "is_travelling": travelling and not waiting,
                    }
                    if return_ts is not None:
                        mission["return_timestamp"] = return_ts
                    state["active_missions"].append(mission)

        return state

        # Parse "Espião em Missão" section for stationed and in-transit counts per city
        state["stationed_by_city"] = {}   # {city_name: count} — waiting for orders
        state["in_transit_by_city"] = {}  # {city_name: count} — still travelling

        mission_idx = html.find("Espião em Missão")
        if mission_idx >= 0:
            mission_section = html[mission_idx:mission_idx + 20000]
            # Each <div class="spyinfo"> is one group at one location
            for block in re.findall(r'<div class="spyinfo">([\s\S]{0,3000}?)</div>\s*</div>', mission_section):
                # City: appears as "CityName (x:y)"
                city_m = re.search(r'<li[^>]*>([^<(]+\s*\(\s*\d+\s*:\s*\d+\s*\))', block)
                city_name = city_m.group(1).strip() if city_m else ""
                # Stationed: "N estão em uso" + "esperam novas ordens"
                stationed_m = re.search(r'(\d+)\s+est[aã]o em uso', block)
                if stationed_m and city_name:
                    count = int(stationed_m.group(1))
                    state["stationed_by_city"][city_name] = (
                        state["stationed_by_city"].get(city_name, 0) + count
                    )
                # In-transit: has SpyCountDown but no "em uso" waiting text
                elif "SpyCountDown" in block:
                    target_m = re.search(r'<li class="user">[^<]*<a[^>]*>([^<]+)</a>', block)
                    tgt = target_m.group(1).strip() if target_m else city_name
                    spy_count = len(re.findall(r'SpyCountDown\d+', block))
                    if tgt:
                        state["in_transit_by_city"][tgt] = (
                            state["in_transit_by_city"].get(tgt, 0) + spy_count
                        )

        # Legacy active_missions list (countdowns)
        spy_ids = re.findall(r'SpyCountDown(\d+)', html)
        countdowns = re.findall(r'enddate:\s*(\d+)', html)
        for i, spy_id in enumerate(spy_ids):
            mission: dict[str, Any] = {"spy_id": spy_id}
            if i < len(countdowns):
                mission["return_timestamp"] = int(countdowns[i])
            state["active_missions"].append(mission)

        return state


# Map Portuguese resource names → standard keys for data_json
_RESOURCE_NAME_MAP = {
    "material de construção": "wood", "madeira": "wood",
    "vinho": "wine", "vinho (produção)": "wine",
    "mármore": "marble",
    "cristal": "crystal",
    "enxofre": "sulfur",
    "ouro": "gold",
    "população": "population",
    "cidadãos livres": "free_citizens",
    "nível de pesquisa": "research_level",
    "nível de ciência": "science_level",
    "pontos de pesquisa": "research_points",
}

_RESOURCE_KEY_MAP = {
    "material de construcao": "wood",
    "madeira": "wood",
    "vinho": "wine",
    "vinho producao": "wine",
    "marmore": "marble",
    "cristal": "crystal",
    "enxofre": "sulfur",
    "ouro": "gold",
    "populacao": "population",
    "cidadaos livres": "free_citizens",
    "nivel de pesquisa": "research_level",
    "nivel de ciencia": "science_level",
    "pontos de pesquisa": "research_points",
}

# Map report subject keywords → mission_id
_SUBJECT_TO_MISSION_ID: list[tuple[str, int]] = [
    ("chegou a", 1), ("enviado", 1),
    ("nível de pesquisa", 3), ("pesquisa", 3),
    ("sala do tesouro", 4), ("tesouro", 4),
    ("recursos em", 5), ("armazém", 5), ("inspecionar", 5),
    ("guarnição", 6), ("garrison", 6),
    ("tropas", 7), ("frotas", 7),
    ("estado", 21),
    ("produção militar", 23),
    ("cargo", 24),
    ("forma de governo", 25), ("governo", 25),
    ("invenções", 26),
    ("colônias", 27),
]


def _infer_mission_id(subject: str) -> int | None:
    s = subject.lower()
    for keyword, mid in _SUBJECT_TO_MISSION_ID:
        if keyword in s:
            return mid
    return None


def _parse_number(s: str) -> int:
    """Parse Portuguese number string like '5.876' → 5876."""
    try:
        return int(re.sub(r"[.\s]", "", s.replace(",", ".")))
    except (ValueError, TypeError):
        return 0


def _normalise_label(label: str) -> str:
    text = unicodedata.normalize("NFKD", str(label or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _cell_text(cell_html: str) -> str:
    img_label = re.search(r'<img\b[^>]*(?:alt|title)="([^"]+)"', cell_html, re.IGNORECASE)
    if img_label:
        return _strip_html(img_label.group(1))
    return _strip_html(cell_html)


def _is_report_header_pair(name: str, value: str) -> bool:
    return _normalise_label(name) == "recursos" and _normalise_label(value) == "quantidade"


def _normalise_report_data(raw_pairs: list) -> dict:
    """Convert raw (name, value) table rows to structured JSON usable for automation.

    Example output for mission 5 (warehouse):
        {"wood": 5876, "wine": 34, "marble": 557, "crystal": 0, "sulfur": 0}
    Example output for mission 6 (garrison):
        {"swordsman": 120, "slinger": 0, ...}
    """
    result: dict = {}
    for name, value in raw_pairs:
        key = _RESOURCE_NAME_MAP.get(name.lower().strip()) or _RESOURCE_KEY_MAP.get(_normalise_label(name))
        if key:
            result[key] = _parse_number(value)
        else:
            # Store unknown fields with normalised key
            key_clean = re.sub(r"[^a-z0-9]+", "_", _normalise_label(name)).strip("_")[:40]
            if key_clean:
                result[key_clean] = _parse_number(value) if re.search(r"\d", value) else value
    return result


def _extract_hidden_value(html: str, name: str) -> str:
    match = re.search(rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', html)
    return match.group(1) if match else ""


def _extract_next_td_html(html: str, label_pattern: str) -> str:
    label_match = re.search(label_pattern, html, re.IGNORECASE)
    if not label_match:
        return ""
    first_td_end = html.find("</td>", label_match.end())
    if first_td_end < 0:
        return ""
    second_td_start = re.search(r"<td\b[^>]*>", html[first_td_end:], re.IGNORECASE)
    if not second_td_start:
        return ""
    start_tag_start = first_td_end + second_td_start.start()
    content_start = first_td_end + second_td_start.end()
    depth = 1
    token_re = re.compile(r"</?td\b[^>]*>", re.IGNORECASE)
    for token in token_re.finditer(html, content_start):
        tag = token.group(0)
        if tag.startswith("</"):
            depth -= 1
            if depth == 0:
                return html[content_start:token.start()]
        else:
            depth += 1
    return html[content_start:]


def _extract_report_text_lines(report_html: str) -> list[str]:
    lines: list[str] = []
    for raw in re.split(r"<br\s*/?>|</p>|</li>|</tr>", report_html):
        text = _strip_html(raw)
        if text and text not in lines:
            lines.append(text)
    return lines


def _extract_report_details(mission_id: int | None, report_text: str, pairs: list[tuple[str, str]]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    text = " ".join(report_text.split())
    if mission_id == 3 and pairs:
        details["research"] = {name: value for name, value in pairs}
        if "Pontos de pesquisa" in details["research"]:
            details["research_points"] = _parse_number(details["research"]["Pontos de pesquisa"])
        return details
    if mission_id == 3 and text:
        match = re.search(r"Campo de pesquisa\s+(.+)", text, re.IGNORECASE)
        if match:
            payload = match.group(1).strip()
            details["research_path"] = payload
            parts = payload.split()
            if parts:
                details["research_field"] = parts[0]
            if len(parts) > 1:
                details["research_topic"] = " ".join(parts[1:])
    elif mission_id == 25 and text:
        if "nÃ£o foi bem sucedida" not in text.lower():
            details["government"] = text

    if pairs and not details:
        flat = [f"{name}: {value}" for name, value in pairs[:6]]
        if flat:
            details["summary"] = " | ".join(flat)
    elif text and not details:
        details["summary"] = text[:240]
    return details


def _extract_research_pairs(report_text: str) -> list[tuple[str, str]]:
    labels = [
        "Campo de pesquisa",
        "Navegação Marítima",
        "Economia",
        "Ciência",
        "Militar",
        "Mitologia",
        "Pontos de pesquisa",
    ]
    text = " ".join((report_text or "").split())
    positions: list[tuple[int, str]] = []
    for label in labels:
        match = re.search(re.escape(label), text, re.IGNORECASE)
        if match:
            positions.append((match.start(), label))
    positions.sort()
    pairs: list[tuple[str, str]] = []
    for idx, (start, label) in enumerate(positions):
        value_start = start + len(label)
        value_end = positions[idx + 1][0] if idx + 1 < len(positions) else len(text)
        value = text[value_start:value_end].strip(" :\t")
        if value:
            pairs.append((label, value))
    return pairs


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
        return {"missions": missions, "target": target_info, "raw_params": js_params}


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
        from urllib.parse import urlencode

        # Build base params with normal URL encoding
        base_params = {
            "action": "Espionage",
            "function": "sendSpy",
            "destinationCityId": str(target_city_id),
            "islandId": str(island_id),
            "currentIslandId": str(island_id),
            "backgroundView": "island",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        # Append spies[] array with literal brackets (NOT %5B/%5D) — game parser expects raw brackets
        body = (
            urlencode(base_params)
            + f"&spies[{source_city_id}][agents]={agents}"
            + f"&spies[{source_city_id}][decoys]={decoys}"
            + f"&spies[{source_city_id}][missionId]={mission_id}"
        )
        send_headers = dict(GAME_AJAX_HEADERS)
        send_headers["Content-Type"] = "application/x-www-form-urlencoded"
        resp = self.client._request("POST", self.client._server_url, data=body, headers=send_headers)
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
    """Train replacement spies in a safehouse."""

    def execute(
        self,
        city_id: int | str,
        count: int = 1,
        position: int = 19,
        **kwargs: Any,
    ) -> dict[str, Any]:
        count = max(1, int(count or 1))
        params = {
            "action": "Espionage",
            "function": "buildSpy",
            "cityId": str(city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        body = urlencode(params) + f"&count={count}"
        headers = dict(GAME_AJAX_HEADERS)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        resp = self.client._request("POST", self.client._server_url, data=body, headers=headers)
        try:
            data = resp.json()
        except Exception as exc:
            raise ActionError(f"Failed to parse buildSpy response: {exc}", action="buildSpy")

        for entry in data:
            if isinstance(entry, list) and entry[0] == "updateGlobalData" and isinstance(entry[1], dict):
                action_request = entry[1].get("actionRequest")
                if action_request:
                    self.client._action_request = str(action_request)
                break

        success, message = _parse_feedback_entries(data)
        if not message:
            success = True
            message = f"Treino de {count} espião(ões) solicitado"
        return {"success": success, "message": message}


class SpyMissionAssignmentAction(BaseAction):
    """Open the internal spy mission view for an already infiltrated group."""

    def execute(
        self,
        source_city_id: int | str,
        target_city_id: int | str,
        position: int = 19,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = {
            "view": "spyMissions",
            "targetCityId": str(target_city_id),
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(source_city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("GET", self.client._server_url, params=params, headers=dict(GAME_AJAX_HEADERS))
        try:
            data = resp.json()
        except Exception as exc:
            raise ActionError(f"Failed to parse spyMissions response: {exc}", action="spyMissions")

        for entry in data:
            if isinstance(entry, list) and entry[0] == "updateGlobalData" and isinstance(entry[1], dict):
                action_request = entry[1].get("actionRequest")
                if action_request:
                    self.client._action_request = str(action_request)
                break

        html = ""
        load_js_params: dict[str, Any] = {}
        for entry in data:
            if isinstance(entry, list) and entry[0] == "changeView" and isinstance(entry[1], list) and len(entry[1]) >= 2:
                html = entry[1][1]
            if isinstance(entry, list) and entry[0] == "updateTemplateData" and isinstance(entry[1], dict):
                load_js = entry[1].get("load_js") or {}
                params_str = load_js.get("params", "") if isinstance(load_js, dict) else ""
                if params_str:
                    try:
                        load_js_params = json.loads(params_str)
                    except Exception:
                        load_js_params = {}

        mission_options: dict[int, dict[str, Any]] = {}
        raw_missions = load_js_params.get("missionData", {}) if isinstance(load_js_params, dict) else {}
        for mid_str, mission_data in raw_missions.items():
            if not isinstance(mission_data, dict):
                continue
            try:
                mid = int(mid_str)
            except (TypeError, ValueError):
                continue
            mission_options[mid] = {
                "name": mission_data.get("name", ""),
                "risk_before": mission_data.get("riskBefore", 0),
                "risk_after": mission_data.get("riskAfter", 0),
                "risk_per_spy": mission_data.get("riskPerSpy", 0),
                "success_chance": mission_data.get("successChance", 0),
                "executable": bool(mission_data.get("executableMission", False)),
            }

        return {
            "spy_id": _extract_hidden_value(html, "spy"),
            "source_city_id": _extract_hidden_value(html, "cityId") or str(source_city_id),
            "target_city_id": _extract_hidden_value(html, "targetCity") or str(target_city_id),
            "island_id": _extract_hidden_value(html, "islandId"),
            "position": position,
            "html": html,
            "missions": mission_options,
        }


class SpyExecuteMissionAction(BaseAction):
    """Execute an internal spy mission from a waiting infiltrated group."""

    def execute(
        self,
        source_city_id: int | str,
        target_city_id: int | str,
        mission_id: int,
        agents: int,
        decoys: int = 0,
        position: int = 19,
        spy_id: int | str | None = None,
        island_id: int | str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        assignment = SpyMissionAssignmentAction(self.client).execute(
            source_city_id=source_city_id,
            target_city_id=target_city_id,
            position=position,
        )
        actual_spy_id = str(spy_id or assignment.get("spy_id") or "").strip()
        actual_island_id = str(assignment.get("island_id") or island_id or source_city_id).strip()
        actual_source_city_id = str(assignment.get("source_city_id") or source_city_id).strip()
        actual_target_city_id = str(assignment.get("target_city_id") or target_city_id).strip()
        if not actual_spy_id:
            raise ActionError("No waiting spy group found for internal mission.", action="executeMission")

        base_params = {
            "action": "Espionage",
            "function": "executeMission",
            "tab": "tabSafehouse",
            "targetCity": actual_target_city_id,
            "cityId": actual_source_city_id,
            "islandId": actual_island_id,
            "spy": actual_spy_id,
            "mission": str(mission_id),
            "payCityId": actual_source_city_id,
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        # Internal spy missions use the same form as the game page. payCityId is
        # required for gold-based decoy missions; without it Ikariam can reject
        # the request as if the target city was being used as the payer.
        base_params[f"spies[{actual_source_city_id}][agents]"] = str(agents)
        base_params[f"spies[{actual_source_city_id}][decoys]"] = str(decoys)
        headers = dict(GAME_AJAX_HEADERS)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        resp = self.client._request("POST", self.client._server_url, data=base_params, headers=headers)
        try:
            data = resp.json()
        except Exception as exc:
            raise ActionError(f"Failed to parse executeMission response: {exc}", action="executeMission")

        for entry in data:
            if isinstance(entry, list) and entry[0] == "updateGlobalData" and isinstance(entry[1], dict):
                action_request = entry[1].get("actionRequest")
                if action_request:
                    self.client._action_request = str(action_request)
                break

        success, message = _parse_feedback_entries(data)
        if not message:
            success = True
            message = f"MissÃ£o {mission_id} executada"
        return {"success": success, "message": message, "spy_id": actual_spy_id}


class SpyRetreatAction(BaseAction):
    """Retreat a waiting infiltrated group."""

    def execute(
        self,
        source_city_id: int | str,
        target_city_id: int | str,
        position: int = 19,
        spy_id: int | str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = {
            "view": "spyMissions",
            "targetCityId": str(target_city_id),
            "retreat": "1",
            "position": str(position),
            "backgroundView": "city",
            "currentCityId": str(source_city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("GET", self.client._server_url, params=params, headers=dict(GAME_AJAX_HEADERS))
        try:
            data = resp.json()
        except Exception:
            data = []

        for entry in data:
            if isinstance(entry, list) and entry[0] == "updateGlobalData" and isinstance(entry[1], dict):
                action_request = entry[1].get("actionRequest")
                if action_request:
                    self.client._action_request = str(action_request)
                break

        success, message = _parse_feedback_entries(data)
        if success or message:
            return {"success": success, "message": message or "Retirada solicitada"}

        if spy_id:
            fallback = {
                "action": "Espionage",
                "function": "abortInvasion",
                "cityId": str(source_city_id),
                "position": str(position),
                "spy": str(spy_id),
                "backgroundView": "city",
                "currentCityId": str(source_city_id),
                "actionRequest": self.client._action_request,
                "ajax": "1",
            }
            fallback_resp = self.client._request("GET", self.client._server_url, params=fallback, headers=dict(GAME_AJAX_HEADERS))
            try:
                fallback_data = fallback_resp.json()
            except Exception:
                fallback_data = []
            success, message = _parse_feedback_entries(fallback_data)
            if not message:
                message = "Retirada solicitada"
            return {"success": success or not bool(fallback_data), "message": message}

        return {"success": True, "message": "Retirada solicitada"}


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
        # Lookahead stops at next row OR end-of-table; covers last report too
        body_pattern = re.compile(
            r'id="tbl_mail(\d+)"[^>]*>([\s\S]{0,15000}?)(?=id="tbl_mail\d+"|id="message\d+"|</tbody>|</table>)',
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

            # Infer mission_id from subject
            report["mission_id"] = _infer_mission_id(report.get("subject", ""))
            report["mission_name"] = _normalise_mission_name(report["mission_id"], report.get("subject", ""))
            report["is_read"] = not report.get("unread", False)

            # Report body
            body_html = body_map.get(report_id, "")
            if body_html:
                # Parse status (second <td> in the status row — skip label "Estado:")
                m_status = re.search(r'class="status"[^>]*>[\s\S]*?<td[^>]*>[^<]*</td>\s*<td[^>]*>([^<]+)</td>', body_html, re.DOTALL)
                status_html = _extract_next_td_html(body_html, r"Estado\s*:")
                report["status"] = _strip_html(status_html) if status_html else (_strip_html(m_status.group(1)) if m_status else "")

                # Parse report content — look for the Relatório row's second <td>
                m_report = re.search(
                    r'[Rr]elat[oó]rio[^<]*</td>\s*<td[^>]*>([\s\S]*?)</td>',
                    body_html,
                )
                if not m_report:
                    m_report = re.search(r'<td[^>]*class="[^"]*report[^"]*"[^>]*>([\s\S]*?)</td>', body_html)
                balanced_report_content = _extract_next_td_html(body_html, r"Relat[oó]rio\s*:")
                if balanced_report_content:
                    report_content = balanced_report_content
                elif m_report:
                    report_content = m_report.group(1)
                else:
                    report_content = ""
                if report_content:
                    report["report_html"] = report_content.strip()
                    report["report_text"] = _strip_html(report_content)
                    lines = _extract_report_text_lines(report_content)
                    if lines:
                        report["report_lines"] = lines

                # Extract INNER tables only (resourcesTable, unitTable etc.)
                inner_tables = re.findall(
                    r'<table[^>]*class="[^"]*(?:resourcesTable|reportTable|unitTable)[^"]*"[^>]*>([\s\S]*?)</table>',
                    body_html, re.DOTALL,
                )
                # Fallback: any inner table with 2 columns and data (not just th rows)
                if not inner_tables:
                    # Look for tables that have actual <td> rows (not just headers)
                    all_tables = re.findall(r'<table[^>]*>([\s\S]*?)</table>', body_html, re.DOTALL)
                    for t in all_tables:
                        # Only include if it has td (data) rows
                        if '<td' in t and 'class="job"' not in t:
                            inner_tables.append(t)
                all_pairs: list[tuple[str, str]] = []
                if inner_tables:
                    for table_content in inner_tables:
                        # Only <td> rows (skip <th> header rows)
                        rows = re.findall(
                            r'<tr[^>]*>\s*<t[dh][^>]*>(.*?)</t[dh]>\s*<t[dh][^>]*>(.*?)</t[dh]>\s*</tr>',
                            table_content, re.DOTALL,
                        )
                        for r in rows:
                            k, v = _cell_text(r[0]), _cell_text(r[1])
                            if k and v and not _is_report_header_pair(k, v):
                                all_pairs.append((k, v))
                if not all_pairs and report_content:
                    rows = re.findall(
                        r'<tr[^>]*>\s*<t[dh][^>]*>(.*?)</t[dh]>\s*<t[dh][^>]*>(.*?)</t[dh]>\s*</tr>',
                        report_content,
                        re.DOTALL,
                    )
                    for r in rows:
                        k, v = _cell_text(r[0]), _cell_text(r[1])
                        if k and v and not _is_report_header_pair(k, v):
                            all_pairs.append((k, v))
                if not all_pairs and report.get("mission_id") == 3:
                    all_pairs = _extract_research_pairs(report.get("report_text", ""))
                if all_pairs:
                    report["data_table"] = all_pairs
                    report["data_json"] = _normalise_report_data(all_pairs)

                detail_payload = _extract_report_details(
                    report.get("mission_id"),
                    report.get("report_text", ""),
                    all_pairs,
                )
                if detail_payload:
                    merged = dict(report.get("data_json") or {})
                    merged.update(detail_payload)
                    report["data_json"] = merged

            if report.get("mission_id") == 1 and not report.get("result_status"):
                report["result_status"] = report.get("status") or "InfiltraÃ§Ã£o"

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
