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

    # --- default-on, but only after the notice was shown -------------------------------

    def test_on_by_default_but_silent_until_the_notice_was_shown(self):
        self.assertEqual(services.get_status(), services.STATUS_UNSET)
        self.assertTrue(services.needs_notice())
        self.assertFalse(services.is_active())
        with patch("apps.telemetry.services.requests.post") as post:
            self.assertFalse(services.is_due())
            self.assertFalse(services.tick())
            ok, _ = services.send_now()
        self.assertFalse(ok)
        post.assert_not_called()

        services.mark_notice_shown()

        self.assertTrue(services.is_active())
        self.assertTrue(services.is_due())

    def test_every_item_is_enabled_by_default(self):
        self.assertEqual(services.enabled_items(), services.ITEM_KEYS)
        payload = services.build_payload("11111111-1111-1111-1111-111111111111")

        self.assertEqual(payload["schema"], 1)
        self.assertTrue(payload["share_ip"])
        self.assertEqual(payload["counts"], {"lobby_accounts": 1, "game_accounts": 3, "nodes": 1})
        self.assertEqual(payload["agents"], [{"version": "0.1.57", "nodes": 1}])
        self.assertEqual(
            payload["worlds"],
            [{"server": "br", "world": 44, "accounts": 1}, {"server": "br", "world": 61, "accounts": 2}],
        )
        self.assertEqual(sum(item["jobs"] for item in payload["usage_30d"]), 1)
        self.assertEqual(set(payload), {
            "schema", "install_id", "hub_version", "share_ip", "arch", "agents", "timezone",
            "counts", "worlds", "usage_30d",
        })

    def test_payload_never_contains_credentials_or_game_content(self):
        blob = json.dumps(services.build_payload("11111111-1111-1111-1111-111111111111")).lower()
        for secret in ("secret-node-name", "private-label", "private@example.com", "playerone", "203.0.113.9"):
            self.assertNotIn(secret, blob)

    def test_switched_off_items_are_not_in_the_payload(self):
        services.save_items({"counts"})  # everything else off
        payload = services.build_payload("11111111-1111-1111-1111-111111111111")

        self.assertEqual(set(payload), {"schema", "install_id", "hub_version", "share_ip", "counts"})
        self.assertFalse(payload["share_ip"])

    def test_ip_item_only_controls_the_share_ip_flag(self):
        services.save_items(set(services.ITEM_KEYS) - {"ip"})
        payload = services.build_payload("11111111-1111-1111-1111-111111111111")

        self.assertFalse(payload["share_ip"])
        self.assertIn("counts", payload)
        self.assertIn("timezone", payload)

    # --- transport ------------------------------------------------------------------------

    def test_send_posts_the_payload_and_waits_a_day(self):
        services.enable()
        self.assertTrue(services.is_due())

        with patch("apps.telemetry.services.requests.post", return_value=_ok()) as post:
            ok, _ = services.send_now()

        self.assertTrue(ok)
        self.assertEqual(post.call_args.args[0], "https://telemetry.test/v1/ping")
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

    def test_kill_switch_overrides_everything(self):
        services.enable()
        with override_settings(TELEMETRY_DISABLED=True):
            self.assertEqual(services.get_status(), services.STATUS_DISABLED)
            self.assertFalse(services.is_active())
            with patch("apps.telemetry.services.requests.post") as post:
                services.send_now()
            post.assert_not_called()

    def test_decline_stops_everything_even_after_the_notice(self):
        services.mark_notice_shown()
        services.decline()

        self.assertFalse(services.is_active())
        self.assertFalse(services.needs_notice())

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

    def test_notice_is_shown_to_admins_and_starts_the_ping(self):
        self.assertFalse(services.is_active())
        self.client.force_login(self.admin)

        response = self.client.get(reverse("settings_app:list"))

        self.assertContains(response, "ativadas por padrão")
        self.assertContains(response, "endereço IP")
        self.assertTrue(services.notice_shown())
        self.assertTrue(services.is_active())

    def test_notice_is_not_shown_to_non_admins_and_does_not_start_anything(self):
        self.client.force_login(self.regular)
        self.client.get("/")

        self.assertFalse(services.notice_shown())
        self.assertFalse(services.is_active())

    def test_notice_disappears_after_a_decision(self):
        self.client.force_login(self.admin)
        with patch("apps.telemetry.services.requests.post", return_value=_ok()):
            self.client.post(reverse("telemetry:consent"), {"choice": "ack"})

        self.assertNotContains(self.client.get(reverse("settings_app:list")), "ativadas por padrão")

    def test_ack_confirms_and_sends(self):
        self.client.force_login(self.admin)
        with patch("apps.telemetry.services.requests.post", return_value=_ok()) as post:
            response = self.client.post(reverse("telemetry:consent"), {"choice": "ack"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(services.get_status(), services.STATUS_ENABLED)
        post.assert_called_once()

    def test_decline_never_sends(self):
        self.client.force_login(self.admin)
        with patch("apps.telemetry.services.requests.post") as post:
            self.client.post(reverse("telemetry:consent"), {"choice": "decline"})

        self.assertEqual(services.get_status(), services.STATUS_DISABLED)
        post.assert_not_called()

    def test_saving_items_applies_immediately_and_drops_switched_off_items(self):
        self.client.force_login(self.admin)
        with patch("apps.telemetry.services.requests.post", return_value=_ok()) as post:
            self.client.post(reverse("telemetry:items"), {"items": ["counts", "worlds", "bogus"]})

        sent = post.call_args.kwargs["json"]
        self.assertEqual(services.enabled_items(), ["counts", "worlds"])
        self.assertFalse(sent["share_ip"])
        self.assertNotIn("timezone", sent)
        self.assertNotIn("usage_30d", sent)
        self.assertIn("worlds", sent)

    def test_settings_page_lists_every_item_with_its_state(self):
        services.save_items({"counts"})
        self.client.force_login(self.admin)
        response = self.client.get(reverse("settings_app:list"))

        self.assertContains(response, 'name="items" value="ip"')
        self.assertContains(response, 'value="counts" class="mt-1" checked')
        self.assertNotContains(response, 'value="ip" class="mt-1" checked')

    def test_non_admin_cannot_change_telemetry(self):
        self.client.force_login(self.regular)
        for name, data in (("telemetry:consent", {"choice": "decline"}), ("telemetry:items", {"items": []})):
            self.assertEqual(self.client.post(reverse(name), data).status_code, 403)

        self.assertEqual(services.get_status(), services.STATUS_UNSET)
        self.assertEqual(services.enabled_items(), services.ITEM_KEYS)

    def test_preview_shows_exact_payload_without_creating_an_install_id(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("telemetry:preview"))

        self.assertContains(response, "&quot;schema&quot;: 1")
        self.assertContains(response, "registra o IP")
        self.assertEqual(get_setting(services.KEY_INSTALL_ID), "")

    def test_next_redirect_rejects_foreign_hosts(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("telemetry:consent"), {"choice": "decline", "next": "https://evil.example/"},
        )

        self.assertRedirects(response, reverse("settings_app:list"), fetch_redirect_response=False)
