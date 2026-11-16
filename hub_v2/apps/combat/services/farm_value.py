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


def barbarian_hourly(game_account_ids, hours: int = 24) -> dict[str, dict]:
    """N-194: what the barbarian farm of each game account made per hour over the last `hours`. per_hour is the gold
    alone (the income screen counts gold only, Rodrigo 15/11); loot and losses (in gold by the resources' values) go
    along for the farm's own screens. From the state of each Roubar Bárbaros workflow."""
    import time as _time
    from datetime import timedelta

    from django.utils import timezone

    from apps.accounts.models import GameAccount
    from apps.jobs.models import Job

    from .barbarian_planner import CITIZEN_VALUE, citizen_value

    ids = [str(i) for i in game_account_ids or [] if i]
    if not ids:
        return {}
    since = _time.time() - hours * 3600
    accounts = {str(ga.pk): ga for ga in GameAccount._base_manager.filter(pk__in=ids)}
    latest: dict = {}
    for job in (Job._base_manager.filter(game_account_id__in=ids, action_code=19, created_at__gte=timezone.now() - timedelta(days=3))
                .order_by("-created_at").only("workflow_id", "game_account_id", "inputs_json", "created_at")):
        latest.setdefault(job.workflow_id or job.pk, job)
    out: dict[str, dict] = {}
    for job in latest.values():
        try:
            inputs = json.loads(job.inputs_json or "{}") if isinstance(job.inputs_json, str) else (job.inputs_json or {})
        except (TypeError, ValueError):
            continue
        ga_id = str(job.game_account_id)
        values = resource_values(accounts.get(ga_id))
        row = out.setdefault(ga_id, {"per_hour": 0.0, "battles": 0, "gold": 0, "loot": 0.0, "losses": 0.0})
        for st in (inputs.get("islands_state") or {}).values():
            if not isinstance(st, dict):
                continue
            per_citizen = citizen_value(st.get("population") or {}) if st.get("population") else CITIZEN_VALUE
            per_unit = {str(u): int(c) for u, c in (st.get("unit_citizens") or {}).items()}
            for entry in st.get("ledger") or []:
                if not isinstance(entry, dict) or int(entry.get("at") or 0) < since or entry.get("loot") is None:
                    continue
                loot = loot_value(entry.get("loot_by_resource"), float(entry.get("gold") or 0), values, total=float(entry.get("loot") or 0))
                lost = sum((unit_value(int(u), values) + max(1, per_unit.get(str(u), 1)) * per_citizen) * int(q or 0)
                           for u, q in (entry.get("losses") or {}).items() if str(u).isdigit())
                row["battles"] += 1
                row["gold"] += int(entry.get("gold") or 0)
                row["loot"] += loot
                row["losses"] += lost
    for row in out.values():
        row["per_hour"] = round(row["gold"] / hours)
        row["loot"], row["losses"] = round(row["loot"]), round(row["losses"])
    return out
