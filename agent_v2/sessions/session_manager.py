"""Session manager: two-layer cache (lobby tokens + game sessions) with per-key locks.

Lobby tokens are cached per Account (email) — shared across all GameAccounts.
Game sessions (cookies) are cached per GameAccount (server) — independent per server.
Locks are per GameAccount so different servers can run in parallel.
"""

import logging
import time
from threading import Lock

logger = logging.getLogger(__name__)

# Lobby tokens are valid ~24h, revalidate after 23h to be safe
LOBBY_TOKEN_MAX_AGE = 23 * 3600

# Game sessions should be revalidated after 5 minutes of inactivity
SESSION_REVALIDATE_AFTER = 300


class LobbyTokenEntry:
    """Cached lobby bearer token for a Gameforge account."""

    __slots__ = ("token", "obtained_at")

    def __init__(self, token: str):
        self.token = token
        self.obtained_at = time.monotonic()

    def is_likely_valid(self, max_age: float = LOBBY_TOKEN_MAX_AGE) -> bool:
        return bool(self.token) and (time.monotonic() - self.obtained_at) < max_age


class SessionEntry:
    """Cached game session (cookies) for a specific GameAccount/server."""

    __slots__ = ("cookies", "metadata", "created_at", "last_used_at")

    def __init__(self, cookies: dict, metadata: dict | None = None):
        self.cookies = cookies
        self.metadata = metadata or {}
        self.created_at = time.monotonic()
        self.last_used_at = time.monotonic()

    def is_valid(self) -> bool:
        return bool(self.cookies)

    def is_stale(self, max_age: float = SESSION_REVALIDATE_AFTER) -> bool:
        return (time.monotonic() - self.last_used_at) > max_age

    def touch(self) -> None:
        """Update last_used_at to extend the freshness window."""
        self.last_used_at = time.monotonic()


class SessionManager:
    """Two-layer session cache with per-key locks.

    Layer 1 — Lobby tokens: keyed by ``account_id`` (the Gameforge email/account).
        Shared across all GameAccounts of the same Account.

    Layer 2 — Game sessions: keyed by ``game_account_id`` (specific server).
        Each GameAccount has its own cookies and can run independently.

    Locks are per ``game_account_id`` so that GameAccounts on different servers
    can execute jobs in parallel (like independent browser tabs).
    For lobby-only operations (e.g. discover_characters), use ``account_id`` as key.
    """

    def __init__(self):
        self._lobby_tokens: dict[str, LobbyTokenEntry] = {}
        self._game_sessions: dict[str, SessionEntry] = {}
        self._locks: dict[str, Lock] = {}
        self._global_lock = Lock()

    def get_lock(self, key: str) -> Lock:
        """Get or create a lock for the given key (game_account_id or account_id)."""
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = Lock()
            return self._locks[key]

    def get_lobby_lock(self, account_id: str) -> Lock:
        """Get or create a lock for lobby auth of the given account."""
        key = f"lobby:{account_id}"
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = Lock()
            return self._locks[key]

    def get_lobby_token(self, account_id: str) -> str | None:
        """Return cached lobby token if still likely valid, else None."""
        entry = self._lobby_tokens.get(account_id)
        if entry and entry.is_likely_valid():
            return entry.token
        return None

    def save_lobby_token(self, account_id: str, token: str) -> None:
        """Cache a lobby token for the given account."""
        self._lobby_tokens[account_id] = LobbyTokenEntry(token)
        logger.debug("Lobby token cached for account %s", account_id)

    def invalidate_lobby_token(self, account_id: str) -> None:
        """Remove cached lobby token."""
        self._lobby_tokens.pop(account_id, None)
        logger.debug("Lobby token invalidated for account %s", account_id)

    def get_game_session(self, game_account_id: str) -> SessionEntry | None:
        """Return cached game session if valid, else None."""
        entry = self._game_sessions.get(game_account_id)
        if entry and entry.is_valid():
            return entry
        return None

    def save_game_session(
        self, game_account_id: str, cookies: dict, metadata: dict | None = None,
    ) -> None:
        """Cache game session cookies for a GameAccount."""
        self._game_sessions[game_account_id] = SessionEntry(cookies, metadata)
        logger.debug("Game session cached for game_account %s", game_account_id)

    def invalidate_game_session(self, game_account_id: str) -> None:
        """Remove cached game session."""
        self._game_sessions.pop(game_account_id, None)
        logger.debug("Game session invalidated for game_account %s", game_account_id)

    def get_session(self, key: str) -> SessionEntry | None:
        """Compat: try game session first, fall back to legacy cache."""
        return self.get_game_session(key)

    def save_session(self, key: str, cookies: dict, metadata: dict | None = None) -> None:
        """Compat: save as game session."""
        self.save_game_session(key, cookies, metadata)

    def invalidate(self, key: str) -> None:
        """Compat: invalidate game session."""
        self.invalidate_game_session(key)
