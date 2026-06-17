from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0012_backfill_job_archived_at"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="joblog",
            index=models.Index(fields=["job", "-created_at"], name="jobs_joblog_job_created_desc"),
        ),
    ]
