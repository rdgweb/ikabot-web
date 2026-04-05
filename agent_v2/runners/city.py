"""Construction and city-management runners."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from core.runner_registry import register_runner
from game_client.constants import GAME_AJAX_HEADERS
from runners.base import BaseRunner, RunnerResult
from services.resource_transport import estimate_incoming_transport_wait_seconds

logger = logging.getLogger(__name__)

RESOURCE_KEYS = ("wood", "wine", "marble", "glas", "sulfur")
SNAPSHOT_RESOURCE_TO_INPUT = {
    "wood": "wood",
    "wine": "wine",
    "marble": "marble",
    "glas": "crystal",
    "sulfur": "sulfur",
}
TRADEGOOD_TO_RESOURCE = {
    1: "wine",
    2: "marble",
    3: "glas",
    4: "sulfur",
}
BUILDING_ALIASES = {
    "hideout": "safehouse",
    "governorsResidence": "palaceColony",
    "chronos_forge": "chronosForge",
    "marketplace": "branchOffice",
    "carpentering": "carpentering",
    "vineyard": "vineyard",
}
MIN_RECHECK_SECONDS = 5 * 60
TRANSPORT_RECHECK_SECONDS = 30 * 60
FINISH_BUFFER_SECONDS = 2 * 60
DONOR_RESERVE_DEFAULT = 5_000


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(raw))
    except Exception:
        return default


def _as_city_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [item for item in raw.values() if isinstance(item, dict)]
    return []


def _city_name(city: dict[str, Any]) -> str:
    return str(city.get("name") or city.get("city_name") or city.get("id") or "?")


def _city_stock(city: dict[str, Any]) -> dict[str, int]:
    return {
        "wood": _to_int(city.get("wood"), 0),
        "wine": _to_int(city.get("wine"), 0),
        "marble": _to_int(city.get("marble"), 0),
        "glas": _to_int(city.get("crystal"), 0),
        "sulfur": _to_int(city.get("sulfur"), 0),
    }


def _city_prod_per_hour(city: dict[str, Any]) -> dict[str, int]:
    out = {key: 0 for key in RESOURCE_KEYS}
    out["wood"] = max(0, _to_int(city.get("resource_production_per_hour"), 0))
    tradegood = _to_int(city.get("produced_tradegood", city.get("tradegood", 0)), 0)
    luxury_key = TRADEGOOD_TO_RESOURCE.get(tradegood)
    if luxury_key:
        out[luxury_key] = max(0, _to_int(city.get("tradegood_production_per_hour"), 0))
    return out


def _find_city(cities: list[dict[str, Any]], city_id: str) -> dict[str, Any] | None:
    for city in cities:
        if str(city.get("id")) == str(city_id):
            return city
    return None


def _find_building(city: dict[str, Any], building_id: str) -> tuple[int, int | None]:
    for item in city.get("buildings") or []:
        if str(item.get("building") or "").strip() != building_id:
            continue
        return _to_int(item.get("level"), 0), _to_int(item.get("position"), 0)
    return 0, None


def _get_building_entry(city: dict[str, Any], building_id: str) -> dict[str, Any] | None:
    for item in city.get("buildings") or []:
        if str(item.get("building") or "").strip() == building_id:
            return item
    return None


def _get_building_entry_at_position(city: dict[str, Any], position: int) -> dict[str, Any] | None:
    wanted = _to_int(position, -1)
    if wanted < 0:
        return None
    for item in city.get("buildings") or []:
        if _to_int(item.get("position"), -1) == wanted:
            return item
    return None


def _get_upgrading_building(city: dict[str, Any]) -> dict[str, Any] | None:
    for item in city.get("buildings") or []:
        if bool(item.get("is_upgrading")):
            return item
    return None


def _find_empty_position(city: dict[str, Any], *, allowed_types: set[str] | None = None) -> int | None:
    empties: list[int] = []
    for item in city.get("buildings") or []:
        if str(item.get("building") or "").strip() == "empty":
            slot_type = str(item.get("type") or "").strip()
            if allowed_types and slot_type and slot_type not in allowed_types:
                continue
            empties.append(_to_int(item.get("position"), -1))
    empties = [pos for pos in empties if pos >= 0]
    return min(empties) if empties else None


def _snapshot_time(raw: Any) -> datetime | None:
    if not raw:
        return None


def _empty_resource_map() -> dict[str, int]:
    return {
        "wood": 0,
        "wine": 0,
        "marble": 0,
        "crystal": 0,
        "sulfur": 0,
    }
    try:
        value = str(raw).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _normalize_building_id(building_id: str) -> str:
    key = str(building_id or "").strip().split()[0]
    return BUILDING_ALIASES.get(key, key)


def _parse_building_options_html(html: str, slot_type: str) -> list[dict[str, Any]]:
    matches = list(
        re.finditer(
            r'<li class="building (.+?)">\s*<div class="buildinginfo">\s*<div title="(.+?)"\s*class="buildingimg .+?"\s*onclick="ajaxHandlerCall\(\'.*?buildingId=(\d+)&.*?</li>',
            html,
            re.S,
        )
    )
    if not matches:
        matches = list(
            re.finditer(
                r'<li class="building ([^"]+?)".*?<div title="([^"]+?)".*?buildingId=(\d+).*?</li>',
                html,
                re.IGNORECASE | re.DOTALL,
            )
        )
    out = []
    for match in matches:
        raw_classes = str(match.group(1)).strip()
        class_tokens = [token for token in raw_classes.split() if token]
        building_css = class_tokens[0] if class_tokens else ""
        researched = "notResearched" not in class_tokens
        out.append(
            {
                "building": _normalize_building_id(building_css),
                "name": str(match.group(2)).strip(),
                "building_id": str(match.group(3)).strip(),
                "slot_type": slot_type,
                "researched": researched,
                "locked_reason": "" if researched else "research",
            }
        )
    return out


def _merge_build_options_for_city(city: dict[str, Any], raw_options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    free_slots_by_type: dict[str, list[int]] = {}
    for item in city.get("buildings") or []:
        if str(item.get("building") or "").strip() != "empty":
            continue
        slot_type = str(item.get("type") or "").strip()
        position = _to_int(item.get("position"), -1)
        if not slot_type or position < 0:
            continue
        free_slots_by_type.setdefault(slot_type, []).append(position)

    grouped: dict[str, dict[str, Any]] = {}
    for option in raw_options:
        building_id = str(option.get("building") or "").strip()
        slot_type = str(option.get("slot_type") or "").strip()
        if not building_id or not slot_type:
            continue
        positions = sorted(pos for pos in free_slots_by_type.get(slot_type, []) if pos >= 0)
        if not positions:
            continue
        if building_id not in grouped:
            grouped[building_id] = {
                "building": building_id,
                "name": str(option.get("name") or building_id),
                "building_id": str(option.get("building_id") or ""),
                "slot_types": [],
                "available_positions": [],
                "preferred_position": 0,
                "researched": bool(option.get("researched", True)),
                "locked_reason": str(option.get("locked_reason") or ""),
            }
        entry = grouped[building_id]
        if slot_type not in entry["slot_types"]:
            entry["slot_types"].append(slot_type)
        entry["available_positions"] = sorted(set(entry["available_positions"] + positions))
        entry["preferred_position"] = min(entry["available_positions"]) if entry["available_positions"] else 0
        entry["researched"] = bool(entry.get("researched", True) and option.get("researched", True))
        if not entry["researched"] and not entry.get("locked_reason"):
            entry["locked_reason"] = str(option.get("locked_reason") or "research")

    return sorted(grouped.values(), key=lambda item: str(item.get("name") or item.get("building") or ""))


class _SnapshotMixin:
    def _get_snapshot(self, job_id: str, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel obter snapshot: {exc}")
            return None

    def _ensure_status_refresh(self, job_id: str) -> None:
        try:
            self.hub.spawn_job(job_id, action_code=100, inputs={})
            self.log(job_id, "info", "Check status solicitado para atualizar snapshot")
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel solicitar check_status: {exc}")


class _CityActionMixin(_SnapshotMixin):
    def _get_client(self, job: dict[str, Any]):
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        creds = self.resolve_credentials(aid, job.get("inputs") or {}, game_account_id=ga_id)
        if not creds:
            raise RuntimeError("missing_credentials")
        client = self.get_or_login_game_client(jid, aid, ga_id, creds)
        return client, ga_id


@register_runner(1001)
class BuildingsSyncRunner(_CityActionMixin, BaseRunner):
    """Sync per-city building options into the current snapshot."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        snapshot = self._get_snapshot(jid, ga_id)
        if snapshot is None:
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "waiting_snapshot"})

        updated_at = _snapshot_time(snapshot.get("updated_at"))
        if updated_at is None or (datetime.now(timezone.utc) - updated_at).total_seconds() > self.get_snapshot_stale_seconds():
            self.log(jid, "warn", "Snapshot antigo para sincronizar edificios; solicitando refresh")
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "stale_snapshot"})

        cities = _as_city_list(snapshot.get("cities"))
        if not cities:
            self.log(jid, "warn", "Snapshot sem cidades para sincronizar edificios")
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "empty_snapshot"})

        try:
            client, ga_id = self._get_client(job)
            synced_at = datetime.now(timezone.utc).isoformat()
            synced_cities: list[dict[str, Any]] = []

            for city in cities:
                city_copy = dict(city)
                city_id = str(city.get("id") or "").strip()
                if not city_id:
                    synced_cities.append(city_copy)
                    continue

                free_slots = [item for item in (city.get("buildings") or []) if str(item.get("building") or "").strip() == "empty"]
                slot_types = []
                for item in free_slots:
                    slot_type = str(item.get("type") or "").strip()
                    if slot_type and slot_type not in slot_types:
                        slot_types.append(slot_type)

                raw_options: list[dict[str, Any]] = []
                for slot_type in slot_types:
                    sample_slot = next((slot for slot in free_slots if str(slot.get("type") or "").strip() == slot_type), None)
                    if sample_slot is None:
                        continue
                    params = {
                        "view": "buildingGround",
                        "cityId": city_id,
                        "position": str(_to_int(sample_slot.get("position"), 0)),
                        "backgroundView": "city",
                        "currentCityId": city_id,
                        "actionRequest": client._action_request,
                        "ajax": "1",
                    }
                    resp = client._request("POST", client._server_url, data=params, headers=GAME_AJAX_HEADERS)
                    payload = resp.json()
                    html = ""
                    try:
                        html = payload[1][1][1]
                    except Exception:
                        html = ""
                    if not html:
                        continue
                    raw_options.extend(_parse_building_options_html(html, slot_type))

                city_copy["available_build_options"] = _merge_build_options_for_city(city_copy, raw_options)
                city_copy["build_options_synced_at"] = synced_at
                synced_cities.append(city_copy)
                self.log(
                    jid,
                    "info",
                    f"Edificios sincronizados: {_city_name(city_copy)} | {len(city_copy['available_build_options'])} opcoes",
                )

            base_snapshot = dict(snapshot.get("base_snapshot") or {})
            base_snapshot["buildings_sync"] = {
                "synced_at": synced_at,
                "city_count": len(synced_cities),
            }
            self.hub.update_snapshot(
                aid,
                {
                    "base_snapshot": base_snapshot,
                    "cities": synced_cities,
                    "military": snapshot.get("military") or {},
                    "source_job_id": jid,
                },
                game_account_id=ga_id,
            )
            if ga_id:
                self.save_game_client(ga_id, client)
            return RunnerResult(success=True, data={"status": "synced", "city_count": len(synced_cities)})
        except Exception as exc:
            self.log(jid, "error", f"Sync de edificios falhou: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(4)
class BuildBuildingRunner(_CityActionMixin, BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        inputs = job.get("inputs") or {}
        city_id = _to_int(inputs.get("city_id"))
        building_type = _normalize_building_id(str(inputs.get("building_type") or ""))
        position = _to_int(inputs.get("position"), -1)
        if city_id <= 0 or not building_type or position < 0:
            return RunnerResult(success=False, data={"error": "missing_build_inputs"})
        try:
            client, ga_id = self._get_client(job)
            client.build(city_id=city_id, building_type=building_type, position=position)
            if ga_id:
                self.save_game_client(ga_id, client)
            self.log(jid, "info", f"Construcao iniciada: {building_type} na posicao {position}")
            return RunnerResult(success=True)
        except Exception as exc:
            self.log(jid, "error", f"Build failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(5)
class UpgradeBuildingRunner(_CityActionMixin, BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        inputs = job.get("inputs") or {}
        city_id = _to_int(inputs.get("city_id"))
        building_position = _to_int(inputs.get("building_position", inputs.get("building_id")), -1)
        if city_id <= 0 or building_position < 0:
            return RunnerResult(success=False, data={"error": "missing_upgrade_inputs"})
        try:
            client, ga_id = self._get_client(job)
            client.upgrade(
                city_id=city_id,
                building_position=building_position,
                current_level=_to_int(inputs.get("current_level"), 0) or None,
                template_view=_normalize_building_id(str(inputs.get("building_type") or "city")),
            )
            if ga_id:
                self.save_game_client(ga_id, client)
            self.log(jid, "info", f"Evolucao iniciada na posicao {building_position}")
            return RunnerResult(success=True)
        except Exception as exc:
            self.log(jid, "error", f"Upgrade failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(19)
class DemolishBuildingRunner(_CityActionMixin, BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        inputs = job.get("inputs") or {}
        city_id = _to_int(inputs.get("city_id"))
        building_position = _to_int(inputs.get("building_position", inputs.get("building_id")), -1)
        if city_id <= 0 or building_position < 0:
            return RunnerResult(success=False, data={"error": "missing_demolish_inputs"})
        try:
            client, ga_id = self._get_client(job)
            client.demolish(
                city_id=city_id,
                building_position=building_position,
                template_view=_normalize_building_id(str(inputs.get("building_type") or "city")),
            )
            if ga_id:
                self.save_game_client(ga_id, client)
            self.log(jid, "info", f"Demolicao iniciada na posicao {building_position}")
            return RunnerResult(success=True)
        except Exception as exc:
            self.log(jid, "error", f"Demolish failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(1002)
class ConstructionPlanRunner(_CityActionMixin, BaseRunner):
    """Execute a multi-step construction plan one level at a time."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}
        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        snapshot = self._get_snapshot(jid, ga_id)
        if snapshot is None:
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "waiting_snapshot"})

        updated_at = _snapshot_time(snapshot.get("updated_at"))
        if updated_at is None or (datetime.now(timezone.utc) - updated_at).total_seconds() > self.get_snapshot_stale_seconds():
            self.log(jid, "warn", "Snapshot antigo para construcao; solicitando refresh")
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "stale_snapshot"})

        cities = _as_city_list(snapshot.get("cities"))
        plan_steps = self._normalized_plan(inputs)
        if not plan_steps:
            return RunnerResult(success=False, data={"error": "missing_plan"})

        pending_steps = self._pick_pending_steps(cities, plan_steps, str(inputs.get("queue_strategy") or "fifo"))
        if not pending_steps:
            self.log(jid, "info", "Plano de construcao concluido")
            return RunnerResult(success=True, data={"status": "complete"})

        ready_steps: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        wait_reasons: list[dict[str, Any]] = []
        refresh_needed = False
        support_by_city = self._get_open_construction_support(jid)

        for pending in pending_steps:
            city = _find_city(cities, pending["city_id"])
            if city is None:
                self.log(jid, "warn", f"Cidade {pending['city_id']} nao encontrada no snapshot atual")
                refresh_needed = True
                wait_reasons.append({"status": "city_missing", "wait_seconds": MIN_RECHECK_SECONDS})
                continue

            upgrading = _get_upgrading_building(city)
            if upgrading is not None:
                upgrading_name = str(upgrading.get("building") or "?")
                upgrading_level = _to_int(upgrading.get("level"), 0)
                wait_seconds = MIN_RECHECK_SECONDS
                if upgrading_name == pending["building_id"]:
                    in_flight_row = self._find_level_row(pending, upgrading_level + 1)
                    if in_flight_row is not None:
                        wait_seconds = max(
                            MIN_RECHECK_SECONDS,
                            _to_int(in_flight_row.get("adjusted_seconds"), 0) + FINISH_BUFFER_SECONDS,
                        )
                self.log(
                    jid,
                    "info",
                    f"Cidade {_city_name(city)} ainda esta em obra ({upgrading_name} lvl {upgrading_level}); aguardando antes da proxima etapa",
                )
                wait_reasons.append(
                    {
                        "status": "waiting_city_busy",
                        "wait_seconds": wait_seconds,
                        "city_id": pending["city_id"],
                        "building_id": pending["building_id"],
                    }
                )
                continue

            next_level = pending["next_level"]
            level_row = self._find_level_row(pending, next_level)
            if level_row is None:
                self.log(jid, "warn", f"Etapa sem detalhe de custo para o nivel {next_level}; aguardando novo snapshot")
                refresh_needed = True
                wait_reasons.append(
                    {
                        "status": "missing_level_row",
                        "wait_seconds": MIN_RECHECK_SECONDS,
                        "city_id": pending["city_id"],
                        "building_id": pending["building_id"],
                    }
                )
                continue

            costs = self._normalize_costs(level_row.get("costs") or {})
            stock = _city_stock(city)
            missing = {key: max(0, costs[key] - stock.get(key, 0)) for key in RESOURCE_KEYS}
            if any(amount > 0 for amount in missing.values()):
                reschedule_seconds = self._handle_missing_resources(
                    job=job,
                    pending=pending,
                    cities=cities,
                    city=city,
                    missing=missing,
                    support_by_city=support_by_city,
                )
                wait_reasons.append(
                    {
                        "status": "waiting_resources",
                        "wait_seconds": reschedule_seconds,
                        "city_id": pending["city_id"],
                        "building_id": pending["building_id"],
                        "missing": missing,
                    }
                )
                continue

            ready_steps.append((pending, city, level_row))

        if refresh_needed:
            self._ensure_status_refresh(jid)

        if not ready_steps:
            next_wait = min(
                [int(reason.get("wait_seconds") or MIN_RECHECK_SECONDS) for reason in wait_reasons] or [MIN_RECHECK_SECONDS]
            )
            return RunnerResult(
                success=True,
                reschedule_seconds=next_wait,
                data={"status": "waiting_parallel", "waiting": wait_reasons},
            )

        try:
            client, ga_id = self._get_client(job)
            started: list[dict[str, Any]] = []
            delays: list[int] = []
            new_waits = list(wait_reasons)

            for pending, city, level_row in ready_steps:
                next_level = pending["next_level"]
                building_id = _normalize_building_id(pending["building_id"])
                city_id = _to_int(pending["city_id"])
                step_mode = str(pending.get("mode") or "upgrade")
                if step_mode == "new":
                    preferred_position = _to_int(pending.get("preferred_position"), 0)
                    slot_entry = _get_building_entry_at_position(city, preferred_position)
                    if slot_entry and str(slot_entry.get("building") or "").strip() == pending["building_id"]:
                        current_level = _to_int(slot_entry.get("level"), 0)
                        position = _to_int(slot_entry.get("position"), 0)
                        current_entry = slot_entry
                    else:
                        current_level = 0
                        position = preferred_position if preferred_position > 0 else None
                        current_entry = None
                else:
                    current_level, position = _find_building(city, pending["building_id"])
                    current_entry = _get_building_entry(city, pending["building_id"])

                if current_entry and bool(current_entry.get("is_upgrading")):
                    wait_seconds = max(MIN_RECHECK_SECONDS, _to_int(level_row.get("adjusted_seconds"), 0) + FINISH_BUFFER_SECONDS)
                    self.log(
                        jid,
                        "info",
                        f"{pending['building_name']} ainda esta em progresso em {_city_name(city)}; aguardando conclusao",
                    )
                    new_waits.append(
                        {
                            "status": "waiting_step_busy",
                            "wait_seconds": wait_seconds,
                            "city_id": pending["city_id"],
                            "building_id": pending["building_id"],
                        }
                    )
                    continue

                if current_level <= 0:
                    allowed_types = set(pending.get("slot_types") or [])
                    empty_position = None
                    preferred_position = _to_int(pending.get("preferred_position"), 0)
                    if preferred_position > 0:
                        for item in city.get("buildings") or []:
                            if _to_int(item.get("position"), -1) != preferred_position:
                                continue
                            if str(item.get("building") or "").strip() != "empty":
                                continue
                            slot_type = str(item.get("type") or "").strip()
                            if allowed_types and slot_type and slot_type not in allowed_types:
                                continue
                            empty_position = preferred_position
                            break
                    if empty_position is None:
                        empty_position = _find_empty_position(city, allowed_types=allowed_types or None)
                    if empty_position is None:
                        raise RuntimeError(f"no_empty_slot:{','.join(sorted(allowed_types)) or 'any'}")
                    client.build(city_id=city_id, building_type=building_id, position=empty_position)
                    self.log(
                        jid,
                        "info",
                        f"Construcao iniciada: {_city_name(city)} | {pending['building_name']} | pos {empty_position} | tipo={','.join(sorted(allowed_types)) or 'any'} | lvl 0 -> 1",
                    )
                else:
                    if position is None:
                        raise RuntimeError("building_position_not_found")
                    client.upgrade(
                        city_id=city_id,
                        building_position=position,
                        current_level=current_level,
                        template_view=building_id,
                    )
                    self.log(
                        jid,
                        "info",
                        f"Evolucao iniciada: {_city_name(city)} | {pending['building_name']} | lvl {current_level} -> {next_level}",
                    )

                delay = max(90, _to_int(level_row.get("adjusted_seconds"), 0) + FINISH_BUFFER_SECONDS)
                delays.append(delay)
                started.append(
                    {
                        "city_id": pending["city_id"],
                        "city_name": pending["city_name"],
                        "building_id": pending["building_id"],
                        "building_name": pending["building_name"],
                        "to_level": next_level,
                        "delay": delay,
                    }
                )

            if ga_id:
                self.save_game_client(ga_id, client)

            if not started:
                next_wait = min(
                    [int(reason.get("wait_seconds") or MIN_RECHECK_SECONDS) for reason in new_waits] or [MIN_RECHECK_SECONDS]
                )
                return RunnerResult(
                    success=True,
                    reschedule_seconds=next_wait,
                    data={"status": "waiting_parallel", "waiting": new_waits},
                )

            next_wait = min(delays + [int(reason.get("wait_seconds") or MIN_RECHECK_SECONDS) for reason in new_waits])
            self.log(
                jid,
                "info",
                f"{len(started)} obra(s) iniciada(s) neste ciclo; proximo check do plano em {next_wait}s",
            )
            return RunnerResult(
                success=True,
                reschedule_seconds=next_wait,
                data={"status": "started_parallel", "started": started, "waiting": new_waits},
            )
        except Exception as exc:
            self.log(jid, "error", f"Plano de construcao falhou: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

    @staticmethod
    def _normalized_plan(inputs: dict[str, Any]) -> list[dict[str, Any]]:
        raw_steps = inputs.get("construction_plan_steps") or inputs.get("construction_plan_json") or []
        out: list[dict[str, Any]] = []
        if not isinstance(raw_steps, list):
            return out
        for idx, step in enumerate(raw_steps, start=1):
            if not isinstance(step, dict):
                continue
            city_id = str(step.get("city_id") or step.get("city") or "").strip()
            building_id = str(step.get("building_id") or step.get("building_type") or "").strip()
            if not city_id or not building_id:
                continue
            out.append({
                "index": _to_int(step.get("index"), idx),
                "city_id": city_id,
                "city_name": str(step.get("city_name") or city_id),
                "building_id": building_id,
                "building_name": str(step.get("building_name") or building_id),
                "mode": str(step.get("mode") or "upgrade"),
                "slot_types": [str(x).strip() for x in (step.get("slot_types") or []) if str(x).strip()],
                "preferred_position": _to_int(step.get("preferred_position"), 0),
                "target_level": _to_int(step.get("target_level"), 0),
                "level_rows": step.get("level_rows") or [],
            })
        return out

    @staticmethod
    def _pick_pending_step(cities: list[dict[str, Any]], plan_steps: list[dict[str, Any]]) -> dict[str, Any] | None:
        for step in sorted(plan_steps, key=lambda item: (_to_int(item.get("index"), 0), str(item.get("city_id")))):
            city = _find_city(cities, step["city_id"])
            if city is None:
                continue
            if str(step.get("mode") or "upgrade") == "new":
                preferred_position = _to_int(step.get("preferred_position"), 0)
                slot_entry = _get_building_entry_at_position(city, preferred_position)
                if slot_entry and str(slot_entry.get("building") or "").strip() == step["building_id"]:
                    current_level = _to_int(slot_entry.get("level"), 0)
                else:
                    current_level = 0
            else:
                current_level, _ = _find_building(city, step["building_id"])
            target_level = _to_int(step.get("target_level"), 0)
            if current_level >= target_level > 0:
                continue
            pending = dict(step)
            pending["current_level"] = current_level
            pending["next_level"] = max(1, current_level + 1)
            return pending
        return None

    @classmethod
    def _pick_pending_steps(
        cls,
        cities: list[dict[str, Any]],
        plan_steps: list[dict[str, Any]],
        queue_strategy: str = "fifo",
    ) -> list[dict[str, Any]]:
        candidates_by_city: dict[str, list[dict[str, Any]]] = {}
        city_order: list[str] = []
        for step in sorted(plan_steps, key=lambda item: (_to_int(item.get("index"), 0), str(item.get("city_id")))):
            city_id = str(step.get("city_id") or "")
            if not city_id:
                continue
            city = _find_city(cities, city_id)
            if city is None:
                pending = dict(step)
                pending["current_level"] = 0
                pending["next_level"] = max(1, _to_int(step.get("target_level"), 1))
            else:
                if str(step.get("mode") or "upgrade") == "new":
                    preferred_position = _to_int(step.get("preferred_position"), 0)
                    slot_entry = _get_building_entry_at_position(city, preferred_position)
                    if slot_entry and str(slot_entry.get("building") or "").strip() == step["building_id"]:
                        current_level = _to_int(slot_entry.get("level"), 0)
                    else:
                        current_level = 0
                else:
                    current_level, _ = _find_building(city, step["building_id"])
                target_level = _to_int(step.get("target_level"), 0)
                if current_level >= target_level > 0:
                    continue
                pending = dict(step)
                pending["current_level"] = current_level
                pending["next_level"] = max(1, current_level + 1)
            if city_id not in candidates_by_city:
                candidates_by_city[city_id] = []
                city_order.append(city_id)
            candidates_by_city[city_id].append(pending)

        strategy = str(queue_strategy or "fifo").strip().lower()
        selected: list[dict[str, Any]] = []
        for city_id in city_order:
            city_candidates = candidates_by_city.get(city_id) or []
            if not city_candidates:
                continue
            if strategy == "eta_first":
                city = _find_city(cities, city_id)
                city_candidates = sorted(
                    city_candidates,
                    key=lambda step: cls._pending_step_score(city, step),
                )
            selected.append(city_candidates[0])
        return selected

    @classmethod
    def _pending_step_score(cls, city: dict[str, Any] | None, step: dict[str, Any]) -> tuple[int, int, int, int]:
        next_level = _to_int(step.get("next_level"), 1)
        level_row = cls._find_level_row(step, next_level)
        if city is None or level_row is None:
            return (9_999_999, 9_999_999, 9_999_999, _to_int(step.get("index"), 0))
        costs = cls._normalize_costs(level_row.get("costs") or {})
        stock = _city_stock(city)
        missing_total = sum(max(0, costs[key] - stock.get(key, 0)) for key in RESOURCE_KEYS)
        adjusted_seconds = max(0, _to_int(level_row.get("adjusted_seconds"), 0))
        cost_sum = sum(max(0, costs[key]) for key in RESOURCE_KEYS)
        return (missing_total, adjusted_seconds, cost_sum, _to_int(step.get("index"), 0))

    @staticmethod
    def _find_level_row(step: dict[str, Any], level: int) -> dict[str, Any] | None:
        for row in step.get("level_rows") or []:
            if _to_int(row.get("level"), 0) == int(level):
                return row
        return None

    @staticmethod
    def _normalize_costs(raw_costs: dict[str, Any]) -> dict[str, int]:
        return {key: _to_int(raw_costs.get(key), 0) for key in RESOURCE_KEYS}

    def _handle_missing_resources(
        self,
        *,
        job: dict[str, Any],
        pending: dict[str, Any],
        cities: list[dict[str, Any]],
        city: dict[str, Any],
        missing: dict[str, int],
        support_by_city: dict[str, dict[str, int]],
    ) -> int:
        jid = job["job_id"]
        inputs = job.get("inputs") or {}
        auto_transport = bool(inputs.get("auto_transport", True))
        target_city_id = str(city.get("id") or "")

        existing_support = support_by_city.get(target_city_id) or _empty_resource_map()
        adjusted_missing = {
            key: max(0, int(missing.get(key, 0)) - int(existing_support.get(SNAPSHOT_RESOURCE_TO_INPUT.get(key, key), 0)))
            for key in RESOURCE_KEYS
        }
        if any(existing_support.values()):
            support_bits = []
            for resource_key in ("wood", "wine", "marble", "crystal", "sulfur"):
                amount = int(existing_support.get(resource_key, 0))
                if amount > 0:
                    support_bits.append(f"{resource_key}={amount}")
            if support_bits:
                self.log(
                    jid,
                    "info",
                    f"Suporte em aberto para {pending['city_name']} abatido do calculo: {', '.join(support_bits)}",
                )
        missing = adjusted_missing

        wait_seconds = self._estimate_local_wait_seconds(city, missing)
        if wait_seconds:
            self.log(
                jid,
                "info",
                f"{pending['city_name']} ainda sem recurso para {pending['building_name']}; ETA local estimado em {wait_seconds}s",
            )

        if auto_transport:
            try:
                aid = job["account_id"]
                ga_id = job.get("game_account_id")
                creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
                if creds:
                    client = self.get_or_login_game_client(jid, aid, ga_id, creds)
                    incoming_wait = estimate_incoming_transport_wait_seconds(client, str(city.get("id")))
                    if incoming_wait:
                        self.log(
                            jid,
                            "info",
                            (
                                f"Ja existe transporte em rota para {pending['city_name']} "
                                f"({city.get('id')}); aguardando {incoming_wait}s antes de pedir nova remessa"
                            ),
                        )
                        if ga_id:
                            self.save_game_client(ga_id, client)
                        return max(wait_seconds or 0, incoming_wait + FINISH_BUFFER_SECONDS)
                    if ga_id:
                        self.save_game_client(ga_id, client)
            except Exception as exc:
                self.log(jid, "warn", f"Nao foi possivel verificar transportes em rota: {exc}")

            transported = self._spawn_transport_cover(
                job_id=jid,
                cities=cities,
                target_city=city,
                pending=pending,
                missing=missing,
                support_by_city=support_by_city,
            )
            if transported:
                self.log(
                    jid,
                    "info",
                    f"Suporte logistico criado para {pending['city_name']} | aguardando transporte antes da proxima obra",
                )
                return max(wait_seconds or 0, TRANSPORT_RECHECK_SECONDS)

        if wait_seconds:
            return max(MIN_RECHECK_SECONDS, wait_seconds + FINISH_BUFFER_SECONDS)

        self.log(
            jid,
            "warn",
            f"{pending['city_name']} sem recurso suficiente para {pending['building_name']} e sem ETA local confiavel",
        )
        return TRANSPORT_RECHECK_SECONDS

    def _spawn_transport_cover(
        self,
        *,
        job_id: str,
        cities: list[dict[str, Any]],
        target_city: dict[str, Any],
        pending: dict[str, Any],
        missing: dict[str, int],
        support_by_city: dict[str, dict[str, int]],
    ) -> bool:
        next_rows = []
        for row in pending.get("level_rows") or []:
            if _to_int(row.get("level"), 0) >= int(pending["next_level"]):
                next_rows.append(row)
            if len(next_rows) >= 2:
                break
        if not next_rows:
            return False

        target_stock = _city_stock(target_city)
        desired_cover = {key: 0 for key in RESOURCE_KEYS}
        for row in next_rows:
            costs = self._normalize_costs(row.get("costs") or {})
            for key in RESOURCE_KEYS:
                desired_cover[key] += costs[key]

        transport_need = {
            key: max(0, desired_cover[key] - target_stock.get(key, 0))
            for key in RESOURCE_KEYS
        }
        existing_support = support_by_city.get(str(target_city.get("id") or "")) or _empty_resource_map()
        for key in RESOURCE_KEYS:
            input_key = SNAPSHOT_RESOURCE_TO_INPUT[key]
            transport_need[key] = max(0, transport_need[key] - int(existing_support.get(input_key, 0)))
        if not any(amount > 0 for amount in transport_need.values()):
            return False

        donor = self._pick_donor_city(cities=cities, target_city_id=str(target_city.get("id")), needed=transport_need)
        if donor is None:
            self.log(job_id, "warn", f"Nenhuma cidade doadora com sobra util para {pending['city_name']}")
            return False

        donor_stock = _city_stock(donor)
        payload: dict[str, Any] = {
            "from_city": str(donor.get("id")),
            "from_city_name": _city_name(donor),
            "to_city": str(target_city.get("id")),
            "to_city_name": _city_name(target_city),
            "transport_load_percent": "100",
            "confirm_arrival": True,
            "confirmation_margin_minutes": 2,
        }
        for key in RESOURCE_KEYS:
            input_key = SNAPSHOT_RESOURCE_TO_INPUT[key]
            available = max(0, donor_stock.get(key, 0) - DONOR_RESERVE_DEFAULT)
            amount = min(transport_need[key], available)
            if amount > 0:
                payload[input_key] = int(amount)

        if not any(_to_int(payload.get(field), 0) > 0 for field in ("wood", "wine", "marble", "crystal", "sulfur")):
            return False

        self.hub.spawn_job(job_id, action_code=2, inputs=payload)
        self.log(
            job_id,
            "info",
            (
                "Remessa criada para cobrir ate 2 niveis: "
                f"{payload['from_city_name']} ({payload['from_city']}) -> "
                f"{payload['to_city_name']} ({payload['to_city']})"
            ),
        )
        target_key = str(target_city.get("id") or "")
        if target_key:
            support_by_city.setdefault(target_key, _empty_resource_map())
            for field in ("wood", "wine", "marble", "crystal", "sulfur"):
                support_by_city[target_key][field] += _to_int(payload.get(field), 0)
        return True

    def _get_open_construction_support(self, job_id: str) -> dict[str, dict[str, int]]:
        support_by_city: dict[str, dict[str, int]] = {}
        try:
            payload = self.hub.get_construction_support(job_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel consultar suporte de construcao ja aberto: {exc}")
            return support_by_city

        entries = payload.get("entries") if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            return support_by_city

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            to_city = str(entry.get("to_city") or "").strip()
            if not to_city:
                continue
            resources = entry.get("resources") if isinstance(entry.get("resources"), dict) else {}
            if not resources:
                continue
            bucket = support_by_city.setdefault(to_city, _empty_resource_map())
            for field in ("wood", "wine", "marble", "crystal", "sulfur"):
                bucket[field] += _to_int(resources.get(field), 0)
        return support_by_city

    @staticmethod
    def _pick_donor_city(
        *,
        cities: list[dict[str, Any]],
        target_city_id: str,
        needed: dict[str, int],
    ) -> dict[str, Any] | None:
        best_city = None
        best_score = 0
        for city in cities:
            if str(city.get("id")) == str(target_city_id):
                continue
            stock = _city_stock(city)
            score = 0
            for key, amount in needed.items():
                if amount <= 0:
                    continue
                score += max(0, stock.get(key, 0) - DONOR_RESERVE_DEFAULT)
            if score > best_score:
                best_score = score
                best_city = city
        return best_city if best_score > 0 else None

    @staticmethod
    def _estimate_local_wait_seconds(city: dict[str, Any], missing: dict[str, int]) -> int | None:
        production = _city_prod_per_hour(city)
        waits: list[int] = []
        for key, amount in missing.items():
            if amount <= 0:
                continue
            rate = production.get(key, 0)
            if rate <= 0:
                continue
            waits.append(int((amount / rate) * 3600))
        if not waits:
            return None
        return max(waits)
