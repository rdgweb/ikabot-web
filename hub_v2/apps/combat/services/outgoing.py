"""What is on its way now: the raids the hub sent (one group per target, one row per account) and the other
military movements of our accounts the military advisor saw (attacks sent by hand, blockades, troops and fleets
moving). Transports are left out."""
from __future__ import annotations

import json

from django.utils import timezone

RAID = 1008
ACTIVE = ("queued", "scheduled", "running")
BATTLE_BUFFER = 120          # the raid checks the battle 2 min after it arrives
MISSIONS = {"plunder": "Saque", "blockade": "Bloqueio", "deployarmy": "Tropas", "deployfleet": "Frota",
            "defend": "Defesa", "defend_port": "Defesa do porto", "occupy": "Ocupação", "piracyRaid": "Pirataria"}


def _city_names(organization) -> dict[str, tuple[str, str]]:
    """city id -> (city name, account name) of our accounts."""
    from apps.game.models import AccountSnapshot

    out = {}
    for account, cities in AccountSnapshot.objects.values_list("game_account__name", "cities"):
        for city in cities or []:
            if isinstance(city, dict):
                out[str(city.get("id"))] = (str(city.get("name") or ""), str(account or ""))
    return out


def _targets(city_ids) -> dict[str, dict]:
    from apps.worldintel.models import WorldDumpCity

    out = {}
    for cid, name, owner, x, y in (WorldDumpCity.objects.filter(game_city_id__in=list(city_ids)).order_by("dump__captured_at")
                                   .values_list("game_city_id", "name", "owner_name", "island__x", "island__y")):
        out[cid] = {"name": name, "owner": owner, "coords": f"{x}:{y}"}
    return out


def outgoing(organization=None) -> dict:
    from apps.game.models import AccountSnapshot
    from apps.jobs.models import Job

    from ..views import _losses

    now = timezone.now()
    cities = _city_names(organization)
    raids, raid_keys = {}, set()
    jobs = list(Job.objects.filter(action_code=RAID, status__in=ACTIVE).select_related("game_account").order_by("scheduled_for"))
    targets = _targets({str(json.loads(j.inputs_json or "{}").get("target_city_id") or "") for j in jobs})
    for job in jobs:
        inputs = json.loads(job.inputs_json or "{}")
        target_id = str(inputs.get("target_city_id") or "")
        phase = str(inputs.get("_phase") or "send")
        source = str(inputs.get("_source_city_id") or inputs.get("source_city_id") or "")
        when = job.scheduled_for
        if phase == "check_battle":
            label, tone, eta = "indo", "live", (when.timestamp() - BATTLE_BUFFER) if when else None
        elif phase in ("wait_movements",):
            label, tone, eta = "voltando com saque", "win", when.timestamp() if when else None
        elif phase in ("wait_blockade",):
            label, tone, eta = "bloqueando o porto", "live", when.timestamp() if when else None
        elif phase == "recall_fleet":
            label, tone, eta = "chamando a frota", "done", None
        else:
            label, tone, eta = ("próxima viagem" if int(inputs.get("_trips_done") or 0) else "saindo"), "done", (
                when.timestamp() if when else None)
        group = raids.setdefault(target_id, {"target_id": target_id, **targets.get(target_id, {"name": f"Cidade {target_id}",
                                                                                              "owner": "", "coords": ""}),
                                             "rows": [], "first": None})
        units = inputs.get("units") or {}
        group["rows"].append({
            "account": job.game_account.name if job.game_account else "",
            "source": cities.get(source, ("", ""))[0] or source,
            "label": label, "tone": tone, "eta": int(eta) if eta else None,
            "trip": int(inputs.get("_trips_done") or 0) + 1, "ships": int(inputs.get("_transporters_sent") or 0),
            "units": _losses(units), "job_id": str(job.pk),
        })
        if eta and (group["first"] is None or eta < group["first"]):
            group["first"] = int(eta)
        raid_keys.add((job.game_account_id and str(job.game_account_id), target_id))

    others = []
    for ga_id, account, movements in AccountSnapshot.objects.values_list("game_account_id", "game_account__name", "movements"):
        for move in (movements or {}).get("movement_details") or []:
            mission = str(move.get("mission") or "")
            if not move.get("is_own") or mission == "transport":
                continue
            target = move.get("target") or {}
            if mission == "plunder" and (str(ga_id), str(target.get("cityId") or "")) in raid_keys:
                continue                                   # already shown with its raid
            event = int(move.get("event_time") or 0)
            if event and event < now.timestamp() - 60:
                continue                                   # an old reading
            origin = move.get("origin") or {}
            others.append({
                "account": account, "mission": MISSIONS.get(mission, move.get("mission_text") or mission),
                "returning": bool(move.get("is_returning")), "eta": event or None,
                "origin": origin.get("name") or "", "target": target.get("name") or "",
                "target_owner": target.get("avatarName") or "",
                "units": _losses({k: v for k, v in (move.get("troops") or {}).items() if str(k).isdigit()}),
                "ships": sum(int(v or 0) for v in (move.get("fleet") or {}).values()),
            })
    groups = sorted(raids.values(), key=lambda g: g["first"] or 10 ** 12)
    for group in groups:
        group["rows"].sort(key=lambda r: r["eta"] or 10 ** 12)
    others.sort(key=lambda m: m["eta"] or 10 ** 12)
    return {"raids": groups, "others": others, "count": sum(len(g["rows"]) for g in groups) + len(others),
            "now": int(now.timestamp())}
