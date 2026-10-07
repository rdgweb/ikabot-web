"""N-73: the blackbox token has its own time budget, shared with the hub.

Uses a real local HTTP server that answers slowly, so the timeouts are the real ones
of `requests`. The scale is shrunk (tenths of a second instead of tens of seconds):
what matters is the order  ordinary limit < slow answer < blackbox budget.
"""

import json
import logging
import sys
import threading
import time
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

import requests  # noqa: E402

from core import hub_client as hub_client_module  # noqa: E402
from core.hub_client import BlackboxTimeout, BlackboxUnavailable, HubClient  # noqa: E402
from game_client.auth.lobby import LobbyAuthenticator  # noqa: E402
from game_client.exceptions import LoginError  # noqa: E402

TOKEN = "tok-SECRET-0123456789abcdef"


class _SlowHub(BaseHTTPRequestHandler):
    """Stands in for the hub: answers after `delay` with `status` and `body`."""

    delay = 0.0
    status = 200
    body: dict = {"token": TOKEN}
    seen: list = []

    def do_GET(self):  # noqa: N802 - http.server API
        type(self).seen.append(parse_qs(urlparse(self.path).query))
        time.sleep(type(self).delay)
        payload = json.dumps(type(self).body).encode()
        try:
            self.send_response(type(self).status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            pass        # the client gave up first: exactly what some tests provoke

    def log_message(self, *args):
        pass


class BlackboxBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHub)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _SlowHub.delay, _SlowHub.status, _SlowHub.body, _SlowHub.seen = 0.0, 200, {"token": TOKEN}, []
        self.client = HubClient()
        self.client.base_url = self.base
        patcher = patch.multiple(hub_client_module.settings, blackbox_timeout=3, blackbox_connect_timeout=1)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_answer_slower_than_an_ordinary_call_but_inside_the_budget_arrives(self):
        _SlowHub.delay = 0.8
        with patch.object(hub_client_module, "HUB_TIMEOUT", 0.3):
            # the ordinary limit is too short for this answer...
            with self.assertRaises(requests.exceptions.ReadTimeout):
                self.client._get("/api/agent/blackbox/token", params={"user_agent": "UA/1.0"})
            # ...the blackbox call has its own budget and gets it
            self.assertEqual(self.client.get_blackbox_token("UA/1.0"), TOKEN)

    def test_ordinary_calls_keep_the_short_limit(self):
        self.assertEqual(hub_client_module.HUB_TIMEOUT, 15)
        self.client.session = MagicMock()
        self.client.session.get.return_value.json.return_value = {}
        self.client._get("/api/agent/config")
        self.assertEqual(self.client.session.get.call_args.kwargs["timeout"], 15)

    def test_agent_tells_the_hub_how_long_it_will_wait(self):
        self.client.get_blackbox_token("UA/1.0")

        self.assertEqual(_SlowHub.seen[-1], {"user_agent": ["UA/1.0"], "wait": ["3"]})

    def test_real_defaults_fit_under_a_reverse_proxy(self):
        from core.config import AgentSettings

        defaults = AgentSettings()
        self.assertEqual((defaults.blackbox_timeout, defaults.blackbox_connect_timeout), (55, 5))
        self.assertGreater(defaults.blackbox_timeout, hub_client_module.HUB_TIMEOUT)
        self.assertLess(defaults.blackbox_timeout + defaults.blackbox_connect_timeout, 61)

    def test_generation_over_the_budget_ends_with_a_clear_error(self):
        _SlowHub.delay = 1.6
        with patch.object(hub_client_module.settings, "blackbox_timeout", 1):
            started = time.monotonic()
            with self.assertRaises(BlackboxTimeout) as ctx:
                self.client.get_blackbox_token("UA/1.0")
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIn("1s", str(ctx.exception))
        self.assertTrue(ctx.exception.is_timeout)

    def test_hub_saying_the_token_was_not_ready_is_a_timeout_with_its_explanation(self):
        _SlowHub.status = 504
        _SlowHub.body = {"error": "ikabotapi nao gerou o blackbox em 45s.", "reason": "timeout", "budget_seconds": 45}

        with self.assertRaises(BlackboxTimeout) as ctx:
            self.client.get_blackbox_token("UA/1.0")

        self.assertEqual(str(ctx.exception), "ikabotapi nao gerou o blackbox em 45s.")

    def test_hub_saying_ikabotapi_is_down_is_unavailable(self):
        _SlowHub.status = 502
        _SlowHub.body = {"error": "ikabotapi inacessivel (container parado ou sem rede).", "reason": "unavailable"}

        with self.assertRaises(BlackboxUnavailable) as ctx:
            self.client.get_blackbox_token("UA/1.0")

        self.assertIn("ikabotapi inacessivel", str(ctx.exception))
        self.assertFalse(ctx.exception.is_timeout)

    def test_hub_that_cannot_be_reached_fails_fast(self):
        self.client.base_url = "http://127.0.0.1:9"        # nothing listens there
        started = time.monotonic()

        with self.assertRaises(BlackboxUnavailable):
            self.client.get_blackbox_token("UA/1.0")

        self.assertLess(time.monotonic() - started, 2.5)    # connect limit, not the whole budget

    def test_other_errors_still_surface(self):
        _SlowHub.status = 400
        _SlowHub.body = {"error": "user_agent query parameter is required."}

        with self.assertRaises(requests.exceptions.HTTPError):
            self.client.get_blackbox_token("")

    def test_the_token_never_reaches_the_log(self):
        auth = LobbyAuthenticator(session=MagicMock(), hub=self.client, user_agent="UA/1.0")
        with self.assertLogs(level=logging.DEBUG) as captured:
            token = auth._get_blackbox()
            _SlowHub.status, _SlowHub.body = 504, {"error": "ikabotapi nao gerou o blackbox em 45s."}
            with patch("game_client.auth.lobby.time.sleep"), self.assertRaises(LoginError) as ctx:
                auth._get_blackbox()

        self.assertEqual(token, TOKEN)
        logged = "\n".join(captured.output) + str(ctx.exception)
        self.assertNotIn(TOKEN, logged)
        self.assertNotIn("SECRET", logged)
        self.assertIn(f"{len(TOKEN)} chars", logged)        # only its size is recorded


class BlackboxRetryPolicyTests(unittest.TestCase):
    """How the login reacts to each kind of failure (no network here)."""

    def setUp(self):
        self.hub = MagicMock()
        self.auth = LobbyAuthenticator(session=MagicMock(), hub=self.hub, user_agent="UA/1.0")

    @patch("game_client.auth.lobby.time.sleep")
    def test_unreachable_is_retried_with_a_short_growing_pause(self, sleep):
        self.hub.get_blackbox_token.side_effect = [BlackboxUnavailable("hub inacessivel"), BlackboxUnavailable("hub inacessivel"), "tok"]

        self.assertEqual(self.auth._get_blackbox(), "tok")

        self.assertEqual([call.args[0] for call in sleep.call_args_list], [3, 6])

    @patch("game_client.auth.lobby.time.sleep")
    def test_a_timeout_gets_one_more_try_and_no_more(self, sleep):
        self.hub.get_blackbox_token.side_effect = BlackboxTimeout("ikabotapi nao gerou o blackbox em 45s.")

        with self.assertRaises(LoginError) as ctx:
            self.auth._get_blackbox()

        self.assertEqual(self.hub.get_blackbox_token.call_count, 2)       # not three whole budgets
        message = str(ctx.exception)
        self.assertIn("limite de tempo", message)
        self.assertIn("ikabotapi nao gerou o blackbox em 45s.", message)
        self.assertNotIn("indisponivel apos", message)                    # a different problem, a different text
        self.assertEqual(sleep.call_count, 1)

    @patch("game_client.auth.lobby.time.sleep")
    def test_a_timeout_followed_by_a_good_answer_logs_in(self, sleep):
        self.hub.get_blackbox_token.side_effect = [BlackboxTimeout("lento"), "tok"]

        self.assertEqual(self.auth._get_blackbox(), "tok")

    @patch("game_client.auth.lobby.time.sleep")
    def test_mixed_failures_stay_within_three_attempts(self, sleep):
        self.hub.get_blackbox_token.side_effect = [BlackboxUnavailable("fora"), BlackboxTimeout("lento"), BlackboxUnavailable("fora")]

        with self.assertRaises(LoginError) as ctx:
            self.auth._get_blackbox()

        self.assertEqual(self.hub.get_blackbox_token.call_count, 3)
        self.assertIn("Blackbox indisponivel apos 3 tentativas", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
