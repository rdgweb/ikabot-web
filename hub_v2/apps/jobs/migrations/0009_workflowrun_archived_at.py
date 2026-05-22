from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0008_workflow_archived_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflowrun",
            name="archived_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
