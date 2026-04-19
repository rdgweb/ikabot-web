from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node

from .models import Job
from .views.create import JobSubmitView


class MarketJobCreationTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="node-1")
        self.account = Account.objects.create(
            node=self.node,
            label="Conta",
            email="conta@example.com",
            password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=1,
            server_id="s1-br",
            server_language="br",
            server_number=1,
            name="Atenas",
        )
        self.city = {
            "id": 101,
            "name": "Capital",
            "buildings": [
                {"building": "branchOffice", "position": 8},
            ],
        }

    def test_sell_market_job_infers_branch_office_position(self):
        JobSubmitView._create_single_job(
            self.ga,
            9,
            {
                "city_id": "101",
                "resource_idx": "0",
                "amount": 5000,
                "unit_price": 12,
                "_city_choices": {"101": "Capital"},
                "_city_objects": {"101": self.city},
            },
        )

        job = Job.objects.latest("created_at")
        self.assertIn('"branchoffice_pos": 8', job.inputs_json)
        self.assertIn('"city_name": "Capital"', job.inputs_json)

    def test_buy_market_job_infers_buyer_branch_office_position(self):
        JobSubmitView._create_single_job(
            self.ga,
            8,
            {
                "buyer_city_id": "101",
                "seller_city_id": "99999",
                "resource_idx": "1",
                "amount": 2500,
                "_city_choices": {"101": "Capital"},
                "_city_objects": {"101": self.city},
            },
        )

        job = Job.objects.latest("created_at")
        self.assertIn('"buyer_branchoffice_pos": 8', job.inputs_json)
        self.assertIn('"seller_branchoffice_pos": 0', job.inputs_json)
        self.assertIn('"buyer_city_name": "Capital"', job.inputs_json)
