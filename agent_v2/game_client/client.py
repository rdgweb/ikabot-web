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
from .actions.barbarians import AttackBarbarianVillageAction, ATTACK_SCHEMATICS, LOOT_SCHEMATICS, get_schematic, calculate_transporters
from .actions.island import IslandActions
from .actions.city import BuildAction, DemolishAction, UpgradeAction
from .actions.cinema import CinemaAction
from .actions.daily import DailyTasksAction
from .actions.port import PortAction
from .actions.premium import PremiumInventoryAction, PremiumTraderAction
from .actions.diplomacy import DiplomacyInboxAction, DiplomacySendAction
from .actions.black_market import (
    AddBlackMarketOfferAction,
    BuyUnitsAction,
    CancelBlackMarketOfferAction,
    GetAvailableUnitOffersAction,
    GetBlackMarketStateAction,
    GetMyBlackMarketOffersAction,
    UNIT_TYPE_MARITIME,
    UNIT_TYPE_TERRESTRIAL,
)
from .actions.market import BuyAction, CreateOfferAction, GetOffersAction, SellAction
from .actions.miracle import MiracleAction
from .actions.military import AttackAction, BlockadeFleetAction, FetchBarracksStateAction, FetchBlockadeViewAction, FetchCombatDetailedReportAction, FetchCombatReportDetailAction, FetchCombatReportsAction, FetchMilitaryAdvisorAction, FetchStationedUnitsAction, PlunderLandAction, RecallBlockadeFleetAction, SendTroopsAction, StationAction, TrainAction
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


class GameClient(IslandActions):
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

    def is_session_valid(
        self,
        server_id: str,
        cookies: dict[str, str] | None = None,
        *,
        raise_on_error: bool = False,
    ) -> bool:
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
        return self.auth.is_session_valid(
            cookies_to_check,
            server_url,
            raise_on_error=raise_on_error,
        )

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

    def activate_vacation_mode(self, city_id: int) -> dict[str, Any]:
        """Activate vacation mode for the account.

        Deactivation is implicit — login after the mandatory vacation period lifts it.
        """
        action = DailyTasksAction(self)
        return action.activate_vacation_mode(city_id=city_id)

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

    def get_research_branch_details(self, city_id: int, research_type: str) -> dict[str, Any]:
        """Full branch tree + preconditions of the selected research."""
        action = ResearchAction(self)
        return action.get_branch_details(city_id=city_id, research_type=research_type)

    def get_cinema_state(self, city_id: int) -> dict[str, Any]:
        """Cineteatro: recompensas disponiveis + videoId da sessao."""
        action = CinemaAction(self)
        return action.get_state(city_id=city_id)

    def get_port_state(self, city_id: int) -> dict[str, Any]:
        """Estado do porto: barcos, custos, ouro."""
        return PortAction(self).get_state(city_id=city_id)

    def buy_ship(self, city_id: int, kind: str) -> dict[str, Any]:
        """Compra 1 barco (kind='transporter' ou 'freighter')."""
        return PortAction(self).buy_one(city_id=city_id, kind=kind)

    def get_premium_inventory(self, city_id: int) -> dict[str, Any]:
        """Itens do inventario premium (leitura)."""
        return PremiumInventoryAction(self).get_inventory(city_id=city_id)

    def activate_premium_item(self, city_id: int, item_id: int, *, target_city_id: int | None = None, target_god: int | None = None) -> dict[str, Any]:
        """Ativa um item premium. So sob confirmacao explicita — nao automatico."""
        return PremiumInventoryAction(self).activate_item(item_id=item_id, city_id=city_id, target_city_id=target_city_id, target_god=target_god)

    def get_premium_trader_state(self, city_id: int) -> dict[str, Any]:
        """Estado do negociante premium (leitura)."""
        return PremiumTraderAction(self).get_state(city_id=city_id)

    def premium_trade(self, city_id: int, *, send: dict, receive: dict, displayed_price: int, position: int = 0) -> dict[str, Any]:
        """Troca no negociante premium. So sob confirmacao explicita — nao automatico."""
        return PremiumTraderAction(self).trade(city_id=city_id, send=send, receive=receive, displayed_price=displayed_price, position=position)

    def claim_cinema_bonus(self, city_id: int, bonus_id: int, video_id: int) -> dict[str, Any]:
        """Cineteatro: coleta uma recompensa (AdVideoRewardAction/RequestBonus)."""
        action = CinemaAction(self)
        return action.claim(city_id=city_id, bonus_id=bonus_id, video_id=video_id)

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

    def fetch_barracks_state(
        self, city_id: int, position: int, building_type: str = "troops"
    ) -> dict[str, Any]:
        """Fetch unit list, costs and garrison state from barracks or shipyard."""
        action = FetchBarracksStateAction(self)
        return action.execute(city_id=city_id, position=position, building_type=building_type)

    def fetch_stationed_units(
        self, city_id: int, building_type: str = "troops"
    ) -> dict[str, Any]:
        """Fetch stationed troop/fleet counts from cityMilitary."""
        action = FetchStationedUnitsAction(self)
        return action.execute(city_id=city_id, building_type=building_type)

    def train_units(
        self,
        city_id: int,
        position: int,
        units: dict[int, int],
        building_type: str = "troops",
    ) -> dict[str, Any]:
        """Train units in barracks (BuildUnits) or shipyard (BuildShips).

        Args:
            city_id: City with the building.
            position: Building slot position.
            units: {unit_id: quantity} — numeric game unit IDs.
            building_type: "troops" or "fleet".
        """
        action = TrainAction(self)
        return action.execute(city_id=city_id, position=position, units=units, building_type=building_type)

    def station_units(
        self,
        from_city_id: int,
        to_city_id: int,
        units: dict[int, int],
        scope: str = "troops",
        to_island_id: int | None = None,
    ) -> dict[str, Any]:
        """Station troops or fleet from one city to another."""
        action = StationAction(self)
        return action.execute(
            from_city_id=from_city_id,
            to_city_id=to_city_id,
            units=units,
            scope=scope,
            to_island_id=to_island_id,
        )

    def train_troops(self, city_id: int, units: dict[str, int]) -> dict[str, Any]:
        """Legacy stub — use train_units instead."""
        action = TrainAction(self)
        return action.execute(city_id=city_id, units=units)

    def fetch_military_advisor(self, city_id: int) -> dict:
        """Fetch military advisor — battles, movements, port occupation, ETA."""
        return FetchMilitaryAdvisorAction(self).execute(city_id=city_id)

    def fetch_combat_reports(self, city_id: int, limit: int = 10) -> list:
        """Fetch recent combat reports list."""
        return FetchCombatReportsAction(self).execute(city_id=city_id, limit=limit)

    def fetch_combat_report_detail(self, city_id: int, combat_id: int) -> dict:
        """Fetch full combat report — loot, winner, units."""
        return FetchCombatReportDetailAction(self).execute(city_id=city_id, combat_id=combat_id)

    def fetch_combat_detailed_report(self, city_id: int, combat_id: int) -> dict:
        """Fetch per-round detailed report — all rounds with slot/unit data."""
        return FetchCombatDetailedReportAction(self).execute(city_id=city_id, combat_id=combat_id)

    def fetch_plunder_view(
        self, from_city_id: int, to_city_id: int, island_id: int
    ) -> dict[str, Any]:
        """Fetch plunder view — returns travel time and confirms raid is possible."""
        return PlunderLandAction(self).fetch_plunder_view(from_city_id, to_city_id, island_id)

    def plunder_land(
        self,
        from_city_id: int,
        to_city_id: int,
        island_id: int,
        units: dict[int, int],
        transporters: int = 0,
    ) -> dict[str, Any]:
        """Send army to plunder a player city. Returns {ok, travel_seconds}."""
        return PlunderLandAction(self).execute(
            from_city_id=from_city_id,
            to_city_id=to_city_id,
            island_id=island_id,
            units=units,
            transporters=transporters,
        )

    def fetch_blockade_view(self, from_city_id: int, to_city_id: int, island_id: int) -> dict:
        """Fetch blockade form — returns fleet travel time before sending."""
        return FetchBlockadeViewAction(self).execute(from_city_id, to_city_id, island_id)

    def recall_blockade_fleet(self, source_city_id: int, enemy_city_id: int) -> dict:
        """Abort blockade and recall fleet from enemy port (3-step process)."""
        return RecallBlockadeFleetAction(self).execute(
            source_city_id=source_city_id,
            enemy_city_id=enemy_city_id,
        )

    def blockade_fleet(
        self,
        from_city_id: int,
        to_city_id: int,
        island_id: int,
        fleet_units: dict[int, int],
    ) -> dict[str, Any]:
        """Send fleet to blockade a player's port."""
        return BlockadeFleetAction(self).execute(
            from_city_id=from_city_id,
            to_city_id=to_city_id,
            island_id=island_id,
            fleet_units=fleet_units,
        )

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

    # ── Diplomacy ──

    def get_diplomacy_inbox(self, city_id: int | str) -> dict[str, Any]:
        """Fetch the diplomacy advisor inbox and return parsed messages.

        Args:
            city_id: Any valid city ID for the account.

        Returns:
            Dict with "messages" (list of dicts) and "raw_html_length".
            Each message contains: id, sender, subject, body, date,
            unread, receiver_id, reply_to, is_treaty, treaty_receiver_id.
        """
        action = DiplomacyInboxAction(self)
        return action.execute(city_id=city_id)

    def send_diplomacy_message(
        self,
        receiver_id: int | str,
        content: str = "",
        reply_to: int | str | None = None,
        msg_type: int | str = 50,
    ) -> dict[str, Any]:
        """Send a message or diplomatic proposal to another player."""
        action = DiplomacySendAction(self)
        return action.execute(receiver_id=receiver_id, msg_type=msg_type, content=content, reply_to=reply_to)

    def get_piracy_state(self, city_id: int | str) -> dict:
        """Get pirate fortress state for a city."""
        from .actions.piracy import PiracyStateAction
        return PiracyStateAction(self).execute(city_id=city_id)

    def start_piracy_mission(
        self,
        city_id: int | str,
        building_level: int,
        game_account_id: str = "",
    ) -> dict:
        """Start a piracy mission from the pirate fortress."""
        from .actions.piracy import PiracyMissionAction
        return PiracyMissionAction(self).execute(
            city_id=city_id,
            building_level=building_level,
            game_account_id=game_account_id,
        )

    def convert_piracy_points(self, city_id: int | str, crew_points: int) -> dict:
        """Convert capture points to crew strength."""
        from .actions.piracy import PiracyConvertAction
        return PiracyConvertAction(self).execute(city_id=city_id, crew_points=crew_points)

    def get_piracy_highscore(self, city_id: int | str) -> dict:
        """Read pirate fortress highscore entries around the current player."""
        from .actions.piracy_targeted import PiracyHighscoreAction
        return PiracyHighscoreAction(self).execute(city_id=city_id)

    def get_piracy_raid_preview(
        self,
        source_city_id: int | str,
        destination_city_id: int | str,
        destination_island_id: int | str,
    ) -> dict:
        """Open the targeted pirate raid modal for one enemy city."""
        from .actions.piracy_targeted import PiracyRaidPreviewAction
        return PiracyRaidPreviewAction(self).execute(
            source_city_id=source_city_id,
            destination_city_id=destination_city_id,
            destination_island_id=destination_island_id,
        )

    def start_piracy_raid(
        self,
        source_city_id: int | str,
        destination_city_id: int | str,
        destination_island_id: int | str,
    ) -> dict:
        """Send a targeted pirate raid against one city."""
        from .actions.piracy_targeted import PiracyRaidAction
        return PiracyRaidAction(self).execute(
            source_city_id=source_city_id,
            destination_city_id=destination_city_id,
            destination_island_id=destination_island_id,
        )

    def get_colonization_preview(
        self,
        source_city_id: int | str,
        island_id: int | str,
        position: int,
    ) -> dict:
        """Fetch colonization requirements and form metadata for one island slot."""
        from .actions.colonization import ColonizationPreviewAction
        return ColonizationPreviewAction(self).execute(
            source_city_id=source_city_id,
            island_id=island_id,
            position=position,
        )

    def start_colonization(
        self,
        source_city_id: int | str,
        island_id: int | str,
        position: int,
        resources: dict[str, int] | None = None,
    ) -> dict:
        """Start founding a colony on one empty island slot."""
        from .actions.colonization import StartColonizationAction
        return StartColonizationAction(self).execute(
            source_city_id=source_city_id,
            island_id=island_id,
            position=position,
            resources=resources,
        )

    def get_abandon_colony_preview(self, city_id: int | str) -> dict:
        """Fetch the abandon-colony form for one owned colony."""
        from .actions.abandon_city import AbandonColonyPreviewAction
        return AbandonColonyPreviewAction(self).execute(city_id=city_id)

    def abandon_colony(
        self,
        city_id: int | str,
        *,
        game_account_id: str = "",
        captcha_timeout_sec: int = 120,
    ) -> dict:
        """Abandon one colony after solving the in-game captcha."""
        from .actions.abandon_city import AbandonColonyAction
        return AbandonColonyAction(self).execute(
            city_id=city_id,
            game_account_id=game_account_id,
            captcha_timeout_sec=captcha_timeout_sec,
        )

    def get_safehouse_state(self, city_id: int | str, position: int = 19) -> dict:
        """Get safehouse state: spy counts, active missions, training queue."""
        from .actions.spy import SpySafehouseAction
        return SpySafehouseAction(self).execute(city_id=city_id, position=position)

    def get_spy_mission_data(self, source_city_id: int | str, target_city_id: int | str, island_id: int | str) -> dict:
        """Fetch real-time risk/success data for all missions against a specific target."""
        from .actions.spy import SpyMissionDataAction
        return SpyMissionDataAction(self).execute(
            source_city_id=source_city_id, target_city_id=target_city_id, island_id=island_id,
        )

    def send_spy(
        self,
        source_city_id: int | str,
        target_city_id: int | str,
        island_id: int | str,
        mission_id: int = 1,
        agents: int = 1,
        decoys: int = 0,
    ) -> dict:
        """Send spies on a mission."""
        from .actions.spy import SpySendAction
        return SpySendAction(self).execute(
            source_city_id=source_city_id,
            target_city_id=target_city_id,
            island_id=island_id,
            mission_id=mission_id,
            agents=agents,
            decoys=decoys,
        )

    def train_spies(self, city_id: int | str, count: int = 1, position: int = 19) -> dict:
        """Train replacement spies in the safehouse."""
        from .actions.spy import SpyTrainAction
        return SpyTrainAction(self).execute(city_id=city_id, count=count, position=position)

    def get_spy_assignment_state(
        self,
        source_city_id: int | str,
        target_city_id: int | str,
        position: int = 19,
    ) -> dict:
        """Open the internal spy mission screen for an infiltrated group."""
        from .actions.spy import SpyMissionAssignmentAction
        return SpyMissionAssignmentAction(self).execute(
            source_city_id=source_city_id,
            target_city_id=target_city_id,
            position=position,
        )

    def execute_spy_mission(
        self,
        source_city_id: int | str,
        target_city_id: int | str,
        mission_id: int,
        agents: int,
        decoys: int = 0,
        position: int = 19,
        spy_id: int | str | None = None,
        island_id: int | str | None = None,
    ) -> dict:
        """Execute an internal espionage mission from an infiltrated group."""
        from .actions.spy import SpyExecuteMissionAction
        return SpyExecuteMissionAction(self).execute(
            source_city_id=source_city_id,
            target_city_id=target_city_id,
            mission_id=mission_id,
            agents=agents,
            decoys=decoys,
            position=position,
            spy_id=spy_id,
            island_id=island_id,
        )

    def retreat_spy_group(
        self,
        source_city_id: int | str,
        target_city_id: int | str,
        position: int = 19,
        spy_id: int | str | None = None,
    ) -> dict:
        """Retreat an infiltrated spy group from the target city."""
        from .actions.spy import SpyRetreatAction
        return SpyRetreatAction(self).execute(
            source_city_id=source_city_id,
            target_city_id=target_city_id,
            position=position,
            spy_id=spy_id,
        )

    def get_spy_reports(
        self, city_id: int | str, position: int = 19, tab: str = "tabReports"
    ) -> list:
        """Fetch and parse spy reports from the safehouse."""
        from .actions.spy import SpyReportsAction
        return SpyReportsAction(self).execute(city_id=city_id, position=position, tab=tab)

    def delete_spy_report(
        self, city_id: int | str, report_id: int | str, position: int = 19
    ) -> dict:
        """Delete a spy report."""
        from .actions.spy import SpyDeleteReportAction
        return SpyDeleteReportAction(self).execute(
            city_id=city_id, report_id=report_id, position=position
        )

    def accept_treaty(self, receiver_id: int | str) -> dict[str, Any]:
        """Accept a cultural treaty offer.

        Args:
            receiver_id: Player ID who sent the treaty offer.

        Returns:
            Parsed AJAX response.
        """
        action = DiplomacySendAction(self)
        return action.execute(receiver_id=receiver_id, msg_type=79)

    def decline_treaty(self, receiver_id: int | str) -> dict[str, Any]:
        """Decline a cultural treaty offer.

        Args:
            receiver_id: Player ID who sent the treaty offer.

        Returns:
            Parsed AJAX response.
        """
        action = DiplomacySendAction(self)
        return action.execute(receiver_id=receiver_id, msg_type=80)

    # ── Barbarian Village ──

    def get_barbarian_state(self, island_id: int | str) -> dict[str, Any]:
        """Fetch barbarian village state from the island view.

        Returns the ``barbarians`` sub-dict from the parsed island data:
            level, gold, resources (list), total_resources, troops,
            destroyed (bool), cooldown_seconds.
        Returns {} if the island has no barbarian data.
        """
        island = self.fetch_island_by_id(island_id)
        return island.get("barbarians") or {}

    def attack_barbarian_village(
        self,
        from_city_id: int | str,
        island_id: int | str,
        troops: dict[int, int],
        transporters: int | None = None,
        extra_ships: int = 0,
    ) -> dict[str, Any]:
        """Send troops to attack the barbarian village on an island.

        Use the same method for the loot phase — when barbarians.destroyed==True
        the "attack" just collects remaining resources.

        Args:
            from_city_id: Departing city.
            island_id: Target island.
            troops: {unit_id: quantity} — use get_barbarian_schematic() to build.
            transporters: Override ship count. Auto-calculated if None.
            extra_ships: Extra ships beyond the minimum.
        """
        action = AttackBarbarianVillageAction(self)
        return action.execute(
            from_city_id=from_city_id,
            island_id=island_id,
            troops=troops,
            transporters=transporters,
            extra_ships=extra_ships,
        )

    def get_barbarian_schematic(self, barb_level: int, loot: bool = False) -> dict[int, int]:
        """Return the standard troop schematic for a given barbarian level."""
        table = LOOT_SCHEMATICS if loot else ATTACK_SCHEMATICS
        return get_schematic(barb_level, table)

    def calculate_barbarian_ships(self, troops: dict[int, int], extra: int = 0) -> int:
        """Calculate minimum cargo ships needed for a barbarian attack."""
        return calculate_transporters(troops, extra)

    # ── Market ──

    def create_market_offer(
        self,
        city_id: int,
        branchoffice_pos: int,
        resource_idx: int,
        amount: int,
        unit_price: int,
        offer_mode: str = "add",
    ) -> dict[str, Any]:
        """Create (or update) a sell offer on the player's own Branch Office.

        Args:
            city_id: Seller's city ID.
            branchoffice_pos: Branch Office building slot in the city.
            resource_idx: 0=wood, 1=wine, 2=marble, 3=crystal, 4=sulfur.
            amount: Number of units to offer for sale.
            unit_price: Price per unit in gold.

        Returns:
            Parsed AJAX response.
        """
        action = CreateOfferAction(self)
        return action.execute(
            city_id=city_id,
            branchoffice_pos=branchoffice_pos,
            resource_idx=resource_idx,
            amount=amount,
            unit_price=unit_price,
            offer_mode=offer_mode,
        )

    def get_market_offers(
        self,
        buyer_city_id: int,
        buyer_branchoffice_pos: int,
        resource_idx: int,
    ) -> list[dict]:
        """Fetch all sell offers for a resource at a Branch Office, sorted cheapest-first."""
        return GetOffersAction(self).execute(
            buyer_city_id=buyer_city_id,
            buyer_branchoffice_pos=buyer_branchoffice_pos,
            resource_idx=resource_idx,
        )

    def buy_market_offer(
        self,
        buyer_city_id: int,
        buyer_branchoffice_pos: int,
        seller_city_id: int,
        seller_branchoffice_pos: int,
        resource_idx: int,
        amount: int,
    ) -> dict[str, Any]:
        """Buy resources from a specific seller's Branch Office.

        Internally scrapes the branch office listing, loads the takeOffer page,
        and posts buyGoodsAtAnotherBranchOffice.

        Args:
            buyer_city_id: City ID where purchased goods will be delivered.
            buyer_branchoffice_pos: Buyer's Branch Office slot (used in the POST).
            seller_city_id: City ID where the sell offer is posted.
            seller_branchoffice_pos: Seller's Branch Office slot (for takeOffer GET).
            resource_idx: 0=wood, 1=wine, 2=marble, 3=crystal, 4=sulfur.
            amount: Amount to buy.

        Returns:
            Parsed AJAX response.
        """
        action = BuyAction(self)
        return action.execute(
            buyer_city_id=buyer_city_id,
            buyer_branchoffice_pos=buyer_branchoffice_pos,
            seller_city_id=seller_city_id,
            seller_branchoffice_pos=seller_branchoffice_pos,
            resource_idx=resource_idx,
            amount=amount,
        )

    def sell_to_offer(
        self,
        city_id: int,
        branchoffice_pos: int,
        destination_city_id: int,
        resource_idx: int,
        amount: int,
        price: int,
        player_name: str = "",
        dest_city_name: str = "",
    ) -> dict[str, Any]:
        """Sell resources to an existing buy offer from another player.

        Args:
            city_id: Seller's city ID.
            branchoffice_pos: Seller's Branch Office slot.
            destination_city_id: Buyer's city ID (where goods will be sent).
            resource_idx: 0=wood, 1=wine, 2=marble, 3=crystal, 4=sulfur.
            amount: Units to sell.
            price: Price per unit in gold.
            player_name: Buyer's player name (from offer listing).
            dest_city_name: Buyer's city name (from offer listing).

        Returns:
            Parsed AJAX response.
        """
        action = SellAction(self)
        return action.execute(
            city_id=city_id,
            branchoffice_pos=branchoffice_pos,
            destination_city_id=destination_city_id,
            resource_idx=resource_idx,
            amount=amount,
            price=price,
            player_name=player_name,
            dest_city_name=dest_city_name,
        )

    # ── Black Market Methods ──

    def get_black_market_state(
        self,
        city_id: int,
        position: int,
        unit_id: int | None = None,
        offer_resource: int = 5,
    ) -> dict[str, Any]:
        """Get Black Market building state: available units, price limits, offer slots."""
        return GetBlackMarketStateAction(self).execute(
            city_id=city_id,
            position=position,
            unit_id=unit_id,
            offer_resource=offer_resource,
        )

    def add_black_market_offer(
        self,
        city_id: int,
        position: int,
        unit_id: int,
        amount: int,
        unit_price: int,
        offer_resource: int = 5,  # 5 = gold
    ) -> dict[str, Any]:
        """List units for sale on the Black Market."""
        return AddBlackMarketOfferAction(self).execute(
            city_id=city_id,
            position=position,
            unit_id=unit_id,
            amount=amount,
            unit_price=unit_price,
            offer_resource=offer_resource,
        )

    def get_my_black_market_offers(self, city_id: int, position: int) -> list[dict]:
        """Fetch seller's current active Black Market offers."""
        return GetMyBlackMarketOffersAction(self).execute(city_id=city_id, position=position)

    def cancel_black_market_offer(self, city_id: int, position: int, offer_id: int) -> dict[str, Any]:
        """Cancel an active Black Market offer."""
        return CancelBlackMarketOfferAction(self).execute(city_id=city_id, position=position, offer_id=offer_id)

    def get_available_unit_offers(
        self,
        buyer_city_id: int,
        bo_position: int,
        unit_category: int = 0,
        market_range: int | None = None,
    ) -> list[dict]:
        """Fetch available unit offers from other players via Branch Office."""
        return GetAvailableUnitOffersAction(self).execute(
            buyer_city_id=buyer_city_id,
            bo_position=bo_position,
            unit_category=unit_category,
            market_range=market_range,
        )

    def buy_units_black_market(
        self,
        buyer_city_id: int,
        bo_position: int,
        seller_city_id: int,
        seller_avatar: str,
        seller_city_name: str,
        unit_id: int,
        quantity: int,
        unit_price: int,
        unit_category: int = UNIT_TYPE_MARITIME,
        num_transporters: int = 1,
        offer_key: int | None = None,
    ) -> dict[str, Any]:
        """Buy units from another player's Black Market offer."""
        return BuyUnitsAction(self).execute(
            buyer_city_id=buyer_city_id,
            bo_position=bo_position,
            seller_city_id=seller_city_id,
            seller_avatar=seller_avatar,
            seller_city_name=seller_city_name,
            unit_id=unit_id,
            quantity=quantity,
            unit_price=unit_price,
            unit_category=unit_category,
            num_transporters=num_transporters,
            offer_key=offer_key,
        )

    def get_unit_offer_details(
        self,
        buyer_city_id: int,
        bo_position: int,
        seller_city_id: int,
        unit_category: int,
    ) -> dict[str, Any]:
        """Fetch takeOffer page details before buying units."""
        return BuyUnitsAction(self).get_takeOffer_details(
            buyer_city_id=buyer_city_id,
            bo_position=bo_position,
            seller_city_id=seller_city_id,
            unit_category=unit_category,
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

        # Default timeout defensivo: sem timeout explícito, requests fica bloqueado
        # indefinidamente se o servidor não responde. Isso já causou jobs zumbi
        # (48h+ marcados como "running" sem heartbeat) em produção.
        kwargs.setdefault("timeout", 60)

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

    def _ajax_get(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a GET AJAX request to the game server and parse the JSON response.

        Same contract as _ajax but uses HTTP GET (query params) instead of POST.
        Used for actions like UpgradeExistingBuilding that the game triggers via GET.

        Args:
            action: AJAX action string (used for logging).
            params: Full parameter dict including actionRequest and ajax=1.

        Returns:
            Parsed response dictionary from AjaxResponseParser.
        """
        logger.debug("AJAX GET: %s", action)
        logger.info("AJAX GET params: %s", {k: v for k, v in params.items() if k != "actionRequest"})

        resp = self._request("GET", self._server_url, params=params, headers=GAME_AJAX_HEADERS)

        try:
            data = resp.json()
        except ValueError:
            raise ActionError(f"Invalid JSON in AJAX GET response for {action}", action=action)

        logger.info("AJAX GET raw response for %s: %s", action, str(data)[:600])
        parsed = self.ajax_parser.parse_response(data)

        if parsed.get("new_action_request"):
            self._action_request = parsed["new_action_request"]
            logger.debug("actionRequest updated from AJAX GET response: %s...", self._action_request[:8])

        if parsed.get("reload"):
            raise ActionError(
                f"Server rejected action '{action}' with reload directive",
                action=action,
            )

        if parsed["errors"]:
            raise ActionError(
                f"Server errors for action {action}: {parsed['errors']}",
                action=action,
                server_errors=parsed["errors"],
            )

        return parsed

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
        logger.info("AJAX POST params: %s", {k: v for k, v in params.items() if k != "actionRequest"})

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

        logger.info("AJAX raw response for %s: %s", action, str(data)[:600])
        parsed = self.ajax_parser.parse_response(data)

        # Persist any fresh actionRequest the server embedded in the response
        if parsed.get("new_action_request"):
            self._action_request = parsed["new_action_request"]
            logger.debug("actionRequest updated from AJAX response: %s...", self._action_request[:8])

        # Server-side reload = action was rejected (e.g. stale AR, wrong city context)
        if parsed.get("reload"):
            raise ActionError(
                f"Server rejected action '{action}' with reload directive",
                action=action,
            )

        # Check for explicit error messages
        if parsed["errors"]:
            raise ActionError(
                f"Server errors for action {action}: {parsed['errors']}",
                action=action,
                server_errors=parsed["errors"],
            )

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
