"""N-73: the hub side of the blackbox time budget (ikabotapi is simulated)."""

import logging
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.game.api.agent import (
    BLACKBOX_ANSWER_MARGIN,
    BLACKBOX_CONNECT_TIMEOUT,
    BLACKBOX_DEFAULT_BUDGET,
    BLACKBOX_MAX_BUDGET,
    blackbox_budget,
)
from apps.settings_app.models import AppSetting

TOKEN = "tok-SECRET-0123456789abcdef"
URL = "/api/agent/blackbox/token/"


def _answer(payload=TOKEN, status=200):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    if status >= 400:
        error = requests.HTTPError(f"{status} Server Error: body with {TOKEN}")
        error.response = response
        response.raise_for_status.side_effect = error
    return response


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="", IKABOTAPI_URL="http://ikabotapi.test:5005")
class BlackboxBudgetTests(TestCase):
    def _get(self, **params):
        return self.client.get(URL, {"user_agent": "UA/1.0", **params}, HTTP_X_AGENT_TOKEN="test-agent-token")

    def _set(self, value):
        AppSetting.objects.update_or_create(key="blackbox_timeout_seconds", defaults={"value": str(value)})

    # ── the budget ──

    def test_budget_rules(self):
        self.assertEqual(blackbox_budget(), BLACKBOX_DEFAULT_BUDGET)
        self.assertEqual(blackbox_budget(""), BLACKBOX_DEFAULT_BUDGET)
        self.assertEqual(blackbox_budget("abc"), BLACKBOX_DEFAULT_BUDGET)
        # an agent that waits 55s is answered before that
        self.assertEqual(blackbox_budget(55), 45)
        self.assertEqual(blackbox_budget(30), 30 - BLACKBOX_ANSWER_MARGIN)
        self.assertEqual(blackbox_budget(5), 3)             # never less than a real chance
        # a patient agent does not stretch the hub's own limit
        self.assertEqual(blackbox_budget(600), BLACKBOX_DEFAULT_BUDGET)

    def test_hub_limit_is_configurable_and_bounded(self):
        self._set(20)
        self.assertEqual(blackbox_budget(), 20)
        self.assertEqual(blackbox_budget(55), 20)
        self._set(9999)
        self.assertEqual(blackbox_budget(), BLACKBOX_MAX_BUDGET)     # stays under the HTTP server timeout
        self._set(0)
        self.assertEqual(blackbox_budget(), 3)

    # ── the proxied call ──

    @patch("apps.game.api.agent.http_requests.get")
    def test_token_is_returned_and_ikabotapi_gets_the_budget(self, ikabotapi):
        ikabotapi.return_value = _answer()

        response = self._get(wait=55)

        self.assertEqual((response.status_code, response.json()), (200, {"token": TOKEN}))
        self.assertEqual(ikabotapi.call_args.kwargs["timeout"], (BLACKBOX_CONNECT_TIMEOUT, 45))
        self.assertEqual(ikabotapi.call_args.kwargs["params"], {"user_agent": "UA/1.0"})

    @patch("apps.game.api.agent.http_requests.get")
    def test_agent_without_wait_gets_the_hub_limit(self, ikabotapi):
        ikabotapi.return_value = _answer({"token": TOKEN})

        response = self._get()

        self.assertEqual(response.json(), {"token": TOKEN})
        self.assertEqual(ikabotapi.call_args.kwargs["timeout"], (BLACKBOX_CONNECT_TIMEOUT, BLACKBOX_DEFAULT_BUDGET))

    @patch("apps.game.api.agent.http_requests.get", side_effect=requests.ReadTimeout("read timed out"))
    def test_not_ready_within_the_budget_is_a_clear_timeout(self, _ikabotapi):
        response = self._get(wait=30)

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json(), {"error": "ikabotapi nao gerou o blackbox em 22s.", "reason": "timeout", "budget_seconds": 22})

    def test_unreachable_and_slow_to_connect_are_both_unavailable(self):
        for error in (requests.ConnectionError("refused"), requests.ConnectTimeout("connect timed out")):
            with patch("apps.game.api.agent.http_requests.get", side_effect=error):
                response = self._get(wait=55)
            self.assertEqual((response.status_code, response.json()["reason"]), (502, "unavailable"), type(error).__name__)

    @patch("apps.game.api.agent.http_requests.get")
    def test_error_answer_from_ikabotapi_is_unavailable_without_copying_its_body(self, ikabotapi):
        ikabotapi.return_value = _answer(status=500)

        with self.assertLogs("apps.game.api.agent", level=logging.ERROR) as captured:
            response = self._get(wait=55)

        self.assertEqual((response.status_code, response.json()), (502, {"error": "ikabotapi respondeu HTTP 500.", "reason": "unavailable"}))
        self.assertNotIn(TOKEN, "\n".join(captured.output) + response.content.decode())

    @patch("apps.game.api.agent.http_requests.get")
    def test_answer_that_is_not_json_is_unavailable(self, ikabotapi):
        broken = _answer()
        broken.json.side_effect = ValueError("no json")
        ikabotapi.return_value = broken

        response = self._get(wait=55)

        self.assertEqual((response.status_code, response.json()["reason"]), (502, "unavailable"))

    @patch("apps.game.api.agent.http_requests.get")
    def test_the_token_is_never_logged(self, ikabotapi):
        ikabotapi.return_value = _answer()

        with self.assertNoLogs("apps.game.api.agent", level=logging.DEBUG):
            self._get(wait=55)

    def test_requires_the_agent_token_and_a_user_agent(self):
        self.assertIn(self.client.get(URL, {"user_agent": "UA/1.0"}).status_code, (401, 403))
        response = self.client.get(URL, HTTP_X_AGENT_TOKEN="test-agent-token")
        self.assertEqual(response.status_code, 400)

    # ── the setting on the settings page ──

    def test_limit_can_be_changed_on_the_settings_page(self):
        user = get_user_model().objects.create_superuser(username="cfg", email="cfg@example.com", password="secret123")
        self.client.force_login(user)
        base = {
            "snapshot_stale_seconds": 7200, "building_options_stale_seconds": 21600,
            "running_job_recovery_grace_seconds": 300, "running_job_lease_seconds": 180,
        }

        self.client.post(reverse("settings_app:snapshot-policy-save"), {**base, "blackbox_timeout_seconds": 30})
        self.assertEqual(blackbox_budget(), 30)

        # the field is optional: an older form post does not reset it
        self.client.post(reverse("settings_app:snapshot-policy-save"), base)
        self.assertEqual(blackbox_budget(), 30)

        # out of bounds is refused, nothing changes
        self.client.post(reverse("settings_app:snapshot-policy-save"), {**base, "blackbox_timeout_seconds": 500})
        self.assertEqual(blackbox_budget(), 30)
        self.assertIn("Limite para gerar o blackbox", self.client.get(reverse("settings_app:list")).content.decode())
