"""Espionagem Mundial (N-150): the campaign of a world spy job in numbers, for its job detail page.

A campaign is the first ac=16 job (its root) and the rounds that follow it (their inputs carry
__campaign_root_id); every spy job it starts (ac=15) carries __root_job_id. What each spy job did is in its
logs ("Inspecionar armazém: ✓ sucesso", "Treinando 5 espião(ões) (custo: 750 ouro, 270 cristal)") and in the
reports it saved (SpyReport.created_by_job).
"""
from __future__ import annotations

import json
import re
from collections import Counter

from django.templatetags.static import static

WORLD_SPY = 16
SPY = 15
MISSIONS = {
    5: {"name": "Armazém", "icon": "game/buildings/warehouse.png"},
    6: {"name": "Guarnição", "icon": "game/units/hoplita.png"},
    7: {"name": "Tropas e frotas", "icon": "game/units/trieme.png"},
    3: {"name": "Nível de pesquisa", "icon": "game/buildings/academy.png"},
    26: {"name": "Invenções", "icon": "game/buildings/workshop.png"},
}
MISSION_BY_NAME = {"Inspecionar armazém": 5, "Guarnição militar": 6, "Tropas e frotas": 7, "Nível de pesquisa": 3,
                   "Invenções": 26}
TRAINED = re.compile(r"Treinando (\d+) espi.*?custo: (\d+) ouro, (\d+) cristal")
PENDING_CAP = 200          # the hub returns at most 200 targets per round
PENDING = re.compile(r"(\d+) alvos pendentes \| safehouses ocupadas=(\d+)/(\d+)")


def _inputs(job) -> dict:
    try:
        value = json.loads(job.inputs_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def campaign_summary(job) -> dict | None:
    """The numbers of the campaign this ac=16 job belongs to; None for any other job."""
    from apps.espionage.models import SpyReport
    from apps.jobs.models import Job, JobLog

    if int(job.action_code) != WORLD_SPY:
        return None
    root_id = str(_inputs(job).get("__campaign_root_id") or job.pk)
    root = Job.objects.filter(pk=root_id).first() or job
    since = root.created_at

    rounds = list(Job.objects.filter(action_code=WORLD_SPY, created_at__gte=since)
                  .filter(inputs_json__contains=root_id).only("pk", "status", "scheduled_for", "created_at"))
    if root.pk not in {r.pk for r in rounds}:
        rounds.append(root)
    round_ids = [r.pk for r in rounds]
    next_round = min((r.scheduled_for for r in rounds if r.status == "scheduled" and r.scheduled_for), default=None)
    # N-163: when the current pass started (the newest round carries it)
    from django.utils.dateparse import parse_datetime

    newest = max(rounds, key=lambda r: r.created_at)
    pass_started = parse_datetime(str(_inputs(Job.objects.filter(pk=newest.pk).only("inputs_json").first() or newest)
                                      .get("__pass_started_at") or "")) if newest else None
    last_count = (JobLog.objects.filter(job_id__in=round_ids, message__contains="alvos pendentes")
                  .order_by("-created_at").values_list("message", flat=True).first() or "")
    match = PENDING.search(last_count)

    spies = list(Job.objects.filter(action_code=SPY, created_at__gte=since, inputs_json__contains=root_id)
                 .only("pk", "status", "root_job_id", "inputs_json"))
    spy_ids = [j.pk for j in spies]
    # one target = every spy job (and reschedule) sent to that city in the campaign
    chains: dict = {}
    for j in spies:
        chains.setdefault(str(_inputs(j).get("target_city_id") or j.pk), []).append(j)
    states = Counter()
    for members in chains.values():
        status = {m.status for m in members}
        if status & {"queued", "scheduled", "running"}:
            states["running"] += 1
        elif "finished" in status:
            states["done"] += 1
        else:
            states["error"] += 1

    missions: dict[int, dict] = {}
    trained = gold = crystal = 0
    for message in JobLog.objects.filter(job_id__in=spy_ids).filter(
            message__regex=r"(: ✓ sucesso|: ✗ falhou|Treinando [0-9]+ espi)").values_list("message", flat=True):
        found = TRAINED.search(message)
        if found:
            trained += int(found.group(1))
            gold += int(found.group(2))
            crystal += int(found.group(3))
            continue
        mission_id = MISSION_BY_NAME.get(message.split(":")[0].strip())
        if mission_id is None:
            continue
        row = missions.setdefault(mission_id, {"ok": 0, "failed": 0})
        row["ok" if "✓" in message else "failed"] += 1

    reports = SpyReport.objects.filter(created_by_job_id__in=spy_ids)
    report_rows = list(reports.values_list("mission_id", "result_status", "agents_lost", "target_city_id"))
    lost = sum(r[2] or 0 for r in report_rows)
    stock_cities = len({r[3] for r in report_rows if r[0] == 5 and r[3]})

    return {
        "root_id": str(root.pk),
        "started_at": root.created_at,
        "rounds": len(rounds),
        "next_round": next_round,
        "pass_started": pass_started,
        "pending": int(match.group(1)) if match else None,
        "pending_capped": bool(match) and int(match.group(1)) >= PENDING_CAP,
        "safehouses_busy": int(match.group(2)) if match else None,
        "safehouses": int(match.group(3)) if match else None,
        "targets_done": states["done"],
        "targets_running": states["running"],
        "targets_error": states["error"],
        "missions": [
            {"id": mid, "name": MISSIONS.get(mid, {}).get("name", str(mid)),
             "icon": static(MISSIONS.get(mid, {}).get("icon", "game/buildings/safehouse.png")), **row}
            for mid, row in sorted(missions.items(), key=lambda item: list(MISSIONS).index(item[0]) if item[0] in MISSIONS else 99)
        ],
        "spies_lost": lost,
        "spies_trained": trained,
        "training_gold": gold,
        "training_crystal": crystal,
        "reports": len(report_rows),
        "reports_failed": sum(1 for r in report_rows if "falhad" in str(r[1] or "").lower()),
        "stock_cities": stock_cities,
    }
