"""
Preset execution service — creates jobs for all (game_account, action) pairs.
"""

import json
import logging

from apps.jobs.services.workflows import create_job_with_workflow

from .models import Preset

# Buildings that are relevant for __auto__ city detection per action code
_AUTO_BUILDING_MAP = {
    17: ("pirateFortress", "reducePiracy"),   # Piracy
    11: ("temple", "shrineOfOlympus"),         # Miracle
    5:  ("temple", "shrineOfOlympus"),         # Shrine loop
    1007: ("temple", "shrineOfOlympus"),
    1203: ("workshop",),                       # Workshop
}


def _resolve_all_cities(ga) -> list | None:
    """Return all city IDs for a game account from its snapshot."""
    try:
        from apps.game.models import AccountSnapshot
        snap = AccountSnapshot.objects.filter(game_account=ga).order_by("-updated_at").first()
        if not snap:
            return None
        import json
        cities = snap.cities if isinstance(snap.cities, list) else json.loads(snap.cities or "[]")
        return [str(c["id"]) for c in cities if c.get("id")]
    except Exception:
        return None


def _resolve_auto_city(ga, action_code: int) -> str | None:
    """Find the city that has the relevant building for the action."""
    try:
        from apps.game.models import AccountSnapshot
        import json
        snap = AccountSnapshot.objects.filter(game_account=ga).order_by("-updated_at").first()
        if not snap:
            return None
        cities = snap.cities if isinstance(snap.cities, list) else json.loads(snap.cities or "[]")
        target_buildings = _AUTO_BUILDING_MAP.get(action_code, ())
        for city in cities:
            for bld in (city.get("buildings") or []):
                if str(bld.get("building") or "").strip() in target_buildings:
                    return str(city["id"])
        # Fallback: first city
        return str(cities[0]["id"]) if cities else None
    except Exception:
        return None

logger = logging.getLogger(__name__)


def execute_preset(preset: Preset, created_by=None) -> tuple[int, list[str]]:
    """
    Execute a preset by creating one job per (game_account, preset_action) combination.

    Returns:
        (jobs_created_count, error_messages)
    """
    preset_accounts = list(
        preset.preset_accounts.select_related(
            "game_account",
            "game_account__account",
            "game_account__account__node",
        ).all()
    )
    preset_actions = list(preset.actions.all())

    if not preset_accounts:
        return 0, ["Nenhuma conta selecionada no preset."]
    if not preset_actions:
        return 0, ["Nenhuma acao configurada no preset."]

    jobs_created = 0
    errors: list[str] = []

    for pa in preset_actions:
        # Parse per-account overrides
        try:
            per_account = json.loads(pa.per_account_json or "{}")
        except Exception:
            per_account = {}

        # Parse default inputs
        try:
            default_inputs = json.loads(pa.inputs_json or "{}")
        except Exception:
            default_inputs = {}

        city_mode = per_account.get("__mode__", "all")

        for pga in preset_accounts:
            ga = pga.game_account
            account = ga.account
            node = account.node  # Account.node is a real FK

            # Start with global inputs
            inputs = dict(default_inputs)

            ga_key = str(ga.pk)
            ga_override = per_account.get(ga_key, {})

            # Resolve city fields based on mode
            if city_mode == "all":
                # Expand to all city IDs from snapshot
                city_ids = _resolve_all_cities(ga)
                if city_ids is not None:
                    inputs["cities"] = city_ids
            elif city_mode == "auto":
                # Auto-detect relevant city (e.g. pirate fortress, shrine)
                city_id = _resolve_auto_city(ga, pa.action_code)
                if city_id:
                    inputs["city_id"] = city_id
                    inputs["city"] = city_id
            elif city_mode == "per_account" and ga_override:
                # Merge per-account city fields
                for k, v in ga_override.items():
                    if v == "__all__":
                        inputs[k] = _resolve_all_cities(ga) or []
                    else:
                        inputs[k] = v

            # Ensure game_account is set
            inputs["game_account"] = str(ga.pk)

            try:
                create_job_with_workflow(
                    account=account,
                    game_account=ga,
                    node=node,
                    profile=None,
                    action_code=pa.action_code,
                    inputs=inputs,
                    status="queued",
                    start_new_run=True,
                    trigger_type="manual",
                    created_by=created_by,
                )
                jobs_created += 1
            except Exception as exc:
                msg = f"Erro ao criar job para {ga} / ac={pa.action_code}: {exc}"
                logger.warning(msg)
                errors.append(msg)

    return jobs_created, errors
