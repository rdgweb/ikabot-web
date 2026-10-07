"""O status de um workflow como ele realmente esta agora (N-89).

O campo Workflow.status e o que a reconciliacao gravou por ultimo; o que o usuario
quer ver e a realidade dos jobs. Esta e a UNICA regra para isso: linhas da Fila de
Jobs, cartoes de resumo, filtro de status e a atualizacao ao vivo usam todos daqui.
Antes cada lugar tinha a sua, e o resumo dizia "0 com problema" com varias linhas
"Com problema" na mesma tela.
"""

from __future__ import annotations

import json

from django.db.models import Max
from django.utils import timezone

from ..models import Job, Workflow

SUMMARY_KEYS = ("active", "waiting", "problem", "paused", "finished", "cancelled")
STATUS_COLORS = {
    "active": "var(--ik-sea)",
    "waiting": "#f59e0b",
    "problem": "var(--ik-bad)",
    "paused": "var(--ik-muted)",
    "finished": "var(--ik-good)",
    "cancelled": "var(--ik-line)",
    "draft": "var(--ik-line)",
}
_NO_FACTS = {"statuses": frozenset(), "active": False, "running": False, "error": False}


def job_facts(workflow_ids=None, *, archived: bool = False) -> dict:
    """What the jobs of each workflow say, in one query.

    {workflow_id: {"statuses": {...}, "active": bool, "running": bool, "error": bool}}
      active   has a job queued, scheduled or running
      running  has a job running right now (a running job whose lease expired is dead
               and does not count)
      error    the most recent job ended in error (an error followed by a newer job
               has been superseded)
    Without workflow_ids it covers every workflow of the list (archived or not).
    """
    jobs = Job.objects.filter(archived_at__isnull=True)
    if workflow_ids is not None:
        jobs = jobs.filter(workflow_id__in=list(workflow_ids))
    else:
        jobs = jobs.filter(workflow__isnull=False, workflow__archived_at__isnull=not archived)
    times: dict = {}
    for row in (
        jobs.exclude(status="running", lease_expires_at__lte=timezone.now())
        .values("workflow_id", "status")
        .annotate(latest=Max("created_at"))
    ):
        times.setdefault(row["workflow_id"], {})[row["status"]] = row["latest"]

    facts: dict = {}
    for workflow_id, by_status in times.items():
        error_at = by_status.get("error")
        newest = max(moment for moment in by_status.values() if moment)
        facts[workflow_id] = {
            "statuses": frozenset(by_status),
            "running": bool(by_status.get("running")),
            "active": bool(by_status.get("running") or by_status.get("queued") or by_status.get("scheduled")),
            "error": bool(error_at and not newest > error_at),
        }
    return facts


def _is_recurring(workflow) -> bool:
    try:
        config = json.loads(workflow.config_json or "{}")
    except Exception:
        return False
    return bool(isinstance(config, dict) and config.get("recurring"))


def effective_status(workflow, facts: dict | None, *, recurring: bool | None = None) -> str:
    """active | waiting | problem | paused | finished | cancelled | draft."""
    facts = facts or _NO_FACTS
    if facts["active"]:
        # an error as the latest news is a problem even with other jobs still lined up
        if facts["error"]:
            return "problem"
        return "active" if facts["running"] else "waiting"
    if workflow.status == "paused":
        # pausing is the user's explicit choice: history does not turn it into "finished"
        return "paused"
    if facts["error"]:
        return "problem"
    recent = set(facts["statuses"]) - {"error"}
    if "running" in recent:
        status = "active"
    elif recent & {"queued", "scheduled"}:
        status = "waiting"
    elif recent and recent <= {"finished", "cancelled"}:
        status = "finished"
    else:
        status = workflow.status
    if recurring is None:
        recurring = _is_recurring(workflow)
    # a recurring workflow with nothing lined up is a loop that stopped, unless it was
    # ended on purpose (paused, cancelled, or reconciled as really finished)
    if status == "finished" and recurring and workflow.status not in ("paused", "cancelled", "finished"):
        return "problem"
    return status


def effective_statuses(workflows, facts: dict | None = None) -> dict:
    """{workflow pk: effective status} for the given workflows (needs status and config_json)."""
    workflows = list(workflows)
    if facts is None:
        facts = job_facts([workflow.pk for workflow in workflows])
    return {workflow.pk: effective_status(workflow, facts.get(workflow.pk)) for workflow in workflows}


def status_summary(*, archived: bool = False) -> dict:
    """Counters of the summary cards: effective status of every workflow of the list."""
    workflows = Workflow.objects.filter(archived_at__isnull=not archived).only("id", "status", "config_json")
    summary = dict.fromkeys(SUMMARY_KEYS, 0)
    for status in effective_statuses(workflows, job_facts(archived=archived)).values():
        if status in summary:
            summary[status] += 1
    summary["total"] = sum(summary.values())
    return summary


def filter_by_effective_status(queryset, status: str):
    """Workflows of the queryset whose effective status is `status`."""
    # the list's queryset carries select_related(); only the three columns are needed here
    candidates = list(queryset.order_by().select_related(None).only("id", "status", "config_json"))
    statuses = effective_statuses(candidates)
    return queryset.filter(pk__in=[pk for pk, current in statuses.items() if current == status])
