import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sessions.game_session_service import GameSessionService, LoginCooldownActive
from sessions.session_manager import SessionManager
from game_client.exceptions import LoginError


class _FakeLobbyAuth:
    def validate_token(self, _token):
        return False

    def authenticate(self, _email, _password, _hint):
        return "fresh-lobby-token"


class _FakeAuth:
    def __init__(self):
        self.lobby = _FakeLobbyAuth()


class _FakeSession:
    def __init__(self, proxy_url):
        self._proxy_url = proxy_url


class _FakeClient:
    behavior_by_proxy = {}

    def __init__(self, account_id, hub, proxy_url=""):
        self.account_id = account_id
        self.hub = hub
        self.proxy_url = proxy_url
        self.auth = _FakeAuth()
        self.session = _FakeSession(proxy_url)
        self.lobby_token = ""

    def is_session_valid(self, _server_id, _cookies=None, *, raise_on_error=False):
        return False

    def connect(self, _server_id, cookies=None):
        return None

    def login(self, _email, _password, _server_id, existing_token="", lobby_account_id=None):
        action = self.behavior_by_proxy.get(self.proxy_url, "ok")
        if action != "ok":
            raise action
        self.lobby_token = f"token:{self.proxy_url or 'direct'}"
        return True

    def export_cookies(self):
        return {"session": f"cookie:{self.proxy_url or 'direct'}"}


class _HubStub:
    def __init__(self, proxies):
        self._proxies = proxies
        self.clear_calls = []
        self.proxy_cooldown_calls = []
        self.login_400_calls = []

    def reserve_lobby_proxies(self, *, account_id, limit=3):
        return {
            "ok": True,
            "account_id": account_id,
            "proxies": [{"proxy_url": proxy} for proxy in self._proxies[:limit]],
        }

    def get_login_cooldown(self, *, game_account_id):
        return {"ok": True, "game_account_id": game_account_id, "blocked_until": "", "backoff_hours": 0, "reason": ""}

    def clear_login_cooldown(self, *, game_account_id):
        self.clear_calls.append(game_account_id)
        return {"ok": True}

    def record_login_proxy_failure(self, *, game_account_id, reason=""):
        self.proxy_cooldown_calls.append((game_account_id, reason))
        return {"blocked_until": "", "backoff_hours": 1, "reason": reason}

    def record_login_400(self, *, game_account_id, reason=""):
        self.login_400_calls.append((game_account_id, reason))
        return {"blocked_until": "", "backoff_hours": 1, "reason": reason}


class GameSessionServiceProxyFailoverTests(unittest.TestCase):
    def setUp(self):
        self.sessions = SessionManager()

    def _service(self, proxies):
        return GameSessionService(self.sessions, _HubStub(proxies), proxy_url="http://default-proxy")

    def _creds(self):
        return {
            "server": "s61-br",
            "email": "user@example.com",
            "password": "secret",
            "gf_token": "",
            "lobby_account_id": 123,
        }

    def test_login_retries_with_next_reserved_proxy(self):
        service = self._service(["http://p1", "http://p2", "http://p3"])
        _FakeClient.behavior_by_proxy = {
            "http://p1": LoginError(
                "Falha ao buscar configuração do lobby: HTTPSConnectionPool(host='lobby.ikariam.gameforge.com', port=443): Max retries exceeded with url: /config/configuration.js (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 502 Bad Gateway')))"
            ),
            "http://p2": "ok",
        }

        with patch("sessions.game_session_service.GameClient", _FakeClient):
            client = service.get_or_login_game_client(
                account_id="acc-1",
                game_account_id="ga-1",
                creds=self._creds(),
                log=None,
            )

        self.assertEqual(client.proxy_url, "http://p2")
        self.assertEqual(service.hub.proxy_cooldown_calls, [])
        self.assertEqual(service.hub.clear_calls, ["ga-1"])

    def test_login_enters_cooldown_after_third_proxy_failure(self):
        service = self._service(["http://p1", "http://p2", "http://p3"])
        failure = LoginError(
            "Falha ao conectar ao lobby: HTTPSConnectionPool(host='lobby.ikariam.gameforge.com', port=443): Max retries exceeded with url: /api/users/me (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 502 Bad Gateway')))"
        )
        _FakeClient.behavior_by_proxy = {
            "http://p1": failure,
            "http://p2": failure,
            "http://p3": failure,
        }

        with patch("sessions.game_session_service.GameClient", _FakeClient):
            with self.assertRaises(LoginCooldownActive):
                service.get_or_login_game_client(
                    account_id="acc-1",
                    game_account_id="ga-1",
                    creds=self._creds(),
                    log=None,
                )

        self.assertEqual(len(service.hub.proxy_cooldown_calls), 1)
        self.assertEqual(service.hub.login_400_calls, [])


if __name__ == "__main__":
    unittest.main()
