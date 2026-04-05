from .admin import ACTIONS as ADMIN_ACTIONS
from .automation import ACTIONS as AUTOMATION_ACTIONS
from .constants import (
    CATEGORY_META,
    CAT_ADMIN,
    CAT_AUTOMATION,
    CAT_CONSTRUCTION,
    CAT_ECONOMY,
    CAT_MARKET,
    CAT_MILITARY,
    CAT_MONITORING,
)
from .construction import ACTIONS as CONSTRUCTION_ACTIONS
from .economy import ACTIONS as ECONOMY_ACTIONS
from .market import ACTIONS as MARKET_ACTIONS
from .military import ACTIONS as MILITARY_ACTIONS
from .monitoring import ACTIONS as MONITORING_ACTIONS


ACTION_CATALOG: dict[int, dict] = {}
for action_group in (
    ADMIN_ACTIONS,
    ECONOMY_ACTIONS,
    CONSTRUCTION_ACTIONS,
    MILITARY_ACTIONS,
    MARKET_ACTIONS,
    MONITORING_ACTIONS,
    AUTOMATION_ACTIONS,
):
    ACTION_CATALOG.update(action_group)


ACTIONS_BY_CATEGORY: dict[str, list[int]] = {}
for code, meta in sorted(ACTION_CATALOG.items()):
    category = meta.get("category", CAT_ADMIN)
    ACTIONS_BY_CATEGORY.setdefault(category, []).append(code)


def get_action_info(code: int) -> dict | None:
    return ACTION_CATALOG.get(code)


def get_action_contract(code: int) -> dict:
    meta = ACTION_CATALOG.get(code, {"name": f"Acao #{code}", "name_en": f"Action #{code}"})
    return {
        "code": code,
        "name": meta["name"],
        "name_en": meta.get("name_en", meta["name"]),
        "runner_engine": meta.get("runner", "generic"),
        "input_schema": meta.get("inputs", []),
        "long_running": meta.get("long_running", False),
        "recurring": meta.get("recurring", False),
    }


def get_actions_for_ui() -> list[dict]:
    groups = []
    ordered_categories = [
        CAT_ECONOMY,
        CAT_CONSTRUCTION,
        CAT_MILITARY,
        CAT_MARKET,
        CAT_MONITORING,
        CAT_AUTOMATION,
        CAT_ADMIN,
    ]
    for category_key in ordered_categories:
        codes = ACTIONS_BY_CATEGORY.get(category_key, [])
        if not codes:
            continue
        category_meta = CATEGORY_META[category_key]
        actions = []
        for code in codes:
            meta = ACTION_CATALOG[code]
            if meta.get("ui_hidden"):
                continue
            actions.append(
                {
                    "code": code,
                    "name": meta["name"],
                    "icon": meta.get("icon", "bi-play"),
                    "description": meta.get("description", ""),
                    "recurring": meta.get("recurring", False),
                    "long_running": meta.get("long_running", False),
                    "has_inputs": bool(meta.get("inputs")),
                    "ready": meta.get("ready", False),
                }
            )
        groups.append(
            {
                "key": category_key,
                "label": category_meta["label"],
                "icon": category_meta["icon"],
                "color": category_meta["color"],
                "actions": actions,
            }
        )
    return groups
