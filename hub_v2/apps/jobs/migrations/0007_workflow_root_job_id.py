from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0006_workflow_foundation"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="root_job_id",
            field=models.UUIDField(blank=True, db_index=True, null=True, unique=True),
        ),
    ]
