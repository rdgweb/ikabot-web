"""The economic balance of the internal market (N-111).

Every order kept says who bought, who sold, how much and at what price. Here that is added up:
what each account sold and bought and the gold left of it, who paid whom, what each resource
cost, and how much gold moved each day. Nothing is written; the orders are only read.
"""
from __future__ import annotations

import json
from datetime import timedelta

from django.db.models import Min
from django.utils import timezone

from apps.game.models import AccountSnapshot

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


def _name(game_account, account) -> str:
    if game_account is not None:
        return game_account.name or game_account.server_id
    return account.label if account is not None else "?"


def _party(parties: dict, game_account, account) -> dict:
    key = f"ga:{game_account.pk}" if game_account is not None else f"acc:{account.pk if account is not None else '?'}"
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

    for order in orders:
        amount = delivered_amount(order)
        if amount <= 0:
            if order.status in ("failed", "canceled"):
                totals["empty"] += 1
            continue
        price = max(0, int(order.unit_price or 0))
        gold = amount * price
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
        "prices": prices,
        "chart": chart,
        "chart_peak": peak,
    }
