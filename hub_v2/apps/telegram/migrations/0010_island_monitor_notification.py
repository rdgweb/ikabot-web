from django.db import migrations, models


def seed_island_monitor_template(apps, schema_editor):
    NotificationTemplate = apps.get_model("telegram", "NotificationTemplate")
    NotificationTemplate.objects.update_or_create(
        event_key="island_monitor",
        defaults={
            "icon": "\U0001f3dd\ufe0f",
            "title_template": "{title}",
            "body_template": "{ga_name} | {server_id}\n{body}",
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("telegram", "0009_messageaudit_context_json"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegrambotconfig",
            name="notify_island_monitor",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="notify_island_monitor",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(seed_island_monitor_template, migrations.RunPython.noop),
    ]
