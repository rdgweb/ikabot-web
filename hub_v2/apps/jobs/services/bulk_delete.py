"""Deleting many workflows or jobs off the request, in small batches (N-201).

Deleting a thousand workflows in one request loaded every job and every log line into memory (the
process grew 1 GB), ran past the page's time (504) and a second try waited on the first one's locks
(500, nothing deleted). Here the page only hands over what to delete and answers at once; a thread
goes through it a few jobs at a time, each batch its own short transaction: the log lines first,
straight in the database (millions of them), then the jobs, the runs of each workflow and the
workflow. A batch that waits too long on a lock is tried again. How far it got is kept in the cache
for the page to show.
"""
from __future__ import annotations

import logging
import threading
import time

from django.core.cache import cache
from django.db import connections

logger = logging.getLogger(__name__)

JOB_BATCH = 200                 # jobs per transaction (their logs go first, by the job index)
RUN_BATCH = 1000                # workflow runs per transaction
PROGRESS_SECONDS = 6 * 3600
BUSY_RETRIES = 8
BUSY_WAIT_SECONDS = 15


def _key(user_id) -> str:
    return f"jobs-bulk-delete:{user_id}"


def progress(user_id) -> dict | None:
    value = cache.get(_key(user_id))
    return value if isinstance(value, dict) else None


def _save(user_id, **fields) -> None:
    value = progress(user_id) or {}
    value.update(fields)
    cache.set(_key(user_id), value, PROGRESS_SECONDS)


def _spawn(work) -> None:
    """Run `work` off the request. Tests replace this to run it inline."""
    threading.Thread(target=work, name="jobs-bulk-delete", daemon=True).start()


def _busy(exc: Exception) -> bool:
    from apps.settings_app.retention import _database_busy

    return _database_busy(exc)


def _retrying(step, *args):
    for attempt in range(BUSY_RETRIES + 1):
        try:
            return step(*args)
        except Exception as exc:  # noqa: BLE001 - only a busy database is tried again
            if not _busy(exc) or attempt == BUSY_RETRIES:
                raise
            time.sleep(BUSY_WAIT_SECONDS * (attempt + 1))


def _delete_jobs(job_ids: list) -> int:
    """These jobs, with their log lines (straight in the database) and what hangs on them (Django)."""
    from apps.jobs.models import Job, JobLog

    logs = JobLog.objects.filter(job_id__in=job_ids)
    logs._raw_delete(logs.db)
    deleted, _ = Job.objects.filter(pk__in=job_ids).delete()
    return deleted


def _jobs_in_batches(job_ids: list, on_batch=None) -> int:
    total = 0
    for start in range(0, len(job_ids), JOB_BATCH):
        total += _retrying(_delete_jobs, job_ids[start:start + JOB_BATCH])
        if on_batch:
            on_batch()
    return total


def _delete_workflow(workflow_id) -> None:
    from apps.jobs.models import Job, Workflow, WorkflowRun

    _jobs_in_batches([str(pk) for pk in Job.objects.filter(workflow_id=workflow_id).values_list("pk", flat=True)])
    while True:
        run_ids = list(WorkflowRun.objects.filter(workflow_id=workflow_id).values_list("pk", flat=True)[:RUN_BATCH])
        if not run_ids:
            break
        _retrying(lambda ids: WorkflowRun.objects.filter(pk__in=ids).delete(), run_ids)
    _retrying(lambda pk: Workflow.objects.filter(pk=pk).delete(), workflow_id)


def start(user_id, *, workflow_ids: list | None = None, job_ids: list | None = None) -> bool:
    """Hand over what to delete; False when this user already has a deletion going."""
    workflow_ids = [str(pk) for pk in (workflow_ids or [])]
    job_ids = [str(pk) for pk in (job_ids or [])]
    current = progress(user_id)
    if current and current.get("status") == "running":
        return False
    kind = "workflows" if workflow_ids else "jobs"
    total = len(workflow_ids) if workflow_ids else len(job_ids)
    _save(user_id, kind=kind, total=total, done=0, status="running", error="", started_at=time.time())
    caller = threading.get_ident()

    def work():
        try:
            if workflow_ids:
                for index, workflow_id in enumerate(workflow_ids, start=1):
                    _delete_workflow(workflow_id)
                    _save(user_id, done=index)
            else:
                state = {"done": 0}

                def tick():
                    state["done"] = min(total, state["done"] + JOB_BATCH)
                    _save(user_id, done=state["done"])

                _jobs_in_batches(job_ids, on_batch=tick)
            _save(user_id, status="done", done=total, finished_at=time.time())
        except Exception as exc:  # noqa: BLE001 - what was deleted stays deleted; the page says it stopped
            logger.exception("Bulk deletion failed")
            _save(user_id, status="error", error="A exclusão parou num erro; o que já foi excluído não volta. Tente de novo.")
        finally:
            if threading.get_ident() != caller:
                connections.close_all()

    _spawn(work)
    return True
