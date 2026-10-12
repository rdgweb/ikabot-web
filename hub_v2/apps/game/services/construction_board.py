"""What the construction panel says about each plan and each city (N-103).

Pure functions over what the hub already stores: the plan a job carries (steps and the
preview with the cost and time of every level), the levels in the snapshot and the
resource reservations. Nothing here changes a plan, a reservation or a job.
"""

from __future__ import annotations

from collections import Counter

RESOURCE_KEYS = ("wood", "wine", "marble", "crystal", "sulfur")
# the plan and the reservations call crystal "glas"
RESOURCE_ALIASES = {"glas": "crystal", "glass": "crystal", "resource": "wood"}

CITY_WORKING, CITY_WAITING, CITY_QUEUED, CITY_IDLE = "working", "waiting", "queued", "idle"
CITY_STATE_LABELS = {
    CITY_WORKING: "Em obra",
    CITY_WAITING: "Esperando recurso",
    CITY_QUEUED: "Na fila do plano",
    CITY_IDLE: "Ociosa",
}
CITY_STATE_ORDER = {CITY_WAITING: 0, CITY_WORKING: 1, CITY_QUEUED: 2, CITY_IDLE: 3}


def _si(value, default=0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def resource_key(raw) -> str:
    key = str(raw or "").strip().lower()
    return RESOURCE_ALIASES.get(key, key)


def current_level(city_levels: dict | None, position, building_id) -> int:
    """Level a plan step is at now: by position when the plan names one, else the highest of that building."""
    city_levels = city_levels or {}
    by_position = city_levels.get("_pos") or {}
    position = str(position or "")
    if position and position in by_position:
        return _si(by_position[position])
    return _si((city_levels.get("_bid") or {}).get(str(building_id or "")))


def step_remaining(preview_step: dict | None, level_now: int) -> tuple[dict[str, int], int]:
    """(cost, building seconds) of the levels of a step that are still to be built."""
    cost: Counter = Counter()
    seconds = 0
    for row in (preview_step or {}).get("level_rows") or []:
        if not isinstance(row, dict) or _si(row.get("level")) <= level_now:
            continue
        for name, amount in (row.get("costs") or {}).items():
            cost[resource_key(name)] += max(0, _si(amount))
        seconds += max(0, _si(row.get("adjusted_seconds")))
    return {name: cost[name] for name in RESOURCE_KEYS if cost[name] > 0}, seconds


def plan_overview(inputs: dict, levels_by_city: dict | None) -> dict:
    """Where a construction plan stands, from its inputs and the levels in the snapshot.

    Cities build in parallel and each city builds one thing at a time, so the building
    time left is that of the slowest city, not the sum. It does not count the time
    spent waiting for resources.
    """
    levels_by_city = levels_by_city or {}
    steps = [step for step in (inputs.get("construction_plan_json") or []) if isinstance(step, dict)]
    previews = [step for step in (inputs.get("construction_plan_steps") or []) if isinstance(step, dict)]
    by_index = {_si(preview.get("index"), -1): preview for preview in previews}
    aligned = len(previews) == len(steps)

    pending: list[dict] = []
    cities: dict[str, dict] = {}
    total_cost: Counter = Counter()
    done = 0
    remaining_levels = 0
    for position, step in enumerate(steps):
        city_id = str(step.get("city_id") or "")
        building = str(step.get("building_id") or step.get("building_type") or "")
        target = _si(step.get("target_level"))
        level = current_level(levels_by_city.get(city_id), step.get("building_position"), building)
        city = cities.setdefault(city_id, {
            "city_id": city_id, "city_name": str(step.get("city_name") or city_id),
            "pending": [], "seconds": 0, "cost": Counter(), "steps": 0, "done": 0,
        })
        city["steps"] += 1
        if target and level >= target:
            done += 1
            city["done"] += 1
            continue
        preview = previews[position] if aligned else by_index.get(_si(step.get("index"), -2))
        cost, seconds = step_remaining(preview, level)
        entry = {
            "idx": position, "city_id": city_id, "city_name": city["city_name"], "building": building,
            "cur": level, "target": target, "priority": bool(step.get("priority")),
            "new": str(step.get("mode") or "") == "new" and level == 0,
            "cost": cost, "seconds": seconds,
        }
        pending.append(entry)
        remaining_levels += max(0, target - level)
        city["pending"].append(entry)
        city["seconds"] += seconds
        city["cost"].update(cost)
        total_cost.update(cost)

    for city in cities.values():
        # what the plan builds first in the city comes first: the prioritized step, then plan order
        city["pending"].sort(key=lambda entry: (not entry["priority"], entry["idx"]))
        city["cost"] = {name: city["cost"][name] for name in RESOURCE_KEYS if city["cost"][name] > 0}
    total = len(steps)
    return {
        "total": total, "done": done, "pct": round(done / total * 100) if total else 0,
        "remaining_levels": remaining_levels,
        "pending": pending,
        "cities": cities,
        "remaining_cost": {name: total_cost[name] for name in RESOURCE_KEYS if total_cost[name] > 0},
        "build_seconds": max((city["seconds"] for city in cities.values()), default=0),
    }


def city_state(*, working: bool, has_pending: bool, has_shortfall: bool) -> str:
    if working:
        return CITY_WORKING
    if has_pending:
        return CITY_WAITING if has_shortfall else CITY_QUEUED
    return CITY_IDLE


def shortfalls_by_city(reservations) -> dict[tuple[str, str], dict[str, int]]:
    """{(game account id, city id): {resource: what is missing}} from the active reservations."""
    missing: dict[tuple[str, str], Counter] = {}
    for reservation in reservations:
        amount = _si(reservation.shortfall_amount)
        if amount <= 0:
            continue
        key = (str(reservation.game_account_id), str(reservation.city_id))
        missing.setdefault(key, Counter())[resource_key(reservation.resource)] += amount
    return {key: {name: bucket[name] for name in RESOURCE_KEYS if bucket[name] > 0} for key, bucket in missing.items()}


def account_counts(cities: list[dict]) -> dict:
    counts = Counter(city["state"] for city in cities)
    return {
        "total": len(cities),
        "working": counts[CITY_WORKING], "waiting": counts[CITY_WAITING],
        "queued": counts[CITY_QUEUED], "idle": counts[CITY_IDLE],
        "free_slots": sum(city.get("free_slots", 0) for city in cities),
    }
