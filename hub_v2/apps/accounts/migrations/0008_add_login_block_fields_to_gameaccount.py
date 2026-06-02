from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_add_chronos_level_to_gameaccount"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameaccount",
            name="login_block_backoff_hours",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Current consecutive loginLink 400 backoff in hours.",
            ),
        ),
        migrations.AddField(
            model_name="gameaccount",
            name="login_block_reason",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Last login-block reason reported by the agent.",
            ),
        ),
        migrations.AddField(
            model_name="gameaccount",
            name="login_blocked_until",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Temporary login cooldown after repeated loginLink 400 responses.",
            ),
        ),
    ]
