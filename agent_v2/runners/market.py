"""
Market runners for generic and internal Branch Office operations.
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

RESOURCE_LABELS = {
    0: "Madeira",
    1: "Vinho",
    2: "Marmore",
    3: "Cristal",
    4: "Enxofre",
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
        city_name = str(inputs.get("city_name") or city_id or "").strip()
        buyer_city_name = str(inputs.get("buyer_city_name") or inputs.get("buyer_city_id") or "").strip()
        resource_label = RESOURCE_LABELS.get(resource_idx, f"res={resource_idx}")

        if not city_id or bo_pos is None or amount <= 0 or not order_id:
            self.log(jid, "error", "Missing required inputs for InternalMarketSellRunner")
            return RunnerResult(success=False, data={"error": "missing inputs"})

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

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", f"[Order {order_id}] Credenciais nao encontradas")
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
            self.log(jid, "info", f"[Order {order_id}] Compra executada | destino={buyer_city_name} | origem={seller_city_name}")
            self.hub.market_order_complete(order_id)
            self.log(jid, "info", f"[Order {order_id}] Order marked as completed")
            return RunnerResult(success=True)
        except Exception as exc:
            exc_str = str(exc)
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
            self.log(jid, "error", f"[Order {order_id}] Buy failed: {exc}")
            return RunnerResult(success=False, data={"error": exc_str})
