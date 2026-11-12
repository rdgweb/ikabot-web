"""Explicit, bounded history maintenance. Merely saving a policy never deletes data."""
import json
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import record
from apps.game.models import AccountSnapshotHistory, AccountHistoryMetric
from apps.game.services.kpi_history import _metrics
from apps.game.services.dashboard_cache import bump_dashboard_cache_version
from apps.jobs.models import Job, JobLog, Workflow
from apps.orgs.scope import unscoped
from .models import AppSetting, RetentionRun, JobRetentionReceipt

POLICY_KEY = "database_retention_policy"
DEFAULT_POLICY = {"enabled": False, "snapshot_days": 30, "job_days": 90, "log_days": 90}
BATCH_SIZE = 100
KINDS = ("snapshots", "jobs", "logs")
COMPACT_PROGRESS = json.dumps({"message": "Detalhes antigos removidos pela política de retenção.", "retention_compacted": True})


def policy_value(raw):
    value = json.loads(raw) if raw else DEFAULT_POLICY.copy()
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise ValueError("Configuração de retenção inválida.")
    for key in ("snapshot_days", "job_days", "log_days"):
        if type(value.get(key)) is not int or not 1 <= value[key] <= 3650:
            raise ValueError("Os prazos devem estar entre 1 e 3650 dias.")
    return {key: value[key] for key in DEFAULT_POLICY}


def get_policy():
    raw = AppSetting.objects.filter(pk=POLICY_KEY).values_list("value", flat=True).first()
    return policy_value(raw)


def eligible_jobs(anchor, days):
    # Never trim a live workflow, a job with live dependencies, or unarchived history.
    cutoff = anchor - timedelta(days=days)
    referenced = Job.objects.filter(Q(root_job_id__isnull=False) | Q(source_job_id__isnull=False))
    return (Job.objects.filter(status__in=["finished", "error", "cancelled"],
                               finished_at__lt=cutoff, updated_at__lte=anchor, archived_at__isnull=False)
            .filter(Q(workflow__isnull=True) | Q(workflow__status__in=["finished", "cancelled"], workflow__active_run__isnull=True))
            .exclude(root_job_id__isnull=False)
            .exclude(pk__in=referenced.exclude(source_job_id__isnull=True).values("source_job_id"))
            .exclude(pk__in=referenced.exclude(root_job_id__isnull=True).values("root_job_id"))
            .exclude(pk__in=Workflow.objects.exclude(root_job_id__isnull=True).values("root_job_id"))
            .exclude(construction_reservations__status="active"))


def candidates(run):
    anchor, policy = run.created_at, run.policy
    snapshots = AccountSnapshotHistory.objects.filter(
        captured_at__lt=anchor - timedelta(days=policy["snapshot_days"]), created_at__lte=anchor)
    jobs = eligible_jobs(anchor, policy["job_days"]).exclude(pk__in=JobRetentionReceipt.objects.values("job_id"))
    logs = JobLog.objects.filter(job_id__in=eligible_jobs(anchor, policy["log_days"]).values("pk"),
                                 created_at__lt=anchor - timedelta(days=policy["log_days"]))
    return {"snapshots": snapshots, "jobs": jobs, "logs": logs}


def preview(user):
    with unscoped(), transaction.atomic():
        run = RetentionRun.objects.create(user=user, policy=get_policy())
        queries = candidates(run)
        run.planned = {kind: qs.count() for kind, qs in queries.items()}
        estimate = 0
        for kind, qs in queries.items():
            sample = list(qs.order_by("pk")[:20])
            sizes = []
            for row in sample:
                if kind == "snapshots":
                    old = json.dumps([row.base_snapshot, row.cities, row.military], ensure_ascii=False)
                    new = json.dumps(_metrics(row), ensure_ascii=False)
                elif kind == "jobs":
                    old, new = row.progress_json, COMPACT_PROGRESS
                else:
                    old, new = row.message, ""
                sizes.append(max(0, len(old.encode("utf-8")) - len(new.encode("utf-8"))))
            if sizes:
                estimate += int(sum(sizes) / len(sizes) * run.planned[kind])
        run.planned["estimated_bytes"] = estimate
        run.save(update_fields=["planned"])
        return run


def process_batch(run_id, user, *, start=False, backup=False):
    with unscoped(), transaction.atomic():
        # Serialize maintenance requests, including concurrent tabs and policy changes.
        setting, _ = AppSetting.objects.get_or_create(pk=POLICY_KEY, defaults={"value": json.dumps(DEFAULT_POLICY)})
        setting = AppSetting.objects.select_for_update().get(pk=setting.pk)
        run = RetentionRun.objects.select_for_update().get(pk=run_id, user=user)
        policy = policy_value(setting.value)
        if policy != run.policy or not policy["enabled"]:
            raise ValueError("A política mudou ou está desativada. Gere uma nova prévia.")
        if run.status == "done":
            return run
        if run.status == "preview":
            if not start or not backup:
                raise ValueError("Confirme a prévia e que existe um backup restaurável antes de executar.")
            if timezone.now() - run.created_at > timedelta(hours=1):
                raise ValueError("Esta prévia expirou. Gere outra antes de executar.")
            run.backup_confirmed = True
            run.status = "running"
        if run.status != "running":
            raise ValueError("Execução inválida.")

        totals = dict(run.processed)
        queries = candidates(run)
        for kind in KINDS:
            remaining = run.planned[kind] - totals.get(kind, 0)
            limit = min(remaining, BATCH_SIZE if kind != "logs" else 1000)
            if limit <= 0:
                continue
            qs = queries[kind]
            rows = list(qs.select_for_update().order_by("pk")[:limit])
            if kind == "snapshots":
                for row in rows:
                    values = dict(account_id=row.account_id, game_account_id=row.game_account_id,
                                  captured_at=row.captured_at, metrics=_metrics(row))
                    metric, _ = AccountHistoryMetric.objects.get_or_create(pk=row.pk, defaults=values)
                    if any(getattr(metric, key) != value for key, value in values.items()):
                        raise ValueError("Métricas divergentes; o snapshot original foi preservado.")
                    row.delete()
            elif kind == "jobs":
                for row in rows:
                    # Keep inputs and all relationships: old jobs remain identifiable/reusable.
                    row.progress_json = COMPACT_PROGRESS
                    row.save(update_fields=["progress_json"])
                    JobRetentionReceipt.objects.get_or_create(job=row)
            else:
                JobLog.objects.filter(pk__in=[row.pk for row in rows]).delete()
            totals[kind] = totals.get(kind, 0) + len(rows)
            if rows:
                run.processed = totals
                run.save(update_fields=["status", "backup_confirmed", "processed"])
                record("retention.batch", target=run, user=user, details={"kind": kind, "count": len(rows)})
                if kind == "snapshots":
                    transaction.on_commit(bump_dashboard_cache_version)
                return run  # one bounded transaction per request
        run.status = "done"
        run.processed = totals
        run.save(update_fields=["status", "backup_confirmed", "processed"])
        record("retention.completed", target=run, user=user, details=totals)
        return run
