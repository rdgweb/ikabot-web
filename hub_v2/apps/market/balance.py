"""The economic balance of the internal market (N-111).

Every order kept says who bought, who sold, how much and at what price. Here that is added up:
what each account sold and bought and the gold left of it, who paid whom, what each resource
cost, and how much gold moved each day. Nothing is written; the orders are only read.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import timedelta

from django.db.models import Min
from django.utils import timezone

from apps.game.models import AccountSnapshot
from core.catalogs import BUILDING_CATALOG

from .models import InternalMarketOrder, PublicMarketOffer

RESOURCES = InternalMarketOrder.RESOURCE_CHOICES
PERIODS = ((7, "7 dias"), (30, "30 dias"), (0, "Tudo"))
DEFAULT_DAYS = 30
CHART_DAYS = 14


def delivered_amount(order) -> int:
    """How much of an order really changed hands.

    A completed order delivered all of it. One that failed, was canceled or is still running may
    have delivered part: the buy job carries how much was already bought, leg by leg.
    """
    amount = max(0, int(order.amount or 0))
    if order.status == "completed":
        return amount
    job = order.buy_job
    if job is None:
        return 0
    raw = job.inputs_json
    try:
        inputs = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (TypeError, ValueError):
        return 0
    try:
        done = int((inputs or {}).get("order_completed_amount") or 0)
    except (TypeError, ValueError):
        done = 0
    return min(amount, max(0, done))


# what the construction wrote when it asked for the resource: "Tribunal | Esconderijo | missing wood=21887 for level 32"
_REASON = re.compile(r"^\s*(?P<city>[^|]+?)\s*\|\s*(?P<building>[^|]+?)\s*\|\s*missing\s+\w+=(?P<missing>\d+)\s+for level\s+(?P<level>\d+)", re.I)
STATES = {"done": "Entregue", "partial": "Em parte", "running": "Em andamento", "empty": "Sem entrega"}


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).lower().strip()


_BUILDINGS = {_plain(item["name"]): item for item in BUILDING_CATALOG.values()}


def why(order) -> dict | None:
    """The work that asked for the resource: city, building (as the game names it), level, how much was missing."""
    found = _REASON.match(order.reason_detail or "")
    if not found:
        return None
    building = _BUILDINGS.get(_plain(found.group("building"))) or {}
    return {
        "city": found.group("city").strip(),
        "building": building.get("name") or found.group("building").strip(),
        "icon": f"game/buildings/{building['icon']}" if building.get("icon") else "",
        "level": int(found.group("level")),
        "missing": int(found.group("missing")),
    }


def _failure(order) -> str:
    """Why an order ended without delivering, in the words it was closed with."""
    parts = [part.strip() for part in (order.result_note or "").split("|") if part.strip()]
    useful = [part for part in parts[1:] if not part.lower().startswith(("cleanup", "remain"))]
    return (useful[0] if useful else parts[0] if parts else "")[:160]


def short_gold(value: int) -> str:
    """Gold the way it fits in a cell: 8,5 mi, 420 mil, 900."""
    value = int(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} mi".replace(".", ",").replace(",0 mi", " mi")
    if value >= 10_000:
        return f"{round(value / 1000)} mil"
    return f"{value:,}".replace(",", ".")


def _city_names(cache: dict, game_account) -> dict:
    """{city id: name} of an account, from its snapshot, read once."""
    if game_account is None:
        return {}
    if game_account.pk not in cache:
        snapshot = AccountSnapshot.objects.filter(game_account=game_account).only("cities").first()
        raw = (snapshot.cities if snapshot else None) or []
        cities = raw.values() if isinstance(raw, dict) else raw
        cache[game_account.pk] = {str(city.get("id")): str(city.get("name") or "") for city in cities if isinstance(city, dict)}
    return cache[game_account.pk]


def _key(game_account, account) -> str:
    return f"ga:{game_account.pk}" if game_account is not None else f"acc:{account.pk if account is not None else '?'}"


def _name(game_account, account) -> str:
    if game_account is not None:
        return game_account.name or game_account.server_id
    return account.label if account is not None else "?"


def _party(parties: dict, game_account, account) -> dict:
    key = _key(game_account, account)
    if key not in parties:
        parties[key] = {
            "key": key,
            "game_account": game_account,
            "name": _name(game_account, account),
            "label": account.label if account is not None else "",
            "sold_gold": 0, "bought_gold": 0, "sales": 0, "purchases": 0,
            "sold": {idx: 0 for idx, _label in RESOURCES}, "bought": {idx: 0 for idx, _label in RESOURCES},
        }
    return parties[key]


def _percent(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole > 0 else 0


def build_balance(days: int = DEFAULT_DAYS, *, now=None) -> dict:
    """Everything the balance page shows, for the orders created in the last `days` (0 = all of them)."""
    now = now or timezone.now()
    orders = InternalMarketOrder.objects.select_related(
        "buyer_game_account", "seller_game_account", "buyer_account", "seller_account", "buy_job",
    ).exclude(seller_account=None)
    if days:
        orders = orders.filter(created_at__gte=now - timedelta(days=days))

    parties: dict[str, dict] = {}
    flows: dict[tuple, int] = {}
    resources = {idx: {"idx": idx, "label": label, "amount": 0, "gold": 0, "orders": 0, "low": 0, "high": 0} for idx, label in RESOURCES}
    by_day: dict = {}
    totals = {"gold": 0, "amount": 0, "orders": 0, "partial": 0, "empty": 0}
    pairs: dict[frozenset, dict] = {}
    works: dict[tuple, dict] = {}
    names: dict = {}
    cities: dict = {}

    for order in orders.order_by("-created_at"):
        amount = delivered_amount(order)
        price = max(0, int(order.unit_price or 0))
        gold = amount * price

        # every purchase between two accounts, delivered or not, under the pair it belongs to
        buyer_key, seller_key = _key(order.buyer_game_account, order.buyer_account), _key(order.seller_game_account, order.seller_account)
        names[buyer_key] = _name(order.buyer_game_account, order.buyer_account)
        names[seller_key] = _name(order.seller_game_account, order.seller_account)
        if order.status == "completed":
            state = "done"
        elif order.status in ("failed", "canceled"):
            state = "partial" if amount else "empty"
        else:
            state = "running"
        reason = why(order)
        target = order.target_city_id or order.buyer_city_id
        deal = {
            "pk": order.pk, "date": order.created_at, "buyer": names[buyer_key], "seller": names[seller_key], "buyer_key": buyer_key,
            "resource": order.resource_idx, "amount": amount, "ordered": int(order.amount or 0), "price": price, "gold": gold,
            "state": state, "state_label": STATES[state], "why": reason, "failure": _failure(order) if state in ("empty", "partial") else "",
            "from_city": _city_names(cities, order.seller_game_account).get(str(order.seller_city_id), ""),
            "to_city": (reason or {}).get("city") or _city_names(cities, order.buyer_game_account).get(str(target), ""),
        }
        pair = pairs.setdefault(frozenset((buyer_key, seller_key)), {"keys": (buyer_key, seller_key), "deals": [], "paid": {}, "empty": 0})
        pair["deals"].append(deal)
        if state == "empty":
            pair["empty"] += 1

        if amount <= 0:
            if order.status in ("failed", "canceled"):
                totals["empty"] += 1
            continue
        pair["paid"][buyer_key] = pair["paid"].get(buyer_key, 0) + gold

        # what it was for: the purchases of each work together
        work_key = (reason["city"], reason["building"]) if reason else ("", "")
        work = works.setdefault(work_key, {
            "city": work_key[0], "building": work_key[1] or "Sem motivo registrado", "icon": (reason or {}).get("icon", ""),
            "level": 0, "gold": 0, "orders": 0, "resources": {}, "sellers": {},
        })
        work["level"] = max(work["level"], (reason or {}).get("level", 0))
        work["gold"] += gold
        work["orders"] += 1
        work["resources"][order.resource_idx] = work["resources"].get(order.resource_idx, 0) + amount
        work["sellers"][names[seller_key]] = work["sellers"].get(names[seller_key], 0) + gold
        buyer = _party(parties, order.buyer_game_account, order.buyer_account)
        seller = _party(parties, order.seller_game_account, order.seller_account)
        buyer["bought_gold"] += gold
        buyer["purchases"] += 1
        buyer["bought"][order.resource_idx] = buyer["bought"].get(order.resource_idx, 0) + amount
        seller["sold_gold"] += gold
        seller["sales"] += 1
        seller["sold"][order.resource_idx] = seller["sold"].get(order.resource_idx, 0) + amount
        flows[(buyer["key"], seller["key"])] = flows.get((buyer["key"], seller["key"]), 0) + gold

        item = resources.get(order.resource_idx)
        if item is not None:
            item["amount"] += amount
            item["gold"] += gold
            item["orders"] += 1
            item["low"] = price if not item["low"] else min(item["low"], price)
            item["high"] = max(item["high"], price)

        day = timezone.localtime(order.created_at).date()
        by_day[day] = by_day.get(day, 0) + gold
        totals["gold"] += gold
        totals["amount"] += amount
        totals["orders"] += 1
        if order.status != "completed":
            totals["partial"] += 1

    # what each account has in the vault now, to read the balance against
    gold_now = {}
    game_accounts = [party["game_account"] for party in parties.values() if party["game_account"] is not None]
    for snapshot in AccountSnapshot.objects.filter(game_account__in=game_accounts).only("game_account_id", "base_snapshot"):
        gold_now[snapshot.game_account_id] = int((snapshot.base_snapshot or {}).get("gold") or 0)

    accounts = []
    for party in parties.values():
        turnover = party["sold_gold"] + party["bought_gold"]
        accounts.append({
            **party,
            "net": party["sold_gold"] - party["bought_gold"],
            "sold_pct": _percent(party["sold_gold"], turnover),
            "sold_list": [{"idx": idx, "amount": amount} for idx, amount in party["sold"].items() if amount],
            "bought_list": [{"idx": idx, "amount": amount} for idx, amount in party["bought"].items() if amount],
            "gold_now": gold_now.get(party["game_account"].pk) if party["game_account"] is not None else None,
        })
    accounts.sort(key=lambda item: (-item["net"], item["name"].lower()))

    # each pair of accounts that dealt: who ended up paying whom, and every purchase between them
    relations = []
    for pair in pairs.values():
        first, second = pair["keys"]
        one, other = pair["paid"].get(first, 0), pair["paid"].get(second, 0)
        payer, receiver = (first, second) if one >= other else (second, first)
        relations.append({
            "payer": names[payer], "receiver": names[receiver], "gold": abs(one - other), "gross": one + other,
            "even": one == other, "nothing": one + other == 0, "deals": pair["deals"], "count": len(pair["deals"]), "empty": pair["empty"],
        })
    relations.sort(key=lambda item: (-item["gross"], item["payer"].lower()))
    widest = max((item["gross"] for item in relations), default=0)
    for item in relations:
        item["pct"] = max(2, _percent(item["gross"], widest)) if item["gross"] else 0

    # who bought from whom at a glance: buyers down, sellers across, the gold in each crossing
    buyers = sorted({deal["buyer_key"] for pair in pairs.values() for deal in pair["deals"] if deal["gold"]}, key=lambda key: names[key].lower())
    sellers = sorted({key for (payer, key) in flows}, key=lambda key: names[key].lower())
    top_cell = max(flows.values(), default=0)
    matrix = {
        "sellers": [names[key] for key in sellers],
        "rows": [
            {"buyer": names[buyer], "total": sum(flows.get((buyer, seller), 0) for seller in sellers), "cells": [
                {"gold": flows.get((buyer, seller), 0), "short": short_gold(flows.get((buyer, seller), 0)) if flows.get((buyer, seller)) else "",
                 "alpha": f"{0.06 + 0.5 * _percent(flows.get((buyer, seller), 0), top_cell) / 100:.2f}" if flows.get((buyer, seller)) else "0",
                 "pct": _percent(flows.get((buyer, seller), 0), top_cell), "self": buyer == seller, "seller": names[seller]}
                for seller in sellers
            ]}
            for buyer in buyers
        ],
    }

    works_list = []
    for work in works.values():
        work["resources_list"] = [{"idx": idx, "amount": amount} for idx, amount in sorted(work["resources"].items()) if amount]
        work["sellers_list"] = [name for name, _gold in sorted(work["sellers"].items(), key=lambda item: -item[1])]
        works_list.append(work)
    works_list.sort(key=lambda item: -item["gold"])

    # who paid whom, with what went the other way already taken off
    net_flows = []
    seen = set()
    for (payer, receiver), gold in flows.items():
        if (payer, receiver) in seen:
            continue
        back = flows.get((receiver, payer), 0)
        seen.update({(payer, receiver), (receiver, payer)})
        if gold == back:
            continue
        source, target, net = (payer, receiver, gold - back) if gold > back else (receiver, payer, back - gold)
        net_flows.append({"payer": parties[source]["name"], "receiver": parties[target]["name"], "gold": net, "back": min(gold, back)})
    net_flows.sort(key=lambda item: -item["gold"])
    biggest = net_flows[0]["gold"] if net_flows else 0
    for flow in net_flows:
        flow["pct"] = max(2, _percent(flow["gold"], biggest))

    # what the general market asks for the same resource, as the last scans saw it
    outside = dict(
        PublicMarketOffer.objects.filter(kind="sell", is_internal=False, unit_price__gt=0)
        .values_list("resource_idx").annotate(low=Min("unit_price"))
    )
    prices = []
    for item in resources.values():
        if not item["amount"]:
            continue
        average = round(item["gold"] / item["amount"], 1)
        reference = outside.get(item["idx"])
        prices.append({
            **item,
            "average": int(average) if average == int(average) else average,
            "outside": reference,
            # the same amount bought outside at the lowest price seen there, less what was paid here
            "saved": int(reference * item["amount"] - item["gold"]) if reference else None,
        })

    span = min(days, CHART_DAYS) if days else CHART_DAYS
    today = timezone.localtime(now).date()
    chart = [{"date": today - timedelta(days=offset), "gold": by_day.get(today - timedelta(days=offset), 0)} for offset in range(span - 1, -1, -1)]
    peak = max((bar["gold"] for bar in chart), default=0)
    for bar in chart:
        bar["pct"] = max(2, _percent(bar["gold"], peak)) if bar["gold"] else 0

    return {
        "days": days,
        "totals": totals,
        "accounts": accounts,
        "flows": net_flows,
        "relations": relations,
        "matrix": matrix,
        "works": works_list,
        "prices": prices,
        "chart": chart,
        "chart_peak": peak,
    }
