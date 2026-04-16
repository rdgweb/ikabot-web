"""
Internal market matching and order creation service.

Flow:
  1. Caller provides buyer GameAccount + resource + amount.
  2. find_eligible_seller() locates a seller on a DIFFERENT node with
     enough stock (net of active construction reservations) and a Branch Office.
  3. create_internal_order() builds the InternalMarketOrder and the sell_job (802).
  4. When Runner 802 completes, the hub creates the buy_job (801) via the API.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from django.db.models import Sum

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from apps.jobs.models import ConstructionResourceReservation, Job
from .models import InternalMarketOrder

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Map resource_idx → snapshot city key
_IDX_TO_KEY = {0: "wood", 1: "wine", 2: "marble", 3: "glas", 4: "sulfur"}

# resource_idx → "resource" string used in Ikariam AJAX / ikabot
_IDX_TO_RESOURCE_STR = {0: "resource", 1: "1", 2: "2", 3: "3", 4: "4"}


# ── Snapshot helpers ────────────────────────────────────────────────────────


def _cities_from_snapshot(snap: AccountSnapshot) -> list[dict]:
    raw = snap.cities or {}
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        return [c for c in raw.values() if isinstance(c, dict)]
    return []


def _city_stock(city: dict, resource_idx: int) -> int:
    """Return the city's stored amount for resource_idx (0–4)."""
    key = _IDX_TO_KEY.get(resource_idx)
    if key is None:
        return 0
    return int(city.get(key) or 0)


def _find_branchoffice(city: dict) -> int:
    """Return the Branch Office position in the city, or -1 if absent."""
    buildings = city.get("buildings") or city.get("position") or []
    for b in buildings:
        if not isinstance(b, dict):
            continue
        if b.get("building") == "branchOffice":
            pos = b.get("position")
            if pos is not None:
                return int(pos)
            # Fallback: use list index
            try:
                return buildings.index(b)
            except ValueError:
                return -1
    return -1


def _active_reservation(seller_ga: GameAccount, city_id: int, resource_idx: int) -> int:
    """Sum of locally-reserved resource amounts in active construction plans."""
    resource_key = _IDX_TO_KEY.get(resource_idx, "")
    if not resource_key:
        return 0
    return (
        ConstructionResourceReservation.objects.filter(
            game_account=seller_ga,
            city_id=str(city_id),
            resource=resource_key,
            status="active",
        ).aggregate(total=Sum("reserved_local_amount"))["total"]
        or 0
    )


# ── Matching ────────────────────────────────────────────────────────────────


def find_eligible_seller(
    buyer_ga: GameAccount,
    resource_idx: int,
    amount: int,
) -> tuple[GameAccount | None, dict | None, int]:
    """Find a seller GameAccount + city that can supply the resource.

    Returns:
        (seller_ga, seller_city_dict, branchoffice_pos) or (None, None, -1).

    Rules:
    - Different node from buyer.
    - open_for_market=True, active=True, not blocked.
    - Has a snapshot with enough free stock in at least one city that has a Branch Office.
    """
    buyer_node = buyer_ga.node

    candidates = (
        GameAccount.objects.filter(open_for_market=True, active=True, blocked=False)
        .exclude(account__node=buyer_node)
        .select_related("account", "account__node")
    )

    for seller_ga in candidates:
        try:
            snap = AccountSnapshot.objects.filter(game_account=seller_ga).first()
        except Exception:
            continue
        if snap is None:
            continue

        for city in _cities_from_snapshot(snap):
            bo_pos = _find_branchoffice(city)
            if bo_pos < 0:
                continue

            stock = _city_stock(city, resource_idx)
            if stock < amount:
                continue

            city_id = city.get("id")
            if city_id is None:
                continue

            reserved = _active_reservation(seller_ga, int(city_id), resource_idx)
            if stock - reserved >= amount:
                return seller_ga, city, bo_pos

    return None, None, -1


def find_buyer_branchoffice(buyer_ga: GameAccount) -> tuple[int | None, int]:
    """Pick the first buyer city that has a Branch Office.

    Returns (city_id, branchoffice_pos) or (None, -1).
    """
    try:
        snap = AccountSnapshot.objects.filter(game_account=buyer_ga).first()
    except Exception:
        return None, -1
    if snap is None:
        return None, -1

    for city in _cities_from_snapshot(snap):
        bo_pos = _find_branchoffice(city)
        if bo_pos >= 0:
            city_id = city.get("id")
            if city_id is not None:
                return int(city_id), bo_pos
    return None, -1


# ── Order creation ──────────────────────────────────────────────────────────


def create_internal_order(
    buyer_ga: GameAccount,
    resource_idx: int,
    amount: int,
    unit_price: int = 12,
) -> InternalMarketOrder | None:
    """Create an InternalMarketOrder and queue the sell_job (802).

    Returns the order on success, or None if no eligible seller was found.
    """
    seller_ga, seller_city, seller_bo_pos = find_eligible_seller(
        buyer_ga, resource_idx, amount
    )
    if seller_ga is None:
        logger.warning(
            "No eligible seller for resource_idx=%s amount=%s buyer_ga=%s",
            resource_idx, amount, buyer_ga.pk,
        )
        return None

    buyer_city_id, buyer_bo_pos = find_buyer_branchoffice(buyer_ga)
    seller_city_id = int(seller_city["id"])

    order = InternalMarketOrder.objects.create(
        buyer_account=buyer_ga.account,
        buyer_game_account=buyer_ga,
        buyer_node=buyer_ga.account.node,
        buyer_city_id=buyer_city_id,
        buyer_branchoffice_pos=buyer_bo_pos,
        seller_account=seller_ga.account,
        seller_game_account=seller_ga,
        seller_node=seller_ga.account.node,
        seller_city_id=seller_city_id,
        seller_branchoffice_pos=seller_bo_pos,
        resource_idx=resource_idx,
        amount=amount,
        unit_price=unit_price,
        status="matched",
    )

    sell_job = Job.objects.create(
        account=seller_ga.account,
        game_account=seller_ga,
        node=seller_ga.account.node,
        action_code=802,
        inputs_json=json.dumps({
            "city_id": seller_city_id,
            "branchoffice_pos": seller_bo_pos,
            "resource_idx": resource_idx,
            "amount": amount,
            "unit_price": unit_price,
            "internal_order_id": str(order.pk),
        }),
        status="queued",
    )

    order.sell_job = sell_job
    order.status = "jobs_created"
    order.save(update_fields=["sell_job", "status", "updated_at"])

    logger.info(
        "InternalMarketOrder %s created: seller=%s city=%s res=%s amount=%s",
        order.pk, seller_ga, seller_city_id, resource_idx, amount,
    )
    return order


def create_buy_job(order: InternalMarketOrder) -> Job | None:
    """Create the buy_job (801) for an order whose sell_job has completed.

    Called by the hub API when Runner 802 signals completion.
    Returns the new Job, or None if the order has no buyer_game_account.
    """
    buyer_ga = order.buyer_game_account
    if buyer_ga is None:
        logger.error("Order %s has no buyer_game_account; cannot create buy_job", order.pk)
        return None

    buy_job = Job.objects.create(
        account=buyer_ga.account,
        game_account=buyer_ga,
        node=buyer_ga.account.node,
        action_code=801,
        inputs_json=json.dumps({
            "buyer_city_id": order.buyer_city_id,
            "buyer_branchoffice_pos": order.buyer_branchoffice_pos,
            "seller_city_id": order.seller_city_id,
            "seller_branchoffice_pos": order.seller_branchoffice_pos,
            "resource_idx": order.resource_idx,
            "amount": order.amount,
            "internal_order_id": str(order.pk),
        }),
        status="queued",
    )

    order.buy_job = buy_job
    order.status = "jobs_running"
    order.save(update_fields=["buy_job", "status", "updated_at"])

    logger.info(
        "buy_job %s created for order %s (buyer=%s)", buy_job.pk, order.pk, buyer_ga,
    )
    return buy_job
