"""N-106 stage 4: one platform bot, one chat per organization — notices and commands never cross organizations."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.captcha.models import CaptchaChallenge
from apps.orgs import scope
from apps.orgs.models import Organization
from apps.orgs.services import default_organization
from apps.telegram.api.webhook import TelegramWebhookView
from apps.telegram.models import TelegramBotConfig
from apps.telegram.services import config as tg
from apps.telegram.services.linking import generate_global_link_code
from apps.telegram.services.notifications import notify

WEBHOOK = "apps.telegram.api.webhook"


def _world(organization, tag):
    token = scope.activate(organization)
    try:
        node = Node.objects.create(name=f"agente-{tag}")
        account = Account.objects.create(node=node, label=f"Conta {tag}", email=f"{tag}@example.com", password_enc="x")
        return GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                          server_number=78, name=f"Jogador {tag}")
    finally:
        scope.deactivate(token)


class TelegramPerOrganizationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org_a = default_organization()
        self.org_b = Organization.objects.create(name="Outro cliente", slug="outro")
        bot = tg.platform_bot()
        bot.bot_token, bot.enabled, bot.organization = "123:abc", True, self.org_a
        bot.chat_id, bot.link_status = "111", "linked"
        bot.save()
        config_b = tg.organization_config(self.org_b)
        config_b.chat_id, config_b.link_status = "222", "linked"
        config_b.save()
        self.world_a = _world(self.org_a, "A")
        self.world_b = _world(self.org_b, "B")

    def _update(self, chat_id, text):
        return {"message": {"text": text, "chat": {"id": int(chat_id)}, "from": {"id": int(chat_id), "username": "x"}}}

    def test_notices_go_to_the_chat_of_the_accounts_organization(self):
        self.assertEqual(tg.destination_chat(self.world_a), "111")
        self.assertEqual(tg.destination_chat(self.world_b), "222")
        with patch("apps.telegram.services.notifications._send", return_value={"ok": True}) as send:
            notify("attack_alert", game_account=self.world_b, title="Ataque", body="chegando")
        self.assertEqual(send.call_args.args[0], "222")

    def test_a_chat_of_no_organization_runs_nothing(self):
        challenge = CaptchaChallenge.objects.create(game_account=self.world_a, captcha_type="unknown")
        with patch(f"{WEBHOOK}.send_message") as send:
            TelegramWebhookView().handle_update(self._update("999", f"/captcha {challenge.pk} ABCD"))
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, "pending")
        send.assert_not_called()                                       # not even an answer

    def test_a_chat_reaches_only_its_organization(self):
        challenge_a = CaptchaChallenge.objects.create(game_account=self.world_a, captcha_type="unknown")
        with patch(f"{WEBHOOK}.send_message"):
            TelegramWebhookView().handle_update(self._update("222", f"/captcha {challenge_a.pk} ABCD"))
        challenge_a.refresh_from_db()
        self.assertEqual(challenge_a.status, "pending")               # org B's chat cannot answer org A's captcha
        self.assertIsNone(scope.current())                            # the chat's scope did not outlive the update
        challenge_b = CaptchaChallenge.objects.create(game_account=self.world_b, captcha_type="unknown")
        with patch(f"{WEBHOOK}.send_message"):
            TelegramWebhookView().handle_update(self._update("222", f"/captcha {challenge_b.pk} ABCD"))
        challenge_b.refresh_from_db()
        self.assertNotEqual(challenge_b.status, "pending")

    def test_a_new_chat_links_with_the_long_code_and_only_to_one_organization(self):
        TelegramBotConfig._base_manager.filter(organization=self.org_b).update(chat_id="", link_status="unlinked")
        code = generate_global_link_code(self.org_b)
        self.assertEqual(len(code), 10)
        with patch(f"{WEBHOOK}.send_message"):
            TelegramWebhookView().handle_update(self._update("333", f"/start {code}"))
        self.assertEqual(tg.organization_for_chat("333"), self.org_b)
        # org A's chat cannot be moved to org B by a code
        code = generate_global_link_code(self.org_b)
        with patch(f"{WEBHOOK}.send_message"):
            TelegramWebhookView().handle_update(self._update("111", f"/start {code}"))
        self.assertEqual(tg.organization_for_chat("111"), self.org_a)

    def test_guessing_codes_gets_blocked(self):
        TelegramBotConfig._base_manager.filter(organization=self.org_b).update(chat_id="", link_status="unlinked")
        code = generate_global_link_code(self.org_b)
        with patch(f"{WEBHOOK}.send_message"):
            for attempt in range(tg.LINK_FAILURES_ALLOWED):
                TelegramWebhookView().handle_update(self._update("444", f"/start WRONG{attempt:05d}"))
            TelegramWebhookView().handle_update(self._update("444", f"/start {code}"))   # right code, too late
        self.assertIsNone(tg.organization_for_chat("444"))
