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


def _find_branch_office_position(city: dict[str, Any]) -> int:
    for item in city.get("buildings") or []:
        building = str(item.get("building") or "").strip()
        if building in {"branchOffice", "marketplace"}:
            return _to_int(item.get("position"), -1)
    return -1


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

    # isBusy is the authoritative flag for upgrades; for new builds the slot still shows
    # as buildingGround (empty) so isBusy is False, but the city-level underConstruction
    # field points to that position and endUpgradeTime is set.
    under_construction = _to_int(city.get("underConstruction"), -1)
    is_upgrading = bool(pos.get("isBusy")) or (under_construction == position)

    construction_end_at = 0
    if is_upgrading and under_construction == position:
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
    for key in ("error", "message"):
        text = str(parsed.get(key) or "").strip()
        if text:
            parts.append(text)
    return " | ".join(parts)


def _feedback_indicates_missing_resources(feedback: str) -> bool:
    text = str(feedback or "").strip().lower()
    if not text:
        return False
    markers = (
        "recurso",
        "recursos",
        "mat[ée]ria",
        "wood",
        "madeira",
        "marble",
        "m[áa]rmore",
        "cristal",
        "glas",
        "sulfur",
        "enxofre",
        "não há recursos",
        "nao ha recursos",
        "insufficient resources",
        "not enough resources",
    )
    return any(re.search(marker, text) for marker in markers)


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
        if _feedback_indicates_missing_resources(action_feedback):
            raise RuntimeError(
                f"missing_resources:{city_name}:{building_name}:pos={position}:"
                f"state={last_state or {}}:feedback={action_feedback}"
            )
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


def _clean_number(raw: Any) -> int:
    text = str(raw or "").strip()
    if not text:
        return 0
    text = re.sub(r"<[^>]+>", "", text)
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return 0
    try:
        return int(digits)
    except Exception:
        return 0


def _extract_ajax_html(response_text: str) -> str:
    try:
        data = json.loads(response_text)
    except Exception:
        return ""
    view_cmds = {"changeView", "loadContent", "updateTemplate", "updateTemplateData"}
    for cmd in data:
        if not isinstance(cmd, (list, tuple)) or len(cmd) < 2:
            continue
        if cmd[0] not in view_cmds:
            continue
        payload = cmd[1]
        if isinstance(payload, str) and "<" in payload:
            return payload
        if isinstance(payload, dict):
            for key in ("html", "content", "template"):
                val = payload.get(key)
                if isinstance(val, str) and "<" in val:
                    return val
        if isinstance(payload, (list, tuple)):
            for candidate in reversed(payload):
                if isinstance(candidate, str) and "<" in candidate:
                    return candidate
                if isinstance(candidate, (list, tuple)):
                    for sub in candidate:
                        if isinstance(sub, str) and "<" in sub:
                            return sub
    return ""


def _parse_live_sidebar_costs(html: str) -> dict[str, int]:
    costs = {key: 0 for key in RESOURCE_KEYS}
    if not html:
        return costs
    for match in re.finditer(r'<li class="([^"]+)"[^>]*>(.*?)</li>', html, re.IGNORECASE | re.DOTALL):
        classes = {token for token in str(match.group(1) or "").split() if token}
        body = str(match.group(2) or "")
        if "time" in classes:
            continue
        if "wood" in classes:
            costs["wood"] = _clean_number(body)
        elif "wine" in classes:
            costs["wine"] = _clean_number(body)
        elif "marble" in classes:
            costs["marble"] = _clean_number(body)
        elif "glass" in classes or "crystal" in classes or "glas" in classes:
            costs["glas"] = _clean_number(body)
        elif "sulfur" in classes:
            costs["sulfur"] = _clean_number(body)
    return costs


def _parse_live_sidebar_button_state(html: str) -> dict[str, Any]:
    match = re.search(
        r'<a[^>]+id="js_buildingUpgradeButton"[^>]+class="([^"]*)"[^>]*(?:href="([^"]*)")?',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {"button_found": False, "button_enabled": False, "href": ""}
    classes = {token for token in str(match.group(1) or "").split() if token}
    href = str(match.group(2) or "").strip()
    disabled_tokens = {"disabled", "inactive", "grayedOut", "greyedOut"}
    enabled = not any(token in classes for token in disabled_tokens)
    return {"button_found": True, "button_enabled": enabled, "href": href}


def _ensure_city_context(client, city_id: int) -> None:
    """Force the current session context to the target city before city-scoped reads."""
    try:
        change_current_city(client, city_id)
        return
    except Exception as exc:
        logger.debug("change_current_city failed for city %s: %s", city_id, exc)

    try:
        resp = client._request("GET", client._server_url, params={"view": "city", "cityId": city_id})
    except Exception as exc:
        logger.debug("Fallback city GET failed for city %s: %s", city_id, exc)
        return
    token_match = re.search(r'actionRequest\s*[=:]\s*["\']([a-zA-Z0-9_\-]+)["\']', resp.text)
    if token_match:
        client._action_request = token_match.group(1)


def _live_city_stock_from_game(client, city_id: int) -> dict[str, int] | None:
    _ensure_city_context(client, city_id)
    try:
        resp = client._request(
            "GET",
            client._server_url,
            params={"view": "updateGlobalData", "ajax": "1"},
            headers={"Accept": "*/*", "X-Requested-With": "XMLHttpRequest", "Referer": client._server_url},
        )
        payload = resp.json()
        for cmd in payload:
            if not isinstance(cmd, (list, tuple)) or len(cmd) < 2 or cmd[0] != "updateGlobalData":
                continue
            header = (cmd[1] or {}).get("headerData") if isinstance(cmd[1], dict) else None
            current = (header or {}).get("currentResources") if isinstance(header, dict) else None
            if isinstance(current, dict):
                return {
                    "wood": _to_int(current.get("resource"), 0),
                    "wine": _to_int(current.get("1"), 0),
                    "marble": _to_int(current.get("2"), 0),
                    "glas": _to_int(current.get("3"), 0),
                    "sulfur": _to_int(current.get("4"), 0),
                }
    except Exception as exc:
        logger.debug("Failed to read live header stock for city %s: %s", city_id, exc)

    try:
        resp = client._request("GET", client._server_url, params={"view": "city", "cityId": city_id})
        city = extract_city_data(resp.text)
    except Exception as exc:
        logger.debug("Failed to read live city stock for city %s: %s", city_id, exc)
        return None
    if not isinstance(city, dict):
        return None
    current = city.get("current_resources") if isinstance(city.get("current_resources"), dict) else {}
    if current:
        return {
            "wood": _to_int(current.get("resource"), 0),
            "wine": _to_int(current.get("1"), 0),
            "marble": _to_int(current.get("2"), 0),
            "glas": _to_int(current.get("3"), 0),
            "sulfur": _to_int(current.get("4"), 0),
        }
    return {
        "wood": _to_int(city.get("wood"), 0),
        "wine": _to_int(city.get("wine"), 0),
        "marble": _to_int(city.get("marble"), 0),
        "glas": _to_int(city.get("crystal"), 0),
        "sulfur": _to_int(city.get("sulfur"), 0),
    }


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

        last_market_order_requested_at = _to_float(inputs.get("last_market_order_requested_at"), 0.0)
        if last_market_order_requested_at > 0 and updated_at is not None:
            if updated_at.timestamp() < last_market_order_requested_at:
                age = int(time.time() - last_market_order_requested_at)
                self.log(jid, "info", f"Snapshot anterior a ordem de mercado interno ({age}s atras); aguardando refresh")
                self._ensure_status_refresh(jid)
                return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "waiting_post_market_snapshot"})

        # Resolve time reductions for this game account
        agent_config = self.get_agent_config()
        world_time_reduction = _resolve_world_time_reduction(agent_config, ga_id)
        government_time_reduction = _resolve_government_time_reduction(agent_config, ga_id)

        cities = _as_city_list(snapshot.get("cities"))
        plan_steps = self._normalized_plan(inputs)
        if not plan_steps:
            return RunnerResult(success=False, data={"error": "missing_plan"})

        skipped_step_indices: list[int] = list(inputs.get("skipped_step_indices") or [])
        blocked_step_indices: list[int] = list(inputs.get("blocked_step_indices") or [])
        ignored_step_indices = sorted(set(skipped_step_indices + blocked_step_indices))
        pending_steps = self._pick_pending_steps(
            cities,
            plan_steps,
            str(inputs.get("queue_strategy") or "fifo"),
            ignored_step_indices,
        )
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
        reschedule_context: dict[str, Any] = {}
        support_by_city = self._get_open_construction_support(jid)

        def _queue_pending_candidate(pending_step: dict[str, Any]) -> None:
            nonlocal any_transport_dispatched
            city = _find_city(cities, pending_step["city_id"])
            if city is None:
                self.log(jid, "warn", f"Cidade {pending_step['city_id']} nao encontrada no snapshot atual")
                wait_reasons.append({"status": "city_missing", "wait_seconds": MIN_RECHECK_SECONDS})
                return

            upgrading = _get_upgrading_building(city)
            if upgrading is not None:
                busy_pending.append((pending_step, city, upgrading))
                return

            next_level = pending_step["next_level"]
            level_row = self._find_level_row(pending_step, next_level)
            if level_row is None:
                stored_levels = [r.get("level") for r in (pending_step.get("level_rows") or [])]
                self.log(
                    jid, "error",
                    f"Etapa sem custo para nivel {next_level} em {pending_step.get('building_name')} @ {pending_step.get('city_name')}. "
                    f"Niveis armazenados: {stored_levels}. "
                    f"Recrie o job para regenerar os dados de custo."
                )
                raise RuntimeError("missing_level_row")

            costs = self._normalize_costs(level_row.get("costs") or {})
            stock = _city_stock(city)
            missing = {key: max(0, costs[key] - stock.get(key, 0)) for key in RESOURCE_KEYS}
            if any(amount > 0 for amount in missing.values()):
                reschedule_seconds, transport_spawned, extra_inputs, should_skip_now = self._handle_missing_resources(
                    job=job,
                    pending=pending_step,
                    cities=cities,
                    city=city,
                    missing=missing,
                    support_by_city=support_by_city,
                )
                any_transport_dispatched = any_transport_dispatched or transport_spawned
                if extra_inputs:
                    reschedule_context.update(extra_inputs)
                wait_reasons.append(
                    {
                        "status": "waiting_resources",
                        "wait_seconds": reschedule_seconds,
                        "city_id": pending_step["city_id"],
                        "building_id": pending_step["building_id"],
                        "missing": missing,
                    }
                )
                if should_skip_now:
                    _promote_next_pending_candidate(
                        pending_step,
                        [*ignored_step_indices, _to_int(pending_step.get("index"), 0)],
                    )
                return

            ready_steps.append((pending_step, city, level_row))

        def _promote_next_pending_candidate(pending_step: dict[str, Any], ignored_indices: list[int]) -> None:
            promoted = self._pick_pending_step_for_city(
                cities,
                plan_steps,
                pending_step["city_id"],
                str(inputs.get("queue_strategy") or "fifo"),
                ignored_indices,
            )
            if promoted is None:
                return
            self.log(
                jid,
                "info",
                (
                    f"[{pending_step['city_name']}] Etapa {pending_step['building_name']} bloqueada neste ciclo; "
                    f"promovendo {promoted['building_name']} Lv {promoted['next_level']}"
                ),
            )
            _queue_pending_candidate(promoted)

        for pending in pending_steps:
            try:
                _queue_pending_candidate(pending)
            except RuntimeError as queue_exc:
                if str(queue_exc) == "missing_level_row":
                    next_level = pending["next_level"]
                    stored_levels = [r.get("level") for r in (pending.get("level_rows") or [])]
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
                raise

        if refresh_needed:
            self._ensure_status_refresh(jid)

        if not ready_steps and not busy_pending:
            next_wait = min(
                [int(reason.get("wait_seconds") or MIN_RECHECK_SECONDS) for reason in wait_reasons] or [MIN_RECHECK_SECONDS]
            )
            reschedule_inputs: dict[str, Any] = {}
            if any_transport_dispatched:
                reschedule_inputs["last_transport_dispatched_at"] = int(time.time())
            if reschedule_context:
                reschedule_inputs.update(reschedule_context)
            if skipped_step_indices:
                reschedule_inputs["skipped_step_indices"] = skipped_step_indices
            if blocked_step_indices:
                reschedule_inputs["blocked_step_indices"] = blocked_step_indices
            return RunnerResult(
                success=True,
                reschedule_seconds=next_wait,
                reschedule_inputs=reschedule_inputs or None,
                data={"status": "waiting_parallel", "waiting": wait_reasons},
            )

        try:
            client, ga_id = self._get_client(job)
            started: list[dict[str, Any]] = []
            delays: list[int] = []
            new_waits = wait_reasons
            step_failures: list[dict[str, Any]] = []
            _skipped_step_indices: list[int] = list(inputs.get("skipped_step_indices") or [])
            _blocked_step_indices: list[int] = list(inputs.get("blocked_step_indices") or [])

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
                                    self._reconcile_snapshot_building_state(
                                        ga_id=str(ga_id),
                                        city=city,
                                        position=upgrading_position,
                                        live_state=live_state,
                                    )
                                    live_level = _to_int(live_state.get("level"), 0)
                                    if live_level > upgrading_level:
                                        self.log(
                                            jid, "info",
                                            f"[{_city_name(city)}] Obra concluida no jogo: {upgrading_name} Lv {upgrading_level} -> {live_level}; snapshot atualizado",
                                        )
                                        continue
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
                                    if live_state:
                                        live_end = _to_int(live_state.get("construction_end_at"), 0)
                                        if live_end > 0 and not upgrading.get("construction_end_at"):
                                            upgrading = dict(upgrading)
                                            upgrading["construction_end_at"] = live_end
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
                            self._reconcile_snapshot_building_state(
                                ga_id=str(ga_id),
                                city=city,
                                position=upgrading_position,
                                live_state=live_state,
                            )
                            live_level = _to_int(live_state.get("level"), 0)
                            if live_level > upgrading_level:
                                completed_idx = _to_int(pending.get("index"), 0)
                                if completed_idx not in _skipped_step_indices:
                                    _skipped_step_indices.append(completed_idx)
                                self.log(
                                    jid, "info",
                                    f"[{_city_name(city)}] Obra concluida no jogo: {upgrading_name} Lv {upgrading_level} -> {live_level}; snapshot atualizado",
                                )
                                promoted = self._pick_pending_step_for_city(
                                    cities,
                                    plan_steps,
                                    pending["city_id"],
                                    str(inputs.get("queue_strategy") or "fifo"),
                                    _skipped_step_indices + _blocked_step_indices,
                                )
                                if promoted is not None:
                                    self.log(
                                        jid,
                                        "info",
                                        f"[{_city_name(city)}] Promovendo proxima etapa no mesmo ciclo: {promoted['building_name']} Lv {promoted['next_level']}",
                                    )
                                    try:
                                        _queue_pending_candidate(promoted)
                                    except RuntimeError:
                                        pass
                                continue
                            self.log(
                                jid, "warn",
                                f"[{_city_name(city)}] Snapshot indicava obra ({upgrading_name} Lv {upgrading_level}) "
                                f"mas jogo não confirma — snapshot desatualizado; retomando fila",
                            )
                        elif live_state:
                            live_end = _to_int(live_state.get("construction_end_at"), 0)
                            if live_end > 0 and not upgrading.get("construction_end_at"):
                                upgrading = dict(upgrading)
                                upgrading["construction_end_at"] = live_end
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
                    current_level, position, current_entry = self._resolve_step_state(city, pending)
                    estimated_costs = self._normalize_costs(level_row.get("costs") or {})

                    if current_level >= _to_int(pending.get("target_level"), 0) > 0:
                        self.log(
                            jid,
                            "info",
                            f"Etapa concluida no jogo: {_city_name(city)} | {pending['building_name']} Lv {current_level}",
                        )
                        _skipped_idx = _to_int(pending.get("index"), 0)
                        if _skipped_idx not in _skipped_step_indices:
                            _skipped_step_indices.append(_skipped_idx)
                        if position is not None:
                            self._reconcile_snapshot_building_state(
                                ga_id=str(ga_id),
                                city=city,
                                position=position,
                                live_state={
                                    "position": position,
                                    "building": building_id,
                                    "level": current_level,
                                    "is_upgrading": False,
                                },
                            )
                        continue

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

                    live_debug = self._collect_live_step_debug(
                        client=client,
                        city=city,
                        pending=pending,
                        current_level=current_level,
                        next_level=next_level,
                        position=position,
                        estimated_costs=estimated_costs,
                    )
                    live_stock = live_debug["live_stock"]
                    live_costs = live_debug["live_costs"]
                    if live_costs is not None:
                        live_missing = live_debug["live_missing"] or {key: 0 for key in RESOURCE_KEYS}
                        if any(amount > 0 for amount in live_missing.values()):
                            self.log(
                                jid,
                                "info",
                                f"Custo real do jogo bloqueia {pending['building_name']} | {live_debug['debug_line']}",
                            )
                            reschedule_seconds, transport_spawned, extra_inputs, should_skip_now = self._handle_missing_resources(
                                job=job,
                                pending=pending,
                                cities=cities,
                                city=city,
                                missing=live_missing,
                                support_by_city=support_by_city,
                            )
                            any_transport_dispatched = any_transport_dispatched or transport_spawned
                            if extra_inputs:
                                reschedule_context.update(extra_inputs)
                            new_waits.append(
                                {
                                    "status": "waiting_real_cost_resources",
                                    "wait_seconds": reschedule_seconds,
                                    "city_id": pending["city_id"],
                                    "building_id": pending["building_id"],
                                    "estimated_costs": estimated_costs,
                                    "live_costs": live_costs,
                                    "live_stock": live_stock,
                                    "missing": live_missing,
                                }
                            )
                            if should_skip_now:
                                _promote_next_pending_candidate(
                                    pending,
                                    [*_skipped_step_indices, *_blocked_step_indices, _to_int(pending.get("index"), 0)],
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

                        # Build fallback position list (preferred first, then up to 4 other empties)
                        _alt_positions = [empty_position]
                        for _item in city.get("buildings") or []:
                            if len(_alt_positions) >= 5:
                                break
                            _p = _to_int(_item.get("position"), -1)
                            if _p >= 0 and _p != empty_position and str(_item.get("building") or "").strip() == "empty":
                                _st = str(_item.get("type") or "").strip()
                                if not allowed_types or not _st or _st in allowed_types:
                                    _alt_positions.append(_p)

                        logger.debug(
                            "[%s] Entrando na cidade para construir %s; candidatos: %s",
                            _city_name(city), building_id, _alt_positions[:5],
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

                        build_response = None
                        for _try_pos in _alt_positions:
                            try:
                                build_response = client.build(city_id=city_id, building_type=building_id, position=_try_pos)
                                empty_position = _try_pos
                                break
                            except Exception as _pos_exc:
                                _exc_str = str(_pos_exc)
                                if "position_locked" in _exc_str:
                                    self.log(jid, "info", f"[{_city_name(city)}] Slot pos={_try_pos} bloqueado por pesquisa; tentando proximo slot")
                                    continue
                                if "not available for slot" in _exc_str or "slot occupied" in _exc_str or "queue may be full" in _exc_str:
                                    self.log(jid, "info", f"[{_city_name(city)}] Slot pos={_try_pos} ocupado ou fila cheia; tentando proximo slot")
                                    continue
                                raise
                        if build_response is None:
                            # No slot accepted the build — check if building already exists elsewhere
                            # (including Lv 0 = under construction: _find_building returns pos=None only when not found)
                            _existing_level, _existing_pos = _find_building(city, building_id)
                            if _existing_pos is not None:
                                self.log(
                                    jid, "info",
                                    f"[{_city_name(city)}] {pending['building_name']} ja existe em pos={_existing_pos} lv={_existing_level}; etapa ignorada",
                                )
                                _skipped_idx = _to_int(pending.get("index"), 0)
                                if _skipped_idx not in _skipped_step_indices:
                                    _skipped_step_indices.append(_skipped_idx)
                                delays.append(90)
                                continue
                            raise RuntimeError(f"no_unlocked_slot:{building_id}")
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
                            f" | {_format_costs(estimated_costs)}"
                            f" | #{pending['index']} de {len(plan_steps)}",
                        )
                        position = empty_position
                        current_level = 0
                        # Patch snapshot immediately so next run sees the ongoing construction
                        # without waiting for check_status to update it.
                        if ga_id:
                            try:
                                _end_at = _to_int((live_entry or {}).get("construction_end_at"), 0) or None
                                self._reconcile_snapshot_building_state(
                                    ga_id=str(ga_id),
                                    city=city,
                                    position=empty_position,
                                    live_state={
                                        "position": empty_position,
                                        "building": building_id,
                                        "level": 0,
                                        "is_upgrading": True,
                                        "construction_end_at": _end_at,
                                    },
                                )
                            except Exception as _patch_exc:
                                logger.debug("[%s] Snapshot patch after build failed: %s", _city_name(city), _patch_exc)
                    else:
                        if position is None:
                            raise RuntimeError("building_position_not_found")
                        self.log(
                            jid,
                            "info",
                            f"Executando etapa | {self._format_step_debug(city=city, pending=pending, current_level=current_level, next_level=next_level, position=position, estimated_costs=estimated_costs, live_costs=live_costs, live_stock=live_stock, live_missing=live_debug['live_missing'], button_state=live_debug['button_state'])}",
                        )
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
                        action_feedback = _action_feedback(upgrade_response)
                        try:
                            live_entry = _confirm_building_state(
                                client,
                                city_id=city_id,
                                position=position,
                                next_level=next_level,
                                building_name=pending["building_name"],
                                city_name=_city_name(city),
                                expect_build=False,
                                action_feedback=action_feedback,
                            )
                        except RuntimeError as confirm_exc:
                            post_debug = self._collect_live_step_debug(
                                client=client,
                                city=city,
                                pending=pending,
                                current_level=current_level,
                                next_level=next_level,
                                position=position,
                                estimated_costs=estimated_costs,
                                feedback=action_feedback,
                            )
                            post_missing = post_debug["live_missing"] or {key: 0 for key in RESOURCE_KEYS}
                            if any(amount > 0 for amount in post_missing.values()):
                                self.log(
                                    jid,
                                    "info",
                                    f"Upgrade rejeitado por recurso insuficiente | {post_debug['debug_line']}",
                                )
                                reschedule_seconds, transport_spawned, extra_inputs, should_skip_now = self._handle_missing_resources(
                                    job=job,
                                    pending=pending,
                                    cities=cities,
                                    city=city,
                                    missing=post_missing,
                                    support_by_city=support_by_city,
                                )
                                any_transport_dispatched = any_transport_dispatched or transport_spawned
                                if extra_inputs:
                                    reschedule_context.update(extra_inputs)
                                new_waits.append(
                                    {
                                        "status": "waiting_real_cost_resources",
                                        "wait_seconds": reschedule_seconds,
                                        "city_id": pending["city_id"],
                                        "building_id": pending["building_id"],
                                        "estimated_costs": estimated_costs,
                                        "live_costs": post_debug["live_costs"],
                                        "live_stock": post_debug["live_stock"],
                                        "missing": post_missing,
                                        "feedback": action_feedback,
                                    }
                                )
                                if should_skip_now:
                                    _promote_next_pending_candidate(
                                        pending,
                                        [*_skipped_step_indices, *_blocked_step_indices, _to_int(pending.get("index"), 0)],
                                    )
                                continue
                            raise RuntimeError(f"{confirm_exc} | {post_debug['debug_line']}") from confirm_exc
                        self.log(
                            jid,
                            "info",
                            f"Evolução: {_city_name(city)} | {pending['building_name']} Lv {current_level} → {next_level}"
                            f" | {_format_duration(_to_int(level_row.get('adjusted_seconds'), 0))}"
                            f" | {_format_costs(estimated_costs)}"
                            f" | #{pending['index']} de {len(plan_steps)}",
                        )

                    if ga_id and position is not None:
                        try:
                            _end_at = _to_int((live_entry or {}).get("construction_end_at"), 0) or None
                            self._reconcile_snapshot_building_state(
                                ga_id=str(ga_id),
                                city=city,
                                position=position,
                                live_state={
                                    "position": position,
                                    "building": building_id,
                                    "level": current_level,
                                    "is_upgrading": True,
                                    "construction_end_at": _end_at,
                                },
                            )
                        except Exception as _patch_exc:
                            logger.debug("[%s] Snapshot patch after upgrade failed: %s", _city_name(city), _patch_exc)

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
                    err = str(step_exc)
                    if err.startswith("no_empty_slot:") or err.startswith("no_unlocked_slot:"):
                        blocked_idx = _to_int(pending.get("index"), 0)
                        if blocked_idx not in _blocked_step_indices:
                            _blocked_step_indices.append(blocked_idx)
                        self.log(
                            jid,
                            "warn",
                            f"Etapa bloqueada estruturalmente: {_city_name(city)} | {pending['building_name']} | {err}",
                        )
                        new_waits.append(
                            {
                                "status": "blocked_structural",
                                "wait_seconds": TRANSPORT_RECHECK_SECONDS,
                                "city_id": pending["city_id"],
                                "building_id": pending["building_id"],
                                "error": err,
                            }
                        )
                        continue
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
                _ri_no_start: dict[str, Any] = {}
                if _skipped_step_indices:
                    _ri_no_start["skipped_step_indices"] = _skipped_step_indices
                if _blocked_step_indices:
                    _ri_no_start["blocked_step_indices"] = _blocked_step_indices
                return RunnerResult(
                    success=True,
                    reschedule_seconds=next_wait,
                    reschedule_inputs=_ri_no_start or None,
                    data={"status": "waiting_parallel", "waiting": new_waits, "failed_steps": step_failures},
                )

            # next_wait = earliest of: first build finishing, or resource becoming available.
            # We compute two pools separately to avoid inflating with MIN_RECHECK fallbacks
            # from cities-busy that have no meaningful ETA.
            timed_waits = [
                int(r.get("wait_seconds") or MIN_RECHECK_SECONDS)
                for r in new_waits
                if str(r.get("status") or "") not in {"blocked_structural"}
            ]
            next_wait = min(delays + timed_waits) if timed_waits else min(delays)
            now_ts = time.time()
            self.log(
                jid,
                "info",
                f"{len(started)} obra(s) iniciada(s) neste ciclo; proximo check em {next_wait}s",
            )
            _ri_started: dict[str, Any] = {"last_builds_started_at": now_ts}
            if _skipped_step_indices:
                _ri_started["skipped_step_indices"] = _skipped_step_indices
            if _blocked_step_indices:
                _ri_started["blocked_step_indices"] = _blocked_step_indices
            return RunnerResult(
                success=True,
                reschedule_seconds=next_wait,
                reschedule_inputs=_ri_started,
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
    def _resolve_step_state(city: dict[str, Any], step: dict[str, Any]) -> tuple[int, int | None, dict[str, Any] | None]:
        if str(step.get("mode") or "upgrade") == "new":
            preferred_position = _to_int(step.get("preferred_position"), 0)
            slot_entry = _get_building_entry_at_position(city, preferred_position) if preferred_position > 0 else None
            if slot_entry and str(slot_entry.get("building") or "").strip() in _building_ids_to_match(step["building_id"]):
                return _to_int(slot_entry.get("level"), 0), _to_int(slot_entry.get("position"), 0), slot_entry
            current_level, position = _find_building(city, step["building_id"])
            current_entry = _get_building_entry(city, step["building_id"])
            if position is not None:
                return current_level, position, current_entry
            return 0, preferred_position if preferred_position > 0 else None, None

        building_position = _to_int(step.get("building_position"), 0)
        if building_position > 0:
            slot_entry = _get_building_entry_at_position(city, building_position)
            if slot_entry and str(slot_entry.get("building") or "").strip() in _building_ids_to_match(step["building_id"]):
                return _to_int(slot_entry.get("level"), 0), _to_int(slot_entry.get("position"), 0), slot_entry
            return 0, None, None
        current_level, position = _find_building(city, step["building_id"])
        current_entry = _get_building_entry(city, step["building_id"])
        return current_level, position, current_entry

    def _reconcile_snapshot_building_state(
        self,
        *,
        ga_id: str,
        city: dict[str, Any],
        position: int,
        live_state: dict[str, Any] | None,
    ) -> None:
        if position < 0:
            return
        live = live_state or {}
        live_building = str(live.get("building") or "").strip()
        if not live_building or live_building == "empty":
            patch = {"building": "empty", "level": 0, "is_upgrading": False, "construction_end_at": None}
        else:
            patch = {
                "building": live_building,
                "level": _to_int(live.get("level"), 0),
                "is_upgrading": bool(live.get("is_upgrading")),
                "construction_end_at": _to_int(live.get("construction_end_at"), 0) or None,
            }

        entry = _get_building_entry_at_position(city, position)
        if entry is not None:
            entry.update(patch)
            if patch["construction_end_at"] is None:
                entry.pop("construction_end_at", None)

        self.hub.patch_snapshot_building(
            game_account_id=ga_id,
            city_id=str(city.get("id") or ""),
            position=position,
            patch=patch,
        )

    @staticmethod
    def _format_step_debug(
        *,
        city: dict[str, Any],
        pending: dict[str, Any],
        current_level: int,
        next_level: int,
        position: int | None,
        estimated_costs: dict[str, int] | None = None,
        live_costs: dict[str, int] | None = None,
        live_stock: dict[str, int] | None = None,
        live_missing: dict[str, int] | None = None,
        button_state: dict[str, Any] | None = None,
        feedback: str = "",
    ) -> str:
        parts = [
            f"cidade={_city_name(city)}",
            f"city_id={city.get('id')}",
            f"predio={pending.get('building_name')}",
            f"building_id={pending.get('building_id')}",
            f"pos={position if position is not None else '?'}",
            f"nivel={current_level}->{next_level}",
        ]
        if estimated_costs is not None:
            parts.append(f"estimado={_format_costs(estimated_costs)}")
        if live_costs is not None:
            parts.append(f"custo_real={_format_costs(live_costs)}")
        if live_stock is not None:
            parts.append(f"estoque_real={_format_costs(live_stock)}")
        if live_missing is not None:
            parts.append(f"faltando={_format_costs(live_missing)}")
        if button_state:
            parts.append(
                "botao_upgrade="
                f"found:{bool(button_state.get('button_found'))}"
                f"/enabled:{bool(button_state.get('button_enabled'))}"
            )
        if feedback:
            parts.append(f"feedback={feedback}")
        return " | ".join(parts)

    def _get_live_step_costs(
        self,
        *,
        client,
        city_id: int,
        pending: dict[str, Any],
        current_level: int,
        position: int | None,
    ) -> dict[str, int] | None:
        if position is None or current_level <= 0:
            return None
        _ensure_city_context(client, city_id)
        template_view = _normalize_building_id(str(pending.get("building_id") or "city"))
        try:
            resp = client._request(
                "GET",
                client._server_url,
                params={
                    "view": template_view,
                    "cityId": str(city_id),
                    "position": str(position),
                    "backgroundView": "city",
                    "currentCityId": str(city_id),
                    "ajax": "1",
                },
                headers={
                    "Accept": "text/plain, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        except Exception as exc:
            logger.debug("Failed to load live sidebar for %s in city %s: %s", template_view, city_id, exc)
            return None
        html = _extract_ajax_html(resp.text)
        if not html:
            return None
        costs = _parse_live_sidebar_costs(html)
        return costs if any(costs.values()) else None

    def _get_live_step_button_state(
        self,
        *,
        client,
        city_id: int,
        pending: dict[str, Any],
        current_level: int,
        position: int | None,
    ) -> dict[str, Any] | None:
        if position is None or current_level <= 0:
            return None
        _ensure_city_context(client, city_id)
        template_view = _normalize_building_id(str(pending.get("building_id") or "city"))
        try:
            resp = client._request(
                "GET",
                client._server_url,
                params={
                    "view": template_view,
                    "cityId": str(city_id),
                    "position": str(position),
                    "backgroundView": "city",
                    "currentCityId": str(city_id),
                    "ajax": "1",
                },
                headers={
                    "Accept": "text/plain, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        except Exception as exc:
            logger.debug("Failed to load live button state for %s in city %s: %s", template_view, city_id, exc)
            return None
        html = _extract_ajax_html(resp.text)
        if not html:
            return None
        return _parse_live_sidebar_button_state(html)

    def _collect_live_step_debug(
        self,
        *,
        client,
        city: dict[str, Any],
        pending: dict[str, Any],
        current_level: int,
        next_level: int,
        position: int | None,
        estimated_costs: dict[str, int] | None = None,
        feedback: str = "",
    ) -> dict[str, Any]:
        city_id = _to_int(pending.get("city_id"), 0)
        live_stock = _live_city_stock_from_game(client, city_id) or _city_stock(city)
        live_costs = self._get_live_step_costs(
            client=client,
            city_id=city_id,
            pending=pending,
            current_level=current_level,
            position=position,
        )
        button_state = self._get_live_step_button_state(
            client=client,
            city_id=city_id,
            pending=pending,
            current_level=current_level,
            position=position,
        )
        live_missing = None
        if live_costs is not None:
            live_missing = {key: max(0, live_costs[key] - live_stock.get(key, 0)) for key in RESOURCE_KEYS}
        return {
            "city_id": city_id,
            "current_level": current_level,
            "next_level": next_level,
            "position": position,
            "estimated_costs": estimated_costs,
            "live_costs": live_costs,
            "live_stock": live_stock,
            "live_missing": live_missing,
            "button_state": button_state,
            "feedback": feedback,
            "debug_line": self._format_step_debug(
                city=city,
                pending=pending,
                current_level=current_level,
                next_level=next_level,
                position=position,
                estimated_costs=estimated_costs,
                live_costs=live_costs,
                live_stock=live_stock,
                live_missing=live_missing,
                button_state=button_state,
                feedback=feedback,
            ),
        }

    @staticmethod
    def _pick_pending_step(cities: list[dict[str, Any]], plan_steps: list[dict[str, Any]]) -> dict[str, Any] | None:
        for step in sorted(plan_steps, key=lambda item: (_to_int(item.get("index"), 0), str(item.get("city_id")))):
            city = _find_city(cities, step["city_id"])
            if city is None:
                continue
            current_level, _, _ = ConstructionPlanRunner._resolve_step_state(city, step)
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
        skipped_step_indices: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        _skipped = set(skipped_step_indices or [])
        candidates_by_city: dict[str, list[dict[str, Any]]] = {}
        city_order: list[str] = []
        for step in sorted(plan_steps, key=lambda item: (_to_int(item.get("index"), 0), str(item.get("city_id")))):
            if _to_int(step.get("index"), 0) in _skipped:
                continue
            city_id = str(step.get("city_id") or "")
            if not city_id:
                continue
            city = _find_city(cities, city_id)
            if city is None:
                pending = dict(step)
                pending["current_level"] = 0
                pending["next_level"] = max(1, _to_int(step.get("target_level"), 1))
            else:
                current_level, _, _ = cls._resolve_step_state(city, step)
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
            selected_step = cls._pick_pending_step_for_city(
                cities,
                plan_steps,
                city_id,
                strategy,
                skipped_step_indices,
            )
            if selected_step is not None:
                selected.append(selected_step)
        return selected

    @classmethod
    def _pick_pending_step_for_city(
        cls,
        cities: list[dict[str, Any]],
        plan_steps: list[dict[str, Any]],
        city_id: str,
        queue_strategy: str = "fifo",
        skipped_step_indices: list[int] | None = None,
    ) -> dict[str, Any] | None:
        _skipped = set(skipped_step_indices or [])
        city_candidates: list[dict[str, Any]] = []
        city_id = str(city_id or "")
        if not city_id:
            return None
        for step in sorted(plan_steps, key=lambda item: (_to_int(item.get("index"), 0), str(item.get("city_id")))):
            if str(step.get("city_id") or "") != city_id:
                continue
            if _to_int(step.get("index"), 0) in _skipped:
                continue
            city = _find_city(cities, city_id)
            if city is None:
                pending = dict(step)
                pending["current_level"] = 0
                pending["next_level"] = max(1, _to_int(step.get("target_level"), 1))
            else:
                current_level, _, _ = cls._resolve_step_state(city, step)
                target_level = _to_int(step.get("target_level"), 0)
                if current_level >= target_level > 0:
                    continue
                pending = dict(step)
                pending["current_level"] = current_level
                pending["next_level"] = max(1, current_level + 1)
            city_candidates.append(pending)

        if not city_candidates:
            return None

        strategy = str(queue_strategy or "fifo").strip().lower()
        if strategy in ("eta_first", "smart"):
            city = _find_city(cities, city_id)
            city_candidates = sorted(
                city_candidates,
                key=lambda step: cls._pending_step_score(city, step, strategy, cities),
            )
        return city_candidates[0]

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
    ) -> tuple[int, bool, dict[str, Any] | None, bool]:
        """Returns (reschedule_seconds, transport_was_dispatched, reschedule_inputs, should_skip_city_step_now)."""
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
            return TRANSPORT_RECHECK_SECONDS, False, None, False

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
                        return max(wait_seconds or 0, incoming_wait + FINISH_BUFFER_SECONDS), False, None, False
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
                return max(wait_seconds or 0, TRANSPORT_RECHECK_SECONDS), True, None, False

            # No donor city available — try buying from the public market
            if bool(inputs.get("auto_market_buy", True)):
                try:
                    ga_id = job.get("game_account_id")
                    if ga_id:
                        market_eta, extra_inputs = self._try_cover_with_internal_market(
                            jid=jid,
                            ga_id=str(ga_id),
                            target_city=city,
                            pending=pending,
                            missing=missing,
                            level_rows=pending.get("level_rows") or [],
                        )
                        if market_eta is not None:
                            return max(market_eta, TRANSPORT_RECHECK_SECONDS), False, extra_inputs, False
                except Exception as exc:
                    self.log(jid, "warn", f"Mercado interno falhou: {exc}")

        if wait_seconds:
            return max(MIN_RECHECK_SECONDS, wait_seconds + FINISH_BUFFER_SECONDS), False, None, False

        self.log(
            jid,
            "warn",
            f"{pending['city_name']} sem recurso suficiente para {pending['building_name']} e sem ETA local confiavel",
        )
        return TRANSPORT_RECHECK_SECONDS, False, None, True

    # Resource index mapping matching game_client constants
    _RESOURCE_KEY_TO_IDX = {"wood": 0, "wine": 1, "marble": 2, "glas": 3, "sulfur": 4}

    def _try_cover_with_internal_market(
        self,
        *,
        jid: str,
        ga_id: str,
        target_city: dict[str, Any],
        pending: dict[str, Any],
        missing: dict[str, int],
        level_rows: list[dict[str, Any]],
    ) -> tuple[int | None, dict[str, Any] | None]:
        """Create internal market orders for the missing resources.

        Finds the account's branchOffice city, fetches offers, calculates a buffered
        buy amount (next level + 1 extra if possible, else ×1.20), buys the cheapest
        offers in order, then transports to the target city if needed.

        Returns the estimated ETA in seconds until resources arrive, or None on failure.
        """
        target_city_id = _to_int(target_city.get("id"))
        target_bo_pos = _find_branch_office_position(target_city)
        if target_bo_pos < 0:
            self.log(
                jid,
                "warn",
                f"{pending['city_name']} sem Branch Office; mercado interno nao entrega direto na cidade da obra",
            )
            return None, None

        next_level = _to_int(pending.get("next_level"), 1)
        level_rows_sorted = sorted(level_rows, key=lambda r: _to_int(r.get("level"), 0))
        next_rows = [r for r in level_rows_sorted if _to_int(r.get("level"), 0) >= next_level]
        costs_after = self._normalize_costs((next_rows[1].get("costs") or {}) if len(next_rows) > 1 else {})

        created_any = False
        for resource_key, needed in missing.items():
            if needed <= 0:
                continue
            resource_idx = self._RESOURCE_KEY_TO_IDX.get(resource_key)
            if resource_idx is None:
                continue

            extra = costs_after.get(resource_key, 0)
            buy_amount = needed + extra if extra > 0 else math.ceil(needed * 1.20)
            try:
                order = self.hub.create_market_order(
                    game_account_id=ga_id,
                    resource_idx=resource_idx,
                    amount=buy_amount,
                    unit_price=0,
                    preferred_buyer_city_id=target_city_id,
                    source_job_id=jid,
                    source_action_code=1002,
                    source_reason="construction_missing_resources",
                    reason_detail=(
                        f"{pending['city_name']} | {pending['building_name']} | "
                        f"missing {resource_key}={needed} for level {next_level}"
                    ),
                    missing_resource_keys=resource_key,
                )
            except Exception as exc:
                self.log(jid, "warn", f"Nao foi possivel criar ordem interna para {resource_key}: {exc}")
                continue

            if not order or not order.get("ok"):
                self.log(
                    jid,
                    "warn",
                    f"Nenhum vendedor elegivel no mercado interno para {resource_key} "
                    f"(cidade={pending['city_name']}, falta={needed}, pedido={buy_amount})",
                )
                continue

            created_any = True
            self.log(
                jid,
                "info",
                f"Mercado interno: ordem {order.get('order_id')} criada para {resource_key} "
                f"x{buy_amount} em {pending['city_name']} (faltando {needed})",
            )

        if not created_any:
            return None, None

        self.log(
            jid,
            "info",
            f"Mercado interno solicitado para {pending['city_name']} via Branch Office pos={target_bo_pos}; aguardando processamento",
        )
        return TRANSPORT_RECHECK_SECONDS, {"last_market_order_requested_at": int(time.time())}

        # Determine how much to buy per resource
        next_level = _to_int(pending.get("next_level"), 1)
        level_rows_sorted = sorted(level_rows, key=lambda r: _to_int(r.get("level"), 0))
        next_rows = [r for r in level_rows_sorted if _to_int(r.get("level"), 0) >= next_level]

        costs_next = self._normalize_costs((next_rows[0].get("costs") or {}) if next_rows else {})
        costs_after = self._normalize_costs((next_rows[1].get("costs") or {}) if len(next_rows) > 1 else {})

        bought_any = False
        for resource_key, needed in missing.items():
            if needed <= 0:
                continue
            resource_idx = self._RESOURCE_KEY_TO_IDX.get(resource_key)
            if resource_idx is None:
                continue

            # Buy amount: next level + 1 extra level if possible, else ×1.20
            extra = costs_after.get(resource_key, 0)
            buy_amount = needed + extra if extra > 0 else math.ceil(needed * 1.20)

            try:
                offers = client.get_market_offers(
                    buyer_city_id=bo_city_id,
                    buyer_branchoffice_pos=bo_position,
                    resource_idx=resource_idx,
                )
            except Exception as exc:
                self.log(jid, "warn", f"Nao foi possivel buscar ofertas de {resource_key}: {exc}")
                continue

            if not offers:
                self.log(jid, "warn", f"Nenhuma oferta disponivel no mercado para {resource_key}")
                continue

            # Average price of available offers (weighted by amount)
            total_available = sum(o["amount"] for o in offers)
            if total_available == 0:
                continue
            avg_price = sum(o["price_per_unit"] * o["amount"] for o in offers) / total_available
            self.log(
                jid, "info",
                f"Mercado: {len(offers)} oferta(s) de {resource_key}, preco medio={avg_price:.1f}, total={total_available}",
            )

            # Buy from cheapest offers until buy_amount is covered
            remaining = buy_amount
            for offer in offers:
                if remaining <= 0:
                    break
                to_buy = min(remaining, offer["amount"])
                try:
                    client.buy_market_offer(
                        buyer_city_id=bo_city_id,
                        buyer_branchoffice_pos=bo_position,
                        seller_city_id=offer["seller_city_id"],
                        seller_branchoffice_pos=offer["seller_bo_pos"],
                        resource_idx=resource_idx,
                        amount=to_buy,
                    )
                    remaining -= to_buy
                    bought_any = True
                    self.log(
                        jid, "info",
                        f"Comprado {to_buy} {resource_key} no mercado @ {offer['price_per_unit']}/un"
                        f" (cidade vendedora={offer['seller_city_id']})",
                    )
                except Exception as exc:
                    self.log(jid, "warn", f"Falha ao comprar {to_buy} {resource_key}: {exc}")
                    break

            if remaining > 0:
                self.log(jid, "warn", f"Mercado sem quantidade suficiente de {resource_key}: faltam {remaining}")

        if not bought_any:
            return None

        # If branchOffice city ≠ target city, we need to transport the resources
        if bo_city_id != target_city_id:
            try:
                from services.resource_transport import prepare_transport, submit_transport
                SNAPSHOT_TO_TRANSPORT = {"wood": "wood", "wine": "wine", "marble": "marble", "glas": "crystal", "sulfur": "sulfur"}
                transport_resources = {
                    SNAPSHOT_TO_TRANSPORT.get(k, k): math.ceil(v * 1.05)
                    for k, v in missing.items() if v > 0
                }
                plan = prepare_transport(
                    client,
                    from_city_id=bo_city_id,
                    to_city_id=target_city_id,
                    requested=transport_resources,
                )
                submit_transport(client, plan)
                self.log(
                    jid, "info",
                    f"Transporte criado de Mercado ({bo_city.get('name')}) → {target_city.get('name')}",
                )
            except Exception as exc:
                self.log(jid, "warn", f"Falha ao criar transporte pós-compra: {exc}")

        # Estimate arrival ETA
        try:
            eta = estimate_incoming_transport_wait_seconds(client, target_city_id)
            if eta:
                self.log(jid, "info", f"Recurso comprado no mercado; chegada estimada em {_format_duration(eta)}")
                return eta + FINISH_BUFFER_SECONDS
        except Exception as exc:
            logger.debug("Nao foi possivel estimar ETA pós-compra: %s", exc)

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
        for key in RESOURCE_KEYS:
            transport_need[key] = max(transport_need[key], max(0, _to_int(missing.get(key), 0)))
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
