import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import Job
from apps.settings_app.utils import get_setting, set_setting

from . import services

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _ok(status=204):
    response = MagicMock()
    response.status_code = status
    return response


@override_settings(CACHES=LOCMEM, TELEMETRY_URL="https://telemetry.test", TELEMETRY_DISABLED=False)
class TelemetryServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        node = Node.objects.create(name="secret-node-name", agent_version="0.1.57", active=True, external_ip="203.0.113.9")
        account = Account.objects.create(node=node, label="private-label", email="private@example.com")
        for index, (number, name) in enumerate(((61, "PlayerOne"), (61, "PlayerTwo"), (44, "PlayerThree"))):
            GameAccount.objects.create(
                account=account, lobby_account_id=1000 + index, server_id=f"s{number}-br",
                server_language="br", server_number=number, name=name,
            )
        Job.objects.create(account=account, node=node, action_code=1, status="finished")

    def test_default_state_is_undecided_and_sends_nothing(self):
        self.assertEqual(services.get_status(), services.STATUS_UNSET)
        self.assertTrue(services.needs_decision())
        with patch("apps.telemetry.services.requests.post") as post:
            self.assertFalse(services.is_due())
            self.assertFalse(services.tick())
            ok, _ = services.send_now()
        self.assertFalse(ok)
        post.assert_not_called()

    def test_payload_shape_and_no_personal_data(self):
        payload = services.build_payload("11111111-1111-1111-1111-111111111111")

        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["counts"], {"lobby_accounts": 1, "game_accounts": 3, "nodes": 1})
        self.assertEqual(payload["agents"], [{"version": "0.1.57", "nodes": 1}])
        self.assertEqual(
            payload["worlds"],
            [{"server": "br", "world": 44, "accounts": 1}, {"server": "br", "world": 61, "accounts": 2}],
        )
        self.assertEqual(sum(item["jobs"] for item in payload["usage_30d"]), 1)
        self.assertEqual(set(payload), {
            "schema", "install_id", "hub_version", "arch", "timezone", "counts", "agents", "worlds", "usage_30d",
        })

        blob = json.dumps(payload).lower()
        for secret in ("secret-node-name", "private-label", "private@example.com", "playerone", "203.0.113.9"):
            self.assertNotIn(secret, blob)

    def test_enable_sends_daily_ping_once(self):
        services.enable()
        self.assertEqual(services.get_status(), services.STATUS_ENABLED)
        self.assertTrue(services.is_due())

        with patch("apps.telemetry.services.requests.post", return_value=_ok()) as post:
            ok, _ = services.send_now()

        self.assertTrue(ok)
        url = post.call_args.args[0]
        self.assertEqual(url, "https://telemetry.test/v1/ping")
        self.assertEqual(post.call_args.kwargs["json"]["install_id"], get_setting(services.KEY_INSTALL_ID))
        self.assertFalse(services.is_due())

    def test_failed_send_backs_off_and_records_error(self):
        services.enable()
        with patch("apps.telemetry.services.requests.post", side_effect=requests.ConnectionError("boom")):
            ok, message = services.send_now()

        self.assertFalse(ok)
        self.assertIn("ConnectionError", message)
        self.assertEqual(get_setting(services.KEY_LAST_ERROR), message)
        self.assertFalse(services.is_due())  # retry only after RETRY_INTERVAL

        set_setting(services.KEY_LAST_ATTEMPT, (timezone.now() - timedelta(hours=2)).isoformat())
        self.assertTrue(services.is_due())

    def test_kill_switch_overrides_opt_in(self):
        services.enable()
        with override_settings(TELEMETRY_DISABLED=True):
            self.assertEqual(services.get_status(), services.STATUS_DISABLED)
            self.assertFalse(services.is_due())
            with patch("apps.telemetry.services.requests.post") as post:
                services.send_now()
            post.assert_not_called()

    def test_disable_erases_remote_data_and_forgets_install_id(self):
        services.enable()
        install_id = get_setting(services.KEY_INSTALL_ID)

        with patch("apps.telemetry.services.requests.delete", return_value=_ok()) as delete:
            erased, _ = services.disable_and_erase()

        self.assertTrue(erased)
        self.assertEqual(delete.call_args.args[0], f"https://telemetry.test/v1/installs/{install_id}")
        self.assertEqual(services.get_status(), services.STATUS_DISABLED)
        self.assertEqual(get_setting(services.KEY_INSTALL_ID), "")

    def test_disable_keeps_install_id_when_erase_fails(self):
        services.enable()
        install_id = get_setting(services.KEY_INSTALL_ID)
        with patch("apps.telemetry.services.requests.delete", side_effect=requests.Timeout()):
            erased, _ = services.disable_and_erase()

        self.assertFalse(erased)
        self.assertEqual(services.get_status(), services.STATUS_DISABLED)
        self.assertEqual(get_setting(services.KEY_INSTALL_ID), install_id)

    def test_tick_is_throttled_and_only_spawns_when_due(self):
        services.enable()
        with patch("apps.telemetry.services.threading.Thread") as thread:
            self.assertTrue(services.tick())
            self.assertFalse(services.tick())  # throttled for TICK_SECONDS
        thread.assert_called_once()

        cache.clear()
        set_setting(services.KEY_LAST_SENT, timezone.now().isoformat())
        with patch("apps.telemetry.services.threading.Thread") as thread:
            self.assertFalse(services.tick())  # sent recently -> not due
        thread.assert_not_called()


@override_settings(CACHES=LOCMEM, TELEMETRY_URL="https://telemetry.test", TELEMETRY_DISABLED=False)
class TelemetryViewsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username="admin", password="x", is_staff=True)
        self.regular = get_user_model().objects.create_user(username="viewer", password="x")

    def test_banner_only_for_admin_while_undecided(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("settings_app:list")), "estatísticas de uso")

        services.decline()
        self.assertNotContains(self.client.get(reverse("settings_app:list")), "Ajude a melhorar o ikabot")

    def test_consent_accept_enables_and_sends(self):
        self.client.force_login(self.admin)
        with patch("apps.telemetry.services.requests.post", return_value=_ok()) as post:
            response = self.client.post(reverse("telemetry:consent"), {"choice": "accept"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(services.get_status(), services.STATUS_ENABLED)
        post.assert_called_once()

    def test_decline_never_sends(self):
        self.client.force_login(self.admin)
        with patch("apps.telemetry.services.requests.post") as post:
            self.client.post(reverse("telemetry:consent"), {"choice": "decline"})

        self.assertEqual(services.get_status(), services.STATUS_DISABLED)
        post.assert_not_called()

    def test_non_admin_cannot_change_telemetry(self):
        self.client.force_login(self.regular)
        response = self.client.post(reverse("telemetry:consent"), {"choice": "accept"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(services.get_status(), services.STATUS_UNSET)

    def test_preview_shows_exact_payload_without_creating_an_install_id(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("telemetry:preview"))

        self.assertContains(response, "&quot;schema&quot;: 1")
        self.assertEqual(get_setting(services.KEY_INSTALL_ID), "")

    def test_next_redirect_rejects_foreign_hosts(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("telemetry:consent"), {"choice": "decline", "next": "https://evil.example/"},
        )

        self.assertRedirects(response, reverse("settings_app:list"), fetch_redirect_response=False)
