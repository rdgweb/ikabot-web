"""Authorize resources before any agent view or service can use a base manager."""
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework.exceptions import NotFound, PermissionDenied


def _require(model, value, condition):
    try:
        allowed = apps.get_model(model)._base_manager.filter(pk=value).filter(condition).exists()
    except (ValueError, TypeError, ValidationError):
        allowed = False
    if not allowed:
        raise NotFound("Recurso não encontrado para este nó.")


def authorize_resources(request, view, node):
    org = node.organization_id
    account = Q(node_id=node.pk, organization_id=org)
    game_account = Q(account__node_id=node.pk, account__organization_id=org)
    own_job = Q(node_id=node.pk, account__organization_id=org)
    specs = {
        "account_id": ("accounts.Account", account),
        "game_account_id": ("accounts.GameAccount", game_account),
        "ga_id": ("accounts.GameAccount", game_account),
        "job_id": ("jobs.Job", own_job),
        "route_id": ("piracy.PiracyRoute", Q(raider__account__node_id=node.pk, raider__account__organization_id=org, month__organization_id=org)),
        "dump_id": ("worldintel.WorldDump", Q(account__node_id=node.pk, account__organization_id=org)),
        "config_id": ("generals_bank.GeneralsBankConfig", Q(bank_game_account__account__node_id=node.pk, bank_game_account__account__organization_id=org)),
        "cycle_id": ("generals_bank.GeneralsBankCycle", Q(bank_config__bank_game_account__account__node_id=node.pk, bank_config__bank_game_account__account__organization_id=org)),
        "task_id": ("generals_bank.GeneralsBankCycleTask", Q(producer_game_account__account__node_id=node.pk, producer_game_account__account__organization_id=org, cycle__bank_config__bank_game_account__account__organization_id=org)),
        "offer_id": ("market.BlackMarketOffer", Q(game_account__account__node_id=node.pk, game_account__account__organization_id=org)),
    }
    if request.path.endswith("/spawn/") and isinstance(request.data, dict):
        # A world-spy coordinator delegates only its existing helper actions;
        # the Hub routes them to the owning node, never giving it credentials.
        try:
            action = int(request.data.get("action_code", 0))
        except (TypeError, ValueError):
            action = 0
        if action in (13, 15, 100) and apps.get_model("jobs.Job")._base_manager.filter(
                own_job, pk=view.kwargs.get("job_id"), action_code=16).exists():
            specs["game_account_id"] = ("accounts.GameAccount", Q(account__organization_id=org))
    if request.path.endswith("/notify/") and isinstance(request.data, dict):
        child = request.data.get("child_done")
        if isinstance(child, dict) and child.get("child_job_id"):
            parent_id = view.kwargs.get("job_id")
            _require("jobs.Job", child["child_job_id"], own_job & (Q(root_job_id=parent_id) | Q(source_job_id=parent_id)))
            specs["job_id"] = ("jobs.Job", Q(account__organization_id=org))
    if "order_id" in view.kwargs:
        # The seller reports listing; the buyer reports completion/failure.
        side = "seller" if request.path.endswith("/sell-complete/") else "buyer"
        specs["order_id"] = ("market.InternalMarketOrder", Q(**{side+"_node_id":node.pk,side+"_account__organization_id":org}))
    if "pk" in view.kwargs and "/captcha/challenge/" in request.path:
        specs["pk"] = ("captcha.CaptchaChallenge", Q(node_id=node.pk) | Q(
            node__isnull=True, game_account__account__node_id=node.pk, game_account__account__organization_id=org))
    if set(view.kwargs) - set(specs) - {"node_id"}:
        raise PermissionDenied("Política de recurso não definida para esta rota.")
    for key in ("source_job_id", "root_job_id", "parent_job_id", "manager_job_id", "sell_job_id", "action_job_id", "exclude_job_id"):
        # Supporting jobs may live on another node, but never another tenant.
        specs[key] = ("jobs.Job", Q(account__organization_id=org))
    sources = [view.kwargs, request.query_params]
    if isinstance(request.data, dict):
        sources.append(request.data)
    for source in sources:
        for key, (model, condition) in specs.items():
            values = source.getlist(key) if hasattr(source, "getlist") else [source.get(key)]
            for value in values:
                if value not in (None, ""):
                    _require(model, value, condition)
    # References in a bank's purchase result may belong to its producer on a
    # different node. They still must be in the bank's organization.
    if isinstance(request.data, dict):
        for item in request.data.get("purchases", []) if isinstance(request.data.get("purchases"), list) else []:
            if isinstance(item, dict):
                if item.get("producer_ga_id"):
                    _require("accounts.GameAccount", item["producer_ga_id"], Q(account__organization_id=org))
                if item.get("job_id"):
                    _require("jobs.Job", item["job_id"], Q(account__organization_id=org))


def operation_state(node):
    from apps.billing.services import state_for
    if not node.organization_id:
        return {"operation_allowed":False,"reason":"Nó sem organização.","state":"unassigned"}
    state = state_for(node.organization)
    return {"operation_allowed": bool(node.active and state.allows_work),
            "reason": "" if node.active and state.allows_work else (state.label if node.active else "Nó desativado"),
            "state": state.code, "ends_at": state.ends_at.isoformat() if state.ends_at else None}


def authorize_operation(request, view, node):
    state = operation_state(node)
    if state["operation_allowed"]:
        return
    # These routes convey no credentials and initiate no game operation.
    if request.path in ("/api/agent/register/", "/api/agent/heartbeat/", "/api/agent/operation/"):
        return
    if request.path.endswith("/status/") and "job_id" in view.kwargs and isinstance(request.data, dict):
        if request.data.get("status") in ("finished", "error", "cancelled"):
            return  # let a completed in-flight action report its final outcome
    raise PermissionDenied({"code":"operation_blocked", **state})
