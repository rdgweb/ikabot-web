"""
Market runners — internal market buy/sell via Branch Office.

Action codes:
    8   BuyMarketRunner        — buy from any listing (generic, manual use)
    9   SellMarketRunner       — create a sell offer (generic, manual use)
    801 InternalMarketBuyRunner  — buy half of an InternalMarketOrder
    802 InternalMarketSellRunner — sell half of an InternalMarketOrder

Internal market flow
--------------------
1. Hub creates InternalMarketOrder + sell_job (802) via services.create_internal_order().
2. Runner 802 (seller's node):
   - Logs in to game as seller.
   - Posts a sell offer via CityScreen&function=updateOffers.
   - Calls hub: POST /api/agent/market/orders/<id>/sell-complete/
   - Hub creates buy_job (801) on buyer's node.
3. Runner 801 (buyer's node):
   - Logs in to game as buyer.
   - Scrapes buyer's Branch Office listing to locate seller's offer.
   - Buys the offer via transportOperations&function=buyGoodsAtAnotherBranchOffice.
   - Calls hub: POST /api/agent/market/orders/<id>/complete/
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


# ── Generic runners (manual / external market use) ──────────────────────────


@register_runner(9)
class SellMarketRunner(BaseRunner):
    """Create a sell offer on the Branch Office (generic).

    Inputs:
        city_id            — seller city ID
        branchoffice_pos   — Branch Office slot in the city
        resource_idx       — 0=wood 1=wine 2=marble 3=crystal 4=sulfur
        amount             — units to list
        unit_price         — gold per unit
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        city_id = inputs.get("city_id")
        bo_pos = inputs.get("branchoffice_pos")
        resource_idx = int(inputs.get("resource_idx", 0))
        amount = int(inputs.get("amount", 0))
        unit_price = int(inputs.get("unit_price", 12))

        if not city_id or bo_pos is None or amount <= 0:
            self.log(jid, "error", "Missing required inputs: city_id, branchoffice_pos, amount")
            return RunnerResult(success=False, data={"error": "missing inputs"})

        self.log(jid, "info", f"Creating sell offer: city={city_id} res={resource_idx} x{amount} @{unit_price}")

        try:
            client = self.get_game_session(aid)
            client.create_market_offer(
                city_id=int(city_id),
                branchoffice_pos=int(bo_pos),
                resource_idx=resource_idx,
                amount=amount,
                unit_price=unit_price,
            )
            self.save_game_session(aid, client)
            self.log(jid, "info", "Sell offer created")
            return RunnerResult(success=True)
        except Exception as exc:
            self.log(jid, "error", f"Sell offer failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(8)
class BuyMarketRunner(BaseRunner):
    """Buy a resource offer from a specific Branch Office (generic).

    Inputs:
        buyer_city_id          — city where goods are received
        buyer_branchoffice_pos — buyer's Branch Office slot
        seller_city_id         — seller's city ID
        seller_branchoffice_pos — seller's Branch Office slot
        resource_idx           — resource type (0–4)
        amount                 — units to buy
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
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

        try:
            client = self.get_game_session(aid)
            client.buy_market_offer(
                buyer_city_id=int(buyer_city_id),
                buyer_branchoffice_pos=int(buyer_bo),
                seller_city_id=int(seller_city_id),
                seller_branchoffice_pos=int(seller_bo),
                resource_idx=resource_idx,
                amount=amount,
            )
            self.save_game_session(aid, client)
            self.log(jid, "info", "Market purchase complete")
            return RunnerResult(success=True)
        except Exception as exc:
            self.log(jid, "error", f"Market buy failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


# ── Internal market runners ──────────────────────────────────────────────────


@register_runner(802)
class InternalMarketSellRunner(BaseRunner):
    """Create a sell offer for an InternalMarketOrder (action 802).

    Inputs (set by hub matching service):
        city_id            — seller city ID
        branchoffice_pos   — Branch Office slot
        resource_idx       — resource type (0–4)
        amount             — units to sell
        unit_price         — gold per unit
        internal_order_id  — UUID of InternalMarketOrder

    On success: notifies hub → hub creates buy_job (801) on buyer's node.
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        city_id = inputs.get("city_id")
        bo_pos = inputs.get("branchoffice_pos")
        resource_idx = int(inputs.get("resource_idx", 0))
        amount = int(inputs.get("amount", 0))
        unit_price = int(inputs.get("unit_price", 12))
        order_id = inputs.get("internal_order_id")

        if not city_id or bo_pos is None or amount <= 0 or not order_id:
            self.log(jid, "error", "Missing required inputs for InternalMarketSellRunner")
            return RunnerResult(success=False, data={"error": "missing inputs"})

        self.log(
            jid, "info",
            f"[Order {order_id}] Creating sell offer: city={city_id} "
            f"res={resource_idx} x{amount} @{unit_price}",
        )

        try:
            client = self.get_game_session(aid)
            client.create_market_offer(
                city_id=int(city_id),
                branchoffice_pos=int(bo_pos),
                resource_idx=resource_idx,
                amount=amount,
                unit_price=unit_price,
            )
            self.save_game_session(aid, client)
            self.log(jid, "info", f"[Order {order_id}] Sell offer placed in game")

            # Notify hub → creates buy_job (801) on buyer's node
            resp = self.hub.market_order_sell_complete(order_id)
            buy_job_id = resp.get("buy_job_id", "?")
            self.log(jid, "info", f"[Order {order_id}] Buy job created: {buy_job_id}")

            return RunnerResult(success=True, data={"buy_job_id": buy_job_id})

        except Exception as exc:
            self.log(jid, "error", f"[Order {order_id}] Sell failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(801)
class InternalMarketBuyRunner(BaseRunner):
    """Buy the offer placed by InternalMarketSellRunner (action 801).

    Inputs (set by hub when creating this job):
        buyer_city_id          — buyer's city ID
        buyer_branchoffice_pos — buyer's Branch Office slot
        seller_city_id         — seller's city ID
        seller_branchoffice_pos — seller's Branch Office slot
        resource_idx           — resource type (0–4)
        amount                 — units to buy
        internal_order_id      — UUID of InternalMarketOrder

    On success: notifies hub → order marked as completed.
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        buyer_city_id = inputs.get("buyer_city_id")
        buyer_bo = inputs.get("buyer_branchoffice_pos")
        seller_city_id = inputs.get("seller_city_id")
        seller_bo = inputs.get("seller_branchoffice_pos")
        resource_idx = int(inputs.get("resource_idx", 0))
        amount = int(inputs.get("amount", 0))
        order_id = inputs.get("internal_order_id")

        if not all([
            buyer_city_id, buyer_bo is not None,
            seller_city_id, seller_bo is not None,
            amount, order_id,
        ]):
            self.log(jid, "error", "Missing required inputs for InternalMarketBuyRunner")
            return RunnerResult(success=False, data={"error": "missing inputs"})

        self.log(
            jid, "info",
            f"[Order {order_id}] Buying res={resource_idx} x{amount} "
            f"from city={seller_city_id}",
        )

        MAX_OFFER_RETRIES = 5
        offer_retry_count = int(inputs.get("offer_retry_count", 0))

        try:
            client = self.get_game_session(aid)
            client.buy_market_offer(
                buyer_city_id=int(buyer_city_id),
                buyer_branchoffice_pos=int(buyer_bo),
                seller_city_id=int(seller_city_id),
                seller_branchoffice_pos=int(seller_bo),
                resource_idx=resource_idx,
                amount=amount,
            )
            self.save_game_session(aid, client)
            self.log(jid, "info", f"[Order {order_id}] Purchase executed")

            # Notify hub → order marked as completed
            self.hub.market_order_complete(order_id)
            self.log(jid, "info", f"[Order {order_id}] Order marked as completed")

            return RunnerResult(success=True)

        except Exception as exc:
            exc_str = str(exc)
            # If the seller's offer is not yet visible in the listing, retry
            if "not found" in exc_str.lower() and offer_retry_count < MAX_OFFER_RETRIES:
                next_retry = offer_retry_count + 1
                self.log(
                    jid, "warn",
                    f"[Order {order_id}] Offer not found in listing "
                    f"(attempt {next_retry}/{MAX_OFFER_RETRIES}); retrying in 60s",
                )
                retry_inputs = dict(inputs)
                retry_inputs["offer_retry_count"] = next_retry
                self.hub.reschedule_job(jid, delay_seconds=60, inputs=retry_inputs)
                return RunnerResult(success=True, data={"status": "retry_scheduled", "retry": next_retry})
            self.log(jid, "error", f"[Order {order_id}] Buy failed: {exc}")
            return RunnerResult(success=False, data={"error": exc_str})
