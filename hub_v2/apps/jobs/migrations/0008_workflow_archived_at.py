from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0007_workflow_root_job_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="archived_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
