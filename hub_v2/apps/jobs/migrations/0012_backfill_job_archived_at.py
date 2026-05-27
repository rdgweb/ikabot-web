# Backfill Job.archived_at for all jobs whose WorkflowRun is already archived.
# These jobs were archived at the run level before job-level archival was introduced.

from django.db import migrations


def backfill_job_archived_at(apps, schema_editor):
    Job = apps.get_model("jobs", "Job")
    WorkflowRun = apps.get_model("jobs", "WorkflowRun")

    archived_run_ids = list(
        WorkflowRun.objects.filter(archived_at__isnull=False).values_list("pk", flat=True)
    )
    if not archived_run_ids:
        return

    # Set job.archived_at = run.archived_at for all jobs in archived runs
    # Process in batches to avoid huge IN clauses
    batch_size = 1000
    for i in range(0, len(archived_run_ids), batch_size):
        batch = archived_run_ids[i : i + batch_size]
        for run in WorkflowRun.objects.filter(pk__in=batch).only("pk", "archived_at"):
            Job.objects.filter(
                workflow_run_id=run.pk,
                archived_at__isnull=True,
            ).update(archived_at=run.archived_at)


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0011_add_job_archived_at"),
    ]

    operations = [
        migrations.RunPython(backfill_job_archived_at, migrations.RunPython.noop),
    ]
