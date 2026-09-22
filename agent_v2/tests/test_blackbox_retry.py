"""N-39: blackbox token failures must retry a few times, then fail loudly (LoginError
with an explicit cause) instead of silently continuing the login without one."""

import unittest
from unittest.mock import MagicMock, patch

from game_client.auth.lobby import LobbyAuthenticator
from game_client.exceptions import LoginError


class BlackboxRetryTests(unittest.TestCase):
    def setUp(self):
        self.hub = MagicMock()
        self.auth = LobbyAuthenticator(session=MagicMock(), hub=self.hub, user_agent="UA/1.0")

    @patch("game_client.auth.lobby.time.sleep")
    def test_succeeds_immediately_without_sleeping(self, sleep):
        self.hub.get_blackbox_token.return_value = "tok-123"

        token = self.auth._get_blackbox()

        self.assertEqual(token, "tok-123")
        self.hub.get_blackbox_token.assert_called_once_with("UA/1.0")
        sleep.assert_not_called()

    @patch("game_client.auth.lobby.time.sleep")
    def test_retries_a_transient_failure_and_then_succeeds(self, sleep):
        self.hub.get_blackbox_token.side_effect = [
            ConnectionError("ikabotapi unreachable"),
            "tok-456",
        ]

        token = self.auth._get_blackbox()

        self.assertEqual(token, "tok-456")
        self.assertEqual(self.hub.get_blackbox_token.call_count, 2)
        sleep.assert_called_once()

    @patch("game_client.auth.lobby.time.sleep")
    def test_empty_token_counts_as_a_failure_and_is_retried(self, sleep):
        self.hub.get_blackbox_token.side_effect = ["", "tok-789"]

        token = self.auth._get_blackbox()

        self.assertEqual(token, "tok-789")
        self.assertEqual(self.hub.get_blackbox_token.call_count, 2)

    @patch("game_client.auth.lobby.time.sleep")
    def test_gives_up_after_max_attempts_with_an_explicit_cause(self, sleep):
        self.hub.get_blackbox_token.side_effect = ConnectionError("ikabotapi unreachable")

        with self.assertRaises(LoginError) as ctx:
            self.auth._get_blackbox()

        self.assertEqual(self.hub.get_blackbox_token.call_count, LobbyAuthenticator._BLACKBOX_MAX_ATTEMPTS)
        message = str(ctx.exception)
        self.assertIn("ikabotapi", message)
        self.assertIn(str(LobbyAuthenticator._BLACKBOX_MAX_ATTEMPTS), message)
        self.assertIn("ikabotapi unreachable", message)  # last underlying error preserved
        # One sleep between each pair of attempts, none after the final one.
        self.assertEqual(sleep.call_count, LobbyAuthenticator._BLACKBOX_MAX_ATTEMPTS - 1)

    @patch("game_client.auth.lobby.time.sleep")
    def test_authenticate_surfaces_the_blackbox_failure_instead_of_logging_in_without_it(self, sleep):
        """No fallback to an empty-blackbox login attempt — authenticate() must fail
        with the same explicit LoginError, not proceed to POST credentials."""
        self.hub.get_blackbox_token.side_effect = ConnectionError("ikabotapi unreachable")
        self.auth._fetch_game_ids = MagicMock(return_value=("env-1", "game-1"))
        self.auth._prepare_cookies = MagicMock()
        self.auth._post_credentials = MagicMock(side_effect=AssertionError("must not reach credentials POST"))

        with self.assertRaises(LoginError) as ctx:
            self.auth.authenticate("a@b.com", "pw")

        self.assertIn("Blackbox indisponivel", str(ctx.exception))
        self.auth._post_credentials.assert_not_called()


if __name__ == "__main__":
    unittest.main()
