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
from apps.game.services.login_context import (
    LOCALE_SETTING,
    TIMEZONE_SETTING,
    InvalidLoginContext,
    clean_locale,
    clean_timezone,
    login_context,
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


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="", IKABOTAPI_URL="http://ikabotapi.test:5005")
class LoginContextTests(TestCase):
    """N-74: locale and timezone, the same for the lobby login and for the blackbox token."""

    CONTEXT_URL = "/api/agent/login-context/"
    BASE = {
        "snapshot_stale_seconds": 7200, "building_options_stale_seconds": 21600,
        "running_job_recovery_grace_seconds": 300, "running_job_lease_seconds": 180,
    }

    def _token(self, **params):
        return self.client.get(URL, {"user_agent": "UA/1.0", **params}, HTTP_X_AGENT_TOKEN="test-agent-token")

    def _set(self, locale="", timezone_id=""):
        AppSetting.objects.update_or_create(key=LOCALE_SETTING, defaults={"value": locale})
        AppSetting.objects.update_or_create(key=TIMEZONE_SETTING, defaults={"value": timezone_id})

    # ── the values ──

    def test_locale_and_timezone_are_checked_and_normalised(self):
        self.assertEqual([clean_locale(value) for value in ("", None, "pt-br", "PT-BR", "en-GB", "es-419", "pt")], ["", "", "pt-BR", "pt-BR", "en-GB", "es-419", "pt"])
        self.assertEqual([clean_timezone(value) for value in ("", None, "America/Sao_Paulo", "UTC", "America/Argentina/Buenos_Aires")], ["", "", "America/Sao_Paulo", "UTC", "America/Argentina/Buenos_Aires"])
        for bad in ("pt_BR", "portugues", "pt-", "p", "pt-BR;q=0.9", "<script>"):
            with self.assertRaises(InvalidLoginContext, msg=bad):
                clean_locale(bad)
        for bad in ("Sao_Paulo", "America/Nowhere", "GMT-3", "America/Sao Paulo", "../etc/passwd"):
            with self.assertRaises(InvalidLoginContext, msg=bad):
                clean_timezone(bad)

    def test_nothing_configured_is_what_was_always_sent(self):
        self.assertEqual(login_context(), {
            "locale_configured": False, "locale": "en-GB", "gf_lang": "en", "accept_language": "en-US,en;q=0.5", "timezone_id": "",
        })

    def test_configured_context(self):
        self._set("pt-BR", "America/Sao_Paulo")
        self.assertEqual(login_context(), {
            "locale_configured": True, "locale": "pt-BR", "gf_lang": "pt",
            "accept_language": "pt-BR,pt;q=0.9,en;q=0.8", "timezone_id": "America/Sao_Paulo",
        })
        self._set("en-US", "")
        self.assertEqual((login_context()["accept_language"], login_context()["timezone_id"]), ("en-US,en;q=0.9", ""))
        # a stored value that stopped being valid does not break the logins
        self._set("not a locale", "Mars/Phobos")
        self.assertEqual(login_context()["locale_configured"], False)
        self.assertEqual(login_context()["timezone_id"], "")

    def test_agents_read_the_context_from_the_hub(self):
        self._set("pt-BR", "America/Sao_Paulo")
        self.assertIn(self.client.get(self.CONTEXT_URL).status_code, (401, 403))

        response = self.client.get(self.CONTEXT_URL, HTTP_X_AGENT_TOKEN="test-agent-token")

        self.assertEqual((response.status_code, response.json()["locale"], response.json()["timezone_id"]), (200, "pt-BR", "America/Sao_Paulo"))

    # ── the token ──

    @patch("apps.game.api.agent.http_requests.get")
    def test_context_reaches_ikabotapi(self, ikabotapi):
        ikabotapi.return_value = _answer()

        response = self._token(wait=55, locale="pt-BR", timezone_id="America/Sao_Paulo")

        self.assertEqual((response.status_code, response.json()), (200, {"token": TOKEN, "context_applied": True}))
        self.assertEqual(ikabotapi.call_args.kwargs["params"], {"user_agent": "UA/1.0", "locale": "pt-BR", "timezone_id": "America/Sao_Paulo"})
        self.assertEqual(ikabotapi.call_count, 1)

    @patch("apps.game.api.agent.http_requests.get")
    def test_only_what_was_asked_is_sent(self, ikabotapi):
        ikabotapi.return_value = _answer()

        self._token(timezone_id="America/Sao_Paulo")
        self.assertEqual(ikabotapi.call_args.kwargs["params"], {"user_agent": "UA/1.0", "timezone_id": "America/Sao_Paulo"})
        # the system setting is not applied behind the agent's back: the agent says what it presents
        self._set("pt-BR", "America/Sao_Paulo")
        response = self._token()
        self.assertEqual(ikabotapi.call_args.kwargs["params"], {"user_agent": "UA/1.0"})
        self.assertEqual(response.json(), {"token": TOKEN})

    @patch("apps.game.api.agent.http_requests.get")
    def test_invalid_context_is_refused_before_ikabotapi_is_called(self, ikabotapi):
        for params, word in (({"locale": "pt_BR"}, "Idioma invalido"), ({"timezone_id": "America/Nowhere"}, "Fuso horario invalido")):
            response = self._token(**params)
            self.assertEqual((response.status_code, response.json()["reason"]), (400, "invalid_context"), params)
            self.assertIn(word, response.json()["error"])
        ikabotapi.assert_not_called()

    @patch("apps.game.api.agent.http_requests.get")
    def test_old_ikabotapi_gets_the_request_it_understands(self, ikabotapi):
        for refusal in (400, 422):
            ikabotapi.reset_mock()
            ikabotapi.side_effect = [_answer(status=refusal), _answer()]

            with self.assertLogs("apps.game.api.agent", level=logging.WARNING) as captured:
                response = self._token(wait=55, locale="pt-BR", timezone_id="America/Sao_Paulo")

            self.assertEqual((response.status_code, response.json()), (200, {"token": TOKEN, "context_applied": False}), refusal)
            self.assertEqual([call.kwargs["params"] for call in ikabotapi.call_args_list], [
                {"user_agent": "UA/1.0", "locale": "pt-BR", "timezone_id": "America/Sao_Paulo"}, {"user_agent": "UA/1.0"},
            ])
            self.assertNotIn(TOKEN, "\n".join(captured.output))

    @patch("apps.game.api.agent.http_requests.get")
    def test_a_refusal_without_context_is_not_retried(self, ikabotapi):
        ikabotapi.return_value = _answer(status=400)      # e.g. a user agent ikabotapi does not support

        response = self._token(wait=55)

        self.assertEqual((response.status_code, response.json()["reason"]), (502, "unavailable"))
        self.assertEqual(ikabotapi.call_count, 1)

    # ── the settings page ──

    def test_settings_page_saves_checks_and_clears(self):
        user = get_user_model().objects.create_superuser(username="cfg74", email="cfg74@example.com", password="secret123")
        self.client.force_login(user)
        save = reverse("settings_app:snapshot-policy-save")

        self.client.post(save, {**self.BASE, "login_locale": "pt-br", "login_timezone_id": "America/Sao_Paulo"})
        self.assertEqual((login_context()["locale"], login_context()["timezone_id"]), ("pt-BR", "America/Sao_Paulo"))

        # an older form post (without the fields) does not erase the choice
        self.client.post(save, self.BASE)
        self.assertEqual(login_context()["locale"], "pt-BR")

        # an invalid value is refused with its reason and nothing changes
        response = self.client.post(save, {**self.BASE, "login_locale": "pt-BR", "login_timezone_id": "Brasil/Aqui"}, follow=True)
        self.assertIn("Fuso horario invalido", response.content.decode())
        self.assertEqual(login_context()["timezone_id"], "America/Sao_Paulo")

        page = self.client.get(reverse("settings_app:list")).content.decode()
        self.assertIn("Idioma do navegador no login", page)
        self.assertIn('value="pt-BR"', page)

        # empty fields go back to the compatibility default
        self.client.post(save, {**self.BASE, "login_locale": "", "login_timezone_id": ""})
        self.assertEqual(login_context()["locale_configured"], False)
