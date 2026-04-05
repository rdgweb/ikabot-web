"""Ikariam game HTTP client — facade for all game server interactions."""

from .client import GameClient
from .exceptions import (
    ActionError,
    CaptchaRequiredError,
    GameClientError,
    LoginError,
    MaintenanceError,
    ProxyError,
    RateLimitError,
    SessionExpiredError,
)

__all__ = [
    "GameClient",
    "GameClientError",
    "LoginError",
    "SessionExpiredError",
    "CaptchaRequiredError",
    "ProxyError",
    "ActionError",
    "MaintenanceError",
    "RateLimitError",
]
