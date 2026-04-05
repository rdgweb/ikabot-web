"""Snapshot service — collects and pushes account data to hub."""

import logging
from typing import Any

from core.hub_client import HubClient
from game_client.client import GameClient
from game_client.models import City

logger = logging.getLogger(__name__)


class SnapshotService:
    """
    Collects game data for an account and pushes a snapshot to the hub.
    Called after login or periodically by scan runners.
    """

    def __init__(self, hub: HubClient):
        self.hub = hub

    def collect_and_push(self, account_id: str, client: GameClient) -> dict:
        """
        Collect all game data and push snapshot to hub.

        Args:
            account_id: UUID of the account
            client: Authenticated GameClient instance

        Returns:
            Hub API response dict
        """
        logger.info(f"Collecting snapshot for account {account_id}")

        try:
            snapshot_data = self._collect(client)
            result = self.hub.update_snapshot(account_id, snapshot_data)
            logger.info(f"Snapshot pushed for account {account_id}")
            return result
        except Exception as e:
            logger.error(f"Snapshot collection failed for {account_id}: {e}")
            raise

    def _collect(self, client: GameClient) -> dict:
        """Collect game data from client."""
        data: dict[str, Any] = {
            "base_snapshot": {},
            "cities": [],
            "military": {},
        }

        # Collect cities
        try:
            cities = client.get_cities()
            data["cities"] = [c.to_dict() for c in cities]

            # Aggregate base snapshot from city data
            total_gold = sum(c.resources.gold for c in cities)
            total_pop = sum(c.population for c in cities)
            data["base_snapshot"] = {
                "gold": total_gold,
                "population": total_pop,
                "city_count": len(cities),
                "total_resources": sum(c.resources.total() for c in cities),
            }
        except Exception as e:
            logger.warning(f"Failed to collect cities: {e}")

        # TODO: Collect military units via MilitaryAdvisor
        # TODO: Collect research status via ResearchAdvisor

        return data
