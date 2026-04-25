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

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from apps.jobs.models import ConstructionResourceReservation, Job, JobLog
from apps.jobs.services.workflows import create_job_with_workflow

from .models import InternalMarketOrder

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_IDX_TO_KEY = {0: "wood", 1: "wine", 2: "marble", 3: "crystal", 4: "sulfur"}
_IDX_TO_RESOURCE_STR = {0: "resource", 1: "1", 2: "2", 3: "3", 4: "4"}


def _cities_from_snapshot(snap: AccountSnapshot) -> list[dict]:
    raw = snap.cities or {}
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        return [c for c in raw.values() if isinstance(c, dict)]
    return []


def _city_stock(city: dict, resource_idx: int) -> int:
    key = _IDX_TO_KEY.get(resource_idx)
    if key is None:
        return 0
    if key == "crystal":
        return int(city.get("crystal") or city.get("glas") or 0)
    return int(city.get(key) or 0)


def _find_branchoffice(city: dict) -> int:
    buildings = city.get("buildings") or city.get("position") or []
    for b in buildings:
        if not isinstance(b, dict):
            continue
        if b.get("building") == "branchOffice":
            pos = b.get("position")
            if pos is not None:
                return int(pos)
            try:
                return buildings.index(b)
            except ValueError:
                return -1
    return -1


def _active_reservation(seller_ga: GameAccount, city_id: int, resource_idx: int) -> int:
    resource_key = _IDX_TO_KEY.get(resource_idx, "")
    if not resource_key:
        return 0
    resource_keys = [resource_key]
    if resource_key == "crystal":
        resource_keys = ["crystal", "glas"]
    return (
        ConstructionResourceReservation.objects.filter(
            game_account=seller_ga,
            city_id=str(city_id),
            resource__in=resource_keys,
            status="active",
        ).aggregate(total=Sum("reserved_local_amount"))["total"]
        or 0
    )


def _city_name(city: dict | None, fallback: int | None = None) -> str:
    if isinstance(city, dict):
        raw = str(city.get("name") or "").strip()
        if raw:
            return raw
        city_id = city.get("id")
        if city_id not in (None, ""):
            return f"Cidade {city_id}"
    if fallback not in (None, ""):
        return f"Cidade {fallback}"
    return ""


def _resolve_job_chain(source_job_id) -> tuple[str | None, str | None]:
    if not source_job_id:
        return None, None
    parent = Job.objects.filter(pk=source_job_id).only("id", "root_job_id").first()
    if not parent:
        sid = str(source_job_id)
        return sid, sid
    return str(parent.pk), str(parent.root_job_id or parent.pk)


def find_eligible_seller(
    buyer_ga: GameAccount,
    resource_idx: int,
    amount: int,
) -> tuple[GameAccount | None, dict | None, int]:
    buyer_node = buyer_ga.node

    candidates = (
        GameAccount.objects.filter(open_for_market=True, active=True, blocked=False)
        .exclude(account__node=buyer_node)
        .select_related("account", "account__node")
        .order_by("?")
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
            min_stock = int(getattr(seller_ga, "market_min_stock", 0) or 0)
            if stock - reserved - min_stock >= amount:
                return seller_ga, city, bo_pos

    return None, None, -1


def find_buyer_branchoffice(
    buyer_ga: GameAccount,
    preferred_city_id: int | None = None,
) -> tuple[int | None, int]:
    try:
        snap = AccountSnapshot.objects.filter(game_account=buyer_ga).first()
    except Exception:
        return None, -1
    if snap is None:
        return None, -1

    cities = _cities_from_snapshot(snap)

    if preferred_city_id is not None:
        for city in cities:
            city_id = city.get("id")
            if city_id is None or int(city_id) != int(preferred_city_id):
                continue
            bo_pos = _find_branchoffice(city)
            if bo_pos >= 0:
                return int(city_id), bo_pos

    for city in cities:
        bo_pos = _find_branchoffice(city)
        if bo_pos >= 0:
            city_id = city.get("id")
            if city_id is not None:
                return int(city_id), bo_pos
    return None, -1


def create_internal_order(
    buyer_ga: GameAccount,
    resource_idx: int,
    amount: int,
    unit_price: int = 0,
    preferred_buyer_city_id: int | None = None,
    source_job_id: str | None = None,
    source_action_code: int | None = None,
    source_reason: str = "",
    reason_detail: str = "",
    production_eta_seconds: int | None = None,
    missing_resource_keys: str = "",
) -> InternalMarketOrder | None:
    min_gold = int(getattr(buyer_ga, "market_min_gold", 0) or 0)
    buyer_snap = None
    if min_gold > 0:
        try:
            buyer_snap = AccountSnapshot.objects.filter(game_account=buyer_ga).first()
            current_gold = int((buyer_snap.base_snapshot or {}).get("gold", 0)) if buyer_snap else 0
        except Exception:
            current_gold = 0
        if current_gold < min_gold:
            logger.warning(
                "Buyer %s gold %s < market_min_gold %s; skipping order creation",
                buyer_ga.pk, current_gold, min_gold,
            )
            return None
    elif buyer_snap is None:
        buyer_snap = AccountSnapshot.objects.filter(game_account=buyer_ga).first()

    seller_ga, seller_city, seller_bo_pos = find_eligible_seller(buyer_ga, resource_idx, amount)
    if seller_ga is None:
        logger.warning(
            "No eligible seller for resource_idx=%s amount=%s buyer_ga=%s",
            resource_idx, amount, buyer_ga.pk,
        )
        return None

    buyer_city_id, buyer_bo_pos = find_buyer_branchoffice(
        buyer_ga,
        preferred_city_id=preferred_buyer_city_id,
    )
    if buyer_city_id is None or buyer_bo_pos < 0:
        logger.warning(
            "Buyer %s has no eligible Branch Office city (preferred_city_id=%s)",
            buyer_ga.pk,
            preferred_buyer_city_id,
        )
        return None

    seller_city_id = int(seller_city["id"])
    buyer_city_name = _city_name(None, buyer_city_id)
    if buyer_snap:
        for city in _cities_from_snapshot(buyer_snap):
            if int(city.get("id") or 0) == int(buyer_city_id):
                buyer_city_name = _city_name(city, buyer_city_id)
                break
    seller_city_name = _city_name(seller_city, seller_city_id)
    sell_source_job_id, root_job_id = _resolve_job_chain(source_job_id)

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
        price_min=0,
        price_max=0,
        status="matched",
        source_action_code=source_action_code,
        source_reason=source_reason,
        reason_detail=reason_detail,
        production_eta_seconds=production_eta_seconds,
        missing_resource_keys=missing_resource_keys,
    )

    sell_source_job = Job.objects.filter(pk=sell_source_job_id).first() if sell_source_job_id else None
    sell_job = create_job_with_workflow(
        account=seller_ga.account,
        game_account=seller_ga,
        node=seller_ga.account.node,
        action_code=802,
        source_job=sell_source_job,
        inputs={
            "city_id": seller_city_id,
            "city_name": seller_city_name,
            "branchoffice_pos": seller_bo_pos,
            "resource_idx": resource_idx,
            "amount": amount,
            "unit_price": unit_price,
            "buyer_city_id": buyer_city_id,
            "buyer_city_name": buyer_city_name,
            "internal_order_id": str(order.pk),
        },
        status="queued",
        start_new_run=sell_source_job is not None,
        trigger_type="internal_market_sell",
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
    buyer_ga = order.buyer_game_account
    if buyer_ga is None:
        logger.error("Order %s has no buyer_game_account; cannot create buy_job", order.pk)
        return None

    buy_job = create_job_with_workflow(
        account=buyer_ga.account,
        game_account=buyer_ga,
        node=buyer_ga.account.node,
        action_code=801,
        source_job=order.sell_job,
        inputs={
            "buyer_city_id": order.buyer_city_id,
            "buyer_city_name": _city_name(None, order.buyer_city_id),
            "buyer_branchoffice_pos": order.buyer_branchoffice_pos,
            "seller_city_id": order.seller_city_id,
            "seller_city_name": _city_name(None, order.seller_city_id),
            "seller_branchoffice_pos": order.seller_branchoffice_pos,
            "resource_idx": order.resource_idx,
            "amount": order.amount,
            "internal_order_id": str(order.pk),
        },
        status="queued",
        start_new_run=False,
        trigger_type="internal_market_buy",
    )

    order.buy_job = buy_job
    order.status = "jobs_running"
    order.save(update_fields=["buy_job", "status", "updated_at"])

    logger.info("buy_job %s created for order %s (buyer=%s)", buy_job.pk, order.pk, buyer_ga)
    return buy_job


def cancel_internal_order(order: InternalMarketOrder) -> dict[str, int]:
    """Cancel an internal market order and any active jobs in its chain."""
    active_job_statuses = {"queued", "running", "scheduled"}

    direct_job_ids = [job_id for job_id in [order.sell_job_id, order.buy_job_id, order.redistribution_job_id] if job_id]
    root_ids = set(direct_job_ids)
    for root_id in direct_job_ids:
        root_job = Job.objects.filter(pk=root_id).only("pk", "root_job_id").first()
        if root_job and root_job.root_job_id:
            root_ids.add(root_job.root_job_id)

    active_jobs: list[Job] = []
    if root_ids:
        active_jobs = list(
            Job.objects.filter(root_job_id__in=root_ids, status__in=active_job_statuses)
            .only("pk", "status")
        )
        direct_active = list(
            Job.objects.filter(pk__in=direct_job_ids, status__in=active_job_statuses)
            .only("pk", "status")
        )
        seen = {job.pk for job in active_jobs}
        for job in direct_active:
            if job.pk not in seen:
                active_jobs.append(job)
                seen.add(job.pk)

    now = timezone.now()
    cancelled_job_ids = [job.pk for job in active_jobs]

    with transaction.atomic():
        if cancelled_job_ids:
            Job.objects.filter(pk__in=cancelled_job_ids).update(
                status="cancelled",
                finished_at=now,
                updated_at=now,
                lease_expires_at=None,
            )
            JobLog.objects.bulk_create(
                [
                    JobLog(
                        job_id=job_id,
                        level="warn",
                        message=f"Cancelado pelo mercado interno ao cancelar a ordem {order.pk}.",
                    )
                    for job_id in cancelled_job_ids
                ]
            )

        order.status = "canceled"
        order.result_note = "Cancelada manualmente no Mercado Interno."
        order.updated_at = now
        order.save(update_fields=["status", "result_note", "updated_at"])

    return {
        "jobs_cancelled": len(cancelled_job_ids),
    }
