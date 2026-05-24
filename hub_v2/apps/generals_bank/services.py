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


def _has_building(city: dict, building_name: str) -> bool:
    for building in city.get("buildings") or []:
        if isinstance(building, dict) and str(building.get("building") or "").strip() == building_name:
            return True
    return False


def _find_training_city_for_unit(ga: GameAccount, unit_id: int) -> tuple[dict | None, dict | None]:
    """Pick a producer city for a unit.

    Current flow prefers a city that already has both the required training
    building and a Black Market, so the bank cycle can stay self-contained
    without adding a transport stage.
    """
    building_name = "shipyard" if 200 <= int(unit_id) < 300 else "barracks"
    cities = _snapshot_cities(ga)
    bm_candidates: list[dict] = []
    training_candidates: list[dict] = []
    for city in cities:
        has_training = _has_building(city, building_name)
        has_bm = _has_building(city, "blackMarket")
        if has_training:
            training_candidates.append(city)
        if has_training and has_bm:
            bm_candidates.append(city)
    if bm_candidates:
        # Prefer the strongest same-city setup first.
        bm_candidates.sort(
            key=lambda city: max(
                (
                    int((b or {}).get("level") or 0)
                    for b in (city.get("buildings") or [])
                    if isinstance(b, dict)
                    and str(b.get("building") or "").strip() in {building_name, "blackMarket"}
                ),
                default=0,
            ),
            reverse=True,
        )
        chosen = bm_candidates[0]
        return chosen, chosen
    if training_candidates:
        return training_candidates[0], _find_black_market_city(ga)
    return None, _find_black_market_city(ga)


def _city_name(city: dict | None, fallback=None) -> str:
    if isinstance(city, dict):
        name = str(city.get("name") or "").strip()
        if name:
            return name
    return str(fallback or "")


def _normalize_units_map(raw: dict | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in (raw or {}).items():
        try:
            unit_id = str(int(key))
            qty = int(value)
        except Exception:
            continue
        if qty > 0:
            result[unit_id] = qty
    return result


def get_bank_gold(config: GeneralsBankConfig) -> int:
    return _snapshot_gold(config.bank_game_account)


def determine_cycle_mode(config: GeneralsBankConfig) -> str:
    gold = get_bank_gold(config)
    if gold < config.min_gold_floor:
        return "liquidation"
    return "accumulation"


def build_producer_execution_plan(config: GeneralsBankConfig) -> dict:
    producers = list(
        config.producers.filter(is_active=True).select_related("producer_game_account")
    )
    tasks: list[dict] = []
    aggregate: dict[str, int] = {}
    warnings: list[str] = []

    for producer in producers:
        ga = producer.producer_game_account
        template = _normalize_units_map(producer.production_template)
        if not template:
            warnings.append(f"{ga.name}: sem composicao configurada.")
            continue

        for unit_id_str, qty in template.items():
            unit_id = int(unit_id_str)
            training_city, bm_city = _find_training_city_for_unit(ga, unit_id)
            if not training_city:
                warnings.append(f"{ga.name}: sem cidade de treino para unidade {unit_id}.")
                continue
            if not bm_city:
                warnings.append(f"{ga.name}: sem cidade com Mercado Negro para unidade {unit_id}.")
                continue

            tasks.append({
                "producer_id": str(producer.pk),
                "producer_ga_id": str(ga.pk),
                "producer_name": ga.name,
                "unit_id": unit_id,
                "quantity_target": qty,
                "city_id": int(training_city.get("id") or 0),
                "city_name": _city_name(training_city),
                "bm_city_id": int(bm_city.get("id") or 0),
                "bm_city_name": _city_name(bm_city),
                "same_city_flow": int(training_city.get("id") or 0) == int(bm_city.get("id") or 0),
            })
            aggregate[unit_id_str] = aggregate.get(unit_id_str, 0) + qty

    return {
        "tasks": tasks,
        "aggregate_units": aggregate,
        "warnings": warnings,
    }


def get_active_cycle(config: GeneralsBankConfig) -> GeneralsBankCycle | None:
    return (
        config.cycles
        .exclude(status__in=["completed", "failed", "cancelled"])
        .order_by("-created_at")
        .first()
    )


def create_accumulation_cycle(
    config: GeneralsBankConfig,
    target_units: dict[str, int] | None = None,
    manager_job: "Job | None" = None,
) -> GeneralsBankCycle:
    plan = build_producer_execution_plan(config)
    effective_target_units = _normalize_units_map(target_units) or plan.get("aggregate_units") or {}
    if not effective_target_units:
        raise ValueError("no_producer_template")
    with transaction.atomic():
        cycle = GeneralsBankCycle.objects.create(
            bank_config=config,
            mode="accumulation",
            status="training",
            target_units=effective_target_units,
            manager_job=manager_job,
        )
        _create_producer_tasks(cycle, plan)
        create_producer_task_jobs(cycle)
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


def _create_producer_tasks(cycle: GeneralsBankCycle, plan: dict) -> None:
    tasks = list(plan.get("tasks") or [])
    if not tasks:
        return
    producer_map = {
        str(p.producer_game_account_id): p.producer_game_account
        for p in cycle.bank_config.producers.filter(is_active=True).select_related("producer_game_account")
    }
    for item in tasks:
        ga = producer_map.get(str(item.get("producer_ga_id") or ""))
        if not ga:
            continue
        GeneralsBankCycleTask.objects.create(
            cycle=cycle,
            producer_game_account=ga,
            city_id=int(item.get("city_id") or 0),
            city_name=str(item.get("city_name") or ""),
            bm_city_id=int(item.get("bm_city_id") or 0) or None,
            bm_city_name=str(item.get("bm_city_name") or ""),
            unit_id=int(item.get("unit_id") or 0),
            unit_name="",
            quantity_target=int(item.get("quantity_target") or 0),
            status="training",
        )


def create_producer_task_jobs(cycle: GeneralsBankCycle) -> list["Job"]:
    jobs_created: list["Job"] = []
    tasks = list(cycle.tasks.select_related("producer_game_account", "producer_game_account__account", "producer_game_account__account__node"))
    for task in tasks:
        ga = task.producer_game_account
        job = create_job_with_workflow(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=809,
            inputs={
                "cycle_id": str(cycle.pk),
                "bank_config_id": str(cycle.bank_config_id),
                "task_id": str(task.pk),
                "city_id": int(task.city_id or 0),
                "bm_city_id": int(task.bm_city_id or 0),
            },
            status="queued",
            trigger_type="generals_bank_producer_task",
        )
        task.training_job = job
        task.save(update_fields=["training_job", "updated_at"])
        jobs_created.append(job)
    return jobs_created


def resolve_buyer_city_id(config: GeneralsBankConfig) -> int:
    if int(config.buyer_city_id or 0) > 0:
        return int(config.buyer_city_id)
    for city in _snapshot_cities(config.bank_game_account):
        if _has_building(city, "branchOffice"):
            return int(city.get("id") or 0)
    return 0


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

    buyer_city_id = resolve_buyer_city_id(config)
    if buyer_city_id <= 0:
        advance_cycle_status(cycle, "failed", "Banco sem cidade com Branch Office para comprar.")
        return None

    buy_inputs = {
        "cycle_id": str(cycle.pk),
        "bank_config_id": str(config.pk),
        "buyer_city_id": buyer_city_id,
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
