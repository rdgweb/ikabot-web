"""Espionagem Mundial (N-150): the campaign of a world spy job in numbers, for its job detail page.

A campaign is the first ac=16 job (its root), the rounds that follow it and the spy jobs it starts (ac=15): all of them
have the root in root_job_id. What each spy job did is in the reports it saved (SpyReport.created_by_job), and the
spies it trained in its logs ("Treinando 5 espião(ões) (custo: 750 ouro, 270 cristal)").
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import timedelta

from django.core.cache import cache
from django.templatetags.static import static
from django.utils import timezone
from django.utils.dateparse import parse_datetime

WORLD_SPY = 16
SPY = 15
MISSIONS = {
    5: {"name": "Armazém", "icon": "game/buildings/warehouse.png"},
    6: {"name": "Guarnição", "icon": "game/units/hoplita.png"},
    7: {"name": "Tropas e frotas", "icon": "game/units/trieme.png"},
    3: {"name": "Nível de pesquisa", "icon": "game/buildings/academy.png"},
    26: {"name": "Invenções", "icon": "game/buildings/workshop.png"},
}
CACHE_SECONDS = 5 * 60
TRAINING_CACHE_SECONDS = 30 * 24 * 3600
TRAINING_OVERLAP = timedelta(days=1)
TRAINED = re.compile(r"Treinando (\d+) espi.*?custo: (\d+) ouro, (\d+) cristal")
PENDING_CAP = 200          # the hub returns at most 200 targets per round
PENDING = re.compile(r"(\d+) alvos pendentes \| safehouses ocupadas=(\d+)/(\d+)")


def _inputs(job) -> dict:
    try:
        value = json.loads(job.inputs_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _training_totals(root_id, JobLog) -> dict:
    """Spies trained in the campaign. The logs are too many to read on every view (1,8 million lines), so what was
    counted is kept and only the jobs created since the last count (and a day before it) are read again."""
    key = f"world_spy_campaign:{root_id}:training"
    known = cache.get(key) or {"counted": {}, "since": None}
    counted = dict(known["counted"])
    now = timezone.now()
    logs = JobLog.objects.filter(job__root_job_id=root_id, job__action_code=SPY, message__startswith="Treinando ")
    if known["since"] is not None:
        logs = logs.filter(job__created_at__gte=known["since"] - TRAINING_OVERLAP)
    for log_id, message in logs.values_list("pk", "message"):
        found = TRAINED.search(message)
        if found:
            counted[str(log_id)] = (int(found.group(1)), int(found.group(2)), int(found.group(3)))
    cache.set(key, {"counted": counted, "since": now}, TRAINING_CACHE_SECONDS)
    return {"trained": sum(c[0] for c in counted.values()), "gold": sum(c[1] for c in counted.values()),
            "crystal": sum(c[2] for c in counted.values())}


def campaign_summary(job) -> dict | None:
    """The numbers of the campaign this ac=16 job belongs to; None for any other job. Kept for a few minutes: a
    campaign has tens of thousands of jobs (every round and spy job carries the root in root_job_id)."""
    if int(job.action_code) != WORLD_SPY:
        return None
    root_id = str(_inputs(job).get("__campaign_root_id") or job.pk)
    key = f"world_spy_campaign:{root_id}"
    summary = cache.get(key)
    if summary is None:
        summary = _build_summary(job, root_id)
        cache.set(key, summary, CACHE_SECONDS)
    return summary


def _build_summary(job, root_id: str) -> dict:
    from apps.espionage.models import SpyReport
    from apps.jobs.models import Job, JobLog

    root = Job.objects.filter(pk=root_id).first() or job
    rounds = Job.objects.filter(root_job_id=root.pk, action_code=WORLD_SPY)
    round_count = rounds.count() + 1
    next_round = (rounds.filter(status="scheduled", scheduled_for__isnull=False).order_by("scheduled_for")
                  .values_list("scheduled_for", flat=True).first())
    newest = list(rounds.order_by("-created_at").only("pk", "inputs_json")[:30]) or [root]
    # N-163: when the current pass started (the newest round carries it)
    pass_started = parse_datetime(str(_inputs(newest[0]).get("__pass_started_at") or ""))
    last_count = (JobLog.objects.filter(job_id__in=[r.pk for r in newest] + [root.pk], message__contains="alvos pendentes")
                  .order_by("-created_at").values_list("message", flat=True).first() or "")
    match = PENDING.search(last_count)

    # one target = every spy job (and reschedule) sent to that city in the campaign
    chains: dict = {}
    for pk, status, inputs_json in (Job.objects.filter(root_job_id=root.pk, action_code=SPY)
                                    .values_list("pk", "status", "inputs_json")):
        try:
            target = str(json.loads(inputs_json or "{}").get("target_city_id") or "")
        except ValueError:
            target = ""
        chains.setdefault(target or str(pk), set()).add(status)
    states = Counter()
    for status in chains.values():
        if status & {"queued", "scheduled", "running"}:
            states["running"] += 1
        elif "finished" in status:
            states["done"] += 1
        else:
            states["error"] += 1

    missions: dict[int, dict] = {}
    lost = reports = reports_failed = 0
    stock_cities = set()
    for mission_id, result, agents_lost, target_city in (
            SpyReport.objects.filter(created_by_job__root_job_id=root.pk)
            .values_list("mission_id", "result_status", "agents_lost", "target_city_id")):
        lost += agents_lost or 0
        if mission_id not in MISSIONS:              # 1 is the infiltration itself: the spies' trip, not a reading
            continue
        reports += 1
        text = str(result or "").lower()
        success = "sucesso" in text or "success" in text
        reports_failed += 0 if success else 1
        row = missions.setdefault(int(mission_id), {"ok": 0, "failed": 0})
        row["ok" if success else "failed"] += 1
        if mission_id == 5 and target_city:
            stock_cities.add(target_city)
    training = _training_totals(root.pk, JobLog)

    return {
        "root_id": str(root.pk),
        "started_at": root.created_at,
        "rounds": round_count,
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
            {"id": mid, "name": MISSIONS[mid]["name"], "icon": static(MISSIONS[mid]["icon"]), **row}
            for mid, row in sorted(missions.items(), key=lambda item: list(MISSIONS).index(item[0]))
        ],
        "spies_lost": lost,
        "spies_trained": training["trained"],
        "training_gold": training["gold"],
        "training_crystal": training["crystal"],
        "reports": reports,
        "reports_failed": reports_failed,
        "stock_cities": len(stock_cities),
    }
