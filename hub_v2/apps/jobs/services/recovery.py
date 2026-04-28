from __future__ import annotations

from datetime import timedelta
import logging
import json

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Node
from apps.jobs.models import Job, JobLog
from apps.jobs.services.workflows import create_job_with_workflow
from apps.settings_app.utils import get_int_setting

logger = logging.getLogger(__name__)

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
                # Guard: if job already rescheduled itself (child exists from agent reschedule),
                # don't create a second child — just cancel the orphan.
                has_recent_child = Job.objects.filter(
                    source_job_id=locked.pk,
                    created_at__gte=now - timedelta(seconds=300),
                ).exists()
                if has_recent_child:
                    locked.status = "cancelled"
                    locked.exit_code = 98
                    locked.finished_at = now
                    locked.lease_expires_at = None
                    locked.save(update_fields=["status", "exit_code", "finished_at", "lease_expires_at", "updated_at"])
                    JobLog.objects.create(
                        job=locked,
                        level="warn",
                        message=(
                            f"Execucao orfa recuperada apos {elapsed_seconds}s — "
                            f"job ja reschedulado pelo agent, apenas cancelando o orphan."
                        ),
                    )
                    recovered += 1
                    continue

                try:
                    next_inputs = json.loads(locked.inputs_json or "{}")
                except Exception:
                    next_inputs = {}
                recovery = dict(next_inputs.get("__recovery") or {})
                recovery["attempt"] = int(recovery.get("attempt") or 0) + 1
                recovery["recovered_from_job_id"] = str(locked.pk)
                recovery["previous_progress"] = progress
                next_inputs["__recovery"] = recovery
                next_job = create_job_with_workflow(
                    account=locked.account,
                    game_account=locked.game_account,
                    node=locked.node,
                    profile=locked.profile,
                    action_code=locked.action_code,
                    inputs=next_inputs,
                    timeout_sec=locked.timeout_sec,
                    source_job=locked,
                    status="scheduled",
                    scheduled_for=now + timedelta(seconds=_requeue_delay_seconds()),
                    start_new_run=True,
                    trigger_type="recovery_requeue",
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


# How long past scheduled_for before we consider it lost (avoids re-dispatching
# a job that's still in the Redis queue but just hasn't started yet).
_SCHEDULED_GRACE_MINUTES = 10

# How long a "queued" job can sit without starting before we re-dispatch.
_QUEUED_GRACE_MINUTES = 10

# Minimum time since last updated_at before re-dispatching again (prevents
# hammering if the agent is slow to start the job).
_REDISPATCH_COOLDOWN_MINUTES = 5


def recover_stale_scheduled_jobs(*, node: Node | None = None) -> dict[str, int]:
    """Re-dispatch jobs that are stranded in 'scheduled' or 'queued' status.

    This handles the case where the Celery task was lost (Redis restart without
    persistence, or machine was off for a long time) but the Job record in the
    DB still reflects the old status.

    Called from the agent heartbeat so that when a node comes back online after
    an extended outage, its pending jobs are automatically re-submitted.
    """
    from apps.jobs.services.dispatch import dispatch_job

    now = timezone.now()
    scheduled_cutoff = now - timedelta(minutes=_SCHEDULED_GRACE_MINUTES)
    queued_cutoff = now - timedelta(minutes=_QUEUED_GRACE_MINUTES)
    cooldown_cutoff = now - timedelta(minutes=_REDISPATCH_COOLDOWN_MINUTES)

    requeued_scheduled = 0
    requeued_queued = 0

    # --- Stale "scheduled" jobs: scheduled_for is in the past by more than grace ---
    scheduled_qs = Job.objects.filter(
        status="scheduled",
        scheduled_for__lt=scheduled_cutoff,
        # Cooldown: skip if we already re-dispatched recently (updated_at is fresh)
        updated_at__lt=cooldown_cutoff,
    )
    if node is not None:
        scheduled_qs = scheduled_qs.filter(node=node)

    for job in scheduled_qs:
        try:
            dispatch_job(job, eta=None)  # dispatch immediately, job is already overdue
            Job.objects.filter(pk=job.pk).update(updated_at=now)
            logger.warning(
                "Re-dispatched stale scheduled job %s (was due %s)",
                job.pk,
                job.scheduled_for,
            )
            requeued_scheduled += 1
        except Exception as exc:
            logger.error("Failed to re-dispatch scheduled job %s: %s", job.pk, exc)

    # --- Stale "queued" jobs: created long ago, never started ---
    queued_qs = Job.objects.filter(
        status="queued",
        started_at__isnull=True,
        created_at__lt=queued_cutoff,
        updated_at__lt=cooldown_cutoff,
    )
    if node is not None:
        queued_qs = queued_qs.filter(node=node)

    for job in queued_qs:
        try:
            dispatch_job(job, eta=None)
            Job.objects.filter(pk=job.pk).update(updated_at=now)
            logger.warning(
                "Re-dispatched stale queued job %s (queued since %s)",
                job.pk,
                job.created_at,
            )
            requeued_queued += 1
        except Exception as exc:
            logger.error("Failed to re-dispatch queued job %s: %s", job.pk, exc)

    return {
        "requeued_scheduled": requeued_scheduled,
        "requeued_queued": requeued_queued,
    }
