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
        response = self.client.get(reverse("settings_app:health"), HTTP_HX_REQUEST="true", **headers)
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

    def test_the_health_lives_in_configuracoes(self):
        page = self.client.get(reverse("settings_app:list")).content.decode()
        self.assertIn('id="saude"', page)
        self.assertIn(f'hx-get="{reverse("settings_app:health")}"', page)
        self.assertRedirects(self.client.get(reverse("settings_app:health")), "/settings/#saude", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/settings/primeiros-passos/"), "/settings/#saude", fetch_redirect_response=False)
        self.assertNotIn("primeiros-passos", self.client.get("/").content.decode())     # out of the menu


class TourTests(TestCase):
    """The welcome tour starts by itself once for each user and is switched in Configurações."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("novato", password="x", is_staff=True)
        self.client.force_login(self.user)

    def _autostarts(self):
        html = self.client.get("/").content.decode()
        self.assertIn("js/tour.js", html)
        return "data-autostart" in html

    def test_it_starts_once_for_each_user(self):
        self.assertTrue(self._autostarts())
        self.assertEqual(self.client.post(reverse("settings_app:tour-done")).status_code, 204)
        self.assertFalse(self._autostarts())
        other = get_user_model().objects.create_user("outro", password="x")
        self.client.force_login(other)
        self.assertTrue(self._autostarts())

    def test_switching_it_on_shows_it_again_to_everybody(self):
        from apps.settings_app.models import AppSetting

        self.client.post(reverse("settings_app:tour-done"))
        self.client.post(reverse("settings_app:tour-toggle"), {"enabled": "0"})
        self.assertFalse(self._autostarts())
        self.assertIn("Desligado", self.client.get(reverse("settings_app:list")).content.decode())
        self.client.post(reverse("settings_app:tour-toggle"), {"enabled": "1"})
        self.assertTrue(self._autostarts())
        self.assertFalse(AppSetting.objects.filter(key__startswith="tour_done:").exists())

    def test_settings_page_offers_it_and_hides_its_keys(self):
        self.client.post(reverse("settings_app:tour-done"))
        response = self.client.get(reverse("settings_app:list"))
        html = response.content.decode()
        self.assertIn("window.ikTour.start()", html)
        self.assertFalse([item.key for item in response.context["generic_settings"] if item.key.startswith("tour_")])

    def test_the_state_follows_the_installation(self):
        url = reverse("settings_app:tour-state")
        self.assertEqual(self.client.get(url).json(), {"nodes": 0, "online": 0, "accounts": 0, "worlds": 0, "first_account": ""})
        node = Node.objects.create(name="agente-1", agent_last_seen_at=timezone.now())
        Node.objects.create(name="parado")
        account = Account.objects.create(node=node, label="Lobby", email="a@example.com", password_enc="x")
        GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br", server_number=78, name="Alpha")
        self.assertEqual(self.client.get(url).json(), {"nodes": 2, "online": 1, "accounts": 1, "worlds": 1, "first_account": str(account.pk)})
        self.assertIn("no-cache", self.client.get(url)["Cache-Control"])                # polled while the tour waits

    def test_the_account_form_says_why_there_is_no_node(self):
        html = self.client.get(reverse("accounts:account-create")).content.decode()
        self.assertIn("Nenhum nó ainda", html)
        Node.objects.create(name="agente-1")
        self.assertNotIn("Nenhum nó ainda", self.client.get(reverse("accounts:account-create")).content.decode())

    def test_the_tour_points_at_the_real_parts(self):
        from pathlib import Path

        from django.conf import settings as django_settings

        script = (Path(django_settings.BASE_DIR) / "static" / "js" / "tour.js").read_text(encoding="utf-8")
        node = Node.objects.create(name="agente-1")
        account = Account.objects.create(node=node, label="Lobby", email="a@example.com", password_enc="x")
        pages = {
            "/": ['data-tour="novo-job"', 'href="/jobs/"', 'href="/game/"', 'href="/game/actions/"'],
            reverse("accounts:node-list"): ['data-tour="nodes"'],
            reverse("accounts:account-create"): ['id="id_node"', 'id="id_label"', 'id="id_email"', 'id="id_gf_token"', 'data-tour="form-submit"'],
            reverse("accounts:account-list"): ['data-tour="account-row"'],
            reverse("accounts:account-detail", args=[account.pk]): ['data-tour="discover"', 'data-tour="worlds"'],
        }
        for url, markers in pages.items():
            html = self.client.get(url).content.decode()
            for marker in markers:
                self.assertIn(marker, html, f"{marker} em {url}")
        for selector in ('[data-tour="nodes"]', "#id_node", "#id_label", "#id_email", "#id_gf_token", '[data-tour="form-submit"]', '[data-tour="account-row"]',
                         '[data-tour="discover"]', '[data-tour="worlds"]', '[data-tour="novo-job"]', 'a.sidebar-item[href="/jobs/"]'):
            self.assertIn(selector, script)
        self.assertIn('data-state-url="/settings/tour/state/"', self.client.get("/").content.decode())
        self.assertNotIn("data-tour-script", self.client.get("/", HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true").content.decode())
