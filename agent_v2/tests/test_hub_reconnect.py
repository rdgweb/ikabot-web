"""N-133: a connection the hub has just closed is not a failed job — reads are sent again once, and a hub
that cannot be reached while fetching credentials is a network failure, not "no credentials"."""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from core.hub_client import HubSession
from runners.base import BaseRunner

ROOT = Path(__file__).resolve().parents[1]


class HubSessionTests(unittest.TestCase):
    def _session(self, *outcomes):
        session = HubSession()
        calls = list(outcomes)

        def fake(self_, method, url, *args, **kwargs):
            item = calls.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        return session, patch.object(requests.Session, "request", fake), calls

    def test_a_read_on_a_dropped_connection_is_sent_again_once(self):
        ok = MagicMock(status_code=200)
        session, fake, left = self._session(requests.ConnectionError("Connection reset by peer"), ok)
        with fake:
            self.assertIs(session.get("http://hub:8000/api/agent/config/"), ok)
        self.assertEqual(left, [])

    def test_only_once(self):
        drop = requests.ConnectionError("Connection reset by peer")
        session, fake, _left = self._session(drop, drop, MagicMock())
        with fake, self.assertRaises(requests.ConnectionError):
            session.get("http://hub:8000/api/agent/config/")

    def test_a_write_is_never_repeated(self):
        session, fake, left = self._session(requests.ConnectionError("Connection reset by peer"), MagicMock())
        with fake, self.assertRaises(requests.ConnectionError):
            session.post("http://hub:8000/api/agent/jobs/x/logs/", json={})
        self.assertEqual(len(left), 1)

    def test_a_timeout_is_not_repeated(self):
        session, fake, left = self._session(requests.ConnectTimeout("slow"), MagicMock())
        with fake, self.assertRaises(requests.Timeout):
            session.get("http://hub:8000/api/agent/config/")
        self.assertEqual(len(left), 1)


class HubRestartTests(unittest.TestCase):
    """N-136: the proxy answers 502/503/504 while the hub restarts or renews a process."""

    def _session(self, *outcomes):
        session = HubSession()
        calls = list(outcomes)

        def fake(self_, method, url, *args, **kwargs):
            return calls.pop(0)

        return session, patch.object(requests.Session, "request", fake), calls

    def test_a_read_answered_502_is_sent_again_after_a_pause(self):
        ok = MagicMock(status_code=200)
        session, fake, left = self._session(MagicMock(status_code=502), ok)
        with fake, patch("time.sleep") as pause:
            self.assertIs(session.get("http://hub:8000/api/agent/config/"), ok)
        pause.assert_called_once()
        self.assertEqual(left, [])

    def test_a_write_answered_502_is_not_repeated(self):
        bad = MagicMock(status_code=502)
        session, fake, left = self._session(bad, MagicMock(status_code=200))
        with fake, patch("time.sleep"):
            self.assertIs(session.post("http://hub:8000/api/agent/jobs/x/logs/", json={}), bad)
        self.assertEqual(len(left), 1)

    def test_the_executor_counts_a_bad_gateway_as_a_network_failure(self):
        source = (ROOT / "job_executor.py").read_text(encoding="utf-8")
        self.assertIn('"bad gateway"', source)
        self.assertIn('"503 server error"', source)


class CredentialsTests(unittest.TestCase):
    def _runner(self, hub):
        runner = BaseRunner.__new__(BaseRunner)
        runner.hub = hub
        return runner

    def test_an_unreachable_hub_is_a_network_failure(self):
        hub = MagicMock()
        hub.get_config.side_effect = requests.ConnectionError("('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))")
        with self.assertRaises(requests.ConnectionError):
            self._runner(hub).resolve_credentials("acc", {})

    def test_an_account_without_credentials_is_still_none(self):
        hub = MagicMock()
        hub.get_config.return_value = {"accounts": [{"id": "acc", "email": "", "password": ""}]}
        self.assertIsNone(self._runner(hub).resolve_credentials("acc", {}))


if __name__ == "__main__":
    unittest.main()
