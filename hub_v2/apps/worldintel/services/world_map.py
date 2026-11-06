"""N-169: the world map of Inteligência — the islands of a dump, where the world spy campaign has read, how its ring grew
day by day and why cities were read again.

Everything the page draws comes from one summary, kept for a few minutes: a dump has ~5 thousand islands and a
campaign thousands of reports, too much to read on every view (see N-167).
"""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

CACHE_SECONDS = 10 * 60
RING = 5                     # ring width, in map units
STOCK = 5                    # the warehouse mission: the reading that makes a target "read"
WORLD_SPY = 16
SPY = 15
ACTIVE = ("queued", "scheduled", "running")
# N-163 went live here: before it every stock report lasted 48 h
RULE_BY_MISSION_AT = datetime(2026, 10, 26, 6, 24, tzinfo=dt_timezone.utc)
REASONS = ("failed", "old_rule", "rich", "poor", "early")
REASON_LABELS = {"failed": "anterior falhou", "old_rule": "venceu em 48 h", "rich": "rico vencido",
                 "poor": "pobre vencido", "early": "antes de vencer"}


def _why(prev_at, prev_ok, prev_stock, at, rules) -> str:
    """Why a target was read again, from the reading before it."""
    if not prev_ok:
        return "failed"
    if at < RULE_BY_MISSION_AT:
        return "old_rule"
    rich = prev_stock >= rules["rich_at"]
    if at - prev_at < timedelta(hours=rules["ttl_rich"] if rich else rules["ttl_poor"]):
        return "early"
    return "rich" if rich else "poor"


def _rules() -> dict:
    from apps.espionage.services.validity import setting

    return {"rich_at": setting("spy_rich_threshold"), "ttl_rich": setting("spy_ttl_stock_rich_hours"),
            "ttl_poor": setting("spy_ttl_stock_poor_hours")}


def _inputs(job) -> dict:
    try:
        value = json.loads(job.inputs_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _success(result_status) -> bool:
    text = str(result_status or "").lower()
    return "sucesso" in text or "success" in text


def current_campaign_root(game_account=None):
    """The root of the newest world spy campaign (of that account, when one is chosen)."""
    from apps.jobs.models import Job

    qs = Job.objects.filter(action_code=WORLD_SPY)
    if game_account is not None:
        qs = qs.filter(game_account=game_account)
    job = qs.order_by("-created_at").only("pk", "root_job_id", "inputs_json").first()
    if job is None:
        return None
    root_id = _inputs(job).get("__campaign_root_id") or job.root_job_id or job.pk
    return Job.objects.filter(pk=root_id).first() or job


def map_summary(dump, root=None) -> dict:
    key = f"world_map:{dump.pk}:{root.pk if root else '-'}"
    summary = cache.get(key)
    if summary is None:
        summary = build_summary(dump, root)
        cache.set(key, summary, CACHE_SECONDS)
    return summary


def _safehouses(root) -> tuple[list, dict]:
    """Islands of the cities the campaign spies from (the newest round lists them), from the accounts' snapshots."""
    from apps.game.models import AccountSnapshot
    from apps.jobs.models import Job

    newest = (Job.objects.filter(root_job_id=root.pk, action_code=WORLD_SPY).order_by("-created_at")
              .only("inputs_json").first()) or root
    inputs = _inputs(newest)
    sources = {e.split(":")[-1].strip() for e in str(inputs.get("city_ids") or "").split(",") if e.strip()}
    houses = Counter()
    for cities in AccountSnapshot.objects.values_list("cities", flat=True):
        for city in cities or []:
            if str(city.get("id")) in sources and city.get("x") is not None and city.get("y") is not None:
                houses[(int(city["x"]), int(city["y"]))] += 1
    return [[x, y, n] for (x, y), n in sorted(houses.items())], inputs


def build_summary(dump, root=None) -> dict:
    from apps.espionage.models import SpyReport
    from apps.espionage.services.validity import stock_total
    from apps.jobs.models import Job
    from apps.worldintel.models import WorldDumpCity, WorldDumpIsland, WorldDumpPlayer

    now = timezone.now()
    rules = _rules()
    rich_at = rules["rich_at"]

    islands = {}
    for island_id, x, y, res, miracle, cities, helios in (
            WorldDumpIsland.objects.filter(dump=dump)
            .values_list("island_id", "x", "y", "resource_type", "miracle_type", "city_count", "helios_built")):
        islands[island_id] = {"id": island_id, "x": x, "y": y, "res": res, "mir": miracle, "cities": cities,
                              "helios": helios, "t": 0, "v": 0, "e": 0, "n": 0, "r": 0, "rich": 0, "reads": []}
    # the campaign's targets: the inactive cities its score limits let through (as the targets endpoint does)
    limits = _inputs(root) if root is not None else {}
    max_total = int(limits.get("max_total_score") or 0)
    max_army = int(limits.get("max_army_score") or 0)
    too_big = set()
    if max_total > 0 or max_army > 0:
        for owner_id, building, research, army in (WorldDumpPlayer.objects.filter(dump=dump).values_list(
                "owner_id", "building_score", "research_score", "army_score")):
            total = (building or 0) + (research or 0) + (army or 0)
            if (max_total > 0 and total > max_total) or (max_army > 0 and (army or 0) > max_army):
                too_big.add(owner_id)
    target_island = {}
    for city_id, island_id, owner_id in (WorldDumpCity.objects.filter(dump=dump, type="city", state="inactive")
                                         .values_list("game_city_id", "island__island_id", "owner_id")):
        if island_id in islands and city_id and owner_id not in too_big:
            target_island[city_id] = island_id
            islands[island_id]["t"] += 1

    # the newest successful stock reading of each target, from any job
    last = {}
    for city_id, expires_at, data in (SpyReport.objects.filter(mission_id=STOCK, target_city_id__in=list(target_island))
                                      .filter(_success_q()).order_by("created_at")
                                      .values_list("target_city_id", "expires_at", "data_json")):
        last[city_id] = (expires_at, stock_total(data))

    campaign = None
    running = set()
    houses = []
    first_day = {}
    days = []
    rereads = []
    if root is not None:
        houses, round_inputs = _safehouses(root)
        root_inputs = _inputs(root)
        for inputs_json in (Job.objects.filter(root_job_id=root.pk, action_code=SPY, status__in=ACTIVE)
                            .values_list("inputs_json", flat=True)):
            try:
                target = str(json.loads(inputs_json or "{}").get("target_city_id") or "")
            except ValueError:
                target = ""
            if target:
                running.add(target)
        start = timezone.localtime(root.created_at).date()
        today = timezone.localtime(now).date()
        days = [(start + timedelta(days=i)).strftime("%d/%m") for i in range((today - start).days + 1)]
        rereads = [dict.fromkeys(("new",) + REASONS, 0) for _ in days]
        prev = {}
        # a report whose job was deleted loses the link to the campaign (SET_NULL): it still counts
        campaign_reports = (SpyReport.objects.filter(mission_id=STOCK, created_at__gte=root.created_at)
                            .filter(Q(created_by_job__root_job_id=root.pk) | Q(created_by_job__isnull=True)))
        for city_id, at, status, data in (campaign_reports.order_by("created_at")
                                          .values_list("target_city_id", "created_at", "result_status", "data_json")):
            day = (timezone.localtime(at).date() - start).days
            if not 0 <= day < len(days):
                continue
            ok = _success(status)
            stock = stock_total(data) if ok else 0
            if city_id in prev:
                rereads[day][_why(*prev[city_id], at, rules)] += 1
            elif ok:
                rereads[day]["new"] += 1
            if ok and city_id not in first_day:
                first_day[city_id] = day
            prev[city_id] = (at, ok, stock if ok else (prev[city_id][2] if city_id in prev else 0))
        campaign = {
            "root": str(root.pk),
            "started": timezone.localtime(root.created_at).strftime("%d/%m/%Y"),
            "pass_started": str(round_inputs.get("__pass_started_at") or ""),
            "region": [root_inputs.get(k) for k in ("region_x_min", "region_x_max", "region_y_min", "region_y_max")],
        }

    for city_id, island_id in target_island.items():
        isl = islands[island_id]
        if city_id in running:
            isl["r"] += 1
        if city_id in last:
            expires_at, stock = last[city_id]
            if expires_at is None or expires_at > now:
                isl["v"] += 1
                isl["rich"] += stock >= rich_at
            else:
                isl["e"] += 1
        else:
            isl["n"] += 1
        if city_id in first_day:
            isl["reads"].append(first_day[city_id])

    for isl in islands.values():
        isl["ring"] = (int(min(math.hypot(isl["x"] - hx, isl["y"] - hy) for hx, hy, _ in houses) // RING)
                       if houses else None)
        isl["reads"].sort()

    return {
        "dump": {"id": str(dump.pk), "captured": timezone.localtime(dump.captured_at).strftime("%d/%m/%Y %H:%M")},
        "campaign": campaign,
        "days": days,
        "rereads": rereads,
        "houses": houses,
        "ring_width": RING,
        "rich_threshold": rich_at,
        "built": timezone.localtime(now).strftime("%d/%m %H:%M"),
        "islands": [
            [i["id"], i["x"], i["y"], i["res"], i["mir"], i["cities"], int(i["helios"]), i["t"], i["v"], i["e"], i["n"],
             i["r"], i["rich"], i["ring"], i["reads"]]
            for i in islands.values()
        ],
    }


def _success_q():
    return Q(result_status__icontains="sucesso") | Q(result_status__icontains="success")


def island_detail(dump, island_id: str) -> dict | None:
    """One island, opened on the map: its cities and, for the campaign's targets, every stock reading and why."""
    from apps.espionage.models import SpyReport
    from apps.espionage.services.validity import stock_total
    from apps.worldintel.models import WorldDumpCity, WorldDumpIsland

    island = WorldDumpIsland.objects.filter(dump=dump, island_id=island_id).first()
    if island is None:
        return None
    now = timezone.now()
    rules = _rules()
    rich_at = rules["rich_at"]
    cities = list(WorldDumpCity.objects.filter(dump=dump, island=island).order_by("position"))
    ids = [c.game_city_id for c in cities if c.type == "city" and c.game_city_id]
    reports: dict[str, list] = {}
    for city_id, at, expires_at, status, data in (SpyReport.objects.filter(mission_id=STOCK, target_city_id__in=ids)
                                                  .order_by("created_at")
                                                  .values_list("target_city_id", "created_at", "expires_at",
                                                               "result_status", "data_json")):
        reports.setdefault(city_id, []).append((at, expires_at, _success(status), stock_total(data)))
    rows = []
    for city in cities:
        if city.type != "city":
            continue
        reads = reports.get(city.game_city_id, [])
        history = []
        prev = None
        for at, _expires_at, ok, stock in reads:
            why = REASON_LABELS[_why(*prev, at, rules)] if prev is not None else ""
            history.append({"at": timezone.localtime(at).strftime("%d/%m %H:%M"), "ok": ok, "stock": stock, "why": why})
            prev = (at, ok, stock if ok else (prev[2] if prev else 0))
        good = [r for r in reads if r[2]]
        last = good[-1] if good else None
        state = "never"
        if last is not None:
            state = "valid" if (last[1] is None or last[1] > now) else "expired"
        rows.append({
            "name": city.name, "owner": city.owner_name, "ally": city.ally_tag, "level": city.level,
            "state": city.state, "target": city.state == "inactive", "read": state,
            "stock": last[3] if last else None, "rich": bool(last and last[3] >= rich_at),
            "until": timezone.localtime(last[1]).strftime("%d/%m %H:%M") if last and last[1] else "",
            "history": history[-6:], "reads": len(history),
        })
    return {"island": island, "cities": rows, "free": sum(1 for c in cities if c.type == "buildplace")}


def search_islands(dump, q: str) -> list[str]:
    from apps.worldintel.models import WorldDumpCity

    q = q.strip()
    if len(q) < 2:
        return []
    return list(WorldDumpCity.objects.filter(dump=dump).filter(Q(owner_name__icontains=q) | Q(ally_tag__iexact=q))
                .values_list("island__island_id", flat=True).distinct()[:600])
