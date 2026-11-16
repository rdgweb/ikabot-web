"""Troops kept apart from the farm and from the Manter (N-194).

The assault team (Rodrigo, 15/11: "é importante também reservar a equipe") stays where it is: a Roubar
Bárbaros job never sends it and never moves it, and the Manter never gathers it into the main army.
Each Roubar Bárbaros job keeps its reservation in its inputs (reserve_units: {city_id: {unit_id: count}});
this module reads the active ones of an account.
"""
from __future__ import annotations

import json

ACTIVE = ("queued", "scheduled", "running")
BARBARIANS = 19


def _inputs(job) -> dict:
    raw = job.inputs_json
    try:
        return json.loads(raw or "{}") if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        return {}


def parse_reserve(raw) -> dict[str, dict[str, int]]:
    """{city_id: {unit_id: count}} from the job's input (a JSON text or a dict); nonsense is dropped."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
    out: dict[str, dict[str, int]] = {}
    for city, units in (raw or {}).items() if isinstance(raw, dict) else []:
        if not str(city).isdigit() or not isinstance(units, dict):
            continue
        kept = {str(u): int(q) for u, q in units.items() if str(u).isdigit() and str(q).lstrip("-").isdigit() and int(q) > 0}
        if kept:
            out[str(city)] = kept
    return out


def account_reservations(game_account_id: str, exclude_job_id: str = "") -> dict:
    """The troops reserved in each city by the account's active barbarian jobs (summed), and the islands each of the
    other jobs farms ({workflow: [island ids]}; the job asking is left out by its workflow)."""
    from apps.jobs.models import Job

    exclude_wf = None
    if exclude_job_id:
        exclude_wf = Job._base_manager.filter(pk=exclude_job_id).values_list("workflow_id", flat=True).first()
    reserved: dict[str, dict[str, int]] = {}
    islands: dict[str, list[str]] = {}
    for job in Job._base_manager.filter(game_account_id=game_account_id, action_code=BARBARIANS, status__in=ACTIVE):
        inputs = _inputs(job)
        for city, units in parse_reserve(inputs.get("reserve_units")).items():
            for uid, qty in units.items():
                reserved.setdefault(city, {})[uid] = reserved.get(city, {}).get(uid, 0) + qty
        if exclude_job_id and ((exclude_wf is not None and job.workflow_id == exclude_wf) or str(job.pk) == exclude_job_id):
            continue
        ids = [s.strip() for s in str(inputs.get("island_ids") or "").split(",") if s.strip().isdigit()]
        if ids:
            islands[str(job.workflow_id or job.pk)] = ids
    return {"reserved": reserved, "islands": islands}
