"""
Market runners for generic and internal Branch Office operations.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.market import BuyAction, CreateOfferAction
from game_client.parsers.html_parser import GamePageParser
from runners.base import BaseRunner, RunnerResult
from services.resource_transport import (
    _parse_free_transporters,
    _parse_ship_capacity,
    _parse_transport_times,
    estimate_incoming_transport_wait_seconds,
    fetch_city_state,
)

logger = logging.getLogger(__name__)

RESOURCE_LABELS = {
    0: "Madeira",
    1: "Vinho",
    2: "Marmore",
    3: "Cristal",
    4: "Enxofre",
}
RESOURCE_INPUT_KEYS = {
    0: "wood",
    1: "wine",
    2: "marble",
    3: "crystal",
    4: "sulfur",
}


@register_runner(9)
class SellMarketRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs", {})

        city_id = inputs.get("city_id")
        bo_pos = inputs.get("branchoffice_pos")
        resource_idx = int(inputs.get("resource_idx", 0))
        amount = int(inputs.get("amount", 0))
        unit_price = int(inputs.get("unit_price", 0))
        offer_mode = str(inputs.get("offer_mode") or "add").strip().lower()

        if not city_id or bo_pos is None or amount <= 0:
            self.log(jid, "error", "Missing required inputs: city_id, branchoffice_pos, amount")
            return RunnerResult(success=False, data={"error": "missing inputs"})

        price_str = str(unit_price) if unit_price > 0 else "auto"
        self.log(jid, "info", f"Creating sell offer: city={city_id} res={resource_idx} x{amount} @{price_str}")

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            if unit_price > 0:
                limits = CreateOfferAction(client).get_price_limits(int(city_id), int(bo_pos))
                live_min, live_max = limits[resource_idx]
                if unit_price < live_min or unit_price > live_max:
                    adjusted_price = min(max(unit_price, live_min), live_max)
                    self.log(
                        jid,
                        "info",
                        (
                            f"Adjusting sell price to live market range: "
                            f"{unit_price} -> {adjusted_price} (limits {live_min}-{live_max})"
                        ),
                    )
                    unit_price = adjusted_price
            client.create_market_offer(
                city_id=int(city_id),
                branchoffice_pos=int(bo_pos),
                resource_idx=resource_idx,
                amount=amount,
                unit_price=unit_price,
                offer_mode=offer_mode,
            )
            self.save_game_client(ga_id or aid, client)
            self.log(jid, "info", "Sell offer created")
            return RunnerResult(success=True)
        except Exception as exc:
            self.log(jid, "error", f"Sell offer failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(8)
class BuyMarketRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs", {})

        buyer_city_id = inputs.get("buyer_city_id")
        buyer_bo = inputs.get("buyer_branchoffice_pos")
        seller_city_id = inputs.get("seller_city_id")
        seller_bo = inputs.get("seller_branchoffice_pos")
        resource_idx = int(inputs.get("resource_idx", 0))
        amount = int(inputs.get("amount", 0))

        if not all([buyer_city_id, buyer_bo is not None, seller_city_id, seller_bo is not None, amount]):
            self.log(jid, "error", "Missing required inputs")
            return RunnerResult(success=False, data={"error": "missing inputs"})

        self.log(jid, "info", f"Buying res={resource_idx} x{amount} from city={seller_city_id}")

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            client.buy_market_offer(
                buyer_city_id=int(buyer_city_id),
                buyer_branchoffice_pos=int(buyer_bo),
                seller_city_id=int(seller_city_id),
                seller_branchoffice_pos=int(seller_bo),
                resource_idx=resource_idx,
                amount=amount,
            )
            self.save_game_client(ga_id or aid, client)
            self.log(jid, "info", "Market purchase complete")
            return RunnerResult(success=True)
        except Exception as exc:
            self.log(jid, "error", f"Market buy failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(802)
class InternalMarketSellRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs", {})

        city_id = inputs.get("city_id")
        bo_pos = inputs.get("branchoffice_pos")
        resource_idx = int(inputs.get("resource_idx", 0))
        amount = int(inputs.get("amount", 0))
        unit_price = int(inputs.get("unit_price", 0))
        order_id = inputs.get("internal_order_id")
        offer_mode = str(inputs.get("offer_mode") or "add").strip().lower()
        cleanup_only = bool(inputs.get("cleanup_only"))
        city_name = str(inputs.get("city_name") or city_id or "").strip()
        buyer_city_name = str(inputs.get("buyer_city_name") or inputs.get("buyer_city_id") or "").strip()
        resource_label = RESOURCE_LABELS.get(resource_idx, f"res={resource_idx}")

        if not city_id or bo_pos is None or not order_id or (amount <= 0 and offer_mode != "clear"):
            self.log(jid, "error", "Missing required inputs for InternalMarketSellRunner")
            return RunnerResult(success=False, data={"error": "missing inputs"})

        if cleanup_only:
            self.log(
                jid,
                "info",
                f"[Order {order_id}] Cleanup de oferta interna: {city_name} | "
                f"{resource_label} total={amount} modo={offer_mode}",
            )
        else:
            self.log(
                jid,
                "info",
                f"[Order {order_id}] Venda interna: {city_name} -> {buyer_city_name} | "
                f"{resource_label} x{amount} @{'auto' if unit_price <= 0 else unit_price}",
            )

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", f"[Order {order_id}] Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            if cleanup_only:
                action = CreateOfferAction(client)
                _limits, current_offer_state = action.get_market_context(int(city_id), int(bo_pos))
                target_field = "resource" if resource_idx == 0 else f"tradegood{resource_idx}"
                current_total = int(current_offer_state.get(target_field, 0) or 0)
                expected_current_total = int(inputs.get("expected_current_total", -1))
                if expected_current_total >= 0 and current_total != expected_current_total:
                    self.log(
                        jid,
                        "warn",
                        f"[Order {order_id}] Cleanup ignorado: total vivo {current_total} "
                        f"!= esperado {expected_current_total}.",
                    )
                    self.save_game_client(ga_id or aid, client)
                    return RunnerResult(
                        success=True,
                        data={
                            "cleanup_skipped": "market_total_mismatch",
                            "current_total": current_total,
                            "expected_current_total": expected_current_total,
                        },
                    )
                offer_result = action.execute(
                    city_id=int(city_id),
                    branchoffice_pos=int(bo_pos),
                    resource_idx=resource_idx,
                    amount=amount,
                    unit_price=unit_price,
                    offer_mode=offer_mode,
                )
            else:
                offer_result = client.create_market_offer(
                    city_id=int(city_id),
                    branchoffice_pos=int(bo_pos),
                    resource_idx=resource_idx,
                    amount=amount,
                    unit_price=unit_price,
                    offer_mode=offer_mode,
                )
            self.save_game_client(ga_id or aid, client)

            used_unit_price = int(offer_result.get("used_unit_price", 0)) if isinstance(offer_result, dict) else 0
            price_min = int(offer_result.get("price_min", 0)) if isinstance(offer_result, dict) else 0
            price_max = int(offer_result.get("price_max", 0)) if isinstance(offer_result, dict) else 0
            final_offer_amount = int(offer_result.get("final_offer_amount", amount)) if isinstance(offer_result, dict) else amount
            if cleanup_only:
                self.log(
                    jid,
                    "info",
                    f"[Order {order_id}] Cleanup aplicado em {city_name} | bo={bo_pos} | "
                    f"{resource_label} total={final_offer_amount} | modo={offer_mode}",
                )
                return RunnerResult(success=True, data={"cleanup_total": final_offer_amount})

            self.log(
                jid,
                "info",
                f"[Order {order_id}] Oferta publicada em {city_name} | bo={bo_pos} | "
                f"{resource_label} pedido={amount} total={final_offer_amount} | modo={offer_mode} | preco={used_unit_price} | limites={price_min}-{price_max}",
            )

            resp = self.hub.market_order_sell_complete(
                order_id,
                unit_price=used_unit_price,
                price_min=price_min,
                price_max=price_max,
            )
            buy_job_id = resp.get("buy_job_id", "?")
            self.log(jid, "info", f"[Order {order_id}] Job derivado de compra criado: {buy_job_id}")

            return RunnerResult(success=True, data={"buy_job_id": buy_job_id})

        except Exception as exc:
            self.log(jid, "error", f"[Order {order_id}] Sell failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(801)
class InternalMarketBuyRunner(BaseRunner):
    _POLL_MIN_SECONDS = 60
    _POLL_MAX_SECONDS = 1800
    _ARRIVAL_DEADLINE_SECONDS = 2 * 60 * 60
    _TRADE_ADVISOR_CONFIRM_MARGIN_SECONDS = 5 * 60
    _RAISE_GOLD_BUFFER_SECONDS = 180
    _TRANSPORTER_RETRY_MIN_SECONDS = 5 * 60

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs", {})

        buyer_city_id = inputs.get("buyer_city_id")
        buyer_bo = inputs.get("buyer_branchoffice_pos")
        seller_city_id = inputs.get("seller_city_id")
        seller_bo = inputs.get("seller_branchoffice_pos")
        resource_idx = int(inputs.get("resource_idx", 0))
        amount = int(inputs.get("amount", 0))
        order_id = inputs.get("internal_order_id")
        buyer_city_name = str(inputs.get("buyer_city_name") or buyer_city_id or "").strip()
        seller_city_name = str(inputs.get("seller_city_name") or seller_city_id or "").strip()
        resource_label = RESOURCE_LABELS.get(resource_idx, f"res={resource_idx}")
        purchase_monitor_mode = str(inputs.get("purchase_monitor_mode") or "").strip().lower()
        market_order_mode = str(inputs.get("market_order_mode") or "").strip().lower() or "buy_resource"
        is_raise_gold_mode = market_order_mode == "raise_gold" or str(inputs.get("source_reason") or "").strip() == "construction_raise_gold"
        unit_price = int(inputs.get("unit_price", 0) or 0)
        seller_game_account_id = str(inputs.get("seller_game_account_id") or "").strip()

        if not all([
            buyer_city_id, buyer_bo is not None,
            seller_city_id, seller_bo is not None,
            amount, order_id,
        ]):
            self.log(jid, "error", "Missing required inputs for InternalMarketBuyRunner")
            return RunnerResult(success=False, data={"error": "missing inputs"})

        self.log(
            jid,
            "info",
            f"[Order {order_id}] Compra interna: {seller_city_name} -> {buyer_city_name} | "
            f"{resource_label} x{amount}",
        )

        max_offer_retries = 5
        offer_retry_count = int(inputs.get("offer_retry_count", 0))
        login_retry_count = int(inputs.get("login_retry_count", 0))

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", f"[Order {order_id}] Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            resource_key = RESOURCE_INPUT_KEYS.get(resource_idx, "wood")
            if purchase_monitor_mode == "await_arrival":
                purchase_started_at = int(inputs.get("purchase_started_at") or 0)
                expected_outbound_seconds = int(inputs.get("purchase_expected_outbound_seconds") or 0)
                expected_total_seconds = int(inputs.get("purchase_expected_total_seconds") or 0)
                baseline_amount = int(inputs.get("purchase_baseline_amount") or 0)
                if is_raise_gold_mode:
                    status = self._check_raise_gold_arrival_status(
                        jid=jid,
                        client=client,
                        buyer_city_id=int(buyer_city_id),
                        seller_city_id=int(seller_city_id),
                        purchase_started_at=purchase_started_at,
                        expected_arrival_seconds=expected_outbound_seconds,
                    )
                else:
                    status = self._check_purchase_arrival_status(
                        jid=jid,
                        client=client,
                        buyer_city_id=int(buyer_city_id),
                        seller_city_id=int(seller_city_id),
                        resource_idx=resource_idx,
                        amount=amount,
                        baseline_amount=baseline_amount,
                        purchase_started_at=purchase_started_at,
                        expected_outbound_seconds=expected_outbound_seconds,
                        expected_total_seconds=expected_total_seconds,
                    )
                if status["state"] == "arrived":
                    if is_raise_gold_mode:
                        self._apply_raise_gold_snapshot_updates(
                            jid=jid,
                            seller_game_account_id=seller_game_account_id,
                            credited_gold=max(0, amount * max(0, unit_price)),
                            seller_city_id=int(seller_city_id),
                            order_id=str(order_id),
                        )
                    self.save_game_client(ga_id or aid, client)
                    self.log(
                        jid,
                        "info",
                        f"[Order {order_id}] Compra confirmada na cidade compradora | "
                        f"modo={status['mode']} | evidencia={status['evidence']}",
                    )
                    self.hub.market_order_complete(order_id)
                    self.log(jid, "info", f"[Order {order_id}] Order marked as completed")
                    if is_raise_gold_mode:
                        self._retime_followup_construction_job(
                            jid=jid,
                            job=job,
                            delay_seconds=self._RAISE_GOLD_BUFFER_SECONDS,
                        )
                    return RunnerResult(success=True)

                if status["state"] == "pending":
                    retry_inputs = dict(inputs)
                    retry_inputs["purchase_monitor_mode"] = "await_arrival"
                    retry_inputs["purchase_started_at"] = purchase_started_at
                    retry_inputs["purchase_expected_outbound_seconds"] = expected_outbound_seconds
                    retry_inputs["purchase_expected_total_seconds"] = expected_total_seconds
                    retry_inputs["purchase_baseline_amount"] = baseline_amount
                    self.hub.reschedule_job(jid, delay_seconds=int(status["delay_seconds"]), inputs=retry_inputs)
                    self.log(
                        jid,
                        "info",
                        f"[Order {order_id}] Monitor de compra reagendado em {status['delay_seconds']}s | "
                        f"fase={status['mode']} | motivo={status['evidence']}",
                    )
                    if is_raise_gold_mode:
                        self._retime_followup_construction_job(
                            jid=jid,
                            job=job,
                            delay_seconds=int(status["delay_seconds"]) + self._RAISE_GOLD_BUFFER_SECONDS,
                        )
                    return RunnerResult(success=True, data={"status": "arrival_check_scheduled", "delay": status["delay_seconds"]})

                raise RuntimeError(str(status.get("error") or "Purchase arrival confirmation failed"))

            preview = self._preview_purchase_eta(
                client,
                buyer_city_id=int(buyer_city_id),
                buyer_branchoffice_pos=int(buyer_bo),
                seller_city_id=int(seller_city_id),
                resource_idx=resource_idx,
                amount=amount,
            )
            baseline_city = fetch_city_state(client, buyer_city_id)
            baseline_amount = int(baseline_city.available_resources.get(resource_key, 0))
            self.log(
                jid,
                "info",
                f"[Order {order_id}] Previsao de compra | fila={preview['queue_seconds']}s | "
                f"carregamento={preview['loading_seconds']}s | ida={preview['travel_seconds']}s | "
                f"total_ate_compra={preview['total_seconds']}s | estoque_base_{resource_key}={baseline_amount}",
            )
            transporter_wait = self._maybe_reschedule_for_transporters(
                jid=jid,
                inputs=inputs,
                order_id=str(order_id),
                buyer_city_id=int(buyer_city_id),
                amount=amount,
                preview=preview,
            )
            if transporter_wait is not None:
                return transporter_wait
            client.buy_market_offer(
                buyer_city_id=int(buyer_city_id),
                buyer_branchoffice_pos=int(buyer_bo),
                seller_city_id=int(seller_city_id),
                seller_branchoffice_pos=int(seller_bo),
                resource_idx=resource_idx,
                amount=amount,
            )
            self.save_game_client(ga_id or aid, client)
            self.log(jid, "info", f"[Order {order_id}] Compra executada | destino={buyer_city_name} | origem={seller_city_name}")
            purchase_started_at = int(time.time())
            if is_raise_gold_mode:
                status = self._check_raise_gold_arrival_status(
                    jid=jid,
                    client=client,
                    buyer_city_id=int(buyer_city_id),
                    seller_city_id=int(seller_city_id),
                    purchase_started_at=purchase_started_at,
                    expected_arrival_seconds=int(preview["travel_seconds"]),
                )
            else:
                status = self._check_purchase_arrival_status(
                    jid=jid,
                    client=client,
                    buyer_city_id=int(buyer_city_id),
                    seller_city_id=int(seller_city_id),
                    resource_idx=resource_idx,
                    amount=amount,
                    baseline_amount=baseline_amount,
                    purchase_started_at=purchase_started_at,
                    expected_outbound_seconds=int(preview["travel_seconds"]),
                    expected_total_seconds=int(preview["total_seconds"]),
                )
            if status["state"] == "arrived":
                if is_raise_gold_mode:
                    self._apply_raise_gold_snapshot_updates(
                        jid=jid,
                        seller_game_account_id=seller_game_account_id,
                        credited_gold=max(0, amount * max(0, unit_price)),
                        seller_city_id=int(seller_city_id),
                        order_id=str(order_id),
                    )
                self.save_game_client(ga_id or aid, client)
                self.log(
                    jid,
                    "info",
                    f"[Order {order_id}] Compra confirmada na cidade compradora | "
                    f"modo={status['mode']} | evidencia={status['evidence']}",
                )
                self.hub.market_order_complete(order_id)
                self.log(jid, "info", f"[Order {order_id}] Order marked as completed")
                if is_raise_gold_mode:
                    self._retime_followup_construction_job(
                        jid=jid,
                        job=job,
                        delay_seconds=self._RAISE_GOLD_BUFFER_SECONDS,
                    )
                return RunnerResult(success=True)

            if status["state"] != "pending":
                raise RuntimeError(str(status.get("error") or "Purchase arrival confirmation failed"))

            retry_inputs = dict(inputs)
            retry_inputs["purchase_monitor_mode"] = "await_arrival"
            retry_inputs["purchase_started_at"] = purchase_started_at
            retry_inputs["purchase_expected_outbound_seconds"] = int(preview["travel_seconds"])
            retry_inputs["purchase_expected_total_seconds"] = int(preview["total_seconds"])
            retry_inputs["purchase_baseline_amount"] = baseline_amount
            self.hub.reschedule_job(jid, delay_seconds=int(status["delay_seconds"]), inputs=retry_inputs)
            self.log(
                jid,
                "info",
                (
                    f"[Order {order_id}] Monitor de compra agendado em {status['delay_seconds']}s | "
                    f"fase={status['mode']} | motivo={status['evidence']}"
                    if is_raise_gold_mode
                    else (
                        f"[Order {order_id}] Compra executada; confirmacao de chegada obrigatoria. "
                        f"Monitor agendado em {status['delay_seconds']}s | "
                        f"fase={status['mode']} | motivo={status['evidence']}"
                    )
                ),
            )
            if is_raise_gold_mode:
                self._retime_followup_construction_job(
                    jid=jid,
                    job=job,
                    delay_seconds=int(status["delay_seconds"]) + self._RAISE_GOLD_BUFFER_SECONDS,
                )
            return RunnerResult(success=True, data={"status": "arrival_check_scheduled", "delay": status["delay_seconds"]})
        except Exception as exc:
            exc_str = str(exc)
            transporter_wait = self._maybe_reschedule_for_transporters_from_error(
                jid=jid,
                client=client if 'client' in locals() else None,
                inputs=inputs,
                order_id=str(order_id),
                buyer_city_id=int(buyer_city_id),
                amount=amount,
                exc_str=exc_str,
            )
            if transporter_wait is not None:
                return transporter_wait
            loginlink_failed = "loginlink falhou" in exc_str.lower()
            if loginlink_failed:
                max_login_retries = 3
                if login_retry_count < max_login_retries:
                    next_retry = login_retry_count + 1
                    delay_seconds = min(600, 60 * next_retry)
                    self.log(
                        jid,
                        "warn",
                        f"[Order {order_id}] loginLink falhou no lobby (tentativa {next_retry}/{max_login_retries}); reagendando em {delay_seconds}s",
                    )
                    retry_inputs = dict(inputs)
                    retry_inputs["login_retry_count"] = next_retry
                    self.hub.reschedule_job(jid, delay_seconds=delay_seconds, inputs=retry_inputs)
                    return RunnerResult(success=True, data={"status": "loginlink_retry", "retry": next_retry})
            if "not found" in exc_str.lower() and offer_retry_count < max_offer_retries:
                next_retry = offer_retry_count + 1
                self.log(
                    jid,
                    "warn",
                    f"[Order {order_id}] Offer not found in listing "
                    f"(attempt {next_retry}/{max_offer_retries}) | origem={seller_city_name} | destino={buyer_city_name}; retrying in 60s",
                )
                retry_inputs = dict(inputs)
                retry_inputs["offer_retry_count"] = next_retry
                self.hub.reschedule_job(jid, delay_seconds=60, inputs=retry_inputs)
                return RunnerResult(success=True, data={"status": "retry_scheduled", "retry": next_retry})
            gold_insufficient = any(kw in exc_str.lower() for kw in ["ouro suficiente", "not enough gold", "sem ouro"])
            if gold_insufficient:
                gold_retry_count = int(inputs.get("gold_retry_count", 0))
                max_gold_retries = 6  # ~1h total wait
                if gold_retry_count < max_gold_retries:
                    next_retry = gold_retry_count + 1
                    self.log(
                        jid,
                        "warn",
                        f"[Order {order_id}] Ouro insuficiente (tentativa {next_retry}/{max_gold_retries}) | "
                        f"destino={buyer_city_name}; reagendando em 10min",
                    )
                    retry_inputs = dict(inputs)
                    retry_inputs["gold_retry_count"] = next_retry
                    self.hub.reschedule_job(jid, delay_seconds=600, inputs=retry_inputs)
                    return RunnerResult(success=True, data={"status": "gold_retry", "retry": next_retry})
                self.log(jid, "error", f"[Order {order_id}] Ouro insuficiente após {max_gold_retries} tentativas — cancelando")
            self.log(jid, "error", f"[Order {order_id}] Buy failed: {exc}")
            return RunnerResult(success=False, data={"error": exc_str})

    def _maybe_reschedule_for_transporters(
        self,
        *,
        jid: str,
        inputs: dict[str, Any],
        order_id: str,
        buyer_city_id: int,
        amount: int,
        preview: dict[str, int],
    ) -> RunnerResult | None:
        free_transporters = int(preview.get("free_transporters") or 0)
        ship_capacity = max(1, int(preview.get("ship_capacity") or 500))
        required_ships = max(1, int(math.ceil(amount / ship_capacity)))
        if free_transporters >= required_ships:
            return None
        delay_seconds = self._estimate_transporter_retry_delay(
            client=None,
            buyer_city_id=buyer_city_id,
            fallback_eta=int(preview.get("travel_seconds") or 0),
        )
        retry_inputs = dict(inputs)
        retry_inputs["transporter_retry_count"] = int(inputs.get("transporter_retry_count", 0)) + 1
        self.hub.reschedule_job(jid, delay_seconds=delay_seconds, inputs=retry_inputs)
        self.log(
            jid,
            "warn",
            (
                f"[Order {order_id}] Navios insuficientes para compra interna; "
                f"necessario={required_ships} disponivel={free_transporters} "
                f"(amount={amount}, capacidade={ship_capacity}). Retry em {delay_seconds}s"
            ),
        )
        return RunnerResult(success=True, data={"status": "transporters_wait", "delay": delay_seconds})

    def _maybe_reschedule_for_transporters_from_error(
        self,
        *,
        jid: str,
        client,
        inputs: dict[str, Any],
        order_id: str,
        buyer_city_id: int,
        amount: int,
        exc_str: str,
    ) -> RunnerResult | None:
        match = re.search(r"need\s+(\d+),\s+have\s+(\d+).*ship_capacity=(\d+)", exc_str, re.I)
        if not match:
            return None
        delay_seconds = self._estimate_transporter_retry_delay(client=client, buyer_city_id=buyer_city_id, fallback_eta=0)
        retry_inputs = dict(inputs)
        retry_inputs["transporter_retry_count"] = int(inputs.get("transporter_retry_count", 0)) + 1
        self.hub.reschedule_job(jid, delay_seconds=delay_seconds, inputs=retry_inputs)
        self.log(
            jid,
            "warn",
            (
                f"[Order {order_id}] Compra interna aguardando navios mercantes; "
                f"necessario={match.group(1)} disponivel={match.group(2)} capacidade={match.group(3)}. "
                f"Retry em {delay_seconds}s"
            ),
        )
        return RunnerResult(success=True, data={"status": "transporters_wait", "delay": delay_seconds})

    def _estimate_transporter_retry_delay(
        self,
        *,
        client,
        buyer_city_id: int,
        fallback_eta: int,
    ) -> int:
        incoming_wait = None
        if client is not None:
            try:
                incoming_wait = estimate_incoming_transport_wait_seconds(client, buyer_city_id)
            except Exception:
                incoming_wait = None
        candidate = int(incoming_wait or 0)
        if candidate <= 0:
            candidate = int(fallback_eta or 0)
        if candidate <= 0:
            candidate = self._TRANSPORTER_RETRY_MIN_SECONDS
        return max(self._TRANSPORTER_RETRY_MIN_SECONDS, candidate + 120)

    def _preview_purchase_eta(
        self,
        client,
        *,
        buyer_city_id: int,
        buyer_branchoffice_pos: int,
        seller_city_id: int,
        resource_idx: int,
        amount: int,
    ) -> dict[str, int]:
        resource_str = "resource" if resource_idx == 0 else str(resource_idx)
        action = BuyAction(client)
        branch_html = action._get_branch_office_html(buyer_city_id, buyer_branchoffice_pos, resource_str)
        offer = action._find_offer_in_listing(branch_html, seller_city_id, resource_str)
        if offer is None:
            raise RuntimeError(f"Offer preview not found for seller_city_id={seller_city_id}")
        take_html = action._get_take_offer_html(
            seller_city_id=offer["city_id"],
            buyer_city_id=buyer_city_id,
            buyer_bo_pos=buyer_branchoffice_pos,
            offer_type=offer["type"],
            resource_str=resource_str,
        )
        free_transporters = _parse_free_transporters(take_html, use_freighters=False)
        ship_capacity = _parse_ship_capacity(take_html, free_transporters, use_freighters=False)
        eta = _parse_transport_times(
            take_html,
            max(1, amount),
            use_freighters=False,
            capacity_percent=100,
        )
        eta["free_transporters"] = int(free_transporters)
        eta["ship_capacity"] = int(ship_capacity)
        return eta

    def _check_purchase_arrival_status(
        self,
        *,
        jid: str,
        client,
        buyer_city_id: int,
        seller_city_id: int,
        resource_idx: int,
        amount: int,
        baseline_amount: int,
        purchase_started_at: int,
        expected_outbound_seconds: int,
        expected_total_seconds: int,
    ) -> dict[str, Any]:
        resource_key = RESOURCE_INPUT_KEYS.get(resource_idx, "wood")
        now_ts = int(time.time())
        deadline_at = purchase_started_at + max(self._ARRIVAL_DEADLINE_SECONDS, expected_total_seconds * 3, expected_outbound_seconds * 4)
        movement = self._find_purchase_movement(client, seller_city_id=seller_city_id, buyer_city_id=buyer_city_id)
        incoming_eta = estimate_incoming_transport_wait_seconds(client, buyer_city_id)
        city_state = fetch_city_state(client, buyer_city_id)
        current_amount = int(city_state.available_resources.get(resource_key, 0))
        delta = current_amount - baseline_amount
        phase = self._classify_purchase_phase(
            movement=movement,
            incoming_eta=incoming_eta,
            baseline_amount=baseline_amount,
            current_amount=current_amount,
            amount=amount,
        )
        confirmation = self._find_trade_advisor_purchase_arrival(
            client,
            buyer_city_id=buyer_city_id,
            seller_city_id=seller_city_id,
            resource_idx=resource_idx,
            amount=amount,
            purchase_started_at=purchase_started_at,
        )
        detail = ""
        if movement:
            event = movement.get("event") or {}
            target = movement.get("target") or {}
            detail = (
                f" mission={event.get('missionText')} returning={event.get('isFleetReturning') or event.get('isReturning')} "
                f"target_city={target.get('cityId')} eta={movement.get('eta_seconds')}"
            )
        self.log(
            jid,
            "info",
            f"Compra interna em acompanhamento | fase={phase} | incoming_eta={incoming_eta} | delta={delta}.{detail}",
        )
        if confirmation is not None:
            return {
                "state": "arrived",
                "mode": "trade_advisor",
                "evidence": confirmation["summary"],
            }

        if now_ts >= deadline_at:
            return {
                "state": "failed",
                "mode": phase,
                "error": f"Purchase arrival not confirmed within timeout for seller_city_id={seller_city_id} buyer_city_id={buyer_city_id}",
            }

        delay_seconds = self._next_purchase_poll_seconds(
            movement=movement,
            incoming_eta=incoming_eta,
            estimated_outbound_seconds=max(expected_outbound_seconds, expected_total_seconds),
        )
        return {
            "state": "pending",
            "mode": phase,
            "delay_seconds": delay_seconds,
            "evidence": confirmation["summary"] if confirmation else (detail.strip() or "aguardando movimento/confirmacao"),
        }

    def _check_raise_gold_arrival_status(
        self,
        *,
        jid: str,
        client,
        buyer_city_id: int,
        seller_city_id: int,
        purchase_started_at: int,
        expected_arrival_seconds: int,
    ) -> dict[str, Any]:
        now_ts = int(time.time())
        deadline_at = purchase_started_at + max(self._ARRIVAL_DEADLINE_SECONDS, expected_arrival_seconds * 4, 10 * 60)
        movement = self._find_purchase_movement(client, seller_city_id=seller_city_id, buyer_city_id=buyer_city_id)
        detail = ""
        if movement:
            event = movement.get("event") or {}
            target = movement.get("target") or {}
            detail = (
                f" mission={event.get('missionText')} returning={event.get('isFleetReturning') or event.get('isReturning')} "
                f"target_city={target.get('cityId')} eta={movement.get('eta_seconds')}"
            )
            self.log(jid, "info", f"Raise-gold em acompanhamento | fase=outbound |{detail}")
            delay_seconds = self._next_purchase_poll_seconds(
                movement=movement,
                incoming_eta=None,
                estimated_outbound_seconds=max(expected_arrival_seconds, self._POLL_MIN_SECONDS),
            )
            return {
                "state": "pending",
                "mode": "outbound",
                "delay_seconds": delay_seconds,
                "evidence": detail.strip() or "comprador em deslocamento",
            }

        arrived_at = purchase_started_at + max(0, expected_arrival_seconds) + self._TRADE_ADVISOR_CONFIRM_MARGIN_SECONDS
        if now_ts >= arrived_at:
            self.log(jid, "info", "Raise-gold confirmado por desaparecimento do movimento apos ETA de chegada.")
            return {
                "state": "arrived",
                "mode": "buyer_arrival",
                "evidence": "militaryAdvisor nao mostra mais o comprador apos o ETA de chegada",
            }

        if now_ts >= deadline_at:
            return {
                "state": "failed",
                "mode": "waiting_visibility",
                "error": f"Raise-gold arrival not confirmed within timeout for seller_city_id={seller_city_id} buyer_city_id={buyer_city_id}",
            }

        delay_seconds = max(
            self._POLL_MIN_SECONDS,
            min(self._POLL_MAX_SECONDS, max(1, arrived_at - now_ts)),
        )
        self.log(jid, "info", "Raise-gold aguardando janela minima de chegada do comprador.")
        return {
            "state": "pending",
            "mode": "waiting_arrival_window",
            "delay_seconds": delay_seconds,
            "evidence": "aguardando ETA minimo de chegada do comprador",
        }

    def _apply_raise_gold_snapshot_updates(
        self,
        *,
        jid: str,
        seller_game_account_id: str,
        credited_gold: int,
        seller_city_id: int,
        order_id: str,
    ) -> None:
        if not seller_game_account_id or credited_gold <= 0:
            return
        try:
            snapshot = self.hub.get_snapshot(game_account_id=seller_game_account_id)
            base_snapshot = dict((snapshot or {}).get("base_snapshot") or {})
            if str(base_snapshot.get("last_raise_gold_order_id") or "").strip() == str(order_id):
                self.log(jid, "info", f"[Order {order_id}] Snapshot de ouro do vendedor ja estava aplicado.")
                return
            current_gold = int(base_snapshot.get("gold") or 0)
            new_gold = current_gold + int(credited_gold)
            self.hub.patch_snapshot_base(
                seller_game_account_id,
                {
                    "gold": new_gold,
                    "last_raise_gold_order_id": str(order_id),
                    "last_raise_gold_city_id": int(seller_city_id),
                    "last_raise_gold_amount": int(credited_gold),
                    "last_raise_gold_updated_at": int(time.time()),
                },
            )
            self.log(
                jid,
                "info",
                f"[Order {order_id}] Snapshot de ouro atualizado para vendedor | {current_gold} -> {new_gold}",
            )
        except Exception as exc:
            self.log(jid, "warn", f"[Order {order_id}] Falha ao atualizar snapshot de ouro do vendedor: {exc}")

    def _retime_followup_construction_job(
        self,
        *,
        jid: str,
        job: dict[str, Any],
        delay_seconds: int,
    ) -> None:
        root_job_id = str(job.get("root_job_id") or "").strip()
        if not root_job_id:
            return
        try:
            resp = self.hub.retime_root_followup_job(
                root_job_id=root_job_id,
                action_code=1002,
                delay_seconds=max(0, int(delay_seconds)),
                exclude_job_id=str(job.get("job_id") or ""),
            )
            if resp.get("updated"):
                self.log(jid, "info", f"Proximo 1002 reagendado pela cadeia raiz em {delay_seconds}s.")
        except Exception as exc:
            self.log(jid, "warn", f"Nao foi possivel reagendar o proximo 1002 pelo ETA do raise-gold: {exc}")

    def _find_purchase_movement(self, client, *, seller_city_id: int, buyer_city_id: int) -> dict[str, Any] | None:
        home = client.session.get(client._server_url, timeout=30).text
        current_city_match = re.search(r'currentCityId\s*[:=]\s*["\']?(\d+)', home)
        current_city_id = current_city_match.group(1) if current_city_match else str(buyer_city_id)
        action_request = GamePageParser().extract_action_request(home) or client._action_request
        response = client.session.post(
            client._server_url,
            params={
                "view": "militaryAdvisor",
                "oldView": "city",
                "oldBackgroundView": "city",
                "backgroundView": "city",
                "currentCityId": current_city_id,
                "actionRequest": action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        data = json.loads(response.text)
        server_time = 0
        movements: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] == "updateGlobalData" and isinstance(item[1], dict):
                try:
                    server_time = int(float(item[1].get("time") or 0))
                except Exception:
                    server_time = 0
            elif item[0] == "changeView" and isinstance(item[1], list):
                try:
                    movements = item[1][2]["viewScriptParams"]["militaryAndFleetMovements"] or []
                except Exception:
                    movements = []

        best: dict[str, Any] | None = None
        for movement in movements:
            if not isinstance(movement, dict) or not bool(movement.get("isOwnArmyOrFleet")):
                continue
            event = movement.get("event") or {}
            mission_text = str(event.get("missionText") or "")
            if "Trocar" not in mission_text and "Comprar" not in mission_text and "trade" not in mission_text.lower():
                continue
            target = movement.get("target") or {}
            source = movement.get("source") or movement.get("origin") or {}
            related = {
                str(target.get("cityId") or ""),
                str(source.get("cityId") or ""),
            }
            if str(seller_city_id) not in related and str(buyer_city_id) not in related:
                continue
            movement = dict(movement)
            event_time = movement.get("eventTime")
            try:
                movement["eta_seconds"] = int(float(event_time)) - server_time if event_time and server_time else None
            except Exception:
                movement["eta_seconds"] = None
            best = movement
            break
        return best

    @staticmethod
    def _classify_purchase_phase(
        *,
        movement: dict[str, Any] | None,
        incoming_eta: int | None,
        baseline_amount: int,
        current_amount: int,
        amount: int,
    ) -> str:
        delta = current_amount - baseline_amount
        if delta >= amount:
            return "stock_arrived"
        if incoming_eta:
            return "returning"
        if movement:
            event = movement.get("event") or {}
            if bool(event.get("isFleetReturning")) or int(event.get("isReturning") or 0) == 1:
                return "returning"
            return "outbound"
        return "waiting_visibility"

    @staticmethod
    def _purchase_arrived(
        *,
        phase: str,
        seen_outbound: bool,
        seen_incoming: bool,
        seen_returning: bool,
        delta: int,
        amount: int,
        movement: dict[str, Any] | None,
        incoming_eta: int | None,
    ) -> bool:
        if delta < amount:
            return False
        if phase == "stock_arrived" and seen_returning:
            return True
        return False

    def _next_purchase_poll_seconds(
        self,
        *,
        movement: dict[str, Any] | None,
        incoming_eta: int | None,
        estimated_outbound_seconds: int,
    ) -> int:
        eta_candidates: list[int] = []
        if movement and movement.get("eta_seconds") is not None:
            try:
                eta_candidates.append(int(movement["eta_seconds"]))
            except Exception:
                pass
        if incoming_eta is not None:
            eta_candidates.append(int(incoming_eta))
        if not eta_candidates:
            eta_candidates.append(int(estimated_outbound_seconds or self._POLL_MAX_SECONDS))
        target = max(0, min(eta_candidates))
        if target <= self._POLL_MIN_SECONDS:
            return self._POLL_MIN_SECONDS
        return max(self._POLL_MIN_SECONDS, min(self._POLL_MAX_SECONDS, target + self._TRADE_ADVISOR_CONFIRM_MARGIN_SECONDS))

    def _fetch_trade_advisor_html(self, client, *, buyer_city_id: int) -> str:
        home = client.session.get(client._server_url, timeout=30).text
        current_city_match = re.search(r'currentCityId\s*[:=]\s*["\']?(\d+)', home)
        current_city_id = current_city_match.group(1) if current_city_match else str(buyer_city_id)
        action_request = GamePageParser().extract_action_request(home) or client._action_request
        response = client.session.post(
            client._server_url,
            params={
                "view": "tradeAdvisor",
                "oldView": "tradeAdvisor",
                "cityId": buyer_city_id,
                "backgroundView": "city",
                "currentCityId": current_city_id,
                "actionRequest": action_request,
                "ajax": 1,
            },
            timeout=30,
        )
        try:
            data = json.loads(response.text)
        except Exception:
            return ""
        for item in data:
            if not isinstance(item, list) or len(item) < 2 or item[0] != "changeView":
                continue
            payload = item[1]
            if not isinstance(payload, list):
                continue
            for part in payload:
                if isinstance(part, str) and len(part) > 500:
                    return part
        return ""

    def _find_trade_advisor_purchase_arrival(
        self,
        client,
        *,
        buyer_city_id: int,
        seller_city_id: int,
        resource_idx: int,
        amount: int,
        purchase_started_at: int,
    ) -> dict[str, Any] | None:
        html = self._fetch_trade_advisor_html(client, buyer_city_id=buyer_city_id)
        if not html:
            return None
        amount_token = f"{int(amount):,}".replace(",", ".")
        resource_title = {
            0: "Materiais de construção",
            1: "Vinho",
            2: "Mármore",
            3: "Cristal",
            4: "Enxofre",
        }.get(resource_idx, "")
        row_pattern = re.compile(
            rf'<td class="date">\s*([^<]+?)\s*</td>\s*<td class="subject">\s*'
            rf'A sua frota comercial voltou de <a href="\?view=island&cityId={int(seller_city_id)}">.*?</a> '
            rf'para a sua cidade <a href="\?view=island&cityId={int(buyer_city_id)}">.*?</a> e est[aá]\s+descarregando:\s*'
            rf'<ul class="resources"><li class="value">\s*{re.escape(amount_token)}\s*<img[^>]+title="{re.escape(resource_title)}"',
            re.I | re.S,
        )
        tzinfo = datetime.now().astimezone().tzinfo
        for match in row_pattern.finditer(html):
            raw_date = " ".join(str(match.group(1) or "").split())
            try:
                parsed = datetime.strptime(raw_date, "%d.%m.%Y %H:%M").replace(tzinfo=tzinfo)
                event_ts = int(parsed.timestamp())
            except Exception:
                event_ts = 0
            if event_ts and event_ts + self._TRADE_ADVISOR_CONFIRM_MARGIN_SECONDS < purchase_started_at:
                continue
            return {
                "summary": f"tradeAdvisor confirmou descarga em {raw_date}",
                "event_ts": event_ts,
            }
        return None
