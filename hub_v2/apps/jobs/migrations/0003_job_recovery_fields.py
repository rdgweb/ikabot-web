from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0002_alter_job_inputs_json_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="progress_json",
            field=models.TextField(default="{}"),
        ),
        migrations.AddField(
            model_name="job",
            name="last_heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="job",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="job",
            index=models.Index(fields=["lease_expires_at"], name="jobs_job_lease_ex_35be31_idx"),
        ),
    ]
