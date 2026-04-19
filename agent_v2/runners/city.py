"""Construction and city-management runners."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any

from core.runner_registry import register_runner
from game_client.constants import GAME_AJAX_HEADERS
from runners.base import BaseRunner, RunnerResult
from services.resource_transport import estimate_incoming_transport_wait_seconds, change_current_city
from services.island_donation import extract_city_data

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

RESOURCE_PT = {
    "wood": "madeira",
    "wine": "vinho",
    "marble": "mármore",
    "glas": "vidro",
    "sulfur": "enxofre",
}


def _format_duration(seconds: int) -> str:
    """Convert seconds to human-readable duration string."""
    if seconds <= 0:
        return "0s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m:02d}m" if m > 0 else f"{h}h"
    if m > 0:
        return f"{m}m {s:02d}s" if s > 0 else f"{m}m"
    return f"{s}s"


def _format_costs(costs: dict[str, int]) -> str:
    """Format resource costs as 'madeira=15.000 | vidro=800' string."""
    parts = []
    for key in RESOURCE_KEYS:
        amount = costs.get(key, 0)
        if amount > 0:
            label = RESOURCE_PT.get(key, key)
            parts.append(f"{label}={amount:,}".replace(",", "."))
    return " | ".join(parts) if parts else "sem custo"


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(raw))
    except Exception:
        return default


def _to_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except Exception:
        return default


def _apply_world_time_reduction(base_seconds: int, world_reduction_pct: int) -> int:
    """Apply world/server build-time reduction percentage to a base duration."""
    if world_reduction_pct <= 0 or base_seconds <= 0:
        return base_seconds
    factor = 1.0 - (world_reduction_pct / 100.0)
    return max(1, int(math.ceil(base_seconds * factor)))


def _resolve_world_time_reduction(config: dict[str, Any], ga_id: str | None) -> int:
    """Extract build_time_reduction for a specific game account from hub config."""
    if not ga_id:
        return 0
    try:
        for account in (config.get("accounts") or []):
            for ga in (account.get("game_accounts") or []):
                if str(ga.get("id") or "") == str(ga_id):
                    return int(ga.get("build_time_reduction") or 0)
    except Exception:
        pass
    return 0


def _resolve_government_time_reduction(config: dict[str, Any], ga_id: str | None) -> int:
    """Extract government_time_reduction for a specific game account from hub config."""
    if not ga_id:
        return 0
    try:
        for account in (config.get("accounts") or []):
            for ga in (account.get("game_accounts") or []):
                if str(ga.get("id") or "") == str(ga_id):
                    return int(ga.get("government_time_reduction") or 0)
    except Exception:
        pass
    return 0


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


def _building_ids_to_match(building_id: str) -> set[str]:
    """Return all snapshot keys that correspond to the given building_id.

    The snapshot may store a building under a different key than what the job
    uses (e.g. job uses 'governorsResidence' but snapshot stores 'palaceColony').
    We resolve in both directions so the lookup is alias-agnostic.
    """
    ids = {building_id}
    # Direct alias: job key → snapshot key
    if building_id in BUILDING_ALIASES:
        ids.add(BUILDING_ALIASES[building_id])
    # Reverse alias: snapshot key → job key
    for k, v in BUILDING_ALIASES.items():
        if v == building_id:
            ids.add(k)
    return ids


def _find_building(city: dict[str, Any], building_id: str) -> tuple[int, int | None]:
    candidates = _building_ids_to_match(building_id)
    for item in city.get("buildings") or []:
        if str(item.get("building") or "").strip() not in candidates:
            continue
        return _to_int(item.get("level"), 0), _to_int(item.get("position"), 0)
    return 0, None


def _get_building_entry(city: dict[str, Any], building_id: str) -> dict[str, Any] | None:
    candidates = _building_ids_to_match(building_id)
    for item in city.get("buildings") or []:
        if str(item.get("building") or "").strip() in candidates:
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


def _live_city_building_state(client, city_id: int, position: int) -> dict[str, Any] | None:
    """Read the current state of one city position directly from the game page.

    Uses the city JSON embedded in the page (updateBackgroundData) which has
    authoritative isBusy / endUpgradeTime fields. The old CSS-class approach
    (looking for 'constructionSite' in the DOM) is unreliable because the game
    does not add that class when upgrading an existing building.
    """
    resp = client._request("GET", client._server_url, params={"view": "city", "cityId": city_id})
    html = resp.text
    try:
        city = extract_city_data(html)
    except Exception as exc:
        logger.debug("extract_city_data failed for city %s: %s", city_id, exc)
        return None

    positions = city.get("position", [])
    if position < 0 or position >= len(positions):
        return None
    pos = positions[position]
    if not isinstance(pos, dict):
        return None

    building_name = str(pos.get("building") or "").strip()
    is_empty = "buildingGround" in building_name or building_name == ""
    if is_empty:
        building_name = "empty"

    # isBusy is the authoritative flag: True when this slot is currently upgrading
    is_upgrading = bool(pos.get("isBusy"))

    # endUpgradeTime is a city-level field that applies to whichever position is upgrading
    construction_end_at = 0
    if is_upgrading:
        under_construction = _to_int(city.get("underConstruction"), -1)
        if under_construction == position:
            end_time = _to_int(city.get("endUpgradeTime"), 0)
            if end_time > 0:
                construction_end_at = end_time

    state: dict[str, Any] = {
        "position": position,
        "building": building_name,
        "level": _to_int(pos.get("level"), 0),
        "is_upgrading": is_upgrading,
    }
    if construction_end_at > 0:
        state["construction_end_at"] = construction_end_at
    return state


def _get_active_constructions_via_advisor(client) -> dict[str, dict[str, Any]]:
    """Query buildingAdvisor and return a map of city_id -> construction info.

    Returns empty dict on any failure so callers can fall back gracefully.
    Each value has: {city_id, building_name, end_time (unix timestamp or 0)}.
    """
    try:
        resp = client._request(
            "GET",
            client._server_url,
            params={"view": "buildingAdvisor", "ajax": "1"},
            headers={"Accept": "*/*", "X-Requested-With": "XMLHttpRequest"},
        )
        payload = resp.json()
    except Exception as exc:
        logger.debug("buildingAdvisor request failed: %s", exc)
        return {}

    html = ""
    try:
        for cmd in payload:
            if isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == "changeView":
                inner = cmd[1]
                if isinstance(inner, (list, tuple)) and len(inner) >= 2:
                    html = str(inner[1] or "")
                elif isinstance(inner, str):
                    html = inner
                if html:
                    break
    except Exception:
        pass

    if not html:
        return {}

    result: dict[str, dict[str, Any]] = {}
    # Each city construction row has a data attribute with city id and building info.
    # Pattern: data-cityid="12345" ... class="buildingname" ... data-end="1713400000"
    # We also parse simple table rows: <tr ... data-cityid="...">
    for row_match in re.finditer(
        r'data-cityid=["\'](\d+)["\'][^>]*>.*?</tr>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        city_id = row_match.group(1)
        row_html = row_match.group(0)
        end_time = 0
        end_m = re.search(r'data-end(?:time)?=["\'](\d+)["\']', row_html, re.IGNORECASE)
        if end_m:
            end_time = _to_int(end_m.group(1), 0)
        name_m = re.search(r'class=["\'][^"\']*building[^"\']*["\'][^>]*>\s*([^<]{2,40})', row_html, re.IGNORECASE)
        building_name = name_m.group(1).strip() if name_m else ""
        result[city_id] = {"city_id": city_id, "building_name": building_name, "end_time": end_time}

    # Fallback: look for any occurrence of city ids next to finish times in the HTML
    if not result:
        for m in re.finditer(r'cityId=(\d+)[^"\']*["\']', html):
            city_id = m.group(1)
            if city_id not in result:
                result[city_id] = {"city_id": city_id, "building_name": "", "end_time": 0}

    return result


def _action_feedback(parsed: dict[str, Any] | None) -> str:
    if not isinstance(parsed, dict):
        return ""
    parts: list[str] = []
    for key in ("errors", "notifications"):
        values = parsed.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            text = str(item or "").strip()
            if text:
                parts.append(text)
    return " | ".join(parts)


def _confirm_building_state(
    client,
    *,
    city_id: int,
    position: int,
    next_level: int,
    building_name: str,
    city_name: str,
    expect_build: bool,
    action_feedback: str = "",
    attempts: int = 5,
) -> dict[str, Any]:
    """Retry live city reads briefly before declaring build/upgrade unconfirmed.

    For a new build (expect_build=True), the game may briefly keep showing
    the slot as empty even after accepting the build command — but isBusy will
    become True almost immediately.  We accept either condition as confirmation.
    """
    # Give the game server a moment to register the command before first read.
    time.sleep(2)
    last_state: dict[str, Any] | None = None
    for attempt in range(1, max(1, attempts) + 1):
        live_state = _live_city_building_state(client, city_id, position)
        if live_state:
            last_state = live_state
            live_level = _to_int(live_state.get("level"), 0)
            live_busy = bool(live_state.get("is_upgrading"))
            live_building = str(live_state.get("building") or "").strip()
            if expect_build:
                # Accept: slot occupied with any building OR slot is now busy
                # (game shows isBusy before the building name resolves).
                if live_building not in {"", "empty"} or live_busy:
                    return live_state
            else:
                if live_busy or live_level >= next_level:
                    return live_state
        if attempt < attempts:
            time.sleep(float(attempt))

    if expect_build:
        detail = (
            f"build_not_confirmed:{city_name}:{building_name}:pos={position}:"
            f"state={last_state or {}}"
        )
    else:
        detail = (
            f"upgrade_not_confirmed:{city_name}:{building_name}:pos={position}:"
            f"state={last_state or {}}:target={next_level}"
        )
    if action_feedback:
        detail = f"{detail}:feedback={action_feedback}"
    raise RuntimeError(detail)


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
    try:
        value = str(raw).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _empty_resource_map() -> dict[str, int]:
    return {
        "wood": 0,
        "wine": 0,
        "marble": 0,
        "crystal": 0,
        "sulfur": 0,
    }


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
            self.log(jid, "warn", "Snapshot ausente para sincronizar edificios; aguardando novo check_status")
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "waiting_snapshot"})

        updated_at = _snapshot_time(snapshot.get("updated_at"))
        if updated_at is None or (datetime.now(timezone.utc) - updated_at).total_seconds() > self.get_snapshot_stale_seconds():
            self.log(jid, "warn", "Snapshot antigo para sincronizar edificios; aguardando refresh de status")
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "stale_snapshot"})

        cities = _as_city_list(snapshot.get("cities"))
        if not cities:
            self.log(jid, "warn", "Snapshot sem cidades para sincronizar edificios; aguardando refresh de status")
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

        # Ensure snapshot is newer than the last time we started builds.
        # Without this, the agent would try to start builds on a stale snapshot
        # that doesn't yet show the buildings as upgrading.
        last_builds_started_at = _to_float(inputs.get("last_builds_started_at"), 0.0)
        if last_builds_started_at > 0 and updated_at is not None:
            if updated_at.timestamp() < last_builds_started_at:
                age = int(time.time() - last_builds_started_at)
                self.log(jid, "info", f"Snapshot anterior ao ultimo inicio de obra ({age}s atras); aguardando refresh")
                self._ensure_status_refresh(jid)
                return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "waiting_post_build_snapshot"})

        last_transport_dispatched_at = _to_float(inputs.get("last_transport_dispatched_at"), 0.0)
        if last_transport_dispatched_at > 0 and updated_at is not None:
            if updated_at.timestamp() < last_transport_dispatched_at:
                age = int(time.time() - last_transport_dispatched_at)
                self.log(jid, "info", f"Snapshot anterior ao ultimo transporte de suporte ({age}s atras); aguardando refresh")
                self._ensure_status_refresh(jid)
                return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "waiting_post_transport_snapshot"})

        # Resolve time reductions for this game account
        agent_config = self.get_agent_config()
        world_time_reduction = _resolve_world_time_reduction(agent_config, ga_id)
        government_time_reduction = _resolve_government_time_reduction(agent_config, ga_id)

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
        # Cities where snapshot says a building is upgrading — need live verification
        # before trusting the wait (snapshot can be stale after a manual cancel or after
        # the previous execution completed the upgrade).
        busy_pending: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        refresh_needed = False
        any_transport_dispatched = False
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
                # Defer decision: verify live before committing to a long wait
                busy_pending.append((pending, city, upgrading))
                continue

            next_level = pending["next_level"]
            level_row = self._find_level_row(pending, next_level)
            if level_row is None:
                stored_levels = [r.get("level") for r in (pending.get("level_rows") or [])]
                self.log(
                    jid, "error",
                    f"Etapa sem custo para nivel {next_level} em {pending.get('building_name')} @ {pending.get('city_name')}. "
                    f"Niveis armazenados: {stored_levels}. "
                    f"Recrie o job para regenerar os dados de custo."
                )
                return RunnerResult(
                    success=False,
                    data={
                        "status": "missing_level_row",
                        "city_id": pending["city_id"],
                        "building_id": pending["building_id"],
                        "next_level": next_level,
                        "stored_levels": stored_levels,
                    },
                )
                continue

            costs = self._normalize_costs(level_row.get("costs") or {})
            stock = _city_stock(city)
            missing = {key: max(0, costs[key] - stock.get(key, 0)) for key in RESOURCE_KEYS}
            if any(amount > 0 for amount in missing.values()):
                reschedule_seconds, transport_spawned = self._handle_missing_resources(
                    job=job,
                    pending=pending,
                    cities=cities,
                    city=city,
                    missing=missing,
                    support_by_city=support_by_city,
                )
                any_transport_dispatched = any_transport_dispatched or transport_spawned
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

        if not ready_steps and not busy_pending:
            next_wait = min(
                [int(reason.get("wait_seconds") or MIN_RECHECK_SECONDS) for reason in wait_reasons] or [MIN_RECHECK_SECONDS]
            )
            reschedule_inputs = {"last_transport_dispatched_at": int(time.time())} if any_transport_dispatched else None
            return RunnerResult(
                success=True,
                reschedule_seconds=next_wait,
                reschedule_inputs=reschedule_inputs,
                data={"status": "waiting_parallel", "waiting": wait_reasons},
            )

        try:
            client, ga_id = self._get_client(job)
            started: list[dict[str, Any]] = []
            delays: list[int] = []
            new_waits = list(wait_reasons)
            step_failures: list[dict[str, Any]] = []

            # Live-verify cities where snapshot indicated a building is upgrading.
            # If the game doesn't confirm it (stale snapshot after cancel or completion),
            # promote them to ready_steps instead of waiting.
            # First, try a single buildingAdvisor call to check all busy cities at once.
            advisor_active: dict[str, dict[str, Any]] = {}
            if busy_pending:
                try:
                    advisor_active = _get_active_constructions_via_advisor(client)
                    logger.debug("buildingAdvisor: %d cidades com obra ativa", len(advisor_active))
                except Exception as adv_exc:
                    logger.debug("buildingAdvisor falhou, usando fallback por cidade: %s", adv_exc)

            for pending, city, upgrading in busy_pending:
                city_id_int = _to_int(pending["city_id"])
                city_id_str = str(pending["city_id"])
                upgrading_name = str(upgrading.get("building") or "?")
                upgrading_level = _to_int(upgrading.get("level"), 0)
                upgrading_position = _to_int(upgrading.get("position"), -1)

                live_still_busy = True
                # Check advisor first (one request already made for all cities)
                if advisor_active:
                    advisor_info = advisor_active.get(city_id_str)
                    if advisor_info is None:
                        # City not in advisor — do live read to confirm before assuming free.
                        # The advisor HTML parser may miss some cities; trusting the absence
                        # blindly caused spurious "build while busy" attempts.
                        if upgrading_position >= 0:
                            try:
                                live_state = _live_city_building_state(client, city_id_int, upgrading_position)
                                if live_state and not live_state.get("is_upgrading"):
                                    live_still_busy = False
                                    refresh_needed = True
                                    self.log(
                                        jid, "warn",
                                        f"[{_city_name(city)}] Snapshot indicava obra ({upgrading_name} Lv {upgrading_level}) "
                                        f"mas jogo não confirma — snapshot desatualizado; retomando fila",
                                    )
                                else:
                                    logger.debug(
                                        "[%s] buildingAdvisor ausente mas leitura ao vivo confirma obra em pos=%d",
                                        _city_name(city), upgrading_position,
                                    )
                            except Exception as _live_exc:
                                logger.debug("[%s] Falha ao verificar estado ao vivo: %s", _city_name(city), _live_exc)
                        else:
                            live_still_busy = False
                            refresh_needed = True
                            self.log(
                                jid, "warn",
                                f"[{_city_name(city)}] Snapshot indicava obra ({upgrading_name} Lv {upgrading_level}) "
                                f"mas buildingAdvisor não confirma e posição desconhecida — retomando fila",
                            )
                    else:
                        # City present in advisor → still busy; enrich end_time if available
                        advisor_end = _to_int(advisor_info.get("end_time"), 0)
                        if advisor_end > 0 and not upgrading.get("construction_end_at"):
                            upgrading = dict(upgrading)
                            upgrading["construction_end_at"] = advisor_end
                        logger.debug(
                            "[%s] buildingAdvisor confirma obra ativa (end_time=%s)",
                            _city_name(city), advisor_info.get("end_time"),
                        )
                elif upgrading_position >= 0:
                    # Advisor unavailable — fall back to per-city page read
                    try:
                        logger.debug(
                            "[%s] Verificando estado ao vivo da obra (pos=%d) antes de aguardar",
                            _city_name(city), upgrading_position,
                        )
                        live_state = _live_city_building_state(client, city_id_int, upgrading_position)
                        if live_state and not live_state.get("is_upgrading"):
                            live_still_busy = False
                            refresh_needed = True
                            self.log(
                                jid, "warn",
                                f"[{_city_name(city)}] Snapshot indicava obra ({upgrading_name} Lv {upgrading_level}) "
                                f"mas jogo não confirma — snapshot desatualizado; retomando fila",
                            )
                    except Exception as live_exc:
                        logger.debug("[%s] Falha ao verificar estado ao vivo: %s", _city_name(city), live_exc)

                if live_still_busy:
                    wait_seconds = MIN_RECHECK_SECONDS
                    if upgrading_name in _building_ids_to_match(pending["building_id"]):
                        construction_end_at = _to_int(upgrading.get("construction_end_at"), 0)
                        if construction_end_at > 0:
                            remaining = max(0, construction_end_at - int(time.time()))
                            wait_seconds = max(MIN_RECHECK_SECONDS, remaining + FINISH_BUFFER_SECONDS)
                        else:
                            in_flight_row = self._find_level_row(pending, upgrading_level + 1)
                            if in_flight_row is not None:
                                base_adj = _to_int(in_flight_row.get("adjusted_seconds"), 0)
                                wait_seconds = max(MIN_RECHECK_SECONDS, base_adj + FINISH_BUFFER_SECONDS)
                    self.log(
                        jid, "info",
                        f"{_city_name(city)} em obra: {upgrading_name} Lv {upgrading_level} → {upgrading_level + 1}; próxima etapa em {_format_duration(wait_seconds)}",
                    )
                    new_waits.append(
                        {
                            "status": "waiting_city_busy",
                            "wait_seconds": wait_seconds,
                            "city_id": pending["city_id"],
                            "building_id": pending["building_id"],
                        }
                    )
                else:
                    # Snapshot was stale — try the upgrade
                    next_level = pending["next_level"]
                    level_row = self._find_level_row(pending, next_level)
                    if level_row is None:
                        stored_levels = [r.get("level") for r in (pending.get("level_rows") or [])]
                        self.log(
                            jid, "error",
                            f"Etapa sem custo para nivel {next_level} em {pending.get('building_name')} @ {pending.get('city_name')}. "
                            f"Niveis armazenados: {stored_levels}. Recrie o job.",
                        )
                    else:
                        ready_steps.append((pending, city, level_row))

            for pending, city, level_row in ready_steps:
                try:
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
                        building_position = _to_int(pending.get("building_position"), 0)
                        if building_position > 0:
                            current_entry = _get_building_entry_at_position(city, building_position)
                            if current_entry and str(current_entry.get("building") or "").strip() in _building_ids_to_match(pending["building_id"]):
                                current_level = _to_int(current_entry.get("level"), 0)
                                position = _to_int(current_entry.get("position"), 0)
                            else:
                                current_level = 0
                                position = None
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
                        logger.debug(
                            "[%s] Entrando na cidade para construir %s na pos %d",
                            _city_name(city), building_id, empty_position,
                        )
                        # Navigate to city view — sets current city in session AND gives fresh AR
                        _cr = client._request("GET", client._server_url, params={"view": "city", "cityId": city_id})
                        logger.info("[%s] City page GET: status=%s len=%d snippet=%r", _city_name(city), _cr.status_code, len(_cr.text), _cr.text[:200])
                        _ar = re.search(r'actionRequest\s*[=:]\s*["\']([a-zA-Z0-9_\-]+)["\']', _cr.text)
                        if _ar:
                            client._action_request = _ar.group(1)
                            logger.info("[%s] Navegando para cidade (build), AR=%s...", _city_name(city), _ar.group(1)[:8])
                        else:
                            logger.warning("[%s] AR não encontrado na city page (build) — token anterior mantido. Resp: %r", _city_name(city), _cr.text[:500])
                        logger.debug(
                            "[%s] Enviando build: building_type=%s pos=%d",
                            _city_name(city), building_id, empty_position,
                        )
                        build_response = client.build(city_id=city_id, building_type=building_id, position=empty_position)
                        live_entry = _confirm_building_state(
                            client,
                            city_id=city_id,
                            position=empty_position,
                            next_level=1,
                            building_name=pending["building_name"],
                            city_name=_city_name(city),
                            expect_build=True,
                            action_feedback=_action_feedback(build_response),
                        )
                        self.log(
                            jid,
                            "info",
                            f"Construção: {_city_name(city)} | {pending['building_name']} Lv 0 → 1"
                            f" | pos {empty_position}"
                            f" | {_format_duration(_to_int(level_row.get('adjusted_seconds'), 0))}"
                            f" | {_format_costs(self._normalize_costs(level_row.get('costs') or {}))}"
                            f" | #{pending['index']} de {len(plan_steps)}",
                        )
                        position = empty_position
                        current_level = 0
                    else:
                        if position is None:
                            raise RuntimeError("building_position_not_found")
                        logger.debug(
                            "[%s] Entrando na cidade (city_id=%s, building=%s, pos=%d, lvl %d->%d)",
                            _city_name(city), city_id, building_id, position, current_level, next_level,
                        )
                        # Navigate to city view — sets current city in session AND gives fresh AR
                        _cr = client._request("GET", client._server_url, params={"view": "city", "cityId": city_id})
                        _ar = re.search(r'actionRequest\s*[=:]\s*["\']([a-zA-Z0-9_\-]+)["\']', _cr.text)
                        if _ar:
                            client._action_request = _ar.group(1)
                            logger.info("[%s] Navegando para cidade (upgrade), AR=%s...", _city_name(city), _ar.group(1)[:8])
                        else:
                            logger.warning("[%s] AR não encontrado na city page (upgrade) — token anterior mantido", _city_name(city))
                        logger.debug(
                            "[%s] Na cidade; consultando edificio %s na pos %d",
                            _city_name(city), building_id, position,
                        )
                        logger.debug(
                            "[%s] Enviando upgrade: city_id=%s pos=%d lvl=%d template=%s",
                            _city_name(city), city_id, position, current_level, building_id,
                        )
                        upgrade_response = client.upgrade(
                            city_id=city_id,
                            building_position=position,
                            current_level=current_level,
                            template_view=building_id,
                        )
                        logger.debug(
                            "[%s] Resposta do upgrade recebida; aguardando confirmacao",
                            _city_name(city),
                        )
                        live_entry = _confirm_building_state(
                            client,
                            city_id=city_id,
                            position=position,
                            next_level=next_level,
                            building_name=pending["building_name"],
                            city_name=_city_name(city),
                            expect_build=False,
                            action_feedback=_action_feedback(upgrade_response),
                        )
                        self.log(
                            jid,
                            "info",
                            f"Evolução: {_city_name(city)} | {pending['building_name']} Lv {current_level} → {next_level}"
                            f" | {_format_duration(_to_int(level_row.get('adjusted_seconds'), 0))}"
                            f" | {_format_costs(self._normalize_costs(level_row.get('costs') or {}))}"
                            f" | #{pending['index']} de {len(plan_steps)}",
                        )

                    # adjusted_seconds already has world/government reductions baked in
                    # from hub job creation — use it directly.
                    construction_end_at = _to_int((live_entry or {}).get("construction_end_at"), 0)
                    if construction_end_at > 0:
                        delay = max(90, construction_end_at - int(time.time()) + FINISH_BUFFER_SECONDS)
                    else:
                        base_adj = _to_int(level_row.get("adjusted_seconds"), 0)
                        delay = max(90, base_adj + FINISH_BUFFER_SECONDS)
                    delays.append(delay)
                    started.append(
                        {
                            "city_id": pending["city_id"],
                            "city_name": pending["city_name"],
                            "building_id": pending["building_id"],
                            "building_name": pending["building_name"],
                            "to_level": next_level,
                            "delay": delay,
                            "confirmed_level": _to_int((live_entry or {}).get("level"), 0),
                            "confirmed_upgrading": bool((live_entry or {}).get("is_upgrading")),
                        }
                    )
                except Exception as step_exc:
                    step_failures.append(
                        {
                            "city_id": pending["city_id"],
                            "city_name": pending["city_name"],
                            "building_id": pending["building_id"],
                            "building_name": pending["building_name"],
                            "error": str(step_exc),
                        }
                    )
                    self.log(
                        jid,
                        "warn",
                        f"Etapa nao confirmada: {_city_name(city)} | {pending['building_name']} | {step_exc}",
                    )
                    refresh_needed = True
                    new_waits.append(
                        {
                            "status": "waiting_refresh_after_failed_start",
                            "wait_seconds": MIN_RECHECK_SECONDS,
                            "city_id": pending["city_id"],
                            "building_id": pending["building_id"],
                            "error": str(step_exc),
                        }
                    )
                    continue

            if ga_id:
                self.save_game_client(ga_id, client)
            if refresh_needed or step_failures:
                self._ensure_status_refresh(jid)

            if not started:
                next_wait = min(
                    [int(reason.get("wait_seconds") or MIN_RECHECK_SECONDS) for reason in new_waits] or [MIN_RECHECK_SECONDS]
                )
                return RunnerResult(
                    success=True,
                    reschedule_seconds=next_wait,
                    data={"status": "waiting_parallel", "waiting": new_waits, "failed_steps": step_failures},
                )

            # next_wait = earliest of: first build finishing, or resource becoming available.
            # We compute two pools separately to avoid inflating with MIN_RECHECK fallbacks
            # from cities-busy that have no meaningful ETA.
            resource_waits = [
                int(r.get("wait_seconds") or MIN_RECHECK_SECONDS)
                for r in new_waits
                if r.get("status") == "waiting_resources"
            ]
            next_wait = min(delays + resource_waits) if resource_waits else min(delays)
            now_ts = time.time()
            self.log(
                jid,
                "info",
                f"{len(started)} obra(s) iniciada(s) neste ciclo; proximo check em {next_wait}s",
            )
            return RunnerResult(
                success=True,
                reschedule_seconds=next_wait,
                reschedule_inputs={"last_builds_started_at": now_ts},
                data={"status": "started_parallel", "started": started, "waiting": new_waits, "failed_steps": step_failures},
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
                "building_position": _to_int(step.get("building_position"), 0),
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
                building_position = _to_int(step.get("building_position"), 0)
                if building_position > 0:
                    slot_entry = _get_building_entry_at_position(city, building_position)
                    if slot_entry and str(slot_entry.get("building") or "").strip() in _building_ids_to_match(step["building_id"]):
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
                    building_position = _to_int(step.get("building_position"), 0)
                    if building_position > 0:
                        slot_entry = _get_building_entry_at_position(city, building_position)
                        if slot_entry and str(slot_entry.get("building") or "").strip() in _building_ids_to_match(step["building_id"]):
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
            if strategy in ("eta_first", "smart"):
                city = _find_city(cities, city_id)
                city_candidates = sorted(
                    city_candidates,
                    key=lambda step: cls._pending_step_score(city, step, strategy, cities),
                )
            selected.append(city_candidates[0])
        return selected

    @classmethod
    def _pending_step_score(
        cls,
        city: dict[str, Any] | None,
        step: dict[str, Any],
        strategy: str = "eta_first",
        all_cities: list[dict[str, Any]] | None = None,
    ) -> tuple[int | float, ...]:
        import math
        next_level = _to_int(step.get("next_level"), 1)
        level_row = cls._find_level_row(step, next_level)
        if city is None or level_row is None:
            return (9_999_999, 9_999_999, 9_999_999, _to_int(step.get("index"), 0))
        costs = cls._normalize_costs(level_row.get("costs") or {})
        stock = _city_stock(city)
        missing_total = sum(max(0, costs[key] - stock.get(key, 0)) for key in RESOURCE_KEYS)
        adjusted_seconds = max(0, _to_int(level_row.get("adjusted_seconds"), 0))
        cost_sum = sum(max(0, costs[key]) for key in RESOURCE_KEYS)
        if strategy == "smart":
            # base_seconds is the raw ika-tools reference time (Chronos-independent).
            # adjusted_seconds was projected at plan-creation time with a future Chronos
            # level, making builds planned for later appear artificially fast (e.g. 1s).
            # Using base_seconds gives a stable, current baseline for comparison.
            base_seconds = max(0, _to_int(level_row.get("base_seconds"), adjusted_seconds))
            cost_penalty = math.log10(1 + cost_sum) * 3600
            if missing_total > 0:
                missing_by_key = {key: max(0, costs[key] - stock.get(key, 0)) for key in RESOURCE_KEYS}
                if not cls._missing_can_be_sourced(city, missing_by_key, all_cities or []):
                    # Resources unobtainable anywhere — fall back to eta_first behaviour
                    return (9_999_999, adjusted_seconds, cost_sum, _to_int(step.get("index"), 0))
                composite = base_seconds + missing_total * 2 + cost_penalty
            else:
                composite = base_seconds + cost_penalty
            return (composite, _to_int(step.get("index"), 0))
        # eta_first (default)
        return (missing_total, adjusted_seconds, cost_sum, _to_int(step.get("index"), 0))

    @staticmethod
    def _missing_can_be_sourced(
        city: dict[str, Any],
        missing: dict[str, int],
        all_cities: list[dict[str, Any]],
    ) -> bool:
        """Return True if all missing resources can be obtained (local production or donor city)."""
        prod = _city_prod_per_hour(city)
        city_id = str(city.get("id") or "")
        for key, amount in missing.items():
            if amount <= 0:
                continue
            if prod.get(key, 0) > 0:
                continue  # local production will cover it eventually
            has_donor = any(
                _city_stock(c).get(key, 0) > 0
                for c in all_cities
                if str(c.get("id") or "") != city_id
            )
            if not has_donor:
                return False
        return True

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
    ) -> tuple[int, bool]:
        """Returns (reschedule_seconds, transport_was_dispatched)."""
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

        # If existing support already covers all missing resources, just wait for it to arrive.
        if not any(amount > 0 for amount in missing.values()):
            self.log(
                jid,
                "info",
                f"Suporte em transito ja cobre {pending['building_name']} em {pending['city_name']}; aguardando chegada",
            )
            return TRANSPORT_RECHECK_SECONDS, False

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
                        return max(wait_seconds or 0, incoming_wait + FINISH_BUFFER_SECONDS), False
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
                return max(wait_seconds or 0, TRANSPORT_RECHECK_SECONDS), True

        if wait_seconds:
            return max(MIN_RECHECK_SECONDS, wait_seconds + FINISH_BUFFER_SECONDS), False

        self.log(
            jid,
            "warn",
            f"{pending['city_name']} sem recurso suficiente para {pending['building_name']} e sem ETA local confiavel",
        )
        return TRANSPORT_RECHECK_SECONDS, False

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
