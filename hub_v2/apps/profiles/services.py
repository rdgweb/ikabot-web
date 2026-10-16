"""
Preset execution service — creates jobs for all (game_account, action) pairs.
"""

import json
import logging

from apps.jobs.services.workflows import create_job_with_workflow
from core.contracts import ACTION_CATALOG

from .models import Preset


def _city_select_fields(action_code: int) -> list[dict]:
    meta = ACTION_CATALOG.get(int(action_code), {})
    return [f for f in meta.get("inputs", []) if f.get("type") == "city_select"]

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


def _spawn_training(ga, inputs: dict, created_by) -> int:
    from apps.game.models import AccountSnapshot
    from apps.jobs.services.training import create_training_jobs
    from apps.jobs.views.create import _get_cities, _training_form_context

    snapshot = AccountSnapshot.objects.filter(game_account=ga).first()
    cities = _training_form_context(snapshot, _get_cities(ga)).get("training_cities") or []
    inputs = {k: v for k, v in inputs.items() if k not in ("city_id", "city", "consolidate_city_id", "city_targets")}
    inputs["game_account"] = str(ga.pk)
    return create_training_jobs(ga, inputs, cities, created_by=created_by)


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
        return 0, ["Nenhuma ação configurada no preset."]

    jobs_created = 0
    errors: list[str] = []
    preset_ga_ids = {str(pga.game_account_id) for pga in preset_accounts}

    for pa in preset_actions:
        if int(pa.action_code) == 35:
            try:
                saved = json.loads(pa.inputs_json or "{}")
            except Exception:
                saved = {}
            if str(saved.get("target_mode") or "") == "island_faith":
                # N-141: one plan for every island of the preset's accounts, balanced across them
                from apps.game.services.temples import balance_jobs, islands_for_organization

                islands = [i for i in islands_for_organization() if any(t.game_account_id in preset_ga_ids for t in i.own)]
                try:
                    target = max(1, min(100, int(saved.get("target_value") or 100)))
                except (TypeError, ValueError):
                    target = 100
                try:
                    reserve = max(0, int(saved.get("reserve_citizens") or 0))
                except (TypeError, ValueError):
                    reserve = 0
                jobs_created += balance_jobs(islands, target=target, reserve=reserve, created_by=created_by)
                continue

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

            if int(pa.action_code) == 1005:
                # N-138: the preset holds a city rule; each account gets its own cities, troops and fleet
                # in one workflow
                try:
                    jobs_created += _spawn_training(ga, inputs, created_by)
                except Exception as exc:
                    msg = f"Erro ao criar job para {ga} / ac=1005: {exc}"
                    logger.warning(msg)
                    errors.append(msg)
                continue
            if int(pa.action_code) == 1203:
                # N-138: every city with a Workshop (the runner's default when no city is given)
                inputs.pop("city_id", None)
                inputs.pop("city", None)
                inputs["game_account"] = str(ga.pk)
                try:
                    create_job_with_workflow(account=account, game_account=ga, node=node, profile=None,
                                             action_code=1203, inputs=inputs, status="queued", start_new_run=True,
                                             trigger_type="manual", created_by=created_by)
                    jobs_created += 1
                except Exception as exc:
                    msg = f"Erro ao criar job para {ga} / ac=1203: {exc}"
                    logger.warning(msg)
                    errors.append(msg)
                continue

            ga_key = str(ga.pk)
            ga_override = per_account.get(ga_key, {})

            city_fields = _city_select_fields(pa.action_code)
            multi_field = next((f for f in city_fields if f.get("multiple")), None)
            single_field = next((f for f in city_fields if not f.get("multiple")), None)

            # Some runners plan across all selected cities in one job (transport
            # distribution, academy scientists, island/world monitors) — everyone
            # else (e.g. donate_loop) processes exactly one city per job and
            # expects a singular "city_id" input, so the multi-select list must be
            # fanned out into one job per city (mirrors JobSubmitView._create_jobs).
            NO_FANOUT_ACTION_CODES = {3, 27, 35, 601, 602}
            fans_out = bool(multi_field) and int(pa.action_code) not in NO_FANOUT_ACTION_CODES

            fanout_city_ids: list[str] = []

            if city_mode == "all":
                city_ids = _resolve_all_cities(ga) or []
                if multi_field:
                    if fans_out:
                        fanout_city_ids = city_ids
                    else:
                        inputs[multi_field["key"]] = city_ids
                elif single_field and city_ids:
                    auto_id = _resolve_auto_city(ga, pa.action_code) or city_ids[0]
                    inputs[single_field["key"]] = auto_id
            elif city_mode == "auto":
                city_id = _resolve_auto_city(ga, pa.action_code)
                if city_id:
                    if single_field:
                        inputs[single_field["key"]] = city_id
                    if multi_field:
                        if fans_out:
                            fanout_city_ids = [city_id]
                        else:
                            inputs[multi_field["key"]] = [city_id]
                    inputs.setdefault("city_id", city_id)
                    inputs.setdefault("city", city_id)
            elif city_mode == "per_account" and ga_override:
                for k, v in ga_override.items():
                    selected = _resolve_all_cities(ga) or [] if v == "__all__" else v
                    if fans_out and multi_field and k == multi_field["key"]:
                        fanout_city_ids = selected if isinstance(selected, list) else [selected]
                    else:
                        inputs[k] = selected

            if fanout_city_ids and multi_field:
                inputs.pop(multi_field["key"], None)

            inputs["game_account"] = str(ga.pk)

            # donate_loop (ac=902/1006) takes a single donation_type string per
            # job, not the multi-select list saved on the preset action — fan out
            # one job per donation type too (mirrors JobSubmitView._create_jobs_for_city).
            donation_types = inputs.pop("donation_type", None)
            if donation_types and not isinstance(donation_types, list):
                donation_types = [donation_types]

            def _spawn(job_inputs: dict) -> None:
                nonlocal jobs_created
                try:
                    create_job_with_workflow(
                        account=account,
                        game_account=ga,
                        node=node,
                        profile=None,
                        action_code=pa.action_code,
                        inputs=job_inputs,
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

            def _spawn_with_donation_types(job_inputs: dict) -> None:
                if donation_types:
                    for donation_type in donation_types:
                        _spawn({**job_inputs, "donation_type": donation_type})
                else:
                    _spawn(job_inputs)

            if fanout_city_ids:
                for city_id in fanout_city_ids:
                    per_city_inputs = dict(inputs)
                    per_city_inputs["city_id"] = str(city_id)
                    _spawn_with_donation_types(per_city_inputs)
            else:
                _spawn_with_donation_types(inputs)

    return jobs_created, errors


def catalog_presets() -> dict[str, list[dict]]:
    """N-142: the presets chosen to show up in Ações do Jogo, by category (the request's organization scope applies)."""
    from core.actions.registry import _plain

    from .models import Preset

    by_category: dict[str, list[dict]] = {}
    presets = (Preset.objects.exclude(catalog_category="").prefetch_related("actions", "preset_accounts").order_by("name"))
    for preset in presets:
        actions = [a.action_name or str(a.action_code) for a in preset.actions.all()]
        accounts = len(preset.preset_accounts.all())
        by_category.setdefault(preset.catalog_category, []).append({
            "pk": preset.pk,
            "name": preset.name,
            "description": preset.description,
            "actions": actions,
            "accounts": accounts,
            "search": _plain(" ".join([preset.name, preset.description, "preset", *actions])),
        })
    return by_category
