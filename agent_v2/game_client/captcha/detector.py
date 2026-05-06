"""Captcha detection and solving via hub API (proxied to ikabotapi)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from ..exceptions import CaptchaRequiredError, GameClientError

if TYPE_CHECKING:
    from core.hub_client import HubClient

    from requests import Response

logger = logging.getLogger(__name__)

# Known patterns that indicate a captcha challenge in game responses
CAPTCHA_INDICATORS = [
    "captchaNeeded",
    "showCaptcha",
    "captcha_challenge",
    # "Piracy_trainer_trainer" removed — it's a JS class name in ALL piracy responses (false positive)
    # Real piracy captcha uses "captchaNeeded" or "createCaptcha" in the JSON entries
]


class CaptchaDetector:
    """Detects captcha challenges in Ikariam game responses."""

    def check_response(self, response: Response) -> bool:
        """Check if a game response contains a captcha challenge.

        Args:
            response: The HTTP response from the game server.

        Returns:
            True if a captcha is present in the response.
        """
        text = response.text
        return any(indicator in text for indicator in CAPTCHA_INDICATORS)

    def extract_captcha_data(self, response: Response) -> dict[str, Any]:
        """Extract captcha challenge data from a game response.

        Args:
            response: The HTTP response containing captcha data.

        Returns:
            Dictionary with captcha type, image URLs, and other challenge data.

        Raises:
            GameClientError: If captcha data cannot be extracted.
        """
        # TODO: Implement actual captcha data extraction from game HTML/JSON
        # Ikariam uses different captcha types:
        #   - Drag captcha (drag item to correct position)
        #   - Image captcha (select correct images)
        #   - Piracy captcha (specific to piracy feature)
        text = response.text
        captcha_data: dict[str, Any] = {}

        # TODO: Extract captcha type
        captcha_type = self._detect_captcha_type(text)
        captcha_data["type"] = captcha_type

        # TODO: Extract captcha images/challenge data
        # Parse the HTML/JSON to find image URLs or challenge parameters
        images = self._extract_images(text)
        captcha_data["images"] = images

        # TODO: Extract any additional tokens or IDs needed for solving
        captcha_data["challenge_id"] = self._extract_challenge_id(text)

        if not captcha_data.get("type"):
            raise GameClientError("Could not determine captcha type from response")

        logger.info(f"Detected captcha type: {captcha_type}")
        return captcha_data

    def _detect_captcha_type(self, html: str) -> str:
        """Determine which type of captcha is being presented.

        Args:
            html: Raw response text.

        Returns:
            Captcha type identifier string.
        """
        # TODO: Implement type detection logic based on HTML content
        if "Piracy_trainer" in html:
            return "piracy"
        if "dragCaptcha" in html:
            return "drag"
        return "image"

    def _extract_images(self, html: str) -> dict[str, str]:
        """Extract captcha image URLs from response.

        Args:
            html: Raw response text.

        Returns:
            Dict mapping image identifiers to their URLs.
        """
        # TODO: Parse actual captcha image URLs from the game HTML
        images: dict[str, str] = {}

        # Example pattern (actual patterns depend on game version):
        # <img src="/captcha/image?id=..." />
        img_pattern = re.compile(r'captcha[^"]*?src=["\']([^"\']+)["\']', re.IGNORECASE)
        for i, match in enumerate(img_pattern.finditer(html)):
            images[f"image_{i}"] = match.group(1)

        return images

    def _extract_challenge_id(self, html: str) -> str:
        """Extract the captcha challenge ID from response.

        Args:
            html: Raw response text.

        Returns:
            Challenge ID string, or empty string if not found.
        """
        # TODO: Extract challenge/token ID from captcha HTML
        match = re.search(r'captcha[_-]?id["\s:=]+["\']?(\w+)', html, re.IGNORECASE)
        return match.group(1) if match else ""


class CaptchaSolver:
    """Solves Ikariam captchas via hub API (proxied to ikabotapi container)."""

    def __init__(self, hub: HubClient):
        self.hub = hub

    def solve(self, captcha_data: dict[str, Any]) -> str:
        """Send captcha data to hub for solving and return the solution.

        Args:
            captcha_data: Dictionary containing captcha type, images, and challenge data
                          as extracted by CaptchaDetector.

        Returns:
            Solution string to submit back to the game server.

        Raises:
            CaptchaRequiredError: If the captcha cannot be solved.
        """
        captcha_type = captcha_data.get("type", "unknown")
        images = captcha_data.get("images", {})

        logger.info(f"Sending captcha to hub for solving (type={captcha_type})")

        try:
            result = self.hub.solve_captcha(captcha_type, images)
        except Exception as e:
            raise CaptchaRequiredError(
                f"Captcha solving failed: {e}",
                captcha_data=captcha_data,
            ) from e

        solution = result.get("solution", "")
        if not solution:
            raise CaptchaRequiredError(
                "Hub returned empty captcha solution",
                captcha_data=captcha_data,
            )

        logger.info("Captcha solved successfully")
        return solution
