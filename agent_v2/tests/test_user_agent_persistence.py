"""N-38: the same User-Agent must be used for a whole GameAccount session — never
re-randomized between GameClient.__init__/IkariamAuth, between a fresh login and the
next cache-reuse in the same process, or after an agent restart (hub-persisted)."""

import unittest
from unittest.mock import MagicMock

from game_client.client import GameClient
from game_client.constants import USER_AGENTS
from sessions.game_session_service import GameSessionService
from sessions.session_manager import SessionManager


class GameClientUserAgentTests(unittest.TestCase):
    def test_uses_the_given_user_agent_verbatim_everywhere(self):
        given = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"

        client = GameClient(account_id="acc-1", hub=MagicMock(), proxy_url="socks5://p:1", user_agent=given)

        self.assertEqual(client.user_agent, given)
        self.assertEqual(client.session.headers["User-Agent"], given)
        self.assertEqual(client.auth._user_agent, given)  # IkariamAuth must not pick its own
        self.assertEqual(client.auth.lobby.user_agent, given)  # ...nor should LobbyAuthenticator

    def test_picks_one_from_the_pool_when_none_given(self):
        client = GameClient(account_id="acc-1", hub=MagicMock(), proxy_url="socks5://p:1")

        self.assertIn(client.user_agent, USER_AGENTS)
        self.assertEqual(client.session.headers["User-Agent"], client.user_agent)
        self.assertEqual(client.auth._user_agent, client.user_agent)

    def test_two_clients_without_a_persisted_agent_can_still_differ(self):
        # Not a hard guarantee (random.choice can repeat), but pinning both to the same
        # fixed seed-independent pool member would defeat rotation across *different*
        # accounts that have no persisted UA yet. Sanity: at least it's from the pool.
        for _ in range(5):
            client = GameClient(account_id="acc-1", hub=MagicMock())
            self.assertIn(client.user_agent, USER_AGENTS)


class GameSessionServiceUserAgentTests(unittest.TestCase):
    def setUp(self):
        self.sessions = SessionManager()
        self.hub = MagicMock()
        self.service = GameSessionService(self.sessions, self.hub, proxy_url="socks5://fallback:1")

    def test_save_then_get_game_client_restores_the_same_user_agent(self):
        original = GameClient(account_id="ga-1", hub=self.hub, user_agent=USER_AGENTS[0])
        original.restore_cookies({"session": "abc"})
        self.service.save_game_client("ga-1", original)

        restored = self.service.get_game_client("ga-1")

        self.assertEqual(restored.user_agent, USER_AGENTS[0])

    def test_fresh_login_reuses_the_hub_persisted_user_agent_not_a_new_random_one(self):
        persisted = USER_AGENTS[1]
        creds = {"server": "s61-br", "email": "a@b.com", "password": "x", "user_agent": persisted}

        def fake_build(*, account_id, proxy_url="", user_agent=""):
            client = MagicMock()
            client.user_agent = user_agent
            client.auth.lobby = MagicMock()
            client.login = MagicMock()
            client.lobby_token = ""
            return client

        self.service._build_game_client = fake_build
        self.service.acquire_lobby_token = MagicMock(return_value="tok")

        client = self.service._get_or_login_game_client_with_proxy(
            account_id="acc-1", game_account_id="ga-2", creds=creds, allow_cached=False,
        )

        self.assertEqual(client.user_agent, persisted)

    def test_fresh_login_without_a_persisted_user_agent_falls_back_to_blank_for_the_client_to_pick(self):
        """No hub value yet (brand new account): the runner passes blank through, and
        GameClient itself does the one-time random pick — see GameClientUserAgentTests."""
        creds = {"server": "s61-br", "email": "a@b.com", "password": "x", "user_agent": ""}
        seen_user_agent = {}

        def fake_build(*, account_id, proxy_url="", user_agent=""):
            seen_user_agent["value"] = user_agent
            client = MagicMock()
            client.user_agent = user_agent
            client.auth.lobby = MagicMock()
            client.login = MagicMock()
            client.lobby_token = ""
            return client

        self.service._build_game_client = fake_build
        self.service.acquire_lobby_token = MagicMock(return_value="tok")

        self.service._get_or_login_game_client_with_proxy(
            account_id="acc-1", game_account_id="ga-3", creds=creds, allow_cached=False,
        )

        self.assertEqual(seen_user_agent["value"], "")


if __name__ == "__main__":
    unittest.main()
