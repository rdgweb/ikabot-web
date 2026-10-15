"""N-132: a frozen ikabotapi is noticed from the blackbox requests themselves, and the memory meter notes
the requests that make a hub process swell."""
from unittest.mock import patch

import requests as http_requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from apps.accounts.models import Node
from apps.game.services import ikabotapi_watch as watch
from core.middleware import MemoryWatchMiddleware


@override_settings(AGENT_TOKEN="", AGENT_ALLOWED_IPS="")
class IkabotapiWatchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.node = Node.objects.create(name="agente", transport="https")
        self.key = self.node.new_agent_token

    def _ask(self):
        return self.client.get("/api/agent/blackbox/token/", {"user_agent": "Mozilla/5.0", "wait": 30},
                               HTTP_X_AGENT_TOKEN=self.key, HTTP_X_AGENT_NODE_ID=str(self.node.pk))

    def test_three_failures_in_a_row_tell_the_platform_once(self):
        with patch("apps.game.api.agent.http_requests.get", side_effect=http_requests.Timeout()), \
             patch.object(watch, "_tell") as tell:
            for _ in range(4):
                self.assertEqual(self._ask().status_code, 504)
        self.assertTrue(watch.is_stuck())
        tell.assert_called_once()
        self.assertIn("não está gerando o blackbox", tell.call_args.args[0])

    def test_a_good_token_clears_it_and_says_so(self):
        cache.set(watch.STUCK_KEY, 1)
        good = type("R", (), {"status_code": 200, "raise_for_status": lambda self: None, "json": lambda self: "tok"})()
        with patch("apps.game.api.agent.http_requests.get", return_value=good), patch.object(watch, "_tell") as tell:
            self.assertEqual(self._ask().status_code, 200)
        self.assertFalse(watch.is_stuck())
        self.assertIn("voltou", tell.call_args.args[0])

    def test_two_failures_are_not_an_alarm(self):
        with patch("apps.game.api.agent.http_requests.get", side_effect=http_requests.Timeout()), \
             patch.object(watch, "_tell") as tell:
            self._ask()
            self._ask()
        self.assertFalse(watch.is_stuck())
        tell.assert_not_called()

    def test_the_platform_dashboard_shows_it(self):
        cache.set(watch.STUCK_KEY, 1)
        staff = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(staff)
        self.assertIn("ikabotapi não está gerando o blackbox", self.client.get("/").content.decode())


class MemoryWatchTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_only_big_growth_is_noted_with_the_path_only(self):
        sizes = iter([100.0, 160.0, 160.0, 170.0])
        middleware = MemoryWatchMiddleware(lambda request: type("R", (), {"status_code": 200})())
        with patch.object(MemoryWatchMiddleware, "_rss_mb", side_effect=lambda self=None: next(sizes)):
            middleware(RequestFactory().get("/game/?secret=abc"))
            middleware(RequestFactory().get("/jobs/"))
        events = cache.get("memwatch:events")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["path"], events[0]["grew_mb"]), ("/game/", 60.0))
        self.assertNotIn("secret", str(events))
