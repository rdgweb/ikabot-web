from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from game_client.parsers.html_parser import GamePageParser

def _num(raw: Any, default: int = 0) -> int:
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return int(float(raw))
    match = re.search(r"-?[\d.,]+", str(raw))
    if not match:
        return default
    token = match.group(0)
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "." in token:
        parts = token.split(".")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            token = "".join(parts)
    elif "," in token:
        parts = token.split(",")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            token = "".join(parts)
        else:
            token = token.replace(",", ".")
    try:
        return int(float(token))
    except Exception:
        return default


def find_tavern_position(city: dict[str, Any]) -> int:
    buildings = city.get("buildings")
    if isinstance(buildings, list):
        for building in buildings:
            if not isinstance(building, dict):
                continue
            if str(building.get("building") or "").strip().lower() == "tavern":
                return int(building.get("position", -1))

    positions = city.get("position")
    if isinstance(positions, list):
        for idx, building in enumerate(positions):
            if not isinstance(building, dict):
                continue
            if str(building.get("building") or "").strip().lower() == "tavern":
                return idx
    return -1


def _parse_option_costs(tavern_html: str) -> dict[int, int]:
    costs: dict[int, int] = {}
    for raw_amount, raw_cost in re.findall(
        r'<option[^>]*value=\\"(\d+)\\"[^>]*>\s*([\d.,]+)\s+Vinho por hora',
        tavern_html,
        re.I,
    ):
        costs[int(raw_amount)] = _num(raw_cost, 0)
    if costs:
        return costs

    for raw_amount, raw_cost in re.findall(
        r'<option[^>]*value="(\d+)"[^>]*>\s*([\d.,]+)\s+Vinho por hora',
        tavern_html,
        re.I,
    ):
        costs[int(raw_amount)] = _num(raw_cost, 0)
    return costs


@dataclass
class TavernState:
    current_amount: int
    max_amount: int
    action_request: str
    wine_spendings_per_hour: int
    happiness_bonus: int
    option_costs: dict[int, int]
    raw_html: str


@dataclass
class TownHallState:
    total_happiness: int
    happiness_text: str
    growth_per_hour: float
    occupied_space: int
    max_inhabitants: int
    action_points_available: int
    action_points_max: int
    breakdown: dict[str, int]
    raw_html: str


def parse_tavern_state(tavern_html: str) -> TavernState:
    current = 0
    selected = re.search(
        r'<select[^>]*name=\\"amount\\"[^>]*>.*?<option[^>]*value=\\"(\d+)\\"[^>]*selected',
        tavern_html,
        re.S | re.I,
    )
    if not selected:
        selected = re.search(
            r'<select[^>]*name="amount"[^>]*>.*?<option[^>]*value="(\d+)"[^>]*selected',
            tavern_html,
            re.S | re.I,
        )
    if selected:
        current = int(selected.group(1))

    slider_match = re.search(
        r'sliderbg_wine\\" class=\\"sliderbg\\" title=\\"slider value = (\d+)\\"',
        tavern_html,
        re.I,
    )
    if not slider_match:
        slider_match = re.search(
            r'sliderbg_wine" class="sliderbg" title="slider value = (\d+)"',
            tavern_html,
            re.I,
        )
    if slider_match:
        current = int(slider_match.group(1))

    option_costs = _parse_option_costs(tavern_html)
    max_amount = max(option_costs) if option_costs else 0
    max_match = re.search(r"setMax.*?setActualValue\((\d+)\)", tavern_html, re.S)
    if max_match:
        max_amount = max(max_amount, int(max_match.group(1)))

    action_request = ""
    action_match = re.search(r'actionRequest:\s*\\"([^\\"]+)\\"', tavern_html)
    if not action_match:
        action_match = re.search(r'actionRequest:\s*"([^"]+)"', tavern_html)
    if action_match:
        action_request = str(action_match.group(1))

    spendings = 0
    spendings_match = re.search(r"wineSpendings:\s*([-\d.,]+)", tavern_html)
    if spendings_match:
        spendings = abs(_num(spendings_match.group(1), 0))

    happiness_bonus = 0
    bonus_match = re.search(r'id=\\"bonus\\"[^>]*title=\\"(\d+)\\"', tavern_html)
    if not bonus_match:
        bonus_match = re.search(r'id="bonus"[^>]*title="(\d+)"', tavern_html)
    if bonus_match:
        happiness_bonus = _num(bonus_match.group(1), 0)

    if current <= 0 and spendings > 0 and option_costs:
        exact = [amount for amount, cost in option_costs.items() if cost == spendings]
        if exact:
            current = min(exact)
        else:
            closest = min(
                option_costs.items(),
                key=lambda item: abs(item[1] - spendings),
            )
            current = int(closest[0])

    return TavernState(
        current_amount=max(0, current),
        max_amount=max(0, max_amount),
        action_request=action_request,
        wine_spendings_per_hour=max(0, spendings),
        happiness_bonus=max(0, happiness_bonus),
        option_costs=option_costs,
        raw_html=tavern_html,
    )


def open_tavern_page(client, city_id: int | str, position: int) -> TavernState:
    city_id = str(city_id)
    html = client.session.get(
        client._server_url,
        params={
            "view": "tavern",
            "cityId": city_id,
            "position": int(position),
            "currentCityId": city_id,
            "backgroundView": "city",
            "actionRequest": client._action_request,
        },
        timeout=30,
    ).text
    state = parse_tavern_state(html)
    if state.action_request:
        client._action_request = state.action_request
    return state


def set_tavern_service(client, *, city_id: int | str, position: int, desired_amount: int) -> dict[str, Any]:
    initial = open_tavern_page(client, city_id, position)
    if initial.max_amount <= 0 or not initial.action_request:
        return {
            "ok": False,
            "reason": "missing_state",
            "state": initial,
        }

    target = max(0, min(int(desired_amount), initial.max_amount))
    if target == initial.current_amount:
        return {
            "ok": True,
            "changed": False,
            "state": initial,
            "target": target,
        }

    response = client.session.post(
        client._server_url,
        data={
            "action": "CityScreen",
            "function": "assignWinePerTick",
            "cityId": str(city_id),
            "position": int(position),
            "amount": target,
            "actionRequest": initial.action_request,
            "ajax": "1",
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "tavern",
        },
        timeout=30,
    )
    if initial.action_request:
        client._action_request = initial.action_request

    verified = open_tavern_page(client, city_id, position)
    return {
        "ok": True,
        "changed": verified.current_amount != initial.current_amount,
        "state": verified,
        "previous": initial,
        "target": target,
    }


def find_townhall_position(city: dict[str, Any]) -> int:
    buildings = city.get("buildings")
    if isinstance(buildings, list):
        for building in buildings:
            if not isinstance(building, dict):
                continue
            if str(building.get("building") or "").strip().lower() == "townhall":
                return int(building.get("position", 0))
    positions = city.get("position")
    if isinstance(positions, list):
        for idx, building in enumerate(positions):
            if not isinstance(building, dict):
                continue
            if str(building.get("building") or "").strip().lower() == "townhall":
                return idx
    return 0


def _decode_number(text: str) -> int:
    cleaned = (text or "").replace("+", "").replace("&nbsp;", " ").strip()
    return _num(cleaned, 0)


def parse_townhall_state(html: str) -> TownHallState:
    total_happiness = 0
    happiness_text = ""
    growth_per_hour = 0.0
    occupied_space = 0
    max_inhabitants = 0
    action_points_available = 0
    action_points_max = 0

    total_match = re.search(
        r'id=\\"js_TownHallHappinessLargeValue\\"[^>]*>\s*([+\-\d.,]+)\s*<',
        html,
        re.I,
    )
    if not total_match:
        total_match = re.search(
            r'id="js_TownHallHappinessLargeValue"[^>]*>\s*([+\-\d.,]+)\s*<',
            html,
            re.I,
        )
    if total_match:
        total_happiness = _decode_number(total_match.group(1))

    text_match = re.search(
        r'id=\\"js_TownHallHappinessLargeText\\"[^>]*>\s*([^<]+)\s*<',
        html,
        re.I,
    )
    if not text_match:
        text_match = re.search(
            r'id="js_TownHallHappinessLargeText"[^>]*>\s*([^<]+)\s*<',
            html,
            re.I,
        )
    if text_match:
        happiness_text = text_match.group(1).strip()

    growth_match = re.search(
        r'id=\\"js_TownHallPopulationGrowthValue\\"[^>]*>\s*([+\-\d.,]+)\s*<',
        html,
        re.I,
    )
    if not growth_match:
        growth_match = re.search(
            r'id="js_TownHallPopulationGrowthValue"[^>]*>\s*([+\-\d.,]+)\s*<',
            html,
            re.I,
        )
    if growth_match:
        token = growth_match.group(1).replace(".", "").replace(",", ".").strip()
        try:
            growth_per_hour = float(token)
        except Exception:
            growth_per_hour = 0.0

    occupied_match = re.search(
        r'id=\\"js_TownHallOccupiedSpace\\"[^>]*>\s*([+\-\d.,]+)\s*<',
        html,
        re.I,
    )
    if not occupied_match:
        occupied_match = re.search(
            r'id="js_TownHallOccupiedSpace"[^>]*>\s*([+\-\d.,]+)\s*<',
            html,
            re.I,
        )
    if occupied_match:
        occupied_space = _decode_number(occupied_match.group(1))

    max_inhabitants_match = re.search(
        r'id=\\"js_TownHallMaxInhabitants\\"[^>]*>\s*([+\-\d.,]+)\s*<',
        html,
        re.I,
    )
    if not max_inhabitants_match:
        max_inhabitants_match = re.search(
            r'id="js_TownHallMaxInhabitants"[^>]*>\s*([+\-\d.,]+)\s*<',
            html,
            re.I,
        )
    if max_inhabitants_match:
        max_inhabitants = _decode_number(max_inhabitants_match.group(1))

    ap_available_match = re.search(
        r'id=\\"js_TownHallActionPointsAvailable\\"[^>]*>\s*([+\-\d.,]+)\s*<',
        html,
        re.I,
    )
    if not ap_available_match:
        ap_available_match = re.search(
            r'id="js_TownHallActionPointsAvailable"[^>]*>\s*([+\-\d.,]+)\s*<',
            html,
            re.I,
        )
    if ap_available_match:
        action_points_available = _decode_number(ap_available_match.group(1))

    ap_max_match = re.search(
        r'id=\\"js_TownHallMaxActionPointsAvailable\\"[^>]*>\s*([+\-\d.,]+)\s*<',
        html,
        re.I,
    )
    if not ap_max_match:
        ap_max_match = re.search(
            r'id="js_TownHallMaxActionPointsAvailable"[^>]*>\s*([+\-\d.,]+)\s*<',
            html,
            re.I,
        )
    if ap_max_match:
        action_points_max = _decode_number(ap_max_match.group(1))

    breakdown: dict[str, int] = {}
    for key, raw in re.findall(
        r'id=\\"(js_TownHallSatisfactionOverview[^\\"]+Value)\\"[^>]*>\s*([+\-\d.,]+)\s*<',
        html,
        re.I,
    ):
        breakdown[key] = _decode_number(raw)
    if not breakdown:
        for key, raw in re.findall(
            r'id="(js_TownHallSatisfactionOverview[^"]+Value)"[^>]*>\s*([+\-\d.,]+)\s*<',
            html,
            re.I,
        ):
            breakdown[key] = _decode_number(raw)

    return TownHallState(
        total_happiness=total_happiness,
        happiness_text=happiness_text,
        growth_per_hour=growth_per_hour,
        occupied_space=occupied_space,
        max_inhabitants=max_inhabitants,
        action_points_available=action_points_available,
        action_points_max=action_points_max,
        breakdown=breakdown,
        raw_html=html,
    )


def open_townhall_page(client, city_id: int | str, position: int) -> TownHallState:
    city_id = str(city_id)
    html = client.session.get(
        client._server_url,
        params={
            "view": "townHall",
            "cityId": city_id,
            "position": int(position),
            "currentCityId": city_id,
            "backgroundView": "city",
            "actionRequest": client._action_request,
        },
        timeout=30,
    ).text
    parser = GamePageParser()
    action_request = parser.extract_action_request(html) or client._action_request
    if action_request:
        client._action_request = action_request
    return parse_townhall_state(html)
