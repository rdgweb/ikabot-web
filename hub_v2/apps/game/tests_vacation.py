"""N-68: marcador de ferias no Painel do Jogo.

O agent avisa pelo canal de estado de login (mode=vacation) quando o jogo
bloqueia o login por ferias; o "login ok" (mode=clear) encerra o estado.
"""

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.game.services.vacation import read_vacation_state, set_vacation_state


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class VacationMarkerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="vacation-tester", email="vacation@example.com", password="secret123",
        )
        self.account = Account.objects.create(
            node=Node.objects.create(name="agent-vacation"),
            label="Lobby Vacation", email="vacation-lobby@example.com", password_enc="enc",
        )
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=888, server_id="s9-br",
            server_language="br", server_number=9, name="VacationPlayer",
        )
        self.snapshot = AccountSnapshot.objects.create(
            account=self.account, game_account=self.ga, military={},
            base_snapshot={"gold": 1000, "attack_alert_state": {"hostile_count": 0}},
            cities=[{"id": "501", "name": "Alfa", "tradegood": 1, "buildings": []}],
        )

    def _report(self, mode, ga=None):
        return self.client.post(
            f"/api/agent/game-accounts/{(ga or self.ga).pk}/login-cooldown/",
            data=json.dumps({"mode": mode}),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

    def _state(self):
        self.snapshot.refresh_from_db()
        return self.snapshot.base_snapshot.get("vacation_state")

    def test_blocked_login_marks_the_account_and_a_good_login_clears_it(self):
        response = self._report("vacation")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["changed"])
        state = self._state()
        self.assertTrue(state["active"])
        self.assertTrue(state["since"])
        self.assertEqual(self.snapshot.base_snapshot["gold"], 1000)   # nothing else is touched

        # still blocked later: same vacation, same start
        self.assertFalse(self._report("vacation").json()["changed"])
        self.assertEqual(self._state()["since"], state["since"])

        self.assertEqual(self._report("clear").status_code, 200)
        ended = self._state()
        self.assertFalse(ended["active"])
        self.assertEqual(ended["since"], state["since"])
        self.assertTrue(ended["ended_at"])

    def test_vacation_is_not_a_login_backoff(self):
        self._report("vacation")
        self.ga.refresh_from_db()
        self.assertIsNone(self.ga.login_blocked_until)
        self.assertEqual(int(self.ga.login_block_backoff_hours or 0), 0)

    def test_ordinary_good_login_writes_nothing(self):
        self._report("clear")
        self.assertIsNone(self._state())

    def test_account_without_snapshot_is_accepted(self):
        other = GameAccount.objects.create(
            account=self.account, lobby_account_id=889, server_id="s9-br",
            server_language="br", server_number=9, name="NoSnapshot",
        )
        response = self._report("vacation", ga=other)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["changed"])

    def test_read_state_tolerates_missing_or_odd_data(self):
        for base in (None, {}, {"vacation_state": "x"}, {"vacation_state": {"active": 0}}):
            self.assertFalse(read_vacation_state(base)["active"])
        self.assertTrue(set_vacation_state(self.ga, True))
        self.snapshot.refresh_from_db()
        state = read_vacation_state(self.snapshot.base_snapshot)
        self.assertTrue(state["active"])
        self.assertIsNotNone(state["since"])

    def test_panel_shows_the_marker_only_while_on_vacation(self):
        self.client.force_login(self.user)
        self.assertNotIn("DE FERIAS", self.client.get(reverse("game:dashboard")).content.decode())

        self._report("vacation")
        page = self.client.get(reverse("game:dashboard")).content.decode()
        self.assertIn("DE FERIAS", page)
        self.assertIn("bi-umbrella-fill", page)

        self._report("clear")
        self.assertNotIn("DE FERIAS", self.client.get(reverse("game:dashboard")).content.decode())
