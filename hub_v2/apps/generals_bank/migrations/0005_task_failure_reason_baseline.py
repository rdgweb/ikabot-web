from django.db import migrations, models


class Migration(migrations.Migration):
    """N-184: why a task failed, and the stock it started from (kept in the hub, so a retry never trains again)."""

    dependencies = [("generals_bank", "0004_generalsbankconfig_auto_cycle_fields")]

    operations = [
        migrations.AddField(
            model_name="generalsbankcycletask",
            name="failure_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="generalsbankcycletask",
            name="baseline_count",
            field=models.IntegerField(blank=True, null=True,
                                      help_text="Units of this kind the producer city had before the cycle trained any."),
        ),
    ]
