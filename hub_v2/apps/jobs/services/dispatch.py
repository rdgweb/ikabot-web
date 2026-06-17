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


def build_payload(job: Job) -> dict:
    return {
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


def dispatch_job(job: Job, eta: datetime | None = None) -> None:
    celery_app.send_task(
        TASK_NAME,
        kwargs={"job": build_payload(job)},
        queue=str(job.node_id),
        eta=eta,
    )
