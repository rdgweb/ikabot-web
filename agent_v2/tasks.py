"""Celery task entrypoints for agent job execution."""

import logging

from celery import Task

from celery_app import app
from core.hub_client import HubClient
from job_executor import execute_job_payload
from sessions import SessionManager

# Import all runners so the registry is populated in worker startup.
import runners  # noqa: F401

logger = logging.getLogger(__name__)

_sessions = SessionManager()
_proxy_url = ""


def configure_worker_runtime(proxy_url: str = "") -> None:
    global _proxy_url
    _proxy_url = proxy_url


class AgentTask(Task):
    autoretry_for = ()
    retry_kwargs = {}
    max_retries = 0


@app.task(name="agent_v2.execute_job", bind=True, base=AgentTask, ignore_result=True)
def execute_job(self, job: dict) -> None:
    execute_job_payload(job=job, sessions=_sessions, proxy_url=_proxy_url)
