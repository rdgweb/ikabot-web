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
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import GameAccount
from apps.game.models import AccountSnapshot
from apps.jobs.models import ConstructionResourceReservation, Job, JobLog
from apps.jobs.services.workflows import create_job_with_workflow
from apps.telegram.services.bot_api import send_message, edit_message_text

from .models import ConstructionMarketIntervention, InternalMarketOrder

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_IDX_TO_KEY = {0: "wood", 1: "wine", 2: "marble", 3: "crystal", 4: "sulfur"}
_IDX_TO_RESOURCE_STR = {0: "resource", 1: "1", 2: "2", 3: "3", 4: "4"}
_RESOURCE_IDX_TO_INPUT_KEY = {0: "wood", 1: "wine", 2: "marble", 3: "crystal", 4: "sulfur"}
_MARKET_VISIBILITY_SAFETY_MARGIN = 1
_INTERVENTION_ACTIVE_STATUSES = ("pending", "approved", "sell_queued")


@dataclass(slots=True)
class InternalOrderCreateResult:
    ok: bool
    code: str
    order: InternalMarketOrder | None = None
    detail: str = ""
    buyer_city_id: int | None = None
    target_city_id: int | None = None

    @property
    def error(self) -> str:
        return self.code if not self.ok else ""


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


def _branchoffice_capacity(level: int | None) -> int:
    """Total resources that can be listed for sale in this Branch Office."""
    try:
        level_int = int(level or 0)
    except Exception:
        level_int = 0
    if level_int <= 0:
        return 0
    if level_int < 40:
        return 400 * level_int * level_int
    return int(608_400 * (1.0533241 ** (level_int - 39)))


def _market_resource_amounts(city: dict) -> dict[str, int]:
    raw = city.get("market_resources") if isinstance(city.get("market_resources"), dict) else {}
    amounts: dict[str, int] = {}
    for key in ("resource", "1", "2", "3", "4"):
        try:
            amounts[key] = max(0, int(float(raw.get(key) or 0)))
        except Exception:
            amounts[key] = 0
    return amounts


def _market_offer_total(city: dict) -> int:
    return sum(_market_resource_amounts(city).values())


def _seller_market_free_capacity(city: dict) -> int | None:
    info = _find_branchoffice_info(city)
    if not info or info.get("level") is None:
        return None
    capacity = _branchoffice_capacity(info.get("level"))
    if capacity <= 0:
        return None
    return max(0, capacity - _market_offer_total(city))


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


def _find_branchoffice_info(city: dict) -> dict | None:
    buildings = city.get("buildings") or city.get("position") or []
    for b in buildings:
        if not isinstance(b, dict):
            continue
        if b.get("building") != "branchOffice":
            continue
        pos = b.get("position")
        if pos is None:
            try:
                pos = buildings.index(b)
            except ValueError:
                return None
        raw_level = b.get("level")
        level = None
        if raw_level not in (None, ""):
            try:
                level = int(raw_level)
            except Exception:
                level = None
        max_range = math.ceil(level / 2) if level and level > 0 else None
        effective_range = None
        if max_range is not None:
            effective_range = max(1, max_range - _MARKET_VISIBILITY_SAFETY_MARGIN)
        return {
            "position": int(pos),
            "level": level,
            "max_range": max_range,
            "effective_range": effective_range,
        }
    return None


def _city_axis_distance(city_a: dict | None, city_b: dict | None) -> int | None:
    if not isinstance(city_a, dict) or not isinstance(city_b, dict):
        return None
    try:
        ax = int(city_a.get("x"))
        ay = int(city_a.get("y"))
        bx = int(city_b.get("x"))
        by = int(city_b.get("y"))
    except Exception:
        return None
    return max(abs(ax - bx), abs(ay - by))


def _city_is_visible_for_market(buyer_city: dict, seller_city: dict | None) -> bool:
    if seller_city is None:
        return True
    info = _find_branchoffice_info(buyer_city)
    if not info:
        return False
    effective_range = info.get("effective_range")
    if effective_range is None:
        return True
    distance = _city_axis_distance(buyer_city, seller_city)
    if distance is None:
        return True
    return distance <= int(effective_range)


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


def _estimate_committed_buy_gold(buyer_ga: GameAccount) -> int:
    """Sum estimated gold cost of all active buy orders for this buyer.

    Used to prevent creating new orders when pending orders will already
    exhaust gold below the min_gold floor.
    """
    total = 0
    for order in InternalMarketOrder.objects.filter(
        buyer_game_account=buyer_ga,
        status__in=["matched", "jobs_created", "jobs_running"],
    ):
        price = order.price_max or order.unit_price or 0
        if price > 0:
            total += order.amount * price
    return total


def _available_gold_for_buyer(game_account: GameAccount) -> int:
    return buyer_market_gold_position(game_account)["available"]


def buyer_market_gold_position(game_account: GameAccount) -> dict[str, int]:
    snap = _load_snapshot(game_account)
    current_gold = 0
    if snap is not None:
        try:
            current_gold = int((snap.base_snapshot or {}).get("gold", 0))
        except Exception:
            current_gold = 0
    reserved_gold = _estimate_committed_buy_gold(game_account)
    available_gold = current_gold - reserved_gold
    return {
        "current": current_gold,
        "reserved": reserved_gold,
        "available": available_gold,
        "minimum": int(getattr(game_account, "market_min_gold", 0) or 0),
    }


def _has_gold_for_market_order(game_account: GameAccount, *, amount: int, unit_price: int) -> tuple[bool, dict[str, int]]:
    position = buyer_market_gold_position(game_account)
    required_gold = max(0, int(amount)) * max(0, int(unit_price))
    position["required"] = required_gold
    if position["available"] - required_gold < position["minimum"]:
        return False, position
    return True, position


def _format_gold_position_detail(position: dict[str, int]) -> str:
    parts = [
        f"ouro atual {_fmt_market_gold(position.get('current', 0))}",
        f"reservado {_fmt_market_gold(position.get('reserved', 0))}",
        f"disponivel {_fmt_market_gold(position.get('available', 0))}",
    ]
    required = int(position.get("required", 0) or 0)
    if required > 0:
        parts.append(f"compra {_fmt_market_gold(required)}")
    parts.append(f"minimo {_fmt_market_gold(position.get('minimum', 0))}")
    return "; ".join(parts)


def _fmt_market_gold(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


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


def _load_snapshot(game_account: GameAccount | None) -> AccountSnapshot | None:
    if game_account is None:
        return None
    try:
        return AccountSnapshot.objects.filter(game_account=game_account).first()
    except Exception:
        return None


def _resolve_target_city_id(
    *,
    target_city_id: int | None,
    preferred_buyer_city_id: int | None,
    buyer_city_id: int | None,
) -> int | None:
    if target_city_id not in (None, ""):
        return int(target_city_id)
    if preferred_buyer_city_id not in (None, ""):
        return int(preferred_buyer_city_id)
    if buyer_city_id not in (None, ""):
        return int(buyer_city_id)
    return None


def _find_city_in_snapshot(snap: AccountSnapshot | None, city_id: int | None) -> dict | None:
    if snap is None or city_id in (None, ""):
        return None
    for city in _cities_from_snapshot(snap):
        raw_city_id = city.get("id")
        if raw_city_id is None:
            continue
        if int(raw_city_id) == int(city_id):
            return city
    return None


def _result_note(prefix: str, detail: str = "") -> str:
    text = str(prefix or "").strip()
    suffix = str(detail or "").strip()
    if text and suffix:
        return f"{text} | {suffix}"
    return text or suffix


def _extract_internal_order_id_from_note(note: str) -> str:
    for chunk in str(note or "").split("|"):
        part = str(chunk or "").strip()
        if not part.startswith("internal_order_id="):
            continue
        return part.split("=", 1)[1].strip()
    return ""


def _set_order_terminal_state(order: InternalMarketOrder, *, status: str, note: str) -> bool:
    if order.status in {"completed", "failed", "canceled"}:
        return False
    order.status = status
    order.result_note = note
    order.updated_at = timezone.now()
    order.save(update_fields=["status", "result_note", "updated_at"])
    return True


def _published_internal_offer_orders(order: InternalMarketOrder):
    return InternalMarketOrder.objects.filter(
        seller_game_account=order.seller_game_account,
        seller_city_id=order.seller_city_id,
        resource_idx=order.resource_idx,
        sell_job__status="finished",
    ).filter(Q(pk=order.pk) | ~Q(status__in=("completed", "canceled")))


def _has_active_cleanup_job(order: InternalMarketOrder) -> bool:
    token = f'"cleanup_for_order_id": "{order.pk}"'
    return Job.objects.filter(
        action_code=802,
        status__in=("queued", "scheduled", "running"),
        inputs_json__contains=token,
    ).exists()


def schedule_internal_offer_cleanup(order: InternalMarketOrder, *, note: str = "") -> Job | None:
    """Schedule a safe live-checked cleanup for a bot-managed offer.

    Branch Office offers are per city/resource totals. The agent will read the live
    current offer total and only mutate it when it matches expected_current_total.
    """
    if order.seller_game_account is None or order.seller_city_id is None or order.seller_branchoffice_pos is None:
        return None
    if order.sell_job_id is None or getattr(order.sell_job, "status", "") != "finished":
        return None
    if _has_active_cleanup_job(order):
        return None

    published_orders = list(_published_internal_offer_orders(order))
    expected_total = sum(max(0, int(item.amount or 0)) for item in published_orders)
    remaining_total = sum(
        max(0, int(item.amount or 0))
        for item in published_orders
        if item.pk != order.pk
    )
    offer_mode = "replace" if remaining_total > 0 else "clear"
    seller_snap = _load_snapshot(order.seller_game_account)
    seller_city = _find_city_in_snapshot(seller_snap, order.seller_city_id)
    cleanup_job = create_job_with_workflow(
        account=order.seller_game_account.account,
        game_account=order.seller_game_account,
        node=order.seller_game_account.account.node,
        action_code=802,
        source_job=order.buy_job or order.sell_job,
        inputs={
            "city_id": order.seller_city_id,
            "city_name": _city_name(seller_city, order.seller_city_id),
            "branchoffice_pos": order.seller_branchoffice_pos,
            "resource_idx": order.resource_idx,
            "amount": int(remaining_total),
            "unit_price": int(order.unit_price or 0),
            "offer_mode": offer_mode,
            "cleanup_only": True,
            "cleanup_for_order_id": str(order.pk),
            "expected_current_total": int(expected_total),
            "internal_order_id": str(order.pk),
        },
        status="queued",
        start_new_run=False,
        trigger_type="internal_market_cleanup",
    )
    order.result_note = _result_note(
        order.result_note,
        f"cleanup_job_id={cleanup_job.pk} remaining_total={remaining_total}{(' | ' + note) if note else ''}",
    )
    order.save(update_fields=["result_note", "updated_at"])
    logger.info("cleanup_job %s created for internal order %s", cleanup_job.pk, order.pk)
    return cleanup_job


def _create_redistribution_job(order: InternalMarketOrder) -> Job | None:
    buyer_ga = order.buyer_game_account
    if buyer_ga is None or order.buyer_city_id in (None, "") or order.target_city_id in (None, ""):
        return None

    resource_input_key = _RESOURCE_IDX_TO_INPUT_KEY.get(order.resource_idx)
    if not resource_input_key:
        return None

    buyer_snap = _load_snapshot(buyer_ga)
    from_city = _find_city_in_snapshot(buyer_snap, order.buyer_city_id)
    to_city = _find_city_in_snapshot(buyer_snap, order.target_city_id)

    payload = {
        "from_city": str(order.buyer_city_id),
        "from_city_name": _city_name(from_city, order.buyer_city_id),
        "to_city": str(order.target_city_id),
        "to_city_name": _city_name(to_city, order.target_city_id),
        "transport_load_percent": "100",
        "confirm_arrival": True,
        "confirmation_margin_minutes": 2,
        "internal_order_id": str(order.pk),
        resource_input_key: int(order.amount),
    }

    redistribution_job = create_job_with_workflow(
        account=buyer_ga.account,
        game_account=buyer_ga,
        node=buyer_ga.account.node,
        action_code=2,
        source_job=order.buy_job,
        inputs=payload,
        status="queued",
        start_new_run=False,
        trigger_type="internal_market_redistribution",
    )
    order.redistribution_job = redistribution_job
    order.status = "jobs_running"
    order.result_note = _result_note(
        "Compra concluida; redistribuicao em andamento.",
        f"{payload['from_city_name']} -> {payload['to_city_name']}",
    )
    order.save(update_fields=["redistribution_job", "status", "result_note", "updated_at"])
    logger.info("redistribution_job %s created for order %s", redistribution_job.pk, order.pk)
    return redistribution_job


def complete_internal_order(order: InternalMarketOrder) -> dict[str, object]:
    if order.status == "completed":
        return {"ok": True, "status": "completed", "completed": True, "created_redistribution": False}
    if order.status in {"failed", "canceled"}:
        return {"ok": False, "status": order.status, "completed": False, "created_redistribution": False}

    target_city_id = _resolve_target_city_id(
        target_city_id=order.target_city_id,
        preferred_buyer_city_id=None,
        buyer_city_id=order.buyer_city_id,
    )
    if target_city_id is not None and order.buyer_city_id is not None and int(target_city_id) != int(order.buyer_city_id):
        if order.redistribution_job_id:
            return {
                "ok": True,
                "status": order.status,
                "completed": False,
                "created_redistribution": False,
                "redistribution_job_id": str(order.redistribution_job_id),
            }
        redistribution_job = _create_redistribution_job(order)
        if redistribution_job is None:
            _set_order_terminal_state(
                order,
                status="failed",
                note="Compra concluida, mas nao foi possivel criar a redistribuicao interna.",
            )
            return {"ok": False, "status": "failed", "completed": False, "created_redistribution": False}
        return {
            "ok": True,
            "status": order.status,
            "completed": False,
            "created_redistribution": True,
            "redistribution_job_id": str(redistribution_job.pk),
        }

    _set_order_terminal_state(order, status="completed", note="Compra interna concluida.")
    logger.info("InternalMarketOrder %s marked as completed", order.pk)
    return {"ok": True, "status": "completed", "completed": True, "created_redistribution": False}


def reconcile_internal_order_for_job(job: Job, *, terminal_status: str, note: str = "") -> InternalMarketOrder | None:
    try:
        order = InternalMarketOrder.objects.select_related("buyer_game_account", "buyer_game_account__account").get(
            Q(sell_job=job) | Q(buy_job=job) | Q(redistribution_job=job)
        )
    except Exception:
        order = None
    if order is None:
        try:
            inputs = job.inputs_json if isinstance(job.inputs_json, dict) else json.loads(job.inputs_json or "{}")
        except Exception:
            inputs = {}
        internal_order_id = str(inputs.get("internal_order_id") or "").strip()
        if not internal_order_id:
            return None
        order = InternalMarketOrder.objects.filter(pk=internal_order_id).first()
        if order is None:
            return None

    try:
        inputs = job.inputs_json if isinstance(job.inputs_json, dict) else json.loads(job.inputs_json or "{}")
    except Exception:
        inputs = {}
    internal_order_id = str(inputs.get("internal_order_id") or "").strip()
    note_text = str(note or "").strip()

    def _active_buy_retry_child(current_job: Job) -> Job | None:
        return (
            Job.objects.filter(
                source_job_id=current_job.pk,
                action_code=current_job.action_code,
                status__in=("queued", "scheduled", "running"),
            )
            .order_by("-created_at")
            .first()
        )
    if job.pk == order.sell_job_id:
        if terminal_status == "error":
            _set_order_terminal_state(order, status="failed", note=_result_note("Falha na publicacao da oferta interna.", note_text))
        elif terminal_status == "cancelled":
            _set_order_terminal_state(order, status="canceled", note=_result_note("Oferta interna cancelada.", note_text))
        return order

    is_buy_chain_job = (
        int(job.action_code) == 801
        and (
            job.pk == order.buy_job_id
            or (internal_order_id and internal_order_id == str(order.pk))
        )
    )
    if is_buy_chain_job:
        if terminal_status == "finished":
            retry_job = _active_buy_retry_child(job)
            if retry_job is not None:
                updates: list[str] = ["updated_at"]
                if order.buy_job_id != retry_job.pk:
                    order.buy_job = retry_job
                    updates.append("buy_job")
                if order.status != "jobs_running":
                    order.status = "jobs_running"
                    updates.append("status")
                retry_note = "Compra interna reagendada; aguardando nova tentativa."
                if order.result_note != retry_note:
                    order.result_note = retry_note
                    updates.append("result_note")
                order.save(update_fields=updates)
                return order
            complete_internal_order(order)
        elif terminal_status == "error":
            _set_order_terminal_state(order, status="failed", note=_result_note("Falha na compra interna.", note_text))
            schedule_internal_offer_cleanup(order, note="buy_failed")
        elif terminal_status == "cancelled":
            _set_order_terminal_state(order, status="canceled", note=_result_note("Compra interna cancelada.", note_text))
            schedule_internal_offer_cleanup(order, note="buy_cancelled")
        return order

    if job.pk == order.redistribution_job_id:
        if terminal_status == "error":
            _set_order_terminal_state(order, status="failed", note=_result_note("Falha na redistribuicao apos compra interna.", note_text))
        elif terminal_status == "cancelled":
            _set_order_terminal_state(order, status="canceled", note=_result_note("Redistribuicao apos compra interna cancelada.", note_text))
        return order

    if int(job.action_code) != 2 or str(inputs.get("monitor_mode") or "").strip().lower() != "arrival_check":
        return order

    if terminal_status == "finished":
        _set_order_terminal_state(order, status="completed", note="Compra interna concluida com redistribuicao confirmada.")
    elif terminal_status == "error":
        _set_order_terminal_state(order, status="failed", note=_result_note("Falha ao confirmar chegada da redistribuicao.", note_text))
    elif terminal_status == "cancelled":
        _set_order_terminal_state(order, status="canceled", note=_result_note("Confirmacao de chegada da redistribuicao cancelada.", note_text))
    return order


def create_internal_order_result(
    buyer_ga: GameAccount,
    resource_idx: int,
    amount: int,
    unit_price: int = 0,
    preferred_buyer_city_id: int | None = None,
    target_city_id: int | None = None,
    source_job_id: str | None = None,
    source_action_code: int | None = None,
    source_reason: str = "",
    reason_detail: str = "",
    production_eta_seconds: int | None = None,
    missing_resource_keys: str = "",
) -> InternalOrderCreateResult:
    has_gold, gold_position = _has_gold_for_market_order(buyer_ga, amount=amount, unit_price=unit_price)
    if not has_gold:
        logger.warning(
            "Buyer %s market gold unavailable: current=%s reserved=%s available=%s required=%s minimum=%s; skipping order",
            buyer_ga.pk,
            gold_position["current"],
            gold_position["reserved"],
            gold_position["available"],
            gold_position["required"],
            gold_position["minimum"],
        )
        return InternalOrderCreateResult(
            ok=False,
            code="buyer_below_min_gold",
            detail=_format_gold_position_detail(gold_position),
        )
    buyer_snap = _load_snapshot(buyer_ga)

    resolved_target_city_id = _resolve_target_city_id(
        target_city_id=target_city_id,
        preferred_buyer_city_id=preferred_buyer_city_id,
        buyer_city_id=None,
    )
    existing_active = (
        InternalMarketOrder.objects.filter(
            buyer_game_account=buyer_ga,
            resource_idx=resource_idx,
            target_city_id=resolved_target_city_id,
            source_reason=source_reason,
            status__in=["matched", "jobs_created", "jobs_running"],
        )
        .order_by("-created_at")
        .first()
    )
    if existing_active is not None:
        return InternalOrderCreateResult(
            ok=False,
            code="existing_active_order",
            detail=f"order_id={existing_active.pk} status={existing_active.status}",
        )
    buyer_city_id = None
    buyer_bo_pos = -1
    seller_ga = None
    seller_city = None
    seller_bo_pos = -1
    visibility_blocked = False
    for candidate_seller_ga, candidate_seller_city, candidate_seller_bo_pos in iter_eligible_sellers(
        buyer_ga,
        resource_idx,
        amount,
    ):
        candidate_buyer_city_id, candidate_buyer_bo_pos = find_buyer_branchoffice(
            buyer_ga,
            preferred_city_id=preferred_buyer_city_id,
            target_city_id=resolved_target_city_id,
            seller_city=candidate_seller_city,
        )
        if candidate_buyer_city_id is None or candidate_buyer_bo_pos < 0:
            visibility_blocked = True
            continue
        seller_ga = candidate_seller_ga
        seller_city = candidate_seller_city
        seller_bo_pos = candidate_seller_bo_pos
        buyer_city_id = candidate_buyer_city_id
        buyer_bo_pos = candidate_buyer_bo_pos
        break

    if seller_ga is None or seller_city is None:
        code = "no_visible_market_city" if visibility_blocked else "no_seller"
        logger.warning(
            "No eligible market pair for resource_idx=%s amount=%s buyer_ga=%s code=%s",
            resource_idx, amount, buyer_ga.pk, code,
        )
        return InternalOrderCreateResult(ok=False, code=code)
    seller_city_id = int(seller_city["id"])
    buyer_city_name = _city_name(None, buyer_city_id)
    if buyer_snap:
        for city in _cities_from_snapshot(buyer_snap):
            if int(city.get("id") or 0) == int(buyer_city_id):
                buyer_city_name = _city_name(city, buyer_city_id)
                break
    seller_city_name = _city_name(seller_city, seller_city_id)
    sell_source_job_id, _root_job_id = _resolve_job_chain(source_job_id)

    order = InternalMarketOrder.objects.create(
        buyer_account=buyer_ga.account,
        buyer_game_account=buyer_ga,
        buyer_node=buyer_ga.account.node,
        buyer_city_id=buyer_city_id,
        buyer_branchoffice_pos=buyer_bo_pos,
        target_city_id=resolved_target_city_id,
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
            "target_city_id": resolved_target_city_id,
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
        "InternalMarketOrder %s created: seller=%s city=%s res=%s amount=%s target_city=%s",
        order.pk, seller_ga, seller_city_id, resource_idx, amount, resolved_target_city_id,
    )
    return InternalOrderCreateResult(
        ok=True,
        code="ok",
        order=order,
        buyer_city_id=buyer_city_id,
        target_city_id=resolved_target_city_id,
    )


def _create_fixed_internal_order(
    *,
    buyer_ga: GameAccount,
    buyer_city_id: int,
    buyer_bo_pos: int,
    seller_ga: GameAccount,
    seller_city: dict,
    seller_bo_pos: int,
    resource_idx: int,
    amount: int,
    unit_price: int,
    target_city_id: int | None,
    source_job_id: str | None,
    source_action_code: int | None,
    source_reason: str,
    reason_detail: str,
    existing_sell_job: Job | None = None,
) -> InternalMarketOrder:
    buyer_snap = _load_snapshot(buyer_ga)
    seller_city_id = int(seller_city["id"])
    buyer_city_name = _city_name(None, buyer_city_id)
    if buyer_snap:
        for city in _cities_from_snapshot(buyer_snap):
            if int(city.get("id") or 0) == int(buyer_city_id):
                buyer_city_name = _city_name(city, buyer_city_id)
                break
    seller_city_name = _city_name(seller_city, seller_city_id)
    sell_source_job_id, _root_job_id = _resolve_job_chain(source_job_id)

    order = InternalMarketOrder.objects.create(
        buyer_account=buyer_ga.account,
        buyer_game_account=buyer_ga,
        buyer_node=buyer_ga.account.node,
        buyer_city_id=buyer_city_id,
        buyer_branchoffice_pos=buyer_bo_pos,
        target_city_id=_resolve_target_city_id(
            target_city_id=target_city_id,
            preferred_buyer_city_id=buyer_city_id,
            buyer_city_id=buyer_city_id,
        ),
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
    )

    if existing_sell_job is not None:
        order.sell_job = existing_sell_job
        order.status = "jobs_running"
        order.save(update_fields=["sell_job", "status", "updated_at"])
        buy_job = create_buy_job(order)
        if buy_job is None:
            raise RuntimeError("Could not create buy_job for fixed internal order.")
        return order

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
            "target_city_id": order.target_city_id,
            "internal_order_id": str(order.pk),
        },
        status="queued",
        start_new_run=sell_source_job is not None,
        trigger_type="internal_market_sell",
    )
    order.sell_job = sell_job
    order.status = "jobs_created"
    order.save(update_fields=["sell_job", "status", "updated_at"])
    return order


def create_internal_gold_raise_order_result(
    *,
    seller_ga: GameAccount,
    seller_city_id: int,
    seller_branchoffice_pos: int,
    resource_idx: int,
    amount: int,
    unit_price: int,
    source_job_id: str | None = None,
    source_action_code: int | None = None,
    source_reason: str = "",
    reason_detail: str = "",
    existing_sell_job: Job | None = None,
) -> InternalOrderCreateResult:
    seller_snap = _load_snapshot(seller_ga)
    seller_city = _find_city_in_snapshot(seller_snap, seller_city_id)
    if seller_city is None:
        return InternalOrderCreateResult(ok=False, code="seller_city_missing")

    required_gold = max(1, int(amount)) * max(1, int(unit_price))
    visibility_blocked = False
    candidates: list[tuple[int, GameAccount, int, int]] = []
    buyer_qs = (
        GameAccount.objects.filter(open_for_market=True, active=True, blocked=False)
        .exclude(account__node=seller_ga.account.node)
        .select_related("account", "account__node")
    )
    for buyer_ga in buyer_qs:
        if buyer_ga.pk == seller_ga.pk:
            continue
        available_gold = _available_gold_for_buyer(buyer_ga)
        reserve_gold = int(getattr(buyer_ga, "market_min_gold", 0) or 0)
        if available_gold - required_gold < reserve_gold:
            continue
        buyer_city_id, buyer_bo_pos = find_buyer_branchoffice(
            buyer_ga,
            preferred_city_id=None,
            target_city_id=None,
            seller_city=seller_city,
        )
        if buyer_city_id is None or buyer_bo_pos < 0:
            visibility_blocked = True
            continue
        candidates.append((available_gold - required_gold - reserve_gold, buyer_ga, buyer_city_id, buyer_bo_pos))

    if not candidates:
        return InternalOrderCreateResult(
            ok=False,
            code="no_visible_buyer_market_city" if visibility_blocked else "no_buyer_gold",
            detail=f"required_gold={required_gold}",
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    _remaining_gold, buyer_ga, buyer_city_id, buyer_bo_pos = candidates[0]
    order = _create_fixed_internal_order(
        buyer_ga=buyer_ga,
        buyer_city_id=buyer_city_id,
        buyer_bo_pos=buyer_bo_pos,
        seller_ga=seller_ga,
        seller_city=seller_city,
        seller_bo_pos=int(seller_branchoffice_pos),
        resource_idx=resource_idx,
        amount=amount,
        unit_price=unit_price,
        target_city_id=buyer_city_id,
        source_job_id=source_job_id,
        source_action_code=source_action_code,
        source_reason=source_reason,
        reason_detail=reason_detail,
        existing_sell_job=existing_sell_job,
    )
    return InternalOrderCreateResult(
        ok=True,
        code="ok",
        order=order,
        buyer_city_id=buyer_city_id,
        target_city_id=buyer_city_id,
    )


def iter_eligible_sellers(
    buyer_ga: GameAccount,
    resource_idx: int,
    amount: int,
) -> list[tuple[GameAccount, dict, int]]:
    buyer_node = buyer_ga.node
    matches: list[tuple[GameAccount, dict, int]] = []

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
            free_market_capacity = _seller_market_free_capacity(city)
            if free_market_capacity is not None and free_market_capacity < amount:
                logger.info(
                    "Skipping seller %s city=%s for res=%s amount=%s: Branch Office free capacity=%s",
                    seller_ga.pk,
                    city_id,
                    resource_idx,
                    amount,
                    free_market_capacity,
                )
                continue
            if stock - reserved - min_stock >= amount:
                matches.append((seller_ga, city, bo_pos))

    return matches


def find_eligible_seller(
    buyer_ga: GameAccount,
    resource_idx: int,
    amount: int,
) -> tuple[GameAccount | None, dict | None, int]:
    matches = iter_eligible_sellers(buyer_ga, resource_idx, amount)
    if not matches:
        return None, None, -1
    return matches[0]


def find_buyer_branchoffice(
    buyer_ga: GameAccount,
    preferred_city_id: int | None = None,
    target_city_id: int | None = None,
    seller_city: dict | None = None,
) -> tuple[int | None, int]:
    try:
        snap = AccountSnapshot.objects.filter(game_account=buyer_ga).first()
    except Exception:
        return None, -1
    if snap is None:
        return None, -1

    cities = _cities_from_snapshot(snap)

    target_city = None
    if target_city_id is not None:
        for city in cities:
            raw_city_id = city.get("id")
            if raw_city_id is not None and int(raw_city_id) == int(target_city_id):
                target_city = city
                break

    candidates: list[tuple[tuple[int, int, int], int, int]] = []
    preferred_match: tuple[int, int] | None = None
    for city in cities:
        city_id = city.get("id")
        if city_id is None:
            continue
        info = _find_branchoffice_info(city)
        if not info:
            continue
        if not _city_is_visible_for_market(city, seller_city):
            continue
        if preferred_city_id is not None and int(city_id) == int(preferred_city_id):
            return int(city_id), int(info["position"])
        distance_to_target = _city_axis_distance(city, target_city)
        if distance_to_target is None:
            distance_to_target = 999_999
        effective_range = int(info["effective_range"]) if info.get("effective_range") is not None else 999_999
        candidates.append(((distance_to_target, -effective_range, int(city_id)), int(city_id), int(info["position"])))

    if candidates:
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1], candidates[0][2]
    return None, -1


def create_internal_order(
    buyer_ga: GameAccount,
    resource_idx: int,
    amount: int,
    unit_price: int = 0,
    preferred_buyer_city_id: int | None = None,
    target_city_id: int | None = None,
    source_job_id: str | None = None,
    source_action_code: int | None = None,
    source_reason: str = "",
    reason_detail: str = "",
    production_eta_seconds: int | None = None,
    missing_resource_keys: str = "",
) -> InternalMarketOrder | None:
    result = create_internal_order_result(
        buyer_ga=buyer_ga,
        resource_idx=resource_idx,
        amount=amount,
        unit_price=unit_price,
        preferred_buyer_city_id=preferred_buyer_city_id,
        target_city_id=target_city_id,
        source_job_id=source_job_id,
        source_action_code=source_action_code,
        source_reason=source_reason,
        reason_detail=reason_detail,
        production_eta_seconds=production_eta_seconds,
        missing_resource_keys=missing_resource_keys,
    )
    return result.order


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
            "seller_game_account_id": str(order.seller_game_account_id or ""),
            "resource_idx": order.resource_idx,
            "amount": order.amount,
            "unit_price": order.unit_price,
            "internal_order_id": str(order.pk),
            "source_reason": order.source_reason,
            "source_action_code": order.source_action_code,
            "market_order_mode": "raise_gold" if order.source_reason == "construction_raise_gold" else "buy_resource",
        },
        status="queued",
        timeout_sec=7200,
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


def _resource_label(resource_idx: int) -> str:
    for idx, label in InternalMarketOrder.RESOURCE_CHOICES:
        if int(idx) == int(resource_idx):
            return str(label)
    return f"Recurso {resource_idx}"


def _resolve_telegram_chat_id(game_account: GameAccount) -> str:
    from apps.telegram.models import TelegramAccountConfig, TelegramBotConfig

    try:
        bot = TelegramBotConfig.objects.get(pk=1)
    except TelegramBotConfig.DoesNotExist:
        return ""
    if not bot.is_active:
        return ""

    try:
        ga_config = TelegramAccountConfig.objects.get(game_account=game_account)
    except TelegramAccountConfig.DoesNotExist:
        ga_config = None

    if ga_config and ga_config.enabled and ga_config.chat_id:
        return str(ga_config.chat_id)
    return str(bot.chat_id or "")


def _build_intervention_message(request: ConstructionMarketIntervention) -> str:
    eta_hours = round(int(request.eta_seconds or 0) / 3600, 1)
    buy_cost_avg = int(request.estimated_buy_cost_avg or request.estimated_buy_cost_min or 0)
    sale_revenue = int(request.estimated_sale_gold or 0)
    buy_min = int(request.estimated_buy_unit_min or 0)
    buy_avg = int(request.estimated_buy_unit_avg or 0)
    sale_target = int(request.sale_price_target or request.sale_price_max or 0)
    sale_min = int(request.sale_price_min or 0)
    sale_max = int(request.sale_price_max or 0)

    return (
        "📦 <b>Intervenção de mercado para construção</b>\n"
        f"{request.game_account.name} | {request.city_name} | {request.building_name}\n\n"
        f"Motivo: <code>{request.wait_reason}</code>\n"
        f"ETA atual: <b>{eta_hours}h</b>\n"
        f"Falta: <b>{request.needed_amount:,}</b> {_resource_label(request.needed_resource_idx)}\n"
        f"Ouro atual: <code>{request.available_gold:,}</code> | piso mercado: <code>{request.min_gold:,}</code>\n\n"
        f"Venda proposta: <b>{request.sale_city_name}</b>\n"
        f"{_resource_label(request.sale_resource_idx)} x <b>{request.sale_amount:,}</b>\n"
        f"Preço venda alvo: <code>{sale_target}</code> (limites {sale_min}-{sale_max})\n"
        f"Arrecadação estimada: <code>{sale_revenue:,}</code>\n\n"
        f"Compra estimada de {_resource_label(request.needed_resource_idx)}:\n"
        f"mín: <code>{buy_min}</code> | méd: <code>{buy_avg}</code>\n"
        f"custo mín: <code>{int(request.estimated_buy_cost_min or 0):,}</code>\n"
        f"custo méd: <code>{buy_cost_avg:,}</code>\n\n"
        f"Cobre no mín? <b>{'sim' if request.can_fund_min else 'não'}</b>\n"
        f"Cobre na média? <b>{'sim' if request.can_fund_avg else 'não'}</b>"
    )


def _intervention_keyboard(request_id) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Aprovar venda", "callback_data": f"cmi_approve:{request_id}"},
            {"text": "❌ Recusar", "callback_data": f"cmi_reject:{request_id}"},
        ]]
    }


def create_construction_market_intervention(
    *,
    game_account: GameAccount,
    source_job: Job | None,
    payload: dict,
) -> tuple[ConstructionMarketIntervention, bool, bool]:
    active_filters = {
        "game_account": game_account,
        "status__in": _INTERVENTION_ACTIVE_STATUSES,
        "wait_reason": str(payload.get("wait_reason") or "").strip(),
        "city_id": payload.get("city_id"),
        "needed_resource_idx": int(payload.get("needed_resource_idx") or 0),
    }
    if source_job is not None:
        active_filters["source_job"] = source_job

    existing = (
        ConstructionMarketIntervention.objects
        .select_related("game_account", "account", "node", "source_job")
        .filter(**active_filters)
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing, False, False

    request = ConstructionMarketIntervention.objects.create(
        account=game_account.account,
        game_account=game_account,
        node=game_account.account.node,
        source_job=source_job,
        status="pending",
        wait_reason=str(payload.get("wait_reason") or "").strip(),
        eta_seconds=int(payload.get("eta_seconds") or 0),
        city_id=int(payload.get("city_id")) if payload.get("city_id") not in (None, "") else None,
        city_name=str(payload.get("city_name") or "").strip(),
        building_name=str(payload.get("building_name") or "").strip(),
        needed_resource_idx=int(payload.get("needed_resource_idx") or 0),
        needed_amount=int(payload.get("needed_amount") or 0),
        available_gold=int(payload.get("available_gold") or 0),
        min_gold=int(payload.get("min_gold") or 0),
        estimated_buy_unit_min=int(payload.get("estimated_buy_unit_min") or 0),
        estimated_buy_unit_avg=int(payload.get("estimated_buy_unit_avg") or 0),
        estimated_buy_cost_min=int(payload.get("estimated_buy_cost_min") or 0),
        estimated_buy_cost_avg=int(payload.get("estimated_buy_cost_avg") or 0),
        sale_city_id=int(payload.get("sale_city_id")) if payload.get("sale_city_id") not in (None, "") else None,
        sale_city_name=str(payload.get("sale_city_name") or "").strip(),
        sale_branchoffice_pos=int(payload.get("sale_branchoffice_pos")) if payload.get("sale_branchoffice_pos") not in (None, "") else None,
        sale_resource_idx=int(payload.get("sale_resource_idx") or 0),
        sale_amount=int(payload.get("sale_amount") or 0),
        sale_price_min=int(payload.get("sale_price_min") or 0),
        sale_price_max=int(payload.get("sale_price_max") or 0),
        sale_price_target=int(payload.get("sale_price_target") or 0),
        estimated_sale_gold=int(payload.get("estimated_sale_gold") or 0),
        can_fund_min=bool(payload.get("can_fund_min")),
        can_fund_avg=bool(payload.get("can_fund_avg")),
    )

    chat_id = _resolve_telegram_chat_id(game_account)
    sent = False
    if chat_id:
        message = _build_intervention_message(request)
        result = send_message(chat_id, message, reply_markup=_intervention_keyboard(request.pk))
        if result.get("ok"):
            request.telegram_chat_id = str(chat_id)
            request.telegram_message_id = str(result.get("result", {}).get("message_id", "") or "")
            request.save(update_fields=["telegram_chat_id", "telegram_message_id", "updated_at"])
            sent = True

    return request, True, sent


def approve_construction_market_intervention(
    request: ConstructionMarketIntervention,
    *,
    decided_by: str = "",
    note: str = "",
) -> tuple[bool, str]:
    existing_internal_order_id = _extract_internal_order_id_from_note(request.decision_note)
    if existing_internal_order_id:
        existing_order = InternalMarketOrder.objects.filter(pk=existing_internal_order_id).first()
        if existing_order is not None:
            if request.sell_job_id != existing_order.sell_job_id:
                request.sell_job = existing_order.sell_job
                request.save(update_fields=["sell_job", "updated_at"])
            return True, f"Ordem interna ja existe: {existing_order.pk}."

    if request.status == "sell_queued" and request.sell_job_id:
        sell_job = Job.objects.filter(pk=request.sell_job_id).only("status", "action_code").first()
        if sell_job and int(sell_job.action_code) == 802 and sell_job.status in {"queued", "scheduled", "running", "finished"}:
            return True, "Venda interna já estava enfileirada."
    if request.status == "rejected":
        return False, "Solicitação já foi recusada."

    if request.sell_job_id:
        request.status = "sell_queued"
        request.decided_by = str(decided_by or "").strip()
        request.decision_note = str(note or "").strip()
        request.decided_at = timezone.now()
        request.save(update_fields=["status", "decided_by", "decision_note", "decided_at", "updated_at"])
        existing_job = Job.objects.filter(pk=request.sell_job_id).only("status").first()
        if existing_job and existing_job.status in {"error", "cancelled"}:
            request.sell_job = None
            request.save(update_fields=["sell_job", "updated_at"])
        elif existing_job and existing_job.status in {"queued", "scheduled", "running"}:
            return True, "Venda já existia; status atualizado."

    if request.sale_city_id in (None, "") or request.sale_branchoffice_pos in (None, ""):
        return False, "Cidade de venda ou Branch Office ausente."

    existing_sell_job = None
    if request.sell_job_id:
        candidate = Job.objects.filter(pk=request.sell_job_id).only("status", "action_code").first()
        if candidate and int(candidate.action_code) == 9 and candidate.status == "finished":
            existing_sell_job = candidate

    result = create_internal_gold_raise_order_result(
        seller_ga=request.game_account,
        seller_city_id=int(request.sale_city_id),
        seller_branchoffice_pos=int(request.sale_branchoffice_pos),
        resource_idx=int(request.sale_resource_idx),
        amount=int(request.sale_amount),
        unit_price=int(request.sale_price_target or request.sale_price_max or request.sale_price_min or 0),
        source_job_id=str(request.source_job_id) if request.source_job_id else None,
        source_action_code=1002,
        source_reason="construction_raise_gold",
        reason_detail=(
            f"intervention={request.pk} | {request.city_name} | {request.building_name} | "
            f"need_resource={request.needed_resource_idx} amount={request.needed_amount}"
        ),
        existing_sell_job=existing_sell_job,
    )
    if not result.ok or result.order is None:
        return False, result.error or "Falha ao criar ordem interna para levantar ouro."

    request.sell_job = result.order.sell_job
    request.status = "sell_queued"
    request.decided_by = str(decided_by or "").strip()
    request.decision_note = (
        f"{str(note or '').strip()} | internal_order_id={result.order.pk}".strip(" |")
    )
    request.decided_at = timezone.now()
    request.save(update_fields=["sell_job", "status", "decided_by", "decision_note", "decided_at", "updated_at"])
    return True, f"Ordem interna criada: {result.order.pk}."


def reject_construction_market_intervention(
    request: ConstructionMarketIntervention,
    *,
    decided_by: str = "",
    note: str = "",
) -> tuple[bool, str]:
    if request.status == "sell_queued":
        return False, "A venda já foi aprovada e enfileirada."
    if request.status == "rejected":
        return True, "Solicitação já estava recusada."

    request.status = "rejected"
    request.decided_by = str(decided_by or "").strip()
    request.decision_note = str(note or "").strip()
    request.decided_at = timezone.now()
    request.save(update_fields=["status", "decided_by", "decision_note", "decided_at", "updated_at"])
    return True, "Solicitação recusada."


def refresh_intervention_message(
    request: ConstructionMarketIntervention,
    *,
    decision_label: str,
) -> None:
    if not request.telegram_chat_id or not request.telegram_message_id:
        return
    stamped = timezone.localtime(request.decided_at or timezone.now()).strftime("%d/%m %H:%M")
    text = f"{_build_intervention_message(request)}\n\n<i>{decision_label} · {stamped}</i>"
    try:
        edit_message_text(
            request.telegram_chat_id,
            int(request.telegram_message_id),
            text,
        )
    except Exception:
        logger.warning("Failed to edit Telegram intervention message for %s", request.pk)
