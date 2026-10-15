"""N-127 and the game panel filters: "Atualizar todas" asks Verificar Status for every visible account, one after
another and never twice; the node and account filters show up and every option says where it belongs."""
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from apps.orgs.models import Organization


class GamePanelTests(TestCase):
    def setUp(self):
        cache.clear()
        user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(user)
        self.node_a = Node.objects.create(name="casa", transport="https")
        self.node_b = Node.objects.create(name="servidor", transport="https")
        self.acc_a = Account.objects.create(node=self.node_a, label="Conta A", email="a@example.com", password_enc="x")
        self.acc_b = Account.objects.create(node=self.node_b, label="Conta B", email="b@example.com", password_enc="x")
        self.ga1 = self._ga(self.acc_a, 1, "Atenas", ["Capital", "Porto"])
        self.ga2 = self._ga(self.acc_a, 2, "Esparta", ["Capital"])
        self.ga3 = self._ga(self.acc_b, 3, "Tebas", ["Colina"])
        self.addCleanup(cache.clear)

    def _ga(self, account, number, name, cities):
        ga = GameAccount.objects.create(account=account, lobby_account_id=number, server_id=f"s{number}-br",
                                        server_language="br", server_number=number, name=name)
        AccountSnapshot.objects.create(account=account, game_account=ga, base_snapshot={"gold": 10},
                                       cities=[{"id": str(number * 10 + i), "name": city, "wood": 100, "tradegood": 1}
                                               for i, city in enumerate(cities)], military={})
        return ga

    def _check_all(self, ids=""):
        response = self.client.post("/game/check-status-all/", {"game_account_ids": ids})
        return json.loads(response["HX-Trigger"])["toast"]["message"]

    def test_every_visible_account_gets_one_check_spaced_out(self):
        message = self._check_all(f"{self.ga1.pk},{self.ga3.pk}")
        jobs = list(Job.objects.filter(action_code=100).order_by("created_at"))
        self.assertEqual({job.game_account_id for job in jobs}, {self.ga1.pk, self.ga3.pk})
        self.assertEqual([job.status for job in jobs], ["queued", "scheduled"])
        self.assertIn("2 contas", message)

    def test_an_account_already_waiting_is_not_asked_twice(self):
        self._check_all(str(self.ga1.pk))
        message = self._check_all("")                      # no filter: every account
        self.assertEqual(Job.objects.filter(action_code=100, game_account=self.ga1).count(), 1)
        self.assertEqual(Job.objects.filter(action_code=100).count(), 3)
        self.assertIn("1 já estava na fila", message)

    def test_another_organizations_account_is_never_touched(self):
        other = Organization.objects.create(name="Outra", slug="outra")
        node = Node._base_manager.create(name="deles", transport="https", organization=other)
        account = Account._base_manager.create(node=node, label="Deles", email="o@example.com", password_enc="x",
                                               organization=other)
        theirs = GameAccount._base_manager.create(account=account, lobby_account_id=9, server_id="s9-br",
                                                  server_language="br", server_number=9, name="Deles")
        self._check_all(str(theirs.pk))
        self.assertFalse(Job._base_manager.filter(game_account=theirs).exists())

    def test_the_filters_show_up_and_each_option_says_where_it_belongs(self):
        html = self.client.get("/game/").content.decode()
        self.assertIn('data-filter="node"', html)
        self.assertIn('data-filter="account"', html)
        self.assertIn(f'value="{self.ga3.pk}" data-game-account="{self.ga3.pk}"', html)
        capital = "|".join(sorted([str(self.ga1.pk), str(self.ga2.pk)]))
        self.assertIn(f'value="Capital" data-game-accounts="{capital}"', html)
        self.assertIn('data-city-name="Porto"', html)
        self.assertIn("Atualizar todas", html)
