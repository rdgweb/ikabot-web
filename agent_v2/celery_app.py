"""Celery application for agent-side job execution."""

from celery import Celery

from core.config import settings

app = Celery("agent_v2", broker=settings.redis_url, backend=settings.redis_url)
app.conf.update(
    task_default_queue=settings.agent_node_id,
    task_ignore_result=True,
    task_track_started=False,
    worker_prefetch_multiplier=1,
    # Reconnect to broker automatically after Redis restart
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=None,  # retry indefinitely
    imports=("tasks",),
    # Keep Redis TCP connection alive to prevent idle disconnects
    broker_transport_options={
        "socket_keepalive": True,
        "health_check_interval": 25,
    },
)
