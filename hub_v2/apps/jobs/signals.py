"""
Job signals — dispatch via Celery on create, notify on terminal status.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Job
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
        from apps.telegram.services.notifications import notify
        notify(
            event_key=event_key,
            game_account=instance.game_account,
            account=instance.account,
            node=instance.node,
            job=instance,
            agent_name=instance.agent,
            exit_code=instance.exit_code,
        )
    except Exception as e:
        logger.warning("Telegram notification failed for job %s: %s", instance.pk, e)
