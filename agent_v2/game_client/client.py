"""Main GameClient — facade for all Ikariam game operations.

This is the single entry point for job runners to interact with the game.
All HTTP requests go through StrictProxySession (game domains are proxied,
internal/hub requests bypass proxy).

Usage:
    from core.hub_client import HubClient
    from game_client import GameClient

    hub = HubClient()
    client = GameClient(account_id="abc123", hub=hub, proxy_url="socks5://proxy:1080")
    client.login(email="user@example.com", password="secret", server_id="s201-br")

    cities = client.get_cities()
    client.upgrade(city_id=cities[0].id, building_position=0)
"""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING, Any

import requests

from core.proxy import StrictProxySession

from .actions.academy import AcademyAction
from .actions.city import BuildAction, DemolishAction, UpgradeAction
from .actions.daily import DailyTasksAction
from .actions.market import BuyAction, SellAction
from .actions.miracle import MiracleAction
from .actions.military import AttackAction, SendTroopsAction, TrainAction
from .actions.research import ResearchAction
from .actions.resources import CollectAction, DonateAction, SendResourcesAction
from .actions.shrine import ShrineAction
from .actions.workshop import WorkshopAction
from .auth.login import IkariamAuth
from .captcha.detector import CaptchaDetector, CaptchaSolver
from .constants import (
    GAME_AJAX_HEADERS,
    GAME_URL_TEMPLATE,
    MAX_RETRIES,
    REQUEST_DELAY_MAX,
    REQUEST_DELAY_MIN,
    USER_AGENTS,
)
from .exceptions import (
    ActionError,
    CaptchaRequiredError,
    GameClientError,
    MaintenanceError,
    ProxyError,
    RateLimitError,
    SessionExpiredError,
)
from .models.city import City, Resources
from .parsers.html_parser import GamePageParser
from .parsers.json_parser import AjaxResponseParser

if TYPE_CHECKING:
    from core.hub_client import HubClient

logger = logging.getLogger(__name__)


class GameClient:
    """Main game client — facade for all Ikariam operations.

    Wraps authentication, AJAX requests, response parsing, captcha handling,
    and all game actions behind a clean interface. Job runners interact with
    the game exclusively through this class.
    """

    def __init__(self, account_id: str, hub: HubClient, proxy_url: str = ""):
        """Initialize the game client.

        Args:
            account_id: Unique identifier for the game account.
            hub: HubClient instance for blackbox tokens, captcha solving, etc.
            proxy_url: SOCKS5/HTTP proxy URL for game requests.
                       Required — StrictProxySession will reject game requests without it.
        """
        self.account_id = account_id
        self.hub = hub

        # HTTP session — all game requests go through proxy
        self.session = StrictProxySession(proxy_url)
        self.session.headers["User-Agent"] = random.choice(USER_AGENTS)

        # Sub-components
        self.auth = IkariamAuth(self.session, hub)
        self.page_parser = GamePageParser()
        self.ajax_parser = AjaxResponseParser()
        self.captcha_detector = CaptchaDetector()
        self.captcha_solver = CaptchaSolver(hub)

        # State
        self._action_request: str = ""
        self._server_url: str = ""
        self._cookies: dict[str, str] = {}
        self._last_request_time: float = 0.0

    # ── Authentication ──

    def login(
        self, email: str, password: str, server_id: str,
        existing_token: str = "",
        lobby_account_id: int | None = None,
    ) -> bool:
        """Authenticate with the game server.

        Args:
            email: Gameforge account email.
            password: Gameforge account password.
            server_id: Target server (e.g. "s201-br").
            existing_token: Optional cached lobby token to try first (avoids re-login).
            lobby_account_id: Optional Gameforge account ID to disambiguate
                multiple accounts on the same server.

        Returns:
            True if login was successful.

        Raises:
            LoginError: If authentication fails.
        """
        logger.info(f"GameClient login for account {self.account_id} on {server_id}")

        cookies = self.auth.login(
            email, password, server_id, existing_token, lobby_account_id,
        )
        self._cookies = cookies
        self._server_url = self._build_server_url(server_id)

        # Setup game-specific headers now that we know the server
        self._setup_game_headers()

        # Fetch the initial page to get the actionRequest token
        self._refresh_action_request()

        logger.info("GameClient login complete")
        return True

    @property
    def lobby_token(self) -> str:
        """Return the lobby token obtained during login."""
        return self.auth.lobby_token

    @property
    def account_info(self) -> dict:
        """Account metadata populated after login (player_name, server_lang, etc.)."""
        return self.auth.account_info

    def connect(self, server_id: str, cookies: dict[str, str] | None = None) -> None:
        """Connect to a game server, optionally restoring cached cookies.

        Sets the server URL and game headers. If cookies are provided,
        restores them and refreshes the actionRequest token.
        Use this for session reuse without full login.

        Args:
            server_id: Target server (e.g. "s61-br").
            cookies: Optional cached cookies to restore.
        """
        self._server_url = self._build_server_url(server_id)
        self._setup_game_headers()

        if cookies:
            self._cookies = cookies
            self.session.cookies.update(cookies)
            logger.debug(f"Restored {len(cookies)} session cookies for {server_id}")
            self._refresh_action_request()

    def restore_cookies(self, cookies: dict[str, str]) -> None:
        """Restore a previously cached session.

        Args:
            cookies: Dictionary of session cookies from a prior login.
        """
        self._cookies = cookies
        self.session.cookies.update(cookies)
        logger.debug(f"Restored {len(cookies)} session cookies")

    def export_cookies(self) -> dict[str, str]:
        """Export current session cookies for caching.

        Returns:
            Dictionary of session cookies.
        """
        return dict(self._cookies)

    def is_session_valid(self, server_id: str, cookies: dict[str, str] | None = None) -> bool:
        """Validate whether cached game cookies still open the target server.

        Args:
            server_id: Target server (e.g. "s61-br").
            cookies: Optional cookies to validate. Falls back to current cookies.

        Returns:
            True when the game session is still active for that server.
        """
        cookies_to_check = cookies or self._cookies
        if not cookies_to_check:
            return False
        server_url = self._build_server_url(server_id)
        return self.auth.is_session_valid(cookies_to_check, server_url)

    # ── City Operations ──

    def get_cities(self) -> list[City]:
        """Fetch all cities owned by the player.

        Returns:
            List of City models with buildings and resources.
        """
        # TODO: Implement full city list retrieval
        # This typically requires fetching the town advisor or city dropdown
        # and then loading each city individually
        logger.info("Fetching city list")

        resp = self._request("GET", self._server_url, params={"view": "city"})
        city = self.page_parser.parse_city_view(resp.text)

        # TODO: Iterate over all cities in the account (city dropdown)
        # For now, return just the current city
        return [city]

    def build(self, city_id: int, building_type: str, position: int) -> dict[str, Any]:
        """Build a new building in a city slot.

        Args:
            city_id: Target city ID.
            building_type: Building type name (see BUILDING_TYPES).
            position: City slot position.

        Returns:
            Parsed AJAX response.
        """
        action = BuildAction(self)
        return action.execute(city_id=city_id, building_type=building_type, position=position)

    def upgrade(
        self,
        city_id: int,
        building_position: int,
        *,
        current_level: int | None = None,
        template_view: str = "city",
    ) -> dict[str, Any]:
        """Upgrade a building to the next level.

        Args:
            city_id: Target city ID.
            building_position: Building position slot.

        Returns:
            Parsed AJAX response.
        """
        action = UpgradeAction(self)
        return action.execute(
            city_id=city_id,
            building_position=building_position,
            current_level=current_level,
            template_view=template_view,
        )

    def demolish(self, city_id: int, building_position: int, *, template_view: str = "city") -> dict[str, Any]:
        """Demolish (downgrade) a building by one level.

        Args:
            city_id: Target city ID.
            building_position: Building position slot.

        Returns:
            Parsed AJAX response.
        """
        action = DemolishAction(self)
        return action.execute(
            city_id=city_id,
            building_position=building_position,
            template_view=template_view,
        )

    # ── Resources ──

    def donate(self, island_id: int | str, donation_type: str, amount: int) -> dict[str, Any]:
        """Donate resources to an island project.

        Args:
            island_id: Island ID where the donation goes.
            donation_type: "resource" (forest/wood) or "tradegood" (island luxury).
            amount: Amount to donate.

        Returns:
            Parsed AJAX response.
        """
        action = DonateAction(self)
        return action.execute(island_id=island_id, donation_type=donation_type, amount=amount)

    def get_city_island_id(self, city_id: int) -> str | None:
        """Fetch a city page and extract the islandId.

        Args:
            city_id: City ID to look up.

        Returns:
            Island ID as string, or None if not found.
        """
        import json as _json
        import re as _re

        resp = self._request("GET", self._server_url, params={"view": "city", "cityId": city_id})
        html = resp.text

        # Method 1: JSON from updateBackgroundData
        bg_match = _re.search(
            r'"updateBackgroundData",\s?([\s\S]*?)\],\s*\["updateTemplateData"', html,
        )
        if bg_match:
            try:
                city_data = _json.loads(bg_match.group(1), strict=False)
                island_id = city_data.get("islandId")
                if island_id:
                    return str(island_id)
            except (_json.JSONDecodeError, AttributeError):
                pass

        # Method 2: regex fallback
        m = _re.search(r'"islandId"[:\s]*"?(\d+)"?', html)
        return m.group(1) if m else None

    def send_resources(
        self, from_city: int, to_city: int, resources: dict[str, int]
    ) -> dict[str, Any]:
        """Send resources between cities via trade ships.

        Args:
            from_city: Source city ID.
            to_city: Destination city ID.
            resources: Dict mapping resource name to amount.

        Returns:
            Parsed AJAX response.
        """
        action = SendResourcesAction(self)
        return action.execute(from_city=from_city, to_city=to_city, resources=resources)

    def get_shrine_state(self, city_id: int, position: int) -> dict[str, Any]:
        """Fetch the structured Shrine of Olympus overview state."""
        action = ShrineAction(self)
        return action.get_state(city_id=city_id, position=position)

    def get_shrine_favor(self, city_id: int, position: int) -> int:
        """Fetch current shrine favor."""
        action = ShrineAction(self)
        return action.get_favor(city_id=city_id, position=position)

    def activate_shrine_god(self, city_id: int, position: int, god_id: int) -> dict[str, Any]:
        """Donate favor to one shrine god."""
        action = ShrineAction(self)
        return action.activate_god(city_id=city_id, position=position, god_id=god_id)

    def get_shrine_page(self, city_id: int, position: int) -> str:
        """Fetch the full shrine HTML page."""
        action = ShrineAction(self)
        return action.get_full_page(city_id=city_id, position=position)

    def get_daily_tasks_state(self, city_id: int) -> dict[str, Any]:
        """Fetch daily tasks state for a city context."""
        action = DailyTasksAction(self)
        return action.get_state(city_id=city_id)

    def collect_daily_login_bonus(self, city_id: int) -> dict[str, Any]:
        """Collect the daily login wine bonus."""
        action = DailyTasksAction(self)
        return action.collect_daily_bonus(city_id=city_id)

    def collect_daily_task_favor(self, city_id: int, task_id: int) -> dict[str, Any]:
        """Collect favor for one completed daily task."""
        action = DailyTasksAction(self)
        return action.collect_task_favor(city_id=city_id, task_id=task_id)

    def get_daily_city_overview(self, city_id: int) -> dict[str, Any]:
        """Fetch city overview helpers used by daily login flows."""
        action = DailyTasksAction(self)
        return action.get_city_overview(city_id=city_id)

    def collect_ambrosia_fountain(self, city_id: int) -> dict[str, Any]:
        """Collect ambrosia fountain reward for the current city."""
        action = DailyTasksAction(self)
        return action.collect_ambrosia_fountain(city_id=city_id)

    def get_temple_miracle_state(self, city_id: int, position: int) -> dict[str, Any]:
        """Fetch the miracle state for a city's temple."""
        action = MiracleAction(self)
        return action.get_temple_state(city_id=city_id, position=position)

    def activate_miracle(self, city_id: int, position: int) -> dict[str, Any]:
        """Activate the island miracle from the temple."""
        action = MiracleAction(self)
        return action.activate_miracle(city_id=city_id, position=position)

    def get_research_state(self, city_id: int) -> dict[str, Any]:
        """Fetch research advisor state for one city context."""
        action = ResearchAction(self)
        return action.get_state(city_id=city_id)

    def discover_research(self, city_id: int, research_type: str) -> dict[str, Any]:
        """Discover the next available research of one branch."""
        action = ResearchAction(self)
        return action.discover(city_id=city_id, research_type=research_type)

    def get_academy_state(self, city_id: int, position: int) -> dict[str, Any]:
        """Fetch academy state for a city academy slot."""
        action = AcademyAction(self)
        return action.get_state(city_id=city_id, position=position)

    def set_scientists(self, city_id: int, position: int, scientists: int) -> dict[str, Any]:
        """Set the number of scientists assigned to one academy."""
        action = AcademyAction(self)
        return action.set_scientists(city_id=city_id, position=position, scientists=scientists)

    def buy_research(
        self,
        city_id: int,
        position: int,
        *,
        use_athena_scroll: bool = False,
        pay_with_ambrosia: bool = False,
    ) -> dict[str, Any]:
        """Run a crystal experiment in one academy."""
        action = AcademyAction(self)
        return action.buy_research(
            city_id=city_id,
            position=position,
            use_athena_scroll=use_athena_scroll,
            pay_with_ambrosia=pay_with_ambrosia,
        )

    # ── Workshop ──

    def get_workshop_state(self, city_id: int, position: int) -> dict[str, Any]:
        """Fetch workshop state for a city.

        Returns a dict with ``in_progress``, ``remaining_seconds``, ``improvements``
        (list of available improvements) and current ``gold``.
        """
        action = WorkshopAction(self)
        return action.get_state(city_id=city_id, position=position)

    def start_workshop_improvement(
        self,
        city_id: int,
        position: int,
        improvement_id: int,
        *,
        upgrade_type: str = "offensive",
    ) -> dict[str, Any]:
        """Start researching a unit improvement in the Workshop.

        Args:
            city_id: City where the Workshop is located.
            position: Building slot position of the Workshop.
            improvement_id: ID of the improvement to research.
            upgrade_type: Workshop branch to improve (usually ``offensive`` or ``defensive``).

        Returns:
            Parsed response with ``ok`` and updated ``gold``.
        """
        action = WorkshopAction(self)
        return action.start_improvement(
            city_id=city_id,
            position=position,
            improvement_id=improvement_id,
            upgrade_type=upgrade_type,
        )

    # ── Military ──

    def train_troops(self, city_id: int, units: dict[str, int]) -> dict[str, Any]:
        """Train military units at barracks/shipyard.

        Args:
            city_id: City with the barracks.
            units: Dict mapping unit type to quantity.

        Returns:
            Parsed AJAX response.
        """
        action = TrainAction(self)
        return action.execute(city_id=city_id, units=units)

    def attack(
        self, from_city: int, target_city: int, units: dict[str, int]
    ) -> dict[str, Any]:
        """Launch an attack against another city.

        Args:
            from_city: Source city ID.
            target_city: Target city ID.
            units: Dict mapping unit type to quantity.

        Returns:
            Parsed AJAX response.
        """
        action = AttackAction(self)
        return action.execute(from_city=from_city, target_city=target_city, units=units)

    def send_troops(
        self, from_city: int, to_city: int, units: dict[str, int]
    ) -> dict[str, Any]:
        """Send troops to garrison a friendly city.

        Args:
            from_city: Source city ID.
            to_city: Destination city ID.
            units: Dict mapping unit type to quantity.

        Returns:
            Parsed AJAX response.
        """
        action = SendTroopsAction(self)
        return action.execute(from_city=from_city, to_city=to_city, units=units)

    # ── Market ──

    def buy_offer(self, city_id: int, offer_id: int) -> dict[str, Any]:
        """Buy a marketplace offer.

        Args:
            city_id: City to receive purchased resources.
            offer_id: Marketplace offer ID.

        Returns:
            Parsed AJAX response.
        """
        action = BuyAction(self)
        return action.execute(city_id=city_id, offer_id=offer_id)

    def sell_resources(
        self, city_id: int, resource_type: int, amount: int, price: int
    ) -> dict[str, Any]:
        """Create a sell offer on the marketplace.

        Args:
            city_id: City to sell from.
            resource_type: Resource type index.
            amount: Amount to sell.
            price: Price per unit in gold.

        Returns:
            Parsed AJAX response.
        """
        action = SellAction(self)
        return action.execute(
            city_id=city_id, resource_type=resource_type, amount=amount, price=price
        )

    # ── Internal HTTP Methods ──

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Make an HTTP request to the game server with retry and delay logic.

        All game HTTP requests should go through this method to ensure:
        - Human-like delays between requests
        - Captcha detection on every response
        - Session expiry detection
        - Retry on transient errors

        Args:
            method: HTTP method (GET, POST).
            url: Full URL.
            **kwargs: Additional arguments for requests.Session.request().

        Returns:
            HTTP response object.

        Raises:
            SessionExpiredError: If the session is no longer valid.
            CaptchaRequiredError: If a captcha is triggered.
            MaintenanceError: If the server is in maintenance.
            ProxyError: If the proxy connection fails.
        """
        # Enforce human-like delay between requests
        self._enforce_delay()

        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.request(method, url, **kwargs)
                self._last_request_time = time.time()
            except requests.exceptions.ProxyError as e:
                raise ProxyError(f"Proxy connection failed: {e}") from e
            except requests.exceptions.ConnectionError as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Connection error (attempt {attempt + 1}): {e}")
                    time.sleep(2 ** attempt)
                    continue
                raise GameClientError(f"Connection failed after {MAX_RETRIES} attempts: {e}") from e
            except requests.exceptions.Timeout as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Timeout (attempt {attempt + 1}): {e}")
                    time.sleep(2 ** attempt)
                    continue
                raise GameClientError(f"Request timed out after {MAX_RETRIES} attempts") from e

            # Check for captcha in response
            if self.captcha_detector.check_response(resp):
                captcha_data = self.captcha_detector.extract_captcha_data(resp)
                logger.warning("Captcha detected in response")
                # TODO: Auto-solve captcha and retry, or raise for caller to handle
                raise CaptchaRequiredError("Captcha detected", captcha_data=captcha_data)

            # Check for session expiry (redirect to login page)
            if resp.status_code in (302, 303) and "login" in resp.headers.get("Location", ""):
                raise SessionExpiredError("Session expired — redirected to login")

            # Check for maintenance
            if resp.status_code == 503:
                raise MaintenanceError("Server returned 503 — likely maintenance")

            return resp

        # Should not reach here, but just in case
        raise GameClientError("Request failed after all retries")

    def _ajax(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send an AJAX request to the game server and parse the JSON response.

        Args:
            action: AJAX action string (used for logging).
            params: Full parameter dict including actionRequest.

        Returns:
            Parsed response dictionary from AjaxResponseParser.

        Raises:
            ActionError: If the server returns errors for this action.
        """
        logger.debug(f"AJAX: {action}")

        resp = self._request(
            "POST",
            self._server_url,
            data=params,
            headers=GAME_AJAX_HEADERS,
        )

        # Ikariam AJAX responses are JSON arrays
        try:
            data = resp.json()
        except ValueError:
            raise ActionError(
                f"Invalid JSON in AJAX response for {action}",
                action=action,
            )

        parsed = self.ajax_parser.parse_response(data)

        # Check for action-level errors
        if parsed["errors"]:
            # Captcha error is special
            if "captcha_required" in parsed["errors"]:
                raise CaptchaRequiredError(
                    f"Captcha required during action: {action}"
                )
            raise ActionError(
                f"Server errors for action {action}: {parsed['errors']}",
                action=action,
                server_errors=parsed["errors"],
            )

        # Update actionRequest if a new one is present in the response
        # TODO: Extract new actionRequest from AJAX response updates
        self._update_action_request_from_response(parsed)

        return parsed

    def _refresh_action_request(self) -> None:
        """Fetch a game page to obtain a fresh actionRequest token."""
        logger.debug("Refreshing actionRequest token")
        resp = self._request("GET", self._server_url)
        token = self.page_parser.extract_action_request(resp.text)
        if token:
            self._action_request = token
            logger.debug(f"actionRequest updated: {token[:16]}...")
        else:
            logger.warning("Failed to extract actionRequest from page")

    def _update_action_request_from_response(self, parsed: dict[str, Any]) -> None:
        """Extract and update actionRequest token from an AJAX response.

        The game sometimes sends a new actionRequest in AJAX responses,
        which must be used for the next request.
        """
        # TODO: Implement extraction from parsed AJAX response
        # The new token may appear in the "updates" or "background" sections
        pass

    def _setup_game_headers(self) -> None:
        """Set game-specific headers on the session after login/connect.

        These headers mimic a real browser interacting with the game server.
        """
        if not self._server_url:
            return

        # Extract host from server URL (e.g. "s61-br.ikariam.gameforge.com")
        from urllib.parse import urlparse
        host = urlparse(self._server_url).hostname or ""

        ua = self.session.headers.get("User-Agent", USER_AGENTS[0])
        self.session.headers.update({
            "Host": host,
            "User-Agent": ua,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": f"https://{host}",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": f"https://{host}",
            "DNT": "1",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
        })

    def _enforce_delay(self) -> None:
        """Enforce minimum delay between consecutive game requests."""
        if self._last_request_time > 0:
            elapsed = time.time() - self._last_request_time
            min_delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
            if elapsed < min_delay:
                sleep_time = min_delay - elapsed
                logger.debug(f"Delaying {sleep_time:.1f}s between requests")
                time.sleep(sleep_time)

    @staticmethod
    def _build_server_url(server_id: str) -> str:
        """Build the game server URL from a server ID.

        Args:
            server_id: Server identifier like "s201-br".

        Returns:
            Full game server URL.
        """
        # Parse server_id format: "s{number}-{lang}"
        # Example: "s201-br" → number=201, lang=br
        parts = server_id.split("-", 1)
        if len(parts) != 2:
            raise GameClientError(f"Invalid server_id format: {server_id} (expected 's{{N}}-{{lang}}')")

        number = parts[0].lstrip("s")
        lang = parts[1]

        return GAME_URL_TEMPLATE.format(server_id=number, lang=lang)
