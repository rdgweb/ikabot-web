from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job

from .services import create_buy_job, create_internal_order


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
                    "wood": 1000,
                    "buildings": [{"building": "branchOffice", "position": 6}],
                },
                {
                    "id": 202,
                    "name": "Marble City",
                    "wood": 500,
                    "buildings": [{"building": "branchOffice", "position": 8}],
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
                    "wood": 50000,
                    "wine": 0,
                    "marble": 0,
                    "glas": 0,
                    "sulfur": 0,
                    "buildings": [{"building": "branchOffice", "position": 4}],
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
