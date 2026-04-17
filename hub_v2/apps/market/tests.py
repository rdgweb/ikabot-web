from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot

from .services import create_internal_order


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
