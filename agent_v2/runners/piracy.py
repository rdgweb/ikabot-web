"""
Piracy runners — action 17 (PiracyMissionRunner) and 1018 (CollectPiracyRunner).

Action codes:
    17    piracy_mission   (recurring)
    1018  collect_piracy   (legacy/internal recurring helper)
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.runner_registry import register_runner
from game_client.exceptions import CaptchaRequiredError
from runners.base import BaseRunner, RunnerResult
from runners.misc import detect_founded_city, fetch_city_payload, fetch_owned_cities

logger = logging.getLogger(__name__)

# Mission duration map: buildingLevel → duration in seconds
_MISSION_DURATIONS: dict[int, int] = {
    1: 150,
    3: 450,
    5: 900,
    7: 1800,
    9: 3600,
    11: 7200,
    13: 14400,
    15: 28800,
    17: 57600,
}

CAPTCHA_RESCHEDULE = 5 * 60       # 5 min — best-effort captcha handling
ERROR_RESCHEDULE = 10 * 60        # 10 min — generic error back-off
MISSION_BUFFER = 60               # seconds of extra buffer after mission completes
COLLECT_INTERVAL = 8 * 3600       # 8 h — legacy collect runner interval
TARGETED_RECHECK = 60
TARGETED_TZ = ZoneInfo("America/Cuiaba")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clear_targeted_state(inputs: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(inputs or {})
    for key in list(cleaned.keys()):
        if key.startswith("_targeted_"):
            cleaned.pop(key, None)
    return cleaned


def _server_key(server_id: str) -> str:
    return str(server_id or "").strip().lower()


def _mission_level_for_time(inputs: dict, now_hour: int) -> int:
    """Return mission level based on current hour and day/night configuration."""
    try:
        day_start = int(inputs.get("day_start_hour") or 8)
        day_end   = int(inputs.get("day_end_hour")   or 22)
        day_level = int(inputs.get("day_mission_level") or 7)
        night_level = int(inputs.get("night_mission_level") or 13)
    except (ValueError, TypeError):
        return 7

    if day_start <= day_end:
        is_day = day_start <= now_hour < day_end
    else:
        # wraps midnight (e.g. day_start=22, day_end=8)
        is_day = now_hour >= day_start or now_hour < day_end

    return day_level if is_day else night_level


@register_runner(17)
class PiracyMissionRunner(BaseRunner):
    """Send crew on a piracy mission from the pirate fortress.

    Recurring — reschedules to keep piracy running continuously.
    """

    def _select_mission_by_time(self, inputs: dict) -> int:
        return _mission_level_for_time(inputs, datetime.now().hour)

    @staticmethod
    def _capture_gain_since_last_mission(inputs: dict[str, Any], capture_points: int) -> int:
        try:
            last_capture_points = int(inputs.get("last_capture_points") or 0)
        except (ValueError, TypeError):
            return 0
        try:
            last_time_remaining = int(inputs.get("last_time_remaining") or 0)
        except (ValueError, TypeError):
            last_time_remaining = 0

        if last_time_remaining <= 0:
            return 0
        gain = capture_points - last_capture_points
        return gain if gain > 0 else 0

    def _collect_highscore_state(
        self,
        jid: str,
        client,
        city_id: str,
        game_account_id: str,
    ) -> dict[str, Any]:
        try:
            ranking = client.get_piracy_highscore(city_id)
        except Exception as exc:
            self.log(jid, "warn", f"Falha ao ler ranking da pirataria: {exc}")
            return {}

        entries = list(ranking.get("entries") or [])
        trimmed_entries = entries[:10]
        self_avatar_id = str(ranking.get("self_avatar_id") or "").strip()
        own_entry = next(
            (
                entry for entry in entries
                if (
                    str(entry.get("target_city_id") or "") == str(city_id)
                    or (self_avatar_id and str(entry.get("avatar_id") or "") == self_avatar_id)
                )
            ),
            None,
        )
        state = {
            "piracy_highscore_entries": trimmed_entries,
            "piracy_highscore_time_left": int(ranking.get("highscore_time_left") or 0),
            "piracy_highscore_updated_at": int(time.time()),
            "piracy_highscore_avatar_id": self_avatar_id,
        }
        if own_entry:
            state["piracy_highscore_self"] = own_entry

        if game_account_id:
            try:
                self.hub.patch_snapshot_base(
                    game_account_id,
                    {
                        "piracy_highscore": {
                            "city_id": str(city_id),
                            "entries": trimmed_entries,
                            "time_left": int(ranking.get("highscore_time_left") or 0),
                            "updated_at": int(time.time()),
                            "self_entry": own_entry or {},
                            "self_avatar_id": self_avatar_id,
                        }
                    },
                )
            except Exception as exc:
                self.log(jid, "warn", f"Falha ao salvar ranking pirata no snapshot: {exc}")

        return state

    def _targeted_cycle_due(self, inputs: dict[str, Any]) -> bool:
        if not _to_bool(inputs.get("enable_targeted_cycle")):
            return False
        interval_days = max(1, _to_int(inputs.get("targeted_interval_days"), 7))
        after_hour = max(0, min(23, _to_int(inputs.get("targeted_after_hour"), 16)))
        now_local = datetime.now(TARGETED_TZ)
        if now_local.hour < after_hour:
            return False
        last_ts = _to_int(inputs.get("last_targeted_cycle_at"), 0)
        if last_ts <= 0:
            return True
        return (time.time() - last_ts) >= interval_days * 86400

    def _protected_owner_names(self, game_account_id: str, current_player_name: str) -> set[str]:
        protected = {str(current_player_name or "").strip().lower()}
        try:
            config = self.get_agent_config()
        except Exception:
            return {name for name in protected if name}

        target_server = ""
        for account in config.get("accounts") or []:
            for ga in account.get("game_accounts") or []:
                if str(ga.get("id") or "") == str(game_account_id or ""):
                    target_server = _server_key(str(ga.get("server_id") or ""))
                    break
            if target_server:
                break

        for account in config.get("accounts") or []:
            for ga in account.get("game_accounts") or []:
                if target_server and _server_key(str(ga.get("server_id") or "")) != target_server:
                    continue
                name = str(ga.get("name") or ga.get("player_name") or "").strip().lower()
                if name:
                    protected.add(name)
        return {name for name in protected if name}

    def _within_active_foundation_limit(self, client, *, limit: int) -> bool:
        if limit <= 0:
            return True
        own_cities = fetch_owned_cities(client)
        active_colonies = 0
        for city in own_cities:
            city_id = _to_int(city.get("id"), 0)
            if city_id <= 0:
                continue
            try:
                payload = fetch_city_payload(client, city_id)
            except Exception:
                continue
            if bool(payload.get("isCapital")):
                continue
            if len(payload.get("position") or []) > 17:
                pos17 = (payload.get("position") or [])[17] or {}
                if str(pos17.get("building") or "") == "pirateFortress":
                    active_colonies += 1
        return active_colonies < limit

    def _read_player_scores(self, client, city_id: str) -> tuple[str, dict[str, int]]:
        payload = fetch_city_payload(client, city_id)
        owner_id = str(payload.get("ownerId") or payload.get("owner_id") or "").strip()
        island = client.fetch_island_by_city_id(city_id)
        score = island.get("avatar_scores", {}).get(owner_id) or {}
        return owner_id, {
            "total_score": _to_int(score.get("building_score")) + _to_int(score.get("research_score")) + _to_int(score.get("army_score")),
            "general_score": _to_int(score.get("army_score")),
        }

    def _target_scores_for_entry(self, client, entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
        island = client.fetch_island_by_city_id(entry.get("target_city_id") or "")
        target_city = next(
            (
                city for city in (island.get("cities") or [])
                if str(city.get("id") or "") == str(entry.get("target_city_id") or "")
            ),
            None,
        )
        if not target_city:
            raise RuntimeError("target_city_not_found")
        owner_id = str(target_city.get("owner_id") or "").strip()
        score = island.get("avatar_scores", {}).get(owner_id) or {}
        return target_city, {
            "total_score": _to_int(score.get("building_score")) + _to_int(score.get("research_score")) + _to_int(score.get("army_score")),
            "general_score": _to_int(score.get("army_score")),
        }

    def _choose_targeted_entry(
        self,
        client,
        *,
        city_id: str,
        game_account_id: str,
        highscore_state: dict[str, Any],
        inputs: dict[str, Any],
    ) -> dict[str, Any] | None:
        entries = list(highscore_state.get("piracy_highscore_entries") or [])
        self_entry = highscore_state.get("piracy_highscore_self") or {}
        self_avatar_id = str(highscore_state.get("piracy_highscore_avatar_id") or "")
        self_place = _to_int(self_entry.get("place"), 0)
        max_gap = max(1, _to_int(inputs.get("target_max_score_gap"), 5))
        require_lower_total = _to_bool(inputs.get("target_require_lower_total_score"))
        require_lower_general = _to_bool(inputs.get("target_require_lower_general_score"))
        protected_names = self._protected_owner_names(game_account_id, str(client.account_info.get("player_name") or ""))

        own_owner_id, own_scores = self._read_player_scores(client, city_id)
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for entry in entries:
            avatar_id = str(entry.get("avatar_id") or "")
            target_city_id = str(entry.get("target_city_id") or "")
            if not target_city_id or target_city_id == city_id or (self_avatar_id and avatar_id == self_avatar_id):
                continue
            place_gap = abs(_to_int(entry.get("place")) - self_place) if self_place > 0 else 0
            if self_place > 0 and place_gap > max_gap:
                continue
            try:
                target_city, target_scores = self._target_scores_for_entry(client, entry)
            except Exception:
                continue
            owner_name = str(target_city.get("owner_name") or "").strip().lower()
            if owner_name and owner_name in protected_names:
                continue
            actions = target_city.get("actions") or {}
            if isinstance(actions, dict):
                raid_available = _to_int(actions.get("piracy_raid"), 0) > 0
            else:
                raid_available = "piracy_raid" in actions
            if not raid_available:
                continue
            if require_lower_total and target_scores["total_score"] >= own_scores["total_score"]:
                continue
            if require_lower_general and target_scores["general_score"] >= own_scores["general_score"]:
                continue
            weight = abs(_to_int(entry.get("score")) - _to_int(self_entry.get("score")))
            enriched = {
                **entry,
                "target_owner_id": str(target_city.get("owner_id") or ""),
                "target_owner_name": str(target_city.get("owner_name") or ""),
                "target_total_score": target_scores["total_score"],
                "target_general_score": target_scores["general_score"],
                "own_total_score": own_scores["total_score"],
                "own_general_score": own_scores["general_score"],
                "own_owner_id": own_owner_id,
                "place_gap": place_gap,
            }
            candidates.append((place_gap, weight, enriched))

        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], str(item[2].get("player_name") or "")))
        return candidates[0][2]

    def _find_colony_spot(self, client, target_city_id: str) -> dict[str, Any] | None:
        target_island = client.fetch_island_by_city_id(target_city_id)
        islands_to_check = [target_island]
        try:
            x = _to_int(target_island.get("x"))
            y = _to_int(target_island.get("y"))
            nearby = client.fetch_island_area(max(0, x - 1), max(0, y - 1), x + 1, y + 1)
            for item in nearby:
                island_id = str(item.get("island_id") or "")
                if not island_id or island_id == str(target_island.get("island_id") or ""):
                    continue
                islands_to_check.append(client.fetch_island_by_id(island_id))
        except Exception:
            pass

        for island in islands_to_check:
            for slot in island.get("cities") or []:
                if str(slot.get("type") or "").strip() not in {"empty", "buildplace"}:
                    continue
                position = _to_int(slot.get("position"))
                if position <= 0 or position == 17:
                    continue
                return {
                    "island_id": str(island.get("island_id") or ""),
                    "island_name": str(island.get("name") or ""),
                    "coord_x": _to_int(island.get("x")),
                    "coord_y": _to_int(island.get("y")),
                    "position": position,
                    "adjacent": str(island.get("island_id") or "") != str(target_island.get("island_id") or ""),
                }
        return None

    def _pirate_fortress_ready(self, client, city_id: int | str) -> tuple[bool, int]:
        payload = fetch_city_payload(client, city_id)
        positions = list(payload.get("position") or [])
        if len(positions) > 17:
            fortress = positions[17] or {}
            if str(fortress.get("building") or "") == "pirateFortress":
                if _to_int(payload.get("underConstruction"), -1) == 17:
                    return False, max(0, _to_int(payload.get("endUpgradeTime")) - int(time.time()))
                return True, 0
        under_construction = _to_int(payload.get("underConstruction"), -1)
        if under_construction == 17:
            return False, max(0, _to_int(payload.get("endUpgradeTime")) - int(time.time()))
        return False, 0

    def _abandon_temp_colony(self, jid: str, client, game_account_id: str, city_id: int) -> RunnerResult | None:
        try:
            client.abandon_colony(city_id, game_account_id=game_account_id)
        except Exception as exc:
            self.log(jid, "warn", f"Abandono da colonia falhou por enquanto: {exc}")
            return RunnerResult(
                success=True,
                reschedule_seconds=TARGETED_RECHECK,
                data={"status": "abandon_retry", "city_id": city_id},
            )

        remaining = fetch_owned_cities(client)
        if any(_to_int(item.get("id")) == city_id for item in remaining):
            self.log(jid, "info", "Pedido de abandono enviado; cidade ainda visivel, aguardando confirmacao")
            return RunnerResult(
                success=True,
                reschedule_seconds=TARGETED_RECHECK,
                data={"status": "abandon_pending", "city_id": city_id},
            )
        return None

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        game_account_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        city_id = str(inputs.get("city_id") or "").strip()
        if not city_id:
            self.log(jid, "error", "city_id obrigatorio para missao pirata")
            return RunnerResult(success=False, data={"error": "city_id_missing"})

        # Mission level: schedule_by_time overrides simple mission_level
        schedule_by_time = bool(inputs.get("schedule_by_time") or False)
        if schedule_by_time:
            mission_level = self._select_mission_by_time(inputs)
        else:
            try:
                mission_level = int(inputs.get("mission_level") or 7)
            except (ValueError, TypeError):
                mission_level = 7

        # Random wait before starting (anti-detection)
        try:
            max_random_wait = int(inputs.get("max_random_wait") or 0)
        except (ValueError, TypeError):
            max_random_wait = 0

        effective_level = mission_level  # may be updated inside try block
        convert_mode = str(inputs.get("convert_mode") or "all").strip().lower()
        try:
            convert_threshold = int(inputs.get("convert_threshold") or 0)
        except (ValueError, TypeError):
            convert_threshold = 0
        try:
            convert_percent = max(1, min(100, int(inputs.get("convert_percent") or 100)))
        except (ValueError, TypeError):
            convert_percent = 100

        creds = self.resolve_credentials(aid, inputs, game_account_id=game_account_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(
                success=False,
                reschedule_seconds=ERROR_RESCHEDULE,
                data={"error": "credentials_missing"},
            )

        try:
            client = self.get_or_login_game_client(jid, aid, game_account_id, creds)

            # Step 1: get current piracy state
            self.log(jid, "info", f"Lendo estado da fortaleza pirata na cidade {city_id}")
            state = client.get_piracy_state(city_id)
            highscore_state = self._collect_highscore_state(jid, client, city_id, game_account_id)

            time_remaining = int(state.get("time_remaining") or 0)
            fortress_level = int(state.get("fortress_level") or 1)
            capture_points = int(state.get("capture_points") or 0)
            crew_points = int(state.get("crew_points") or 0)
            complete_crew_points = int(state.get("complete_crew_points") or 0)

            _state_inputs = {
                "last_capture_points": capture_points,
                "last_crew_points": crew_points,
                "last_complete_crew_points": complete_crew_points,
                "last_fortress_level": fortress_level,
                "last_time_remaining": time_remaining,
                "last_state_updated_at": int(time.time()),
                **highscore_state,
            }

            targeted_phase = str(inputs.get("_targeted_phase") or "").strip().lower()
            if targeted_phase:
                target_city_id = str(inputs.get("_targeted_target_city_id") or "").strip()
                target_island_id = str(inputs.get("_targeted_target_island_id") or "").strip()
                colony_island_id = str(inputs.get("_targeted_colony_island_id") or "").strip()
                colony_position = _to_int(inputs.get("_targeted_colony_position"), 0)
                temp_city_id = _to_int(inputs.get("_targeted_temp_city_id"), 0)

                if targeted_phase == "wait_colony":
                    founded_city = detect_founded_city(
                        client,
                        known_city_ids=list(inputs.get("_targeted_known_city_ids") or []),
                        island_id=colony_island_id,
                        position=colony_position,
                        owner_name=str(client.account_info.get("player_name") or ""),
                    )
                    if not founded_city:
                        self.log(jid, "info", "Aguardando a fundacao da colonia temporaria")
                        self.save_game_client(game_account_id, client)
                        return RunnerResult(
                            success=True,
                            reschedule_seconds=TARGETED_RECHECK,
                            reschedule_inputs={**inputs, **_state_inputs},
                            data={"status": "waiting_temp_colony"},
                        )
                    temp_city_id = _to_int(founded_city.get("id"), 0)
                    inputs = {
                        **inputs,
                        "_targeted_phase": "wait_fortress",
                        "_targeted_temp_city_id": temp_city_id,
                    }
                    self.log(jid, "info", f"Colonia temporaria detectada: {founded_city.get('name') or temp_city_id} ({temp_city_id})")
                    targeted_phase = "wait_fortress"

                if targeted_phase == "wait_fortress":
                    if temp_city_id <= 0:
                        self.log(jid, "warn", "Cidade temporaria ausente; limpando ciclo especial")
                        return RunnerResult(
                            success=True,
                            reschedule_seconds=30,
                            reschedule_inputs={**_clear_targeted_state(inputs), **_state_inputs},
                        )

                    fortress_ready, wait_left = self._pirate_fortress_ready(client, temp_city_id)
                    if not fortress_ready:
                        if wait_left <= 0:
                            self.log(jid, "info", f"Construindo fortaleza pirata na colonia {temp_city_id}")
                            client.build(city_id=temp_city_id, building_type="pirateFortress", position=17)
                            wait_left = TARGETED_RECHECK
                        else:
                            self.log(jid, "info", f"Fortaleza pirata em construcao na colonia {temp_city_id}; faltam {wait_left}s")
                        self.save_game_client(game_account_id, client)
                        return RunnerResult(
                            success=True,
                            reschedule_seconds=max(TARGETED_RECHECK, min(wait_left + MISSION_BUFFER if wait_left else TARGETED_RECHECK, 3600)),
                            reschedule_inputs={**inputs, **_state_inputs, "_targeted_phase": "wait_fortress"},
                            data={"status": "waiting_pirate_fortress", "temp_city_id": temp_city_id},
                        )

                    self.log(jid, "info", f"Fortaleza pronta na colonia {temp_city_id}; executando raid em {target_city_id}")
                    raid = client.start_piracy_raid(
                        source_city_id=temp_city_id,
                        destination_city_id=target_city_id,
                        destination_island_id=target_island_id,
                    )
                    raid_wait = max(TARGETED_RECHECK, _to_int(raid.get("time_remaining"), 0) + MISSION_BUFFER)
                    self.save_game_client(game_account_id, client)
                    return RunnerResult(
                        success=True,
                        reschedule_seconds=raid_wait,
                        reschedule_inputs={
                            **inputs,
                            **_state_inputs,
                            "_targeted_phase": "wait_abandon",
                            "_targeted_temp_city_id": temp_city_id,
                        },
                        data={"status": "targeted_raid_started", "temp_city_id": temp_city_id, "raid": raid},
                    )

                if targeted_phase == "wait_abandon":
                    if temp_city_id <= 0:
                        return RunnerResult(
                            success=True,
                            reschedule_seconds=30,
                            reschedule_inputs={**_clear_targeted_state(inputs), **_state_inputs, "last_targeted_cycle_at": int(time.time())},
                            data={"status": "targeted_cycle_complete"},
                        )
                    try:
                        temp_state = client.get_piracy_state(str(temp_city_id))
                        temp_remaining = _to_int(temp_state.get("time_remaining"), 0)
                    except Exception:
                        temp_remaining = 0
                    if temp_remaining > 0:
                        self.log(jid, "info", f"Assalto em andamento na colonia {temp_city_id}; abandono depois de {temp_remaining}s")
                        self.save_game_client(game_account_id, client)
                        return RunnerResult(
                            success=True,
                            reschedule_seconds=temp_remaining + MISSION_BUFFER,
                            reschedule_inputs={**inputs, **_state_inputs, "_targeted_phase": "wait_abandon"},
                            data={"status": "waiting_assault_end", "temp_city_id": temp_city_id},
                        )
                    abandon_pending = self._abandon_temp_colony(jid, client, game_account_id, temp_city_id)
                    if abandon_pending is not None:
                        self.save_game_client(game_account_id, client)
                        return RunnerResult(
                            success=abandon_pending.success,
                            reschedule_seconds=abandon_pending.reschedule_seconds,
                            reschedule_inputs={**inputs, **_state_inputs, "_targeted_phase": "wait_abandon"},
                            data=abandon_pending.data,
                        )
                    self.log(jid, "info", f"Colonia temporaria {temp_city_id} abandonada; ciclo especial concluido")
                    cleaned = _clear_targeted_state(inputs)
                    cleaned["last_targeted_cycle_at"] = int(time.time())
                    self.save_game_client(game_account_id, client)
                    return RunnerResult(
                        success=True,
                        reschedule_seconds=30,
                        reschedule_inputs={**cleaned, **_state_inputs},
                        data={"status": "targeted_cycle_complete", "temp_city_id": temp_city_id},
                    )

            # Step 2: mission already active — wait for it
            if time_remaining > 0:
                wait = time_remaining + MISSION_BUFFER
                self.log(
                    jid,
                    "info",
                    f"Missao pirata em andamento. Navio retorna em {time_remaining}s. "
                    f"Reagendando em {wait}s.",
                )
                self.save_game_client(game_account_id, client)
                return RunnerResult(success=True, reschedule_seconds=wait, reschedule_inputs=_state_inputs)

            if self._targeted_cycle_due(inputs):
                target_entry = self._choose_targeted_entry(
                    client,
                    city_id=city_id,
                    game_account_id=game_account_id,
                    highscore_state=highscore_state,
                    inputs=inputs,
                )
                if target_entry:
                    colony_spot = self._find_colony_spot(client, str(target_entry.get("target_city_id") or ""))
                else:
                    colony_spot = None
                if target_entry and colony_spot:
                    max_foundations = max(1, _to_int(inputs.get("targeted_max_active_foundations"), 1))
                    if not self._within_active_foundation_limit(client, limit=max_foundations):
                        self.log(jid, "info", f"Limite ativo de colonias temporarias atingido ({max_foundations}); adiando ciclo especial")
                        self.save_game_client(game_account_id, client)
                        return RunnerResult(
                            success=True,
                            reschedule_seconds=30 * 60,
                            reschedule_inputs={**inputs, **_state_inputs},
                            data={"status": "targeted_foundation_limit_reached", "limit": max_foundations},
                        )
                    preview = client.get_colonization_preview(
                        source_city_id=city_id,
                        island_id=colony_spot["island_id"],
                        position=int(colony_spot["position"]),
                    )
                    known_city_ids = [item["id"] for item in fetch_owned_cities(client)]
                    result = client.start_colonization(
                        source_city_id=city_id,
                        island_id=colony_spot["island_id"],
                        position=int(colony_spot["position"]),
                        resources={
                            "wood": 2250,
                            "wine": 2500,
                            "marble": 2500,
                            "crystal": 2500,
                            "sulfur": 2500,
                        },
                    )
                    wait_seconds = (
                        _to_int(preview.get("loading_time_seconds"))
                        + _to_int(preview.get("travel_time_seconds"))
                        + MISSION_BUFFER
                    )
                    self.log(
                        jid,
                        "info",
                        f"Ciclo especial iniciado contra {target_entry.get('player_name')} "
                        f"(cidade {target_entry.get('target_city_id')}) | colonia em {colony_spot['coord_x']}:{colony_spot['coord_y']} "
                        f"pos {colony_spot['position']} | chegada {preview.get('arrival_at_text') or '-'}",
                    )
                    self.save_game_client(game_account_id, client)
                    return RunnerResult(
                        success=True,
                        reschedule_seconds=max(wait_seconds, TARGETED_RECHECK),
                        reschedule_inputs={
                            **inputs,
                            **_state_inputs,
                            "_targeted_phase": "wait_colony",
                            "_targeted_target_city_id": str(target_entry.get("target_city_id") or ""),
                            "_targeted_target_island_id": str(target_entry.get("target_island_id") or ""),
                            "_targeted_colony_island_id": str(colony_spot["island_id"]),
                            "_targeted_colony_position": int(colony_spot["position"]),
                            "_targeted_known_city_ids": known_city_ids,
                            "_targeted_target_player_name": str(target_entry.get("player_name") or ""),
                            "_targeted_colonization_feedback": result.get("feedback") or [],
                            "_targeted_colonization_arrival_at_text": preview.get("arrival_at_text") or "",
                        },
                        data={
                            "status": "targeted_cycle_started",
                            "target": target_entry,
                            "colony_spot": colony_spot,
                            "colonization": result,
                        },
                    )
                if target_entry:
                    self.log(jid, "warn", f"Nenhum slot livre encontrado para atacar {target_entry.get('player_name')}; seguindo fluxo normal")
                else:
                    self.log(jid, "info", "Nenhum alvo elegivel no ranking pirata nesta rodada especial")

            # Step 3: ship in port — optionally convert, then start new mission
            # a) convert capture points to crew based on mode
            mission_capture_gain = self._capture_gain_since_last_mission(inputs, capture_points)

            if convert_mode == "all":
                should_convert = capture_points > 0
                points_to_convert = capture_points
            elif convert_mode == "percent":
                should_convert = mission_capture_gain > 0
                points_to_convert = int(mission_capture_gain * convert_percent / 100)
            elif convert_mode == "threshold":
                should_convert = capture_points >= max(convert_threshold, 1)
                points_to_convert = capture_points
            else:
                should_convert = False
                points_to_convert = 0

            _total_crew_converted = int(inputs.get("total_crew_converted") or 0)
            _total_missions = int(inputs.get("total_missions_started") or 0)

            if should_convert and points_to_convert > 0:
                conversion_factor = int(state.get("conversion_factor") or 10)
                crew_to_create = points_to_convert // conversion_factor
                if crew_to_create > 0:
                    if convert_mode == "percent":
                        self.log(
                            jid,
                            "info",
                            f"Convertendo {points_to_convert}/{mission_capture_gain} pontos do ganho da missao "
                            f"({convert_percent}%) em {crew_to_create} de forca da tripulacao",
                        )
                    else:
                        self.log(
                            jid,
                            "info",
                            f"Convertendo {points_to_convert} pontos de captura em "
                            f"{crew_to_create} de forca da tripulacao",
                        )
                    try:
                        client.convert_piracy_points(city_id, crew_to_create)
                        _total_crew_converted += crew_to_create
                        self.log(jid, "info", f"Convertidos {points_to_convert} pontos → {crew_to_create} força de tripulação (total acumulado: {_total_crew_converted})")
                        if convert_mode == "percent":
                            # Save post-conversion value so next run calculates gain relative
                            # to what actually remained in the game after converting.
                            _state_inputs["last_capture_points"] = capture_points - points_to_convert
                    except CaptchaRequiredError:
                        self.log(jid, "warn", "Captcha necessario na conversao de pontos. Reagendando em 5 min.")
                        self.save_game_client(game_account_id, client)
                        return RunnerResult(
                            success=False,
                            reschedule_seconds=CAPTCHA_RESCHEDULE,
                            data={"error": "captcha_convert"},
                        )
                    except Exception as exc:
                        self.log(jid, "warn", f"Conversao de pontos falhou (continuando): {exc}")
            elif convert_mode == "percent" and capture_points > 0:
                self.log(
                    jid,
                    "info",
                    "Sem ganho de missao confiavel desde a ultima coleta; pulando conversao percentual nesta rodada.",
                )

            # b) cap mission_level to what the fortress supports
            effective_level = mission_level
            if effective_level > fortress_level:
                # find the highest available level that doesn't exceed fortress_level
                available = sorted(
                    (lvl for lvl in _MISSION_DURATIONS if lvl <= fortress_level),
                    reverse=True,
                )
                effective_level = available[0] if available else 1
                self.log(
                    jid,
                    "info",
                    f"Nível da missão ajustado de {mission_level} para {effective_level} "
                    f"(fortaleza nível {fortress_level})",
                )

            # c) optional random wait before mission (anti-detection)
            if max_random_wait > 0:
                wait_secs = random.randint(0, max_random_wait)
                if wait_secs > 0:
                    self.log(jid, "info", f"Aguardando {wait_secs}s antes de iniciar (espera aleatória)")
                    time.sleep(wait_secs)

            # d) start the mission
            self.log(jid, "info", f"Iniciando missão pirata nível {effective_level}")
            result = client.start_piracy_mission(
                city_id,
                effective_level,
                game_account_id=game_account_id,
            )

            if not result.get("success"):
                self.log(
                    jid,
                    "warn",
                    f"Missão pirata não foi confirmada pelo jogo: "
                    f"{result.get('message') or 'mission_not_confirmed'}",
                )
                self.save_game_client(game_account_id, client)
                return RunnerResult(
                    success=False,
                    reschedule_seconds=ERROR_RESCHEDULE,
                    data={"error": result.get("message") or "mission_not_confirmed"},
                )

            # d) compute reschedule from actual time_remaining returned or mission duration
            new_time_remaining = int(result.get("time_remaining") or 0)
            if new_time_remaining > 0:
                wait = new_time_remaining + MISSION_BUFFER
            else:
                wait = _MISSION_DURATIONS.get(effective_level, 1800) + MISSION_BUFFER
                new_time_remaining = max(wait - MISSION_BUFFER, 1)

            _total_missions += 1
            mission_msg = result.get("message") or "ok"
            self.log(
                jid,
                "info",
                f"Missão pirata iniciada (nível {effective_level}). "
                f"Reagendando em {wait}s. Total missões: {_total_missions}.",
            )

            self.save_game_client(game_account_id, client)
            return RunnerResult(
                success=True,
                reschedule_seconds=wait,
                reschedule_inputs={
                    **_state_inputs,
                    "last_time_remaining": new_time_remaining,
                    "total_missions_started": _total_missions,
                    "total_crew_converted": _total_crew_converted,
                },
            )

        except CaptchaRequiredError as exc:
            # CaptchaRequiredError from state GET or other non-mission request.
            # Try to fetch and solve the captcha before rescheduling.
            self.log(jid, "warn", f"Captcha detectado fora do flow da missão: {exc}. Tentando resolver via hub.")
            try:
                import base64 as _b64
                from game_client.constants import GAME_AJAX_HEADERS
                img_resp = client.session.get(
                    client._server_url,
                    params={
                        "action": "Options",
                        "function": "createCaptcha",
                        "actionRequest": client._action_request,
                        "ajax": "1",
                    },
                    headers=dict(GAME_AJAX_HEADERS),
                    timeout=20,
                )
                if img_resp.content and len(img_resp.content) > 10:
                    img_b64 = _b64.b64encode(img_resp.content).decode("ascii")
                    result = self.hub.create_captcha_challenge("pirate", img_b64, game_account_id=game_account_id)
                    solution = str(result.get("solution") or "").strip().upper()
                    if not solution and result.get("challenge_id"):
                        solution = self.hub.poll_captcha_solution(result["challenge_id"], timeout_sec=120, interval=10).strip().upper()
                    if solution:
                        self.log(jid, "info", f"Captcha resolvido via hub: {solution}. Reagendando imediatamente.")
                        self.save_game_client(game_account_id, client)
                        return RunnerResult(success=False, reschedule_seconds=30, data={"error": "captcha_solved_retry"})
                    self.log(jid, "warn", "Captcha não resolvido (sem solução). Reagendando em 5 min.")
                else:
                    self.log(jid, "warn", "Imagem do captcha vazia. Reagendando em 5 min.")
            except Exception as cap_exc:
                self.log(jid, "warn", f"Erro ao resolver captcha externo: {cap_exc}. Reagendando em 5 min.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=False, reschedule_seconds=CAPTCHA_RESCHEDULE, data={"error": "captcha_unexpected"})
        except Exception as exc:
            self.log(jid, "error", f"Missão pirata falhou: {exc}")
            return RunnerResult(
                success=False,
                reschedule_seconds=ERROR_RESCHEDULE,
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
        game_account_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        city_id = str(inputs.get("city_id") or "").strip()

        self.log(jid, "info", f"Verificando loot pirata para conta {aid}")

        creds = self.resolve_credentials(aid, inputs, game_account_id=game_account_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(
                success=False,
                reschedule_seconds=COLLECT_INTERVAL,
                data={"error": "credentials_missing"},
            )

        try:
            client = self.get_or_login_game_client(jid, aid, game_account_id, creds)

            if city_id:
                state = client.get_piracy_state(city_id)
                capture_points = int(state.get("capture_points") or 0)
                time_remaining = int(state.get("time_remaining") or 0)
                self.log(
                    jid,
                    "info",
                    f"Fortaleza pirata — pontos: {capture_points}, "
                    f"missao ativa: {time_remaining > 0} ({time_remaining}s restantes)",
                )
            else:
                self.log(jid, "info", "city_id nao fornecido — nada a coletar")

            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=COLLECT_INTERVAL)

        except Exception as exc:
            self.log(jid, "error", f"Coleta pirata falhou: {exc}")
            return RunnerResult(
                success=False,
                reschedule_seconds=COLLECT_INTERVAL,
                data={"error": str(exc)},
            )
