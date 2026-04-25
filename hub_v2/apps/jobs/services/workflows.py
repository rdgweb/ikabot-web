"""
Workflow domain helpers — one Workflow per root_job_id chain.
"""

import json

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.contracts import ACTION_CATALOG

from ..models import Job, Workflow, WorkflowRun


def _normalize_inputs(inputs) -> tuple[dict, str]:
    if isinstance(inputs, str):
        try:
            parsed = json.loads(inputs or "{}")
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        return parsed, json.dumps(parsed)
    parsed = dict(inputs or {})
    return parsed, json.dumps(parsed)


def _workflow_type(action_code: int, inputs: dict) -> str:
    action_code = int(action_code)
    if action_code == 1002:
        return "construction_plan"
    if action_code == 2:
        if inputs.get("monitor_mode") == "arrival_check":
            return "arrival_monitor"
        return "transport_route"
    if action_code in {801, 802}:
        return "internal_market_order"
    meta = ACTION_CATALOG.get(action_code, {})
    runner = str(meta.get("runner") or "").strip()
    if runner:
        return runner
    category = str(meta.get("category") or "generic").strip()
    return f"{category}_action_{action_code}"


def _workflow_category(action_code: int) -> str:
    return str(ACTION_CATALOG.get(int(action_code), {}).get("category") or "").strip()


def _workflow_scope(action_code: int, account, game_account, node, inputs: dict) -> dict:
    scope = {
        "account_id": str(account.pk),
        "account_label": getattr(account, "label", ""),
        "node_id": str(node.pk) if node else "",
        "node_name": getattr(node, "name", ""),
        "action_code": int(action_code),
    }
    if game_account:
        scope["game_account_id"] = str(game_account.pk)
        scope["game_account_name"] = game_account.name or game_account.server_id or ""
    city_name = str(inputs.get("city_name") or inputs.get("buyer_city_name") or inputs.get("from_city_name") or "").strip()
    city_id = str(inputs.get("city_id") or inputs.get("buyer_city_id") or inputs.get("from_city") or "").strip()
    if city_name:
        scope["city_name"] = city_name
    if city_id:
        scope["city_id"] = city_id
    if isinstance(inputs.get("cities"), list):
        scope["city_count"] = len(inputs.get("cities") or [])
    plan = inputs.get("construction_plan_json")
    if isinstance(plan, list):
        scope["plan_steps"] = len(plan)
    return scope


def _workflow_config(action_code: int, inputs: dict) -> dict:
    meta = ACTION_CATALOG.get(int(action_code), {})
    action_name = meta.get("name", f"Acao #{action_code}")
    if int(action_code) == 2 and inputs.get("monitor_mode") == "arrival_check":
        action_name = "Monitor de Chegada"
    return {
        "action_code": int(action_code),
        "action_name": action_name,
        "runner": meta.get("runner", ""),
        "recurring": bool(meta.get("recurring", False)),
        "long_running": bool(meta.get("long_running", False)),
    }


def _workflow_status_from_job(status: str) -> str:
    if status in {"queued", "running"}:
        return "active"
    if status == "scheduled":
        return "waiting"
    if status == "error":
        return "problem"
    if status == "finished":
        return "finished"
    if status == "cancelled":
        return "cancelled"
    return "draft"


def _run_status_from_job(status: str) -> str:
    if status in {"queued", "scheduled"}:
        return "scheduled"
    if status == "running":
        return "running"
    if status == "error":
        return "problem"
    if status == "finished":
        return "finished"
    if status == "cancelled":
        return "cancelled"
    return "scheduled"


def _next_run_sequence(workflow: Workflow) -> int:
    current_max = WorkflowRun.objects.filter(workflow=workflow).aggregate(max_sequence=Max("sequence"))["max_sequence"] or 0
    return int(current_max) + 1


def _resolve_created_by(created_by):
    if created_by is None:
        return None
    user_model = get_user_model()
    if isinstance(created_by, user_model):
        return created_by
    return None


def create_workflow(
    *,
    account,
    node,
    action_code: int,
    inputs,
    game_account=None,
    created_by=None,
    status: str = "draft",
    root_job_id=None,
) -> Workflow:
    inputs_dict, _ = _normalize_inputs(inputs)
    return Workflow.objects.create(
        account=account,
        game_account=game_account,
        node=node,
        created_by=_resolve_created_by(created_by),
        workflow_type=_workflow_type(action_code, inputs_dict),
        category=_workflow_category(action_code),
        scope_json=json.dumps(_workflow_scope(action_code, account, game_account, node, inputs_dict)),
        config_json=json.dumps(_workflow_config(action_code, inputs_dict)),
        status=status,
        root_job_id=root_job_id,
        last_event_at=timezone.now(),
    )


def create_workflow_run(
    *,
    workflow: Workflow,
    status: str,
    scheduled_for=None,
    trigger_type: str = "manual",
    summary: dict | None = None,
) -> WorkflowRun:
    return WorkflowRun.objects.create(
        workflow=workflow,
        sequence=_next_run_sequence(workflow),
        trigger_type=trigger_type,
        status=status,
        scheduled_for=scheduled_for,
        summary_json=json.dumps(summary or {}),
    )


_GROUPED_BY_GA_TYPE = frozenset({"donate", "donate_loop"})


def _find_or_create_workflow_for_chain(
    chain_root_id,
    account,
    game_account,
    node,
    action_code: int,
    inputs_dict: dict,
    job_status: str,
) -> Workflow:
    """Return the single Workflow for a root_job_id chain, creating if needed."""
    workflow = Workflow.objects.filter(root_job_id=chain_root_id).first()
    if workflow is not None:
        return workflow
    return create_workflow(
        account=account,
        game_account=game_account,
        node=node,
        action_code=action_code,
        inputs=inputs_dict,
        status=_workflow_status_from_job(job_status),
        root_job_id=chain_root_id,
    )


def create_job_with_workflow(
    *,
    account,
    node,
    action_code: int,
    inputs,
    status: str,
    game_account=None,
    profile=None,
    timeout_sec: int = 1800,
    source_job: Job | None = None,
    scheduled_for=None,
    created_by=None,
    explicit_workflow: Workflow | None = None,
    explicit_run: WorkflowRun | None = None,
    start_new_run: bool | None = None,
    trigger_type: str = "manual",
) -> Job:
    inputs_dict, inputs_json = _normalize_inputs(inputs)
    action_code = int(action_code)

    with transaction.atomic():
        source_job_id = None
        root_job_id_for_job = None

        if source_job is not None:
            source_job_id = source_job.pk
            root_job_id_for_job = source_job.root_job_id or source_job.pk

        wtype = _workflow_type(action_code, inputs_dict)
        use_group = wtype in _GROUPED_BY_GA_TYPE and game_account is not None

        workflow = explicit_workflow
        if workflow is None and use_group:
            workflow = Workflow.objects.filter(
                game_account=game_account,
                workflow_type=wtype,
            ).first()
        if workflow is None and source_job is not None:
            workflow = source_job.workflow
            if workflow is None and root_job_id_for_job:
                workflow = Workflow.objects.filter(root_job_id=root_job_id_for_job).first()
        if workflow is None and root_job_id_for_job and not use_group:
            workflow = _find_or_create_workflow_for_chain(
                root_job_id_for_job, account, game_account, node, action_code, inputs_dict, status
            )

        # Root jobs: create workflow without root_job_id (set after job creation)
        if workflow is None:
            workflow = create_workflow(
                account=account,
                game_account=game_account,
                node=node,
                action_code=action_code,
                inputs=inputs_dict,
                created_by=created_by,
                status=_workflow_status_from_job(status),
                root_job_id=None,
            )

        if start_new_run is None:
            start_new_run = source_job is None

        workflow_run = explicit_run
        if workflow_run is None:
            if source_job is not None and source_job.workflow_run_id and not start_new_run:
                workflow_run = source_job.workflow_run
            else:
                workflow_run = create_workflow_run(
                    workflow=workflow,
                    status=_run_status_from_job(status),
                    scheduled_for=scheduled_for,
                    trigger_type=trigger_type,
                    summary={
                        "action_code": action_code,
                        "action_name": ACTION_CATALOG.get(action_code, {}).get("name", f"Acao #{action_code}"),
                    },
                )

        job = Job.objects.create(
            account=account,
            game_account=game_account,
            node=node,
            profile=profile,
            action_code=action_code,
            workflow=workflow,
            workflow_run=workflow_run,
            source_job_id=source_job_id,
            root_job_id=root_job_id_for_job,
            inputs_json=inputs_json,
            timeout_sec=timeout_sec,
            status=status,
            scheduled_for=scheduled_for,
        )

        update_fields_wf = ["status", "active_run", "next_scheduled_for", "last_event_at",
                            "last_error_at", "last_error_summary", "updated_at"]
        if workflow.root_job_id is None and not use_group:
            workflow.root_job_id = job.pk
            update_fields_wf.append("root_job_id")

        workflow.status = _workflow_status_from_job(status)
        workflow.active_run = workflow_run
        workflow.next_scheduled_for = scheduled_for
        workflow.last_event_at = timezone.now()
        if status == "error":
            workflow.last_error_at = workflow.last_event_at
            workflow.last_error_summary = f"Job {job.pk} terminou com erro."
        workflow.save(update_fields=update_fields_wf)

        workflow_run.status = _run_status_from_job(status)
        workflow_run.scheduled_for = scheduled_for
        workflow_run.save(update_fields=["status", "scheduled_for", "updated_at"])

        return job


def ensure_workflow_for_job(job: Job, *, start_new_run: bool = False) -> tuple[Workflow, WorkflowRun]:
    """Ensure a job is linked to the single Workflow for its root chain.

    For grouped types (e.g. donate_loop), all jobs from the same game_account
    share one Workflow regardless of root_job_id.
    """
    if job.workflow_id and job.workflow_run_id and not start_new_run:
        return job.workflow, job.workflow_run

    inputs_dict, _ = _normalize_inputs(job.inputs_json)
    wtype = _workflow_type(job.action_code, inputs_dict)
    use_group = wtype in _GROUPED_BY_GA_TYPE and job.game_account_id is not None
    chain_root_id = job.root_job_id or job.pk

    with transaction.atomic():
        workflow = None

        if use_group:
            # All jobs of this type+GA share one workflow (no root_job_id anchor)
            workflow = Workflow.objects.filter(
                game_account=job.game_account,
                workflow_type=wtype,
            ).first()
        else:
            workflow = Workflow.objects.filter(root_job_id=chain_root_id).first()
            if workflow is None and job.workflow_id:
                workflow = job.workflow
                if workflow and not workflow.root_job_id:
                    workflow.root_job_id = chain_root_id
                    workflow.save(update_fields=["root_job_id", "updated_at"])

        if workflow is None:
            workflow = create_workflow(
                account=job.account,
                game_account=job.game_account,
                node=job.node,
                action_code=job.action_code,
                inputs=inputs_dict,
                status=_workflow_status_from_job(job.status),
                root_job_id=None if use_group else chain_root_id,
            )

        # Link all jobs in this chain that aren't linked yet
        if use_group:
            # Grouped types: link ALL jobs from same GA+action_code (all cities)
            Job.objects.filter(
                game_account=job.game_account,
                action_code=job.action_code,
                workflow__isnull=True,
            ).update(workflow=workflow)
        else:
            Job.objects.filter(root_job_id=chain_root_id, workflow__isnull=True).update(workflow=workflow)
            if job.root_job_id is None:
                Job.objects.filter(pk=job.pk, workflow__isnull=True).update(workflow=workflow)

        if job.workflow_id != workflow.pk:
            job.workflow = workflow

        if job.workflow_run_id and not start_new_run:
            workflow_run = job.workflow_run
        else:
            workflow_run = create_workflow_run(
                workflow=workflow,
                status=_run_status_from_job(job.status),
                scheduled_for=job.scheduled_for,
                trigger_type="backfill",
                summary={"job_id": str(job.pk), "action_code": int(job.action_code)},
            )
            job.workflow_run = workflow_run

        job.save(update_fields=["workflow", "workflow_run", "updated_at"])

        workflow.active_run = workflow_run
        # For grouped types, derive status from most recent active job across all cities
        if use_group:
            active_job = (
                Job.objects.filter(
                    game_account=job.game_account,
                    action_code=job.action_code,
                    status__in=("queued", "running", "scheduled"),
                )
                .order_by("-created_at")
                .first()
            )
            if active_job:
                workflow.status = _workflow_status_from_job(active_job.status)
                workflow.next_scheduled_for = active_job.scheduled_for
            else:
                workflow.status = "finished"
                workflow.next_scheduled_for = None
        else:
            workflow.status = _workflow_status_from_job(job.status)
            workflow.next_scheduled_for = job.scheduled_for
        workflow.last_event_at = timezone.now()
        workflow.save(update_fields=["active_run", "status", "next_scheduled_for", "last_event_at", "updated_at"])

        return workflow, workflow_run
