"""No game request is sent after expiry, including cached and manual sessions."""
import sys
import unittest
import types
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
config = types.ModuleType("core.config")
config.settings = types.SimpleNamespace(hub_url="http://hub.invalid", agent_token="test-only", agent_node_id="test-node")
sys.modules["core.config"] = config
import requests
from core.hub_client import HubClient
from core.operation import OperationBlocked
from core.proxy import StrictProxySession
from game_client.client import GameClient
from game_client.auth.lobby import LobbyAuthenticator


class OperationPolicyTests(unittest.TestCase):
    def test_every_game_send_revalidates_and_revocation_blocks_cached_session(self):
        hub=HubClient()
        session=StrictProxySession()
        session.operation_guard=hub.require_operation
        response=requests.Response();response.status_code=200
        with patch.object(hub,"_get",side_effect=[{"operation_allowed":True},{"operation_allowed":False,"reason":"Teste encerrado"}]) as check, \
             patch.object(requests.Session,"send",return_value=response) as send:
            session.get("https://s1-br.ikariam.gameforge.com/index.php")
            with self.assertRaises(OperationBlocked):
                session.get("https://s1-br.ikariam.gameforge.com/index.php")
            self.assertEqual(send.call_count,1)
            self.assertEqual(check.call_count,2)

    def test_no_hub_connection_or_missing_authorization_never_falls_back_to_game(self):
        hub=HubClient();session=StrictProxySession();session.operation_guard=hub.require_operation
        for value in (requests.ConnectionError("offline"), {}):
            with self.subTest(value=type(value).__name__):
                args={"side_effect":value} if isinstance(value,Exception) else {"return_value":value}
                with patch.object(hub,"_get",**args),patch.object(requests.Session,"send") as send:
                    with self.assertRaises((requests.ConnectionError,OperationBlocked)):
                        session.post("https://s1-br.ikariam.gameforge.com/index.php",data={"action":"buy"})
                    send.assert_not_called()

    def test_game_and_lobby_sessions_install_the_guard(self):
        hub=HubClient()
        client=GameClient("fake-account",hub)
        self.assertEqual(client.session.operation_guard,hub.require_operation)
        session=StrictProxySession()
        LobbyAuthenticator(session,hub,"test")
        self.assertEqual(session.operation_guard,hub.require_operation)

    def test_renewal_works_without_restarting_the_agent(self):
        hub=HubClient()
        with patch.object(hub,"_get",side_effect=[{"operation_allowed":False},{"operation_allowed":True}]):
            with self.assertRaises(OperationBlocked):hub.require_operation()
            hub.require_operation()

if __name__ == "__main__":unittest.main()
