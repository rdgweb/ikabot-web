"""Dispatch jobs to Celery workers bound to a specific node queue."""

from __future__ import annotations

import ast
import json
from datetime import datetime

from apps.jobs.models import Job
from config.celery import app as celery_app

TASK_NAME = "agent_v2.execute_job"


def _serialize_inputs(job: Job) -> dict:
    try:
        return json.loads(job.inputs_json) if job.inputs_json else {}
    except (json.JSONDecodeError, TypeError):
        try:
            parsed = ast.literal_eval(job.inputs_json or "{}")
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}


def _serialize_progress(job: Job) -> dict:
    raw = job.progress_json
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
    except Exception:
        return {}


# N-141: shipments that may ask Poseidon or Hermes when they take most of the account's cargo ships
MIRACLE_SHIPMENT_ACTIONS = frozenset({2, 3})


def _miracle_rules(job: Job) -> dict:
    from apps.accounts.models import Account
    from apps.game.services.miracle_rules import agent_rules

    organization = Account._base_manager.filter(pk=job.account_id).select_related("organization").first()
    return agent_rules(getattr(organization, "organization", None))


# N-143: the shrine jobs that follow the organization's choice of god per luxury good
SHRINE_ACTIONS = frozenset({36, 1007})


def _shrine_gods(job: Job) -> dict:
    from apps.accounts.models import Account
    from apps.game.services.miracle_rules import shrine_gods_for

    account = Account._base_manager.filter(pk=job.account_id).select_related("organization").first()
    return shrine_gods_for(getattr(account, "organization", None))


def build_payload(job: Job) -> dict:
    payload = {
        "job_id": str(job.pk),
        "account_id": str(job.account_id),
        "game_account_id": str(job.game_account_id) if job.game_account_id else None,
        "source_job_id": str(job.source_job_id) if job.source_job_id else None,
        "root_job_id": str(job.root_job_id) if job.root_job_id else None,
        "action_code": job.action_code,
        "inputs": _serialize_inputs(job),
        "progress": _serialize_progress(job),
        "timeout_sec": job.timeout_sec,
    }
    if job.action_code in SHRINE_ACTIONS:
        try:
            payload["shrine_gods"] = _shrine_gods(job)
        except Exception:  # noqa: BLE001 - the agent falls back to the god of each luxury good
            payload["shrine_gods"] = {}
    if job.action_code in MIRACLE_SHIPMENT_ACTIONS:
        try:
            payload["miracle_rules"] = _miracle_rules(job)
        except Exception:  # noqa: BLE001 - the job runs without the rule rather than not at all
            payload["miracle_rules"] = {}
    return payload


def dispatch_job(job: Job, eta: datetime | None = None) -> None:
    from apps.accounts.models import Node

    transport = Node._base_manager.filter(pk=job.node_id).values_list("transport", flat=True).first()
    if transport == Node.TRANSPORT_HTTPS:
        # N-106 3b-2: the job stays in the database and the node's agent claims it over HTTPS
        # when it is due; nothing goes to the broker.
        if eta is not None:
            Job._base_manager.filter(pk=job.pk).update(scheduled_for=eta)
        return
    celery_app.send_task(
        TASK_NAME,
        kwargs={"job": build_payload(job)},
        queue=str(job.node_id),
        eta=eta,
    )
