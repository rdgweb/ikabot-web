"""
Job signals — dispatch via Celery on create, notify on terminal status.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Job, Workflow
from .services.dispatch import dispatch_job

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"finished", "error", "cancelled"}


@receiver(post_save, sender=Job)
def enqueue_job_on_create(sender, instance, created, **kwargs):
    """Dispatch newly created queued/scheduled jobs to Celery after commit."""
    if not created:
        return

    if instance.status not in {"queued", "scheduled"}:
        return

    if not instance.node_id:
        logger.warning("Job %s has no node — cannot dispatch", instance.pk)
        return

    eta = instance.scheduled_for if instance.status == "scheduled" else None

    def _dispatch():
        dispatch_job(instance, eta=eta)
        logger.info(
            "Job %s (action=%d) dispatched to Celery queue %s%s",
            instance.pk,
            instance.action_code,
            instance.node_id,
            f" eta={eta.isoformat()}" if eta else "",
        )

    transaction.on_commit(_dispatch)


@receiver(post_save, sender=Job)
def update_workflow_status_on_terminal(sender, instance, created, update_fields, **kwargs):
    """When a job reaches terminal status, check if its workflow should be marked finished."""
    if created:
        return
    if update_fields and "status" not in update_fields:
        return
    if instance.status not in _TERMINAL_STATUSES:
        return
    if not instance.workflow_id:
        return

    def _check():
        try:
            wf = Workflow.objects.filter(pk=instance.workflow_id).select_for_update(skip_locked=True).first()
            if wf is None or wf.status in ("finished", "cancelled", "paused"):
                return
            has_active = Job.objects.filter(
                workflow_id=instance.workflow_id,
                status__in=("queued", "running", "scheduled"),
            ).exists()
            if not has_active:
                wf.status = "finished"
                wf.finished_at = timezone.now()
                wf.save(update_fields=["status", "finished_at", "updated_at"])
        except Exception as exc:
            logger.debug("Workflow status update skipped: %s", exc)

    transaction.on_commit(_check)


@receiver(post_save, sender=Job)
def notify_on_terminal_status(sender, instance, created, update_fields, **kwargs):
    """Send Telegram notification when a job reaches terminal status."""
    if created:
        return

    # Only fire when status field was explicitly updated
    if update_fields and "status" not in update_fields:
        return

    if instance.status not in _TERMINAL_STATUSES:
        return

    # Determine event key
    if instance.status == "error":
        event_key = "job_failed"
    elif instance.status == "finished":
        event_key = "job_done"
    else:
        return  # cancelled — no notification for now

    # Send notification — template handles all formatting
    try:
        import json as _json
        import os as _os
        from apps.telegram.services.notifications import notify

        # Extract last error log for richer notification
        last_error = (
            instance.logs.filter(level="error")
            .order_by("-created_at")
            .values_list("message", flat=True)
            .first()
        ) or ""

        # Extract city_name from inputs
        try:
            _inputs = _json.loads(instance.inputs_json or "{}")
            city_name = str(_inputs.get("city_name") or _inputs.get("from_city_name") or "")
        except Exception:
            city_name = ""

        # Build hub URL for the job detail (optional — uses HUB_PUBLIC_URL env var)
        hub_url = _os.environ.get("HUB_PUBLIC_URL", "").rstrip("/")
        job_url = f"{hub_url}/jobs/{instance.pk}/" if hub_url else ""

        reply_markup = None
        if job_url:
            reply_markup = {"inline_keyboard": [[{"text": "🔗 Ver detalhes", "url": job_url}]]}

        notify(
            event_key=event_key,
            game_account=instance.game_account,
            account=instance.account,
            node=instance.node,
            job=instance,
            agent_name=instance.agent,
            exit_code=instance.exit_code,
            body=last_error,
            city_name=city_name,
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.warning("Telegram notification failed for job %s: %s", instance.pk, e)
