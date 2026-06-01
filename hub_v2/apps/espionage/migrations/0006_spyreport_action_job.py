from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0006_workflow_foundation"),
        ("espionage", "0005_spyreport_created_by_job"),
    ]

    operations = [
        migrations.AddField(
            model_name="spyreport",
            name="action_job",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="spy_action_reports",
                to="jobs.job",
            ),
        ),
    ]
