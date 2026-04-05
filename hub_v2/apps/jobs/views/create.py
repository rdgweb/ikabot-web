"""
Job creation views — modal wizard with dynamic forms.

Flow:
  1. /jobs/new/                  → Step 1 (pick GA) or smart redirect
  2. /jobs/new/?action=901       → Step 1 (pick GA for action 901; auto-skip if 1 GA)
  3. /jobs/new/?ga=X&action=901  → Step 2 (form for GA X + action 901)
  4. /jobs/new/actions/?ga=X     → Step 1b (pick action for GA X)
  5. /jobs/new/form/?ga=X&action=901 → Step 2 (form)
  6. /jobs/new/submit/           → POST create job(s)
"""

import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views import View
from django.templatetags.static import static

from apps.accounts.models import Account, GameAccount
from apps.game.models import AccountSnapshot
from core.catalogs import BUILDING_CATALOG
from core.contracts import ACTION_CATALOG, get_actions_for_ui
from ..forms import JobCreateForm
from ..models import ConstructionResourceReservation, Job
from ..services.construction_preview import (
    IKA_TOOLS_URL,
    RESOURCE_ICON_MAP,
    RESOURCE_LABELS,
    build_construction_plan_preview,
    build_construction_preview,
)

logger = logging.getLogger(__name__)

CONSTRUCTION_REPEATABLE_BUILDINGS = {"port", "warehouse", "dump"}
CONSTRUCTION_EXCLUDED_NEW_BUILDINGS = {"empty", "brpiort", "forpiester", "winpiegrower", "firpieworker", "pipiracy", "marpiket"}
CONSTRUCTION_BUILDING_SLOT_TYPES = {
    "port": {"shore"},
    "shipyard": {"shore"},
    "marineChartArchive": {"shore"},
    "wall": {"wall"},
    "pirateFortress": {"sea"},
    "reducePiracy": {"sea"},
}
CONSTRUCTION_LUXURY_BUILDINGS = {
    1: {"vineyard", "winePress"},
    2: {"architect", "stonemason"},
    3: {"optician", "glassblowing"},
    4: {"fireworker", "alchemist"},
}
TRADEGOOD_UI = {
    0: {"name": "Madeira", "icon": "game/resources/icon_wood.png"},
    1: {"name": "Vinho", "icon": "game/resources/icon_wine.png"},
    2: {"name": "Marmore", "icon": "game/resources/icon_marble.png"},
    3: {"name": "Cristal", "icon": "game/resources/icon_glass.png"},
    4: {"name": "Enxofre", "icon": "game/resources/icon_sulfur.png"},
}
CITY_RESOURCE_FIELDS = (
    ("wood", "Madeira", "game/resources/icon_wood.png"),
    ("wine", "Vinho", "game/resources/icon_wine.png"),
    ("marble", "Marmore", "game/resources/icon_marble.png"),
    ("crystal", "Cristal", "game/resources/icon_glass.png"),
    ("sulfur", "Enxofre", "game/resources/icon_sulfur.png"),
)
SHRINE_GOD_FIELDS = (
    "god_pan",
    "god_dionysus",
    "god_tyche",
    "god_plutus",
    "god_theia",
    "god_hephaestus",
)
SHRINE_GOD_UI = (
    {
        "field": "god_pan",
        "key": "pan",
        "id": 1,
        "name": "Pan",
        "subtitle": "Lorde da Floresta",
        "effect": "Bencao de madeira",
        "effect_icon": "game/resources/icon_wood.png",
        "icon": "game/gods/pan.png",
        "accent": "#4f86b9",
        "surface": "rgba(79,134,185,0.14)",
    },
    {
        "field": "god_dionysus",
        "key": "dionysus",
        "id": 2,
        "name": "Dionisio",
        "subtitle": "Hora da Festa!",
        "effect": "Bencao de vinho",
        "effect_icon": "game/resources/icon_wine.png",
        "icon": "game/gods/dionysus.png",
        "accent": "#8e5476",
        "surface": "rgba(142,84,118,0.14)",
    },
    {
        "field": "god_tyche",
        "key": "tyche",
        "id": 3,
        "name": "Tique",
        "subtitle": "Uma Feliz Coincidencia",
        "effect": "Bencao de marmore",
        "effect_icon": "game/resources/icon_marble.png",
        "icon": "game/gods/tyche.png",
        "accent": "#3f8c7d",
        "surface": "rgba(63,140,125,0.14)",
    },
    {
        "field": "god_plutus",
        "key": "plutus",
        "id": 4,
        "name": "Pluto",
        "subtitle": "Riquezas das Profundezas",
        "effect": "Bencao de ouro",
        "effect_icon": "game/gods/favor.png",
        "icon": "game/gods/plutus.png",
        "accent": "#c99c4e",
        "surface": "rgba(201,156,78,0.16)",
    },
    {
        "field": "god_theia",
        "key": "theia",
        "id": 5,
        "name": "Teia",
        "subtitle": "O Esplendor da Deusa",
        "effect": "Bencao de cristal",
        "effect_icon": "game/resources/icon_glass.png",
        "icon": "game/gods/theia.png",
        "accent": "#8f6cc2",
        "surface": "rgba(143,108,194,0.16)",
    },
    {
        "field": "god_hephaestus",
        "key": "hephaestus",
        "id": 6,
        "name": "Hefesto",
        "subtitle": "Forjado em Lava",
        "effect": "Bencao de enxofre",
        "effect_icon": "game/resources/icon_sulfur.png",
        "icon": "game/gods/hephaestus.png",
        "accent": "#cc6b4a",
        "surface": "rgba(204,107,74,0.16)",
    },
)
RESEARCH_BRANCH_UI = (
    {
        "id": "seafaring",
        "field": "branch_seafaring",
        "name": "Navegacao Maritima",
        "subtitle": "Navios, porto e mar",
        "description": "Rotas, manutencao naval e tecnologias de porto.",
        "icon": "bi-water",
        "accent": "#4f86b9",
        "surface": "rgba(79,134,185,0.14)",
        "resource_icon": "game/resources/icon_wood.png",
    },
    {
        "id": "economy",
        "field": "branch_economy",
        "name": "Economia",
        "subtitle": "Crescimento e recursos",
        "description": "Habitantes, armazens e eficiencia economica.",
        "icon": "bi-coin",
        "accent": "#c99c4e",
        "surface": "rgba(201,156,78,0.16)",
        "resource_icon": "game/resources/icon_population.png",
    },
    {
        "id": "knowledge",
        "field": "branch_knowledge",
        "name": "Ciencia",
        "subtitle": "Academias e pesquisa",
        "description": "Pontos de pesquisa, cultura e ciencia aplicada.",
        "icon": "bi-lightbulb",
        "accent": "#8f6cc2",
        "surface": "rgba(143,108,194,0.16)",
        "resource_icon": "game/resources/icon_glass.png",
    },
    {
        "id": "military",
        "field": "branch_military",
        "name": "Militar",
        "subtitle": "Exercito e defesa",
        "description": "Unidades, cercos e manutencao militar.",
        "icon": "bi-shield-check",
        "accent": "#cc6b4a",
        "surface": "rgba(204,107,74,0.16)",
        "resource_icon": "game/resources/icon_sulfur.png",
    },
    {
        "id": "mythology",
        "field": "branch_mythology",
        "name": "Mitologia",
        "subtitle": "Deuses e milagres",
        "description": "Linha mitologica e desbloqueios divinos.",
        "icon": "bi-stars",
        "accent": "#5f8c4e",
        "surface": "rgba(95,140,78,0.14)",
        "resource_icon": "game/gods/favor.png",
    },
)


def _default_construction_modifiers(action_code: int) -> tuple[int, str]:
    if int(action_code) == 1002:
        # For the planner, global modifiers must come from the game state.
        # Until research/global bonus sync exists, use conservative zeroes
        # and keep only city-derived reducers/Chronos in the preview.
        return 0, "0"
    return 14, "25"


def _normalize_building_id(building_id: str) -> str:
    key = str(building_id or "").strip().split()[0]
    aliases = {
        "chronosForge": "chronos_forge",
        "palaceColony": "governorsResidence",
        "branchOffice": "branchOffice",
        "marketplace": "branchOffice",
        "shrineOfOlympus": "shrineOfOlympus",
        "dockyard": "dockyard",
    }
    return aliases.get(key, key)


def _construction_building_ui():
    out = {}
    for key, info in BUILDING_CATALOG.items():
        if key in CONSTRUCTION_EXCLUDED_NEW_BUILDINGS:
            continue
        icon = info.get("icon")
        out[key] = {
            "name": info.get("name", key),
            "icon": static(f"game/buildings/{icon}") if icon else "",
            "bi": info.get("bi") or "bi-building",
            "slot_types": sorted(CONSTRUCTION_BUILDING_SLOT_TYPES.get(key, {"land"})),
        }
    return out


def _construction_city_data(cities):
    building_ui = _construction_building_ui()
    prepared = []
    for city in cities or []:
        buildings = []
        built_ids = set()
        empty_slots = 0
        empty_slots_by_type: dict[str, list[int]] = {"land": [], "shore": [], "sea": [], "wall": []}
        for item in city.get("buildings") or []:
            building_id = _normalize_building_id(str(item.get("building") or "").strip())
            level = int(item.get("level", 0) or 0)
            position = int(item.get("position", 0) or 0)
            slot_type = str(item.get("type") or "").strip()
            if building_id == "empty":
                empty_slots += 1
                if slot_type in empty_slots_by_type:
                    empty_slots_by_type[slot_type].append(position)
                continue
            if building_id:
                built_ids.add(building_id)
                buildings.append({
                    "position": position,
                    "building": building_id,
                    "level": level,
                })

        try:
            tradegood_id = int(city.get("produced_tradegood") or city.get("tradegood") or 0)
        except Exception:
            tradegood_id = 0
        tradegood_meta = TRADEGOOD_UI.get(tradegood_id, TRADEGOOD_UI[0])
        available_new = []
        synced_options = city.get("available_build_options") or []
        if synced_options:
            for item in synced_options:
                building_id = _normalize_building_id(str(item.get("building") or "").strip())
                if not building_id:
                    continue
                if item.get("researched") is False or str(item.get("locked_reason") or "").strip() == "research":
                    continue
                meta = building_ui.get(building_id) or {}
                available_positions = sorted(
                    {
                        int(pos)
                        for pos in (item.get("available_positions") or [])
                        if str(pos).strip()
                    }
                )
                preferred_position = int(item.get("preferred_position") or 0) if item.get("preferred_position") else (available_positions[0] if available_positions else 0)
                slot_types = [str(slot).strip() for slot in (item.get("slot_types") or []) if str(slot).strip()]
                if not available_positions:
                    continue
                available_new.append({
                    "building": building_id,
                    "name": str(item.get("name") or meta.get("name") or building_id),
                    "icon": meta.get("icon", ""),
                    "slot_types": slot_types or list(meta.get("slot_types") or ["land"]),
                    "preferred_position": preferred_position,
                    "available_positions": available_positions,
                })
        elif empty_slots > 0:
            for building_id, meta in building_ui.items():
                if building_id in CONSTRUCTION_EXCLUDED_NEW_BUILDINGS:
                    continue
                required_tradegood = next(
                    (tradegood for tradegood, buildings in CONSTRUCTION_LUXURY_BUILDINGS.items() if building_id in buildings),
                    None,
                )
                if required_tradegood is not None and tradegood_id != required_tradegood:
                    continue
                if building_id in built_ids and building_id not in CONSTRUCTION_REPEATABLE_BUILDINGS:
                    continue
                slot_types = list(meta.get("slot_types") or ["land"])
                viable_positions = []
                for slot_type in slot_types:
                    viable_positions.extend(empty_slots_by_type.get(slot_type, []))
                if not viable_positions:
                    continue
                available_new.append({
                    "building": building_id,
                    "name": meta["name"],
                    "icon": meta.get("icon", ""),
                    "slot_types": slot_types,
                    "preferred_position": min(viable_positions),
                    "available_positions": sorted(set(viable_positions)),
                })
            available_new.sort(key=lambda item: item["name"])
        try:
            x = int(city.get("x")) if city.get("x") not in (None, "") else None
        except Exception:
            x = None
        try:
            y = int(city.get("y")) if city.get("y") not in (None, "") else None
        except Exception:
            y = None
        prepared.append({
            "id": city.get("id"),
            "name": city.get("name", ""),
            "x": x,
            "y": y,
            "population": int(city.get("population", 0) or 0),
            "free_citizens": int(city.get("free_citizens", 0) or 0),
            "storage_capacity": int(city.get("storage_capacity", 0) or 0),
            "tradegood_id": tradegood_id,
            "tradegood_name": tradegood_meta["name"],
            "tradegood_icon": static(tradegood_meta["icon"]),
            "city_art": static("game/buildings/townhall.png"),
            "resources": [
                {
                    "key": key,
                    "label": label,
                    "icon": static(icon),
                    "amount": int(city.get(key, 0) or 0),
                }
                for key, label, icon in CITY_RESOURCE_FIELDS
            ],
            "buildings": buildings,
            "available_new_buildings": available_new,
            "empty_slots": empty_slots,
            "empty_slots_by_type": empty_slots_by_type,
        })
    return prepared


def _resolve_construction_new_slots(steps, cities):
    city_lookup = {str(city.get("id")): city for city in cities if city.get("id") is not None}
    used_positions_by_city: dict[str, set[int]] = {}
    resolved = []

    for step in steps:
        step_copy = dict(step)
        if str(step_copy.get("mode") or "upgrade").strip().lower() != "new":
            resolved.append(step_copy)
            continue

        city_id = str(step_copy.get("city_id") or "").strip()
        building_id = _normalize_building_id(str(step_copy.get("building_id") or step_copy.get("building_type") or "").strip())
        city = city_lookup.get(city_id) or {}
        options = city.get("available_new_buildings") or []
        option = next(
            (item for item in options if _normalize_building_id(str(item.get("building") or "").strip()) == building_id),
            None,
        )
        available_positions = []
        if option:
            available_positions = [
                int(pos) for pos in (option.get("available_positions") or []) if str(pos).strip()
            ]
        preferred_position = int(step_copy.get("preferred_position") or 0) if str(step_copy.get("preferred_position") or "").strip() else 0
        if preferred_position and preferred_position not in available_positions:
            available_positions.insert(0, preferred_position)
        if preferred_position:
            available_positions = [preferred_position] + [pos for pos in available_positions if pos != preferred_position]

        used = used_positions_by_city.setdefault(city_id, set())
        selected_position = 0
        for pos in available_positions:
            if pos in used:
                continue
            selected_position = pos
            break
        if not selected_position and preferred_position and preferred_position not in used:
            selected_position = preferred_position
        if selected_position:
            used.add(selected_position)
            step_copy["preferred_position"] = str(selected_position)
        resolved.append(step_copy)

    return resolved


def _shrine_form_context(cities):
    shrine_city = None
    for city in cities or []:
        for building in city.get("buildings") or []:
            if str(building.get("building") or "").strip() != "shrineOfOlympus":
                continue
            shrine_state = city.get("shrine_state") or {}
            shrine_city = {
                "city_id": city.get("id"),
                "city_name": city.get("name", ""),
                "position": int(building.get("position", 0) or 0),
                "level": int(building.get("level", 0) or 0),
                "current_favor": int((shrine_state.get("current_favor")) or city.get("current_favor") or 0),
                "has_current_favor": "current_favor" in shrine_state or "current_favor" in city,
                "shrine_updated_at": str((shrine_state.get("updated_at")) or "").strip(),
                "gods": shrine_state.get("gods") or {},
                "researched_gods": list(shrine_state.get("researched_gods") or []),
                "tradegood_name": city.get("tradegood_name", ""),
                "tradegood_icon": city.get("tradegood_icon", ""),
                "x": city.get("x"),
                "y": city.get("y"),
            }
            break
        if shrine_city:
            break

    return {
        "summary": {
            "has_shrine": bool(shrine_city),
            "shrine_city": shrine_city,
            "city_count": len(cities or []),
            "favor_cost_each": 100,
            "building_icon": static("game/buildings/temple.png"),
            "favor_icon": static("game/gods/favor.png"),
        },
        "gods": [
            {
                **item,
                "icon_url": static(item["icon"]),
                "effect_icon_url": static(item["effect_icon"]),
                "progress": int((((shrine_city or {}).get("gods") or {}).get(item["id"], {}) or {}).get("progress") or 0),
                "progress_visible": bool((((shrine_city or {}).get("gods") or {}).get(item["id"], {}) or {}).get("progress_visible")),
                "researched": item["id"] in set((shrine_city or {}).get("researched_gods") or []),
            }
            for item in SHRINE_GOD_UI
        ],
    }


def _daily_login_form_context(snapshot, cities):
    base_snapshot = {}
    if isinstance(snapshot, AccountSnapshot):
        base_snapshot = snapshot.base_snapshot or {}
    elif isinstance(snapshot, dict):
        base_snapshot = snapshot.get("base_snapshot") or {}

    daily_state = dict(base_snapshot.get("daily_login_state") or {})
    selected_city_id = str(daily_state.get("bonus_city_id") or "").strip()

    city_cards = []
    for city in cities or []:
        city_id = str(city.get("id") or "").strip()
        is_selected = bool(selected_city_id and city_id == selected_city_id) or bool(city.get("daily_login_bonus_city"))
        wine_amount = 0
        population = int(city.get("population", 0) or 0)
        for resource in city.get("resources") or []:
            if str(resource.get("key") or "") == "wine":
                wine_amount = int(resource.get("amount", 0) or 0)
                break
        city_cards.append({
            "id": city.get("id"),
            "name": city.get("name", ""),
            "x": city.get("x"),
            "y": city.get("y"),
            "city_art": city.get("city_art", static("game/buildings/townhall.png")),
            "tradegood_name": city.get("tradegood_name", ""),
            "tradegood_icon": city.get("tradegood_icon", ""),
            "wine_amount": wine_amount,
            "population": population,
            "is_bonus_city": is_selected,
        })

    city_cards.sort(key=lambda item: (not item["is_bonus_city"], str(item["name"]).lower()))

    return {
        "summary": {
            "has_state": bool(daily_state),
            "current_favor": int(base_snapshot.get("current_favor") or daily_state.get("current_favor") or 0),
            "favor_limit": int(daily_state.get("favor_limit") or 2500),
            "tasks_done": int(daily_state.get("tasks_done") or 0),
            "tasks_count": int(daily_state.get("tasks_count") or 0),
            "collectible_tasks_count": int(daily_state.get("collectible_tasks_count") or 0),
            "countdown_seconds": int(daily_state.get("countdown_seconds") or 0),
            "countdown_end_at": str(daily_state.get("countdown_end_at") or ""),
            "bonus_city_id": selected_city_id,
            "bonus_city_name": str(daily_state.get("bonus_city_name") or ""),
            "updated_at": str(daily_state.get("updated_at") or ""),
            "fountain_collected": bool(daily_state.get("fountain_collected")),
            "collected_task_ids": list(daily_state.get("collected_task_ids") or []),
            "favor_icon": static("game/gods/favor.png"),
            "wine_icon": static("game/resources/icon_wine.png"),
            "time_icon": static("game/resources/icon_time.png"),
            "population_icon": static("game/resources/icon_population.png"),
        },
        "tasks": list(daily_state.get("tasks") or []),
        "cities": city_cards,
    }


def _miracle_form_context(cities):
    city_cards = []
    for city in cities or []:
        temple = next((building for building in city.get("buildings") or [] if str(building.get("building") or "") == "temple"), None)
        if not temple:
            continue
        miracle_state = dict(city.get("miracle_state") or {})
        city_cards.append({
            "id": city.get("id"),
            "name": city.get("name", ""),
            "x": city.get("x"),
            "y": city.get("y"),
            "city_art": city.get("city_art", static("game/buildings/temple.png")),
            "tradegood_name": city.get("tradegood_name", ""),
            "tradegood_icon": city.get("tradegood_icon", ""),
            "temple_level": int(temple.get("level", 0) or 0),
            "temple_position": int(temple.get("position", 0) or 0),
            "island_id": city.get("island_id"),
            "miracle_state": miracle_state,
        })
    city_cards.sort(key=lambda item: str(item.get("name") or "").lower())
    return {
        "summary": {
            "count": len(city_cards),
            "temple_icon": static("game/buildings/temple.png"),
            "time_icon": static("game/resources/icon_time.png"),
            "population_icon": static("game/resources/icon_population.png"),
        },
        "cities": city_cards,
    }


def _research_form_context(snapshot, cities):
    base_snapshot = {}
    if isinstance(snapshot, AccountSnapshot):
        base_snapshot = snapshot.base_snapshot or {}
    elif isinstance(snapshot, dict):
        base_snapshot = snapshot.get("base_snapshot") or {}

    research_state = dict(base_snapshot.get("research_state") or {})
    state_by_type = {
        str(item.get("research_type") or "").strip(): item
        for item in (research_state.get("branches") or [])
        if str(item.get("research_type") or "").strip()
    }

    academy_cities = []
    for city in cities or []:
        academy = next((building for building in city.get("buildings") or [] if str(building.get("building") or "") == "academy"), None)
        if not academy:
            continue
        academy_cities.append({
            "id": city.get("id"),
            "name": city.get("name", ""),
            "x": city.get("x"),
            "y": city.get("y"),
            "academy_level": int((academy or {}).get("level", 0) or 0),
            "academy_position": int((academy or {}).get("position", 0) or 0),
            "city_art": city.get("city_art", static("game/buildings/academy.png")),
            "tradegood_name": city.get("tradegood_name", ""),
            "tradegood_icon": city.get("tradegood_icon", ""),
            "is_last_context": str(city.get("id") or "") == str(research_state.get("city_id") or ""),
        })
    academy_cities.sort(key=lambda item: (not item["is_last_context"], str(item.get("name") or "").lower()))
    auto_context = None
    if academy_cities:
        auto_context = sorted(
            academy_cities,
            key=lambda item: (
                0 if item["is_last_context"] else 1,
                -(int(item.get("academy_level") or 0)),
                str(item.get("name") or "").lower(),
            ),
        )[0]

    branches = []
    for item in RESEARCH_BRANCH_UI:
        branch_state = state_by_type.get(item["id"], {})
        branches.append({
            **item,
            "resource_icon_url": static(item["resource_icon"]),
            "default_enabled": True,
            "branch_name": str(branch_state.get("branch_name") or item["name"]),
            "next_name": str(branch_state.get("next_name") or ""),
            "cost_text": str(branch_state.get("cost_text") or ""),
            "eta_seconds": int(branch_state.get("eta_seconds") or 0),
            "eta_end_at": str(branch_state.get("eta_end_at") or ""),
            "ready": bool(branch_state.get("ready")),
            "max_reached": bool(branch_state.get("max_reached")),
            "active": bool(branch_state.get("active")),
            "short_desc": str(branch_state.get("short_desc") or ""),
            "updated_from_snapshot": bool(branch_state),
        })

    return {
        "summary": {
            "count": len(academy_cities),
            "academy_icon": static("game/buildings/academy.png"),
            "time_icon": static("game/resources/icon_time.png"),
            "knowledge_icon": static("game/resources/icon_glass.png"),
            "has_state": bool(research_state),
            "academy_cities": academy_cities,
            "auto_context": auto_context,
            "research_city_name": str(research_state.get("city_name") or base_snapshot.get("research_city_name") or ""),
            "current_type": str(research_state.get("current_research_type") or base_snapshot.get("research_current_type") or ""),
            "current_label": str(research_state.get("current_research_label") or ""),
            "updated_at": str(research_state.get("updated_at") or base_snapshot.get("research_updated_at") or ""),
        },
        "cities": academy_cities,
        "branches": branches,
    }


def _academy_city_cards(cities, snapshot=None, *, crystal_only: bool = False):
    snapshot_base = {}
    if isinstance(snapshot, AccountSnapshot):
        snapshot_base = snapshot.base_snapshot or {}
    elif isinstance(snapshot, dict):
        snapshot_base = snapshot.get("base_snapshot") or {}

    academy_state_by_city = {}
    academy_state_by_name = {}
    academy_cities_snapshot = snapshot_base.get("academy_cities") or []
    if isinstance(academy_cities_snapshot, dict):
        academy_city_items = academy_cities_snapshot.values()
    else:
        academy_city_items = academy_cities_snapshot
    for item in academy_city_items:
        if not isinstance(item, dict):
            continue
        city_id = str(item.get("id") or "").strip()
        if not city_id:
            city_id = str(item.get("city_id") or "").strip()
        state_payload = dict(item.get("academy_state") or {})
        if not state_payload:
            state_payload = {
                "scientists": dict(item.get("scientists") or {}),
                "experiment": dict(item.get("experiment") or {}),
                "resources": dict(item.get("resources") or {}),
                "updated_at": str(item.get("updated_at") or ""),
            }
        if city_id:
            academy_state_by_city[city_id] = state_payload
        city_name = str(item.get("city_name") or item.get("name") or "").strip().lower()
        if city_name:
            academy_state_by_name[city_name] = state_payload

    cards = []
    for city in cities or []:
        academy = next((building for building in city.get("buildings") or [] if str(building.get("building") or "") == "academy"), None)
        if not academy:
            continue
        tradegood_id = int(city.get("tradegood_id") or city.get("produced_tradegood") or city.get("tradegood") or 0)
        if crystal_only and tradegood_id != 3:
            continue
        city_id = str(city.get("id") or "").strip()
        academy_state = dict(
            city.get("academy_state")
            or academy_state_by_city.get(city_id)
            or academy_state_by_name.get(str(city.get("name") or "").strip().lower())
            or {}
        )
        scientists = dict(academy_state.get("scientists") or {})
        experiment = dict(academy_state.get("experiment") or {})
        resources = dict(academy_state.get("resources") or {})
        experiment_points = int(experiment.get("research_points_gain") or 0)
        experiment_cost = int(experiment.get("discounted_crystal_cost") or experiment.get("crystal_cost") or 0)
        crystal_amount = int((resources.get("crystal")) or city.get("crystal") or 0)
        cards.append({
            "id": city.get("id"),
            "name": city.get("name", ""),
            "x": city.get("x"),
            "y": city.get("y"),
            "academy_level": int((academy or {}).get("level", 0) or 0),
            "academy_position": int((academy or {}).get("position", 0) or 0),
            "city_art": city.get("city_art", static("game/buildings/academy.png")),
            "tradegood_id": tradegood_id,
            "tradegood_name": city.get("tradegood_name", ""),
            "tradegood_icon": city.get("tradegood_icon", ""),
            "crystal": crystal_amount,
            "scientists_current": int(scientists.get("current") or 0),
            "scientists_max": int(scientists.get("max") or 0),
            "scientists_production": int(scientists.get("total_production") or 0),
            "experiment_points": experiment_points,
            "experiment_cost": experiment_cost,
            "experiment_cost_full": int(experiment.get("crystal_cost") or 0),
            "experiment_cost_discounted": int(experiment.get("discounted_crystal_cost") or 0),
            "experiment_cost_per_point": round((experiment_cost / experiment_points), 4) if experiment_cost and experiment_points else None,
            "experiment_available": bool(experiment.get("available")),
            "experiment_reason": str(experiment.get("reason") or ""),
            "experiment_short_status": (
                "Disponivel"
                if bool(experiment.get("available"))
                else (str(experiment.get("reason") or "").strip() or "Sem leitura ainda")
            ),
            "academy_updated_at": str(academy_state.get("updated_at") or ""),
        })
    cards.sort(key=lambda item: (str(item.get("name") or "").lower()))
    return cards


def _experiment_form_context(snapshot, cities):
    city_cards = _academy_city_cards(cities, snapshot, crystal_only=True)
    if not city_cards:
        city_cards = _academy_city_cards(cities, snapshot, crystal_only=False)
    return {
        "summary": {
            "count": len(city_cards),
            "academy_icon": static("game/buildings/academy.png"),
            "crystal_icon": static("game/resources/icon_glass.png"),
            "research_icon": static("game/resources/icon_time.png"),
            "has_state": any(card.get("academy_updated_at") for card in city_cards),
        },
        "cities": city_cards,
    }


def _scientists_form_context(snapshot, cities):
    city_cards = _academy_city_cards(cities, snapshot, crystal_only=False)
    return {
        "summary": {
            "count": len(city_cards),
            "academy_icon": static("game/buildings/academy.png"),
            "citizens_icon": static("game/resources/icon_population.png"),
            "crystal_icon": static("game/resources/icon_glass.png"),
        },
        "cities": city_cards,
    }


def _custom_field_names(action_code: int) -> list[str]:
    if int(action_code) == 6:
        return ["city", "collect_favor", "collect_fountain", "fallback_interval_hours", "reschedule_margin_minutes"]
    if int(action_code) == 18:
        return ["branch_seafaring", "branch_economy", "branch_knowledge", "branch_military", "branch_mythology", "fallback_interval_minutes", "ready_margin_minutes"]
    if int(action_code) == 26:
        return ["city", "use_athena_scroll", "pay_with_ambrosia"]
    if int(action_code) == 27:
        return ["cities", "target_mode", "target_value", "reserve_citizens"]
    if int(action_code) == 23:
        return ["cities", "sawmill_percent", "luxury_percent"]
    if int(action_code) == 11:
        return ["city"]
    if int(action_code) == 2:
        return [
            "from_city", "to_city", "transport_load_percent", "confirm_arrival",
            "confirmation_margin_minutes", "wood", "wine", "marble", "crystal", "sulfur",
        ]
    if int(action_code) in {902, 1006}:
        return [
            "donation_type",
            "donation_method",
            "method_value",
            "interval_minutes",
            "random_wait_minutes",
            "target_level",
            "post_production_mode",
            "post_sawmill_percent",
            "post_luxury_percent",
        ]
    if int(action_code) == 5:
        return [*SHRINE_GOD_FIELDS, "favor_recheck_minutes"]
    if int(action_code) == 1007:
        return [*SHRINE_GOD_FIELDS, "favor_recheck_minutes", "cycle_hours"]
    return []


def _job_form_context(form, action_meta, action_code, ga, cities):
    snapshot = None
    try:
        snapshot = AccountSnapshot.objects.get(game_account=ga)
    except AccountSnapshot.DoesNotExist:
        snapshot = None
    return {
        "form": form,
        "action_meta": action_meta,
        "action_code": action_code,
        "game_account": ga,
        "cities": cities,
        "construction_buildings": _construction_building_ui(),
        "custom_field_names": _custom_field_names(action_code),
        "shrine_ui": _shrine_form_context(cities),
        "daily_login_ui": _daily_login_form_context(snapshot, cities),
        "research_ui": _research_form_context(snapshot, cities),
        "miracle_ui": _miracle_form_context(cities),
        "experiment_ui": _experiment_form_context(snapshot, cities),
        "scientists_ui": _scientists_form_context(snapshot, cities),
    }


def _get_active_gas():
    """Return all active, non-blocked GameAccounts grouped by Account."""
    accounts = Account.objects.filter(active=True).prefetch_related(
        "game_accounts"
    ).order_by("label")

    groups = []
    all_gas = []
    for account in accounts:
        gas = list(account.game_accounts.filter(active=True, blocked=False).order_by("server_id"))
        if gas:
            groups.append({"account": account, "game_accounts": gas})
            all_gas.extend(gas)
    return groups, all_gas


def _get_cities(ga):
    """Extract cities list from the game account's snapshot."""
    try:
        snapshot = AccountSnapshot.objects.get(game_account=ga)
        cities_data = snapshot.cities
        if isinstance(cities_data, dict):
            return cities_data.get("cities", [])
        if isinstance(cities_data, list):
            return cities_data
    except AccountSnapshot.DoesNotExist:
        pass
    return []


class JobCreateModalView(LoginRequiredMixin, View):
    """
    Smart entry point for the wizard.

    - No params          → GA picker (step 1)
    - ?action=X          → GA picker for that action (skip if 1 GA)
    - ?ga=Y              → Action picker for that GA (step 1b)
    - ?ga=Y&action=X     → Form directly (step 2)
    """

    def get(self, request):
        ga_id = request.GET.get("ga")
        action_code_str = request.GET.get("action")

        action_code = None
        action_meta = None
        if action_code_str:
            try:
                action_code = int(action_code_str)
                action_meta = ACTION_CATALOG.get(action_code)
            except (ValueError, TypeError):
                pass

        # Case: ga + action → go straight to form (step 2)
        if ga_id and action_code and action_meta:
            try:
                ga = GameAccount.objects.select_related("account").get(pk=ga_id, active=True)
                return self._render_form(request, ga, action_code, action_meta)
            except GameAccount.DoesNotExist:
                pass

        # Case: ga only → action picker (step 1b)
        if ga_id and not action_code:
            try:
                ga = GameAccount.objects.get(pk=ga_id, active=True)
                return self._render_action_picker(request, ga)
            except GameAccount.DoesNotExist:
                pass

        # Case: action only → GA picker (auto-skip if 1 GA)
        account_groups, all_gas = _get_active_gas()

        if action_code and action_meta and len(all_gas) == 1:
            # Only 1 GA → skip to form
            return self._render_form(request, all_gas[0], action_code, action_meta)

        # Default: GA picker
        return self._render_ga_picker(request, account_groups, action_code, action_meta)

    def _render_ga_picker(self, request, account_groups, action_code=None, action_meta=None):
        html = render_to_string(
            "jobs/partials/create_step_ga.html",
            {
                "account_groups": account_groups,
                "action_code": action_code,
                "action_meta": action_meta,
            },
            request=request,
        )
        return HttpResponse(html)

    def _render_action_picker(self, request, ga):
        html = render_to_string(
            "jobs/partials/create_step_action.html",
            {
                "action_groups": get_actions_for_ui(),
                "game_account": ga,
            },
            request=request,
        )
        return HttpResponse(html)

    def _render_form(self, request, ga, action_code, action_meta):
        cities = _get_cities(ga)
        construction_cities = _construction_city_data(cities)
        selected_city = request.GET.get("city", "")
        form = JobCreateForm(
            action_code=action_code,
            game_account=ga,
            cities=construction_cities,
            initial={"game_account": str(ga.pk), "action_code": action_code, "city": selected_city},
        )
        html = render_to_string(
            "jobs/partials/create_step_form.html",
            _job_form_context(form, action_meta, action_code, ga, construction_cities),
            request=request,
        )
        return HttpResponse(html)


class JobActionPickerView(LoginRequiredMixin, View):
    """GET: returns action picker for a given game account."""

    def get(self, request):
        ga_id = request.GET.get("ga")
        if not ga_id:
            return HttpResponse("")

        try:
            ga = GameAccount.objects.get(pk=ga_id, active=True)
        except GameAccount.DoesNotExist:
            return HttpResponse("")

        html = render_to_string(
            "jobs/partials/create_step_action.html",
            {"action_groups": get_actions_for_ui(), "game_account": ga},
            request=request,
        )
        return HttpResponse(html)


class JobFormView(LoginRequiredMixin, View):
    """GET: returns the dynamic form for a specific action + game account."""

    def get(self, request):
        ga_id = request.GET.get("ga")
        action_code_str = request.GET.get("action")

        if not ga_id or not action_code_str:
            return HttpResponse("")

        try:
            action_code = int(action_code_str)
            ga = GameAccount.objects.select_related("account").get(pk=ga_id)
        except (ValueError, GameAccount.DoesNotExist):
            return HttpResponse("")

        action_meta = ACTION_CATALOG.get(action_code)
        if not action_meta:
            return HttpResponse("")

        cities = _get_cities(ga)
        construction_cities = _construction_city_data(cities)
        selected_city = request.GET.get("city", "")
        form = JobCreateForm(
            action_code=action_code,
            game_account=ga,
            cities=construction_cities,
            initial={"game_account": str(ga.pk), "action_code": action_code, "city": selected_city},
        )

        html = render_to_string(
            "jobs/partials/create_step_form.html",
            _job_form_context(form, action_meta, action_code, ga, construction_cities),
            request=request,
        )
        return HttpResponse(html)


class ConstructionPlanPreviewView(LoginRequiredMixin, View):
    """POST: returns a live construction plan preview for the modal."""

    def post(self, request):
        ga_id = request.POST.get("game_account")
        action_code_str = request.POST.get("action_code")
        if not ga_id or not action_code_str:
            return HttpResponse("")

        try:
            ga = GameAccount.objects.select_related("account").get(pk=ga_id, active=True)
            action_code = int(action_code_str)
        except (GameAccount.DoesNotExist, ValueError, TypeError):
            return HttpResponse("")

        action_meta = ACTION_CATALOG.get(action_code) or {}
        if action_meta.get("category") != "construction":
            return HttpResponse("")

        plan_raw = request.POST.get("construction_plan_json", "")
        city_id = request.POST.get("city")
        target_level = request.POST.get("target_level")
        default_research, default_time = _default_construction_modifiers(action_code)
        research_reduction = request.POST.get("research_reduction", str(default_research))
        build_time_reduction = request.POST.get("build_time_reduction", str(default_time))
        building_position = request.POST.get("building_position")
        building_type = request.POST.get("building_type")

        preview = None
        plan_preview = None
        try:
            if action_code == 1002 and plan_raw:
                steps = json.loads(plan_raw)
                if isinstance(steps, list) and steps:
                    plan_preview = build_construction_plan_preview(
                        game_account=ga,
                        steps=steps,
                        research_reduction=int(str(research_reduction or str(default_research)).replace("%", "").replace(":", "") or 0),
                        build_time_reduction=str(build_time_reduction or str(default_time)),
                    )
            elif city_id and target_level and (building_position or building_type):
                preview = build_construction_preview(
                    game_account=ga,
                    city_id=city_id,
                    building_position=building_position,
                    building_type=building_type,
                    target_level=int(target_level),
                    research_reduction=int(str(research_reduction or str(default_research)).replace("%", "").replace(":", "") or 0),
                    build_time_reduction=str(build_time_reduction or str(default_time)),
                )
        except Exception as exc:
            logger.warning("Construction preview failed: %s", exc)

        html = render_to_string(
            "jobs/partials/construction_plan_preview.html",
            {
                "preview": preview,
                "plan_preview": plan_preview,
                "ika_tools_url": IKA_TOOLS_URL,
                "resource_labels": RESOURCE_LABELS,
                "resource_icons": RESOURCE_ICON_MAP,
                "action_code": action_code,
            },
            request=request,
        )
        return HttpResponse(html)


class JobSubmitView(LoginRequiredMixin, View):
    """POST: creates job(s) from the dynamic form submission."""

    def post(self, request):
        ga_id = request.POST.get("game_account")
        action_code_str = request.POST.get("action_code")

        if not ga_id or not action_code_str:
            return self._error("Dados incompletos.")

        try:
            action_code = int(action_code_str)
            ga = GameAccount.objects.select_related("account").get(pk=ga_id)
        except (ValueError, GameAccount.DoesNotExist):
            return self._error("Conta ou acao invalida.")

        action_meta = ACTION_CATALOG.get(action_code)
        if not action_meta:
            return self._error("Acao nao encontrada.")

        cities = _get_cities(ga)
        construction_cities = _construction_city_data(cities)

        if int(action_code) == 1002:
            return self._submit_construction_plan(request, ga, action_code, action_meta, construction_cities)

        form = JobCreateForm(
            request.POST,
            action_code=action_code,
            game_account=ga,
            cities=construction_cities,
        )

        if not form.is_valid():
            html = render_to_string(
                "jobs/partials/create_step_form.html",
                _job_form_context(form, action_meta, action_code, ga, construction_cities),
                request=request,
            )
            return HttpResponse(html)

        inputs = form.get_inputs_json()
        if int(action_code) == 18 and not any(bool(inputs.get(key)) for key in ("branch_seafaring", "branch_economy", "branch_knowledge", "branch_military", "branch_mythology")):
            return self._error("Selecione pelo menos um ramo de pesquisa.")
        jobs_created = self._create_jobs(ga, action_code, action_meta, inputs, construction_cities)

        trigger_data = json.dumps({
            "toast": {
                "type": "success",
                "message": f"{jobs_created} job(s) criado(s)!",
            },
            "jobsCreated": True,
        })

        resp = HttpResponse(
            render_to_string(
                "jobs/partials/create_step_success.html",
                {"jobs_created": jobs_created, "action_name": action_meta["name"]},
                request=request,
            )
        )
        resp["HX-Trigger"] = trigger_data
        return resp

    def _submit_construction_plan(self, request, ga, action_code, action_meta, cities):
        form = JobCreateForm(
            request.POST,
            action_code=action_code,
            game_account=ga,
            cities=cities,
        )
        if not form.is_valid():
            html = render_to_string(
                "jobs/partials/create_step_form.html",
                _job_form_context(form, action_meta, action_code, ga, cities),
                request=request,
            )
            return HttpResponse(html)

        try:
            steps = json.loads(request.POST.get("construction_plan_json", "[]"))
        except Exception:
            steps = []
        if not isinstance(steps, list) or not steps:
            return self._error("Selecione pelo menos uma cidade, um predio e o nivel alvo do plano.")

        clean_steps = []
        city_lookup = {str(city.get("id")): city.get("name", "") for city in cities if city.get("id") is not None}
        for idx, step in enumerate(steps, start=1):
            city_id = str(step.get("city_id") or "").strip()
            building_id = str(step.get("building_id") or step.get("building_type") or "").strip()
            target_level = int(step.get("target_level") or 0)
            if not city_id or not building_id or target_level <= 0:
                return self._error(f"Etapa {idx} do plano esta incompleta.")
            clean_steps.append({
                "city_id": city_id,
                "city_name": city_lookup.get(city_id, str(step.get("city_name") or city_id)),
                "building_id": building_id,
                "building_type": building_id,
                "building_name": str(step.get("building_name") or building_id),
                "mode": str(step.get("mode") or "upgrade"),
                "slot_types": list(step.get("slot_types") or []),
                "preferred_position": str(step.get("preferred_position") or ""),
                "target_level": target_level,
            })

        clean_steps = _resolve_construction_new_slots(clean_steps, cities)

        inputs = form.get_inputs_json()
        plan_preview = build_construction_plan_preview(
            game_account=ga,
            steps=clean_steps,
            research_reduction=_default_construction_modifiers(action_code)[0],
            build_time_reduction=_default_construction_modifiers(action_code)[1],
        )
        inputs["construction_plan_json"] = clean_steps
        inputs["construction_plan_steps"] = plan_preview.steps
        inputs["construction_summary"] = {
            "steps": len(clean_steps),
            "totals": plan_preview.totals,
            "reserved_local": plan_preview.reserved_local,
            "missing": plan_preview.missing,
            "base_seconds": plan_preview.base_seconds,
            "adjusted_seconds": plan_preview.adjusted_seconds,
        }
        job = Job.objects.create(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=action_code,
            inputs_json=json.dumps(inputs),
            status="queued",
        )
        self._create_construction_reservations(job, plan_preview)

        trigger_data = json.dumps({
            "toast": {
                "type": "success",
                "message": "Plano de construcao criado com reservas de recursos.",
            },
            "jobsCreated": True,
        })
        resp = HttpResponse(
            render_to_string(
                "jobs/partials/create_step_success.html",
                {"jobs_created": 1, "action_name": action_meta["name"]},
                request=request,
            )
        )
        resp["HX-Trigger"] = trigger_data
        return resp

    @staticmethod
    def _create_construction_reservations(job, plan_preview):
        reservations = []
        for step in plan_preview.steps:
            for resource in ("wood", "wine", "marble", "glas", "sulfur"):
                reserved_local = int(step["reserved_local"].get(resource, 0))
                shortfall = int(step["missing"].get(resource, 0))
                if reserved_local <= 0 and shortfall <= 0:
                    continue
                reservations.append(
                    ConstructionResourceReservation(
                        job=job,
                        account=job.account,
                        game_account=job.game_account,
                        city_id=str(step["city_id"]),
                        city_name=str(step["city_name"]),
                        resource=resource,
                        reserved_local_amount=reserved_local,
                        shortfall_amount=shortfall,
                    )
                )
        if reservations:
            ConstructionResourceReservation.objects.bulk_create(reservations)

    def _create_jobs(self, ga, action_code, action_meta, inputs, cities):
        city_choices = {
            str(city.get("id")): city.get("name", "")
            for city in (cities or [])
            if city.get("id") is not None
        }
        multi_city_key = None
        for field_def in action_meta.get("inputs", []):
            if field_def["type"] == "city_select" and field_def.get("multiple"):
                multi_city_key = field_def["key"]
                break

        donation_types = inputs.get("donation_type")
        if donation_types and not isinstance(donation_types, list):
            donation_types = [donation_types]

        if multi_city_key and multi_city_key in inputs:
            if int(action_code) == 27:
                return self._create_single_job(ga, action_code, dict(inputs))
            selected_city_ids = inputs.pop(multi_city_key)
            if not isinstance(selected_city_ids, list):
                selected_city_ids = [selected_city_ids]

            count = 0
            for city_id in selected_city_ids:
                count += self._create_jobs_for_city(
                    ga=ga,
                    action_code=action_code,
                    inputs=inputs,
                    city_id=city_id,
                    city_name=self._get_city_name(city_id, cities),
                    donation_types=donation_types,
                )
            return count
        else:
            single_inputs = dict(inputs)
            if int(action_code) in {2, 6, 11} and city_choices:
                single_inputs["_city_choices"] = city_choices
            return self._create_single_job(ga, action_code, single_inputs)

    def _create_jobs_for_city(self, ga, action_code, inputs, city_id, city_name="", donation_types=None):
        """Create one or more jobs for a selected city."""
        base_inputs = {**inputs, "city_id": city_id}
        if city_name:
            base_inputs["city_name"] = city_name

        if donation_types:
            count = 0
            for donation_type in donation_types:
                job_inputs = {**base_inputs, "donation_type": donation_type}
                count += self._create_single_job(ga, action_code, job_inputs)
            return count

        return self._create_single_job(ga, action_code, base_inputs)

    @staticmethod
    def _create_single_job(ga, action_code, inputs):
        enriched_inputs = dict(inputs)
        if int(action_code) == 2:
            city_map = {}
            # When these fields are present they were selected from the available city list.
            if isinstance(inputs.get("_city_choices"), dict):
                city_map = inputs["_city_choices"]
            from_city = str(enriched_inputs.get("from_city") or "").strip()
            to_city = str(enriched_inputs.get("to_city") or "").strip()
            if from_city and city_map.get(from_city):
                enriched_inputs["from_city_name"] = city_map[from_city]
            if to_city and city_map.get(to_city):
                enriched_inputs["to_city_name"] = city_map[to_city]
            enriched_inputs.pop("_city_choices", None)
        elif int(action_code) in {6, 11}:
            city_map = inputs.get("_city_choices") if isinstance(inputs.get("_city_choices"), dict) else {}
            city_id = str(enriched_inputs.get("city") or "").strip()
            if city_id and city_map.get(city_id):
                enriched_inputs["city_name"] = city_map[city_id]
            enriched_inputs.pop("_city_choices", None)
        Job.objects.create(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=action_code,
            inputs_json=json.dumps(enriched_inputs),
            status="queued",
        )
        return 1

    @staticmethod
    def _get_city_name(city_id, cities):
        for city in cities:
            if str(city.get("id")) == str(city_id):
                return city.get("name", "")
        return ""

    @staticmethod
    def _error(message):
        trigger = json.dumps({"toast": {"type": "error", "message": message}})
        resp = HttpResponse(
            f'<div class="p-5 text-center"><p class="text-sm text-[var(--ik-bad)]">{message}</p></div>'
        )
        resp["HX-Trigger"] = trigger
        return resp
