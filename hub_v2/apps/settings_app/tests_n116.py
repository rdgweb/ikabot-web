"""N-116: the first-steps page tests each piece of the installation for real."""

from datetime import timedelta
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, DockerHost, GameAccount, Node
from apps.settings_app import first_steps


class _Resp:
    def __init__(self, status=200):
        self.status_code = status


def _offline(*args, **kwargs):
    raise requests.exceptions.ConnectionError("nobody there")


class FirstStepsTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("rodrigo", password="x", is_staff=True))

    def _page(self, **headers):
        response = self.client.get(reverse("settings_app:first-steps"), **headers)
        self.assertEqual(response.status_code, 200)
        return {item["key"]: item for item in response.context["checks"]}, response

    @override_settings(HUB_PUBLIC_URL="", MANUAL_GATEWAY_DOMAIN="")
    def test_an_empty_installation_says_what_is_missing(self):
        with patch("apps.settings_app.services.requests.get", side_effect=_offline):
            checks, response = self._page()
        self.assertEqual({key: item["state"] for key, item in checks.items()}, {
            "ikabotapi": "missing", "agents": "missing", "accounts": "missing", "address": "warn",
            "telegram": "optional", "manual": "optional", "supervisor": "optional",
        })
        self.assertIn("Contas do Jogo", response.content.decode())                     # where to fix it
        self.assertEqual(response.context["counts"], {"ok": 0, "warn": 1, "missing": 3, "optional": 3})

    @override_settings(HUB_PUBLIC_URL="http://testserver", MANUAL_GATEWAY_DOMAIN="jogo.example.com")
    def test_a_working_installation_is_all_ready(self):
        node = Node.objects.create(name="agente-1", agent_last_seen_at=timezone.now())
        account = Account.objects.create(node=node, label="Lobby", email="a@example.com", password_enc="x")
        GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br", server_number=78, name="Alpha")
        DockerHost.objects.create(name="servidor", last_seen_at=timezone.now())
        with patch("apps.settings_app.services.requests.get", return_value=_Resp(200)), \
                patch("apps.settings_app.first_steps.requests.get", return_value=_Resp(200)) as gateway:
            checks, _response = self._page()
        self.assertEqual({key: item["state"] for key, item in checks.items() if key != "telegram"},
                         {"ikabotapi": "ok", "agents": "ok", "accounts": "ok", "address": "ok", "manual": "ok", "supervisor": "ok"})
        self.assertIn("agente-1", checks["agents"]["detail"])
        self.assertEqual(gateway.call_args.args[0], first_steps.GATEWAY_HEALTH_URL)

    @override_settings(HUB_PUBLIC_URL="https://ikabot.example.com")
    def test_opened_by_another_address_than_the_public_one(self):
        Node.objects.create(name="velho", agent_last_seen_at=timezone.now() - timedelta(minutes=10))
        with patch("apps.settings_app.services.requests.get", side_effect=_offline):
            checks, _response = self._page()
        self.assertEqual(checks["address"]["state"], "warn")
        self.assertIn("testserver", checks["address"]["detail"])
        self.assertEqual(checks["agents"]["state"], "missing")
        self.assertIn("3 minutos", checks["agents"]["detail"])

    def test_telegram_tells_webhook_from_polling(self):
        from apps.telegram.models import TelegramBotConfig

        TelegramBotConfig.objects.create(pk=1, bot_token="123:abc", enabled=True, webhook_secret="s", link_status="linked", chat_id="1")
        with override_settings(HUB_PUBLIC_URL="https://ikabot.example.com"):
            self.assertIn("webhook", first_steps.check_telegram()["detail"])
        with override_settings(HUB_PUBLIC_URL="http://192.168.0.10:8000"):
            item = first_steps.check_telegram()
        self.assertEqual(item["state"], "ok")
        self.assertIn("perfil telegram", item["detail"])

    def test_a_broken_check_does_not_take_the_page_down(self):
        with patch("apps.settings_app.first_steps.check_accounts", side_effect=RuntimeError("boom")), \
                patch("apps.settings_app.services.requests.get", side_effect=_offline):
            checks, _response = self._page()
        self.assertIn("boom", checks["error"]["detail"])

    def test_the_menu_has_the_page(self):
        with patch("apps.settings_app.services.requests.get", side_effect=_offline):
            _checks, response = self._page()
        self.assertIn('href="/settings/primeiros-passos/"', response.content.decode())
