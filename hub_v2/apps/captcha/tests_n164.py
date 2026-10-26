"""N-164: the captcha card counts what really happened — answers the game refused, and captchas that did not block."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Account, GameAccount, Node
from apps.captcha.models import CaptchaChallenge


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class CaptchaCardTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="HAVIT")

    def post(self, url, data):
        return self.client.post(url, data, content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
                                HTTP_X_AGENT_NODE_ID=str(self.node.pk))

    def test_a_refused_answer_becomes_failed(self):
        c = CaptchaChallenge.objects.create(captcha_type="piracy", status="solved", solution="ABC", game_account=self.ga)
        self.assertEqual(self.post(f"/api/agent/captcha/challenge/{c.pk}/result/", {"accepted": False}).status_code, 200)
        c.refresh_from_db()
        self.assertEqual((c.status, c.error), ("failed", "Resposta recusada pelo jogo"))

    def test_a_captcha_that_did_not_block_is_counted(self):
        self.assertEqual(self.post("/api/agent/captcha/seen/", {"type": "pirate", "game_account_id": str(self.ga.pk)}).status_code, 200)
        c = CaptchaChallenge.objects.get()
        self.assertEqual((c.status, c.captcha_type, c.game_account_id), ("not_needed", "piracy", self.ga.pk))

    def test_the_card_shows_them(self):
        from apps.orgs.models import Membership
        from apps.orgs.services import default_organization

        CaptchaChallenge.objects.create(captcha_type="piracy", status="solved", game_account=self.ga)
        CaptchaChallenge.objects.create(captcha_type="piracy", status="failed", game_account=self.ga)
        for _ in range(3):
            CaptchaChallenge.objects.create(captcha_type="piracy", status="not_needed", game_account=self.ga)
        user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True, is_superuser=True)
        Membership.objects.get_or_create(organization=default_organization(), user=user, defaults={"role": Membership.OWNER})
        self.client.force_login(user)
        response = self.client.get("/captcha/")
        self.assertEqual(response.status_code, 200)
        stats = response.context["stats"]
        self.assertEqual((stats["total_24h"], stats["solved_24h"], stats["failed_24h"], stats["not_needed_24h"]), (5, 1, 1, 3))
        self.assertIn("Apareceram sem bloquear", response.content.decode())
