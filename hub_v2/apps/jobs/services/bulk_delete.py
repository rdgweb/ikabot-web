"""Deleting many workflows or jobs off the request, in small batches (N-201).

Deleting a thousand workflows in one request loaded every job and every log line into memory (the
process grew 1 GB), ran past the page's time (504) and a second try waited on the first one's locks
(500, nothing deleted). Here the page only hands over what to delete and answers at once; a thread
goes through it a few jobs at a time, each batch its own short transaction: the log lines first,
straight in the database (millions of them), then the jobs, the runs of each workflow and the
workflow. A batch that waits too long on a lock is tried again.

What is still to delete is kept in the cache (the queue): a second request while one runs joins it
instead of being refused, and a deletion cut by a restart of the hub goes on when the list is opened
or another deletion is asked (nothing stays locked). How far it got is shown on the list.
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
SHOW_DONE_SECONDS = 60          # a finished or cancelled deletion stays on the list this long
SHOW_ERROR_SECONDS = 600
STALE_SECONDS = 180            # a running deletion that has not moved in this long was cut (hub restarted)
BUSY_RETRIES = 8
BUSY_WAIT_SECONDS = 15
LOCK_SECONDS = 10


def _key(user_id) -> str:
    return f"jobs-bulk-delete:{user_id}"


def progress(user_id) -> dict | None:
    value = cache.get(_key(user_id))
    if not isinstance(value, dict):
        return None
    # N-201 (Rodrigo, 20/11): the end is shown for a moment, then the banner goes away (it stayed for hours)
    ended_ago = time.time() - float(value.get("finished_at") or value.get("at") or 0)
    if value.get("status") in ("done", "cancelled") and ended_ago > SHOW_DONE_SECONDS:
        return None
    if value.get("status") == "error" and ended_ago > SHOW_ERROR_SECONDS:
        return None
    shown = {k: v for k, v in value.items() if k != "pending"}
    shown["left"] = len(value.get("pending") or [])
    if value.get("status") == "running" and _stale(value):
        shown["status"] = "interrupted"
    return shown


def _stale(value: dict) -> bool:
    return time.time() - float(value.get("at") or 0) > STALE_SECONDS


def _locked(user_id, change):
    """Read, change and write this user's deletion under a short lock (the page and the thread both write it)."""
    lock = f"{_key(user_id)}:lock"
    for _ in range(100):
        if cache.add(lock, 1, LOCK_SECONDS):
            break
        time.sleep(0.05)
    try:
        value = cache.get(_key(user_id))
        value = dict(value) if isinstance(value, dict) else {}
        result = change(value)
        cache.set(_key(user_id), value, PROGRESS_SECONDS)
        return result
    finally:
        cache.delete(lock)


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


def _delete_workflow(workflow_id, beat) -> None:
    from apps.jobs.models import Job, Workflow, WorkflowRun

    job_ids = [str(pk) for pk in Job.objects.filter(workflow_id=workflow_id).values_list("pk", flat=True)]
    for start in range(0, len(job_ids), JOB_BATCH):
        _retrying(_delete_jobs, job_ids[start:start + JOB_BATCH])
        beat()
    while True:
        run_ids = list(WorkflowRun.objects.filter(workflow_id=workflow_id).values_list("pk", flat=True)[:RUN_BATCH])
        if not run_ids:
            break
        _retrying(lambda ids: WorkflowRun.objects.filter(pk__in=ids).delete(), run_ids)
        beat()
    _retrying(lambda pk: Workflow.objects.filter(pk=pk).delete(), workflow_id)


def start(user_id, *, workflow_ids: list | None = None, job_ids: list | None = None) -> str:
    """Hand over what to delete. Returns "started", "queued" (joined the deletion already running) or "resumed" (a
    deletion cut by a restart goes on, with these added)."""
    from django.utils import timezone

    from apps.jobs.models import Workflow

    # N-201 (Rodrigo, 20/11): what was marked leaves the list at once, not when the server gets to it. A workflow is
    # archived (the list hides those) with a stamp, so "Cancelar" brings back only the ones this deletion hid; a job
    # is left out of the technical list while it is queued.
    stamp = timezone.now()
    to_hide = [str(pk) for pk in Workflow.objects.filter(pk__in=list(workflow_ids or []), archived_at__isnull=True)
               .values_list("pk", flat=True)]
    if to_hide:
        Workflow.objects.filter(pk__in=to_hide).update(archived_at=stamp)
    hidden = set(to_hide)
    items = ([["w", str(pk), stamp.isoformat() if str(pk) in hidden else ""] for pk in (workflow_ids or [])]
             + [["j", str(pk), ""] for pk in (job_ids or [])])
    kind = "workflows" if workflow_ids else "jobs"

    def add(value):
        running = value.get("status") == "running"
        fresh = running and not _stale(value)
        known = {tuple(i[:2]) for i in value.get("pending") or []}
        new = [i for i in items if tuple(i[:2]) not in known]
        if running:
            value["pending"] = list(value.get("pending") or []) + new
            value["total"] = int(value.get("total") or 0) + len(new)
            if value.get("kind") != kind:
                value["kind"] = "workflows e jobs"
        else:
            value.clear()
            value.update(kind=kind, total=len(new), done=0, status="running", error="", started_at=time.time(), pending=new)
        value["at"] = time.time()
        return "queued" if fresh else ("resumed" if running else "started")

    outcome = _locked(user_id, add)
    if outcome != "queued":
        _run(user_id)
    return outcome


def pending_job_ids(user_id, limit: int = 5000) -> list[str]:
    """The jobs this user's deletion still has to delete (left out of the technical list meanwhile)."""
    value = cache.get(_key(user_id))
    if not isinstance(value, dict) or value.get("status") != "running":
        return []
    return [i[1] for i in (value.get("pending") or []) if i[0] == "j"][:limit]


def cancel(user_id) -> int:
    """Stop the deletion: what was deleted stays deleted; the workflows it hid and did not delete come back."""
    from datetime import datetime

    from apps.jobs.models import Workflow

    def stop(value):
        pending = list(value.get("pending") or [])
        value.update(status="cancelled", pending=[], at=time.time(), finished_at=time.time())
        return pending

    pending = _locked(user_id, stop)
    back = 0
    for item in pending:
        if item[0] == "w" and len(item) > 2 and item[2]:
            back += Workflow.objects.filter(pk=item[1], archived_at=datetime.fromisoformat(item[2])).update(archived_at=None)
    return back


def resume_if_cut(user_id) -> bool:
    """A deletion cut by a restart (no progress for a while, still things to delete) goes on."""
    value = cache.get(_key(user_id))
    if not isinstance(value, dict) or value.get("status") != "running" or not value.get("pending") or not _stale(value):
        return False

    def touch(v):
        if v.get("status") == "running" and _stale(v):
            v["at"] = time.time()
            return True
        return False

    if _locked(user_id, touch):
        _run(user_id)
        return True
    return False


def _run(user_id) -> None:
    caller = threading.get_ident()

    def beat():
        _locked(user_id, lambda v: v.__setitem__("at", time.time()))

    def take(v):
        if v.get("status") != "running":
            return None                                  # cancelled on the page
        pending = list(v.get("pending") or [])
        if not pending:
            v.update(status="done", finished_at=time.time(), at=time.time())
            return None
        first = pending[0]
        if first[0] == "w":
            batch = [first]
        else:
            batch = []
            for item in pending:
                if item[0] != "j" or len(batch) >= JOB_BATCH:
                    break
                batch.append(item)
        v["pending"] = pending[len(batch):]
        v["at"] = time.time()
        return batch

    def work():
        try:
            while True:
                batch = _locked(user_id, take)
                if not batch:
                    break
                if batch[0][0] == "w":
                    _delete_workflow(batch[0][1], beat)
                else:
                    _retrying(_delete_jobs, [item[1] for item in batch])
                _locked(user_id, lambda v, n=len(batch): v.update(done=int(v.get("done") or 0) + n, at=time.time())
                        if v.get("status") == "running" else None)
        except Exception:  # noqa: BLE001 - what was deleted stays deleted; the page says it stopped
            logger.exception("Bulk deletion failed")
            _locked(user_id, lambda v: v.update(status="error", at=time.time(),
                                                error="A exclusão parou num erro; o que já foi excluído não volta. Peça de novo."))
        finally:
            if threading.get_ident() != caller:
                connections.close_all()

    _spawn(work)
