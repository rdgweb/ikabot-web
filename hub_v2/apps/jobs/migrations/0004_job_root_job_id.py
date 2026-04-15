from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0003_job_recovery_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="root_job_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
