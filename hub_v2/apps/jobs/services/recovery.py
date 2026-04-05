from __future__ import annotations

from datetime import timedelta
import json

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Node
from apps.jobs.models import Job, JobLog
from apps.settings_app.utils import get_int_setting

SAFE_REQUEUE_ACTIONS = {
    2,
    5,
    6,
    10,
    11,
    18,
    23,
    26,
    27,
    100,
    1001,
    1002,
    701,
    702,
    902,
    1006,
    1007,
}


def _grace_seconds() -> int:
    return max(60, get_int_setting("running_job_recovery_grace_seconds", 300))


def _requeue_delay_seconds() -> int:
    return max(5, get_int_setting("running_job_requeue_delay_seconds", 30))


def _lease_seconds() -> int:
    return max(30, get_int_setting("running_job_lease_seconds", 180))


def _is_job_stale(job: Job, *, now) -> bool:
    if job.lease_expires_at:
        return job.lease_expires_at <= now
    if job.last_heartbeat_at:
        return (now - job.last_heartbeat_at).total_seconds() > (_lease_seconds() + _grace_seconds())
    if not job.started_at:
        return False
    timeout_sec = max(60, int(job.timeout_sec or 1800))
    stale_after = timeout_sec + _grace_seconds()
    return (now - job.started_at).total_seconds() > stale_after


def recover_stale_running_jobs(*, node: Node | None = None) -> dict[str, int]:
    now = timezone.now()
    stale_jobs = (
        Job.objects.filter(status="running")
        .select_related("account", "game_account", "node", "profile")
        .order_by("started_at")
    )
    if node is not None:
        stale_jobs = stale_jobs.filter(node=node)

    recovered = 0
    requeued = 0
    marked_error = 0

    for job in stale_jobs:
        if not _is_job_stale(job, now=now):
            continue

        with transaction.atomic():
            locked = Job.objects.select_for_update().get(pk=job.pk)
            if locked.status != "running" or not _is_job_stale(locked, now=now):
                continue

            elapsed_seconds = int((now - locked.started_at).total_seconds()) if locked.started_at else 0
            action = "marcada como erro"
            next_job = None
            progress = {}
            try:
                progress = json.loads(locked.progress_json or "{}")
            except Exception:
                progress = {}

            if locked.action_code in SAFE_REQUEUE_ACTIONS:
                try:
                    next_inputs = json.loads(locked.inputs_json or "{}")
                except Exception:
                    next_inputs = {}
                recovery = dict(next_inputs.get("__recovery") or {})
                recovery["attempt"] = int(recovery.get("attempt") or 0) + 1
                recovery["recovered_from_job_id"] = str(locked.pk)
                recovery["previous_progress"] = progress
                next_inputs["__recovery"] = recovery
                next_job = Job.objects.create(
                    account=locked.account,
                    game_account=locked.game_account,
                    node=locked.node,
                    profile=locked.profile,
                    action_code=locked.action_code,
                    source_job_id=locked.pk,
                    inputs_json=json.dumps(next_inputs),
                    timeout_sec=locked.timeout_sec,
                    status="scheduled",
                    scheduled_for=now + timedelta(seconds=_requeue_delay_seconds()),
                )
                requeued += 1
                action = f"reagendada como {next_job.pk}"
                locked.status = "cancelled"
                locked.exit_code = 98
            else:
                marked_error += 1
                locked.status = "error"
                locked.exit_code = 98

            locked.finished_at = now
            locked.lease_expires_at = None
            locked.save(update_fields=["status", "exit_code", "finished_at", "lease_expires_at", "updated_at"])
            JobLog.objects.create(
                job=locked,
                level="warn",
                message=(
                    f"Execucao orfa recuperada apos {elapsed_seconds}s sem conclusao; "
                    f"{action}."
                ),
            )
            recovered += 1

    return {
        "recovered": recovered,
        "requeued": requeued,
        "marked_error": marked_error,
    }
