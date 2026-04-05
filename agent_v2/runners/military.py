"""
Military runners — troop/fleet training and combat operations.

Action codes:
    15  train_troops
     7  train_fleet
    11  send_troops
    12  attack
    13  pillage
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


@register_runner(15)
class TrainTroopsRunner(BaseRunner):
    """Queue land-unit training in a city barracks.

    Inputs:
        city_id    — city with the barracks
        unit_type  — unit identifier (e.g. ``hoplite``, ``steam_giant``)
        quantity   — number of units to train
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Training troops for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.train_troops(city_id, unit_type, quantity)
            # city_id   = inputs["city_id"]
            # unit_type = inputs["unit_type"]
            # quantity  = inputs["quantity"]
            # client.train_troops(city_id, unit_type, quantity)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Troop training queued")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Train troops failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(7)
class TrainFleetRunner(BaseRunner):
    """Queue naval-unit training in a city shipyard.

    Inputs:
        city_id    — city with the shipyard
        ship_type  — ship identifier (e.g. ``ram_ship``, ``catapult_ship``)
        quantity   — number of ships to build
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Training fleet for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.train_fleet(city_id, ship_type, quantity)
            # city_id   = inputs["city_id"]
            # ship_type = inputs["ship_type"]
            # quantity  = inputs["quantity"]
            # client.train_fleet(city_id, ship_type, quantity)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Fleet training queued")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Train fleet failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(11)
class SendTroopsRunner(BaseRunner):
    """Send troops from one city to another (reinforcement or garrison).

    Inputs:
        source_city_id  — origin city
        target_city_id  — destination city
        units           — dict of {unit_type: quantity}
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Sending troops for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.send_troops(source, target, units)
            # source_city_id = inputs["source_city_id"]
            # target_city_id = inputs["target_city_id"]
            # units          = inputs["units"]
            # client.send_troops(source_city_id, target_city_id, units)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Troops dispatched")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Send troops failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(12)
class AttackRunner(BaseRunner):
    """Launch an attack against an enemy city.

    Inputs:
        source_city_id  — origin city
        target_city_id  — enemy city to attack
        units           — dict of {unit_type: quantity}
        ships           — dict of {ship_type: quantity} (optional)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Launching attack for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.attack(source, target, units, ships)
            # source_city_id = inputs["source_city_id"]
            # target_city_id = inputs["target_city_id"]
            # units          = inputs["units"]
            # ships          = inputs.get("ships", {})
            # client.attack(source_city_id, target_city_id, units, ships)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Attack launched")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Attack failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(13)
class PillageRunner(BaseRunner):
    """Launch a pillage raid against an enemy city.

    Similar to attack but optimised for resource capture.

    Inputs:
        source_city_id  — origin city
        target_city_id  — enemy city to pillage
        units           — dict of {unit_type: quantity}
        ships           — dict of {ship_type: quantity} (optional)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Launching pillage for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.pillage(source, target, units, ships)
            # source_city_id = inputs["source_city_id"]
            # target_city_id = inputs["target_city_id"]
            # units          = inputs["units"]
            # ships          = inputs.get("ships", {})
            # client.pillage(source_city_id, target_city_id, units, ships)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Pillage launched")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Pillage failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
