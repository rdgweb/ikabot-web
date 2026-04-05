"""Ikariam authentication — full login flow via Gameforge lobby.

Delegates lobby authentication to LobbyAuthenticator and adds game-server
session management (validation, refresh, cookie handling).

All HTTP requests go through StrictProxySession (game domains are proxied).
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from .lobby import LobbyAuthenticator
from ..constants import USER_AGENTS
from ..exceptions import LoginError, SessionExpiredError

if TYPE_CHECKING:
    from core.hub_client import HubClient
    from core.proxy import StrictProxySession

logger = logging.getLogger(__name__)


class IkariamAuth:
    """Handles the full Ikariam login flow via Gameforge lobby.

    Uses LobbyAuthenticator for lobby auth, then manages the game-server
    session (loginLink → follow redirect → game cookies).
    """

    def __init__(self, session: StrictProxySession, hub: HubClient):
        self.session = session
        self.hub = hub
        self._user_agent = random.choice(USER_AGENTS)
        self._lobby = LobbyAuthenticator(session, hub, self._user_agent)
        self._lobby_token: str = ""
        self.account_info: dict = {}  # Populated after login

    def login(
        self,
        email: str,
        password: str,
        server_id: str,
        existing_token: str = "",
        lobby_account_id: int | None = None,
    ) -> dict:
        """Execute the full login flow and return cookies for caching.

        Args:
            email: Gameforge account email.
            password: Gameforge account password.
            server_id: Target game server (e.g. "s61-br").
            existing_token: Optional existing lobby token to try first.
            lobby_account_id: Optional Gameforge account ID to disambiguate
                multiple accounts on the same server.

        Returns:
            Dictionary of session cookies to cache for reuse.

        Raises:
            LoginError: If any step of the authentication fails.
        """
        logger.info("Starting login for server %s", server_id)

        # Step 1: Lobby authentication (delegated)
        lobby_token = self._lobby.authenticate(email, password, existing_token)
        self._lobby_token = lobby_token

        # Step 2: Find the right account for this server
        accounts = self._lobby.fetch_accounts(lobby_token)
        account = self._find_account(accounts, server_id, lobby_account_id)
        if not account:
            raise LoginError(f"No account found for server {server_id}")

        # Store account metadata for callers
        srv = account.get("server", {})
        self.account_info = {
            "player_name": account.get("name", "?"),
            "server_lang": srv.get("language", ""),
            "server_number": str(srv.get("number", "")),
            "account_id_gf": account.get("id"),
        }

        # Step 3: Get loginLink and follow to game server
        login_url = self._lobby.get_login_link(lobby_token, account)

        self._lobby.follow_login_link(
            login_url,
            self.account_info["server_lang"],
            self.account_info["server_number"],
        )

        # Step 4: Collect cookies
        cookies = dict(self.session.cookies)
        if not cookies:
            raise LoginError("No session cookies obtained from game server")

        logger.info("Login successful for server %s (%d cookies)", server_id, len(cookies))
        return cookies

    @property
    def lobby(self) -> LobbyAuthenticator:
        """Expose the underlying LobbyAuthenticator for token management."""
        return self._lobby

    @property
    def lobby_token(self) -> str:
        """Return the lobby bearer token obtained during login."""
        return self._lobby_token

    def refresh_session(self, cookies: dict, server_url: str) -> dict:
        """Try to refresh an existing session without full re-login.

        Args:
            cookies: Previously cached session cookies.
            server_url: Base URL of the game server.

        Returns:
            Updated cookies dict if refresh succeeded.

        Raises:
            SessionExpiredError: If the session cannot be refreshed.
        """
        self._apply_cookies(cookies)

        if not self.is_session_valid(cookies, server_url):
            raise SessionExpiredError("Session cookies are expired, full re-login required")

        # Session is valid — return potentially updated cookies
        return dict(self.session.cookies)

    def is_session_valid(self, cookies: dict, server_url: str) -> bool:
        """Check if session cookies are still valid.

        Makes a lightweight GET to the game server and checks the response.

        Args:
            cookies: Session cookies to validate.
            server_url: Base URL of the game server (e.g. https://s61-br.ikariam...).

        Returns:
            True if the session is still active.
        """
        if not cookies:
            return False

        self._apply_cookies(cookies)

        try:
            resp = self.session.get(
                server_url,
                timeout=15,
                allow_redirects=False,
            )
        except Exception:
            return False

        # 302/303 redirect = session expired (redirects to login)
        if resp.status_code in (302, 303):
            return False

        # 200 with game content = valid
        if resp.status_code == 200:
            text = resp.text
            if "actionRequest" in text and "loginForm" not in text:
                return True

        return False

    # ── Private helpers ──

    @staticmethod
    def _find_account(
        accounts,
        server_id: str,
        lobby_account_id: int | None = None,
    ) -> dict | None:
        """Find account matching server_id in the accounts list.

        When multiple avatars exist on the same server, ``lobby_account_id``
        disambiguates which one should be opened. If it is missing, returns the
        first matching non-blocked account.
        """
        if isinstance(accounts, dict):
            accounts = list(accounts.values())

        fallback_match = None
        for acc in accounts:
            if acc.get("blocked"):
                continue
            srv = acc.get("server", {})
            lang = srv.get("language", "")
            number = srv.get("number", 0)
            sid = f"s{number}-{lang}"

            server_matches = sid == server_id or lang == server_id
            if not server_matches:
                continue

            if fallback_match is None:
                fallback_match = acc

            if lobby_account_id is not None and acc.get("id") == lobby_account_id:
                return acc

        return fallback_match

    def _apply_cookies(self, cookies: dict) -> None:
        """Load cached cookies into the session."""
        self.session.cookies.update(cookies)
