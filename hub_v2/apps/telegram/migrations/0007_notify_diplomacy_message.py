"""
Add notify_diplomacy_message toggle to TelegramBotConfig and TelegramAccountConfig.
Seed NotificationTemplate for diplomacy_message event.
"""

from django.db import migrations, models


def seed_diplomacy_template(apps, schema_editor):
    """Create NotificationTemplate for diplomacy_message (if not exists)."""
    NotificationTemplate = apps.get_model("telegram", "NotificationTemplate")
    NotificationTemplate.objects.get_or_create(
        event_key="diplomacy_message",
        defaults={
            "icon": "📨",
            "title_template": "Diplomacia — {ga_name}",
            "body_template": "{body}",
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("telegram", "0006_template_title_body"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegrambotconfig",
            name="notify_diplomacy_message",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="notify_diplomacy_message",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(seed_diplomacy_template, migrations.RunPython.noop),
    ]
