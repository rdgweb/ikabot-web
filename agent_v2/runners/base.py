"""Base classes for agent runners."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sessions import GameSessionService
from core.config import settings

if TYPE_CHECKING:
    from core.hub_client import HubClient
    from sessions import SessionManager

logger = logging.getLogger(__name__)
_LOG_LEVEL_ALIASES = {
    "warning": "warn",
}


@dataclass
class RunnerResult:
    """Standard return value from runner execution."""

    success: bool = True
    reschedule_seconds: int = 0
    reschedule_inputs: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)


class BaseRunner:
    """Abstract base class for runners."""

    def __init__(self, hub: HubClient, sessions: SessionManager, proxy_url: str = "") -> None:
        self.hub = hub
        self.sessions = sessions
        self.proxy_url = proxy_url
        self.game_sessions = GameSessionService(sessions, hub, proxy_url)
        self._config_cache: dict[str, Any] | None = None
        self._config_cache_at = 0.0

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        raise NotImplementedError

    def log(self, job_id: str, level: str, msg: str) -> None:
        """Send a structured log line to the hub."""
        normalized_level = _LOG_LEVEL_ALIASES.get(str(level or "").strip().lower(), str(level or "info").strip().lower() or "info")
        self.hub.report_log(job_id, normalized_level, msg)

    def checkpoint(self, job_id: str, *, phase: str, metadata: dict[str, Any] | None = None) -> None:
        self.hub.report_status(
            job_id,
            status="running",
            agent=settings.agent_name,
            progress={
                "phase": str(phase or "").strip() or "unknown",
                "updated_at": int(time.time()),
                "metadata": metadata or {},
            },
        )

    def get_lobby_token(self, account_id: str) -> str | None:
        return self.game_sessions.get_lobby_token(account_id)

    def save_lobby_token(self, account_id: str, token: str) -> None:
        self.game_sessions.save_lobby_token(account_id, token)

    def acquire_lobby_token(self, account_id: str, auth, email: str, password: str, hint: str = "") -> str:
        return self.game_sessions.acquire_lobby_token(account_id, auth, email, password, hint)

    def get_game_client(self, game_account_id: str):
        return self.game_sessions.get_game_client(game_account_id)

    def save_game_client(self, game_account_id: str, client) -> None:
        self.game_sessions.save_game_client(game_account_id, client)

    def get_or_login_game_client(
        self,
        job_id: str,
        account_id: str,
        game_account_id: str | None,
        creds: dict[str, Any],
        *,
        allow_cached: bool = True,
    ):
        return self.game_sessions.get_or_login_game_client(
            account_id=account_id,
            game_account_id=game_account_id,
            creds=creds,
            log=lambda level, message: self.log(job_id, level, message),
            allow_cached=allow_cached,
        )

    def get_game_session(self, account_id: str):
        """Deprecated compatibility alias."""
        return self.get_game_client(account_id)

    def save_game_session(self, account_id: str, client) -> None:
        """Deprecated compatibility alias."""
        self.save_game_client(account_id, client)

    def resolve_credentials(
        self, account_id: str, inputs: dict, game_account_id: str | None = None
    ) -> dict | None:
        """Resolve server + email + password from job inputs or hub config."""
        if inputs.get("server") and inputs.get("email") and inputs.get("password"):
            return {
                "server": inputs["server"],
                "email": inputs["email"],
                "password": inputs["password"],
                "gf_token": inputs.get("gf_token", "") or inputs.get("lobby_token", ""),
                "lobby_account_id": None,
            }

        try:
            config = self.hub.get_config()
            accounts = config.get("accounts", [])
            for acc in accounts:
                if acc.get("id") != account_id:
                    continue

                email = acc.get("email", "")
                password = acc.get("password", "")
                gf_token = acc.get("gf_token", "")

                if not email or not password:
                    continue

                server = ""
                lobby_account_id = None
                if game_account_id:
                    for ga in acc.get("game_accounts", []):
                        if ga.get("id") == game_account_id:
                            server = ga.get("server_id", "")
                            lobby_account_id = ga.get("lobby_account_id")
                            break

                if not server:
                    for ga in acc.get("game_accounts", []):
                        if ga.get("active") and ga.get("server_id"):
                            server = ga["server_id"]
                            lobby_account_id = ga.get("lobby_account_id")
                            break

                if server:
                    return {
                        "server": server,
                        "lobby_account_id": lobby_account_id,
                        "email": email,
                        "password": password,
                        "gf_token": gf_token,
                    }
        except Exception as e:
            logger.warning("Failed to fetch config from hub: %s", e)

        return None

    def get_agent_config(self, *, max_age_seconds: int = 30) -> dict[str, Any]:
        now = time.monotonic()
        if self._config_cache and (now - self._config_cache_at) <= max_age_seconds:
            return self._config_cache
        config = self.hub.get_config()
        self._config_cache = config
        self._config_cache_at = now
        return config

    def get_system_setting_int(self, key: str, default: int) -> int:
        try:
            config = self.get_agent_config()
            raw = ((config.get("system_settings") or {}).get(key, default))
            return int(raw)
        except Exception:
            return int(default)

    def get_snapshot_stale_seconds(self) -> int:
        return self.get_system_setting_int("snapshot_stale_seconds", 2 * 60 * 60)

    def get_building_options_stale_seconds(self) -> int:
        return self.get_system_setting_int("building_options_stale_seconds", 6 * 60 * 60)

    def get_snapshot(self, job_id: str, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Falha ao buscar snapshot atual: {exc}")
            return None


class GenericRunner(BaseRunner):
    """Fallback runner used when no action-specific runner exists."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        self.log(
            job["job_id"],
            "warning",
            f"No runner implemented for action_code={job['action_code']}",
        )
        return RunnerResult(success=False, data={"reason": "unimplemented_action"})
