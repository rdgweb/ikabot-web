import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("espionage", "0004_raidalertsent_pending_since"),
        ("jobs", "0012_backfill_job_archived_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="spyreport",
            name="created_by_job",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="spy_reports",
                to="jobs.job",
            ),
        ),
    ]
