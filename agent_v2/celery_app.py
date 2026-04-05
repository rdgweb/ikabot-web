"""Celery application for agent-side job execution."""

from celery import Celery

from core.config import settings

app = Celery("agent_v2", broker=settings.redis_url, backend=settings.redis_url)
app.conf.update(
    task_default_queue=settings.agent_node_id,
    task_ignore_result=True,
    task_track_started=False,
    broker_connection_retry_on_startup=True,
    imports=("tasks",),
)
