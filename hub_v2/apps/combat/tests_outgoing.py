"""Combates shows what is on its way: the raids the hub sent, one group per target."""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job

from .services.outgoing import outgoing


class OutgoingTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="agente", transport="https")
        self.account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Lord")
        AccountSnapshot.objects.create(account=self.account, game_account=self.ga, cities=[{"id": 66483, "name": "CC1"}])
        for phase, minutes in (("check_battle", 60), ("wait_movements", 30)):
            Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=1008, status="scheduled",
                               scheduled_for=timezone.now() + timedelta(minutes=minutes), timeout_sec=60,
                               inputs_json=json.dumps({"target_city_id": "52292", "_phase": phase, "_source_city_id": "66483",
                                                       "_transporters_sent": 195, "units": {"303": 30, "305": 6}}))

    def test_raids_grouped_by_target_with_their_state(self):
        found = outgoing()
        self.assertEqual(found["count"], 2)
        rows = found["raids"][0]["rows"]
        self.assertEqual([r["label"] for r in rows], ["voltando com saque", "indo"])
        self.assertEqual((rows[1]["source"], rows[1]["ships"], len(rows[1]["units"])), ("CC1", 195, 2))

    def test_the_page_shows_them(self):
        user = get_user_model().objects.create_user(username="rodrigo", password="x", is_staff=True, is_superuser=True)
        self.client.force_login(user)
        self.assertContains(self.client.get("/combat/"), "A caminho")
