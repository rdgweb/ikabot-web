"""
Miscellaneous runners — colonisation, miracles, scanning, premium, and more.

Action codes:
    16  activate_miracle
    20  scan_island
    21  scan_player
    25  vacation_mode
    29  buy_premium
    30  activate_ambro
    31  setup_trade_route
    32  build_museum
    820 targeted_colonize
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from services.island_donation import extract_city_data

logger = logging.getLogger(__name__)

FOUNDING_BUFFER_SECONDS = 45
FOUNDING_POLL_SECONDS = 60


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _parse_related_cities(html: str) -> list[dict[str, Any]]:
    match = re.search(r"relatedCityData:\s*JSON\.parse\('(.*?)'\)", html)
    if not match:
        current_match = re.search(r'currentCityId["\s:=]+(\d+)', html)
        if not current_match:
            return []
        return [{"id": int(current_match.group(1)), "name": "", "coords": ""}]

    raw = match.group(1)
    clean = raw.replace('\\"', '"').replace("\\\\'", "'").replace("\\\\", "\\")
    try:
        payload = json.loads(clean)
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for key, city_info in payload.items():
        if not isinstance(city_info, dict) or city_info.get("relationship") != "ownCity":
            continue
        city_id = _to_int(city_info.get("id"))
        if city_id <= 0 and "_" in str(key):
            city_id = _to_int(str(key).split("_", 1)[-1], 0)
        if city_id <= 0:
            continue
        out.append(
            {
                "id": city_id,
                "name": str(city_info.get("name") or "").strip(),
                "coords": str(city_info.get("coords") or "").strip(),
                "tradegood": _to_int(city_info.get("tradegood")),
            }
        )
    return out


def fetch_owned_cities(client) -> list[dict[str, Any]]:
    html = client._request("GET", client._server_url, params={"view": "city"}, timeout=30).text
    return _parse_related_cities(html)


def detect_founded_city(
    client,
    *,
    known_city_ids: list[int] | list[str],
    island_id: int | str,
    position: int,
    owner_name: str = "",
) -> dict[str, Any] | None:
    known_ids = {str(_to_int(value)) for value in known_city_ids if _to_int(value) > 0}
    current_cities = fetch_owned_cities(client)
    current_by_id = {str(item["id"]): item for item in current_cities if _to_int(item.get("id")) > 0}

    new_ids = [cid for cid in current_by_id if cid not in known_ids]
    if len(new_ids) == 1:
        return current_by_id[new_ids[0]]

    island = client.fetch_island_by_id(island_id)
    for slot in island.get("cities") or []:
        if _to_int(slot.get("position")) != int(position):
            continue
        slot_id = str(_to_int(slot.get("id")))
        if slot_id in current_by_id:
            return current_by_id[slot_id]
        if owner_name and str(slot.get("owner_name") or "").strip() == owner_name and _to_int(slot.get("id")) > 0:
            return {
                "id": _to_int(slot.get("id")),
                "name": str(slot.get("name") or "").strip(),
                "coords": f"[{island.get('x')}:{island.get('y')}]",
            }
    return None


def fetch_city_payload(client, city_id: int | str) -> dict[str, Any]:
    html = client._request("GET", client._server_url, params={"view": "city", "cityId": str(city_id)}, timeout=30).text
    return extract_city_data(html)


@register_runner(820)
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
        ga_id = job.get("game_account_id", "")
        phase = str(inputs.get("_phase") or "start").strip().lower()
        self.log(jid, "info", f"Colonizando para conta {aid} | fase={phase}")

        source_city_id = inputs.get("source_city_id") or inputs.get("city_id")
        island_id = inputs.get("island_id")
        position = inputs.get("position")
        if not source_city_id or island_id in (None, "") or position in (None, ""):
            return RunnerResult(
                success=False,
                data={"error": "missing_required_inputs", "required": ["source_city_id", "island_id", "position"]},
            )

        resource_keys = ("wood", "wine", "marble", "crystal", "sulfur")
        resources = {
            key: int(inputs.get(key, 0) or 0)
            for key in resource_keys
            if int(inputs.get(key, 0) or 0) > 0
        }

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            if phase == "wait_founding":
                founded_city = detect_founded_city(
                    client,
                    known_city_ids=list(inputs.get("_known_city_ids") or []),
                    island_id=island_id,
                    position=int(position),
                    owner_name=str(client.account_info.get("player_name") or ""),
                )
                if not founded_city:
                    self.save_game_client(ga_id or aid, client)
                    self.log(jid, "info", "Colonia ainda nao apareceu; mantendo polling")
                    return RunnerResult(
                        success=True,
                        reschedule_seconds=FOUNDING_POLL_SECONDS,
                        reschedule_inputs={**inputs, "_phase": "wait_founding"},
                        data={"status": "waiting_city"},
                    )

                founded_city_id = _to_int(founded_city.get("id"))
                founded_name = str(founded_city.get("name") or founded_city_id)
                self.save_game_client(ga_id or aid, client)
                self.log(jid, "info", f"Colonia fundada detectada: {founded_name} ({founded_city_id})")
                return RunnerResult(
                    success=True,
                    data={
                        "status": "founded",
                        "source_city_id": str(source_city_id),
                        "island_id": str(island_id),
                        "position": int(position),
                        "resources": resources,
                        "new_city_id": founded_city_id,
                        "new_city_name": founded_name,
                        "coords": founded_city.get("coords") or "",
                    },
                )

            preview = client.get_colonization_preview(
                source_city_id=source_city_id,
                island_id=island_id,
                position=int(position),
            )
            try:
                known_city_ids = [item["id"] for item in fetch_owned_cities(client)]
            except Exception:
                known_city_ids = []
            self.log(
                jid,
                "info",
                f"Preview colonizacao ok: ilha {island_id} pos {position}, chegada {preview.get('arrival_at_text') or '-'}",
            )

            result = client.start_colonization(
                source_city_id=source_city_id,
                island_id=island_id,
                position=int(position),
                resources=resources,
            )

            wait_seconds = (
                _to_int(preview.get("loading_time_seconds"))
                + _to_int(preview.get("travel_time_seconds"))
                + FOUNDING_BUFFER_SECONDS
            )

            self.save_game_client(ga_id or aid, client)
            feedback = "; ".join(result.get("feedback") or []) or "Colonizacao enviada"
            self.log(jid, "info", f"{feedback} | aguardando {wait_seconds}s pela fundacao")
            return RunnerResult(
                success=True,
                reschedule_seconds=max(wait_seconds, FOUNDING_POLL_SECONDS),
                reschedule_inputs={
                    **inputs,
                    "_phase": "wait_founding",
                    "_known_city_ids": known_city_ids,
                    "_colonization_started_at": int(time.time()),
                    "_colonization_arrival_at_text": preview.get("arrival_at_text") or "",
                },
                data={
                    "status": "founding_started",
                    "source_city_id": str(source_city_id),
                    "island_id": str(island_id),
                    "position": int(position),
                    "resources": resources,
                    "preview": {
                        "capacity": preview.get("capacity", 0),
                        "max_capacity": preview.get("max_capacity", 0),
                        "transporters": preview.get("transporters", 0),
                        "loading_time_text": preview.get("loading_time_text", ""),
                        "loading_time_seconds": _to_int(preview.get("loading_time_seconds")),
                        "travel_time_text": preview.get("travel_time_text", ""),
                        "travel_time_seconds": _to_int(preview.get("travel_time_seconds")),
                        "arrival_at_text": preview.get("arrival_at_text", ""),
                        "destination_name": preview.get("destination_name", ""),
                    },
                    "feedback": result.get("feedback") or [],
                },
            )
        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
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

    @staticmethod
    def _looks_like_vacation_block(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return (
            "vacation" in text
            or "nologin_umod" in text
            or "modo f" in text
            or "umod" in text
        )

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
            if not enable:
                fresh_client = self.get_or_login_game_client(
                    jid,
                    aid,
                    ga_id,
                    creds,
                    allow_cached=False,
                )
                self.log(jid, "info", "Login fresco confirmou conta fora do modo ferias")
                self.save_game_client(ga_id, fresh_client)
                return RunnerResult(success=True, data={"enabled": False, "confirmed": True})

            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

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
            self.log(jid, "info", f"Solicitacao de modo ferias enviada: {result}")

            if ga_id:
                self.sessions.invalidate_game_session(ga_id)

            try:
                fresh_client = self.get_or_login_game_client(
                    jid,
                    aid,
                    ga_id,
                    creds,
                    allow_cached=False,
                )
            except Exception as confirm_exc:
                if self._looks_like_vacation_block(confirm_exc):
                    self.log(jid, "info", "Modo ferias confirmado: login fresco bloqueado pelo jogo")
                    return RunnerResult(
                        success=True,
                        data={
                            "enabled": True,
                            "city_id": city_id,
                            "confirmed": True,
                            "confirmation": "fresh_login_blocked",
                        },
                    )
                if self.is_network_error(confirm_exc):
                    return self.network_error_result(jid, confirm_exc)
                self.log(jid, "error", f"Ativacao nao confirmada: {confirm_exc}")
                return RunnerResult(
                    success=False,
                    data={
                        "error": "vacation_mode_not_confirmed",
                        "city_id": city_id,
                        "detail": str(confirm_exc),
                    },
                )

            self.save_game_client(ga_id, fresh_client)
            self.log(jid, "warn", "Login fresco continuou funcionando; modo ferias nao foi confirmado")
            return RunnerResult(
                success=False,
                data={
                    "error": "vacation_mode_not_confirmed",
                    "city_id": city_id,
                    "detail": "fresh_login_still_succeeds",
                },
            )

        except Exception as exc:
            if not enable and self._looks_like_vacation_block(exc):
                self.log(jid, "warn", "Conta continua em modo ferias; desativacao ainda nao liberada pelo jogo")
                return RunnerResult(
                    success=False,
                    data={"error": "vacation_mode_still_active", "enabled": True},
                )
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
