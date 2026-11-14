"""N-186: ask before extending recurring jobs to newly observed cities.

The first dispatch establishes the roster; an existing subset never opts in.
Consent is durable, scoped to a workflow/chain, and consumed at a later dispatch.
No running job is rewritten by a Telegram callback.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from html import escape

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.game.models import AccountSnapshot
from apps.jobs.models import CityExpansionProposal, CityExpansionScope, Job, Workflow
from core.contracts import ACTION_CATALOG

logger = logging.getLogger(__name__)
ACTIVE = ("queued", "scheduled", "running")


def _ids(value):
    return sorted({str(v) for v in value if str(v).isdigit()}) if isinstance(value, (list, tuple)) else []


def _inputs(job):
    from .workflows import _normalize_inputs
    return _normalize_inputs(job.inputs_json)[0]


def _cities(game_account_id):
    snapshot = AccountSnapshot._base_manager.filter(game_account_id=game_account_id).first()
    if snapshot is None or not snapshot.full_snapshot_updated_at:
        return {}
    raw = snapshot.cities
    if isinstance(raw, dict):
        raw = [dict(v, id=v.get("id") or k) for k, v in raw.items() if isinstance(v, dict)]
    if not isinstance(raw, list):
        return {}
    return {str(c["id"]): str(c.get("name") or c["id"]) for c in raw
            if isinstance(c, dict) and str(c.get("id", "")).isdigit()}


def _kind(job, inputs):
    if inputs.get("loop_enabled") is False:
        return None
    if job.action_code == 702:
        from apps.accounts.services.agent_versions import parse_version
        if (parse_version(job.node.agent_version) or (0, 0, 0)) < (0, 1, 159):
            return None  # Older wine runners do not enforce a frozen city roster.
        return "city_ids", str(job.root_job_id or job.pk)
    if job.action_code == 1005:
        if inputs.get("mode") == "maintain" and inputs.get("maintain_scope") == "selected_uniform":
            return "city_ids", str(job.root_job_id or job.pk)
        # Custom targets require a city-specific decision in the form.
        return None
    if job.action_code == 902:
        return "city_id", "donation:" + str(inputs.get("donation_type", ""))
    action = ACTION_CATALOG.get(job.action_code, {})
    if action.get("recurring"):
        for field in action.get("inputs", []):
            if field.get("key") == "cities" and field.get("multiple") and isinstance(inputs.get("cities"), list):
                return "cities", str(job.root_job_id or job.pk)
    return None


def _donations(job, inputs):
    """Only a uniform all-city donation group has an unambiguous template."""
    fields = [f["key"] for f in ACTION_CATALOG[902]["inputs"] if f["key"] != "cities"]
    signature = {k: inputs.get(k) for k in fields}
    rows = []
    for candidate in Job._base_manager.filter(workflow_id=job.workflow_id, action_code=902, status__in=ACTIVE):
        params = _inputs(candidate)
        if {k: params.get(k) for k in fields} == signature:
            rows.append((candidate, params))
    return rows


def prepare_city_scope(job, inputs):
    """Called by both HTTPS and broker dispatch. Return this execution's inputs."""
    kind = _kind(job, inputs)
    if not kind or not job.workflow_id or not job.game_account_id or job.status not in ("queued", "scheduled"):
        return inputs
    cities = _cities(job.game_account_id)
    if not cities:
        return inputs
    field, key = kind
    selected = (_ids([p.get("city_id") for _, p in _donations(job, inputs)]) if field == "city_id"
                else _ids(inputs.get(field, list(cities) if job.action_code == 702 else [])))
    with transaction.atomic():
        scope, _ = CityExpansionScope._base_manager.get_or_create(
            workflow_id=job.workflow_id, key=key,
            defaults={"action_code": job.action_code, "field": field, "known_ids": sorted(cities),
                      "selected_ids": selected, "approved_ids": selected,
                      "enabled": bool(selected) and set(selected) == set(cities)},
        )
        scope = CityExpansionScope._base_manager.select_for_update().get(pk=scope.pk)
        if not scope.enabled:
            # Split jobs can be dispatched while the form is still creating the other cities.
            if field == "city_id" and set(selected) == set(cities) and not scope.proposals.exists():
                scope.enabled = True
                scope.selected_ids = scope.approved_ids = selected
                scope.known_ids = sorted(cities)
            else:
                return inputs
        if field != "city_id" and selected != scope.selected_ids:
            # A manual scope edit takes precedence over a previous approval.
            scope.enabled = False
            scope.save(update_fields=["enabled", "updated_at"])
            scope.proposals.filter(status="pending").update(status="expired")
            return inputs
        if field == "city_id" and not set(scope.selected_ids).issubset(selected):
            # A city stopped donating or was given different settings since the baseline.
            return inputs
        new_ids = set(cities) - set(scope.known_ids)
        for city_id in sorted(new_ids):
            CityExpansionProposal._base_manager.get_or_create(
                scope=scope, city_id=city_id,
                defaults={"city_name": cities[city_id][:255], "template_root_id": job.root_job_id or job.pk},
            )
        scope.known_ids = sorted(set(scope.known_ids) | set(cities))
        if field != "city_id":
            updated = dict(inputs, **{field: scope.approved_ids})
            # Compare-and-swap preserves edits/rescheduling arriving during dispatch.
            if not Job._base_manager.filter(pk=job.pk, inputs_json=job.inputs_json, status__in=("queued", "scheduled")).update(inputs_json=json.dumps(updated)):
                return _inputs(Job._base_manager.get(pk=job.pk))
            inputs = updated
            job.inputs_json = json.dumps(updated)
            scope.selected_ids = scope.approved_ids
        scope.save()
        transaction.on_commit(lambda: notify_pending(scope.pk))
    return inputs


def notify_pending(scope_id):
    from apps.telegram.services.bot_api import send_message
    from apps.telegram.services.config import destination_chat

    for proposal in CityExpansionProposal._base_manager.filter(scope_id=scope_id, status="pending", sent_at=None).select_related("scope__workflow__game_account"):
        workflow = proposal.scope.workflow
        chat = destination_chat(workflow.game_account)
        if not chat:
            continue
        now = timezone.now()
        reserved = CityExpansionProposal._base_manager.filter(pk=proposal.pk, status="pending", sent_at=None).filter(
            Q(last_attempt_at=None) | Q(last_attempt_at__lt=now - timedelta(minutes=5))
        ).update(last_attempt_at=now, chat_id=chat)
        if not reserved:
            continue
        name = ACTION_CATALOG.get(proposal.scope.action_code, {}).get("name", "Rotina")
        text = (f"🏙 <b>Nova cidade: {escape(proposal.city_name)}</b>\n"
                f"Conta: {escape(str(workflow.game_account))}\nRotina: {escape(name)}\n"
                "Incluir nas próximas execuções com a mesma configuração das cidades atuais? "
                "A execução em andamento não muda.")
        try:
            result = send_message(chat, text, reply_markup={"inline_keyboard": [[
                {"text": "Incluir cidade", "callback_data": f"city_yes:{proposal.pk}"},
                {"text": "Não incluir", "callback_data": f"city_no:{proposal.pk}"},
            ]]})
            if result.get("ok"):
                CityExpansionProposal._base_manager.filter(pk=proposal.pk).update(sent_at=now)
        except Exception:
            logger.exception("Could not send city expansion proposal %s", proposal.pk)


def answer_proposal(proposal_id, *, chat_id, accept, user_id=""):
    from apps.telegram.services.config import destination_chat

    # The default manager enforces the webhook's organization scope as well as the chat check.
    try:
        proposal = CityExpansionProposal.objects.select_related("scope__workflow__game_account").get(pk=proposal_id)
    except (CityExpansionProposal.DoesNotExist, ValueError, ValidationError):
        return False, "Solicitação não encontrada."
    with transaction.atomic():
        scope = CityExpansionScope.objects.select_for_update().get(pk=proposal.scope_id)
        proposal.refresh_from_db()
        workflow = Workflow.objects.select_for_update().get(pk=scope.workflow_id)
        if str(chat_id) != proposal.chat_id or str(chat_id) != destination_chat(workflow.game_account):
            return False, "Este chat não pode responder pela conta."
        if proposal.status != "pending":
            return False, "Esta solicitação já foi respondida ou expirou."
        jobs = Job._base_manager.filter(workflow=workflow, action_code=scope.action_code, status__in=ACTIVE).filter(
            Q(pk=proposal.template_root_id) | Q(root_job_id=proposal.template_root_id)
        ).order_by("-created_at")
        template = jobs.first()
        if not scope.enabled or workflow.status in ("finished", "cancelled", "paused") or template is None or proposal.city_id not in _cities(workflow.game_account_id):
            proposal.status = "expired"
            proposal.save(update_fields=["status", "updated_at"])
            return False, "A rotina ou a cidade mudou; solicitação expirada."
        if accept:
            params = _inputs(template)
            pending = params.get("__pending_patch") or {}
            if _kind(template, params) != (scope.field, scope.key):
                return False, "A configuração da rotina mudou. Revise no Hub."
            if scope.field != "city_id" and (_ids(params.get(scope.field, scope.selected_ids)) != scope.selected_ids or scope.field in pending):
                return False, "A seleção de cidades foi editada. Revise a rotina no Hub."
            if scope.field == "city_id":
                if not set(scope.selected_ids).issubset(_ids([p.get("city_id") for _, p in _donations(template, params)])) or pending:
                    return False, "As configurações de doação mudaram. Revise no Hub."
                _create_donation(proposal, template, params)
            scope.approved_ids = sorted(set(scope.approved_ids) | {proposal.city_id})
            scope.save(update_fields=["approved_ids", "updated_at"])
        proposal.status = "accepted" if accept else "declined"
        proposal.answered_by = str(user_id)[:64]
        proposal.save(update_fields=["status", "answered_by", "updated_at"])
    return True, "Cidade incluída nas próximas execuções." if accept else "Cidade não incluída."


def _create_donation(proposal, template, params):
    from .workflows import create_job_with_workflow

    # Copy configuration only, never carry-over, recovery or progress from another city.
    fields = {f["key"] for f in ACTION_CATALOG[902]["inputs"]} - {"cities"}
    new_inputs = {k: v for k, v in params.items() if k in fields}
    new_inputs.update(city_id=proposal.city_id, city_name=proposal.city_name)
    for candidate in Job._base_manager.filter(game_account_id=template.game_account_id, action_code=902, status__in=ACTIVE):
        existing = _inputs(candidate)
        if str(existing.get("city_id")) == proposal.city_id and str(existing.get("donation_type")) == str(params.get("donation_type")):
            return
    create_job_with_workflow(
        account=template.account, game_account=template.game_account, node=template.node,
        profile=template.profile, action_code=902, inputs=new_inputs, status="scheduled",
        scheduled_for=timezone.now() + timedelta(minutes=1), timeout_sec=template.timeout_sec,
        explicit_workflow=template.workflow, trigger_type="city_expansion",
    )
