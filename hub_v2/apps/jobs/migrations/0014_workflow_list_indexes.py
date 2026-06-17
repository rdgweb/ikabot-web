from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0013_joblog_job_created_at_index"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="workflowrun",
            index=models.Index(fields=["workflow", "archived_at", "-sequence"], name="jobs_run_wf_arch_seq_idx"),
        ),
        migrations.AddIndex(
            model_name="job",
            index=models.Index(fields=["workflow", "archived_at", "-created_at"], name="jobs_job_wf_arch_created_idx"),
        ),
        migrations.AddIndex(
            model_name="job",
            index=models.Index(fields=["workflow", "archived_at", "status", "-created_at"], name="jobs_job_wf_arch_stat_idx"),
        ),
    ]
