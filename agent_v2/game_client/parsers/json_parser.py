"""AJAX JSON response parser for Ikariam.

Ikariam AJAX responses use a list-of-lists format:
    [
        ["command_name", data],
        ["command_name2", data2],
        ...
    ]

Known commands observed in live traffic:
    "custom"           → ["reload", {"link": "?view=..."}]  — server rejected the action
                         BUT if "provideFeedback" is also present with success type,
                         the reload is just a page refresh after a successful action.
    "updateGlobalData" → {"actionRequest": "...", ...}       — fresh AR token + global state
    "updateBacklink"   → null | string                       — ignored
    "changeView"       → [[selector, html], ...]             — DOM updates (success)
    "loadContent"      → [[selector, html], ...]             — DOM load (success)
    "updateResources"  → {...}                               — resource bar update
    "showMessage"      → {"title": ..., "text": ...}         — server error message
    "error"            → string | {...}                      — explicit error
    "provideFeedback"  → [{"type": 10, "text": "..."}]       — type 10 = success confirmation
    "popupData"        → {...}                               — popup (achievement, etc.)
"""

from __future__ import annotations

import logging
from typing import Any

from ..exceptions import ActionError, MaintenanceError, RateLimitError

logger = logging.getLogger(__name__)


class AjaxResponseParser:
    """Parses Ikariam AJAX JSON responses (list-of-lists format) into usable data."""

    def parse_response(self, data: list[Any]) -> dict[str, Any]:
        """Parse a full AJAX response into categorized components.

        Args:
            data: Raw parsed JSON list from an AJAX response.

        Returns:
            Dictionary with:
                - "updates": list of DOM update directives (changeView / loadContent)
                - "background": background / resource data
                - "redirect": reload link if game sent a reload directive
                - "errors": list of error message strings
                - "notifications": list of notification strings
                - "new_action_request": fresh AR token from updateGlobalData, or None
                - "raw": the original data
                - "reload": True if the game sent a custom reload (action rejected)

        Raises:
            MaintenanceError: If the server reports maintenance.
            RateLimitError: If the server indicates rate limiting.
        """
        result: dict[str, Any] = {
            "updates": [],
            "background": [],
            "redirect": None,
            "errors": [],
            "notifications": [],
            "new_action_request": None,
            "raw": data,
            "reload": False,
            "success_feedback": False,  # True when provideFeedback confirms success
        }

        if not isinstance(data, list):
            logger.warning("AJAX response is not a list: %s", type(data))
            return result

        for entry in data:
            if not isinstance(entry, (list, tuple)) or len(entry) < 1:
                continue

            cmd = entry[0]
            payload = entry[1] if len(entry) > 1 else None

            if cmd == "custom":
                self._handle_custom(payload, result)

            elif cmd == "updateGlobalData":
                if isinstance(payload, dict):
                    ar = payload.get("actionRequest")
                    if ar:
                        result["new_action_request"] = ar
                    result["background"].append(payload)

            elif cmd in ("changeView", "loadContent", "updateTemplate"):
                result["updates"].append(payload)

            elif cmd == "updateResources":
                result["background"].append(payload)

            elif cmd == "showMessage":
                msg = self._extract_message(payload)
                if msg:
                    result["errors"].append(msg)
                    logger.warning("AJAX showMessage: %s", msg)

            elif cmd == "error":
                msg = self._extract_message(payload)
                if msg:
                    result["errors"].append(msg)
                    logger.warning("AJAX error: %s", msg)

            elif cmd == "provideFeedback":
                # type 10 = success (green); type 11 = error (red); others = info
                if isinstance(payload, (list, tuple)):
                    for fb in payload:
                        if not isinstance(fb, dict):
                            continue
                        fb_type = int(fb.get("type", 0))
                        fb_text = fb.get("text", "")
                        if fb_type == 10:
                            result["success_feedback"] = True
                            logger.info("AJAX provideFeedback success: %s", fb_text)
                        elif fb_type == 11:
                            result["errors"].append(fb_text)
                            logger.warning("AJAX provideFeedback error: %s", fb_text)

            elif cmd in ("updateBacklink", "setVariable", "logData", "popupData"):
                pass  # informational only

            else:
                logger.debug("Unknown AJAX command: %s", cmd)

        # custom reload alongside success feedback = page refresh after action, not rejection
        if result["reload"] and result["success_feedback"]:
            logger.info("AJAX custom reload after success feedback — treating as success (page refresh)")
            result["reload"] = False

        self._check_critical_errors(result["errors"])
        return result

    # ── backward-compat helpers used by other modules ──

    def extract_errors(self, data: list[Any]) -> list[str]:
        """Extract only error messages from an AJAX response."""
        try:
            return self.parse_response(data)["errors"]
        except Exception:
            return []

    def extract_redirect(self, data: list[Any]) -> str | None:
        """Extract a redirect/reload link from an AJAX response."""
        try:
            return self.parse_response(data)["redirect"]
        except Exception:
            return None

    # ── Private helpers ──

    def _handle_custom(self, payload: Any, result: dict[str, Any]) -> None:
        """Handle the 'custom' command — usually a server-side reload directive."""
        if not isinstance(payload, (list, tuple)) or len(payload) < 1:
            return
        action = payload[0]
        action_data = payload[1] if len(payload) > 1 else {}
        if action == "reload":
            link = action_data.get("link", "") if isinstance(action_data, dict) else ""
            result["reload"] = True
            result["redirect"] = link
            logger.warning("AJAX custom reload — server rejected the action (link=%s)", link)
        else:
            logger.debug("AJAX custom action: %s", action)

    def _extract_message(self, payload: Any) -> str | None:
        """Extract a human-readable string from a showMessage / error payload."""
        if isinstance(payload, str):
            return payload or None
        if isinstance(payload, dict):
            return (
                payload.get("text")
                or payload.get("message")
                or payload.get("description")
                or str(payload)
            )
        if isinstance(payload, (list, tuple)) and payload:
            return self._extract_message(payload[0])
        return None

    def _check_critical_errors(self, errors: list[str]) -> None:
        """Raise exceptions for critical server-side errors."""
        for error in errors:
            el = error.lower()
            if "maintenance" in el:
                raise MaintenanceError(f"Server maintenance: {error}")
            if "rate" in el and "limit" in el:
                raise RateLimitError(f"Rate limited: {error}")
            if "too many" in el:
                raise RateLimitError(f"Too many requests: {error}")
