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


def raid_targets(organization, server_id: str, *, min_total: int, resource: str = "", only_undefended: bool = False,
                 limit: int = 120) -> list[dict]:
    """The cities with a valid stock reading at or above `min_total`, richest first, with their defense, alert and
    raid state. Possible multi accounts come marked (and last)."""
    from django.templatetags.static import static

    from apps.espionage.api.views import _ID_TO_NAME, _parse_enemy_intel
    from apps.espionage.models import RaidAlertSent, SpyReport
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
    rows = []
    for city_id, report in latest.items():
        data = report.data_json or {}
        total = stock_total(data)
        if total < min_total:
            continue
        rows.append((city_id, report, total, data))
    sort_key = (lambda r: -int(r[3].get(resource) or 0)) if resource else (lambda r: -r[2])
    rows.sort(key=sort_key)

    multi = marked(organization, server_id, "multi")
    raiding = set()
    for inputs_json in Job.objects.filter(action_code=RAID, status__in=("queued", "scheduled", "running")).values_list(
            "inputs_json", flat=True):
        try:
            raiding.add(str(json.loads(inputs_json or "{}").get("target_city_id") or ""))
        except ValueError:
            continue
    ignored = {r.target_city_id: r for r in RaidAlertSent.objects.filter(target_city_id__in=[r[0] for r in rows])}
    islands = dict(WorldDumpCity.objects.filter(game_city_id__in=[r[0] for r in rows], dump__game_account__server_id=server_id)
                   .order_by("dump__captured_at").values_list("game_city_id", "island__island_id"))

    out = []
    for city_id, report, total, data in rows:
        intel = _parse_enemy_intel(city_id, report.target_owner_id, server_id, report.target_owner or "")
        troops = sum(int(q or 0) for q in (intel.get("enemy_units") or {}).values())
        ships = sum(int(q or 0) for q in (intel.get("enemy_fleet") or {}).values())
        if only_undefended and (troops or ships):
            continue
        sent = ignored.get(city_id)
        state = "raiding" if city_id in raiding else (
            "ignored" if sent and sent.ignored_report_id else "alerted" if sent and sent.last_alerted_at else "new")
        out.append({
            "city_id": city_id, "island_id": islands.get(city_id, ""), "name": report.target_city_name,
            "owner": report.target_owner, "owner_id": report.target_owner_id,
            "coords": f"{report.target_x}:{report.target_y}" if report.target_x is not None else "",
            "total": total, "read_at": report.created_at, "expires_at": report.expires_at,
            "resources": [{"key": key, "label": label, "icon": static(icon), "amount": int(data.get(key) or 0),
                           "share": round(100 * int(data.get(key) or 0) / total) if total else 0}
                          for key, label, icon in RESOURCES],
            "troops": troops, "ships": ships, "wall": intel.get("wall_level"),
            "fleet": ", ".join(f"{q}× {_ID_TO_NAME.get(int(u), u)}" for u, q in (intel.get("enemy_fleet") or {}).items()),
            "state": state, "multi": is_marked(report.target_owner_id, report.target_owner, multi),
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda t: t["multi"])          # multi accounts last, the rest keeps its order
    return out


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
