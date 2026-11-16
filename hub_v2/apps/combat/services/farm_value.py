"""What the farm's resources are worth, one by one (N-194).

The farm used to add wood, wine, marble, crystal and sulfur as if one of each were worth the same.
They are not, and the market is not the measure (Rodrigo, 15/11): what matters is what the accounts
need. The order, from the most to the least worth:

    marble > crystal > sulfur > wine > wood      (the account's wine production covers its use)
    wine > marble > crystal > sulfur > wood      (it does not: the taverns run short)

Wood is what matters least. Each place in the order is worth a step of gold per unit (LADDER), so the
farm weighs everything in gold:

- a resource: its step of the ladder;
- a citizen: the tax it stops paying until the town grows it back (barbarian_planner.citizen_value);
- a unit: the resources it takes to train again, plus its citizens;
- a loot: each resource by its step, plus the gold.

A setting (barbarian_resource_values, JSON {"wood": 1, ...}) overrides the ladder when Rodrigo wants.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

RESOURCES = ("wood", "wine", "marble", "crystal", "sulfur")
ORDER_WINE_COVERED = ("marble", "crystal", "sulfur", "wine", "wood")
ORDER_WINE_SHORT = ("wine", "marble", "crystal", "sulfur", "wood")
LADDER = (3.0, 2.5, 2.0, 1.5, 1.0)     # gold per unit, from the first of the order to the last (wood = 1, as before)
DEFAULT_VALUES = dict(zip(ORDER_WINE_COVERED, LADDER))
SETTING_KEY = "barbarian_resource_values"


def wine_short(game_account) -> bool | None:
    """The account's wine production does not cover what its taverns serve (None when the snapshot does not say)."""
    try:
        snap = game_account.snapshot
    except Exception:  # noqa: BLE001 - no snapshot, no answer
        return None
    produced = used = 0.0
    seen = False
    for city in (getattr(snap, "cities", None) or []):
        if not isinstance(city, dict):
            continue
        if str(city.get("tradegood")) == "1":
            produced += float(city.get("tradegood_production_per_hour") or 0)
        if city.get("wine_consumption") is not None:
            used += float(city.get("wine_consumption") or 0)
            seen = True
    return (produced < used) if seen else None


def resource_values(game_account=None) -> dict[str, float]:
    """Gold per unit of each resource for this account: its place in the order times the ladder."""
    order = ORDER_WINE_SHORT if game_account is not None and wine_short(game_account) else ORDER_WINE_COVERED
    values = dict(zip(order, LADDER))
    try:
        from apps.settings_app.utils import get_setting

        chosen = json.loads(get_setting(SETTING_KEY, "") or "{}") or {}
        for name in RESOURCES:
            if float(chosen.get(name) or 0) > 0:
                values[name] = float(chosen[name])
    except Exception:  # noqa: BLE001 - a bad setting is ignored
        logger.debug("Bad %s setting", SETTING_KEY, exc_info=True)
    return values


def unit_resources(uid: int) -> dict[str, int]:
    """The resources one unit takes to train (the catalog's, as the account's barracks showed them)."""
    from core.catalogs import TRAINING_UNITS

    for unit in TRAINING_UNITS.get("troops", []):
        if int(unit["id"]) == int(uid):
            return {name: int(unit.get(name) or 0) for name in RESOURCES if int(unit.get(name) or 0)}
    return {}


def resources_value(amounts: dict, values: dict | None = None) -> float:
    """Gold worth of {resource: amount}."""
    values = values or DEFAULT_VALUES
    return float(sum(float(values.get(name, 0)) * float(qty or 0) for name, qty in (amounts or {}).items()))


def unit_value(uid: int, values: dict | None = None) -> float:
    """Gold worth of the resources one unit takes to train again (its citizens apart)."""
    return resources_value(unit_resources(uid), values)


def losses_resources(losses: dict) -> dict[str, int]:
    """{resource: amount} that training the losses again takes."""
    out = {name: 0 for name in RESOURCES}
    for uid, qty in (losses or {}).items():
        for name, amount in unit_resources(int(uid)).items():
            out[name] += amount * int(qty or 0)
    return {k: v for k, v in out.items() if v}


def loot_value(by_resource, gold: float = 0, values: dict | None = None, total: float = 0) -> float:
    """Gold worth of a loot: each resource by its value, plus the gold. With only the total known (old entries), the
    resources at the average value."""
    values = values or DEFAULT_VALUES
    amounts = as_resources(by_resource)
    if amounts:
        return resources_value(amounts, values) + float(gold or 0)
    average = sum(float(values[name]) for name in RESOURCES) / len(RESOURCES)
    return float(total or 0) * average + float(gold or 0)


def as_resources(raw) -> dict[str, int]:
    """{resource: amount} from the game's list (wood, wine, marble, crystal, sulfur) or a dict by name or index."""
    if isinstance(raw, (list, tuple)):
        return {RESOURCES[i]: int(q or 0) for i, q in enumerate(raw[:5]) if int(q or 0)}
    if isinstance(raw, dict):
        out = {}
        for key, qty in raw.items():
            name = RESOURCES[int(key)] if str(key).isdigit() and int(key) < 5 else ("crystal" if str(key) == "glass" else str(key))
            if name in RESOURCES and int(qty or 0):
                out[name] = out.get(name, 0) + int(qty)
        return out
    return {}


def coverage(loot_by_resource, losses: dict) -> list[dict]:
    """Per resource: what the loot brought and what training the losses again takes."""
    got = as_resources(loot_by_resource)
    need = losses_resources(losses)
    return [{"resource": name, "loot": got.get(name, 0), "need": need.get(name, 0),
             "covered": got.get(name, 0) >= need.get(name, 0)}
            for name in RESOURCES if got.get(name) or need.get(name)]
