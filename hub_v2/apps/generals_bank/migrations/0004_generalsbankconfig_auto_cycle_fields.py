from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("generals_bank", "0003_generalsbankproducer_policy_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="generalsbankconfig",
            name="auto_cycle_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Keep a background manager loop active for this bank.",
            ),
        ),
        migrations.AddField(
            model_name="generalsbankconfig",
            name="auto_cycle_interval_minutes",
            field=models.IntegerField(
                default=30,
                help_text="When auto-cycle is enabled, wait this many minutes between cycle checks.",
            ),
        ),
    ]
