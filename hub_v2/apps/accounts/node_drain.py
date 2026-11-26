"""N-261: an agent update waits for its node to be free instead of being refused.

While an update is pending the node takes no new job (they stay in the queue, nothing is lost); the jobs already
running finish; then the update is applied and the queue goes on with the new version. A pending update that is not
applied in UPDATE_MAX_WAIT is closed, so a missing supervisor never holds a node's work for good.

The same pause serves the platform's own deploy (agents without a supervisor): `manage.py node_drain`.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

UPDATE_MAX_WAIT = timedelta(hours=6)
DRAIN_KEY = "node_drain:{pk}"


def busy_jobs(node) -> int:
    """Jobs the agent is working on: running, or claimed and still inside their lease."""
    from apps.jobs.models import Job

    now = timezone.now()
    return Job._base_manager.filter(node=node).filter(
        Q(status="running") | Q(status__in=("queued", "scheduled"), lease_expires_at__gt=now)
    ).count()


def pending_update(node):
    """The queued update of this node, if still worth waiting for."""
    from .models import AgentUpdateRequest

    pending = AgentUpdateRequest._base_manager.filter(node=node, status="queued").order_by("created_at").first()
    if pending is not None and pending.created_at < timezone.now() - UPDATE_MAX_WAIT:
        AgentUpdateRequest._base_manager.filter(pk=pending.pk, status="queued").update(
            status="failed", finished_at=timezone.now(), updated_at=timezone.now(),
            status_message="Não foi aplicada em 6 h (supervisor sem resposta); o nó voltou a trabalhar.")
        return None
    return pending


def is_draining(node) -> bool:
    return bool(cache.get(DRAIN_KEY.format(pk=node.pk))) or pending_update(node) is not None


def start_drain(node, seconds: int = 3600) -> None:
    cache.set(DRAIN_KEY.format(pk=node.pk), 1, max(60, int(seconds)))


def stop_drain(node) -> None:
    cache.delete(DRAIN_KEY.format(pk=node.pk))
