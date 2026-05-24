"""
Generals Bank service layer.

Handles cycle creation, task management, and bank activation logic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from apps.jobs.services.workflows import create_job_with_workflow

from .models import (
    GeneralsBankConfig,
    GeneralsBankCycle,
    GeneralsBankCycleTask,
    GeneralsBankTransaction,
)

if TYPE_CHECKING:
    from apps.jobs.models import Job

logger = logging.getLogger(__name__)

RESOURCE_KEYS = ["wood", "wine", "marble", "crystal", "sulfur"]


def _snapshot_cities(ga: GameAccount) -> list[dict]:
    snap = AccountSnapshot.objects.filter(game_account=ga).first()
    if snap is None:
        return []
    raw = snap.cities or {}
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        return [c for c in raw.values() if isinstance(c, dict)]
    return []


def _snapshot_gold(ga: GameAccount) -> int:
    snap = AccountSnapshot.objects.filter(game_account=ga).first()
    if snap is None:
        return 0
    try:
        return int((snap.base_snapshot or {}).get("gold", 0))
    except Exception:
        return 0


def _find_black_market_city(ga: GameAccount) -> dict | None:
    for city in _snapshot_cities(ga):
        buildings = city.get("buildings") or []
        for b in buildings:
            if isinstance(b, dict) and b.get("building") == "blackMarket":
                return city
    return None


def _city_name(city: dict | None, fallback=None) -> str:
    if isinstance(city, dict):
        name = str(city.get("name") or "").strip()
        if name:
            return name
    return str(fallback or "")


def get_bank_gold(config: GeneralsBankConfig) -> int:
    return _snapshot_gold(config.bank_game_account)


def determine_cycle_mode(config: GeneralsBankConfig) -> str:
    gold = get_bank_gold(config)
    if gold < config.min_gold_floor:
        return "liquidation"
    return "accumulation"


def get_active_cycle(config: GeneralsBankConfig) -> GeneralsBankCycle | None:
    return (
        config.cycles
        .exclude(status__in=["completed", "failed", "cancelled"])
        .order_by("-created_at")
        .first()
    )


def create_accumulation_cycle(
    config: GeneralsBankConfig,
    target_units: dict[str, int],
    manager_job: "Job | None" = None,
) -> GeneralsBankCycle:
    with transaction.atomic():
        cycle = GeneralsBankCycle.objects.create(
            bank_config=config,
            mode="accumulation",
            status="training",
            target_units=target_units,
            manager_job=manager_job,
        )
        _create_producer_tasks(cycle, target_units)
    return cycle


def create_liquidation_cycle(
    config: GeneralsBankConfig,
    manager_job: "Job | None" = None,
) -> GeneralsBankCycle:
    cycle = GeneralsBankCycle.objects.create(
        bank_config=config,
        mode="liquidation",
        status="bank_listing",
        manager_job=manager_job,
    )
    return cycle


def _create_producer_tasks(cycle: GeneralsBankCycle, target_units: dict[str, int]) -> None:
    producers = list(
        cycle.bank_config.producers.filter(is_active=True).select_related("producer_game_account")
    )
    if not producers:
        return

    unit_ids = list(target_units.keys())
    producers_count = len(producers)

    for idx, (unit_id_str, total_qty) in enumerate(target_units.items()):
        unit_id = int(unit_id_str)
        qty_per_producer = max(1, total_qty // producers_count)
        remainder = total_qty - qty_per_producer * producers_count

        for p_idx, producer in enumerate(producers):
            qty = qty_per_producer + (1 if p_idx < remainder else 0)
            if qty <= 0:
                continue

            ga = producer.producer_game_account
            bm_city = _find_black_market_city(ga)

            GeneralsBankCycleTask.objects.create(
                cycle=cycle,
                producer_game_account=ga,
                city_id=0,
                city_name="",
                bm_city_id=int(bm_city.get("id")) if bm_city else None,
                bm_city_name=_city_name(bm_city),
                unit_id=unit_id,
                unit_name="",
                quantity_target=qty,
                status="training",
            )


def advance_cycle_status(cycle: GeneralsBankCycle, new_status: str, note: str = "") -> None:
    cycle.status = new_status
    if note:
        cycle.result_note = note
    cycle.updated_at = timezone.now()
    cycle.save(update_fields=["status", "result_note", "updated_at"])


def all_tasks_listed(cycle: GeneralsBankCycle) -> bool:
    tasks = cycle.tasks.all()
    if not tasks.exists():
        return False
    return not tasks.exclude(status__in=["listed", "sold", "failed", "cancelled"]).exists()


def create_bank_buy_job(cycle: GeneralsBankCycle) -> "Job | None":
    config = cycle.bank_config
    ga = config.bank_game_account

    listed_tasks = cycle.tasks.filter(status="listed").select_related("producer_game_account")
    if not listed_tasks.exists():
        return None

    buy_inputs = {
        "cycle_id": str(cycle.pk),
        "bank_config_id": str(config.pk),
        "offers": [
            {
                "producer_ga_id": str(task.producer_game_account_id),
                "unit_id": task.unit_id,
                "quantity": task.quantity_target,
                "unit_price": task.unit_price,
                "bm_city_id": task.bm_city_id,
                "bm_city_name": task.bm_city_name,
                "task_id": str(task.pk),
            }
            for task in listed_tasks
        ],
    }

    job = create_job_with_workflow(
        account=ga.account,
        game_account=ga,
        node=ga.account.node,
        action_code=807,
        inputs=buy_inputs,
        status="queued",
        trigger_type="generals_bank_buy",
    )
    cycle.buy_job = job
    cycle.save(update_fields=["buy_job", "updated_at"])
    return job


def create_buyer_sell_jobs(cycle: GeneralsBankCycle, bank_inventory: list[dict]) -> list:
    """Create buy jobs on eligible buyer accounts for bank's listed units."""
    from apps.market.models import BlackMarketUnitQuote
    from django.db.models import Avg

    config = cycle.bank_config
    bank_ga = config.bank_game_account
    bank_node = bank_ga.account.node

    buyer_candidates = (
        GameAccount.objects
        .filter(open_for_market=True, active=True, blocked=False)
        .exclude(account__node=bank_node)
        .select_related("account", "account__node")
    )

    jobs_created = []
    for item in bank_inventory:
        unit_id = int(item.get("unit_id", 0))
        quantity = int(item.get("quantity", 0))
        unit_price = int(item.get("unit_price", 0))
        bm_city_id = int(item.get("bm_city_id", 0))
        if not unit_id or not quantity or not unit_price:
            continue

        for buyer_ga in buyer_candidates:
            buyer_gold = _snapshot_gold(buyer_ga)
            total_cost = quantity * unit_price
            if buyer_gold < total_cost:
                continue

            job = create_job_with_workflow(
                account=buyer_ga.account,
                game_account=buyer_ga,
                node=buyer_ga.account.node,
                action_code=804,
                inputs={
                    "unit_id": unit_id,
                    "quantity": quantity,
                    "max_price": unit_price,
                    "seller_city_id": bm_city_id,
                    "cycle_id": str(cycle.pk),
                },
                status="queued",
                trigger_type="generals_bank_liquidation_buy",
            )

            GeneralsBankTransaction.objects.create(
                cycle=cycle,
                direction="sell",
                unit_id=unit_id,
                quantity=quantity,
                unit_price=unit_price,
                gold_delta=quantity * unit_price,
                counterpart_ga=buyer_ga,
                status="pending",
                job=job,
            )
            jobs_created.append(job)
            break  # one buyer per unit batch

    return jobs_created


def record_buy_transaction(
    cycle: GeneralsBankCycle,
    unit_id: int,
    unit_name: str,
    quantity: int,
    unit_price: int,
    counterpart_ga: "GameAccount | None",
    job: "Job | None",
) -> GeneralsBankTransaction:
    return GeneralsBankTransaction.objects.create(
        cycle=cycle,
        direction="buy",
        unit_id=unit_id,
        unit_name=unit_name,
        quantity=quantity,
        unit_price=unit_price,
        gold_delta=-(quantity * unit_price),
        counterpart_ga=counterpart_ga,
        status="completed",
        job=job,
    )


def get_avg_unit_price(unit_id: int) -> int:
    from apps.market.models import BlackMarketUnitQuote
    from django.db.models import Avg
    result = (
        BlackMarketUnitQuote.objects
        .filter(unit_id=unit_id)
        .aggregate(avg=Avg("price_min"))
    )
    avg = result.get("avg")
    return int(avg) if avg else 0
