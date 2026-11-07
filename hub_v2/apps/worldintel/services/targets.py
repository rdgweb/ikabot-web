"""N-174: Inteligência > Alvos — the rich targets to raid and the piracy targets, from what the hub already has."""
from __future__ import annotations

import json

from django.utils import timezone

RESOURCES = (("wood", "Madeira", "game/resources/icon_wood.png"), ("wine", "Vinho", "game/resources/icon_wine.png"),
             ("marble", "Mármore", "game/resources/icon_marble.png"), ("crystal", "Cristal", "game/resources/icon_glass.png"),
             ("sulfur", "Enxofre", "game/resources/icon_sulfur.png"))
RAID = 1008
PIRACY = 17


def _success_q():
    from django.db.models import Q

    return Q(result_status__icontains="sucesso") | Q(result_status__icontains="success")


def _defense(data_json) -> tuple[int, int, str]:
    """(troops, ships, fleet text) from a garrison report: the game lists units by category (naval or not)."""
    from apps.espionage.api.views import _ID_TO_NAME, _NAME_TO_ID

    troops = ships = 0
    fleet = []
    for cat in (data_json or {}).get("troops_data") or []:
        if not isinstance(cat, dict):
            continue
        naval = "naval" in f"{cat.get('category_type') or ''} {cat.get('category') or ''}".lower()
        for unit in cat.get("units") or []:
            count = int(unit.get("count") or 0)
            uid = _NAME_TO_ID.get(str(unit.get("name") or "").strip())
            if count <= 0:
                continue
            if naval or (uid and 200 <= uid < 300):
                ships += count
                fleet.append(f"{count}× {_ID_TO_NAME.get(uid, unit.get('name'))}")
            else:
                troops += count
    return troops, ships, ", ".join(fleet)


def _own_cities(organization, server_id: str) -> list[tuple[int, int, str, str, str, str]]:
    """(x, y, city name, account name, game account id, city id) of every city of the organization on that server."""
    from apps.game.models import AccountSnapshot

    out = []
    for ga_id, name, cities in (AccountSnapshot._base_manager.filter(account__organization=organization,
                                                                      game_account__server_id=server_id,
                                                                      game_account__active=True)
                                .values_list("game_account_id", "game_account__name", "cities")):
        for city in cities or []:
            if isinstance(city, dict) and city.get("x") is not None and city.get("y") is not None:
                out.append((int(city["x"]), int(city["y"]), str(city.get("name") or ""), str(name or ""), str(ga_id),
                            str(city.get("id") or "")))
    return out


def _accounts_by_trip(own, target) -> list[dict]:
    """Each account's nearest city to the target and the troops' trip from it, nearest account first."""
    import math

    from apps.espionage.services import travel

    best = {}
    for x, y, city_name, account, ga_id, city_id in own:
        distance = math.dist((x, y), target)
        if ga_id not in best or distance < best[ga_id][0]:
            best[ga_id] = (distance, city_name, account, city_id)
    rows = []
    for ga_id, (distance, city_name, account, city_id) in best.items():
        trip = travel.journey_seconds(distance, 60, travel.TROOPS_JOURNEY_BASE)
        rows.append({"ga_id": ga_id, "account": account, "city_id": city_id, "city": city_name, "trip": trip,
                     "trip_human": travel.human(trip)})
    return sorted(rows, key=lambda r: r["trip"])


def raid_targets(organization, server_id: str, *, min_total: int, order: str = "", only_undefended: bool = False) -> dict:
    """The cities with a valid stock reading at or above `min_total`, with their defense, the troops' trip from our
    nearest city, and alert and raid state; richest first (or by one resource, or nearest). Possible multi accounts
    come marked and last. Everything is read in a few queries: the page shows hundreds of targets."""
    import math

    from django.templatetags.static import static

    from apps.espionage.api.views import _guess_wall_level_from_city_level
    from apps.espionage.models import RaidAlertSent, SpyReport
    from apps.espionage.services import travel
    from apps.espionage.services.validity import stock_total
    from apps.jobs.models import Job
    from apps.worldintel.models import WorldDumpCity

    from .marks import is_marked, marked

    now = timezone.now()
    latest = {}
    for report in (SpyReport.objects.filter(mission_id=5, game_account__server_id=server_id, expires_at__gt=now)
                   .filter(_success_q()).order_by("created_at")
                   .only("target_city_id", "target_city_name", "target_owner", "target_owner_id", "target_x", "target_y",
                         "data_json", "created_at", "expires_at")):
        latest[report.target_city_id] = report
    rows = [(cid, r, stock_total(r.data_json or {}), r.data_json or {}) for cid, r in latest.items()]
    rows = [row for row in rows if row[2] >= min_total]
    ids = [row[0] for row in rows]

    garrison = dict(SpyReport.objects.filter(mission_id=6, target_city_id__in=ids).order_by("created_at")
                    .values_list("target_city_id", "data_json"))
    dump_city = {cid: (island, level) for cid, island, level in WorldDumpCity.objects.filter(
        game_city_id__in=ids, dump__game_account__server_id=server_id).order_by("dump__captured_at")
        .values_list("game_city_id", "island__island_id", "level")}
    raiding = set()
    for inputs_json in Job.objects.filter(action_code=RAID, status__in=("queued", "scheduled", "running")).values_list(
            "inputs_json", flat=True):
        try:
            raiding.add(str(json.loads(inputs_json or "{}").get("target_city_id") or ""))
        except ValueError:
            continue
    alerts = {r.target_city_id: r for r in RaidAlertSent.objects.filter(target_city_id__in=ids)}
    multi = marked(organization, server_id, "multi")
    own = _own_cities(organization, server_id)

    out = []
    for city_id, report, total, data in rows:
        troops, ships, fleet = _defense(garrison.get(city_id))
        if only_undefended and (troops or ships):
            continue
        accounts = _accounts_by_trip(own, (report.target_x, report.target_y)) if own and report.target_x is not None else []
        trip = accounts[0]["trip"] if accounts else 0
        origin = f"{accounts[0]['city']} ({accounts[0]['account']})" if accounts else ""
        island, level = dump_city.get(city_id, ("", 0))
        sent = alerts.get(city_id)
        state = "raiding" if city_id in raiding else (
            "ignored" if sent and sent.ignored_report_id else "alerted" if sent and sent.last_alerted_at else "new")
        out.append({
            "city_id": city_id, "island_id": island, "name": report.target_city_name,
            "owner": report.target_owner, "owner_id": report.target_owner_id,
            "coords": f"{report.target_x}:{report.target_y}" if report.target_x is not None else "",
            "total": total, "read_at": report.created_at, "expires_at": report.expires_at,
            "resources": [{"key": key, "label": label, "icon": static(icon), "amount": int(data.get(key) or 0),
                           "share": round(100 * int(data.get(key) or 0) / total) if total else 0}
                          for key, label, icon in RESOURCES],
            "troops": troops, "ships": ships, "fleet": fleet,
            "wall": _guess_wall_level_from_city_level(level) if level else None,
            "trip": trip, "trip_human": travel.human(trip) if trip else "", "origin": origin, "accounts": accounts,
            "state": state, "multi": is_marked(report.target_owner_id, report.target_owner, multi),
        })
    if order == "near":
        out.sort(key=lambda t: (t["multi"], t["trip"] or 10 ** 9))
    elif order:
        out.sort(key=lambda t: (t["multi"], -next(r["amount"] for r in t["resources"] if r["key"] == order)))
    else:
        out.sort(key=lambda t: (t["multi"], -t["total"]))
    return {"targets": out, "count": len(out)}


def piracy_overview(organization, server_id: str) -> dict:
    """Each account's pirate fortress ranking (from its piracy job) and the players marked as piracy targets,
    with whether they show up in any ranking now."""
    from apps.jobs.models import Job

    from .marks import marks_of

    accounts, seen_players = [], {}
    now = int(timezone.now().timestamp())
    for job in (Job.objects.filter(action_code=PIRACY, status__in=("queued", "scheduled", "running"),
                                   game_account__server_id=server_id).select_related("game_account").order_by("game_account__name")):
        try:
            inputs = json.loads(job.inputs_json or "{}")
        except ValueError:
            continue
        left = int(inputs.get("piracy_highscore_time_left") or 0)
        read_at = int(inputs.get("piracy_highscore_updated_at") or 0)
        entries = inputs.get("piracy_highscore_entries") if isinstance(inputs.get("piracy_highscore_entries"), list) else []
        for entry in entries:
            seen_players.setdefault(str(entry.get("player_name") or "").casefold(), []).append(job.game_account.name)
        accounts.append({
            "name": job.game_account.name, "job_id": str(job.pk),
            "self": inputs.get("piracy_highscore_self") or {}, "entries": entries[:10],
            "left": max(0, left - (now - read_at)) if left and read_at else 0,
            "final_phase": bool(inputs.get("piracy_final_phase_active")),
        })
    picked = [{"name": m.owner_name, "owner_id": m.owner_id, "in_ranking": seen_players.get(m.owner_name.casefold(), [])}
              for m in marks_of(organization, server_id, "pirate_target")]
    return {"accounts": accounts, "picked": picked}
