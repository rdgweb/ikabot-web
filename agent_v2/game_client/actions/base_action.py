"""Base class for all Ikariam game actions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..constants import ActionID, GAME_AJAX_HEADERS
from ..exceptions import ActionError, CaptchaRequiredError, SessionExpiredError

if TYPE_CHECKING:
    from ..client import GameClient

logger = logging.getLogger(__name__)


class BaseAction:
    """Abstract base for game actions that communicate via AJAX.

    All concrete actions inherit from this and implement execute().
    Common AJAX request logic lives here.
    """

    def __init__(self, client: GameClient):
        self.client = client

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the action. Must be overridden by subclasses.

        Returns:
            Parsed AJAX response dictionary.

        Raises:
            NotImplementedError: If subclass does not override.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement execute()")

    def _ajax_request(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send an AJAX request to the game server.

        This is the core method for all game interactions. It adds the
        required actionRequest token and handles common error cases.

        Args:
            action: The AJAX action string (see ActionID constants).
            params: Additional parameters for the action.

        Returns:
            Parsed response dictionary from AjaxResponseParser.

        Raises:
            ActionError: If the server rejects the action.
            SessionExpiredError: If the session has expired.
            CaptchaRequiredError: If a captcha is triggered.
        """
        # Ensure we have a valid actionRequest token
        if not self.client._action_request:
            raise ActionError(
                "No actionRequest token available — fetch a page first",
                action=action,
            )

        # Build the full parameter set
        # ActionID format: "ScreenName&function=funcName" → split into separate params
        if "&function=" in action:
            action_name, _, function_name = action.partition("&function=")
            full_params = {
                "action": action_name,
                "function": function_name,
            }
        else:
            full_params = {"action": action}

        full_params["actionRequest"] = self.client._action_request
        full_params["ajax"] = "1"
        full_params.update(params)

        logger.debug(f"AJAX request: action={full_params.get('action')}, function={full_params.get('function', '-')}")

        # Delegate to the client's internal _ajax method
        return self.client._ajax(action, full_params)
