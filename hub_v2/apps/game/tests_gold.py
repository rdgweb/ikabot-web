"""N-76: one rule for the net gold income, with the two bonuses of the game's gold tooltip."""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot, AccountSnapshotHistory
from apps.game.services.dashboard_cache import bump_dashboard_cache_version
from apps.game.services.gold import gold_income, net_gold_income
from apps.game.views.dashboard import DashboardView
from apps.generals_bank.services import _project_net_gold_after_upkeep

# what the game showed for a real account on 07/10/2026 (nothing identifying):
#   Rendimento 5.037 | Recibos de Ouro Aumentados 0 | Deuses (Pluto) 0 | Cientista -1.704 | Manutencao -209 | Total 3.124
REAL = {"gold": 2467246, "income": 5037, "upkeep": -209, "scientists_upkeep": -1704, "god_gold_result": 0, "bad_tax_accountant": 0}
WITH_BONUS = {**REAL, "god_gold_result": 1500, "bad_tax_accountant": 1007}


class GoldIncomeRuleTests(TestCase):
    def test_real_account_matches_the_total_the_game_shows(self):
        self.assertEqual(net_gold_income(REAL), 3124)
        self.assertEqual(gold_income(REAL), {
            "income": 5037, "accountant_bonus": 0, "god_bonus": 0, "receipts": 5037,
            "upkeep": -209, "scientists_upkeep": -1704, "net": 3124,
        })

    def test_bonuses_are_added_once_and_keep_their_sign(self):
        parts = gold_income(WITH_BONUS)
        self.assertEqual((parts["receipts"], parts["net"]), (5037 + 1500 + 1007, 3124 + 2507))
        self.assertEqual(net_gold_income({"income": 1000, "upkeep": -200, "bad_tax_accountant": -300}), 500)
        self.assertEqual(net_gold_income({"income": 100, "upkeep": -500, "god_gold_result": 50}), -350)

    def test_old_snapshots_and_odd_values(self):
        self.assertEqual(net_gold_income({"gold": 1, "income": 16021, "upkeep": -20353, "scientists_upkeep": -1929}), -6261)
        self.assertEqual(net_gold_income({}), 0)
        self.assertEqual(net_gold_income(None), 0)
        self.assertEqual(net_gold_income({"income": "5037.09", "upkeep": None, "god_gold_result": "abc", "bad_tax_accountant": 12.9}), 5049)


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class GoldIncomeConsumersTests(TestCase):
    def setUp(self):
        bump_dashboard_cache_version()      # the dashboard keeps its context for a while; each test reads its own
        self.user = get_user_model().objects.create_user(username="gold", email="gold@example.com", password="secret123")
        node = Node.objects.create(name="node-gold")
        self.account = Account.objects.create(node=node, label="Lobby Gold", email="gold-lobby@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=76, server_id="s7-br", server_language="br", server_number=7, name="Ouro",
        )
        self.snapshot = AccountSnapshot.objects.create(account=self.account, game_account=self.ga, base_snapshot=dict(WITH_BONUS), cities=[], military={})

    def _dashboard(self):
        request = RequestFactory().get("/game/")
        request.user = self.user
        response = DashboardView.as_view()(request)
        response.render()
        return response

    def test_dashboard_card_counts_the_bonuses_and_shows_them(self):
        response = self._dashboard()
        card = next(card for card in response.context_data["account_cards"] if card["game_account_id"] == str(self.ga.pk))

        self.assertEqual(card["income"], 5631)                       # 5037 + 1007 + 1500 - 1704 - 209
        self.assertEqual((card["city_income"], card["accountant_bonus"], card["god_bonus"]), (5037, 1007, 1500))
        self.assertEqual(card["gross_income"], 7544)                 # everything that comes in (the KPI charts use it)
        self.assertEqual(card["gross_income"] + card["upkeep"] + card["scientists_upkeep"], card["income"])
        html = response.content.decode()
        self.assertIn("Recibos de ouro aumentados", html)
        self.assertIn("Deuses (Pluto)", html)
        self.assertNotIn("{#", html)

    def test_rows_of_the_bonuses_only_appear_when_there_is_one(self):
        self.snapshot.base_snapshot = dict(REAL)
        self.snapshot.save(update_fields=["base_snapshot"])

        response = self._dashboard()
        card = next(card for card in response.context_data["account_cards"] if card["game_account_id"] == str(self.ga.pk))

        self.assertEqual((card["income"], card["gross_income"]), (3124, 5037))
        html = response.content.decode()
        self.assertNotIn("Recibos de ouro aumentados", html)
        self.assertNotIn("Deuses (Pluto)", html)

    def test_history_points_use_the_same_rule(self):
        now = timezone.now()
        AccountSnapshotHistory.objects.create(
            account=self.account, game_account=self.ga, base_snapshot=dict(WITH_BONUS), cities=[], military={}, captured_at=now,
        )
        AccountSnapshotHistory.objects.create(      # a row from before the bonuses were collected
            account=self.account, game_account=self.ga, cities=[], military={}, captured_at=now - timezone.timedelta(hours=1),
            base_snapshot={"gold": 100, "income": 5037, "upkeep": -209, "scientists_upkeep": -1704},
        )
        self.client.force_login(self.user)

        payload = self.client.get(reverse("game:dashboard-history")).json()

        points = payload["history"][str(self.ga.pk)]
        self.assertEqual(sorted(point["income"] for point in points), [3124, 5631])

    def test_generals_bank_budget_uses_the_same_rule(self):
        self.assertEqual(_project_net_gold_after_upkeep(self.ga, 0), 5631)
        self.assertEqual(_project_net_gold_after_upkeep(self.ga, 631), 5000)

    def test_agent_can_patch_the_new_fields_as_numbers(self):
        response = self.client.patch(
            "/api/agent/snapshots/patch-base/",
            {"game_account_id": str(self.ga.pk), "patch": {"god_gold_result": "2000.7", "bad_tax_accountant": 0}},
            content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.snapshot.refresh_from_db()
        self.assertEqual((self.snapshot.base_snapshot["god_gold_result"], self.snapshot.base_snapshot["bad_tax_accountant"]), (2000, 0))
        self.assertEqual(net_gold_income(self.snapshot.base_snapshot), 5037 + 2000 - 1704 - 209)
        refused = self.client.patch(
            "/api/agent/snapshots/patch-base/",
            {"game_account_id": str(self.ga.pk), "patch": {"god_gold_result": "muito"}},
            content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
        )
        self.assertEqual(refused.status_code, 400)
