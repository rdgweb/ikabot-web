"""N-84: the internal market fills what a seller can deliver instead of demanding
the whole amount from a single city (a buyer that needs 100k while the markets
hold 30k used to get nothing)."""

import json

from django.test import TestCase, override_settings

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot

from .models import InternalMarketOrder
from apps.jobs.models import Job
from apps.jobs.services.workflows import create_job_with_workflow

from .services import (
    INTERNAL_MARKET_MIN_PARTIAL,
    create_internal_order_result,
    iter_seller_candidates,
    create_buy_job,
    reconcile_internal_order_for_job,
)

MARBLE = 2


def _seller_city(city_id, marble, *, bo_level=20, on_offer=0, x=11, y=11):
    return {
        "id": city_id, "name": f"Seller {city_id}", "x": x, "y": y,
        "wood": 0, "wine": 0, "marble": marble, "glas": 0, "sulfur": 0,
        "market_resources": {"2": on_offer},
        "buildings": [{"building": "branchOffice", "position": 4, "level": bo_level}],
    }


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class PartialFillTests(TestCase):
    def setUp(self):
        self.buyer_account = Account.objects.create(
            node=Node.objects.create(name="buyer-node"), label="Buyer", email="buyer@example.com", password_enc="x",
        )
        self.buyer_ga = GameAccount.objects.create(
            account=self.buyer_account, lobby_account_id=1, server_id="s1-br", server_language="br",
            server_number=1, name="BuyerGA", market_min_gold=0,
        )
        AccountSnapshot.objects.create(
            account=self.buyer_account, game_account=self.buyer_ga, base_snapshot={"gold": 99_999_999},
            cities=[{"id": 101, "name": "Capital", "x": 10, "y": 10,
                     "buildings": [{"building": "branchOffice", "position": 6, "level": 20}]}],
        )
        self._seq = 10

    def _seller(self, name, cities, *, min_stock=0):
        self._seq += 1
        account = Account.objects.create(
            node=Node.objects.create(name=f"node-{name}"), label=name, email=f"{name}@example.com", password_enc="x",
        )
        ga = GameAccount.objects.create(
            account=account, lobby_account_id=self._seq, server_id="s1-br", server_language="br",
            server_number=1, name=name, open_for_market=True, market_min_stock=min_stock,
        )
        AccountSnapshot.objects.create(account=account, game_account=ga, base_snapshot={"gold": 1}, cities=cities)
        return ga

    def _order(self, amount):
        return create_internal_order_result(
            self.buyer_ga, MARBLE, amount, target_city_id=101,
            source_reason="construction_missing_resources", reason_detail="Capital | Armazem | missing marble",
        )

    def test_buyer_gets_what_the_seller_can_deliver(self):
        self._seller("small", [_seller_city(303, 30_000)])

        result = self._order(100_000)

        self.assertTrue(result.ok)
        self.assertEqual(result.order.amount, 30_000)
        self.assertIn("pedido 100000, atendido 30000", result.order.reason_detail)
        self.assertEqual(json.loads(result.order.sell_job.inputs_json)["amount"], 30_000)

    def test_seller_that_covers_everything_is_preferred(self):
        self._seller("partial", [_seller_city(303, 90_000)])
        full = self._seller("full", [_seller_city(404, 150_000)])

        result = self._order(100_000)

        self.assertEqual((result.order.amount, result.order.seller_game_account_id), (100_000, full.pk))
        self.assertNotIn("atendido", result.order.reason_detail)

    def test_among_partial_sellers_the_biggest_goes_first(self):
        self._seller("a", [_seller_city(303, 12_000)])
        big = self._seller("b", [_seller_city(404, 40_000)])

        candidates = iter_seller_candidates(self.buyer_ga, MARBLE, 100_000)

        self.assertEqual([(ga.pk, sellable) for ga, _city, _pos, sellable in candidates][0], (big.pk, 40_000))
        self.assertEqual(self._order(100_000).order.amount, 40_000)

    def test_market_capacity_and_minimum_stock_limit_the_fill(self):
        # level 10 market holds 40,000; 25,000 already on offer -> 15,000 free
        self._seller("cap", [_seller_city(303, 200_000, bo_level=10, on_offer=25_000)])
        self.assertEqual(self._order(100_000).order.amount, 15_000)

        InternalMarketOrder.objects.all().delete()
        self._seller("reserve", [_seller_city(505, 60_000)], min_stock=50_000)
        candidates = {ga.name: sellable for ga, _c, _p, sellable in iter_seller_candidates(self.buyer_ga, MARBLE, 100_000)}
        self.assertEqual(candidates["reserve"], 10_000)

    def test_too_little_is_not_worth_an_order(self):
        self._seller("crumbs", [_seller_city(303, INTERNAL_MARKET_MIN_PARTIAL - 1)])

        result = self._order(100_000)

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "no_seller")
        self.assertEqual(InternalMarketOrder.objects.count(), 0)

    def test_small_request_is_filled_whole_or_not_at_all(self):
        self._seller("tiny", [_seller_city(303, 3_000)])
        self.assertFalse(self._order(4_000).ok)       # 3,000 < the partial minimum
        self.assertEqual(self._order(2_500).order.amount, 2_500)

    def test_api_tells_the_agent_how_much_was_granted(self):
        self._seller("small", [_seller_city(303, 30_000)])

        response = self.client.post(
            "/api/agent/market/orders/create/",
            data=json.dumps({
                "game_account_id": str(self.buyer_ga.pk), "resource_idx": MARBLE, "amount": 100_000,
                "target_city_id": 101, "source_reason": "construction_missing_resources",
            }),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual((body["amount"], body["requested_amount"]), (30_000, 100_000))


    def test_buy_job_carries_the_range_that_reaches_the_seller(self):
        # buyer city at [10:10]; seller cities one and four steps away
        self._seller("near", [_seller_city(303, 50_000, x=11, y=11)])
        near = create_buy_job(self._order(10_000).order)
        self.assertEqual(json.loads(near.inputs_json)["market_range"], 2)          # distance 1 + one step of margin
        self.assertEqual(json.loads(near.inputs_json)["buyer_city_name"], "Capital")
        self.assertEqual(json.loads(near.inputs_json)["seller_city_name"], "Seller 303")

        InternalMarketOrder.objects.all().delete()
        GameAccount.objects.filter(name="near").update(open_for_market=False)
        self._seller("far", [_seller_city(404, 50_000, x=14, y=9)])
        far = create_buy_job(self._order(10_000).order)
        self.assertEqual(json.loads(far.inputs_json)["market_range"], 5)

    def test_unknown_coordinates_leave_the_range_to_the_agent(self):
        city = _seller_city(303, 50_000)
        city.pop("x"); city.pop("y")
        self._seller("nocoords", [city])

        job = create_buy_job(self._order(10_000).order)

        self.assertEqual(json.loads(job.inputs_json)["market_range"], 0)

    def _failed_buy(self, order, *, delivered):
        Job.objects.filter(pk=order.sell_job_id).update(status="finished")
        order.refresh_from_db()
        buy_job = create_job_with_workflow(
            account=self.buyer_account, game_account=self.buyer_ga, node=self.buyer_account.node,
            action_code=801, status="queued",
            inputs={"internal_order_id": str(order.pk), "amount": order.amount - delivered,
                    "order_total_amount": order.amount, "order_completed_amount": delivered},
        )
        Job.objects.filter(pk=buy_job.pk).update(status="error")
        buy_job.refresh_from_db()
        order.buy_job = buy_job
        order.status = "jobs_running"
        order.save(update_fields=["buy_job", "status"])
        reconcile_internal_order_for_job(buy_job, terminal_status="error", note="buy timeout")
        order.refresh_from_db()
        cleanup = [j for j in Job.objects.filter(action_code=802) if '"cleanup_only": true' in j.inputs_json]
        return order, (json.loads(cleanup[-1].inputs_json) if cleanup else None)

    def test_cleanup_after_a_partly_delivered_order_targets_only_what_was_not_bought(self):
        self._seller("big", [_seller_city(303, 500_000, bo_level=30)])
        order = self._order(70_149).order

        order, cleanup = self._failed_buy(order, delivered=60_500)

        self.assertEqual(order.status, "failed")
        self.assertEqual(cleanup["cleanup_leftover_amount"], 9_649)
        self.assertEqual(cleanup["cleanup_keep_total"], 0)
        self.assertIn("entregue=60500 sobra=9649", order.result_note)

    def test_cleanup_keeps_what_other_open_orders_published_in_the_same_city(self):
        self._seller("big", [_seller_city(303, 500_000, bo_level=30)])
        first = self._order(20_000).order
        # a second buyer city of the same account asks the same seller city
        other = InternalMarketOrder.objects.get(pk=first.pk)
        other.pk = None
        other.amount = 7_000
        other.status = "jobs_running"
        other.save()
        other.sell_job = create_job_with_workflow(
            account=first.seller_account, game_account=first.seller_game_account, node=first.seller_node,
            action_code=802, status="queued", inputs={"internal_order_id": str(other.pk)},
        )
        other.save(update_fields=["sell_job"])
        Job.objects.filter(pk=other.sell_job_id).update(status="finished")

        _order, cleanup = self._failed_buy(first, delivered=0)

        self.assertEqual((cleanup["cleanup_leftover_amount"], cleanup["cleanup_keep_total"]), (20_000, 7_000))
