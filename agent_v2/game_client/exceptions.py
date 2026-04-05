"""Custom exceptions for the Ikariam game client."""


class GameClientError(Exception):
    """Base exception for all game client errors."""
    pass


class LoginError(GameClientError):
    """Authentication or login flow failure."""
    pass


class SessionExpiredError(GameClientError):
    """Game session cookies are no longer valid."""
    pass


class CaptchaRequiredError(GameClientError):
    """The game server is requesting captcha verification."""

    def __init__(self, message: str = "Captcha required", captcha_data: dict | None = None):
        super().__init__(message)
        self.captcha_data = captcha_data or {}


class ProxyError(GameClientError):
    """Proxy-related failure (connection, auth, timeout)."""
    pass


class ActionError(GameClientError):
    """A game action (build, train, send, etc.) was rejected by the server."""

    def __init__(self, message: str, action: str = "", server_errors: list[str] | None = None):
        super().__init__(message)
        self.action = action
        self.server_errors = server_errors or []


class MaintenanceError(GameClientError):
    """Game server is under maintenance."""
    pass


class RateLimitError(GameClientError):
    """Too many requests — game server throttling."""
    pass
