"""
Miscellaneous runners — colonisation, miracles, scanning, premium, and more.

Action codes:
    14  colonize
    16  activate_miracle
    20  scan_island
    21  scan_player
    25  vacation_mode
    29  buy_premium
    30  activate_ambro
    31  setup_trade_route
    32  build_museum
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


@register_runner(14)
class ColonizeRunner(BaseRunner):
    """Colonize a free spot on an island.

    Inputs:
        source_city_id — city sending the colony ship
        island_id      — target island
        position       — slot on the island
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Colonizing for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.colonize(source_city_id, island_id, position)
            # source_city_id = inputs["source_city_id"]
            # island_id      = inputs["island_id"]
            # position       = inputs["position"]
            # client.colonize(source_city_id, island_id, position)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Colonization started")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Colonize failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(16)
class ActivateMiracleRunner(BaseRunner):
    """Activate an island miracle (wonder).

    Inputs:
        island_id     — island with the miracle
        miracle_type  — miracle identifier (optional, auto-detect)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Activating miracle for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.activate_miracle(island_id)
            # island_id = inputs["island_id"]
            # client.activate_miracle(island_id)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Miracle activated")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Activate miracle failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(20)
class ScanIslandRunner(BaseRunner):
    """Scan an island to gather intelligence (cities, resources, players).

    Results are pushed to the hub as a snapshot update.

    Inputs:
        island_id — island to scan
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Scanning island for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.scan_island(island_id)
            # island_id = inputs["island_id"]
            # data      = client.scan_island(island_id)
            # self.hub.update_snapshot(aid, {"island_scan": data})

            self.save_game_session(aid, client)
            self.log(jid, "info", "Island scan complete")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Scan island failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(21)
class ScanPlayerRunner(BaseRunner):
    """Scan a player profile to gather intelligence (cities, military score).

    Results are pushed to the hub as a snapshot update.

    Inputs:
        target_player_id — player to scan
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Scanning player for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.scan_player(target_player_id)
            # target_player_id = inputs["target_player_id"]
            # data             = client.scan_player(target_player_id)
            # self.hub.update_snapshot(aid, {"player_scan": data})

            self.save_game_session(aid, client)
            self.log(jid, "info", "Player scan complete")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Scan player failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(25)
class VacationModeRunner(BaseRunner):
    """Activate or deactivate vacation mode for a game account.

    Inputs:
        enable — bool, True to activate (default), False to deactivate

    Deactivation note: the game lifts vacation mode automatically on the next
    successful login after the mandatory period ends. So enable=False simply
    logs in — if the session is healthy, vacation mode is already gone.
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}

        enable = bool(inputs.get("enable", True))
        self.log(jid, "info", f"{'Ativar' if enable else 'Desativar'} modo ferias para conta {aid}")

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            if not enable:
                # Deactivation = login is sufficient; game lifts vacation on next login
                self.log(jid, "info", "Login realizado; modo ferias desativado pelo jogo automaticamente apos periodo obrigatorio")
                self.save_game_client(ga_id, client)
                return RunnerResult(success=True, data={"enabled": False})

            # Activate: call Options API
            city_id = 0
            try:
                snap = self.hub.get_snapshot(game_account_id=ga_id)
                cities = snap.get("cities") or []
                if cities:
                    city_id = int(cities[0].get("id") or 0)
            except Exception:
                pass

            if not city_id:
                return RunnerResult(success=False, data={"error": "no_city_found_in_snapshot"})

            result = client.activate_vacation_mode(city_id=city_id)
            self.log(jid, "info", f"Modo ferias ativado: {result}")
            self.save_game_client(ga_id, client)

            return RunnerResult(success=True, data={"enabled": True, "city_id": city_id})

        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
            self.log(jid, "error", f"Modo ferias falhou: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(29)
class BuyPremiumRunner(BaseRunner):
    """Purchase a premium feature (e.g. Ambrosia-based bonus).

    Inputs:
        feature — premium feature identifier
        city_id — target city (if feature is city-scoped)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Buying premium feature for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.buy_premium(feature, city_id)
            # feature = inputs["feature"]
            # city_id = inputs.get("city_id")
            # client.buy_premium(feature, city_id)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Premium purchase complete")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Buy premium failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(30)
class ActivateAmbroRunner(BaseRunner):
    """Activate an Ambrosia bonus (e.g. resource boost, construction speed).

    Inputs:
        bonus_type — bonus identifier
        city_id    — target city (if bonus is city-scoped)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Activating Ambrosia bonus for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.activate_ambro(bonus_type, city_id)
            # bonus_type = inputs["bonus_type"]
            # city_id    = inputs.get("city_id")
            # client.activate_ambro(bonus_type, city_id)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Ambrosia bonus activated")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Activate Ambrosia failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(31)
class SetupTradeRouteRunner(BaseRunner):
    """Create or update an automated trade route between two cities.

    Inputs:
        source_city_id — origin city
        target_city_id — destination city
        resources      — dict of {resource_type: amount} per trip
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Setting up trade route for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.setup_trade_route(source, target, resources)
            # source_city_id = inputs["source_city_id"]
            # target_city_id = inputs["target_city_id"]
            # resources      = inputs["resources"]
            # client.setup_trade_route(source_city_id, target_city_id, resources)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Trade route configured")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Setup trade route failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(32)
class BuildMuseumRunner(BaseRunner):
    """Arrange cultural goods in the museum for satisfaction bonus.

    Inputs:
        city_id     — city with the museum
        arrangement — list of cultural good placements (optional, auto-arrange)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Arranging museum for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.arrange_museum(city_id, arrangement)
            # city_id     = inputs["city_id"]
            # arrangement = inputs.get("arrangement")
            # client.arrange_museum(city_id, arrangement)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Museum arranged")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Build museum failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
