"""HTML parser for Ikariam game pages.

Ikariam serves most views as server-rendered HTML. This parser extracts
structured data (cities, buildings, resources, tokens) from game pages.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any

from ..models.building import Building
from ..models.city import City, Resources

logger = logging.getLogger(__name__)


class GamePageParser:
    """Parses Ikariam HTML pages into structured data models."""

    def parse_city_view(self, html: str) -> City:
        """Parse a city view page into a City model.

        Args:
            html: Raw HTML of the city view page.

        Returns:
            Populated City dataclass.
        """
        # TODO: Implement full city view parsing
        # The city page contains:
        #   - City name and ID in various JS variables and HTML elements
        #   - Building positions (div elements with position-based IDs)
        #   - Resource counters in the header bar
        #   - Population info
        logger.debug("Parsing city view HTML")

        city_id = self._extract_city_id(html)
        city_name = self._extract_city_name(html)
        resources = self.extract_resources(html)
        buildings = self._extract_buildings(html)

        # TODO: Extract island_id, coordinates, level, population from HTML
        return City(
            id=city_id,
            name=city_name,
            island_id=0,       # TODO: extract from page
            x=0,               # TODO: extract from page
            y=0,               # TODO: extract from page
            level=0,           # TODO: extract from page
            population=0,      # TODO: extract from page
            buildings=buildings,
            resources=resources,
        )

    def parse_island_view(self, html: str) -> dict[str, Any]:
        """Parse an island view page.

        Args:
            html: Raw HTML of the island view page.

        Returns:
            Dictionary with island data: cities, wonder, resource type, etc.
        """
        # TODO: Implement island view parsing
        # The island page shows:
        #   - All cities on the island (with player names)
        #   - Island wonder type and level
        #   - Resource type (luxury good)
        #   - Wood production level (forester)
        logger.debug("Parsing island view HTML")

        return {
            "island_id": 0,        # TODO: extract
            "cities": [],          # TODO: list of city summaries on island
            "wonder_type": "",     # TODO: extract wonder name
            "wonder_level": 0,     # TODO: extract
            "resource_type": "",   # TODO: luxury resource type
        }

    def parse_military_view(self, html: str) -> dict[str, Any]:
        """Parse a military advisor or barracks view.

        Args:
            html: Raw HTML of the military page.

        Returns:
            Dictionary with military information (units, fleets, movements).
        """
        # TODO: Implement military view parsing
        # Shows troop counts, fleet status, and ongoing movements
        logger.debug("Parsing military view HTML")

        return {
            "units": [],       # TODO: list of MilitaryUnit
            "fleets": [],      # TODO: list of Fleet
            "movements": [],   # TODO: ongoing troop/fleet movements
        }

    def extract_action_request(self, html: str) -> str:
        """Extract the actionRequest token from a game page.

        The actionRequest token is required for all AJAX POST requests.
        It changes with each page load and acts as a CSRF token.

        Args:
            html: Raw HTML of any game page.

        Returns:
            The actionRequest token string.
        """
        # TODO: Implement actionRequest extraction
        # The token appears in the HTML as a JS variable or hidden input:
        #   actionRequest = "xxxx-xxxx-xxxx-xxxx..."
        #   <input type="hidden" name="actionRequest" value="..."/>
        match = re.search(r'actionRequest\s*[=:]\s*["\']([a-f0-9-]+)["\']', html)
        if match:
            token = match.group(1)
            logger.debug(f"Extracted actionRequest token: {token[:16]}...")
            return token

        # Fallback: try hidden input pattern
        match = re.search(r'name=["\']actionRequest["\']\s+value=["\']([^"\']+)["\']', html)
        if match:
            return match.group(1)

        logger.warning("Could not extract actionRequest token from page")
        return ""

    def extract_resources(self, html: str) -> Resources:
        """Extract current resource amounts from a game page header.

        Args:
            html: Raw HTML of any game page (resources are in the global header).

        Returns:
            Populated Resources dataclass.
        """
        # TODO: Implement resource extraction from the header bar
        # Resources are typically shown in the header:
        #   <span id="js_GlobalMenu_wood">12345</span>
        #   <span id="js_GlobalMenu_wine">6789</span>
        #   etc.
        logger.debug("Extracting resources from page header")

        resource_map = {
            "wood": 0,
            "wine": 0,
            "marble": 0,
            "crystal": 0,
            "sulfur": 0,
            "gold": 0,
        }

        for resource_name in resource_map:
            # Pattern: id="js_GlobalMenu_{resource}">{amount}</span>
            pattern = rf'id=["\']js_GlobalMenu_{resource_name}["\'][^>]*>([0-9,.\s]+)<'
            match = re.search(pattern, html)
            if match:
                raw_value = match.group(1).replace(",", "").replace(".", "").strip()
                try:
                    resource_map[resource_name] = int(raw_value)
                except ValueError:
                    logger.warning(f"Could not parse resource value for {resource_name}: {raw_value}")

        return Resources(**resource_map)

    # ── Private Helpers ──

    def _extract_city_id(self, html: str) -> int:
        """Extract current city ID from page."""
        # TODO: Implement — look for currentCityId in JS variables
        match = re.search(r'currentCityId\s*[=:]\s*(\d+)', html)
        return int(match.group(1)) if match else 0

    def _extract_city_name(self, html: str) -> str:
        """Extract current city name from page."""
        # TODO: Implement — look for city name in header or JS
        match = re.search(r'id=["\']js_cityName["\'][^>]*>([^<]+)<', html)
        return match.group(1).strip() if match else ""

    def _extract_buildings(self, html: str) -> list[Building]:
        """Extract building data from city view page.

        Each building position (0-N) has corresponding HTML elements with
        building type and level information.
        """
        # TODO: Implement building extraction
        # City buildings are in position-numbered containers:
        #   <li class="buildingSlot" id="position{N}">
        #     <div class="building {buildingType}" ...>
        #       <span class="level">{level}</span>
        #     </div>
        #   </li>
        buildings: list[Building] = []

        # Pattern to match building positions with type and level
        pattern = re.compile(
            r'id=["\']position(\d+)["\'].*?'
            r'class=["\'][^"\']*building\s+(\w+)[^"\']*["\'].*?'
            r'level["\'][^>]*>(\d+)<',
            re.DOTALL,
        )

        for match in pattern.finditer(html):
            position = int(match.group(1))
            building_type = match.group(2)
            level = int(match.group(3))

            # TODO: Check if building is currently upgrading
            is_upgrading = False  # TODO: detect from HTML

            buildings.append(Building(
                position=position,
                building_type=building_type,
                level=level,
                is_upgrading=is_upgrading,
            ))

        logger.debug(f"Extracted {len(buildings)} buildings from city view")
        return buildings
