"""Dispatch jobs to Celery workers bound to a specific node queue."""

from __future__ import annotations

import json
from datetime import datetime

from apps.jobs.models import Job
from config.celery import app as celery_app

TASK_NAME = "agent_v2.execute_job"


def _serialize_inputs(job: Job) -> dict:
    try:
        return json.loads(job.inputs_json) if job.inputs_json else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def build_payload(job: Job) -> dict:
    return {
        "job_id": str(job.pk),
        "account_id": str(job.account_id),
        "game_account_id": str(job.game_account_id) if job.game_account_id else None,
        "action_code": job.action_code,
        "inputs": _serialize_inputs(job),
        "timeout_sec": job.timeout_sec,
    }


def dispatch_job(job: Job, eta: datetime | None = None) -> None:
    celery_app.send_task(
        TASK_NAME,
        kwargs={"job": build_payload(job)},
        queue=str(job.node_id),
        eta=eta,
    )
