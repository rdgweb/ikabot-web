"""Mailbox campaigns (Espionagem Mundial, N-152): a campaign must always have a round to wake up.

The root job keeps in progress_json["current_runner_id"] the round that the children notify. If that round
ended without leaving the next one (the hub refused its fallback, for example), the children would notify a
job that is over and the campaign would stop for good. revive_campaign points the root to the round that is
still active or, when there is none, creates the next round from the last one (same inputs: the targets
already claimed and the players already spied are kept).
"""
from __future__ import annotations

import ast
import json
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

ACTIVE = ("queued", "scheduled", "running")
WAKE_IN = timedelta(seconds=5)


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        try:
            parsed = ast.literal_eval(value)       # older rows saved the dict's repr
        except (TypeError, ValueError, SyntaxError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


def revive_campaign(root_job_id, *, inbox: list | None = None):
    """The round the campaign's children should notify, created when there is none; None when the
    campaign was cancelled (a cancelled round is the operator's decision, never undone here)."""
    from apps.jobs.models import Job
    from apps.jobs.services.dispatch import dispatch_job
    from apps.jobs.services.workflows import create_job_with_workflow

    created = woken = None
    with transaction.atomic():
        root = Job.objects.select_for_update().get(pk=root_job_id)
        root_id = str(root.pk)
        progress = _as_dict(root.progress_json)
        current_id = str(progress.get("current_runner_id") or "").strip()
        runner = Job.objects.filter(pk=current_id).first() if current_id else None
        if runner is not None and runner.status in ACTIVE:
            return runner
        if runner is not None and runner.status == "cancelled":
            return None
        active = (Job.objects.filter(action_code=root.action_code, status__in=ACTIVE, created_at__gte=root.created_at)
                  .filter(inputs_json__contains=root_id).order_by("-created_at").first())
        if active is None and root.status in ACTIVE:
            active = root
        if active is not None:
            target = active
            if target.status == "scheduled" and target.scheduled_for and target.scheduled_for > timezone.now() + 6 * WAKE_IN:
                target.scheduled_for = timezone.now() + WAKE_IN       # a fallback sleeping for hours wakes now
                Job.objects.filter(pk=target.pk).update(scheduled_for=target.scheduled_for)
                woken = target
        else:
            base = runner or root
            inputs = _as_dict(base.inputs_json)
            inputs.pop("__pending_patch", None)
            inputs["__campaign_root_id"] = root_id
            created = target = create_job_with_workflow(
                account=base.account,
                game_account=base.game_account,
                node=base.node,
                profile=base.profile,
                action_code=base.action_code,
                inputs=json.dumps(inputs),
                timeout_sec=base.timeout_sec,
                source_job=base,
                status="scheduled",
                scheduled_for=timezone.now() + WAKE_IN,
                start_new_run=True,
                trigger_type="agent_reschedule",
            )
        if inbox:
            target_progress = _as_dict(target.progress_json)
            target_progress["inbox"] = list(target_progress.get("inbox") or []) + list(inbox)
            Job.objects.filter(pk=target.pk).update(progress_json=json.dumps(target_progress))
        progress["current_runner_id"] = str(target.pk)
        Job.objects.filter(pk=root.pk).update(progress_json=json.dumps(progress))

    if woken is not None:
        dispatch_job(woken, eta=woken.scheduled_for)
    if created is not None:
        dispatch_job(created, eta=created.scheduled_for)
        logger.warning("Campanha %s sem rodada ativa (ultima %s): nova rodada %s",
                       root_id[:8], current_id[:8] or "-", str(created.pk)[:8])
    return target
