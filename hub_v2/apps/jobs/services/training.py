"""Gerenciar Treino Militar (ac=1005): one request covers troops and fleet (N-138).

The agent trains one kind per job (barracks or shipyard), so a request with both is split into one job per
kind, created in the same workflow: they show, pause and resume together.
"""
from __future__ import annotations

from core.catalogs import TRAINING_UNITS

KINDS = ("troops", "fleet")


def unit_kinds() -> dict[str, str]:
    """{unit id as text: "troops" | "fleet"}."""
    return {str(unit["id"]): kind for kind in KINDS for unit in TRAINING_UNITS[kind]}


def _only(units, kind: str, kinds: dict[str, str]) -> dict[str, int]:
    result = {}
    for unit_id, qty in (units or {}).items():
        try:
            qty = int(qty or 0)
        except (TypeError, ValueError):
            continue
        if qty > 0 and kinds.get(str(unit_id)) == kind:
            result[str(unit_id)] = qty
    return result


def kinds_in(inputs: dict) -> list[str]:
    """The kinds that have something to train or keep, in a fixed order."""
    kinds = unit_kinds()
    present = set()
    for key in ("units", "target_garrison"):
        for unit_id, qty in (inputs.get(key) or {}).items():
            try:
                if int(qty or 0) > 0 and str(unit_id) in kinds:
                    present.add(kinds[str(unit_id)])
            except (TypeError, ValueError):
                continue
    for targets in (inputs.get("city_targets") or {}).values():
        for unit_id, qty in (targets or {}).items():
            try:
                if int(qty or 0) > 0 and str(unit_id) in kinds:
                    present.add(kinds[str(unit_id)])
            except (TypeError, ValueError):
                continue
    return [kind for kind in KINDS if kind in present]


def split_by_kind(inputs: dict) -> list[dict]:
    """One inputs dict per kind with units (troops first). Nothing to train keeps the request as it came."""
    kinds_present = kinds_in(inputs)
    if not kinds_present:
        return [dict(inputs)]
    kinds = unit_kinds()
    chosen = str(inputs.get("building_type") or "")
    parts = []
    for kind in kinds_present:
        part = dict(inputs)
        part["building_type"] = kind
        if "units" in inputs:
            part["units"] = _only(inputs.get("units"), kind, kinds)
        if "target_garrison" in inputs:
            part["target_garrison"] = _only(inputs.get("target_garrison"), kind, kinds)
        if isinstance(inputs.get("city_targets"), dict):
            city_targets = {}
            for city_id, targets in inputs["city_targets"].items():
                only = _only(targets, kind, kinds)
                if only:
                    city_targets[str(city_id)] = only
            part["city_targets"] = city_targets
        if len(kinds_present) > 1 or kind != chosen:
            # the position sent belongs to the building on screen; the agent finds the other one itself
            part["position"] = 0
        parts.append(part)
    return parts


def initial_quantities(inputs: dict) -> dict:
    """What the form shows again (editing a job or a preset): base quantities and per-city targets."""
    base = {}
    for key in ("units", "target_garrison"):
        for unit_id, qty in (inputs.get(key) or {}).items():
            try:
                if int(qty or 0) > 0:
                    base[str(unit_id)] = int(qty)
            except (TypeError, ValueError):
                continue
    city_targets = {}
    for city_id, targets in (inputs.get("city_targets") or {}).items():
        clean = {}
        for unit_id, qty in (targets or {}).items():
            try:
                if int(qty or 0) > 0:
                    clean[str(unit_id)] = int(qty)
            except (TypeError, ValueError):
                continue
        if clean:
            city_targets[str(city_id)] = clean
    return {"units": base, "city_targets": city_targets, "city_ids": [str(c) for c in (inputs.get("city_ids") or [])]}


def distribute_parts(part: dict, cities: list[dict]) -> list[dict]:
    """Distribuir: the quantities split evenly across the chosen cities that have the building, one
    "train now" job per city (remainder handed out one by one, nothing lost)."""
    building = "has_shipyard" if part.get("building_type") == "fleet" else "has_barracks"
    able = {str(city.get("id")) for city in cities if city.get(building)}
    picked = [str(c) for c in (part.get("city_ids") or [])]
    chosen = [c for c in picked if c in able] if picked else sorted(able)
    units = part.get("units") or {}
    if not chosen or not units:
        return []
    jobs = []
    for index, city_id in enumerate(chosen):
        share = {}
        for unit_id, qty in units.items():
            qty = int(qty or 0)
            amount = qty // len(chosen) + (1 if index < qty % len(chosen) else 0)
            if amount > 0:
                share[str(unit_id)] = amount
        if share:
            job = {k: v for k, v in part.items() if k not in ("city_ids", "consolidate_city_id")}
            job.update({"mode": "once", "city_id": city_id, "units": share, "position": 0, "distributed": True})
            jobs.append(job)
    return jobs


PRESET_CITY_RULES = ("all", "training")


def apply_city_rule(part: dict, cities: list[dict]) -> dict:
    """A preset keeps "the same target in every city" or "only in the cities that train this kind"."""
    rule = part.pop("preset_cities", None)
    if rule not in PRESET_CITY_RULES or part.get("mode") != "maintain":
        return part
    building = "has_shipyard" if part.get("building_type") == "fleet" else "has_barracks"
    part["maintain_scope"] = "selected_uniform"
    part["city_ids"] = [str(city.get("id")) for city in cities if rule == "all" or city.get(building)]
    return part


def create_training_jobs(ga, inputs: dict, cities: list[dict] | None = None, *, created_by=None, trigger_type: str = "manual") -> int:
    """Create the request's jobs in one workflow (N-138): one per kind; Distribuir, one per city and kind."""
    from apps.jobs.services.workflows import create_job_with_workflow

    jobs: list[dict] = []
    for part in split_by_kind(inputs):
        part = apply_city_rule(part, cities or [])
        if part.get("mode") == "distribute":
            jobs.extend(distribute_parts(part, cities or []))
        else:
            jobs.append(part)
    first = None
    for job_inputs in jobs:
        job = create_job_with_workflow(
            account=ga.account, game_account=ga, node=ga.account.node, action_code=1005, inputs=job_inputs,
            status="queued", created_by=created_by, trigger_type=trigger_type,
            explicit_workflow=first.workflow if first else None,
            explicit_run=first.workflow_run if first else None,
        )
        first = first or job
    return len(jobs)
