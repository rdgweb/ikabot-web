"""N-74: one regional context (locale, language, timezone) for the lobby headers, the
credentials and the blackbox token; nothing changes while the hub has none configured."""

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules leave bare stub packages behind; this one needs the real ones.
for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "core")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from core import hub_client as hub_client_module  # noqa: E402
from core.hub_client import LEGACY_LOGIN_CONTEXT, BlackboxRejected, HubClient  # noqa: E402
from game_client.auth.lobby import LobbyAuthenticator  # noqa: E402
from game_client.auth.login import IkariamAuth  # noqa: E402
from game_client.exceptions import LoginError  # noqa: E402

BRAZIL = {
    "locale_configured": True, "locale": "pt-BR", "gf_lang": "pt",
    "accept_language": "pt-BR,pt;q=0.9,en;q=0.8", "timezone_id": "America/Sao_Paulo",
}


class _Session:
    """Records what the authenticator sends: headers at the time of each request, and the body."""

    def __init__(self):
        self.headers = {}
        self.cookies = MagicMock()
        self.calls = []

    def _record(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": dict(self.headers), **kwargs})
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        if "auth/thin/sessions" in url or "sessions" in url and method == "POST":
            response.text = '{"token": "lobby-token"}'
            response.json.return_value = {"token": "lobby-token"}
        elif url.endswith("/loginLink"):
            response.text = '{"url": "https://s1-br.ikariam.gameforge.com/index.php?x=1"}'
            response.json.return_value = {"url": "https://s1-br.ikariam.gameforge.com/index.php?x=1"}
        elif "configuration.js" in url or "config" in url:
            response.text = '"gameEnvironmentId":"env-1","platformGameId":"game-1"'
        else:
            response.text = "[]"
            response.json.return_value = []
        return response

    def get(self, url, **kwargs):
        return self._record("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._record("POST", url, **kwargs)

    def options(self, url, **kwargs):
        return self._record("OPTIONS", url, **kwargs)

    def sent(self, fragment):
        return next(call for call in self.calls if fragment in call["url"])


def _hub(context):
    hub = MagicMock()
    hub.get_login_context.return_value = context
    hub.get_blackbox_token.return_value = "blackbox-token"
    return hub


class LobbyRegionalContextTests(unittest.TestCase):
    def _login(self, context):
        session, hub = _Session(), _hub(context)
        auth = LobbyAuthenticator(session, hub, "UA/1.0")
        with patch("game_client.auth.lobby.LOBBY_LOGIN_URL", "https://gameforge.com/api/v1/auth/thin/sessions"):
            token = auth.authenticate("user@example.com", "pw")
            auth.fetch_accounts(token)
            auth.get_login_link(token, {"id": 7, "server": {"language": "br", "number": 1}})
        return session, hub

    def test_nothing_changes_while_no_locale_is_configured(self):
        session, hub = self._login(dict(LEGACY_LOGIN_CONTEXT))

        credentials = next(call for call in session.calls if call["method"] == "POST" and "thin/sessions" in call["url"])
        self.assertEqual((credentials["json"]["locale"], credentials["json"]["gfLang"]), ("en-GB", "en"))
        self.assertEqual(credentials["headers"]["Accept-Language"], "en-US,en;q=0.5")
        self.assertEqual(session.sent("/users/me/accounts")["headers"]["Referer"], "https://lobby.ikariam.gameforge.com/es_AR/hub")
        link = session.sent("/loginLink")
        self.assertEqual(link["headers"]["accept-language"], "en-US,en;q=0.9")
        self.assertEqual(link["headers"]["referer"], "https://lobby.ikariam.gameforge.com/en_GB/accounts")
        self.assertEqual({call["data"]["language"] for call in session.calls if "pixelzirkus" in call["url"]}, {"en"})
        # the token is asked exactly as before: no locale, no timezone
        self.assertEqual([call.args + tuple(call.kwargs.items()) for call in hub.get_blackbox_token.call_args_list], [("UA/1.0",), ("UA/1.0",)])

    def test_configured_locale_and_timezone_are_the_same_everywhere(self):
        session, hub = self._login(BRAZIL)

        credentials = next(call for call in session.calls if call["method"] == "POST" and "thin/sessions" in call["url"])
        self.assertEqual((credentials["json"]["locale"], credentials["json"]["gfLang"]), ("pt-BR", "pt"))
        languages = {call["headers"].get("Accept-Language") or call["headers"].get("accept-language") for call in session.calls}
        self.assertEqual(languages, {"pt-BR,pt;q=0.9,en;q=0.8"})        # every request of the login, one language
        self.assertEqual(session.sent("/users/me/accounts")["headers"]["Referer"], "https://lobby.ikariam.gameforge.com/pt_BR/hub")
        self.assertEqual(session.sent("/loginLink")["headers"]["referer"], "https://lobby.ikariam.gameforge.com/pt_BR/accounts")
        self.assertEqual({call["data"]["language"] for call in session.calls if "pixelzirkus" in call["url"]}, {"pt"})
        # both tokens (lobby login and server login) are generated in that same context
        for call in hub.get_blackbox_token.call_args_list:
            self.assertEqual((call.args, call.kwargs), (("UA/1.0",), {"locale": "pt-BR", "timezone_id": "America/Sao_Paulo"}))
        self.assertEqual(hub.get_blackbox_token.call_count, 2)
        hub.get_login_context.assert_called_once()                      # asked once per login, not per request

    def test_timezone_alone_reaches_the_token_and_leaves_the_headers_alone(self):
        session, hub = self._login({**LEGACY_LOGIN_CONTEXT, "timezone_id": "America/Sao_Paulo"})

        credentials = next(call for call in session.calls if call["method"] == "POST" and "thin/sessions" in call["url"])
        self.assertEqual((credentials["json"]["locale"], credentials["headers"]["Accept-Language"]), ("en-GB", "en-US,en;q=0.5"))
        self.assertEqual(hub.get_blackbox_token.call_args.kwargs, {"locale": "", "timezone_id": "America/Sao_Paulo"})

    def test_a_hub_that_cannot_say_falls_back_to_what_was_always_sent(self):
        for broken in (None, {}, "nope", {"locale": ""}):
            hub = _hub(broken)
            auth = LobbyAuthenticator(_Session(), hub, "UA/1.0")
            self.assertEqual(auth.context, LEGACY_LOGIN_CONTEXT, repr(broken))
        hub = MagicMock()
        hub.get_login_context.side_effect = RuntimeError("hub down")
        auth = LobbyAuthenticator(_Session(), hub, "UA/1.0")
        self.assertEqual(auth._accept_language(), "en-US,en;q=0.5")
        # a hub client from before this existed (no such method) is fine too
        self.assertEqual(LobbyAuthenticator(_Session(), object(), "UA/1.0").context, LEGACY_LOGIN_CONTEXT)

    @patch("game_client.auth.lobby.time.sleep")
    def test_a_refused_context_fails_at_once_with_the_reason(self, sleep):
        hub = _hub(BRAZIL)
        hub.get_blackbox_token.side_effect = BlackboxRejected("Fuso horario invalido: 'Mars/Phobos'.")
        auth = LobbyAuthenticator(_Session(), hub, "UA/1.0")

        with self.assertRaises(LoginError) as ctx:
            auth._get_blackbox()

        self.assertEqual(hub.get_blackbox_token.call_count, 1)          # asking again would change nothing
        sleep.assert_not_called()
        self.assertIn("Mars/Phobos", str(ctx.exception))

    def test_in_game_requests_use_the_login_language(self):
        self.assertEqual(IkariamAuth(_Session(), _hub(BRAZIL), "UA/1.0").accept_language(), "pt-BR,pt;q=0.9,en;q=0.8")
        self.assertEqual(IkariamAuth(_Session(), _hub(dict(LEGACY_LOGIN_CONTEXT)), "UA/1.0").accept_language(), "en-US,en;q=0.5")


class _Hub(BaseHTTPRequestHandler):
    """Stands in for the hub."""

    answers: dict = {}
    seen: list = []

    def do_GET(self):  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")           # the client always adds the trailing slash
        type(self).seen.append((path, parse_qs(parsed.query)))
        status, body = type(self).answers.get(path, (404, {"detail": "Not found."}))
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class HubContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Hub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Hub.answers, _Hub.seen = {"/api/agent/blackbox/token": (200, {"token": "tok"})}, []
        hub_client_module._login_context_cache.update({"at": 0.0, "value": None})
        self.addCleanup(lambda: hub_client_module._login_context_cache.update({"at": 0.0, "value": None}))
        self.client = HubClient()
        self.client.base_url = self.base
        patcher = patch.multiple(hub_client_module.settings, blackbox_timeout=3, blackbox_connect_timeout=1)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_context_comes_from_the_hub_and_is_reused(self):
        _Hub.answers["/api/agent/login-context"] = (200, BRAZIL)

        self.assertEqual(self.client.get_login_context(), BRAZIL)
        self.assertEqual(self.client.get_login_context(), BRAZIL)

        self.assertEqual([path for path, _query in _Hub.seen], ["/api/agent/login-context"])     # second one from the cache

    def test_hub_without_a_locale_keeps_the_historical_values(self):
        _Hub.answers["/api/agent/login-context"] = (200, {
            "locale_configured": False, "locale": "en-GB", "gf_lang": "en", "accept_language": "en-US,en;q=0.5", "timezone_id": "America/Cuiaba",
        })

        self.assertEqual(self.client.get_login_context(), {**LEGACY_LOGIN_CONTEXT, "timezone_id": "America/Cuiaba"})

    def test_old_hub_or_hub_down_means_legacy_and_is_asked_again_later(self):
        self.assertEqual(self.client.get_login_context(), LEGACY_LOGIN_CONTEXT)      # 404: a hub from before N-74
        down = HubClient()
        down.base_url = "http://127.0.0.1:9"
        hub_client_module._login_context_cache.update({"at": 0.0, "value": None})
        with patch.object(hub_client_module, "HUB_TIMEOUT", 1):
            self.assertEqual(down.get_login_context(), LEGACY_LOGIN_CONTEXT)
        self.assertIsNone(hub_client_module._login_context_cache["value"])           # a failure is not remembered

    def test_token_request_carries_the_context_only_when_there_is_one(self):
        self.client.get_blackbox_token("UA/1.0")
        self.client.get_blackbox_token("UA/1.0", locale="pt-BR", timezone_id="America/Sao_Paulo")
        self.client.get_blackbox_token("UA/1.0", timezone_id="America/Sao_Paulo")

        self.assertEqual([query for _path, query in _Hub.seen], [
            {"user_agent": ["UA/1.0"], "wait": ["3"]},
            {"user_agent": ["UA/1.0"], "wait": ["3"], "locale": ["pt-BR"], "timezone_id": ["America/Sao_Paulo"]},
            {"user_agent": ["UA/1.0"], "wait": ["3"], "timezone_id": ["America/Sao_Paulo"]},
        ])

    def test_hub_refusing_the_context_is_a_final_error_with_its_message(self):
        _Hub.answers["/api/agent/blackbox/token"] = (400, {"error": "Idioma invalido: 'xx_YY'.", "reason": "invalid_context"})

        with self.assertRaises(BlackboxRejected) as ctx:
            self.client.get_blackbox_token("UA/1.0", locale="xx_YY")

        self.assertEqual(str(ctx.exception), "Idioma invalido: 'xx_YY'.")
        self.assertTrue(ctx.exception.is_final)

    def test_token_from_an_ikabotapi_that_ignored_the_context_still_logs_in(self):
        _Hub.answers["/api/agent/blackbox/token"] = (200, {"token": "tok", "context_applied": False})

        with self.assertLogs(level="WARNING") as captured:
            token = self.client.get_blackbox_token("UA/1.0", locale="pt-BR")

        self.assertEqual(token, "tok")
        self.assertIn("nao aceita esses parametros", "\n".join(captured.output))
        self.assertNotIn("tok", "\n".join(captured.output).replace("token", ""))


if __name__ == "__main__":
    unittest.main()
