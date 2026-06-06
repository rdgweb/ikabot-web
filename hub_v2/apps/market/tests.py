from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job

from .services import (
    approve_construction_market_intervention,
    cancel_internal_order,
    complete_internal_order,
    create_buy_job,
    create_internal_order,
    create_internal_order_result,
    reconcile_internal_order_for_job,
)
from .models import ConstructionMarketIntervention, InternalMarketOrder


class MarketServiceTests(TestCase):
    def setUp(self):
        self.buyer_node = Node.objects.create(name="buyer-node")
        self.seller_node = Node.objects.create(name="seller-node")
        self.buyer_account = Account.objects.create(
            node=self.buyer_node,
            label="Buyer",
            email="buyer@example.com",
            password_enc="x",
        )
        self.seller_account = Account.objects.create(
            node=self.seller_node,
            label="Seller",
            email="seller@example.com",
            password_enc="x",
        )
        self.buyer_ga = GameAccount.objects.create(
            account=self.buyer_account,
            lobby_account_id=1,
            server_id="s1-br",
            server_language="br",
            server_number=1,
            name="BuyerGA",
            market_min_gold=0,
        )
        self.seller_ga = GameAccount.objects.create(
            account=self.seller_account,
            lobby_account_id=2,
            server_id="s1-br",
            server_language="br",
            server_number=1,
            name="SellerGA",
            open_for_market=True,
            market_min_stock=0,
        )
        AccountSnapshot.objects.create(
            account=self.buyer_account,
            game_account=self.buyer_ga,
            base_snapshot={"gold": 999999},
            cities=[
                {
                    "id": 101,
                    "name": "Capital",
                    "x": 10,
                    "y": 10,
                    "wood": 1000,
                    "buildings": [{"building": "branchOffice", "position": 6, "level": 4}],
                },
                {
                    "id": 202,
                    "name": "Marble City",
                    "x": 11,
                    "y": 10,
                    "wood": 500,
                    "buildings": [{"building": "branchOffice", "position": 8, "level": 12}],
                },
                {
                    "id": 404,
                    "name": "Target Without BO",
                    "x": 50,
                    "y": 50,
                    "wood": 0,
                    "buildings": [{"building": "warehouse", "position": 3}],
                },
            ],
        )
        AccountSnapshot.objects.create(
            account=self.seller_account,
            game_account=self.seller_ga,
            base_snapshot={"gold": 999999},
            cities=[
                {
                    "id": 303,
                    "name": "Seller Port",
                    "x": 11,
                    "y": 11,
                    "wood": 50000,
                    "wine": 0,
                    "marble": 0,
                    "glas": 0,
                    "sulfur": 0,
                    "buildings": [{"building": "branchOffice", "position": 4, "level": 20}],
                }
            ],
        )

    def test_create_internal_order_uses_preferred_buyer_city(self):
        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=1000,
            unit_price=12,
            preferred_buyer_city_id=202,
        )

        self.assertIsNotNone(order)
        self.assertEqual(order.buyer_city_id, 202)
        self.assertEqual(order.buyer_branchoffice_pos, 8)
        self.assertEqual(order.price_min, 0)
        self.assertEqual(order.price_max, 0)
        self.assertEqual(order.target_city_id, 202)

    def test_create_internal_order_result_exposes_buyer_below_min_gold(self):
        self.buyer_ga.market_min_gold = 2_000_000
        self.buyer_ga.save(update_fields=["market_min_gold", "updated_at"])

        result = create_internal_order_result(
            self.buyer_ga,
            resource_idx=0,
            amount=1000,
            unit_price=0,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "buyer_below_min_gold")
        self.assertIsNone(result.order)

    def test_create_internal_order_skips_seller_without_market_free_capacity(self):
        seller_snapshot = AccountSnapshot.objects.get(game_account=self.seller_ga)
        seller_snapshot.cities = [
            {
                "id": 303,
                "name": "Small Market",
                "x": 11,
                "y": 11,
                "wood": 50000,
                "buildings": [{"building": "branchOffice", "position": 4, "level": 3}],
                "market_resources": {"resource": 2570, "1": 0, "2": 0, "3": 0, "4": 0},
            }
        ]
        seller_snapshot.save(update_fields=["cities"])

        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=4627,
            unit_price=0,
        )

        self.assertIsNone(order)

    def test_buy_failure_schedules_cleanup_when_market_total_matches_bot_expected_total(self):
        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=1000,
            unit_price=0,
        )
        self.assertIsNotNone(order)
        order.sell_job.status = "finished"
        order.sell_job.save(update_fields=["status", "updated_at"])
        create_buy_job(order)

        seller_snapshot = AccountSnapshot.objects.get(game_account=self.seller_ga)
        seller_snapshot.cities[0]["market_resources"] = {"resource": 1000, "1": 0, "2": 0, "3": 0, "4": 0}
        seller_snapshot.save(update_fields=["cities"])

        order.buy_job.status = "error"
        order.buy_job.save(update_fields=["status", "updated_at"])
        reconcile_internal_order_for_job(order.buy_job, terminal_status="error", note="test failure")

        cleanup = Job.objects.filter(action_code=802, source_job=order.buy_job).latest("created_at")
        self.assertIn('"cleanup_only": true', cleanup.inputs_json)
        self.assertIn('"offer_mode": "clear"', cleanup.inputs_json)
        self.assertIn('"amount": 0', cleanup.inputs_json)
        self.assertIn('"expected_current_total": 1000', cleanup.inputs_json)

    def test_buy_failure_cleanup_preserves_other_published_internal_orders(self):
        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=1000,
            unit_price=0,
        )
        self.assertIsNotNone(order)
        order.sell_job.status = "finished"
        order.sell_job.save(update_fields=["status", "updated_at"])
        create_buy_job(order)

        other_order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=600,
            unit_price=0,
            target_city_id=202,
            source_reason="other_need",
        )
        self.assertIsNotNone(other_order)
        other_order.sell_job.status = "finished"
        other_order.sell_job.save(update_fields=["status", "updated_at"])

        order.buy_job.status = "error"
        order.buy_job.save(update_fields=["status", "updated_at"])
        reconcile_internal_order_for_job(order.buy_job, terminal_status="error", note="test failure")

        cleanup = Job.objects.filter(action_code=802, source_job=order.buy_job).latest("created_at")
        self.assertIn('"cleanup_only": true', cleanup.inputs_json)
        self.assertIn('"offer_mode": "replace"', cleanup.inputs_json)
        self.assertIn('"amount": 600', cleanup.inputs_json)
        self.assertIn('"expected_current_total": 1600', cleanup.inputs_json)

    def test_create_internal_order_falls_back_to_other_bo_city_but_keeps_target_city(self):
        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=1000,
            unit_price=0,
            preferred_buyer_city_id=404,
            target_city_id=404,
        )

        self.assertIsNotNone(order)
        self.assertEqual(order.buyer_city_id, 202)
        self.assertEqual(order.target_city_id, 404)

    def test_create_internal_order_uses_visible_buyer_city_for_seller_range(self):
        buyer_snapshot = AccountSnapshot.objects.get(game_account=self.buyer_ga)
        buyer_snapshot.cities = [
            {
                "id": 101,
                "name": "Short Range",
                "x": 10,
                "y": 10,
                "wood": 1000,
                "buildings": [{"building": "branchOffice", "position": 6, "level": 2}],
            },
            {
                "id": 202,
                "name": "Long Range",
                "x": 80,
                "y": 80,
                "wood": 500,
                "buildings": [{"building": "branchOffice", "position": 8, "level": 20}],
            },
            {
                "id": 404,
                "name": "Target Without BO",
                "x": 81,
                "y": 81,
                "wood": 0,
                "buildings": [{"building": "warehouse", "position": 3}],
            },
        ]
        buyer_snapshot.save(update_fields=["cities"])

        seller_snapshot = AccountSnapshot.objects.get(game_account=self.seller_ga)
        seller_snapshot.cities = [
            {
                "id": 303,
                "name": "Seller Port",
                "x": 83,
                "y": 83,
                "wood": 50000,
                "wine": 0,
                "marble": 0,
                "glas": 0,
                "sulfur": 0,
                "buildings": [{"building": "branchOffice", "position": 4, "level": 20}],
            }
        ]
        seller_snapshot.save(update_fields=["cities"])

        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=1000,
            unit_price=0,
            preferred_buyer_city_id=404,
            target_city_id=404,
        )

        self.assertIsNotNone(order)
        self.assertEqual(order.buyer_city_id, 202)
        self.assertEqual(order.target_city_id, 404)

    def test_create_internal_order_matches_crystal_from_snapshot_aliases(self):
        seller_snapshot = AccountSnapshot.objects.get(game_account=self.seller_ga)
        seller_snapshot.cities = [
            {
                "id": 303,
                "name": "Seller Port",
                "wood": 0,
                "wine": 0,
                "marble": 0,
                "crystal": 5000,
                "buildings": [{"building": "branchOffice", "position": 4}],
            }
        ]
        seller_snapshot.save(update_fields=["cities"])

        order = create_internal_order(
            self.buyer_ga,
            resource_idx=3,
            amount=2000,
            unit_price=0,
        )

        self.assertIsNotNone(order)
        self.assertEqual(order.resource_idx, 3)
        self.assertEqual(order.seller_city_id, 303)

    def test_market_jobs_keep_source_chain(self):
        parent_job = Job.objects.create(
            account=self.buyer_account,
            game_account=self.buyer_ga,
            node=self.buyer_node,
            action_code=1002,
            inputs_json="{}",
            status="queued",
        )
        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=500,
            unit_price=0,
            source_job_id=str(parent_job.pk),
        )

        self.assertIsNotNone(order)
        self.assertEqual(str(order.sell_job.source_job_id), str(parent_job.pk))
        self.assertEqual(str(order.sell_job.root_job_id), str(parent_job.pk))

        buy_job = create_buy_job(order)
        self.assertIsNotNone(buy_job)
        self.assertEqual(str(buy_job.source_job_id), str(order.sell_job.pk))
        self.assertEqual(str(buy_job.root_job_id), str(parent_job.pk))

    def test_cancel_internal_order_cancels_active_job_chain(self):
        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=500,
            unit_price=0,
        )
        self.assertIsNotNone(order)
        buy_job = create_buy_job(order)
        self.assertIsNotNone(buy_job)

        order.sell_job.status = "finished"
        order.sell_job.save(update_fields=["status", "updated_at"])
        buy_job.status = "running"
        buy_job.save(update_fields=["status", "updated_at"])

        retry_job = Job.objects.create(
            account=self.buyer_account,
            game_account=self.buyer_ga,
            node=self.buyer_node,
            action_code=801,
            source_job_id=buy_job.pk,
            root_job_id=order.sell_job.pk,
            inputs_json="{}",
            status="scheduled",
        )

        result = cancel_internal_order(order)

        order.refresh_from_db()
        buy_job.refresh_from_db()
        retry_job.refresh_from_db()

        self.assertEqual(order.status, "canceled")
        self.assertEqual(buy_job.status, "cancelled")
        self.assertEqual(retry_job.status, "cancelled")
        self.assertEqual(result["jobs_cancelled"], 2)

    def test_complete_internal_order_creates_redistribution_when_target_differs(self):
        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=500,
            unit_price=0,
            preferred_buyer_city_id=404,
            target_city_id=404,
        )
        self.assertIsNotNone(order)
        create_buy_job(order)

        result = complete_internal_order(order)

        order.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertTrue(result["created_redistribution"])
        self.assertEqual(order.status, "jobs_running")
        self.assertIsNotNone(order.redistribution_job_id)
        self.assertEqual(order.redistribution_job.action_code, 2)

    def test_reconcile_internal_order_marks_buy_failure_as_failed(self):
        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=500,
            unit_price=0,
        )
        self.assertIsNotNone(order)
        buy_job = create_buy_job(order)

        reconcile_internal_order_for_job(buy_job, terminal_status="error", note="transporters missing")

        order.refresh_from_db()
        self.assertEqual(order.status, "failed")
        self.assertIn("transporters missing", order.result_note)

    def test_reconcile_internal_order_keeps_order_running_when_buy_retry_exists(self):
        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=500,
            unit_price=0,
        )
        self.assertIsNotNone(order)
        buy_job = create_buy_job(order)

        retry_job = Job.objects.create(
            account=self.buyer_account,
            game_account=self.buyer_ga,
            node=self.buyer_node,
            action_code=801,
            source_job_id=buy_job.pk,
            root_job_id=buy_job.root_job_id,
            inputs_json='{"internal_order_id":"%s"}' % order.pk,
            status="scheduled",
        )

        reconcile_internal_order_for_job(buy_job, terminal_status="finished")

        order.refresh_from_db()
        self.assertEqual(order.status, "jobs_running")
        self.assertEqual(order.buy_job_id, retry_job.pk)
        self.assertEqual(order.result_note, "Compra interna reagendada; aguardando nova tentativa.")

    def test_reconcile_internal_order_completes_on_arrival_monitor_finish(self):
        order = create_internal_order(
            self.buyer_ga,
            resource_idx=0,
            amount=500,
            unit_price=0,
            preferred_buyer_city_id=404,
            target_city_id=404,
        )
        self.assertIsNotNone(order)
        create_buy_job(order)
        result = complete_internal_order(order)
        self.assertTrue(result["created_redistribution"])

        arrival_monitor = Job.objects.create(
            account=self.buyer_account,
            game_account=self.buyer_ga,
            node=self.buyer_node,
            action_code=2,
            source_job_id=order.redistribution_job_id,
            root_job_id=order.redistribution_job.root_job_id,
            inputs_json='{"monitor_mode":"arrival_check","internal_order_id":"%s"}' % order.pk,
            status="finished",
        )

        reconcile_internal_order_for_job(arrival_monitor, terminal_status="finished")

        order.refresh_from_db()
        self.assertEqual(order.status, "completed")

    def test_approve_construction_market_intervention_is_idempotent_after_internal_order_exists(self):
        self.buyer_ga.open_for_market = True
        self.buyer_ga.save(update_fields=["open_for_market", "updated_at"])

        sell_job = Job.objects.create(
            account=self.seller_account,
            game_account=self.seller_ga,
            node=self.seller_node,
            action_code=9,
            inputs_json="{}",
            status="finished",
        )
        intervention = ConstructionMarketIntervention.objects.create(
            account=self.seller_account,
            game_account=self.seller_ga,
            node=self.seller_node,
            sell_job=sell_job,
            status="pending",
            wait_reason="market_gold_eta",
            eta_seconds=3600,
            city_id=303,
            city_name="Seller Port",
            building_name="Mercado",
            needed_resource_idx=2,
            needed_amount=1000,
            available_gold=0,
            min_gold=0,
            sale_city_id=303,
            sale_city_name="Seller Port",
            sale_branchoffice_pos=4,
            sale_resource_idx=0,
            sale_amount=500,
            sale_price_min=10,
            sale_price_max=12,
            sale_price_target=11,
            estimated_sale_gold=5500,
        )

        ok, _msg = approve_construction_market_intervention(intervention, decided_by="tester")
        self.assertTrue(ok)

        intervention.refresh_from_db()
        order_id = intervention.decision_note.split("internal_order_id=", 1)[1].strip()
        order = InternalMarketOrder.objects.get(pk=order_id)
        first_buy_job_id = order.buy_job_id

        ok, msg = approve_construction_market_intervention(intervention, decided_by="tester")
        self.assertTrue(ok)
        self.assertIn(str(order.pk), msg)

        intervention.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(InternalMarketOrder.objects.count(), 1)
        self.assertEqual(order.buy_job_id, first_buy_job_id)
        self.assertEqual(intervention.sell_job_id, order.sell_job_id)
