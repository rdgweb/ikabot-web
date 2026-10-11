from django.db import migrations, models


class Migration(migrations.Migration):
    """N-96: Telegram message when the automatic proxy maintenance changes something.

    On by default in the global config (the operator asked to be told); a system
    event, so the per-account config has it only for symmetry, off.
    """

    dependencies = [
        ("telegram", "0013_add_notify_combat_report"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegrambotconfig",
            name="notify_proxy_change",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="notify_proxy_change",
            field=models.BooleanField(default=False),
        ),
    ]
