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
import re
from datetime import datetime
from pathlib import Path

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views import View
from django.templatetags.static import static

from apps.accounts.models import Account, GameAccount
from apps.game.models import AccountSnapshot
from apps.worldintel.models import WorldDump, WorldDumpCity, WorldDumpIsland
from core.catalogs import BUILDING_CATALOG
from core.contracts import ACTION_CATALOG, get_actions_for_ui
from ..forms import JobCreateForm
from ..models import ConstructionResourceReservation, Job
from ..services.workflows import create_job_with_workflow
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
WORKSHOP_UNITS_UI = (
    # Tropas (12)
    {"key": "hoplita",      "game_id": 303, "name": "Hoplita",           "tab": "land", "icon": "game/units/hoplita.png",           "bi": "bi-shield-fill",        "accent": "rgba(79,134,185,0.85)",  "surface": "rgba(79,134,185,0.08)"},
    {"key": "gigante",      "game_id": 308, "name": "Gigante a Vapor",   "tab": "land", "icon": "game/units/gigante.png",           "bi": "bi-robot",              "accent": "rgba(50,145,210,0.85)",  "surface": "rgba(50,145,210,0.08)"},
    {"key": "lanceiro",     "game_id": 315, "name": "Lanceiro",          "tab": "land", "icon": "game/units/lanceiro.png",          "bi": "bi-person-fill",        "accent": "rgba(80,100,160,0.85)",  "surface": "rgba(80,100,160,0.08)"},
    {"key": "espadachim",   "game_id": 302, "name": "Espadachim",        "tab": "land", "icon": "game/units/espadachim.png",        "bi": "bi-person-fill",        "accent": "rgba(130,80,50,0.85)",   "surface": "rgba(130,80,50,0.08)"},
    {"key": "fundeiro",     "game_id": 301, "name": "Fundeiro",          "tab": "land", "icon": "game/units/fundeiro.png",          "bi": "bi-person-fill",        "accent": "rgba(95,130,60,0.85)",   "surface": "rgba(95,130,60,0.08)"},
    {"key": "arqueiro",     "game_id": 313, "name": "Arqueiro",          "tab": "land", "icon": "game/units/arqueiro.png",          "bi": "bi-person-fill",        "accent": "rgba(85,135,90,0.85)",   "surface": "rgba(85,135,90,0.08)"},
    {"key": "atirador",     "game_id": 304, "name": "Carabineiro",       "tab": "land", "icon": "game/units/atirador.png",          "bi": "bi-person-fill",        "accent": "rgba(90,140,80,0.85)",   "surface": "rgba(90,140,80,0.08)"},
    {"key": "ariete",       "game_id": 307, "name": "Ariete",            "tab": "land", "icon": "game/units/ariete.png",            "bi": "bi-arrow-right-square", "accent": "rgba(100,65,40,0.85)",   "surface": "rgba(100,65,40,0.08)"},
    {"key": "catapulta",    "game_id": 306, "name": "Catapulta",         "tab": "land", "icon": "game/units/catapulta.png",         "bi": "bi-bullseye",           "accent": "rgba(140,105,60,0.85)",  "surface": "rgba(140,105,60,0.08)"},
    {"key": "morteiro",     "game_id": 305, "name": "Morteiro",          "tab": "land", "icon": "game/units/morteiro.png",          "bi": "bi-bullseye",           "accent": "rgba(90,80,80,0.85)",    "surface": "rgba(90,80,80,0.08)"},
    {"key": "girocoptero",  "game_id": 312, "name": "Girocóptero",       "tab": "land", "icon": "game/units/girocoptero.png",       "bi": "bi-wind",               "accent": "rgba(150,120,70,0.85)",  "surface": "rgba(150,120,70,0.08)"},
    {"key": "balao",        "game_id": 309, "name": "Balão-Bombardeiro", "tab": "land", "icon": "game/units/balao.png",             "bi": "bi-balloon-fill",       "accent": "rgba(215,175,50,0.85)",  "surface": "rgba(215,175,50,0.08)"},
    # Barcos (12)
    {"key": "lancachamas",    "game_id": 211, "name": "Lança-Chamas",    "tab": "sea", "icon": "game/units/lancachamas.png",      "bi": "bi-fire",          "accent": "rgba(220,75,35,0.85)",   "surface": "rgba(220,75,35,0.08)"},
    {"key": "ariete_vapor",   "game_id": 216, "name": "Aríete a Vapor",  "tab": "sea", "icon": "game/units/Aríete a Vapor.png",   "bi": "bi-tsunami",       "accent": "rgba(60,100,185,0.85)",  "surface": "rgba(60,100,185,0.08)"},
    {"key": "trieme",         "game_id": 210, "name": "Trireme",         "tab": "sea", "icon": "game/units/trieme.png",           "bi": "bi-tsunami",       "accent": "rgba(50,130,205,0.85)",  "surface": "rgba(50,130,205,0.08)"},
    {"key": "barco_balista",  "game_id": 213, "name": "Barco Balista",   "tab": "sea", "icon": "game/units/Barco Balista.png",   "bi": "bi-tsunami",       "accent": "rgba(75,130,175,0.85)",  "surface": "rgba(75,130,175,0.08)"},
    {"key": "barcocatapulta", "game_id": 214, "name": "Barco Catapulta", "tab": "sea", "icon": "game/units/barcocatapulta.png",  "bi": "bi-tsunami",       "accent": "rgba(95,140,80,0.85)",   "surface": "rgba(95,140,80,0.08)"},
    {"key": "barcomorteiro",  "game_id": 215, "name": "Barco Morteiro",  "tab": "sea", "icon": "game/units/barcomorteiro.png",   "bi": "bi-tsunami",       "accent": "rgba(90,65,55,0.85)",    "surface": "rgba(90,65,55,0.08)"},
    {"key": "lanca_foguetes", "game_id": 217, "name": "Lança-Foguetes",  "tab": "sea", "icon": "game/units/Lança-Foguetes.png",  "bi": "bi-fire",          "accent": "rgba(205,75,35,0.85)",   "surface": "rgba(205,75,35,0.08)"},
    {"key": "submergivel",    "game_id": 212, "name": "Submergível",     "tab": "sea", "icon": "game/units/Submergível.png",     "bi": "bi-water",         "accent": "rgba(25,80,145,0.85)",   "surface": "rgba(25,80,145,0.08)"},
    {"key": "lancha_rapida",  "game_id": 218, "name": "Lancha Rápida",   "tab": "sea", "icon": "game/units/Lancha Rápida.png",   "bi": "bi-tsunami",       "accent": "rgba(45,165,120,0.85)",  "surface": "rgba(45,165,120,0.08)"},
    {"key": "porta_balaos",   "game_id": 219, "name": "Porta-balões",    "tab": "sea", "icon": "game/units/Porta-balões.png",    "bi": "bi-box-seam",      "accent": "rgba(155,200,70,0.85)",  "surface": "rgba(155,200,70,0.08)"},
    {"key": "barco_mercante", "game_id": 201, "name": "Barco Mercante",  "tab": "sea", "icon": "game/units/barco_mercante.png", "bi": "bi-shop",          "accent": "rgba(180,140,60,0.85)",  "surface": "rgba(180,140,60,0.08)"},
    {"key": "cargueiro",      "game_id": 204, "name": "Cargueiro",       "tab": "sea", "icon": "game/units/cargueiro.png",      "bi": "bi-box-seam-fill", "accent": "rgba(110,90,70,0.85)",   "surface": "rgba(110,90,70,0.08)"},
)

_UNIT_STATS_DB: dict[int, dict] = {}
try:
    _unit_stats_path = Path(__file__).resolve().parents[3] / "core" / "data" / "unit_stats.json"
    _UNIT_STATS_DB = {int(k): v for k, v in json.loads(_unit_stats_path.read_text(encoding="utf-8")).items()}
except Exception:
    pass


def _build_job_form_initial(request, ga, action_code: int) -> dict:
    selected_city = request.GET.get("city_id", request.GET.get("city", ""))
    initial = {
        "game_account": str(ga.pk),
        "action_code": action_code,
        "city_id": selected_city,
        "city": selected_city,
        "revolt_type": request.GET.get("revolt_type", ""),
    }
    for key in request.GET.keys():
        if not key.startswith("input_"):
            continue
        clean_key = key[6:]
        values = [str(v).strip() for v in request.GET.getlist(key) if str(v).strip()]
        if not values:
            continue
        initial[clean_key] = values if len(values) > 1 else values[0]
    return initial


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)

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
        return 14, "0"
    return 14, "25"


def _parse_modifier_int(value, default: int = 0) -> int:
    raw = str(value or "").replace("%", "").replace(":", "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _resolve_construction_modifiers(action_code: int, ga: GameAccount, source: dict | None = None) -> tuple[int, str, int]:
    default_research, default_time = _default_construction_modifiers(action_code)
    research_reduction = _parse_modifier_int((source or {}).get("research_reduction"), default_research)
    if int(action_code) == 1002:
        build_time_reduction = str(ga.build_time_reduction or 0)
    else:
        build_time_reduction = str((source or {}).get("build_time_reduction") or default_time)
    government_time_reduction = int(ga.government_time_reduction or 0)
    return research_reduction, build_time_reduction, government_time_reduction


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
            "island_id": city.get("island_id"),
            "city_occupied": bool(city.get("city_occupied")),
            "harbour_occupied": bool(city.get("harbour_occupied")),
            "occupier_name": city.get("occupier_name", ""),
            "port_controller_name": city.get("port_controller_name", ""),
            "occupation_forces": city.get("occupation_forces") or [],
            "blockade_forces": city.get("blockade_forces") or [],
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


def _find_branch_office_pos(city: dict | None) -> int | None:
    if not isinstance(city, dict):
        return None
    for item in city.get("buildings") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("building") or "").strip() != "branchOffice":
            continue
        try:
            return int(item.get("position") or 0)
        except Exception:
            return None
    return None


def _training_form_context(snapshot, cities):
    """Build context for the train units (ac=1005) and station (ac=1202) form sections."""
    from core.catalogs import TRAINING_UNITS, UNIT_ICON_MAP, get_unit_info

    unit_stats_db = _UNIT_STATS_DB

    # Improvement levels from snapshot (set by runner 1203)
    unit_improvements: dict[str, dict] = {}
    if snapshot:
        base = snapshot.base_snapshot or {}
        unit_improvements = base.get("unit_improvements") or {}

    # Current troop/fleet counts from snapshot military data
    military = {}
    if snapshot:
        mil = snapshot.military or {}
        for city_mil in mil.get("by_city", []):
            if not isinstance(city_mil, dict):
                continue
            cid = str(city_mil.get("city_id") or "")
            if cid:
                military[cid] = {
                    "troops": dict(city_mil.get("troops") or {}),
                    "fleet": dict(city_mil.get("fleet") or {}),
                }

    # Find barracks and shipyard positions per city
    city_buildings: dict[str, dict] = {}
    for city in (cities or []):
        cid = str(city.get("id") or "")
        barracks_pos = None
        shipyard_pos = None
        for b in (city.get("buildings") or []):
            if isinstance(b, dict):
                bname = str(b.get("building") or "")
                pos = b.get("position")
                if bname == "barracks" and barracks_pos is None:
                    barracks_pos = pos
                elif bname == "shipyard" and shipyard_pos is None:
                    shipyard_pos = pos
        city_buildings[cid] = {
            "barracks_pos": barracks_pos,
            "shipyard_pos": shipyard_pos,
        }

    def enrich(units):
        result = []
        for u in units:
            icon = (UNIT_ICON_MAP.get(u.get("name") or "")
                    or UNIT_ICON_MAP.get(u.get("css") or "")
                    or "")
            uid = u.get("id")
            us = unit_stats_db.get(uid, {})
            imp = unit_improvements.get(str(uid), {})
            off_lv = int(imp.get("offensive") or 0)
            def_lv = int(imp.get("defensive") or 0)
            imp_data = us.get("improvements", {})
            off_bonus = int((imp_data.get("offensive") or {}).get("bonus_per_level") or 0)
            def_bonus = int((imp_data.get("defensive") or {}).get("bonus_per_level") or 0)
            weapons = us.get("weapons") or []
            base_dmg = weapons[0].get("damage", 0) if weapons else 0
            precision = weapons[0].get("precision", 0) if weapons else 0
            damage = base_dmg + off_lv * off_bonus
            armor = int(us.get("armor") or 0) + def_lv * def_bonus
            stats: dict = {}
            if us.get("hp"):
                stats = {
                    "hp": us.get("hp", 0),
                    "damage": damage,
                    "precision": round(precision),
                    "armor": armor,
                    "speed": us.get("speed", 0),
                    "off_lv": off_lv,
                    "def_lv": def_lv,
                }
                if us.get("capacity"):
                    stats["capacity"] = us["capacity"]
            result.append({**u, "icon": icon, "stats": stats})
        return result

    # Enrich cities for JSON data in template (used by Alpine.js)
    training_cities = []
    for city in (cities or []):
        cid = str(city.get("id") or "")
        cb = city_buildings.get(cid, {})
        city_mil = military.get(cid, {})
        training_cities.append({
            "id": city.get("id"),
            "name": city.get("name", ""),
            "x": city.get("x", 0),
            "y": city.get("y", 0),
            "tradegood_name": city.get("resource_name") or city.get("tradegood_name") or "",
            "tradegood_icon": city.get("tradegood_icon") or "",
            "city_art": city.get("city_art") or "",
            "barracks_pos": cb.get("barracks_pos"),
            "shipyard_pos": cb.get("shipyard_pos"),
            "has_barracks": cb.get("barracks_pos") is not None,
            "has_shipyard": cb.get("shipyard_pos") is not None,
            "troops": city_mil.get("troops") or {},
            "fleet": city_mil.get("fleet") or {},
        })

    return {
        "troops_units": enrich(TRAINING_UNITS["troops"]),
        "fleet_units": enrich(TRAINING_UNITS["fleet"]),
        "military": military,
        "training_cities": training_cities,
    }


def _black_market_form_context(cities, snapshot):
    from core.catalogs import TRAINING_UNITS, UNIT_ICON_MAP
    from apps.market.models import BlackMarketOffer, BlackMarketUnitQuote
    from django.db.models import Avg

    bm_cities = []
    for city in (cities or []):
        bm_pos = None
        for b in (city.get("buildings") or []):
            if isinstance(b, dict) and str(b.get("building") or "").strip() == "blackMarket":
                try:
                    bm_pos = int(b.get("position") or 0)
                except Exception:
                    pass
                break
        if bm_pos is None:
            continue
        bm_cities.append({
            "id": city.get("id"),
            "name": city.get("name", ""),
            "x": city.get("x"),
            "y": city.get("y"),
            "tradegood_name": city.get("tradegood_name", "") or city.get("resource_name", ""),
            "tradegood_icon": city.get("tradegood_icon", ""),
            "city_art": city.get("city_art", ""),
            "black_market_pos": bm_pos,
        })

    military_by_city = {}
    if snapshot:
        mil = snapshot.military or {}
        for city_mil in mil.get("by_city", []):
            if not isinstance(city_mil, dict):
                continue
            cid = str(city_mil.get("city_id") or "")
            if cid:
                military_by_city[cid] = {
                    "troops": dict(city_mil.get("troops") or {}),
                    "fleet": dict(city_mil.get("fleet") or {}),
                }

    # Active BM offers for this GA — {city_id: {unit_id: amount}}
    active_offers: dict[str, dict[int, int]] = {}
    if snapshot:
        try:
            qs = BlackMarketOffer.objects.filter(
                game_account=snapshot.game_account, status="active"
            ).values("city_id", "unit_id", "amount")
            for row in qs:
                ckey = str(row["city_id"])
                if ckey not in active_offers:
                    active_offers[ckey] = {}
                active_offers[ckey][row["unit_id"]] = row["amount"]
        except Exception:
            pass

    # Price averages per unit_id (across all history)
    price_avgs: dict[int, int] = {}
    try:
        for row in BlackMarketOffer.objects.values("unit_id").annotate(avg=Avg("unit_price")):
            if row["avg"] is not None:
                price_avgs[row["unit_id"]] = int(row["avg"])
    except Exception:
        pass

    # Price limits per (city_id, unit_id) from most recent quote — {city_id: {unit_id: {min, max}}}
    price_limits: dict[str, dict[str, dict]] = {}
    try:
        bm_city_ids = [str(c["id"]) for c in bm_cities if c.get("id")]
        if snapshot and bm_city_ids:
            for q in (BlackMarketUnitQuote.objects
                      .filter(game_account=snapshot.game_account, city_id__in=bm_city_ids)
                      .order_by("city_id", "unit_id", "-captured_at")
                      .values("city_id", "unit_id", "price_min", "price_max")):
                ckey = str(q["city_id"])
                ukey = str(q["unit_id"])
                if ckey not in price_limits:
                    price_limits[ckey] = {}
                if ukey not in price_limits[ckey]:
                    price_limits[ckey][ukey] = {
                        "min": q["price_min"],
                        "max": q["price_max"],
                    }
    except Exception:
        pass

    for city in bm_cities:
        cid = str(city["id"] or "")
        city_mil = military_by_city.get(cid, {})
        city["troops"] = city_mil.get("troops") or {}
        city["fleet"] = city_mil.get("fleet") or {}
        city["active_offers"] = active_offers.get(cid, {})

    def enrich(units):
        result = []
        for u in units:
            icon = (UNIT_ICON_MAP.get(u.get("name") or "")
                    or UNIT_ICON_MAP.get(u.get("css") or "")
                    or "")
            avg = price_avgs.get(u["id"])
            result.append({**u, "icon": icon, "avg_price": avg})
        return result

    return {
        "cities": bm_cities,
        "troops_units": enrich(TRAINING_UNITS["troops"]),
        "fleet_units": enrich(TRAINING_UNITS["fleet"]),
        "price_limits": price_limits,
    }


def _resolve_unit_icon(unit_id: int, unit_name: str) -> str:
    from core.catalogs import UNIT_ICON_MAP
    icon_path = (UNIT_ICON_MAP.get(unit_name)
                 or UNIT_ICON_MAP.get(f"s{unit_id}")
                 or "")
    return static(icon_path) if icon_path else ""


def _bm_available_offers_context(cities, snapshot):
    """Build seller-specific available offers grouped by buyer_city_id for ac=804 form."""
    from apps.market.models import BlackMarketAvailableOffer
    from apps.market.models import BlackMarketUnitQuote

    if not snapshot:
        return {"offers_by_city": {}, "scanned_at_by_city": {}}

    bo_city_ids = []
    for city in (cities or []):
        for b in (city.get("buildings") or []):
            if isinstance(b, dict) and str(b.get("building") or "").strip() == "branchOffice":
                bo_city_ids.append(str(city.get("id") or ""))
                break

    if not bo_city_ids:
        return {"offers_by_city": {}, "scanned_at_by_city": {}}

    # unit_name lookup from most-recent quotes (sell side has names)
    unit_names: dict[int, str] = {}
    try:
        for q in (BlackMarketUnitQuote.objects
                  .filter(game_account=snapshot.game_account)
                  .order_by("unit_id", "-captured_at")
                  .values("unit_id", "unit_name")):
            uid = q["unit_id"]
            if uid not in unit_names and q["unit_name"]:
                unit_names[uid] = q["unit_name"]
    except Exception:
        pass

    offers_by_city: dict[str, list[dict]] = {}
    scanned_at_by_city: dict[str, str] = {}
    try:
        qs = (BlackMarketAvailableOffer.objects
              .filter(game_account=snapshot.game_account, buyer_city_id__in=bo_city_ids)
              .order_by("unit_id", "price_per_unit", "seller_avatar", "seller_city_name")
              .values("buyer_city_id", "seller_city_id", "seller_city_name", "seller_avatar",
                      "unit_id", "unit_category", "amount", "price_per_unit", "scanned_at"))
        for row in qs:
            ckey = str(row["buyer_city_id"])
            uid = row["unit_id"]
            uname = unit_names.get(uid, "")
            if ckey not in offers_by_city:
                offers_by_city[ckey] = []
            offers_by_city[ckey].append({
                "unit_id": uid,
                "unit_name": uname,
                "unit_icon": _resolve_unit_icon(uid, uname),
                "unit_category": row["unit_category"],
                "seller_city_id": row["seller_city_id"],
                "seller_city_name": row["seller_city_name"],
                "seller_avatar": row["seller_avatar"],
                "amount": row["amount"],
                "price_per_unit": row["price_per_unit"],
            })
            if row["scanned_at"] and ckey not in scanned_at_by_city:
                scanned_at_by_city[ckey] = row["scanned_at"].strftime("%d/%m/%Y %H:%M")
    except Exception:
        pass
    return {"offers_by_city": offers_by_city, "scanned_at_by_city": scanned_at_by_city}


def _market_form_context(cities):
    market_cities = []
    for city in cities or []:
        branch_office_pos = _find_branch_office_pos(city)
        if branch_office_pos is None:
            continue
        market_cities.append(
            {
                "id": city.get("id"),
                "name": city.get("name", ""),
                "x": city.get("x"),
                "y": city.get("y"),
                "tradegood_name": city.get("tradegood_name", ""),
                "tradegood_icon": city.get("tradegood_icon", ""),
                "city_art": city.get("city_art", static("game/buildings/branchOffice.png")),
                "branch_office_pos": branch_office_pos,
                "resources": city.get("resources") or [],
            }
        )
    return {"cities": market_cities}


# Intelligence missions only (mission 1 = infiltration is always automatic)
_PLAYER_SCOPE_MISSION_IDS = frozenset({3, 7, 10, 21, 24, 25, 26, 27})

SPY_MISSIONS_UI = [
    {"id": 3,  "icon": "bi-book",           "name": "Nível de pesquisa",  "desc": "Nível de pesquisa da academia",         "risk_before": 35, "risk_after": 10, "risk_per_spy": 4, "success": 50},
    {"id": 5,  "icon": "bi-boxes",          "name": "Armazém",            "desc": "Recursos armazenados na cidade",        "risk_before": 60, "risk_after": 15, "risk_per_spy": 6, "success": 30},
    {"id": 6,  "icon": "bi-shield-fill",    "name": "Guarnição",          "desc": "Tropas estacionadas na guarniçao",      "risk_before": 70, "risk_after": 20, "risk_per_spy": 6, "success": 55},
    {"id": 7,  "icon": "bi-arrows-move",    "name": "Movimentos",         "desc": "Movimento de tropas e frotas",          "risk_before": 50, "risk_after": 22, "risk_per_spy": 2, "success": 20},
    {"id": 21, "icon": "bi-eye",            "name": "Estado geral",       "desc": "Estado completo da cidade",             "risk_before": 40, "risk_after": 25, "risk_per_spy": 7, "success": 50},
    {"id": 25, "icon": "bi-bank",           "name": "Governo",            "desc": "Forma de governo adotada",              "risk_before": 30, "risk_after": 5,  "risk_per_spy": 4, "success": 60},
    {"id": 23, "icon": "bi-hammer",         "name": "Produção militar",   "desc": "Produção de unidades militares",        "risk_before": 65, "risk_after": 15, "risk_per_spy": 6, "success": 45},
    {"id": 26, "icon": "bi-lightbulb",      "name": "Invenções",          "desc": "Melhorias de unidades pesquisadas",     "risk_before": 55, "risk_after": 10, "risk_per_spy": 4, "success": 40},
    {"id": 27, "icon": "bi-geo-alt",        "name": "Colônias",           "desc": "Cidades colonizadas pelo jogador",      "risk_before": 30, "risk_after": 5,  "risk_per_spy": 3, "success": 45},
    {"id": 10, "icon": "bi-chat-dots",      "name": "Comunicações",       "desc": "Mensagens trocadas pela aliança",       "risk_before": 90, "risk_after": 26, "risk_per_spy": 5, "success": 40},
    {"id": 24, "icon": "bi-star",           "name": "Cargo na aliança",   "desc": "Posição do jogador na aliança",         "risk_before": 60, "risk_after": 15, "risk_per_spy": 6, "success": 50},
]


def _extract_safehouse_cities(cities: list, *, ga_pk: str = "", ga_name: str = "", multi_ga: bool = False) -> list:
    """Extract cities that have a safehouse (Guilda dos Ladrões).

    When multi_ga=True, adds ga_pk, ga_name, and city_value='{ga_pk}:{city.id}'
    so the world-spy form can spawn child jobs from the correct game account.
    """
    result = []
    for city in cities:
        safehouse_level = 0
        for b in (city.get("buildings") or []):
            bid = str(b.get("building") or "").lower()
            if bid in ("safehouse", "safe_house", "hideout", "spyhouse", "spy_house"):
                safehouse_level = int(b.get("level") or 0)
                break
        if safehouse_level > 0:
            entry = {**city, "safehouse_level": safehouse_level}
            if multi_ga:
                entry["ga_pk"] = ga_pk
                entry["ga_name"] = ga_name
                entry["city_value"] = f"{ga_pk}:{city.get('id') or ''}"
            result.append(entry)
    return result


def _world_spy_context(ga, gas: list[str] | None = None) -> dict:
    """Build world spy form context — safehouses from selected game accounts on the same server.

    gas: list of GameAccount PKs to include. If None/empty, falls back to all GAs on same server.
    Adds ga_pk, ga_name, city_value='{ga_pk}:{city_id}' to each city so that:
    - The form can display which account each safehouse belongs to.
    - The runner can spawn ac=15 child jobs using the correct game account.
    """
    if gas:
        qs = GameAccount.objects.filter(pk__in=gas, server_id=ga.server_id, active=True).order_by("name")
    else:
        # Fallback: all GAs on same server (single-tenant)
        qs = GameAccount.objects.filter(server_id=ga.server_id, active=True).order_by("name")

    all_spy_cities = []
    for other_ga in qs:
        # Enrich with city_art / tradegood_icon / tradegood_name (same as _construction_city_data)
        raw_cities = _get_cities(other_ga)
        enriched = _construction_city_data(raw_cities)
        ga_pk = str(other_ga.pk)
        ga_name = other_ga.name or ga_pk[:8]
        all_spy_cities.extend(
            _extract_safehouse_cities(enriched, ga_pk=ga_pk, ga_name=ga_name, multi_ga=True)
        )

    missions_with_scope = [
        {**m, "player_scope": m["id"] in _PLAYER_SCOPE_MISSION_IDS}
        for m in SPY_MISSIONS_UI
    ]

    spy_cycle_toggles = [
        {"key": "skipIfValid",  "label": "Pular se já tem intel válida",    "desc": "Ignora cidades com relatórios válidos para todas as missões"},
        {"key": "recallAfter",  "label": "Chamar espiões de volta",          "desc": "Envia missão de retirada após completar cada alvo"},
        {"key": "saveReports",  "label": "Salvar relatórios no hub",         "desc": "Persiste os relatórios de espionagem para consulta posterior"},
    ]

    return {
        "spy_missions":      missions_with_scope,
        "spy_cities":        all_spy_cities,
        "spy_cycle_toggles": spy_cycle_toggles,
    }


def _raid_cities_for_select(cities: list) -> list:
    """Return cities available as raid source (all cities with any troops/fleet info)."""
    result = []
    for city in (cities or []):
        result.append({
            "id":      city.get("id", ""),
            "name":    city.get("name", ""),
            "x":       city.get("x", 0),
            "y":       city.get("y", 0),
            "ga_name": city.get("ga_name", ""),
        })
    return result


def _raid_context(ga, initial: dict | None = None) -> dict:
    """Build raid form context — target city intel from latest spy reports."""
    from apps.espionage.models import SpyReport
    initial = initial or {}
    target_city_id = str(initial.get("target_city_id") or "").strip()

    raid_target = None
    if target_city_id:
        # Latest successful report for resources (mission 5)
        res_report = (
            SpyReport.objects
            .filter(target_city_id=target_city_id, mission_id=5)
            .order_by("-created_at")
            .first()
        )
        # Latest report for troops (mission 6)
        troop_report = (
            SpyReport.objects
            .filter(target_city_id=target_city_id, mission_id__in=[6, 7])
            .order_by("-created_at")
            .first()
        )
        any_report = res_report or troop_report or (
            SpyReport.objects.filter(target_city_id=target_city_id).order_by("-created_at").first()
        )

        if any_report:
            data = any_report.data_json or {}
            resources = {}
            if res_report:
                rd = res_report.data_json or {}
                for label, key in [("Madeira","wood"),("Vinho","wine"),("Mármore","marble"),("Cristal","glass"),("Enxofre","sulfur")]:
                    v = rd.get(label) or rd.get(key)
                    if v is not None:
                        try:
                            resources[key] = int(str(v).replace(".","").replace(",","").strip())
                        except Exception:
                            pass
                stocks = rd.get("stocks") or {}
                if stocks and not resources:
                    for label, key in [("Madeira","wood"),("Vinho","wine"),("Mármore","marble"),("Cristal","glass"),("Enxofre","sulfur")]:
                        v = stocks.get(label)
                        if v is not None:
                            try:
                                resources[key] = int(str(v).replace(".","").replace(",","").strip())
                            except Exception:
                                pass

            total_res = sum(resources.values())
            raid_target = {
                "city_id":    target_city_id,
                "city_name":  any_report.target_city_name or target_city_id,
                "owner":      any_report.target_owner or "?",
                "owner_id":   any_report.target_owner_id or "",
                "island_id":  str(initial.get("island_id") or ""),
                "resources":  resources,
                "total_res":  total_res,
                "has_resources": bool(resources),
                "report_age": any_report.created_at,
                "valid":      any_report.is_valid,
            }

    return {"raid_target": raid_target}


def _spy_context(ga, all_cities: list, initial: dict | None = None) -> dict:
    """Build spy form context — source cities with safehouse + target cities from worldintel."""
    from django.templatetags.static import static as _static

    spy_cities = _extract_safehouse_cities(all_cities)

    # Target cities: load from worldintel when target_owner_id is pre-filled
    target_cities = []
    target_owner_id = (initial or {}).get("target_owner_id") or ""
    if target_owner_id:
        from apps.worldintel.models import WorldDumpCity
        wdc_qs = (
            WorldDumpCity.objects.filter(owner_id=str(target_owner_id), type="city")
            .exclude(game_city_id="").exclude(game_city_id="-1")
            .select_related("island")
            .order_by("-dump__captured_at", "island__x", "island__y")
        )
        seen = set()
        for c in wdc_qs[:20]:
            if c.game_city_id in seen:
                continue
            seen.add(c.game_city_id)
            _RESOURCE_ICONS = {
                0: "game/resources/icon_wood.png",
                1: "game/resources/icon_wine.png",
                2: "game/resources/icon_marble.png",
                3: "game/resources/icon_glass.png",
                4: "game/resources/icon_sulfur.png",
            }
            rtype = c.island.resource_type if c.island else 0
            target_cities.append({
                "game_city_id": c.game_city_id,
                "name": c.name,
                "level": c.level,
                "island_id": c.island.island_id if c.island else "",
                "x": c.island.x if c.island else 0,
                "y": c.island.y if c.island else 0,
                "island_name": c.island.name if c.island else "",
                "resource_name": c.island.resource_name if c.island else "",
                "city_art": _static("game/buildings/townhall.png"),
                "tradegood_icon": _static(_RESOURCE_ICONS.get(rtype, "game/resources/icon_wood.png")),
            })

    return {
        "spy_missions": SPY_MISSIONS_UI,
        "spy_cities": spy_cities,
        "spy_target_cities": target_cities,
    }


PIRACY_MISSIONS = [
    {"buildingLevel": 1,  "duration": 150,   "gold": 40,   "capturePoints": 115,  "name": "Contrabandistas",    "durationLabel": "2m 30s", "localPic": "game/pirate/PirateContrabandistas.png"},
    {"buildingLevel": 3,  "duration": 450,   "gold": 96,   "capturePoints": 276,  "name": "Barco de Pesca",     "durationLabel": "7m 30s", "localPic": "game/pirate/PirateBarcoPesca.png"},
    {"buildingLevel": 5,  "duration": 900,   "gold": 173,  "capturePoints": 442,  "name": "Transporte de Vinho","durationLabel": "15m",    "localPic": "game/pirate/PirateTransporteVinho.png"},
    {"buildingLevel": 7,  "duration": 1800,  "gold": 311,  "capturePoints": 707,  "name": "Barco Mercante",     "durationLabel": "30m",    "localPic": "game/pirate/PirateBarcoMercante.png"},
    {"buildingLevel": 9,  "duration": 3600,  "gold": 560,  "capturePoints": 1131, "name": "Trireme",            "durationLabel": "1h",     "localPic": "game/pirate/PirateTrieme.png"},
    {"buildingLevel": 11, "duration": 7200,  "gold": 1008, "capturePoints": 1810, "name": "Submergível",        "durationLabel": "2h",     "localPic": "game/pirate/PirateSubmergivel.png"},
    {"buildingLevel": 13, "duration": 14400, "gold": 1814, "capturePoints": 2896, "name": "Navio de Diplomata", "durationLabel": "4h",     "localPic": "game/pirate/PirateDiplomata.png"},
    {"buildingLevel": 15, "duration": 28800, "gold": 3265, "capturePoints": 4634, "name": "Náufragos",          "durationLabel": "8h",     "localPic": "game/pirate/PirateNaufragos.png"},
    {"buildingLevel": 17, "duration": 57600, "gold": 5877, "capturePoints": 7414, "name": "Fenícios",           "durationLabel": "16h",    "localPic": "game/pirate/PirateFenicios.png"},
]


def _piracy_context(ga, all_cities: list, snapshot=None) -> dict:
    """Build piracy form context — filters cities with pirate fortress and annotates fortress level."""
    from django.templatetags.static import static

    missions = [
        {**m, "pic": static(m["localPic"])}
        for m in PIRACY_MISSIONS
    ]

    piracy_cities = []
    for city in all_cities:
        fortress_level = 0
        for b in (city.get("buildings") or []):
            bid = str(b.get("building") or "").lower()
            if bid in ("piratefortress", "pirate_fortress", "piratefort"):
                fortress_level = int(b.get("level") or 0)
                break
        if fortress_level > 0:
            piracy_cities.append({
                **city,
                "fortress_level": fortress_level,
            })

    base_snapshot = {}
    if isinstance(snapshot, AccountSnapshot):
        base_snapshot = snapshot.base_snapshot or {}
    elif isinstance(snapshot, dict):
        base_snapshot = snapshot.get("base_snapshot") or {}

    piracy_highscore = base_snapshot.get("piracy_highscore") or {}
    if not isinstance(piracy_highscore, dict):
        piracy_highscore = {}

    return {
        "piracy_missions": missions,
        "piracy_cities": piracy_cities,
        "piracy_highscore_preview": piracy_highscore,
    }


def _colonize_context(all_cities: list) -> dict:
    source_cities = []
    palace_level = 0
    for city in all_cities or []:
        city_copy = dict(city)
        city_copy["resource_cards"] = [
            resource
            for resource in (city.get("resources") or [])
            if str(resource.get("key") or "").strip() in {"wood", "wine", "marble", "crystal", "sulfur"}
        ]
        source_cities.append(city_copy)
        for building in (city.get("buildings") or []):
            if str(building.get("building") or "").strip() != "palace":
                continue
            palace_level = max(palace_level, _safe_int(building.get("level"), 0))

    city_count = len(source_cities)
    next_required_palace_level = max(1, city_count)
    remaining_slots = max(0, palace_level - city_count)
    return {
        "colonize_ui": {
            "source_cities": source_cities,
            "city_count": city_count,
            "palace_level": palace_level,
            "remaining_slots": remaining_slots,
            "can_found_now": palace_level >= city_count,
            "next_required_palace_level": next_required_palace_level,
        }
    }


def _abandon_colony_context(all_cities: list, selected_city_id: str = "") -> dict:
    colony_cities = []
    for city in all_cities or []:
        buildings = city.get("buildings") or []
        has_palace = any(str(item.get("building") or "").strip() == "palace" for item in buildings if isinstance(item, dict))
        if has_palace:
            continue
        city_copy = dict(city)
        city_copy["resource_cards"] = [
            resource
            for resource in (city.get("resources") or [])
            if str(resource.get("key") or "").strip() in {"wood", "wine", "marble", "crystal", "sulfur"}
        ][:3]
        city_copy["selected"] = str(city.get("id") or "") == str(selected_city_id or "")
        colony_cities.append(city_copy)

    selected_city = next((city for city in colony_cities if city["selected"]), None)
    return {
        "abandon_ui": {
            "colony_cities": colony_cities,
            "colony_count": len(colony_cities),
            "selected_city": selected_city,
            "confirm_phrase": "ABANDONAR",
        }
    }


def _latest_world_dump_for_ga(ga: GameAccount):
    exact = (
        WorldDump.objects.filter(game_account=ga)
        .order_by("-captured_at")
        .first()
    )
    if exact:
        return exact, "account"
    fallback = (
        WorldDump.objects.filter(game_account__server_id=ga.server_id)
        .order_by("-captured_at")
        .first()
    )
    if fallback:
        return fallback, "server"
    return None, "none"


def _coerce_coord(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _piracy_raid_level(actions) -> int:
    if isinstance(actions, dict):
        try:
            return int(actions.get("piracy_raid") or 0)
        except (TypeError, ValueError):
            return 0
    if isinstance(actions, list):
        for item in actions:
            text = str(item or "").strip().lower()
            if "piracy" in text and "raid" in text:
                return 1
    return 0


def _format_dump_age(captured_at):
    if not captured_at:
        return ""
    return captured_at.strftime("%d/%m/%Y %H:%M")


def _build_colonize_island_lookup(ga: GameAccount, coord_x=None, coord_y=None, selected_position=None, form=None) -> dict:
    selected_dump, dump_scope = _latest_world_dump_for_ga(ga)
    x = _coerce_coord(coord_x)
    y = _coerce_coord(coord_y)
    lookup = {
        "coord_x": "" if x is None else x,
        "coord_y": "" if y is None else y,
        "selected_position": str(selected_position or "").strip(),
        "has_coords": x is not None and y is not None,
        "has_dump": bool(selected_dump),
        "dump_id": str(selected_dump.pk) if selected_dump else "",
        "dump_captured_at": _format_dump_age(selected_dump.captured_at) if selected_dump else "",
        "dump_scope": dump_scope,
        "dump_game_account_name": (selected_dump.game_account.name or selected_dump.game_account.server_id) if selected_dump and selected_dump.game_account else "",
        "island_found": False,
        "island_id": "",
        "island_name": "",
        "resource_name": "",
        "miracle_name": "",
        "empty_positions": [],
        "occupied_slots": [],
        "raid_targets": [],
    }
    if x is None or y is None or not selected_dump:
        return lookup

    island = (
        WorldDumpIsland.objects.filter(dump=selected_dump, x=x, y=y)
        .prefetch_related("cities")
        .first()
    )
    if not island:
        return lookup

    lookup.update({
        "island_found": True,
        "island_id": str(island.island_id or ""),
        "island_name": island.name or "",
        "resource_name": island.resource_name or "",
        "miracle_name": island.miracle_name or "",
    })

    empty_positions = []
    occupied_slots = []
    raid_targets = []
    ordered_cities = sorted(island.cities.all(), key=lambda item: ((item.position or 0), item.pk))
    for idx, city in enumerate(ordered_cities, start=1):
        slot_position = int(city.position or 0) or idx
        slot = {
            "position": slot_position,
            "name": city.name or "",
            "owner_name": city.owner_name or "",
            "owner_id": city.owner_id or "",
            "city_id": city.game_city_id or "",
            "type": city.type or "",
            "ally_tag": city.ally_tag or "",
            "level": int(city.level or 0),
            "state": city.state or "",
            "piracy_raid_level": _piracy_raid_level(city.actions_json),
        }
        if slot["type"] in {"empty", "buildplace"}:
            empty_positions.append(slot)
        else:
            occupied_slots.append(slot)
            if slot["piracy_raid_level"] > 0:
                raid_targets.append(slot)

    lookup["empty_positions"] = empty_positions
    lookup["occupied_slots"] = occupied_slots
    lookup["raid_targets"] = raid_targets
    return lookup


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


def _upgrade_units_form_context(cities, snapshot=None):
    """Build context for the Melhorar Unidades (action 1203) creation form."""
    workshop_cities = []
    for city in cities or []:
        for building in city.get("buildings") or []:
            if str(building.get("building") or "").strip() != "workshop":
                continue
            workshop_cities.append({
                "id": city.get("id"),
                "name": city.get("name", ""),
                "x": city.get("x"),
                "y": city.get("y"),
                "tradegood_name": city.get("tradegood_name", ""),
                "tradegood_icon": city.get("tradegood_icon", ""),
                "position": int(building.get("position", 0) or 0),
                "level": int(building.get("level", 0) or 0),
            })
            break
    workshop_cities.sort(key=lambda item: str(item.get("name") or "").lower())

    unit_improvements: dict[str, dict] = {}
    if snapshot:
        base = snapshot.base_snapshot or {}
        unit_improvements = base.get("unit_improvements") or {}

    def _unit_stats(game_id: int) -> dict:
        us = _UNIT_STATS_DB.get(game_id, {})
        if not us.get("hp"):
            return {}
        imp = unit_improvements.get(str(game_id), {})
        off_lv = int(imp.get("offensive") or 0)
        def_lv = int(imp.get("defensive") or 0)
        imp_data = us.get("improvements", {})
        off_bonus = int((imp_data.get("offensive") or {}).get("bonus_per_level") or 0)
        def_bonus = int((imp_data.get("defensive") or {}).get("bonus_per_level") or 0)
        weapons = us.get("weapons") or []
        base_dmg = max((w.get("damage", 0) for w in weapons), default=0)
        return {
            "hp": us.get("hp", 0),
            "damage": base_dmg + off_lv * off_bonus,
            "armor": int(us.get("armor") or 0) + def_lv * def_bonus,
            "speed": us.get("speed", 0),
            "off_lv": off_lv,
            "def_lv": def_lv,
            "off_bonus": off_bonus,
            "def_bonus": def_bonus,
        }

    land_units = [
        {**u, "icon_url": static(u["icon"]) if u.get("icon") else "", "stats": _unit_stats(u["game_id"])}
        for u in WORKSHOP_UNITS_UI if u["tab"] == "land"
    ]
    sea_units = [
        {**u, "icon_url": static(u["icon"]) if u.get("icon") else "", "stats": _unit_stats(u["game_id"])}
        for u in WORKSHOP_UNITS_UI if u["tab"] == "sea"
    ]

    return {
        "summary": {
            "has_workshop": bool(workshop_cities),
            "workshop_cities": workshop_cities,
            "city_count": len(workshop_cities),
            "workshop_icon": static("game/buildings/workshop.png"),
            "crystal_icon": static("game/resources/icon_glass.png"),
        },
        "priority_modes": (
            {"value": "offensive_first", "label": "Ataque primeiro"},
            {"value": "defensive_first", "label": "Defesa primeiro"},
            {"value": "shortest_first", "label": "Menor tempo primeiro"},
            {"value": "cheapest_first", "label": "Menor custo primeiro"},
        ),
        "cities": workshop_cities,
        "land_units": land_units,
        "sea_units": sea_units,
        "all_units": land_units + sea_units,
    }


_DIPLOMACY_STATIC_TYPES = [
    ("50", "Mensagem"),
    ("60", "Proposta de Tratado Comercial"),
    ("77", "Proposta de Tratado Cultural"),
    ("89", "Candidatura para Aliança"),
    ("100", "Pedido de Amizade"),
    ("102", "Aceitar Pedido de Amizade"),
    ("103", "Recusar Pedido de Amizade"),
    ("110", "Oferecer Partilha de IP"),
    ("115", "Declarar Desafio de Guerra"),
]


def _diplomacy_msg_types(receiver_id: str) -> list[tuple[str, str]]:
    """Build available msgType choices for action 31, always includes static types."""
    from apps.worldintel.models import WorldDumpCity
    options = list(_DIPLOMACY_STATIC_TYPES)
    if receiver_id:
        cities = (
            WorldDumpCity.objects.filter(owner_id=str(receiver_id), type="city")
            .exclude(game_city_id="")
            .exclude(game_city_id="-1")
            .order_by("-dump__captured_at", "name")
            .values("game_city_id", "name")
            .distinct()[:20]
        )
        seen = set()
        for city in cities:
            gcid = city["game_city_id"]
            if gcid and gcid not in seen:
                seen.add(gcid)
                options.append((f"66c{gcid}", f"Direito de ocupação — {city['name']}"))
    return options


def _custom_field_names(action_code: int) -> list[str]:
    if int(action_code) == 808:
        return ["buyer_city_id", "unit_category"]
    if int(action_code) == 25:
        return ["enable"]
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
    if int(action_code) == 8:
        return ["buyer_city_id", "seller_city_id", "resource_idx", "amount"]
    if int(action_code) == 9:
        return ["city_id", "resource_idx", "amount", "unit_price"]
    if int(action_code) == 803:
        return ["city_id", "unit_id", "amount", "unit_price"]
    if int(action_code) == 804:
        return ["buyer_city_id", "seller_city_id", "unit_id", "quantity", "max_price", "unit_category"]
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
    if int(action_code) == 1203:
        return ["unit_targets_json", "min_crystal_reserve", "min_gold_reserve", "priority_mode"]
    if int(action_code) == 15:
        return [
            "city_id", "target_city_id", "island_id",
            "target_city_name", "target_owner", "target_owner_id",
            "mission_id", "max_detection_risk",
            "recall_after", "save_reports", "delete_after_save",
        ]
    if int(action_code) == 16:
        return [
            "city_ids", "missions",
            "region_x_min", "region_x_max", "region_y_min", "region_y_max",
            "only_inactive", "max_total_score", "max_army_score",
            "skip_if_valid", "intel_ttl_hours", "max_targets", "interval_minutes",
            "max_detection_risk", "recall_after", "save_reports", "delete_after_save",
            "raid_alert_enabled", "raid_alert_threshold", "raid_source_city_id",
            "raid_transporters", "raid_max_trips",
        ]
    if int(action_code) == 31:
        return ["receiver_name", "receiver_id", "msg_type", "content", "city_id"]
    if int(action_code) == 17:
        return [
            "city_id", "mission_level",
            "schedule_by_time", "day_mission_level", "day_start_hour", "day_end_hour",
            "night_mission_level", "night_start_hour", "night_end_hour",
            "max_random_wait",
            "convert_mode", "convert_percent", "convert_threshold",
            "enable_targeted_cycle", "targeted_interval_days", "targeted_after_hour",
            "target_require_lower_total_score", "target_require_lower_general_score",
            "target_max_score_gap", "targeted_max_active_foundations",
            "target_require_eta_efficiency", "targeted_compare_mission_level",
            "targeted_convert_mode", "targeted_convert_percent",
        ]
    if int(action_code) == 820:
        return [
            "source_city_id", "coord_x", "coord_y", "island_id", "position",
            "wood", "wine", "marble", "crystal", "sulfur",
        ]
    if int(action_code) == 821:
        return ["city_id"]
    if int(action_code) == 9002:
        return ["city_id", "revolt_type"]
    if int(action_code) == 602:
        return ["dump_mode", "own_city_ids", "coord_x", "coord_y", "region_x_min", "region_y_min", "region_x_max", "region_y_max", "include_empty"]
    if int(action_code) == 1005:
        return ["city_id", "city_ids", "building_type", "mode", "maintain_scope", "consolidate_city_id", "position", "recheck_minutes", "city_targets_json"]
    if int(action_code) == 1202:
        return ["scope", "from_city_id", "to_city_id"]
    if int(action_code) == 1008:
        return ["source_city_id", "target_city_id", "island_id", "units", "transporters", "wall_level", "multi_trip", "max_trips", "min_resources_to_continue", "enemy_units", "needs_blockade", "mode"]
    return []


def _job_form_context(form, action_meta, action_code, ga, cities, request=None, gas: list[str] | None = None):
    snapshot = None
    try:
        snapshot = AccountSnapshot.objects.get(game_account=ga)
    except AccountSnapshot.DoesNotExist:
        snapshot = None
    ctx = {
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
        "upgrade_units_ui": _upgrade_units_form_context(cities, snapshot),
        "market_ui": _market_form_context(cities),
        "training_ui": _training_form_context(snapshot, cities),
        "black_market_ui": _black_market_form_context(cities, snapshot),
        "bm_buy_ui": _bm_available_offers_context(cities, snapshot),
    }
    if int(action_code) == 15:
        ctx.update(_spy_context(ga, cities, getattr(form, "initial", {})))
    if int(action_code) == 16:
        ctx["raid_cities"] = _raid_cities_for_select(cities)
    if int(action_code) == 1008:
        ctx.update(_raid_context(ga, getattr(form, "initial", {})))
    if int(action_code) == 16:
        # World spy: safehouses from selected GAs (passed via ?gas=) or all on same server
        ctx.update(_world_spy_context(ga, gas=gas))
    if int(action_code) == 17:
        ctx.update(_piracy_context(ga, cities, snapshot))
    if int(action_code) == 820:
        ctx.update(_colonize_context(cities))
        coord_x = None
        coord_y = None
        selected_position = None
        source = getattr(form, "data", None) if form.is_bound else getattr(form, "initial", {})
        if isinstance(source, dict):
            coord_x = source.get("coord_x")
            coord_y = source.get("coord_y")
            selected_position = source.get("position")
        ctx["colonize_island"] = _build_colonize_island_lookup(
            ga,
            coord_x=coord_x,
            coord_y=coord_y,
            selected_position=selected_position,
            form=form,
        )
    if int(action_code) == 821:
        source = getattr(form, "data", None) if form.is_bound else getattr(form, "initial", {})
        selected_city_id = ""
        if isinstance(source, dict):
            selected_city_id = str(source.get("city_id") or source.get("city") or "").strip()
        ctx.update(_abandon_colony_context(cities, selected_city_id=selected_city_id))
    return ctx


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
        from urllib.parse import urlencode
        extra_params = {k: v for k, v in request.GET.items() if k not in ("ga", "action")}
        extra_qs = ("&" + urlencode(extra_params)) if extra_params else ""
        html = render_to_string(
            "jobs/partials/create_step_ga.html",
            {
                "account_groups": account_groups,
                "action_code": action_code,
                "action_meta": action_meta,
                "extra_qs": extra_qs,
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
        gas_raw = request.GET.get("gas") or ""
        gas = [g.strip() for g in gas_raw.split(",") if g.strip()] if gas_raw else None

        cities = _get_cities(ga)
        construction_cities = _construction_city_data(cities)
        initial = _build_job_form_initial(request, ga, action_code)
        form = JobCreateForm(
            action_code=action_code,
            game_account=ga,
            cities=construction_cities,
            initial=initial,
        )
        ctx = _job_form_context(form, action_meta, action_code, ga, construction_cities, request=request, gas=gas)
        if int(action_code) == 31:
            ctx["diplomacy_msg_types"] = _diplomacy_msg_types(initial.get("receiver_id") or "")
        html = render_to_string(
            "jobs/partials/create_step_form.html",
            ctx,
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


class RaidCityLookupView(LoginRequiredMixin, View):
    """GET /jobs/new/raid-city-lookup/ — busca cidades no dump por coordenada X/Y.

    Params: ga=<ga_id>, coord_x=<int>, coord_y=<int>
    Returns: HTML partial com lista de cidades encontradas.
    """

    def get(self, request):
        from apps.worldintel.models import WorldDump, WorldDumpCity
        from apps.espionage.models import SpyReport
        from django.utils import timezone

        ga_id    = request.GET.get("ga", "").strip()
        coord_x  = request.GET.get("coord_x", "").strip()
        coord_y  = request.GET.get("coord_y", "").strip()

        cities = []
        error  = ""

        if coord_x and coord_y:
            try:
                x = int(coord_x)
                y = int(coord_y)
            except ValueError:
                error = "Coordenadas inválidas."
            else:
                # Get latest dump
                qs = WorldDump.objects.filter(status="complete").order_by("-captured_at")
                if ga_id:
                    try:
                        ga = GameAccount.objects.get(pk=ga_id)
                        qs = qs.filter(game_account__server_id=ga.server_id)
                    except GameAccount.DoesNotExist:
                        pass
                dump = qs.first()

                if dump:
                    city_qs = WorldDumpCity.objects.filter(
                        dump=dump,
                        island__x=x,
                        island__y=y,
                        type="city",
                    ).exclude(owner_name="").select_related("island").order_by("owner_name", "name")

                    # Annotate with latest spy report info
                    now = timezone.now()
                    for city in city_qs:
                        latest = (
                            SpyReport.objects
                            .filter(target_city_id=city.game_city_id, expires_at__gt=now)
                            .order_by("-created_at")
                            .first()
                        )
                        cities.append({
                            "game_city_id": city.game_city_id,
                            "name":         city.name,
                            "owner_name":   city.owner_name,
                            "level":        city.level,
                            "state":        city.state,
                            "island_id":    city.island.island_id if city.island else "",
                            "has_intel":    bool(latest),
                        })
                    if not cities:
                        error = f"Nenhuma cidade encontrada em [{x}:{y}]."
                else:
                    error = "Nenhum dump disponível. Execute o WorldDump primeiro."

        html = render_to_string(
            "jobs/partials/raid_city_lookup.html",
            {"cities": cities, "error": error, "coord_x": coord_x, "coord_y": coord_y, "ga_id": ga_id},
            request=request,
        )
        return HttpResponse(html)


class JobFormView(LoginRequiredMixin, View):
    """GET: returns the dynamic form for a specific action + game account."""

    def get(self, request):
        ga_id = request.GET.get("ga")
        action_code_str = request.GET.get("action")
        # ?gas=ga1pk,ga2pk,... — for ac=16 multi-GA world spy
        gas_raw = request.GET.get("gas") or ""
        gas = [g.strip() for g in gas_raw.split(",") if g.strip()] if gas_raw else None

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
        initial = _build_job_form_initial(request, ga, action_code)
        form = JobCreateForm(
            action_code=action_code,
            game_account=ga,
            cities=construction_cities,
            initial=initial,
        )
        ctx = _job_form_context(form, action_meta, action_code, ga, construction_cities, request=request, gas=gas)
        if action_code == 31:
            ctx["diplomacy_msg_types"] = _diplomacy_msg_types(initial.get("receiver_id") or "")
        html = render_to_string(
            "jobs/partials/create_step_form.html",
            ctx,
            request=request,
        )
        return HttpResponse(html)


class ColonizeIslandLookupView(LoginRequiredMixin, View):
    """GET: resolve current dump island by coords and render the slot selector partial."""

    def get(self, request):
        ga_id = str(request.GET.get("ga") or request.GET.get("game_account") or "").strip()
        if not ga_id:
            return HttpResponse("")
        try:
            ga = GameAccount.objects.select_related("account").get(pk=ga_id, active=True)
        except GameAccount.DoesNotExist:
            return HttpResponse("")

        cities = _construction_city_data(_get_cities(ga))
        initial = _build_job_form_initial(request, ga, 820)
        form = JobCreateForm(
            action_code=820,
            game_account=ga,
            cities=cities,
            initial=initial,
        )
        lookup = _build_colonize_island_lookup(
            ga,
            coord_x=request.GET.get("coord_x"),
            coord_y=request.GET.get("coord_y"),
            selected_position=request.GET.get("position"),
            form=form,
        )
        html = render_to_string(
            "jobs/partials/colonize_island_lookup.html",
            {
                "form": form,
                "game_account": ga,
                "colonize_island": lookup,
            },
            request=request,
        )
        return HttpResponse(html)


class ColonizeIslandRefreshView(LoginRequiredMixin, View):
    """POST: create a small 602 job to refresh one coordinate in-place."""

    def post(self, request):
        ga_id = str(request.POST.get("ga") or request.POST.get("game_account") or request.GET.get("ga") or "").strip()
        coord_x = _coerce_coord(request.POST.get("coord_x") or request.GET.get("coord_x"))
        coord_y = _coerce_coord(request.POST.get("coord_y") or request.GET.get("coord_y"))
        if not ga_id or coord_x is None or coord_y is None:
            return self._status_partial(request, "", ga_id, coord_x, coord_y, state="error", message="Coordenadas invalidas.")

        try:
            ga = GameAccount.objects.select_related("account").get(pk=ga_id, active=True)
        except GameAccount.DoesNotExist:
            return self._status_partial(request, "", ga_id, coord_x, coord_y, state="error", message="Conta nao encontrada.")

        current_dump, _dump_scope = _latest_world_dump_for_ga(ga)
        inputs = {
            "dump_mode": "single_island",
            "coord_x": coord_x,
            "coord_y": coord_y,
            "include_empty": True,
        }
        if current_dump:
            inputs["replace_dump_id"] = str(current_dump.pk)

        job = create_job_with_workflow(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=602,
            inputs=inputs,
            status="queued",
            created_by=request.user,
        )
        return self._status_partial(request, str(job.pk), ga_id, coord_x, coord_y, state="running")

    @staticmethod
    def _status_partial(request, job_id: str, ga_id: str, coord_x, coord_y, *, state: str, message: str = ""):
        html = render_to_string(
            "jobs/partials/colonize_island_refresh_status.html",
            {
                "job_id": job_id,
                "ga_id": ga_id,
                "coord_x": "" if coord_x is None else coord_x,
                "coord_y": "" if coord_y is None else coord_y,
                "state": state,
                "message": message,
            },
            request=request,
        )
        return HttpResponse(html)


class ColonizeIslandRefreshStatusView(LoginRequiredMixin, View):
    """GET: poll the coordinate refresh job and swap back to the island lookup partial."""

    def get(self, request):
        job_id = str(request.GET.get("job_id") or "").strip()
        ga_id = str(request.GET.get("ga") or "").strip()
        coord_x = request.GET.get("coord_x")
        coord_y = request.GET.get("coord_y")
        if not job_id or not ga_id:
            return ColonizeIslandRefreshView._status_partial(request, job_id, ga_id, coord_x, coord_y, state="error", message="Parametros ausentes.")

        try:
            job = Job.objects.select_related("game_account").get(pk=job_id)
        except Job.DoesNotExist:
            return ColonizeIslandRefreshView._status_partial(request, job_id, ga_id, coord_x, coord_y, state="error", message="Job de refresh nao encontrado.")

        if job.status in {"queued", "running", "scheduled"}:
            return ColonizeIslandRefreshView._status_partial(request, job_id, ga_id, coord_x, coord_y, state="running")
        if job.status in {"error", "cancelled"}:
            return ColonizeIslandRefreshView._status_partial(request, job_id, ga_id, coord_x, coord_y, state="error", message="O refresh da ilha nao concluiu.")

        try:
            ga = GameAccount.objects.select_related("account").get(pk=ga_id, active=True)
        except GameAccount.DoesNotExist:
            return ColonizeIslandRefreshView._status_partial(request, job_id, ga_id, coord_x, coord_y, state="error", message="Conta nao encontrada.")

        cities = _construction_city_data(_get_cities(ga))
        initial = {
            "game_account": str(ga.pk),
            "action_code": 820,
            "coord_x": coord_x,
            "coord_y": coord_y,
        }
        form = JobCreateForm(action_code=820, game_account=ga, cities=cities, initial=initial)
        lookup = _build_colonize_island_lookup(
            ga,
            coord_x=coord_x,
            coord_y=coord_y,
            selected_position=request.GET.get("position"),
            form=form,
        )
        html = render_to_string(
            "jobs/partials/colonize_island_lookup.html",
            {
                "form": form,
                "game_account": ga,
                "colonize_island": lookup,
                "refresh_message": "Ilha atualizada agora",
            },
            request=request,
        )
        response = HttpResponse(html)
        response["HX-Trigger"] = json.dumps({
            "toast": {"type": "success", "message": f"Ilha [{coord_x}:{coord_y}] atualizada."}
        })
        return response


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
        resolved_research, resolved_build_time, resolved_government = _resolve_construction_modifiers(
            action_code,
            ga,
            {
                "research_reduction": research_reduction,
                "build_time_reduction": build_time_reduction,
            },
        )

        preview = None
        plan_preview = None
        try:
            if action_code == 1002 and plan_raw:
                steps = json.loads(plan_raw)
                if isinstance(steps, list) and steps:
                    plan_preview = build_construction_plan_preview(
                        game_account=ga,
                        steps=steps,
                        research_reduction=resolved_research,
                        build_time_reduction=resolved_build_time,
                        government_time_reduction=resolved_government,
                    )
            elif city_id and target_level and (building_position or building_type):
                preview = build_construction_preview(
                    game_account=ga,
                    city_id=city_id,
                    building_position=building_position,
                    building_type=building_type,
                    target_level=int(target_level),
                    research_reduction=resolved_research,
                    build_time_reduction=resolved_build_time,
                    government_time_reduction=resolved_government,
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

        if int(action_code) == 1203:
            return self._submit_upgrade_units(request, ga, action_code, action_meta, cities)

        form = JobCreateForm(
            request.POST,
            action_code=action_code,
            game_account=ga,
            cities=construction_cities,
        )

        if not form.is_valid():
            ctx = _job_form_context(form, action_meta, action_code, ga, construction_cities, request=request)
            if action_code == 31:
                ctx["diplomacy_msg_types"] = _diplomacy_msg_types(
                    request.POST.get("receiver_id") or ""
                )
            html = render_to_string(
                "jobs/partials/create_step_form.html",
                ctx,
                request=request,
            )
            return HttpResponse(html)

        inputs = form.get_inputs_json()
        if int(action_code) == 17:
            inputs = self._normalize_piracy_inputs(inputs, request)
        if int(action_code) == 821:
            error_message = self._validate_abandon_colony_inputs(form, request, construction_cities)
            if error_message:
                form.add_error(None, error_message)
                ctx = _job_form_context(form, action_meta, action_code, ga, construction_cities, request=request)
                html = render_to_string(
                    "jobs/partials/create_step_form.html",
                    ctx,
                    request=request,
                )
                return HttpResponse(html)
        if int(action_code) == 601:
            inputs = self._normalize_island_monitor_inputs(inputs)
        if int(action_code) == 1005:
            inputs = self._normalize_train_units_inputs(inputs, request)
        if int(action_code) == 1202:
            inputs = self._normalize_station_units_inputs(inputs, request)
        if int(action_code) == 1008:
            inputs = self._normalize_raid_inputs(inputs, request)
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

    def _submit_upgrade_units(self, request, ga, action_code, action_meta, cities):
        # Parse unit targets JSON
        raw_targets = request.POST.get("unit_targets_json", "[]").strip()
        try:
            unit_targets = json.loads(raw_targets) if raw_targets else []
        except Exception:
            unit_targets = []
        if not isinstance(unit_targets, list):
            unit_targets = []

        # Sanitise each target entry
        clean_targets = []
        for t in unit_targets:
            if not isinstance(t, dict):
                continue
            key = str(t.get("key") or "").strip()
            if not key:
                continue
            attack_raw = t.get("attack_max")
            defense_raw = t.get("defense_max")
            legacy_raw = t.get("max_level")

            def _coerce_branch(value):
                if value in ("", None):
                    return None
                try:
                    return max(0, int(value))
                except (TypeError, ValueError):
                    return None

            attack_max = _coerce_branch(attack_raw)
            defense_max = _coerce_branch(defense_raw)

            if attack_max is None and defense_max is None and legacy_raw not in ("", None):
                try:
                    legacy_max = max(0, int(legacy_raw))
                except (TypeError, ValueError):
                    legacy_max = 0
                attack_max = legacy_max
                defense_max = legacy_max

            if attack_max is None and defense_max is None:
                continue

            clean_targets.append({
                "key": key,
                "attack_max": attack_max,
                "defense_max": defense_max,
            })

        # Resource reserves
        try:
            min_crystal = int(request.POST.get("min_crystal_reserve") or 0)
        except (ValueError, TypeError):
            min_crystal = 0
        try:
            min_gold = int(request.POST.get("min_gold_reserve") or 0)
        except (ValueError, TypeError):
            min_gold = 0
        priority_mode = str(request.POST.get("priority_mode") or "offensive_first").strip() or "offensive_first"
        if priority_mode not in {"offensive_first", "defensive_first", "shortest_first", "cheapest_first"}:
            priority_mode = "offensive_first"

        # Optional city filter from the standard city selector
        city_id = str(request.POST.get("city_id") or request.POST.get("city") or "").strip()

        inputs = {
            "unit_targets": clean_targets,
            "target_all": len(clean_targets) == 0,
            "min_crystal_reserve": max(0, min_crystal),
            "min_gold_reserve": max(0, min_gold),
            "priority_mode": priority_mode,
        }
        if city_id:
            inputs["city_id"] = city_id

        create_job_with_workflow(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=action_code,
            inputs=inputs,
            status="queued",
        )

        resp = HttpResponse(
            render_to_string(
                "jobs/partials/create_step_success.html",
                {"jobs_created": 1, "action_name": action_meta["name"]},
                request=request,
            )
        )
        resp["HX-Trigger"] = json.dumps({
            "toast": {"type": "success", "message": "Job Melhorar Unidades criado!"},
            "jobsCreated": True,
        })
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
                _job_form_context(form, action_meta, action_code, ga, cities, request=request),
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
                "building_position": str(step.get("building_position") or ""),
                "mode": str(step.get("mode") or "upgrade"),
                "slot_types": list(step.get("slot_types") or []),
                "preferred_position": str(step.get("preferred_position") or ""),
                "target_level": target_level,
            })

        clean_steps = _resolve_construction_new_slots(clean_steps, cities)

        inputs = form.get_inputs_json()
        research_reduction, build_time_reduction, government_time_reduction = _resolve_construction_modifiers(
            action_code,
            ga,
            inputs,
        )
        plan_preview = build_construction_plan_preview(
            game_account=ga,
            steps=clean_steps,
            research_reduction=research_reduction,
            build_time_reduction=build_time_reduction,
            government_time_reduction=government_time_reduction,
        )
        inputs["research_reduction"] = research_reduction
        inputs["build_time_reduction"] = build_time_reduction
        inputs["government_time_reduction"] = government_time_reduction
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
        # Check for an existing active construction plan to merge into
        existing_job = Job.objects.filter(
            game_account=ga,
            action_code=1002,
            status__in=["queued", "running", "scheduled"],
        ).order_by("-created_at").first()

        if existing_job:
            return self._merge_construction_plan(
                request, existing_job, ga, clean_steps, action_meta,
            )

        job = create_job_with_workflow(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=action_code,
            inputs=inputs,
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

    def _merge_construction_plan(self, request, existing_job, ga, new_steps, action_meta):
        """Append new_steps to an existing active construction plan job."""
        try:
            existing_inputs = json.loads(existing_job.inputs_json or "{}")
        except Exception:
            existing_inputs = {}

        existing_plan = existing_inputs.get("construction_plan_json") or []
        merged_plan = list(existing_plan) + list(new_steps)

        # Preserve modifiers from existing job
        research_reduction = existing_inputs.get("research_reduction", 14)
        build_time_reduction = existing_inputs.get("build_time_reduction", "0")
        government_time_reduction = existing_inputs.get("government_time_reduction", 0)

        plan_preview = build_construction_plan_preview(
            game_account=ga,
            steps=merged_plan,
            research_reduction=research_reduction,
            build_time_reduction=build_time_reduction,
            government_time_reduction=government_time_reduction,
        )

        existing_inputs["construction_plan_json"] = merged_plan
        existing_inputs["construction_plan_steps"] = plan_preview.steps
        existing_inputs["construction_summary"] = {
            "steps": len(merged_plan),
            "totals": plan_preview.totals,
            "reserved_local": plan_preview.reserved_local,
            "missing": plan_preview.missing,
            "base_seconds": plan_preview.base_seconds,
            "adjusted_seconds": plan_preview.adjusted_seconds,
        }
        existing_job.inputs_json = json.dumps(existing_inputs)
        existing_job.save(update_fields=["inputs_json", "updated_at"])

        # Recreate reservations for the full merged plan
        existing_job.construction_reservations.all().delete()
        self._create_construction_reservations(existing_job, plan_preview)

        n = len(new_steps)
        resp = HttpResponse(
            render_to_string(
                "jobs/partials/create_step_success.html",
                {"jobs_created": 0, "merged": True, "merged_count": n, "action_name": action_meta["name"]},
                request=request,
            )
        )
        resp["HX-Trigger"] = json.dumps({
            "toast": {
                "type": "success",
                "message": f"{n} etapa(s) mesclada(s) ao plano de construcao existente.",
            },
            "jobsCreated": True,
        })
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
            if int(action_code) in {3, 27, 601, 602}:
                # ac=3 (distribute): single job with full cities list so runner can plan routes
                # ac=27, ac=601, ac=602: single job by design
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
            if int(action_code) in {2, 6, 8, 9, 11, 821, 9002} and city_choices:
                single_inputs["_city_choices"] = city_choices
            if int(action_code) in {8, 9} and cities:
                single_inputs["_city_objects"] = {
                    str(city.get("id")): city
                    for city in (cities or [])
                    if city.get("id") is not None
                }
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
        elif int(action_code) in {821, 9002}:
            city_map = inputs.get("_city_choices") if isinstance(inputs.get("_city_choices"), dict) else {}
            city_id = str(enriched_inputs.get("city_id") or "").strip()
            if city_id and city_map.get(city_id):
                enriched_inputs["city_name"] = city_map[city_id]
            enriched_inputs.pop("_city_choices", None)
        elif int(action_code) in {8, 9}:
            selected_city_key = "buyer_city_id" if int(action_code) == 8 else "city_id"
            city_map = inputs.get("_city_choices") if isinstance(inputs.get("_city_choices"), dict) else {}
            city_id = str(enriched_inputs.get(selected_city_key) or "").strip()
            city_choices = inputs.get("_city_objects") if isinstance(inputs.get("_city_objects"), dict) else {}
            city_obj = city_choices.get(city_id) if city_id else None
            branch_office_pos = _find_branch_office_pos(city_obj)
            if city_id and city_map.get(city_id):
                if int(action_code) == 8:
                    enriched_inputs["buyer_city_name"] = city_map[city_id]
                else:
                    enriched_inputs["city_name"] = city_map[city_id]
            if int(action_code) == 8:
                enriched_inputs["buyer_branchoffice_pos"] = branch_office_pos if branch_office_pos is not None else 0
                enriched_inputs["seller_branchoffice_pos"] = 0
            else:
                enriched_inputs["branchoffice_pos"] = branch_office_pos if branch_office_pos is not None else 0
            enriched_inputs.pop("_city_choices", None)
            enriched_inputs.pop("_city_objects", None)
        # Deduplication for recurring loop actions: skip if active job exists for same city
        _LOOP_ACTIONS = {901, 902, 1006, 1003, 17, 1018}
        if int(action_code) in _LOOP_ACTIONS:
            city_key = str(enriched_inputs.get("city_id") or enriched_inputs.get("city") or "")
            donation_type = str(enriched_inputs.get("donation_type") or "")
            if city_key:
                from apps.jobs.models import Job as _Job
                import json as _json
                active = _Job.objects.filter(
                    game_account=ga,
                    action_code=action_code,
                    status__in=["queued", "scheduled", "running"],
                )
                for existing in active:
                    try:
                        ex_inputs = _json.loads(existing.inputs_json or "{}")
                    except Exception:
                        ex_inputs = {}
                    ex_city = str(ex_inputs.get("city_id") or ex_inputs.get("city") or "")
                    ex_dtype = str(ex_inputs.get("donation_type") or "")
                    if ex_city == city_key and ex_dtype == donation_type:
                        import logging as _log
                        _log.getLogger(__name__).warning(
                            "Skipping duplicate job action=%s ga=%s city=%s dtype=%s — active job %s already exists",
                            action_code, ga.pk, city_key, donation_type, existing.pk,
                        )
                        return 0

        create_job_with_workflow(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=action_code,
            inputs=enriched_inputs,
            status="queued",
        )
        return 1

    @staticmethod
    def _normalize_island_monitor_inputs(inputs):
        normalized = dict(inputs)
        extra_raw = normalized.get("extra_island_ids") or []
        tokens = []

        if isinstance(extra_raw, str):
            compact = extra_raw.replace("\r", "\n")
            for part in re.split(r"[\n,;]+", compact):
                part = str(part or "").strip()
                if part:
                    tokens.append(part)
        elif isinstance(extra_raw, list):
            for item in extra_raw:
                part = str(item or "").strip()
                if part:
                    tokens.append(part)

        resolved = []
        seen = set()
        for token in tokens:
            match = re.search(r"(?:islandId|island_id)=(\d+)", token, re.IGNORECASE)
            if match:
                normalized_token = match.group(1)
            else:
                coord_match = re.fullmatch(r"\[?\s*(\d{1,3})\s*[:xX]\s*(\d{1,3})\s*\]?", token)
                if coord_match:
                    normalized_token = f"{coord_match.group(1)}:{coord_match.group(2)}"
                elif re.fullmatch(r"\d+", token):
                    normalized_token = token
                else:
                    continue
            if normalized_token not in seen:
                seen.add(normalized_token)
                resolved.append(normalized_token)

        normalized["extra_island_ids"] = resolved
        return normalized

    @staticmethod
    def _validate_abandon_colony_inputs(form, request, cities):
        city_id = str(form.cleaned_data.get("city_id") or "").strip()
        if not city_id:
            return "Selecione a colonia que sera abandonada."

        city_lookup = {
            str(city.get("id") or ""): city
            for city in (cities or [])
            if city.get("id") is not None
        }
        city = city_lookup.get(city_id)
        if not city:
            return "A cidade selecionada nao foi encontrada na conta."

        buildings = city.get("buildings") or []
        if any(str(item.get("building") or "").strip() == "palace" for item in buildings if isinstance(item, dict)):
            return "A cidade escolhida parece ser a capital. O abandono manual permite apenas colonias."

        acknowledge = str(request.POST.get("acknowledge_irreversible") or "").strip().lower()
        confirm_colony = str(request.POST.get("confirm_colony_only") or "").strip().lower()
        typed_phrase = str(request.POST.get("confirm_phrase_input") or "").strip().upper()
        typed_city_name = str(request.POST.get("confirm_city_name_input") or "").strip()
        expected_city_name = str(city.get("name") or "").strip()

        if acknowledge not in {"on", "true", "1", "yes"}:
            return "Marque que entendeu que o abandono e irreversivel."
        if confirm_colony not in {"on", "true", "1", "yes"}:
            return "Confirme explicitamente que a cidade alvo e uma colonia descartavel."
        if typed_phrase != "ABANDONAR":
            return 'Digite exatamente "ABANDONAR" para liberar esta acao.'
        if expected_city_name and typed_city_name != expected_city_name:
            return f'Digite o nome exato da cidade: "{expected_city_name}".'
        return None

    @staticmethod
    def _get_city_name(city_id, cities):
        for city in cities:
            if str(city.get("id")) == str(city_id):
                return city.get("name", "")
        return ""

    @staticmethod
    def _normalize_piracy_inputs(inputs, request):
        normalized = dict(inputs)
        bool_fields = (
            "enable_targeted_cycle",
            "target_require_lower_total_score",
            "target_require_lower_general_score",
            "target_require_eta_efficiency",
        )
        int_fields = (
            "targeted_interval_days",
            "targeted_after_hour",
            "target_max_score_gap",
            "targeted_max_active_foundations",
            "targeted_compare_mission_level",
            "targeted_convert_percent",
        )
        for key in bool_fields:
            raw = str(request.POST.get(key) or normalized.get(key) or "").strip().lower()
            normalized[key] = raw in {"1", "true", "on", "yes"}
        for key in int_fields:
            raw = request.POST.get(key, normalized.get(key))
            try:
                normalized[key] = int(raw)
            except (TypeError, ValueError):
                pass
        targeted_convert_mode = str(request.POST.get("targeted_convert_mode", normalized.get("targeted_convert_mode") or "all")).strip().lower()
        if targeted_convert_mode not in {"all", "percent", "never"}:
            targeted_convert_mode = "all"
        normalized["targeted_convert_mode"] = targeted_convert_mode
        return normalized

    @staticmethod
    def _normalize_train_units_inputs(inputs, request):
        """Collect units_XXX POST fields into inputs['units'] dict."""
        normalized = dict(inputs)
        mode = str(normalized.get("mode") or "once")
        maintain_scope = str(normalized.get("maintain_scope") or "local")
        units = {}
        for key, val in request.POST.items():
            if key.startswith("units_"):
                try:
                    uid = key[6:]
                    qty = int(val or 0)
                    if qty > 0:
                        units[uid] = qty
                except (ValueError, TypeError):
                    pass
        selected_city_ids = [str(v).strip() for v in request.POST.getlist("city_ids") if str(v).strip()]
        selected_city_ids = list(dict.fromkeys(selected_city_ids))
        if selected_city_ids:
            normalized["city_ids"] = selected_city_ids
        consolidate_city_id = str(request.POST.get("consolidate_city_id") or "").strip()
        if consolidate_city_id:
            normalized["consolidate_city_id"] = consolidate_city_id
        if mode == "maintain":
            normalized["target_garrison"] = units
            normalized["maintain_scope"] = maintain_scope
            city_targets_raw = str(request.POST.get("city_targets_json") or "").strip()
            if city_targets_raw:
                try:
                    city_targets = json.loads(city_targets_raw)
                except Exception:
                    city_targets = {}
                if isinstance(city_targets, dict) and city_targets:
                    normalized["city_targets"] = city_targets
        else:
            normalized["units"] = units
        return normalized

    @staticmethod
    def _normalize_raid_inputs(inputs, request):
        """Collect units_XXX POST fields into inputs['units'] dict for ac=1008."""
        normalized = dict(inputs)
        units = {}
        for key, val in request.POST.items():
            if key.startswith("units_"):
                try:
                    uid = key[6:]
                    qty = int(val or 0)
                    if qty > 0:
                        units[uid] = qty
                except (ValueError, TypeError):
                    pass
        if units:
            normalized["units"] = units
        # Booleans
        normalized["multi_trip"] = str(request.POST.get("multi_trip") or normalized.get("multi_trip") or "true").lower() in {"1", "true", "on", "yes"}
        normalized["needs_blockade"] = str(request.POST.get("needs_blockade") or "false").lower() in {"1", "true", "on", "yes"}
        # Ints
        for key in ("transporters", "wall_level", "max_trips", "min_resources_to_continue"):
            raw = request.POST.get(key, normalized.get(key))
            try:
                normalized[key] = int(raw)
            except (TypeError, ValueError):
                pass
        return normalized

    @staticmethod
    def _normalize_station_units_inputs(inputs, request):
        """Collect units_XXX POST fields into inputs['units'] dict for station."""
        normalized = dict(inputs)
        normalized["scope"] = str(normalized.get("scope") or request.POST.get("scope") or "troops")
        units = {}
        for key, val in request.POST.items():
            if key.startswith("units_"):
                try:
                    uid = key[6:]
                    qty = int(val or 0)
                    if qty > 0:
                        units[uid] = qty
                except (ValueError, TypeError):
                    pass
        normalized["units"] = units
        return normalized

    @staticmethod
    def _error(message):
        trigger = json.dumps({"toast": {"type": "error", "message": message}})
        resp = HttpResponse(
            f'<div class="p-5 text-center"><p class="text-sm text-[var(--ik-bad)]">{message}</p></div>'
        )
        resp["HX-Trigger"] = trigger
        return resp


class BmScanCreateView(LoginRequiredMixin, View):
    """POST: create a 808 (Verificar Ofertas) job for inline scan from the 804 form."""

    def post(self, request):
        from django.http import JsonResponse

        ga_id = request.POST.get("ga") or request.GET.get("ga")
        city_id = request.POST.get("city_id", "").strip()

        if not ga_id or not city_id:
            return JsonResponse({"error": "ga e city_id obrigatorios"}, status=400)

        try:
            ga = GameAccount.objects.select_related("account").get(pk=ga_id, active=True)
        except GameAccount.DoesNotExist:
            return JsonResponse({"error": "GA nao encontrado"}, status=404)

        job = create_job_with_workflow(
            account=ga.account,
            game_account=ga,
            node=ga.account.node,
            action_code=808,
            inputs={"buyer_city_id": city_id, "unit_category": "0"},
            status="queued",
            created_by=request.user,
        )

        return JsonResponse({"job_id": str(job.pk), "status": "queued"})


class BmScanStatusView(LoginRequiredMixin, View):
    """GET: poll 808 job status; when done return fresh offers for the scanned city."""

    def get(self, request):
        from django.http import JsonResponse
        from apps.market.models import BlackMarketAvailableOffer, BlackMarketUnitQuote

        job_id = request.GET.get("job_id", "").strip()
        if not job_id:
            return JsonResponse({"error": "job_id obrigatorio"}, status=400)

        try:
            job = Job.objects.select_related("game_account").get(pk=job_id)
        except Job.DoesNotExist:
            return JsonResponse({"error": "Job nao encontrado"}, status=404)

        if job.status in ("queued", "running", "scheduled"):
            return JsonResponse({"status": "running"})

        if job.status == "error":
            return JsonResponse({"status": "error", "message": "Job terminou com erro."})

        if job.status in ("cancelled",):
            return JsonResponse({"status": "error", "message": "Job cancelado."})

        # status == "finished" — load fresh offers from DB
        try:
            inputs = json.loads(job.inputs_json or "{}")
        except Exception:
            inputs = {}
        buyer_city_id = str(inputs.get("buyer_city_id") or "").strip()

        ga = job.game_account

        # unit_name lookup
        unit_names: dict[int, str] = {}
        try:
            for q in (BlackMarketUnitQuote.objects
                      .filter(game_account=ga)
                      .order_by("unit_id", "-captured_at")
                      .values("unit_id", "unit_name")):
                uid = q["unit_id"]
                if uid not in unit_names and q["unit_name"]:
                    unit_names[uid] = q["unit_name"]
        except Exception:
            pass

        offers_list: list[dict] = []
        scanned_at_str = ""
        try:
            qs = (BlackMarketAvailableOffer.objects
                  .filter(game_account=ga, buyer_city_id=buyer_city_id)
                  .order_by("unit_id", "price_per_unit", "seller_avatar", "seller_city_name")
                  .values("seller_city_id", "seller_city_name", "seller_avatar",
                          "unit_id", "unit_category", "amount", "price_per_unit", "scanned_at"))
            for row in qs:
                uid = row["unit_id"]
                uname = unit_names.get(uid, "")
                offers_list.append({
                    "unit_id": uid,
                    "unit_name": uname,
                    "unit_icon": _resolve_unit_icon(uid, uname),
                    "unit_category": row["unit_category"],
                    "seller_city_id": row["seller_city_id"],
                    "seller_city_name": row["seller_city_name"],
                    "seller_avatar": row["seller_avatar"],
                    "amount": row["amount"],
                    "price_per_unit": row["price_per_unit"],
                })
                if not scanned_at_str and row["scanned_at"]:
                    scanned_at_str = row["scanned_at"].strftime("%d/%m/%Y %H:%M")
        except Exception:
            pass

        return JsonResponse({
            "status": "done",
            "offers": offers_list,
            "scanned_at": scanned_at_str,
        })
