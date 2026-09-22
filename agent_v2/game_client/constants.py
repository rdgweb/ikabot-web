"""Game constants, URLs, building types, and request templates."""

from enum import IntEnum

# ── Gameforge Lobby URLs ──

LOBBY_URL = "https://lobby.ikariam.gameforge.com"
LOBBY_CONFIG_URL = f"{LOBBY_URL}/config/configuration.js"
LOBBY_LOGIN_URL = "https://spark-web.gameforge.com/api/v2/authProviders/mauth/sessions"
LOBBY_SERVERS_URL = f"{LOBBY_URL}/api/servers"
LOBBY_ACCOUNTS_URL = f"{LOBBY_URL}/api/users/me/accounts"

# Game server URL pattern: https://s{server_id}-{lang}.ikariam.gameforge.com/index.php
GAME_URL_TEMPLATE = "https://s{server_id}-{lang}.ikariam.gameforge.com/index.php"

# ── User Agent Rotation Pool ──

# User agents MUST be in the ikabotapi SupportedUserAgents.json list for
# blackbox token generation to work (ikabotapi's TokenGenerator otherwise
# silently falls back to an unrelated random UA — see
# ikabotapi_overrides/README.md, which is the file that actually keeps the
# two lists in sync in this deployment). Keep this list byte-identical to
# ikabotapi_overrides/SupportedUserAgents.json.
#
# Chrome/Chromium only, Windows and Linux only, complete (non-truncated)
# strings, recent versions (checked 2026-09-21: Chrome stable was 153) —
# see N-37.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
]

# ── Resource Types ──

class ResourceType(IntEnum):
    WOOD = 0
    WINE = 1
    MARBLE = 2
    CRYSTAL = 3
    SULFUR = 4


# ── Building Types ──
# Maps building name → internal game building ID

BUILDING_TYPES: dict[str, int] = {
    "townHall": 0,
    "academy": 1,
    "warehouse": 2,
    "tavern": 3,
    "palace": 4,
    "palaceColony": 5,
    "museum": 6,
    "port": 7,
    "shipyard": 8,
    "barracks": 9,
    "wall": 10,
    "embassy": 11,
    "branchOffice": 12,
    "marketplace": 13,
    "workshop": 14,
    "safehouse": 15,
    "forester": 16,        # Forester's House (resource)
    "glassblowing": 17,
    "alchemist": 18,
    "winePress": 19,
    "stonemason": 20,
    "architect": 21,
    "optician": 22,
    "fireworker": 23,
    "vineyard": 24,
    "quarry": 25,
    "crystalMine": 26,
    "sulfurPit": 27,
    "dump": 28,
    "reducePiracy": 29,    # Piracy fortress
    "pirateFortress": 30,
    "blackMarket": 31,
    "marineChartArchive": 32,
    "temple": 33,
}

# ── AJAX Action IDs ──
# These correspond to the 'action' param in game AJAX requests

class ActionID:
    """Known Ikariam AJAX action identifiers."""
    # City / Building
    CITY_VIEW = "CityScreen"
    ISLAND_VIEW = "IslandScreen"
    WORLD_VIEW = "WorldScreen"
    # Verified from live game button hrefs on 2026-04-17 (HAVIT / s78-br)
    BUILD = "BuildNewBuilding"
    UPGRADE_BUILDING = "UpgradeExistingBuilding"
    DEMOLISH = "CityBuilding&function=demolish"
    CHANGE_PRODUCTION = "CityScreen&function=changeProduction"

    # Military
    BARRACKS_VIEW = "BarracksScreen"
    TRAIN_UNITS = "CityScreen&function=trainUnits"
    DEPLOY_FLEET = "FleetAction&function=deployFleet"
    SEND_TROOPS = "TransportAction&function=deployArmy"

    # Resources / Trade
    DONATE = "IslandScreen&function=donate"
    SEND_RESOURCES = "TransportAction&function=loadTransporters"
    # Marketplace — verified from Ikariam AJAX traffic (ikabot upstream)
    # Create or update own sell offer on Branch Office
    MARKETPLACE_UPDATE_OFFERS = "CityScreen&function=updateOffers"
    # Buy goods from another player's Branch Office offer
    MARKETPLACE_BUY = "transportOperations&function=buyGoodsAtAnotherBranchOffice"
    # Sell goods to another player's buy offer
    MARKETPLACE_SELL = "transportOperations&function=sellGoodsAtAnotherBranchOffice"

    # General
    CHANGE_CITY = "HeaderAction&function=changeCity"
    PIRACY = "PiracyScreen"
    RESEARCH_ADVISOR = "ResearchAdvisor"
    MILITARY_ADVISOR = "MilitaryAdvisor"
    TOWN_ADVISOR = "TownAdvisor"


# ── Request Header Templates ──

LOBBY_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": LOBBY_URL,
    "Referer": f"{LOBBY_URL}/",
}

GAME_AJAX_HEADERS: dict[str, str] = {
    "Accept": "text/plain, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}

# ── Misc Constants ──

# Max retries for transient failures before raising
MAX_RETRIES = 3

# Delay between consecutive game requests, sampled from a log-normal distribution
# (N-43): a uniform draw has a flat, bounded signature (never below MIN, never above
# MAX, equal frequency everywhere) that's easy for anti-bot request-timing analysis
# to spot. A real person's think time is skewed — usually quick, occasionally slow —
# which log-normal reproduces. MEDIAN/SIGMA shape the draw; MIN/MAX are the hard
# clamp applied after sampling (SIGMA=0.5 keeps ~94% of draws above MIN and well
# under 1% above MAX, so the clamp mostly leaves the tail shape intact).
REQUEST_DELAY_MEDIAN = 1.3
REQUEST_DELAY_SIGMA = 0.5
REQUEST_DELAY_MIN = 0.6
REQUEST_DELAY_MAX = 8.0

# Session validity check: if last request was more than N seconds ago, revalidate
SESSION_REVALIDATE_AFTER = 300
