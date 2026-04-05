"""Execute a single job payload using the existing runner stack."""

from __future__ import annotations

import logging
import threading
import time

import requests

from core.config import settings
from core.hub_client import HubClient
from core.runner_registry import get_runner
from sessions import SessionManager

logger = logging.getLogger(__name__)


def _is_terminal_conflict(exc: requests.HTTPError) -> bool:
    return exc.response is not None and exc.response.status_code in (404, 409)


class JobHeartbeatThread(threading.Thread):
    def __init__(self, hub: HubClient, *, job_id: str, interval_seconds: int = 45):
        super().__init__(daemon=True)
        self.hub = hub
        self.job_id = job_id
        self.interval_seconds = max(10, int(interval_seconds))
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.hub.report_status(self.job_id, status="running", agent=settings.agent_name)
            except requests.HTTPError as exc:
                if _is_terminal_conflict(exc):
                    return
            except Exception:
                logger.debug("Heartbeat update failed for job %s", self.job_id, exc_info=True)


def execute_job_payload(job: dict, sessions: SessionManager, proxy_url: str = "") -> None:
    hub = HubClient()
    job_id = job.get("job_id", "unknown")
    action_code = job.get("action_code", 0)
    account_id = job.get("account_id", "")
    lock_key = job.get("game_account_id") or account_id

    with sessions.get_lock(lock_key):
        heartbeat: JobHeartbeatThread | None = None
        try:
            hub.report_status(job_id, status="running", agent=settings.agent_name)
        except requests.HTTPError as exc:
            if _is_terminal_conflict(exc):
                logger.info("Skipping job %s: status update rejected (%s)", job_id, exc.response.status_code)
                return
            raise

        try:
            logger.info("Running job %s (action=%s, account=%s)", job_id, action_code, account_id)
            hub.report_log(job_id, "info", f"Job started on {settings.agent_name}")
            heartbeat = JobHeartbeatThread(hub, job_id=job_id)
            heartbeat.start()

            runner_cls = get_runner(action_code)
            runner = runner_cls(hub=hub, sessions=sessions, proxy_url=proxy_url)
            result = runner.execute(job)
            heartbeat.stop()

            if result.reschedule_seconds:
                hub.reschedule_job(
                    job_id,
                    result.reschedule_seconds,
                    inputs=result.reschedule_inputs,
                )
                hub.report_log(job_id, "info", f"Rescheduled in {result.reschedule_seconds}s")

            try:
                hub.report_status(
                    job_id,
                    status="finished" if result.success else "error",
                    exit_code=0 if result.success else 1,
                    agent=settings.agent_name,
                )
            except requests.HTTPError as exc:
                if _is_terminal_conflict(exc):
                    logger.info("Ignoring terminal status conflict for job %s (%s)", job_id, exc.response.status_code)
                    return
                raise
            logger.info("Job %s completed (success=%s)", job_id, result.success)
        except Exception as exc:
            try:
                if heartbeat is not None:
                    heartbeat.stop()
            except Exception:
                pass
            logger.error("Job %s failed: %s", job_id, exc)
            hub.report_log(job_id, "error", str(exc))
            try:
                hub.report_status(job_id, status="error", exit_code=1, agent=settings.agent_name)
            except requests.HTTPError as status_exc:
                if not _is_terminal_conflict(status_exc):
                    raise
            raise
