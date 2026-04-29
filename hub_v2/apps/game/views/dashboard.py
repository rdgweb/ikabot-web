"""
View: Game dashboard — overview of all game accounts with snapshot KPIs.

Shows real game data from AccountSnapshot: cities, resources, buildings,
production rates, gold, transporters, military, and resource projections.
"""

from datetime import datetime
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, Prefetch, Window
from django.db.models.functions import RowNumber
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshotHistory
from apps.game.services.dashboard_cache import get_dashboard_cache_key

_DASHBOARD_CACHE_TTL = 60  # seconds


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "game/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cache_key = get_dashboard_cache_key(self.request.user.pk)
        cached = cache.get(cache_key)
        if cached is not None:
            ctx.update(cached)
            ctx["now_epoch"] = int(timezone.now().timestamp())
            return ctx

        accounts = (
            Account.objects
            .filter(active=True)
            .select_related("node")
            .prefetch_related(
                Prefetch(
                    "game_accounts",
                    queryset=GameAccount.objects.filter(active=True).select_related("snapshot"),
                    to_attr="active_game_accounts",
                ),
            )
            .order_by("label")
        )

        total_gold = 0
        total_cities = 0
        total_resources = 0
        total_troops = 0
        total_ships = 0
        total_income = 0
        account_cards = []
        resource_modal_data = []
        city_options: set[str] = set()

        for acct in accounts:
            game_accounts = getattr(acct, "active_game_accounts", [])
            for ga in game_accounts:
                snapshot = getattr(ga, "snapshot", None)
                base = snapshot.base_snapshot if snapshot else {}
                raw_cities = snapshot.cities if snapshot else []
                military = snapshot.military if snapshot else {}

                if isinstance(raw_cities, dict):
                    city_list = raw_cities.get("cities", [])
                elif isinstance(raw_cities, list):
                    city_list = raw_cities
                else:
                    city_list = []

                # --- Cities ---
                enriched_cities = []
                acct_resources = 0
                totals = {"wood": 0, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0}
                total_buildings = 0
                for city in city_list:
                    if not isinstance(city, dict):
                        continue
                    city = _annotate_building_remaining(city)
                    wood = _si(city.get("wood"))
                    wine = _si(city.get("wine"))
                    marble = _si(city.get("marble"))
                    crystal = _si(city.get("crystal"))
                    sulfur = _si(city.get("sulfur"))
                    city_total = wood + wine + marble + crystal + sulfur
                    acct_resources += city_total
                    cap = _si(city.get("warehouse_capacity"))
                    wine_cons = _si(city.get("wine_consumption"))
                    wine_cons_gross = _si(city.get("wine_consumption_gross"))
                    wine_savings = _si(city.get("wine_savings"))

                    for k in ("wood", "wine", "marble", "crystal", "sulfur"):
                        totals[k] += locals()[k]

                    buildings = city.get("buildings", [])
                    building_count = sum(
                        1 for b in buildings
                        if isinstance(b, dict) and b.get("building") and b["building"] != "empty"
                    )
                    upgrading_count = sum(
                        1 for b in buildings
                        if isinstance(b, dict) and b.get("is_upgrading")
                    )
                    total_buildings += building_count
                    city_construction_end_at = 0
                    for b in buildings:
                        if isinstance(b, dict) and b.get("is_upgrading"):
                            _end = _si(b.get("construction_end_at"), 0)
                            if _end > city_construction_end_at:
                                city_construction_end_at = _end

                    # Resource projection: hours until wine runs out
                    # wine_cons from city HTML may be 0; we compute per-city share below
                    wine_hours = None

                    cap_pct = round(city_total / cap * 100) if cap > 0 else 0

                    # Hours to fill warehouse (approximate: remaining space / avg production)
                    fill_hours = None
                    remaining_cap = cap - city_total if cap > 0 else 0
                    # We'll compute this after we know total production

                    enriched_cities.append({
                        **city,
                        "wood": wood, "wine": wine, "marble": marble,
                        "crystal": crystal, "sulfur": sulfur,
                        "resource_total": city_total,
                        "building_count": building_count,
                        "upgrading_count": upgrading_count,
                        "warehouse_capacity": cap,
                        "remaining_cap": remaining_cap,
                        "cap_pct": cap_pct,
                        "wine_consumption": wine_cons,
                        "wine_consumption_gross": wine_cons_gross,
                        "wine_savings": wine_savings,
                        "wine_hours": wine_hours,
                        "population": _si(city.get("population")),
                        "construction_end_at": city_construction_end_at,
                    })
                    city_name = str(city.get("name") or "").strip()
                    if city_name:
                        city_options.add(city_name)

                # --- Resource projections ---
                # Production rates from Ikariam API are per-second; convert to per-hour
                res_prod = round(_sf(base.get("resource_production")) * 3600)
                tg_prod = round(_sf(base.get("tradegood_production")) * 3600)
                city_wine_spend = sum(_si(city.get("wine_consumption")) for city in enriched_cities)
                wine_spend = city_wine_spend or _si(base.get("wine_spendings"))  # already per-hour

                resource_projections = _build_city_resource_projections(
                    enriched_cities,
                    wood_production_per_hour=res_prod,
                    tradegood_production_per_hour=tg_prod,
                    global_wine_spendings=wine_spend,
                    snapshot_updated_at=snapshot.updated_at if snapshot else None,
                )

                # Compute wine projection using actual per-city net rates
                # (only wine-producing cities contribute production; all consume)
                net_wine_rate = 0.0
                for city_proj in resource_projections.values():
                    for res in city_proj.get("resources", []):
                        if res.get("key") == "wine":
                            net_wine_rate += res.get("net_hour", 0)
                            break
                net_wine_rate = round(net_wine_rate, 1)

                total_wine_stock = totals["wine"]
                total_wine_hours = None
                if wine_spend > 0 and total_wine_stock > 0:
                    if net_wine_rate < 0:
                        total_wine_hours = int(round(total_wine_stock / abs(net_wine_rate)))

                for ec in enriched_cities:
                    city_projection = resource_projections.get(_si(ec.get("id"), default=-1), {})
                    ec["resource_projections"] = city_projection.get("resources", [])
                    ec["cap_resource"] = city_projection.get("cap_resource")
                    ec["cap_pct"] = city_projection.get("cap_pct", ec.get("cap_pct", 0))
                    ec["fill_hours"] = city_projection.get("fill_hours")

                    # Wine hours for this city: use per-city wine net rate
                    wine_res = None
                    for res in ec["resource_projections"]:
                        if res.get("key") == "wine":
                            wine_res = res
                            break
                    city_wine_net = wine_res.get("net_hour", 0) if wine_res else 0
                    if city_wine_net < 0 and ec["wine"] > 0:
                        ec["wine_hours"] = round(ec["wine"] / abs(city_wine_net), 1)
                    else:
                        ec["wine_hours"] = None

                near_full_count = sum(1 for city in enriched_cities if _si(city.get("cap_pct")) >= 85)
                wine_risk_count = sum(
                    1 for city in enriched_cities
                    if city.get("wine_hours") is not None and float(city.get("wine_hours") or 0) < 72
                )

                # --- Military ---
                troops_list = []
                ships_list = []
                military_by_city = {}
                troop_total = 0
                ship_total = 0
                if isinstance(military, dict):
                    for name, count in military.get("troops", {}).items():
                        c = _si(count)
                        if c > 0:
                            troops_list.append({"name": name, "count": c})
                            troop_total += c
                    for name, count in military.get("fleet", {}).items():
                        c = _si(count)
                        if c > 0:
                            ships_list.append({"name": name, "count": c})
                            ship_total += c
                    for city_military in military.get("by_city", []):
                        if not isinstance(city_military, dict):
                            continue
                        city_id = _si(city_military.get("city_id"), default=None)
                        if city_id is None:
                            continue
                        city_troops = []
                        city_fleet = []
                        for name, count in (city_military.get("troops") or {}).items():
                            c = _si(count)
                            if c > 0:
                                city_troops.append({"name": name, "count": c})
                        for name, count in (city_military.get("fleet") or {}).items():
                            c = _si(count)
                            if c > 0:
                                city_fleet.append({"name": name, "count": c})
                        military_by_city[city_id] = {
                            "troops": city_troops,
                            "fleet": city_fleet,
                            "troop_total": sum(unit["count"] for unit in city_troops),
                            "fleet_total": sum(unit["count"] for unit in city_fleet),
                            "occupation_forces": city_military.get("occupation_forces") or [],
                            "blockade_forces": city_military.get("blockade_forces") or [],
                        }

                for ec in enriched_cities:
                    city_military = military_by_city.get(_si(ec.get("id"), default=-1), {})
                    ec["troops"] = city_military.get("troops", [])
                    ec["fleet"] = city_military.get("fleet", [])
                    ec["troop_total"] = city_military.get("troop_total", 0)
                    ec["fleet_total"] = city_military.get("fleet_total", 0)
                    ec["troop_map"] = {unit["name"]: unit["count"] for unit in ec["troops"]}
                    ec["fleet_map"] = {unit["name"]: unit["count"] for unit in ec["fleet"]}
                    ec["occupation_forces"] = city_military.get("occupation_forces", [])
                    ec["blockade_forces"] = city_military.get("blockade_forces", [])

                troop_columns = _build_military_columns(enriched_cities, "troop")
                fleet_columns = _build_military_columns(enriched_cities, "fleet")

                for ec in enriched_cities:
                    ec["troop_counts"] = [ec["troop_map"].get(name, 0) for name in troop_columns]
                    ec["fleet_counts"] = [ec["fleet_map"].get(name, 0) for name in fleet_columns]

                # --- KPIs ---
                acct_gold = _si(base.get("gold"))
                gross_income = _si(base.get("income"))
                upkeep = _si(base.get("upkeep"))
                scientists_upkeep = _si(base.get("scientists_upkeep"))
                # upkeep and scientists_upkeep are already negative in the Ikariam API
                # Net income = sum of all signed values
                acct_income = gross_income + upkeep + scientists_upkeep
                acct_city_count = len(enriched_cities)

                total_gold += acct_gold
                total_income += acct_income
                total_cities += acct_city_count
                total_resources += acct_resources
                total_troops += troop_total
                total_ships += ship_total

                account_cards.append({
                    "account": acct,
                    "game_account": ga,
                    "game_account_id": str(ga.pk),
                    "snapshot": snapshot,
                    "base": base,
                    "gold": acct_gold,
                    "income": acct_income,
                    "gross_income": gross_income,
                    "upkeep": upkeep,
                    "scientists_upkeep": scientists_upkeep,
                    "free_transporters": _si(base.get("free_transporters")),
                    "max_transporters": _si(base.get("max_transporters")),
                    "resource_production": res_prod,
                    "tradegood_production": tg_prod,
                    "wine_spendings": wine_spend,
                    "net_wine_rate": net_wine_rate,
                    "total_wine_hours": total_wine_hours,
                    "player_name": ga.name or base.get("player_name", ""),
                    "server_id": ga.server_id,
                    "city_count": acct_city_count,
                    "resources": acct_resources,
                    "cities": enriched_cities,
                    "totals": totals,
                    "total_buildings": total_buildings,
                    "troops": troops_list,
                    "ships": ships_list,
                    "troop_total": troop_total,
                    "ship_total": ship_total,
                    "troop_columns": [
                        {"name": name, "total": sum(city["troop_map"].get(name, 0) for city in enriched_cities)}
                        for name in troop_columns
                    ],
                    "fleet_columns": [
                        {"name": name, "total": sum(city["fleet_map"].get(name, 0) for city in enriched_cities)}
                        for name in fleet_columns
                    ],
                    "is_online": acct.node.is_online if acct.node else False,
                    "snapshot_epoch": int(snapshot.updated_at.timestamp()) if snapshot else None,
                    "city_names": [
                        str(city.get("name") or "").strip()
                        for city in enriched_cities
                        if str(city.get("name") or "").strip()
                    ],
                })
                resource_modal_data.append({
                    "node_id": str(acct.node_id or ""),
                    "account_id": str(acct.pk),
                    "game_account_id": str(ga.pk),
                    "player": ga.name or base.get("player_name", ""),
                    "server_id": ga.server_id,
                    "server_label": ga.server_id,
                    "resources": acct_resources,
                    "totals": totals,
                    "city_count": acct_city_count,
                    "wine_spendings": wine_spend,
                    "net_wine_rate": net_wine_rate,
                    "total_wine_hours": total_wine_hours,
                    "near_full_count": near_full_count,
                    "wine_risk_count": wine_risk_count,
                    "city_names": [
                        str(city.get("name") or "").strip()
                        for city in enriched_cities
                        if str(city.get("name") or "").strip()
                    ],
                    "cities": [
                        {
                            "name": str(city.get("name") or "").strip(),
                            "resource_total": _si(city.get("resource_total")),
                            "cap_pct": _si(city.get("cap_pct")),
                            "fill_hours": city.get("fill_hours"),
                            "wine_hours": city.get("wine_hours"),
                            "wine": _si(city.get("wine")),
                            "produced_tradegood": _si(city.get("produced_tradegood") or city.get("tradegood")),
                            "x": _si(city.get("x"), default=0),
                            "y": _si(city.get("y"), default=0),
                        }
                        for city in enriched_cities
                    ],
                })

        account_detail_data = []
        for card in account_cards:
            account_detail_data.append({
                "node_id": str(card["account"].node_id or ""),
                "account_id": str(card["account"].pk),
                "game_account_id": str(card["game_account"].pk),
                "player": card["player_name"],
                "server_id": card["server_id"],
                "server_label": card["server_id"],
                "city_count": card["city_count"],
                "troop_total": card["troop_total"],
                "ship_total": card["ship_total"],
                "resources": card["resources"],
                "free_transporters": card["free_transporters"],
                "max_transporters": card["max_transporters"],
                "total_wine_hours": card["total_wine_hours"],
                "wine_spendings": card["wine_spendings"],
                "troop_columns": card["troop_columns"],
                "fleet_columns": card["fleet_columns"],
                "city_names": card["city_names"],
                "cities": [
                    {
                        "name": str(city.get("name") or "").strip(),
                        "x": _si(city.get("x"), default=0),
                        "y": _si(city.get("y"), default=0),
                        "resource_total": _si(city.get("resource_total")),
                        "cap_pct": _si(city.get("cap_pct")),
                        "wine_hours": city.get("wine_hours"),
                        "troop_total": _si(city.get("troop_total")),
                        "fleet_total": _si(city.get("fleet_total")),
                        "produced_tradegood": _si(city.get("produced_tradegood") or city.get("tradegood")),
                    }
                    for city in card["cities"]
                ],
            })

        ctx["account_cards"] = account_cards
        ctx["city_options"] = sorted(city_options, key=str.lower)
        ctx["kpi_history"] = {}
        ctx["resource_modal_data"] = resource_modal_data
        ctx["account_detail_data"] = account_detail_data
        ctx["now_epoch"] = int(timezone.now().timestamp())
        ctx["kpi"] = {
            "gold": total_gold,
            "income": total_income,
            "cities": total_cities,
            "resources": total_resources,
            "troops": total_troops,
            "ships": total_ships,
        }

        # Filter options for dropdowns — evaluate querysets before caching
        nodes = list(Node.objects.filter(active=True).order_by("name"))
        accounts_list = list(accounts)  # already evaluated by the loop above
        ctx["nodes"] = nodes
        ctx["accounts"] = accounts_list

        cache_payload = {
            "account_cards": account_cards,
            "city_options": ctx["city_options"],
            "kpi_history": {},
            "resource_modal_data": resource_modal_data,
            "account_detail_data": account_detail_data,
            "now_epoch": ctx["now_epoch"],
            "kpi": ctx["kpi"],
            "nodes": nodes,
            "accounts": accounts_list,
        }
        cache.set(cache_key, cache_payload, _DASHBOARD_CACHE_TTL)

        return ctx


class DashboardHistoryView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        cache_key = f"{get_dashboard_cache_key(request.user.pk)}:history"
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse({"history": cached})

        game_account_ids = list(
            GameAccount.objects.filter(active=True, account__active=True, snapshot__isnull=False)
            .values_list("pk", flat=True)
        )
        history_map = _build_history_map(game_account_ids)
        cache.set(cache_key, history_map, _DASHBOARD_CACHE_TTL)
        return JsonResponse({"history": history_map})


def _si(value, default=0) -> int:
    """Safely convert a value to int (handles None, float, string)."""
    if value is None:
        return default
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _sf(value, default=0.0) -> float:
    """Safely convert a value to float."""
    if value is None:
        return default
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _build_military_columns(cities: list[dict], key: str) -> list[str]:
    """Build a stable ordered list of military unit names for table columns."""
    totals: dict[str, int] = {}
    field = "troop_map" if key == "troop" else "fleet_map"
    for city in cities:
        for name, count in city.get(field, {}).items():
            totals[name] = totals.get(name, 0) + count
    return [
        name for name, _ in sorted(
            totals.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def _duration_human(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours < 24:
        return f"{hours}h {minutes}m"
    days = hours // 24
    return f"{days}d {hours % 24}h"


def _annotate_building_remaining(city: dict[str, Any]) -> dict[str, Any]:
    now_ts = int(timezone.now().timestamp())
    buildings = city.get("buildings") or []
    if not isinstance(buildings, list):
        return city

    out: list[dict[str, Any]] = []
    for item in buildings:
        if not isinstance(item, dict):
            out.append(item)
            continue
        current = dict(item)
        end_at = _si(current.get("construction_end_at"), 0)
        if bool(current.get("is_upgrading")) and end_at > now_ts:
            remaining = max(0, end_at - now_ts)
            current["construction_remaining_seconds"] = remaining
            current["construction_remaining_human"] = _duration_human(remaining)
        out.append(current)

    city = dict(city)
    city["buildings"] = out
    return city


# Ikariam numbering: tradegood IDs and storage keys use the SAME mapping:
#   0/"resource"=wood, 1/"1"=wine, 2/"2"=marble, 3/"3"=crystal, 4/"4"=sulfur
RESOURCE_PROJECTION_META = {
    "wood": {"label": "Madeira", "icon": "game/resources/icon_wood.png", "tradegood": 0, "res_key": "resource"},
    "wine": {"label": "Vinho", "icon": "game/resources/icon_wine.png", "tradegood": 1, "res_key": "1"},
    "marble": {"label": "Marmore", "icon": "game/resources/icon_marble.png", "tradegood": 2, "res_key": "2"},
    "crystal": {"label": "Cristal", "icon": "game/resources/icon_glass.png", "tradegood": 3, "res_key": "3"},
    "sulfur": {"label": "Enxofre", "icon": "game/resources/icon_sulfur.png", "tradegood": 4, "res_key": "4"},
}


def _build_city_resource_projections(
    cities: list[dict],
    wood_production_per_hour: float,
    tradegood_production_per_hour: float,
    global_wine_spendings: float = 0,
    snapshot_updated_at=None,
) -> dict[int, dict]:
    """Estimate current fill pressure and fill time per resource and city."""
    if not cities:
        return {}

    snapshot_epoch = None
    if snapshot_updated_at is not None:
        if isinstance(snapshot_updated_at, datetime):
            snapshot_epoch = int(snapshot_updated_at.timestamp())
        else:
            try:
                snapshot_epoch = int(snapshot_updated_at.timestamp())
            except AttributeError:
                snapshot_epoch = None

    # Pre-compute population-weighted wine distribution for fallback.
    # Ikariam doesn't expose per-city wine consumption in the city HTML,
    # so we distribute global_wine_spendings proportionally by population.
    total_pop = sum(_si(c.get("population")) for c in cities)
    city_wine_fallback: dict[int, float] = {}
    if global_wine_spendings > 0:
        for c in cities:
            cid = _si(c.get("id"), default=-1)
            pop = _si(c.get("population"))
            if total_pop > 0:
                city_wine_fallback[cid] = global_wine_spendings * (pop / total_pop)
            else:
                city_wine_fallback[cid] = global_wine_spendings / max(len(cities), 1)

    projections: dict[int, dict] = {}
    for city in cities:
        capacity = _si(city.get("warehouse_capacity"))
        max_resources = city.get("max_resources") if isinstance(city.get("max_resources"), dict) else {}
        market_resources = city.get("market_resources") if isinstance(city.get("market_resources"), dict) else {}
        tradegood = _si(city.get("produced_tradegood") or city.get("tradegood"), default=-1)
        city_id = _si(city.get("id"), default=-1)
        # Respect the city snapshot value as the source of truth. Zero is valid
        # for cities with tavern wine turned off, so do not redistribute account totals here.
        wine_consumption = float(_si(city.get("wine_consumption")))
        # Per-city rates: only use city-specific data (no guessing with averages)
        wood_rate = float(city.get("resource_production_per_hour") or 0)
        luxury_rate = float(city.get("tradegood_production_per_hour") or 0)
        city_resources = []

        for key in ("wood", "wine", "marble", "crystal", "sulfur"):
            meta = RESOURCE_PROJECTION_META[key]
            current = _si(city.get(key))
            res_key = meta["res_key"]
            resource_capacity = _si(max_resources.get(res_key), default=capacity)
            pct = round(current / resource_capacity * 100) if resource_capacity > 0 else 0
            market = _si(market_resources.get(res_key))

            production_hour = 0.0
            consumption_hour = 0.0
            if key == "wood":
                production_hour = wood_rate
            elif key == "wine":
                production_hour = luxury_rate if tradegood == 1 else 0.0
                consumption_hour = wine_consumption
            elif tradegood == meta["tradegood"]:
                production_hour = luxury_rate

            net_hour = production_hour - consumption_hour

            if current >= resource_capacity > 0:
                fill_hours = 0.0
            elif net_hour > 0 and resource_capacity > current:
                fill_hours = round((resource_capacity - current) / net_hour, 1)
            else:
                fill_hours = None

            city_resources.append({
                "key": key,
                "label": meta["label"],
                "icon": meta["icon"],
                "current": current,
                "base_current": current,
                "projected_current": current,
                "capacity": resource_capacity,
                "pct": pct,
                "market": market,
                "production_hour": round(production_hour, 1),
                "consumption_hour": round(consumption_hour, 1),
                "net_hour": round(net_hour, 1),
                "fill_hours": fill_hours,
                "will_fill": fill_hours is not None,
                "snapshot_epoch": snapshot_epoch,
            })

        cap_resource = max(city_resources, key=lambda item: (item["pct"], item["current"])) if city_resources else None
        projections[city_id] = {
            "resources": city_resources,
            "cap_resource": cap_resource,
            "cap_pct": cap_resource["pct"] if cap_resource else 0,
            "fill_hours": cap_resource["fill_hours"] if cap_resource else None,
        }

    return projections


def _build_history_map(game_account_ids: list) -> dict[str, dict]:
    if not game_account_ids:
        return {}

    rows = (
        AccountSnapshotHistory.objects
        .filter(game_account_id__in=game_account_ids)
        .annotate(
            history_rank=Window(
                expression=RowNumber(),
                partition_by=[F("game_account_id")],
                order_by=F("captured_at").desc(),
            )
        )
        .filter(history_rank__lte=12)
        .only("game_account_id", "captured_at", "base_snapshot", "cities", "military")
        .order_by("game_account_id", "-captured_at")
    )

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row.game_account_id)
        bucket = grouped.setdefault(key, [])
        if len(bucket) >= 12:
            continue
        base = row.base_snapshot or {}
        cities = row.cities if isinstance(row.cities, list) else []
        military = row.military if isinstance(row.military, dict) else {}
        gross_income = _si(base.get("income"))
        upkeep = _si(base.get("upkeep"))
        scientists_upkeep = _si(base.get("scientists_upkeep"))
        wood = sum(_si(city.get("wood")) for city in cities if isinstance(city, dict))
        wine = sum(_si(city.get("wine")) for city in cities if isinstance(city, dict))
        marble = sum(_si(city.get("marble")) for city in cities if isinstance(city, dict))
        crystal = sum(_si(city.get("crystal")) for city in cities if isinstance(city, dict))
        sulfur = sum(_si(city.get("sulfur")) for city in cities if isinstance(city, dict))
        troop_total = sum(_si(unit.get("count")) for unit in (military.get("troops") or []) if isinstance(unit, dict))
        ship_total = sum(_si(unit.get("count")) for unit in (military.get("fleet") or []) if isinstance(unit, dict))
        bucket.append({
            "captured_at": row.captured_at.isoformat(),
            "gold": _si(base.get("gold")),
            "income": gross_income + upkeep + scientists_upkeep,
            "cities": len(cities),
            "resources": wood + wine + marble + crystal + sulfur,
            "troops": troop_total,
            "ships": ship_total,
            "wood": wood,
            "wine": wine,
            "marble": marble,
            "crystal": crystal,
            "sulfur": sulfur,
        })

    for key, points in grouped.items():
        grouped[key] = list(reversed(points))

    return grouped
