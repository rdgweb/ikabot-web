from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import requests

from apps.game.models import AccountSnapshot
from core.catalogs import BUILDING_CATALOG, get_building_info


IKA_TOOLS_URL = "https://ika-tools.com/"
RESOURCE_KEYS = ("wood", "wine", "marble", "glas", "sulfur")
RESOURCE_LABELS = {
    "wood": "Madeira",
    "wine": "Vinho",
    "marble": "Marmore",
    "glas": "Cristal",
    "sulfur": "Enxofre",
}
RESOURCE_ICON_MAP = {
    "wood": "game/resources/icon_wood.png",
    "wine": "game/resources/icon_wine.png",
    "marble": "game/resources/icon_marble.png",
    "glas": "game/resources/icon_glass.png",
    "sulfur": "game/resources/icon_sulfur.png",
}
BUILDING_TO_IKA_TOOLS = {
    "academy": "academy",
    "alchemist": "alchemistsTower",
    "architect": "architectsOffice",
    "barracks": "barracks",
    "blackMarket": "blackMarket",
    "carpentering": "carpenter",
    "chronos_forge": "chronos_forge",
    "dump": "depot",
    "dockyard": "dockyard",
    "embassy": "embassy",
    "fireworker": "fireworkTestArea",
    "forester": "forestersHouse",
    "glassblowing": "glassblower",
    "gods_shrine": "gods_shrine",
    "governorsResidence": "governorsResidence",
    "hideout": "hideout",
    "safehouse": "hideout",
    "museum": "museum",
    "optician": "optician",
    "palace": "palace",
    "pirateFortress": "pirateFortress",
    "marineChartArchive": "seaChartArchive",
    "shipyard": "shipyard",
    "stonemason": "stonemason",
    "tavern": "tavern",
    "temple": "temple",
    "townHall": "townHall",
    "wall": "townWall",
    "port": "tradingPort",
    "branchOffice": "tradingPost",
    "marketplace": "tradingPost",
    "warehouse": "warehouse",
    "winePress": "winePress",
    "vineyard": "winegrower",
    "workshop": "workshop",
}
REDUCER_BUILDINGS = {
    "wood": "carpentering",
    "wine": "vineyard",
    "marble": "architect",
    "glas": "optician",
    "sulfur": "fireworker",
}


@dataclass
class ConstructionPreview:
    building_id: str
    building_name: str
    building_icon: str | None
    city_name: str
    current_level: int
    target_level: int
    research_reduction: int
    build_time_reduction: str
    chronos_level: int
    chronos_reduction: float
    reducer_levels: dict[str, int]
    simulated_reducer_levels: dict[str, int]
    final_chronos_level: int
    final_chronos_reduction: float
    totals: dict[str, int]
    missing: dict[str, int]
    available: dict[str, int]
    base_seconds: int
    adjusted_seconds: int
    level_rows: list[dict[str, Any]]
    notes: list[str]


@dataclass
class ConstructionPlanPreview:
    steps: list[dict[str, Any]]
    totals: dict[str, int]
    reserved_local: dict[str, int]
    missing: dict[str, int]
    base_seconds: int
    adjusted_seconds: int
    notes: list[str]


def _parse_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(str(raw)))
    except (TypeError, ValueError):
        return default


def _building_key_from_position(city: dict[str, Any], position: str | int) -> tuple[str, int]:
    wanted = _parse_int(position, default=-1)
    for item in city.get("buildings") or []:
        if _parse_int(item.get("position"), default=-2) != wanted:
            continue
        return str(item.get("building") or "").strip(), _parse_int(item.get("level"))
    return "", 0


def _seconds_from_duration(value: str) -> int:
    text = str(value or "").strip()
    if not text or text == "-":
        return 0
    total = 0
    for token in text.split():
        if token.endswith("s"):
            total += _parse_int(token[:-1])
        elif token.endswith("m"):
            total += _parse_int(token[:-1]) * 60
        elif token.endswith("h"):
            total += _parse_int(token[:-1]) * 3600
        elif token.endswith("D"):
            total += _parse_int(token[:-1]) * 86400
        elif token.endswith("M"):
            total += _parse_int(token[:-1]) * 30 * 86400
        elif token.endswith("Y"):
            total += _parse_int(token[:-1]) * 365 * 86400
    return total


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "-"
    parts: list[str] = []
    remaining = int(seconds)
    units = [
        ("Y", 365 * 86400),
        ("M", 30 * 86400),
        ("D", 86400),
        ("h", 3600),
        ("m", 60),
        ("s", 1),
    ]
    for suffix, scale in units:
        if remaining < scale:
            continue
        amount = remaining // scale
        remaining %= scale
        parts.append(f"{amount}{suffix}")
        if len(parts) >= 3:
            break
    return " ".join(parts) if parts else "0s"


def _js_object_array_to_python(raw: str) -> list[dict[str, Any]]:
    jsonish = re.sub(r"([{,])([A-Za-z_][A-Za-z0-9_]*):", r'\1"\2":', raw)
    jsonish = re.sub(
        r"(?<=:|\[|,)\s*0x[0-9A-Fa-f]+",
        lambda m: str(int(m.group(0).strip(), 16)),
        jsonish,
    )
    return json.loads(f"[{jsonish}]")


def _extract_js_array(source: str, var_name: str) -> str | None:
    marker = f"{var_name}=["
    start = source.find(marker)
    if start < 0:
        return None
    i = start + len(marker)
    depth = 1
    in_string = False
    escaped = False
    quote_char = ""
    chunks: list[str] = []
    while i < len(source):
        ch = source[i]
        if in_string:
            chunks.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote_char:
                in_string = False
        else:
            if ch in ("'", '"'):
                in_string = True
                quote_char = ch
                chunks.append(ch)
            elif ch == "[":
                depth += 1
                chunks.append(ch)
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return "".join(chunks)
                chunks.append(ch)
            else:
                chunks.append(ch)
        i += 1
    return None


def _extract_json_parse_array(source: str, var_name: str) -> str | None:
    match = re.search(rf"\b{re.escape(var_name)}=JSON\.parse\('((?:\\.|[^'])*)'\)", source)
    if not match:
        return None
    raw = match.group(1)
    try:
        return json.loads(f'"{raw}"')
    except Exception:
        return None


@lru_cache(maxsize=1)
def get_ika_tools_tables() -> dict[str, list[dict[str, Any]]]:
    home = requests.get(IKA_TOOLS_URL, timeout=20)
    home.raise_for_status()
    match = re.search(r'src="\./assets/(index-[^"]+\.js)"', home.text)
    if not match:
        raise RuntimeError("ika_tools_bundle_not_found")

    bundle_url = f"{IKA_TOOLS_URL}assets/{match.group(1)}"
    js = requests.get(bundle_url, timeout=30)
    js.raise_for_status()
    source = js.text

    mapping_match = re.search(r"const rl=\{(.*?)\},E1=", source, re.DOTALL)
    if not mapping_match:
        raise RuntimeError("ika_tools_mapping_not_found")

    tables: dict[str, list[dict[str, Any]]] = {}
    for match in re.finditer(r"([A-Za-z0-9_]+):([A-Za-z0-9_]+)", mapping_match.group(1)):
        slug = match.group(1).strip()
        var_name = match.group(2).strip()
        raw_array = _extract_js_array(source, var_name)
        if not raw_array:
            raw_array = _extract_json_parse_array(source, var_name)
        if not raw_array:
            continue
        try:
            if raw_array.lstrip().startswith("["):
                tables[slug] = json.loads(raw_array)
            else:
                tables[slug] = _js_object_array_to_python(raw_array)
        except Exception:
            continue

    if not tables:
        raise RuntimeError("ika_tools_tables_empty")
    return tables


def _get_snapshot_city(game_account, city_id: str | int) -> dict[str, Any] | None:
    try:
        snapshot = AccountSnapshot.objects.get(game_account=game_account)
    except AccountSnapshot.DoesNotExist:
        return None
    cities = snapshot.cities
    if isinstance(cities, dict):
        cities = cities.get("cities", [])
    for city in cities or []:
        if str(city.get("id")) == str(city_id):
            return city
    return None


def _city_resource_stock(city: dict[str, Any]) -> dict[str, int]:
    return {
        "wood": _parse_int(city.get("wood")),
        "wine": _parse_int(city.get("wine")),
        "marble": _parse_int(city.get("marble")),
        "glas": _parse_int(city.get("crystal")),
        "sulfur": _parse_int(city.get("sulfur")),
    }


def _city_reducer_levels(city: dict[str, Any]) -> tuple[dict[str, int], int]:
    levels = {key: 0 for key in RESOURCE_KEYS}
    chronos_level = 0
    for item in city.get("buildings") or []:
        building_id = str(item.get("building") or "").strip()
        level = _parse_int(item.get("level"))
        for resource_key, reducer_building in REDUCER_BUILDINGS.items():
            if building_id == reducer_building:
                levels[resource_key] = level
        if building_id == "chronos_forge":
            chronos_level = level
    return levels, chronos_level


def _cost_after_reduction(base: int, research_reduction: int, building_reduction: int) -> int:
    if base <= 0:
        return 0
    reduced = base * (1 - (research_reduction / 100.0))
    reduced -= base * (building_reduction / 100.0)
    return max(0, int(math.ceil(reduced)))


def _adjusted_time(seconds: int, time_modifier: str, chronos_reduction: float) -> int:
    value = float(seconds)
    if time_modifier == ":3":
        value = value / 3.0
    else:
        value *= 1 - (_parse_int(time_modifier) / 100.0)
    if chronos_reduction > 0:
        value *= 1 - (chronos_reduction / 100.0)
    return max(0, int(math.ceil(value)))


def _get_chronos_reduction(chronos_rows: list[dict[str, Any]], chronos_level: int) -> float:
    for row in chronos_rows:
        if _parse_int(row.get("level")) == chronos_level:
            return float(row.get("construction_time_reduction") or 0.0)
    return 0.0


def _get_city_building_levels(city: dict[str, Any]) -> dict[str, int]:
    levels: dict[str, int] = {}
    for item in city.get("buildings") or []:
        building_id = str(item.get("building") or "").strip()
        if not building_id or building_id == "empty":
            continue
        levels[building_id] = _parse_int(item.get("level"))
    return levels


def _get_city_building_at_position(city: dict[str, Any], position: str | int) -> tuple[str, int]:
    wanted = _parse_int(position, default=-1)
    if wanted < 0:
        return "", 0
    for item in city.get("buildings") or []:
        if _parse_int(item.get("position"), default=-2) != wanted:
            continue
        return str(item.get("building") or "").strip(), _parse_int(item.get("level"))
    return "", 0


def build_construction_preview(
    *,
    game_account,
    city_id: str | int,
    building_position: str | int | None = None,
    building_type: str | None = None,
    target_level: int,
    research_reduction: int = 14,
    build_time_reduction: str = "25",
) -> ConstructionPreview | None:
    city = _get_snapshot_city(game_account, city_id)
    if not city:
        return None

    if building_position not in (None, ""):
        building_id, current_level = _building_key_from_position(city, building_position)
    else:
        building_id = str(building_type or "").strip()
        current_level = 0
    if not building_id:
        return None

    ika_key = BUILDING_TO_IKA_TOOLS.get(building_id)
    tables = get_ika_tools_tables()
    rows = tables.get(ika_key or "")
    if not rows:
        notes = [f"Sem tabela de custos mapeada para {building_id}."]
        return ConstructionPreview(
            building_id=building_id,
            building_name=get_building_info(building_id).get("name", building_id),
            building_icon=get_building_info(building_id).get("icon"),
            city_name=str(city.get("name") or ""),
            current_level=current_level,
            target_level=target_level,
            research_reduction=research_reduction,
            build_time_reduction=build_time_reduction,
            chronos_level=0,
            chronos_reduction=0.0,
            reducer_levels={key: 0 for key in RESOURCE_KEYS},
            simulated_reducer_levels={key: 0 for key in RESOURCE_KEYS},
            final_chronos_level=0,
            final_chronos_reduction=0.0,
            totals={key: 0 for key in RESOURCE_KEYS},
            missing={key: 0 for key in RESOURCE_KEYS},
            available=_city_resource_stock(city),
            base_seconds=0,
            adjusted_seconds=0,
            level_rows=[],
            notes=notes,
        )

    reducer_levels, chronos_level = _city_reducer_levels(city)
    simulated_reducer_levels = dict(reducer_levels)
    simulated_chronos_level = chronos_level
    chronos_rows = tables.get("chronos_forge") or []
    chronos_reduction = _get_chronos_reduction(chronos_rows, chronos_level)
    simulated_chronos_reduction = chronos_reduction

    desired_level = max(current_level + 1 if current_level > 0 else 1, _parse_int(target_level, default=1))
    level_rows = [
        row for row in rows
        if current_level < _parse_int(row.get("level")) <= desired_level
    ]

    totals = {key: 0 for key in RESOURCE_KEYS}
    available = _city_resource_stock(city)
    base_seconds = 0
    adjusted_seconds = 0
    normalized_rows: list[dict[str, Any]] = []

    for row in level_rows:
        row_costs: dict[str, int] = {}
        for resource_key in RESOURCE_KEYS:
            raw_value = row.get(resource_key)
            base_cost = _parse_int(raw_value) if raw_value is not None else 0
            reducer_level = simulated_reducer_levels.get(resource_key, 0)
            final_cost = _cost_after_reduction(base_cost, research_reduction, reducer_level)
            row_costs[resource_key] = final_cost
            totals[resource_key] += final_cost

        base_row_seconds = _seconds_from_duration(str(row.get("building_time") or ""))
        adjusted_row_seconds = _adjusted_time(base_row_seconds, build_time_reduction, simulated_chronos_reduction)
        base_seconds += base_row_seconds
        adjusted_seconds += adjusted_row_seconds
        normalized_rows.append({
            "level": _parse_int(row.get("level")),
            "costs": row_costs,
            "reducer_levels": dict(simulated_reducer_levels),
            "chronos_level": simulated_chronos_level,
            "chronos_reduction": simulated_chronos_reduction,
            "base_seconds": base_row_seconds,
            "adjusted_seconds": adjusted_row_seconds,
            "base_duration": _format_duration(base_row_seconds),
            "adjusted_duration": _format_duration(adjusted_row_seconds),
        })

        for resource_key, reducer_building in REDUCER_BUILDINGS.items():
            if building_id == reducer_building:
                simulated_reducer_levels[resource_key] = _parse_int(row.get("level"))
                break
        if building_id == "chronos_forge":
            simulated_chronos_level = _parse_int(row.get("level"))
            simulated_chronos_reduction = _get_chronos_reduction(chronos_rows, simulated_chronos_level)

    missing = {
        key: max(0, totals[key] - available.get(key, 0))
        for key in RESOURCE_KEYS
    }
    notes: list[str] = []
    if building_id in REDUCER_BUILDINGS.values() or building_id == "chronos_forge":
        notes.append("Simulacao encadeada aplicada: niveis futuros usam os redutores e o Chronos resultantes das etapas anteriores.")
    if desired_level > (normalized_rows[-1]["level"] if normalized_rows else current_level):
        notes.append("A tabela externa nao cobriu todos os niveis pedidos.")

    info = get_building_info(building_id)
    return ConstructionPreview(
        building_id=building_id,
        building_name=info.get("name", building_id),
        building_icon=info.get("icon"),
        city_name=str(city.get("name") or ""),
        current_level=current_level,
        target_level=desired_level,
        research_reduction=research_reduction,
        build_time_reduction=str(build_time_reduction or "0"),
        chronos_level=chronos_level,
        chronos_reduction=chronos_reduction,
        reducer_levels=reducer_levels,
        simulated_reducer_levels=simulated_reducer_levels,
        final_chronos_level=simulated_chronos_level,
        final_chronos_reduction=simulated_chronos_reduction,
        totals=totals,
        missing=missing,
        available=available,
        base_seconds=base_seconds,
        adjusted_seconds=adjusted_seconds,
        level_rows=normalized_rows,
        notes=notes,
    )


def build_construction_plan_preview(
    *,
    game_account,
    steps: list[dict[str, Any]],
    research_reduction: int = 14,
    build_time_reduction: str = "25",
) -> ConstructionPlanPreview:
    tables = get_ika_tools_tables()
    chronos_rows = tables.get("chronos_forge") or []
    city_states: dict[str, dict[str, Any]] = {}
    totals = {key: 0 for key in RESOURCE_KEYS}
    reserved_local = {key: 0 for key in RESOURCE_KEYS}
    missing = {key: 0 for key in RESOURCE_KEYS}
    base_seconds = 0
    adjusted_seconds = 0
    preview_steps: list[dict[str, Any]] = []
    notes: list[str] = []

    for idx, step in enumerate(steps, start=1):
        city_id = str(step.get("city_id") or step.get("city") or "").strip()
        if not city_id:
            continue

        city_state = city_states.get(city_id)
        if not city_state:
            city = _get_snapshot_city(game_account, city_id)
            if not city:
                notes.append(f"Etapa {idx}: cidade {city_id} sem snapshot.")
                continue
            reducer_levels, chronos_level = _city_reducer_levels(city)
            city_state = {
                "city": city,
                "available": _city_resource_stock(city),
                "building_levels": _get_city_building_levels(city),
                "reducer_levels": dict(reducer_levels),
                "chronos_level": chronos_level,
                "chronos_reduction": _get_chronos_reduction(chronos_rows, chronos_level),
            }
            city_states[city_id] = city_state

        city = city_state["city"]
        building_id = str(step.get("building_id") or step.get("building_type") or "").strip()
        if not building_id:
            if step.get("building_position") not in (None, ""):
                building_id, _ = _building_key_from_position(city, step.get("building_position"))
        if not building_id:
            notes.append(f"Etapa {idx}: edificio indefinido.")
            continue

        ika_key = BUILDING_TO_IKA_TOOLS.get(building_id)
        rows = tables.get(ika_key or "")
        if not rows:
            notes.append(f"Etapa {idx}: sem tabela de custos para {building_id}.")
            continue

        step_mode = str(step.get("mode") or "upgrade")
        if step_mode == "new":
            preferred_position = step.get("preferred_position")
            slot_building_id, slot_level = _get_city_building_at_position(city, preferred_position)
            current_level = slot_level if slot_building_id == building_id else 0
        else:
            current_level = int(city_state["building_levels"].get(building_id, 0))
        target_level = max(current_level + 1 if current_level > 0 else 1, _parse_int(step.get("target_level"), 1))
        level_rows = [row for row in rows if current_level < _parse_int(row.get("level")) <= target_level]
        if not level_rows:
            notes.append(f"Etapa {idx}: nenhuma etapa calculada para {building_id} ate {target_level}.")
            continue

        step_totals = {key: 0 for key in RESOURCE_KEYS}
        step_reserved_local = {key: 0 for key in RESOURCE_KEYS}
        step_missing = {key: 0 for key in RESOURCE_KEYS}
        step_base_seconds = 0
        step_adjusted_seconds = 0
        step_level_rows: list[dict[str, Any]] = []

        for row in level_rows:
            row_costs = {key: 0 for key in RESOURCE_KEYS}
            for resource_key in RESOURCE_KEYS:
                base_cost = _parse_int(row.get(resource_key)) if row.get(resource_key) is not None else 0
                reducer_level = int(city_state["reducer_levels"].get(resource_key, 0))
                final_cost = _cost_after_reduction(base_cost, research_reduction, reducer_level)
                row_costs[resource_key] = final_cost
                if final_cost <= 0:
                    continue
                step_totals[resource_key] += final_cost
                totals[resource_key] += final_cost
                local_commit = min(final_cost, int(city_state["available"].get(resource_key, 0)))
                if local_commit > 0:
                    city_state["available"][resource_key] -= local_commit
                    step_reserved_local[resource_key] += local_commit
                    reserved_local[resource_key] += local_commit
                if final_cost > local_commit:
                    shortfall = final_cost - local_commit
                    step_missing[resource_key] += shortfall
                    missing[resource_key] += shortfall

            base_row_seconds = _seconds_from_duration(str(row.get("building_time") or ""))
            adjusted_row_seconds = _adjusted_time(
                base_row_seconds,
                build_time_reduction,
                float(city_state["chronos_reduction"]),
            )
            step_base_seconds += base_row_seconds
            step_adjusted_seconds += adjusted_row_seconds
            step_level_rows.append({
                "level": _parse_int(row.get("level")),
                "costs": row_costs,
                "base_seconds": base_row_seconds,
                "adjusted_seconds": adjusted_row_seconds,
                "base_duration": _format_duration(base_row_seconds),
                "adjusted_duration": _format_duration(adjusted_row_seconds),
            })

            level_value = _parse_int(row.get("level"))
            city_state["building_levels"][building_id] = level_value
            for resource_key, reducer_building in REDUCER_BUILDINGS.items():
                if building_id == reducer_building:
                    city_state["reducer_levels"][resource_key] = level_value
                    break
            if building_id == "chronos_forge":
                city_state["chronos_level"] = level_value
                city_state["chronos_reduction"] = _get_chronos_reduction(chronos_rows, level_value)

        base_seconds += step_base_seconds
        adjusted_seconds += step_adjusted_seconds
        info = get_building_info(building_id)
        preview_steps.append({
            "index": idx,
            "city_id": city_id,
            "city_name": str(city.get("name") or city_id),
            "building_id": building_id,
            "building_name": info.get("name", building_id),
            "building_icon": info.get("icon"),
            "mode": step_mode,
            "slot_types": list(step.get("slot_types") or []),
            "preferred_position": str(step.get("preferred_position") or ""),
            "from_level": current_level,
            "target_level": target_level,
            "totals": step_totals,
            "reserved_local": step_reserved_local,
            "missing": step_missing,
            "base_seconds": step_base_seconds,
            "adjusted_seconds": step_adjusted_seconds,
            "base_duration": _format_duration(step_base_seconds),
            "adjusted_duration": _format_duration(step_adjusted_seconds),
            "level_rows": step_level_rows,
        })

    if preview_steps:
        notes.append("Reserva local simulada: o plano consome o estoque atual da cidade entre as etapas, evitando contar o mesmo recurso duas vezes.")
    return ConstructionPlanPreview(
        steps=preview_steps,
        totals=totals,
        reserved_local=reserved_local,
        missing=missing,
        base_seconds=base_seconds,
        adjusted_seconds=adjusted_seconds,
        notes=notes,
    )
