"""Shared policy for lobby-token reuse and game-session recovery."""

from __future__ import annotations

import requests

from core.proxy import StrictProxySession
from game_client.auth.lobby import LobbyAuthenticator
from game_client.client import GameClient

from .session_manager import SessionManager


class GameSessionService:
    """Owns the policy for acquiring valid lobby and game sessions."""

    def __init__(self, sessions: SessionManager, hub, proxy_url: str = "") -> None:
        self.sessions = sessions
        self.hub = hub
        self.proxy_url = proxy_url

    def get_lobby_token(self, account_id: str) -> str | None:
        return self.sessions.get_lobby_token(account_id)

    def save_lobby_token(self, account_id: str, token: str) -> None:
        self.sessions.save_lobby_token(account_id, token)

    def invalidate_lobby_token(self, account_id: str) -> None:
        self.sessions.invalidate_lobby_token(account_id)

    def acquire_lobby_token(
        self,
        account_id: str,
        auth,
        email: str,
        password: str,
        hint: str = "",
    ) -> str:
        """Get or create a lobby token, serializing concurrent logins per Account."""
        lock = self.sessions.get_lobby_lock(account_id)
        with lock:
            cached = self.sessions.get_lobby_token(account_id)
            if cached:
                if auth.validate_token(cached):
                    cookie_obj = requests.cookies.create_cookie(
                        domain=".gameforge.com",
                        name="gf-token-production",
                        value=cached,
                    )
                    auth.session.cookies.set_cookie(cookie_obj)
                    return cached
                self.sessions.invalidate_lobby_token(account_id)

            token = auth.authenticate(email, password, hint)
            self.sessions.save_lobby_token(account_id, token)
            return token

    def authenticate_lobby(
        self,
        *,
        account_id: str,
        email: str,
        password: str,
        user_agent: str,
        hint: str = "",
    ) -> tuple[LobbyAuthenticator, str]:
        """Build a lobby authenticator and return it with a valid lobby token."""
        session = StrictProxySession(self.proxy_url)
        auth = LobbyAuthenticator(session, self.hub, user_agent)
        token = self.acquire_lobby_token(account_id, auth, email, password, hint)
        return auth, token

    def get_game_client(self, game_account_id: str):
        """Return a GameClient for ``game_account_id``, restoring cached cookies."""
        cached = self.sessions.get_game_session(game_account_id)
        client = GameClient(account_id=game_account_id, hub=self.hub, proxy_url=self.proxy_url)
        if cached and cached.is_valid():
            client.restore_cookies(cached.cookies)
        return client

    def save_game_client(self, game_account_id: str, client) -> None:
        """Persist a GameClient cookie jar into the session cache."""
        self.sessions.save_game_session(game_account_id, client.export_cookies())

    def get_or_login_game_client(
        self,
        *,
        account_id: str,
        game_account_id: str | None,
        creds: dict,
        log=None,
        allow_cached: bool = True,
    ):
        """Return a ready GameClient, reopening only the server session when needed."""
        server_id = creds["server"]
        email = creds["email"]
        password = creds["password"]
        lobby_account_id = creds.get("lobby_account_id")

        if allow_cached and game_account_id:
            cached = self.sessions.get_game_session(game_account_id)
            if cached and cached.is_valid() and not cached.is_stale():
                client = GameClient(
                    account_id=game_account_id,
                    hub=self.hub,
                    proxy_url=self.proxy_url,
                )
                if client.is_session_valid(server_id, cached.cookies):
                    if log:
                        log("info", "Reutilizando sessão cached")
                    client.connect(server_id, cookies=cached.cookies)
                    cached.touch()
                    return client

                if log:
                    log("warn", "Sessão do servidor expirou; reabrindo sem refazer lobby")
                self.sessions.invalidate_game_session(game_account_id)

        if log:
            log("info", f"Login: {server_id} | {email[:3]}***")

        client = GameClient(
            account_id=game_account_id or account_id,
            hub=self.hub,
            proxy_url=self.proxy_url,
        )

        raw_token = creds.get("gf_token", "") or self.get_lobby_token(account_id) or ""
        hint = raw_token.split("=", 1)[-1] if "=" in raw_token else raw_token
        lobby_token = self.acquire_lobby_token(account_id, client.auth.lobby, email, password, hint)

        client.login(
            email,
            password,
            server_id,
            existing_token=lobby_token,
            lobby_account_id=lobby_account_id,
        )

        if log:
            log("info", "Login OK")

        new_token = client.lobby_token
        if new_token:
            self.save_lobby_token(account_id, new_token)
        if game_account_id:
            self.save_game_client(game_account_id, client)

        return client
