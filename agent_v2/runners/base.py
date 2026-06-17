"""Base classes for agent runners."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sessions import GameSessionService
from sessions.game_session_service import LoginCooldownActive
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

    _TRANSIENT_ERRORS = (
        "timed out", "timeout", "connectionpool", "connection reset",
        "connection refused", "remotedisconnected", "broken pipe",
        "eof occurred", "network", "ssl", "max retries exceeded",
    )

    def network_error_result(self, job_id: str, exc: Exception,
                             reschedule_seconds: int = 300) -> "RunnerResult":
        """Return a reschedulable result for transient network errors."""
        exc_str = str(exc)
        self.log(job_id, "warn",
            f"Erro de rede transitório ({type(exc).__name__}): {exc_str[:120]}. "
            f"Reagendando em {reschedule_seconds//60}min.")
        return RunnerResult(success=False, reschedule_seconds=reschedule_seconds,
                            data={"error": exc_str, "retryable": True})

    @staticmethod
    def is_network_error(exc: Exception) -> bool:
        exc_str = str(exc).lower()
        return any(k in exc_str for k in BaseRunner._TRANSIENT_ERRORS)

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

    def is_snapshot_stale(self, snapshot: dict) -> bool:
        """True se o snapshot COMPLETO (check_status) for mais antigo que snapshot_stale_seconds.

        Usa full_snapshot_updated_at quando disponível — esse campo só é escrito por
        check_status completos, não por patches parciais (recursos, buildings, etc.).
        Fallback para updated_at se o campo não existir (snapshots antigos).
        """
        raw = snapshot.get("full_snapshot_updated_at") or snapshot.get("updated_at")
        if raw is None:
            return True
        try:
            if isinstance(raw, str):
                ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            elif isinstance(raw, datetime):
                ts = raw
            else:
                return True
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds() > self.get_snapshot_stale_seconds()
        except Exception:
            return True

    def get_building_options_stale_seconds(self) -> int:
        return self.get_system_setting_int("building_options_stale_seconds", 6 * 60 * 60)

    def get_snapshot(self, job_id: str, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Falha ao buscar snapshot atual: {exc}")
            return None

    def ensure_status_refresh(
        self,
        job_id: str,
        *,
        game_account_id: str | None,
        message: str = "Check status solicitado para atualizar snapshot",
        delay_seconds: int = 0,
        spawn_game_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Spawn check_status only when login cooldown allows it.

        If the account is in login cooldown, raise LoginCooldownActive so the
        executor reschedules the current job instead of creating a doomed
        check_status job.
        """
        self._raise_if_login_cooldown(job_id, game_account_id=game_account_id)
        spawned = self.hub.spawn_job(
            job_id,
            action_code=100,
            inputs={},
            delay_seconds=delay_seconds,
            game_account_id=spawn_game_account_id,
        )
        self.log(job_id, "info", message)
        return spawned

    def _raise_if_login_cooldown(self, job_id: str, *, game_account_id: str | None) -> None:
        if not game_account_id:
            return
        try:
            cooldown = self.hub.get_login_cooldown(game_account_id=game_account_id)
        except Exception:
            self.log(job_id, "warn", "Falha ao consultar cooldown de login antes do check_status; seguindo.")
            return
        blocked_until = self._parse_hub_datetime(cooldown.get("blocked_until") or "")
        if not blocked_until:
            return
        now = datetime.now(timezone.utc)
        if blocked_until <= now:
            return
        wait_seconds = int((blocked_until - now).total_seconds())
        jitter_seconds = random.randint(0, 15 * 60)
        delay_seconds = wait_seconds + jitter_seconds
        self.log(
            job_id,
            "warn",
            f"Check status nao criado: login em cooldown ate {blocked_until.isoformat()} "
            f"(backoff={int(cooldown.get('backoff_hours') or 0)}h). "
            f"Reagendando job atual em {delay_seconds}s.",
        )
        raise LoginCooldownActive(
            delay_seconds=delay_seconds,
            blocked_until=blocked_until.isoformat(),
            backoff_hours=int(cooldown.get("backoff_hours") or 0),
            reason=str(cooldown.get("reason") or ""),
        )

    @staticmethod
    def _parse_hub_datetime(raw: Any) -> datetime | None:
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


class GenericRunner(BaseRunner):
    """Fallback runner used when no action-specific runner exists."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        self.log(
            job["job_id"],
            "warning",
            f"No runner implemented for action_code={job['action_code']}",
        )
        return RunnerResult(success=False, data={"reason": "unimplemented_action"})
