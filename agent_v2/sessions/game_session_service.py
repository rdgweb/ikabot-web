"""Shared policy for lobby-token reuse and game-session recovery."""

from __future__ import annotations

import random
from datetime import datetime, timezone

import requests

from core.proxy import StrictProxySession
from game_client.auth.lobby import LobbyAuthenticator
from game_client.client import GameClient
from game_client.exceptions import LoginError

from .session_manager import SessionManager


class LoginCooldownActive(BaseException):
    """Control-flow exception used to reschedule jobs when login is cooldown-blocked."""

    def __init__(
        self,
        *,
        delay_seconds: int,
        blocked_until: str = "",
        backoff_hours: int = 0,
        reason: str = "",
    ) -> None:
        self.delay_seconds = max(60, int(delay_seconds or 60))
        self.blocked_until = blocked_until
        self.backoff_hours = int(backoff_hours or 0)
        self.reason = str(reason or "").strip()
        super().__init__(self.__str__())

    def __str__(self) -> str:
        parts = [f"Login temporariamente bloqueado; reagendando em {self.delay_seconds}s"]
        if self.backoff_hours:
            parts.append(f"backoff={self.backoff_hours}h")
        if self.blocked_until:
            parts.append(f"ate={self.blocked_until}")
        if self.reason:
            parts.append(f"motivo={self.reason}")
        return " | ".join(parts)


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

    @staticmethod
    def _parse_hub_dt(raw: str) -> datetime | None:
        try:
            dt = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _check_login_cooldown(self, *, game_account_id: str, log=None) -> None:
        try:
            cooldown = self.hub.get_login_cooldown(game_account_id=game_account_id)
        except Exception:
            if log:
                log("warn", "Falha ao consultar cooldown de login; seguindo com tentativa normal.")
            return
        blocked_until = self._parse_hub_dt(cooldown.get("blocked_until") or "")
        if not blocked_until:
            return
        now = datetime.now(timezone.utc)
        if blocked_until <= now:
            return
        wait_seconds = int((blocked_until - now).total_seconds())
        jitter_seconds = random.randint(0, 15 * 60)
        if log:
            log(
                "warn",
                f"Login em cooldown ate {blocked_until.isoformat()} "
                f"(backoff={int(cooldown.get('backoff_hours') or 0)}h). "
                f"Reagendando em {wait_seconds + jitter_seconds}s.",
            )
        raise LoginCooldownActive(
            delay_seconds=wait_seconds + jitter_seconds,
            blocked_until=blocked_until.isoformat(),
            backoff_hours=int(cooldown.get("backoff_hours") or 0),
            reason=str(cooldown.get("reason") or ""),
        )

    def _record_login_400(self, *, game_account_id: str, exc: Exception, log=None) -> None:
        try:
            cooldown = self.hub.record_login_400(game_account_id=game_account_id, reason=str(exc))
        except Exception:
            cooldown = {"blocked_until": "", "backoff_hours": 1, "reason": str(exc)}
        blocked_until_raw = str(cooldown.get("blocked_until") or "").strip()
        blocked_until = self._parse_hub_dt(blocked_until_raw)
        backoff_hours = int(cooldown.get("backoff_hours") or 1)
        delay_seconds = backoff_hours * 3600 + random.randint(0, 15 * 60)
        if blocked_until:
            now = datetime.now(timezone.utc)
            delay_seconds = max(
                60,
                int((blocked_until - now).total_seconds()) + random.randint(0, 15 * 60),
            )
        if log:
            log(
                "warn",
                f"loginLink 400 detectado; conta em backoff de {backoff_hours}h. "
                f"Reagendando em {delay_seconds}s.",
            )
        raise LoginCooldownActive(
            delay_seconds=delay_seconds,
            blocked_until=blocked_until_raw,
            backoff_hours=backoff_hours,
            reason=str(exc),
        )

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
                        log("info", "Reutilizando sessao cached")
                    client.connect(server_id, cookies=cached.cookies)
                    cached.touch()
                    return client

                if log:
                    log("warn", "Sessao do servidor expirou; reabrindo sem refazer lobby")
                self.sessions.invalidate_game_session(game_account_id)

        if game_account_id:
            self._check_login_cooldown(game_account_id=game_account_id, log=log)

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

        try:
            client.login(
                email,
                password,
                server_id,
                existing_token=lobby_token,
                lobby_account_id=lobby_account_id,
            )
        except LoginError as exc:
            if game_account_id and "loginlink falhou: status=400" in str(exc).lower():
                self._record_login_400(game_account_id=game_account_id, exc=exc, log=log)
            raise

        if log:
            log("info", "Login OK")

        if game_account_id:
            try:
                self.hub.clear_login_cooldown(game_account_id=game_account_id)
            except Exception:
                if log:
                    log("warn", "Falha ao limpar cooldown de login apos sucesso.")

        new_token = client.lobby_token
        if new_token:
            self.save_lobby_token(account_id, new_token)
        if game_account_id:
            self.save_game_client(game_account_id, client)

        return client
