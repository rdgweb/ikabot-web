"""History behind the game panel's KPI charts (N-101).

Every account gets a value at the same instants, so the sum over accounts never
misses one: at each instant an account counts with the last snapshot it had up to
then. (The panel used to take "the last 12 snapshots of each account" and add them
by hour: an hour in which only 6 of 10 accounts had a snapshot looked like a 38%
drop, and the whole chart covered about two and a half hours.)
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import timedelta

from django.utils import timezone

from apps.game.models import AccountSnapshotHistory
from apps.game.services.gold import net_gold_income

# period -> (how far back, distance between points)
PERIODS = {
    "24h": (timedelta(hours=24), timedelta(hours=1)),
    "7d": (timedelta(days=7), timedelta(hours=6)),
    "30d": (timedelta(days=30), timedelta(days=1)),
}
DEFAULT_PERIOD = "24h"


def clean_period(raw) -> str:
    value = str(raw or "").strip().lower()
    return value if value in PERIODS else DEFAULT_PERIOD


def _si(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _unit_total(units) -> int:
    """Units of a snapshot: {"Hoplita": 120, ...} as the agent writes it (a list of {"count"} is accepted too).

    The old history read only the list form, which the snapshot never had: the troop and
    ship charts were always zero.
    """
    if isinstance(units, dict):
        return sum(max(_si(count), 0) for count in units.values())
    if isinstance(units, list):
        return sum(max(_si(unit.get("count")), 0) for unit in units if isinstance(unit, dict))
    return 0


def instants(period: str, now=None) -> list:
    """The instants of the chart, oldest first, ending now."""
    span, step = PERIODS[clean_period(period)]
    now = now or timezone.now()
    count = int(span / step)
    return [now - step * index for index in range(count, -1, -1)]


def _metrics(row) -> dict:
    base = row.base_snapshot or {}
    cities = row.cities if isinstance(row.cities, list) else []
    military = row.military if isinstance(row.military, dict) else {}
    totals = {key: sum(_si(city.get(key)) for city in cities if isinstance(city, dict)) for key in ("wood", "wine", "marble", "crystal", "sulfur")}
    return {
        "gold": _si(base.get("gold")),
        "income": net_gold_income(base),
        "cities": len(cities),
        "resources": sum(totals.values()),
        "troops": _unit_total(military.get("troops")),
        "ships": _unit_total(military.get("fleet")),
        **totals,
    }


def build_history_map(game_account_ids: list, period: str = DEFAULT_PERIOD, now=None) -> dict[str, list[dict]]:
    """{game_account_id: [{"captured_at", "gold", "income", ...} for each instant]}, same instants for every account."""
    if not game_account_ids:
        return {}
    points = instants(period, now)
    first, last = points[0], points[-1]
    grouped: dict[str, list[dict]] = {}
    for game_account_id in game_account_ids:
        history = AccountSnapshotHistory.objects.filter(game_account_id=game_account_id)
        # ids and times only: the snapshot columns are large, and only one row per instant is read
        marks = list(
            history.filter(captured_at__gte=first, captured_at__lte=last).order_by("captured_at").values_list("captured_at", "id")
        )
        earlier = history.filter(captured_at__lt=first).order_by("-captured_at").values_list("captured_at", "id").first()
        if earlier:
            marks.insert(0, earlier)
        if not marks:
            continue
        times = [mark[0] for mark in marks]
        chosen = []
        for point in points:
            index = bisect_right(times, point) - 1
            # before the account's first snapshot: its first known value, so the sum does not step
            chosen.append(marks[max(index, 0)][1])
        rows = AccountSnapshotHistory.objects.only("base_snapshot", "cities", "military").in_bulk(set(chosen))
        cache: dict[int, dict] = {}
        bucket = []
        for point, row_id in zip(points, chosen):
            row = rows.get(row_id)
            if row is None:
                continue
            if row_id not in cache:
                cache[row_id] = _metrics(row)
            bucket.append({"captured_at": point.isoformat(), **cache[row_id]})
        if bucket:
            grouped[str(game_account_id)] = bucket
    return grouped
