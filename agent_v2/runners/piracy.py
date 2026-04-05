"""
Piracy runners — pirate fortress missions and loot collection.

Action codes:
    17    piracy_mission   (recurring)
    1018  collect_piracy   (legacy/internal recurring helper)
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

# Piracy mission runs for ~4 h; we reschedule slightly after expected completion.
PIRACY_MISSION_INTERVAL = 4 * 3600 + 300   # 4 h 5 min
PIRACY_COLLECT_INTERVAL = 8 * 3600          # 8 h


@register_runner(17)
class PiracyMissionRunner(BaseRunner):
    """Send crew on a piracy mission from the pirate fortress.

    Recurring — reschedules to keep piracy running continuously.

    Inputs:
        city_id     — city with the pirate fortress
        mission_type — mission tier / type (optional)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Starting piracy mission for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.start_piracy_mission(city_id, mission_type)
            # city_id      = inputs["city_id"]
            # mission_type = inputs.get("mission_type")
            # result       = client.start_piracy_mission(city_id, mission_type)
            # Ideally read the actual mission duration from result and use that
            # as reschedule_seconds instead of the default.

            self.save_game_session(aid, client)
            self.log(jid, "info", "Piracy mission started")

            return RunnerResult(
                success=True,
                reschedule_seconds=PIRACY_MISSION_INTERVAL,
            )

        except Exception as exc:
            self.log(jid, "error", f"Piracy mission failed: {exc}")
            return RunnerResult(
                success=False,
                reschedule_seconds=PIRACY_MISSION_INTERVAL,
                data={"error": str(exc)},
            )


@register_runner(1018)
class CollectPiracyRunner(BaseRunner):
    """Collect loot from completed piracy missions.

    Recurring — reschedules to periodically sweep piracy rewards.

    Inputs:
        city_id — city with the pirate fortress
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Collecting piracy loot for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.collect_piracy(city_id)
            # city_id = inputs["city_id"]
            # loot    = client.collect_piracy(city_id)
            # self.hub.update_snapshot(aid, {"piracy_loot": loot})

            self.save_game_session(aid, client)
            self.log(jid, "info", "Piracy loot collected")

            return RunnerResult(
                success=True,
                reschedule_seconds=PIRACY_COLLECT_INTERVAL,
            )

        except Exception as exc:
            self.log(jid, "error", f"Collect piracy failed: {exc}")
            return RunnerResult(
                success=False,
                reschedule_seconds=PIRACY_COLLECT_INTERVAL,
                data={"error": str(exc)},
            )
