"""Agent over HTTPS only (N-106, stage 3b-2).

No broker: the agent asks the hub for its node's due jobs every few seconds, with the node key,
and runs them with the same executor the broker worker uses. Manual control goes through the hub
too: while a session is open on this node (the claim answer says so), the agent waits on the hub
for the browser's requests and answers them there.

Nothing here can reach another node's work: the hub decides what each key gets.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from job_executor import execute_job_payload

logger = logging.getLogger(__name__)

POLL_SECONDS = 3
ERROR_BACKOFF_SECONDS = 10
MANUAL_WAIT_SECONDS = 20


class HttpsManualChannel:
    """What the manual-control service expects from the broker (brpop/rpush/expire), over the hub's API.

    It only waits on the hub while manual control is on for this node, so an idle agent holds no
    connection open there.
    """

    def __init__(self, hub, *, wait: int = MANUAL_WAIT_SECONDS):
        self.hub = hub
        self.wait = wait
        self.active = threading.Event()

    def brpop(self, key, timeout=5):
        if not self.active.is_set():
            self.active.wait(timeout)
            return None
        data = self.hub.manual_next(wait=self.wait) or {}
        message = data.get("request")
        if not message and not data.get("active"):
            self.active.clear()                       # the session ended: stop waiting on the hub
        return (key, json.dumps(message)) if message else None

    def rpush(self, key, value):
        request_id = str(key).rsplit(":", 1)[-1]
        self.hub.manual_answer(request_id, json.loads(value))

    def expire(self, key, seconds):
        return True                                   # the hub keeps the answer for the time it needs


def _run_one(job: dict, sessions, proxy_url) -> None:
    try:
        execute_job_payload(job=job, sessions=sessions, proxy_url=proxy_url())
    except Exception:  # noqa: BLE001 - one job never stops the loop
        logger.exception("Job %s failed in the HTTPS worker", job.get("job_id"))


def run(*, hub, sessions, proxy_url, max_parallel: int, channel: HttpsManualChannel, stop: threading.Event) -> None:
    """Claim and run this node's jobs until ``stop`` is set."""
    pool = ThreadPoolExecutor(max_workers=max(1, max_parallel), thread_name_prefix="job")
    running: set = set()
    logger.info("HTTPS worker: up to %d job(s) at a time, asking the hub every %ss", max_parallel, POLL_SECONDS)
    while not stop.is_set():
        running = {future for future in running if not future.done()}
        free = max(0, max_parallel - len(running))
        try:
            data = hub.claim_jobs(free) or {}      # with no free slot it still says whether manual control is on
        except Exception as exc:  # noqa: BLE001 - the hub comes back
            logger.warning("HTTPS worker: could not reach the hub (%s); retrying", type(exc).__name__)
            stop.wait(ERROR_BACKOFF_SECONDS)
            continue
        if data.get("manual"):
            channel.active.set()
        jobs = data.get("jobs") or []
        for job in jobs:
            running.add(pool.submit(_run_one, job, sessions, proxy_url))
        stop.wait(0.5 if jobs else POLL_SECONDS)
    pool.shutdown(wait=False, cancel_futures=False)
