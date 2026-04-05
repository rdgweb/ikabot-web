"""
Runner: discover_characters — lists available characters in the Gameforge lobby.

Action code: 101

This runner authenticates with the Gameforge lobby (without entering a game
server) and fetches the list of accounts/characters available.  The result
includes the ``lobby_account_id`` values needed by the hub to map
Ikariam game accounts to their Gameforge lobby entries.

The lobby /api/users/me/accounts returns a **list** like::

    [
        {
            "id": 108637,
            "server": {"language": "br", "number": 61},
            "name": "HAVIT",
            "blocked": false,
            ...
        },
        ...
    ]

This runner collects that data and pushes it back to the hub so accounts
can be matched to their ``lobby_account_id``.
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from game_client.constants import USER_AGENTS
from game_client.exceptions import LoginError
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


@register_runner(101)
class DiscoverCharactersRunner(BaseRunner):
    """Discover all characters in the Gameforge lobby for an account.

    Authenticates with the Gameforge lobby using email/password and
    fetches the full list of game accounts (characters) available.
    Does NOT enter any specific game server.

    Inputs (from job):
        email          — Gameforge account email
        password       — Gameforge account password
        lobby_token    — (optional) existing gf-token-production cookie

    If credentials are not in job inputs, fetches them from hub config.

    Result data:
        characters — list of dicts with:
            lobby_account_id  — Gameforge internal account ID (int)
            server_id         — e.g. "s61-br"
            server_language   — e.g. "br"
            server_number     — e.g. 61
            name              — character/player name
            blocked           — whether the account is blocked
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        self.log(jid, "info", "Iniciando descoberta de personagens no lobby")

        try:
            # ── 1. Resolve credentials ──
            creds = self._resolve_credentials(aid, inputs)
            if not creds:
                self.log(jid, "error", "Credenciais não encontradas para a conta")
                return RunnerResult(
                    success=False,
                    data={"error": "missing_credentials"},
                )

            email = creds["email"]
            password = creds["password"]
            raw_token = (
                creds.get("lobby_token", "")
                or inputs.get("lobby_token", "")
                or self.get_lobby_token(aid)
                or ""
            )
            # Strip "gf-token-production=" prefix if present
            existing_token = raw_token.split("=", 1)[-1] if "=" in raw_token else raw_token

            # Deterministic user agent based on email (like ikabot)
            user_agent = USER_AGENTS[sum(ord(c) for c in email) % len(USER_AGENTS)]

            # ── 2. Authenticate with lobby ──
            self.log(jid, "info", f"Autenticando no lobby Gameforge ({email[:3]}***)")


            try:
                auth, lobby_token = self.game_sessions.authenticate_lobby(
                    account_id=aid,
                    email=email,
                    password=password,
                    user_agent=user_agent,
                    hint=existing_token,
                )
            except LoginError as e:
                self.log(jid, "error", f"Falha na autenticação do lobby: {e}")
                return RunnerResult(
                    success=False,
                    data={"error": "lobby_auth_failed", "detail": str(e)},
                )

            self.log(jid, "info", "Autenticação no lobby bem-sucedida")

            # ── 3. Fetch accounts list ──
            self.log(jid, "info", "Buscando lista de personagens...")

            try:
                raw_accounts = auth.fetch_accounts(lobby_token)
                characters = self._normalize_accounts(raw_accounts)
            except Exception as e:
                self.log(jid, "error", f"Falha ao buscar personagens: {e}")
                return RunnerResult(
                    success=False,
                    data={"error": "fetch_characters_failed", "detail": str(e)},
                )

            self.log(
                jid, "info",
                f"Encontrados {len(characters)} personagens no lobby",
            )

            # Log each character for visibility
            for idx, char in enumerate(characters):
                self.log(
                    jid, "info",
                    f"  [{idx}] {char['server_id']} — "
                    f"lobby_account_id={char['lobby_account_id']}"
                    f"{' — ' + char['name'] if char.get('name') else ''}",
                )

            # Emit structured result so the hub signal can sync GameAccounts
            import json
            self.log(jid, "info", f"DISCOVER_RESULT:{json.dumps(characters)}")

            return RunnerResult(
                success=True,
                data={
                    "characters": characters,
                    "lobby_token": lobby_token,
                },
            )

        except Exception as exc:
            logger.exception("DiscoverCharactersRunner failed for account %s", aid)
            self.log(jid, "error", f"Erro inesperado: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

    # ── Private helpers ────────────────────────────────────────────────

    @staticmethod
    def _normalize_accounts(raw_accounts) -> list[dict]:
        """Normalize raw lobby accounts into a consistent character list.

        The API returns either a list or a dict of account objects.
        """
        characters: list[dict] = []

        if isinstance(raw_accounts, list):
            items = raw_accounts
        elif isinstance(raw_accounts, dict):
            items = list(raw_accounts.values())
        else:
            return characters

        for acc in items:
            server = acc.get("server", {})
            lang = server.get("language", "")
            number = server.get("number", 0)
            server_id = f"s{number}-{lang}" if lang and number else ""

            characters.append({
                "lobby_account_id": acc.get("id", ""),
                "server_id": server_id,
                "server_language": lang,
                "server_number": number,
                "name": acc.get("name", ""),
                "blocked": acc.get("blocked", False),
                "account_group": acc.get("accountGroup", ""),
            })

        # Sort by language then server number for consistent ordering
        characters.sort(key=lambda c: (c["server_language"], c["server_number"]))
        return characters

    def _resolve_credentials(self, account_id: str, inputs: dict) -> dict | None:
        """Resolve email + password from inputs or hub config."""
        if inputs.get("email") and inputs.get("password"):
            return {
                "email": inputs["email"],
                "password": inputs["password"],
                "lobby_token": inputs.get("lobby_token", ""),
            }

        try:
            config = self.hub.get_config()
            accounts = config.get("accounts", [])

            # Try exact match first
            for acc in accounts:
                if acc.get("id") == account_id:
                    if acc.get("email") and acc.get("password"):
                        return {
                            "email": acc["email"],
                            "password": acc["password"],
                            "lobby_token": acc.get("gf_token", "") or acc.get("lobby_token", ""),
                        }

            # Fall back to any account with credentials
            for acc in accounts:
                if acc.get("email") and acc.get("password"):
                    return {
                        "email": acc["email"],
                        "password": acc["password"],
                        "lobby_token": acc.get("gf_token", "") or acc.get("lobby_token", ""),
                    }

        except Exception as e:
            logger.warning("Failed to fetch config from hub: %s", e)

        return None
