"""N-131: a platform agent (AGENT_REQUIRE_PROXY) never reaches the game without a proxy."""
import os
import unittest
from unittest.mock import patch

import requests

from core.proxy import ProxyRequired, StrictProxySession


class ProxyRequiredTests(unittest.TestCase):
    def _send(self, session, url):
        request = requests.Request("GET", url).prepare()
        with patch.object(requests.Session, "send", return_value="sent") as parent:
            result = session.send(request)
        return result, parent

    def test_without_the_flag_nothing_changes(self):
        with patch.dict(os.environ, {"AGENT_REQUIRE_PROXY": ""}):
            result, _parent = self._send(StrictProxySession(""), "https://s78-br.ikariam.gameforge.com/index.php")
        self.assertEqual(result, "sent")

    def test_with_the_flag_a_game_request_without_proxy_is_refused(self):
        with patch.dict(os.environ, {"AGENT_REQUIRE_PROXY": "1"}):
            with self.assertRaises(ProxyRequired):
                self._send(StrictProxySession(""), "https://lobby.ikariam.gameforge.com/api/users/me")

    def test_with_the_flag_and_a_proxy_it_goes_through_the_proxy(self):
        with patch.dict(os.environ, {"AGENT_REQUIRE_PROXY": "1"}):
            _result, parent = self._send(StrictProxySession("http://u:p@10.0.0.1:8080"), "https://s78-br.ikariam.gameforge.com/")
        self.assertEqual(parent.call_args.kwargs["proxies"]["https"], "http://u:p@10.0.0.1:8080")

    def test_the_hub_itself_never_needs_a_proxy(self):
        with patch.dict(os.environ, {"AGENT_REQUIRE_PROXY": "1"}):
            result, _parent = self._send(StrictProxySession(""), "https://ikabot.rdgh.com.br/api/agent/heartbeat/")
        self.assertEqual(result, "sent")


if __name__ == "__main__":
    unittest.main()
