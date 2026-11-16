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
from core.contracts import ACTION_CATALOG

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


def _orphan_resume_delay_seconds() -> int:
    return max(60, get_int_setting("orphan_recurring_resume_delay_seconds", 300))


# N-195: a loop whose run was orphaned (agent restarted mid-run) resumes with a new job;
# after this many orphaned resumes in a row it stops, as before, instead of retrying for ever.
_MAX_CONSECUTIVE_ORPHAN_RESUMES = 3
_ORPHAN_RESUME_TRIGGER = "orphan_resume"

# Inputs that say which slice of a workflow a job is: Manter tropas and Manter frota live in
# one workflow (one job per kind), the donation loops of an account in one workflow (one job
# per city). Two jobs of the same action are the same loop only when these match.
_LANE_KEYS = (
    "building_type",
    "city_id",
    "city_ids",
    "cities",
    "city",
    "target_city",
    "island_ids",
    "own_city_ids",
    "monitor_mode",
)


def _parse_inputs(raw) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _lane_value(value):
    if isinstance(value, (list, tuple)):
        items = [item.get("id") if isinstance(item, dict) else item for item in value]
        return tuple(sorted(str(item) for item in items if item not in (None, "")))
    if isinstance(value, dict):
        return tuple(sorted((str(k), str(v)) for k, v in value.items()))
    if value in (None, ""):
        return ""
    return str(value)


def _lane_key(action_code: int, inputs: dict) -> tuple:
    return (int(action_code),) + tuple(_lane_value(inputs.get(key)) for key in _LANE_KEYS)


def _has_active_same_lane(job: Job, inputs: dict) -> bool:
    """Another queued/scheduled/running job already carries this job's loop."""
    if not job.workflow_id:
        return False
    key = _lane_key(job.action_code, inputs)
    candidates = Job.objects.filter(
        workflow_id=job.workflow_id,
        action_code=job.action_code,
        status__in=("queued", "running", "scheduled"),
    ).exclude(pk=job.pk).only("pk", "source_job_id", "inputs_json", "action_code")
    for other in candidates:
        if other.source_job_id == job.pk:
            return True
        if _lane_key(other.action_code, _parse_inputs(other.inputs_json)) == key:
            return True
    return False


def _job_loops(action_code: int, inputs: dict) -> bool:
    """The job is a loop that schedules its own next cycle (catalog "recurring")."""
    if not ACTION_CATALOG.get(int(action_code), {}).get("recurring"):
        return False
    if int(action_code) == 1005:
        # Treinar agora / Distribuir run once; only Manter efetivo loops
        return str(inputs.get("mode") or "once") == "maintain"
    if int(action_code) == 3:
        return bool(inputs.get("loop_enabled"))
    return True


def _consecutive_orphan_resumes(job: Job) -> int:
    count = 0
    current = job
    seen = set()
    while current is not None and current.pk not in seen:
        seen.add(current.pk)
        run = current.workflow_run
        if run is None or run.trigger_type != _ORPHAN_RESUME_TRIGGER:
            break
        count += 1
        if not current.source_job_id:
            break
        current = Job.objects.select_related("workflow_run").filter(pk=current.source_job_id).first()
    return count


def _resume_orphaned_loop(locked: Job, *, now, elapsed_seconds: int) -> str | None:
    """Create the loop's next job from the orphan's inputs; the reason it did not, or None."""
    workflow = locked.workflow
    if workflow is not None and workflow.status in ("paused", "cancelled"):
        return "workflow pausado" if workflow.status == "paused" else "workflow cancelado"
    if _consecutive_orphan_resumes(locked) >= _MAX_CONSECUTIVE_ORPHAN_RESUMES:
        return f"{_MAX_CONSECUTIVE_ORPHAN_RESUMES} retomadas seguidas tambem ficaram orfas"

    inputs = _parse_inputs(locked.inputs_json)
    # parameters edited while the job ran (N-13) apply to the next cycle, as in a reschedule
    pending_patch = inputs.pop("__pending_patch", None)
    if isinstance(pending_patch, dict) and pending_patch:
        inputs.update(pending_patch)

    delay = _orphan_resume_delay_seconds()
    next_job = create_job_with_workflow(
        account=locked.account,
        game_account=locked.game_account,
        node=locked.node,
        profile=locked.profile,
        action_code=locked.action_code,
        inputs=inputs,
        timeout_sec=locked.timeout_sec,
        source_job=locked,
        status="scheduled",
        scheduled_for=now + timedelta(seconds=delay),
        start_new_run=True,
        trigger_type=_ORPHAN_RESUME_TRIGGER,
    )
    locked.status = "cancelled"
    locked.exit_code = 98
    locked.finished_at = now
    locked.lease_expires_at = None
    locked.save(update_fields=["status", "exit_code", "finished_at", "lease_expires_at", "updated_at"])
    minutes = max(1, round(delay / 60))
    JobLog.objects.create(
        job=locked,
        level="warn",
        message=(
            f"Execucao orfa recuperada apos {elapsed_seconds}s sem conclusao (o agente parou no meio). "
            f"Acao recorrente: o loop continua no job {next_job.pk}, agendado para daqui a {minutes} min "
            f"com os mesmos parametros."
        ),
    )
    JobLog.objects.create(
        job=next_job,
        level="info",
        message=f"Retomada do loop: o job anterior {locked.pk} ficou orfao apos {elapsed_seconds}s.",
    )
    return None


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
    resumed = 0
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
                # N-195: only a job of the same slice (kind, city) counts; a workflow can hold
                # several loops of one action (Manter tropas + frota, donations per city)
                has_active_followup = _has_active_same_lane(locked, _parse_inputs(locked.inputs_json))
                if has_active_followup:
                    locked.status = "cancelled"
                    locked.exit_code = 98
                    locked.finished_at = now
                    locked.lease_expires_at = None
                    locked.save(update_fields=["status", "exit_code", "finished_at", "lease_expires_at", "updated_at"])
                    JobLog.objects.create(
                        job=locked,
                        level="warn",
                        message=(
                            f"Execucao orfa recuperada apos {elapsed_seconds}s -- "
                            f"workflow ja possui follow-up ativo da mesma acao; cancelando o orphan."
                        ),
                    )
                    recovered += 1
                    continue

                # Guard: if job already rescheduled itself, don't create a second child.
                # Filter by trigger_type="agent_reschedule" to avoid confusing spawn children
                # (e.g. ac=2 transport spawns ac=2 arrival monitor — that's NOT a reschedule).
                has_recent_child = Job.objects.filter(
                    source_job_id=locked.pk,
                    action_code=locked.action_code,
                    workflow_run__trigger_type="agent_reschedule",
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
            elif _job_loops(locked.action_code, _parse_inputs(locked.inputs_json)):
                # N-195: a loop (Manter tropas, Roubar Barbaros...) used to end here for good
                if _has_active_same_lane(locked, _parse_inputs(locked.inputs_json)):
                    locked.status = "cancelled"
                    locked.exit_code = 98
                    locked.finished_at = now
                    locked.lease_expires_at = None
                    locked.save(update_fields=["status", "exit_code", "finished_at", "lease_expires_at", "updated_at"])
                    JobLog.objects.create(
                        job=locked,
                        level="warn",
                        message=(
                            f"Execucao orfa recuperada apos {elapsed_seconds}s -- "
                            f"o loop ja tem o proximo job na fila; cancelando o orphan."
                        ),
                    )
                    recovered += 1
                    continue
                not_resumed = _resume_orphaned_loop(locked, now=now, elapsed_seconds=elapsed_seconds)
                if not_resumed is None:
                    resumed += 1
                    recovered += 1
                    continue
                marked_error += 1
                locked.status = "error"
                locked.exit_code = 98
                action = f"marcada como erro; loop nao retomado ({not_resumed})"
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
        "resumed": resumed,
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
