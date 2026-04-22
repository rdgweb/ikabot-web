from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from math import ceil, floor
from typing import Any
from urllib.parse import urlencode

from game_client.parsers.html_parser import GamePageParser


RESOURCE_ORDER = ("wood", "wine", "marble", "crystal", "sulfur")
RESOURCE_INDEX = {
    "wood": 0,
    "wine": 1,
    "marble": 2,
    "crystal": 3,
    "sulfur": 4,
}
RESOURCE_FORM_KEYS = {
    "wood": "cargo_resource",
    "wine": "cargo_tradegood1",
    "marble": "cargo_tradegood2",
    "crystal": "cargo_tradegood3",
    "sulfur": "cargo_tradegood4",
}
TRANSPORT_CONFIRM_MARGIN_SECONDS = 60
TRANSPORT_LOAD_STEP_BY_PERCENT = {
    100: 5,
    80: 4,
    60: 3,
    40: 2,
    20: 1,
}


def _num(raw: Any, default: int = 0) -> int:
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return int(float(raw))
    token_match = re.search(r"-?[\d.,]+", str(raw))
    if not token_match:
        return default
    token = token_match.group(0)
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


def _extract_current_city_id(html: str) -> str | None:
    match = re.search(r'currentCityId\s*[:=]\s*["\']?(\d+)', html)
    return match.group(1) if match else None


def _extract_ajax_pairs(text: str) -> list[list[Any]]:
    try:
        data = json.loads(text)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _extract_feedbacks(text: str) -> list[dict[str, Any]]:
    feedbacks: list[dict[str, Any]] = []
    for item in _extract_ajax_pairs(text):
        if isinstance(item, list) and len(item) > 1 and item[0] == "provideFeedback":
            payload = item[1]
            if isinstance(payload, list):
                for entry in payload:
                    if isinstance(entry, dict):
                        feedbacks.append(entry)
    return feedbacks


def _extract_update_global(text: str) -> dict[str, Any]:
    for item in _extract_ajax_pairs(text):
        if isinstance(item, list) and len(item) > 1 and item[0] == "updateGlobalData":
            return item[1] if isinstance(item[1], dict) else {}
    return {}


def _extract_city_background(html: str) -> dict[str, Any]:
    match = re.search(
        r'\["updateBackgroundData",(\{.*?\})\],\["updateTemplateData"',
        html,
        re.S,
    )
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except Exception:
        return {}


def _extract_hidden_value(html: str, name: str, default: str = "") -> str:
    match = re.search(
        rf'<input[^>]+type="hidden"[^>]+name="{re.escape(name)}"[^>]+value="([^"]*)"',
        html,
        re.I,
    )
    return match.group(1) if match else default


def _parse_free_transporters(html: str, *, use_freighters: bool = False) -> int:
    key = "freeFreighters" if use_freighters else "freeTransporters"
    match = re.search(r'"{}":\s*"?([\d.]+)"?'.format(key), html)
    if match:
        return _num(match.group(1), default=0)

    menu_match = re.search(
        r'id=\\"js_GlobalMenu_{}\\">(\d+)<'.format(key),
        html,
        re.I,
    )
    if menu_match:
        return _num(menu_match.group(1), default=0)

    if not use_freighters:
        # takeOffer/transport fragments may omit the global menu counters but still
        # expose the slider max for merchant ships as normalTransportersMax.
        max_match = re.search(
            r'name=["\']normalTransportersMax["\'].*?value=["\']([\d.,]+)["\']',
            html,
            re.I | re.S,
        )
        if max_match:
            return _num(max_match.group(1), default=0)
    return 0


def _parse_ship_capacity(html: str, free_transporters: int, *, use_freighters: bool = False) -> int:
    capacity_title = re.search(
        r'title=\\"Capacidade de carga: Barco Mercante: ([\d.,]+), Cargueiro: ([\d.,]+)\\"',
        html,
        re.I,
    )
    if capacity_title:
        merchant_capacity = _num(capacity_title.group(1), default=500)
        freighter_capacity = _num(capacity_title.group(2), default=50000)
        return freighter_capacity if use_freighters else merchant_capacity

    total_capacity_match = re.search(
        r'class=\\"portLoadingIcon js_{}Capacity\\"[^>]*>\+\s*([\d.,]+)<'.format(
            "premiumTransporters" if use_freighters else "transporters"
        ),
        html,
        re.I,
    )
    if total_capacity_match and free_transporters > 0:
        total_capacity = _num(total_capacity_match.group(1), default=0)
        if total_capacity > 0:
            return max(1, int(total_capacity / free_transporters))

    return 50000 if use_freighters else 500


def _normalize_transport_load_percent(raw: Any) -> int:
    value = _num(raw, default=100)
    return value if value in TRANSPORT_LOAD_STEP_BY_PERCENT else 100


def _capacity_step_from_percent(percent: int) -> int:
    return TRANSPORT_LOAD_STEP_BY_PERCENT.get(percent, 5)


def _parse_transport_times(
    html: str,
    total_resources: int,
    *,
    use_freighters: bool = False,
    capacity_percent: int = 100,
) -> dict[str, int]:
    def _flt(pattern: str, fallback: float = 0.0) -> float:
        match = re.search(pattern, html)
        return float(match.group(1)) if match else fallback

    def _int(pattern: str, fallback: int = 0) -> int:
        match = re.search(pattern, html)
        return int(float(match.group(1))) if match else fallback

    transporter_speed = _flt(r"'transporterSpeed': ([\d.]+),", 0.0)
    world_bonus = _flt(r"'worldBonus': ([\d.]+),", 1.0)
    government_bonus = _flt(r"'governmentBonus': ([\d.]+),", 1.0)
    poseidon_effect = _flt(r"'poseidonEffect': ([\d.]+),", 0.0)
    marine_chart_bonus = _flt(r"'marineChartArchiveBonus': ([\d.]+),", 1.0)
    minimum_journey = _int(r"'minimumJourneyDuration': (\d+),", 0)
    distance = _flt(r"'distance': ([\d.]+),", 0.0)
    fleet_journey_time = _int(r"'fleetJourneyTime': (\d+),", 0)
    queue_time_at = _int(r"'queueTime': (\d+),", 0)
    loading_speed = _flt(r"'loadingSpeed': ([\d.]+),", 0.0)

    queue_time = 0 if queue_time_at - time.time() <= 0 else int(queue_time_at - time.time())
    loading_time = int(total_resources / loading_speed) if loading_speed > 0 else 0
    percent = _normalize_transport_load_percent(capacity_percent)
    speed_factor = 1.0 + (-0.835 * percent + 83.5) / 100.0
    fleet_speed = floor(transporter_speed * speed_factor) * world_bonus * government_bonus * (1.0 + poseidon_effect)
    if fleet_speed <= 0:
        travel_time = minimum_journey
    else:
        uncapped = int(ceil(((distance * fleet_journey_time) / fleet_speed) * marine_chart_bonus))
        if use_freighters:
            uncapped *= 20
        travel_time = uncapped if uncapped > minimum_journey else minimum_journey

    return {
        "queue_seconds": queue_time,
        "loading_seconds": loading_time,
        "travel_seconds": travel_time,
        "total_seconds": queue_time + loading_time + travel_time,
    }


def _resource_vector(city_state: dict[str, Any]) -> dict[str, int]:
    available = city_state.get("available_resources") or {}
    return {name: int(available.get(name, 0)) for name in RESOURCE_ORDER}


@dataclass
class CityState:
    city_id: str
    city_name: str
    island_id: str
    available_resources: dict[str, int]
    free_space: dict[str, int]
    port_positions: list[int]


@dataclass
class TransportPlan:
    transport_url: str
    action_request: str
    ship_capacity: int
    effective_ship_capacity: int
    capacity_percent: int
    capacity_step: int
    free_transporters: int
    total_requested: int
    total_dispatched: int
    requested: dict[str, int]
    dispatched: dict[str, int]
    remaining: dict[str, int]
    eta: dict[str, int]
    origin: CityState
    destination: CityState


def fetch_city_state(client, city_id: int | str) -> CityState:
    city_id = str(city_id)
    html = client.session.get(
        client._server_url,
        params={"view": "city", "cityId": city_id},
        timeout=30,
    ).text
    background = _extract_city_background(html)
    current_match = re.search(r'"currentResources"\s*:\s*(\{.*?\})\s*,\s*"maxResources"', html, re.S)
    max_match = re.search(r'"maxResources"\s*:\s*(\{.*?\})\s*,\s*"maxResourcesWithModifier"', html, re.S)
    current = {}
    max_resources = {}
    if current_match:
        try:
            current = json.loads(current_match.group(1))
        except Exception:
            current = {}
    if max_match:
        try:
            max_resources = json.loads(max_match.group(1))
        except Exception:
            max_resources = {}

    available = {
        "wood": _num(current.get("resource")),
        "wine": _num(current.get("1")),
        "marble": _num(current.get("2")),
        "crystal": _num(current.get("3")),
        "sulfur": _num(current.get("4")),
    }
    free_space = {
        "wood": max(0, _num(max_resources.get("resource")) - available["wood"]),
        "wine": max(0, _num(max_resources.get("1")) - available["wine"]),
        "marble": max(0, _num(max_resources.get("2")) - available["marble"]),
        "crystal": max(0, _num(max_resources.get("3")) - available["crystal"]),
        "sulfur": max(0, _num(max_resources.get("4")) - available["sulfur"]),
    }

    ports: list[int] = []
    for idx, building in enumerate(background.get("position") or []):
        if isinstance(building, dict) and str(building.get("building") or "").strip().lower() == "port":
            ports.append(idx)

    return CityState(
        city_id=city_id,
        city_name=str(background.get("name") or city_id),
        island_id=str(background.get("islandId") or ""),
        available_resources=available,
        free_space=free_space,
        port_positions=ports or [1],
    )


def change_current_city(client, city_id: int | str) -> str:
    city_id = str(city_id)
    parser = GamePageParser()
    home = client.session.get(client._server_url, timeout=30).text
    current_city_id = _extract_current_city_id(home) or city_id
    action_request = parser.extract_action_request(home) or client._action_request
    client.session.post(
        client._server_url,
        data={
            "action": "header",
            "function": "changeCurrentCity",
            "actionRequest": action_request,
            "oldView": "city",
            "cityId": city_id,
            "backgroundView": "city",
            "currentCityId": current_city_id,
            "ajax": "1",
        },
        timeout=30,
    )
    root = client.session.get(client._server_url, timeout=30).text
    refreshed = parser.extract_action_request(root) or action_request
    client._action_request = refreshed
    return refreshed


def prepare_transport(
    client,
    *,
    from_city_id: int | str,
    to_city_id: int | str,
    requested: dict[str, int],
    use_freighters: bool = False,
    capacity_percent: int = 100,
) -> TransportPlan:
    from_city_id = str(from_city_id)
    to_city_id = str(to_city_id)
    requested_clean = {name: max(0, int(requested.get(name, 0) or 0)) for name in RESOURCE_ORDER}
    total_requested = sum(requested_clean.values())
    if total_requested <= 0:
        raise ValueError("No resources requested")

    change_current_city(client, from_city_id)

    origin = fetch_city_state(client, from_city_id)
    destination = fetch_city_state(client, to_city_id)
    port_position = origin.port_positions[0]
    query = {
        "view": "transport",
        "destinationCityId": to_city_id,
        "cityId": from_city_id,
        "position": str(port_position),
        "backgroundView": "city",
        "currentCityId": from_city_id,
    }
    transport_url = f"{client._server_url}?{urlencode(query)}"
    transport_html = client.session.get(transport_url, timeout=30).text

    parser = GamePageParser()
    action_request = parser.extract_action_request(transport_html) or client._action_request
    free_transporters = _parse_free_transporters(transport_html, use_freighters=use_freighters)
    ship_capacity = _parse_ship_capacity(
        transport_html,
        free_transporters,
        use_freighters=use_freighters,
    )
    normalized_capacity_percent = _normalize_transport_load_percent(capacity_percent)
    capacity_step = _capacity_step_from_percent(normalized_capacity_percent)
    effective_ship_capacity = max(1, int(ship_capacity * normalized_capacity_percent / 100))
    immediate_capacity = max(0, free_transporters * effective_ship_capacity)

    dispatched: dict[str, int] = {}
    remaining: dict[str, int] = {}
    capacity_left = immediate_capacity
    for name in RESOURCE_ORDER:
        wanted = requested_clean[name]
        possible = min(
            wanted,
            origin.available_resources[name],
            destination.free_space[name],
            capacity_left,
        )
        dispatched[name] = max(0, possible)
        remaining[name] = max(0, wanted - dispatched[name])
        capacity_left -= dispatched[name]

    total_dispatched = sum(dispatched.values())
    eta = _parse_transport_times(
        transport_html,
        max(1, total_dispatched or total_requested),
        use_freighters=use_freighters,
        capacity_percent=normalized_capacity_percent,
    )
    return TransportPlan(
        transport_url=transport_url,
        action_request=action_request,
        ship_capacity=ship_capacity,
        effective_ship_capacity=effective_ship_capacity,
        capacity_percent=normalized_capacity_percent,
        capacity_step=capacity_step,
        free_transporters=free_transporters,
        total_requested=total_requested,
        total_dispatched=total_dispatched,
        requested=requested_clean,
        dispatched=dispatched,
        remaining=remaining,
        eta=eta,
        origin=origin,
        destination=destination,
    )


def submit_transport(client, plan: TransportPlan, *, use_freighters: bool = False) -> dict[str, Any]:
    if plan.total_dispatched <= 0:
        return {"ok": False, "reason": "nothing_dispatchable", "feedbacks": []}

    payload = {
        "action": "transportOperations",
        "function": "loadTransportersWithFreight",
        "destinationCityId": plan.destination.city_id,
        "islandId": plan.destination.island_id,
        "oldView": "",
        "position": "",
        "avatar2Name": "",
        "city2Name": "",
        "type": "",
        "activeTab": "",
        "transportDisplayPrice": "0",
        "capacity": str(plan.capacity_step),
        "max_capacity": str(plan.capacity_step),
        "actionRequest": plan.action_request,
        "ajax": "1",
        "backgroundView": "city",
        "currentCityId": plan.origin.city_id,
    }
    if use_freighters:
        payload["usedFreightersShips"] = str(int(ceil(plan.total_dispatched / max(1, plan.effective_ship_capacity))))
    else:
        payload["transporters"] = str(int(ceil(plan.total_dispatched / max(1, plan.effective_ship_capacity))))
    for name in RESOURCE_ORDER:
        amount = plan.dispatched.get(name, 0)
        if amount > 0:
            payload[RESOURCE_FORM_KEYS[name]] = str(amount)

    response = client.session.post(plan.transport_url, data=payload, timeout=30)
    text = response.text
    feedbacks = _extract_feedbacks(text)
    update_global = _extract_update_global(text)
    new_action_request = str(update_global.get("actionRequest") or "").strip()
    if new_action_request:
        client._action_request = new_action_request

    success = any(
        int(entry.get("type", 0) or 0) == 10 or str(entry.get("locakey") or "") == "TXT_ACTION_SUCCESSFUL"
        for entry in feedbacks
    )
    return {
        "ok": success,
        "feedbacks": feedbacks,
        "response_text": text,
        "new_action_request": new_action_request,
    }


def estimate_incoming_transport_wait_seconds(client, destination_city_id: int | str) -> int | None:
    destination_city_id = str(destination_city_id)
    home = client.session.get(client._server_url, timeout=30).text
    current_city_id = _extract_current_city_id(home)
    if not current_city_id:
        return None
    action_request = GamePageParser().extract_action_request(home) or client._action_request
    response = client.session.post(
        client._server_url,
        params={
            "view": "militaryAdvisor",
            "oldView": "city",
            "oldBackgroundView": "city",
            "backgroundView": "city",
            "currentCityId": current_city_id,
            "actionRequest": action_request,
            "ajax": "1",
        },
        timeout=30,
    )
    try:
        data = json.loads(response.text)
    except Exception:
        return None

    time_now = 0
    movements: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, list) or len(item) < 2:
            continue
        if item[0] == "updateGlobalData" and isinstance(item[1], dict):
            time_now = _num(item[1].get("time"), default=0)
        elif item[0] == "changeView" and isinstance(item[1], list):
            try:
                movements = item[1][2]["viewScriptParams"]["militaryAndFleetMovements"] or []
            except Exception:
                movements = []

    waits: list[int] = []
    for movement in movements:
        target = movement.get("target") or {}
        if str(target.get("cityId") or "") != destination_city_id:
            continue
        mission = str((movement.get("event") or {}).get("missionText") or "").lower()
        if "transport" not in mission:
            continue
        event_time = _num(movement.get("eventTime"), default=0)
        if event_time <= 0 or time_now <= 0:
            continue
        waits.append(max(0, event_time - time_now))
    if not waits:
        return None
    return max(TRANSPORT_CONFIRM_MARGIN_SECONDS, min(waits))


def estimate_next_ship_availability_seconds(client) -> int:
    home = client.session.get(client._server_url, timeout=30).text
    current_city_id = _extract_current_city_id(home)
    if not current_city_id:
        return 0
    action_request = GamePageParser().extract_action_request(home) or client._action_request
    response = client.session.post(
        client._server_url,
        params={
            "view": "militaryAdvisor",
            "oldView": "city",
            "oldBackgroundView": "city",
            "backgroundView": "city",
            "currentCityId": current_city_id,
            "actionRequest": action_request,
            "ajax": "1",
        },
        timeout=30,
    )
    try:
        data = json.loads(response.text)
    except Exception:
        return 0

    time_now = 0
    movements: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, list) or len(item) < 2:
            continue
        if item[0] == "updateGlobalData" and isinstance(item[1], dict):
            time_now = _num(item[1].get("time"), default=0)
        elif item[0] == "changeView" and isinstance(item[1], list):
            try:
                movements = item[1][2]["viewScriptParams"]["militaryAndFleetMovements"] or []
            except Exception:
                movements = []

    waits: list[int] = []
    for movement in movements:
        if not bool(movement.get("isOwnArmyOrFleet")):
            continue
        event_time = _num(movement.get("eventTime"), default=0)
        if event_time > 0 and time_now > 0:
            waits.append(max(0, event_time - time_now))
    if not waits:
        return 0
    return min(waits) + TRANSPORT_CONFIRM_MARGIN_SECONDS


def confirm_arrival(client, *, to_city_id: int | str, baseline: dict[str, int], sent: dict[str, int]) -> dict[str, Any]:
    eta_wait = estimate_incoming_transport_wait_seconds(client, to_city_id)
    if eta_wait:
        return {"confirmed": False, "pending": True, "next_check_seconds": eta_wait}

    destination = fetch_city_state(client, to_city_id)
    current = _resource_vector(destination.__dict__)
    expected = {name: baseline.get(name, 0) + sent.get(name, 0) for name in RESOURCE_ORDER}
    delta = {name: current.get(name, 0) - baseline.get(name, 0) for name in RESOURCE_ORDER}
    exact_confirmed = any(
        sent.get(name, 0) > 0 and current.get(name, 0) >= expected.get(name, 0)
        for name in RESOURCE_ORDER
    )
    delta_confirmed = any(
        sent.get(name, 0) > 0 and delta.get(name, 0) > 0
        for name in RESOURCE_ORDER
    )
    delivered_amount = sum(max(0, delta.get(name, 0)) for name in RESOURCE_ORDER if sent.get(name, 0) > 0)
    sent_total = sum(max(0, int(sent.get(name, 0) or 0)) for name in RESOURCE_ORDER)
    confirmation_mode = "exact_stock" if exact_confirmed else ("delta_stock" if delta_confirmed else "movement_cleared")
    return {
        "confirmed": exact_confirmed or delta_confirmed,
        "pending": False,
        "current": current,
        "expected": expected,
        "delta": delta,
        "delivered_amount": delivered_amount,
        "sent_total": sent_total,
        "confirmation_mode": confirmation_mode,
    }
