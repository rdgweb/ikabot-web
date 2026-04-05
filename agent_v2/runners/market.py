"""
Market runners — buying and selling on the trade market.

Action codes:
    8  buy_market
    9  sell_market
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


@register_runner(8)
class BuyMarketRunner(BaseRunner):
    """Buy a resource offer from the trade market.

    Inputs:
        city_id       — city that receives the goods
        offer_id      — market offer identifier
        resource_type — resource to buy (optional, for validation)
        amount        — quantity to buy
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Buying from market for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.buy_market(city_id, offer_id, amount)
            # city_id  = inputs["city_id"]
            # offer_id = inputs["offer_id"]
            # amount   = inputs["amount"]
            # client.buy_market(city_id, offer_id, amount)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Market purchase complete")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Market buy failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(9)
class SellMarketRunner(BaseRunner):
    """Create a sell offer on the trade market.

    Inputs:
        city_id       — city selling the goods
        resource_type — resource to sell
        amount        — quantity to offer
        price         — price per unit (optional, use market default)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Selling on market for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.sell_market(city_id, resource_type, amount, price)
            # city_id       = inputs["city_id"]
            # resource_type = inputs["resource_type"]
            # amount        = inputs["amount"]
            # price         = inputs.get("price")
            # client.sell_market(city_id, resource_type, amount, price)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Market sell offer created")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Market sell failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
