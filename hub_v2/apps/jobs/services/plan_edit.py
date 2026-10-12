"""Edits a user makes to a running construction plan (N-108).

A plan lives in the inputs of its job as two lists that go together: ``construction_plan_json``
(what to build) and ``construction_plan_steps`` (the same steps with the cost and time of each
level). The agent builds from the second one, so an edit has to land on both; before this,
"priorizar" and "remover" changed only the first and the agent never saw them.

Priority is per level and ordered inside a city: priority 1 "Quartel até o 30" means that city
builds only the barracks until it reaches level 30, then priority 2, then back to the plan's
own strategy. Cutting is per level too: cutting level 30 of a step that goes to 35 leaves it
going to 29.
"""

from __future__ import annotations

RESOURCES = ("wood", "wine", "marble", "glas", "sulfur")


class PlanEditError(ValueError):
    """The edit asked for cannot be applied; the message is for the user."""


def _si(value, default=0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _lists(inputs: dict) -> tuple[list, list]:
    steps = inputs.get("construction_plan_json")
    previews = inputs.get("construction_plan_steps")
    return (steps if isinstance(steps, list) else []), (previews if isinstance(previews, list) else [])


def _by_index(items: list, index: int) -> dict | None:
    for position, item in enumerate(items, start=1):
        if isinstance(item, dict) and _si(item.get("index"), position) == index:
            return item
    return None


def _both(inputs: dict, index: int) -> list[dict]:
    """The step in each of the two lists (the plan one must exist)."""
    steps, previews = _lists(inputs)
    step = _by_index(steps, index)
    if step is None:
        raise PlanEditError("Etapa não encontrada no plano.")
    preview = _by_index(previews, index)
    return [step] + ([preview] if preview is not None else [])


def _city_priorities(steps: list, city_id: str) -> list[dict]:
    marked = [s for s in steps if isinstance(s, dict) and str(s.get("city_id") or "") == city_id and _si(s.get("priority_order")) > 0]
    return sorted(marked, key=lambda s: _si(s.get("priority_order")))


def _renumber(inputs: dict, city_id: str) -> None:
    """Keep the priorities of a city as 1, 2, 3... in both lists."""
    steps, previews = _lists(inputs)
    for order, step in enumerate(_city_priorities(steps, city_id), start=1):
        for item in (step, _by_index(previews, _si(step.get("index")))):
            if item is not None:
                item["priority_order"] = order


def set_priority(inputs: dict, index: int, level: int) -> str:
    """Build this step first in its city, until it reaches ``level``. Returns the message for the user."""
    items = _both(inputs, index)
    step = items[0]
    target = _si(step.get("target_level"))
    level = _si(level) or target
    if level < 1 or (target and level > target):
        raise PlanEditError(f"O nível tem que estar entre 1 e {target}, que é até onde o plano leva esse prédio.")
    city_id = str(step.get("city_id") or "")
    steps, _previews = _lists(inputs)
    order = _si(step.get("priority_order"))
    if order <= 0:
        order = len(_city_priorities(steps, city_id)) + 1
    for item in items:
        item.pop("priority", None)          # the old yes/no mark
        item["priority_order"] = order
        item["priority_level"] = level
    _renumber(inputs, city_id)
    order = _si(step.get("priority_order"))
    name = str(step.get("building_name") or step.get("building_id") or "prédio")
    return f"Prioridade {order} em {step.get('city_name') or city_id}: {name} até o nível {level}."


def clear_priority(inputs: dict, index: int) -> str:
    items = _both(inputs, index)
    city_id = str(items[0].get("city_id") or "")
    for item in items:
        for key in ("priority", "priority_order", "priority_level"):
            item.pop(key, None)
    _renumber(inputs, city_id)
    return "Prioridade retirada."


def cut_step(inputs: dict, index: int, level: int, *, current_level: int = 0) -> str:
    """Take ``level`` and everything above it out of a step. Returns the message for the user."""
    from .construction_preview import _format_duration

    items = _both(inputs, index)
    step = items[0]
    preview = items[1] if len(items) > 1 else None
    target = _si(step.get("target_level"))
    level = _si(level)
    if level < 1 or level > target:
        raise PlanEditError(f"O nível tem que estar entre 1 e {target}, que é até onde o plano leva esse prédio.")
    new_target = level - 1
    name = str(step.get("building_name") or step.get("building_id") or "prédio")
    city = str(step.get("city_name") or step.get("city_id") or "")
    floor = max(_si(current_level), _si((preview or {}).get("from_level")))
    steps, previews = _lists(inputs)
    if new_target <= floor:
        # nothing of the step is left to build: it leaves the plan
        city_id = str(step.get("city_id") or "")
        steps.remove(step)
        if preview is not None:
            previews.remove(preview)
        _renumber(inputs, city_id)
        recompute_summary(inputs)
        return f"{name} saiu do plano em {city}."

    for item in items:
        item["target_level"] = new_target
        if _si(item.get("priority_order")) > 0 and _si(item.get("priority_level")) > new_target:
            item["priority_level"] = new_target
    if preview is not None:
        rows = [row for row in (preview.get("level_rows") or []) if isinstance(row, dict) and _si(row.get("level")) <= new_target]
        preview["level_rows"] = rows
        totals = {key: sum(_si((row.get("costs") or {}).get(key)) for row in rows) for key in RESOURCES}
        reserved = {key: min(_si((preview.get("reserved_local") or {}).get(key)), totals[key]) for key in RESOURCES}
        preview["totals"] = totals
        preview["reserved_local"] = reserved
        preview["missing"] = {key: max(0, totals[key] - reserved[key]) for key in RESOURCES}
        preview["base_seconds"] = sum(_si(row.get("base_seconds")) for row in rows)
        preview["adjusted_seconds"] = sum(_si(row.get("adjusted_seconds")) for row in rows)
        preview["base_duration"] = _format_duration(preview["base_seconds"])
        preview["adjusted_duration"] = _format_duration(preview["adjusted_seconds"])
    recompute_summary(inputs)
    return f"{name} em {city} agora vai até o nível {new_target} (níveis {level} a {target} retirados)."


def recompute_summary(inputs: dict) -> None:
    """Totals of the plan after an edit, from what its steps still hold."""
    steps, previews = _lists(inputs)
    if not previews:
        return
    summary = dict(inputs.get("construction_summary") or {})
    for field in ("totals", "reserved_local", "missing"):
        summary[field] = {key: sum(_si((preview.get(field) or {}).get(key)) for preview in previews if isinstance(preview, dict)) for key in RESOURCES}
    summary["steps"] = len(steps)
    summary["base_seconds"] = sum(_si(preview.get("base_seconds")) for preview in previews if isinstance(preview, dict))
    summary["adjusted_seconds"] = sum(_si(preview.get("adjusted_seconds")) for preview in previews if isinstance(preview, dict))
    inputs["construction_summary"] = summary


def editor_state(inputs: dict) -> list[dict]:
    """What the panel needs to redraw its list after an edit: every step by its index."""
    steps, _previews = _lists(inputs)
    return [
        {
            "id": _si(step.get("index"), position),
            "target": _si(step.get("target_level")),
            "p_order": _si(step.get("priority_order")) or (1 if step.get("priority") is True else 0),
            "p_level": _si(step.get("priority_level")) or (_si(step.get("target_level")) if step.get("priority") is True else 0),
        }
        for position, step in enumerate(steps, start=1) if isinstance(step, dict)
    ]
