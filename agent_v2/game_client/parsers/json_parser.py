"""AJAX JSON response parser for Ikariam.

Ikariam AJAX responses use a custom JSON format: a list of "change" objects,
each describing a DOM update, redirect, or other client-side action.

Typical response structure:
    [
        {"type": 0, "data": [...]},       # background data
        {"type": 1, "data": [...]},       # DOM updates (changeView)
        {"type": 5, "data": "..."},       # redirect
        {"type": 10, "data": [...]},      # errors
        ...
    ]
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..exceptions import ActionError, MaintenanceError, RateLimitError

logger = logging.getLogger(__name__)

# Known AJAX response type codes
RESPONSE_TYPE_BACKGROUND = 0
RESPONSE_TYPE_CHANGE_VIEW = 1
RESPONSE_TYPE_UPDATE_TEMPLATE = 2
RESPONSE_TYPE_LOAD_JS = 3
RESPONSE_TYPE_NOTIFICATION = 4
RESPONSE_TYPE_REDIRECT = 5
RESPONSE_TYPE_UPDATE_GLOBAL = 6
RESPONSE_TYPE_ERROR = 10
RESPONSE_TYPE_CAPTCHA = 11


class AjaxResponseParser:
    """Parses Ikariam AJAX JSON responses into usable data."""

    def parse_response(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        """Parse a full AJAX response into categorized components.

        Args:
            data: Raw parsed JSON list from an AJAX response.

        Returns:
            Dictionary with parsed components:
                - "updates": list of DOM update directives
                - "background": background data
                - "redirect": redirect URL if any
                - "errors": list of error messages
                - "notifications": list of notification strings
                - "raw": the original data

        Raises:
            ActionError: If the response contains critical errors.
            MaintenanceError: If the server reports maintenance.
            RateLimitError: If the server indicates rate limiting.
        """
        result: dict[str, Any] = {
            "updates": [],
            "background": [],
            "redirect": None,
            "errors": [],
            "notifications": [],
            "raw": data,
        }

        if not isinstance(data, list):
            logger.warning(f"AJAX response is not a list: {type(data)}")
            return result

        for entry in data:
            if not isinstance(entry, dict):
                continue

            entry_type = entry.get("type")
            entry_data = entry.get("data")

            if entry_type == RESPONSE_TYPE_BACKGROUND:
                result["background"].append(entry_data)

            elif entry_type == RESPONSE_TYPE_CHANGE_VIEW:
                result["updates"].append(entry_data)

            elif entry_type == RESPONSE_TYPE_UPDATE_TEMPLATE:
                result["updates"].append(entry_data)

            elif entry_type == RESPONSE_TYPE_REDIRECT:
                result["redirect"] = entry_data

            elif entry_type == RESPONSE_TYPE_ERROR:
                errors = self._parse_errors(entry_data)
                result["errors"].extend(errors)

            elif entry_type == RESPONSE_TYPE_NOTIFICATION:
                if isinstance(entry_data, str):
                    result["notifications"].append(entry_data)

            elif entry_type == RESPONSE_TYPE_CAPTCHA:
                # Captcha required — handled at a higher level
                logger.warning("AJAX response contains captcha challenge")
                result["errors"].append("captcha_required")

        # Check for critical errors
        self._check_critical_errors(result["errors"])

        return result

    def extract_errors(self, data: list[dict[str, Any]]) -> list[str]:
        """Extract only error messages from an AJAX response.

        Args:
            data: Raw parsed JSON list from an AJAX response.

        Returns:
            List of error message strings.
        """
        errors: list[str] = []

        if not isinstance(data, list):
            return errors

        for entry in data:
            if isinstance(entry, dict) and entry.get("type") == RESPONSE_TYPE_ERROR:
                errors.extend(self._parse_errors(entry.get("data")))

        return errors

    def extract_redirect(self, data: list[dict[str, Any]]) -> str | None:
        """Extract a redirect URL from an AJAX response.

        Args:
            data: Raw parsed JSON list from an AJAX response.

        Returns:
            Redirect URL string, or None if no redirect is present.
        """
        if not isinstance(data, list):
            return None

        for entry in data:
            if isinstance(entry, dict) and entry.get("type") == RESPONSE_TYPE_REDIRECT:
                redirect = entry.get("data")
                if isinstance(redirect, str):
                    return redirect

        return None

    # ── Private Helpers ──

    def _parse_errors(self, error_data: Any) -> list[str]:
        """Parse error data into a list of human-readable strings."""
        errors: list[str] = []

        if isinstance(error_data, str):
            errors.append(error_data)
        elif isinstance(error_data, list):
            for item in error_data:
                if isinstance(item, str):
                    errors.append(item)
                elif isinstance(item, dict):
                    msg = item.get("message") or item.get("text") or str(item)
                    errors.append(msg)
        elif isinstance(error_data, dict):
            msg = error_data.get("message") or error_data.get("text") or str(error_data)
            errors.append(msg)

        return errors

    def _check_critical_errors(self, errors: list[str]) -> None:
        """Raise exceptions for critical server errors.

        Args:
            errors: List of error strings from the response.

        Raises:
            MaintenanceError: If a maintenance message is detected.
            RateLimitError: If rate limiting is indicated.
        """
        for error in errors:
            error_lower = error.lower()
            if "maintenance" in error_lower:
                raise MaintenanceError(f"Server maintenance: {error}")
            if "rate" in error_lower and "limit" in error_lower:
                raise RateLimitError(f"Rate limited: {error}")
            if "too many" in error_lower:
                raise RateLimitError(f"Too many requests: {error}")
