"""
Add NotificationTemplate model + seed defaults.
"""

from django.db import migrations, models


def seed_defaults(apps, schema_editor):
    """Create default templates for all event types."""
    NotificationTemplate = apps.get_model("telegram", "NotificationTemplate")
    defaults = {
        "attack_alert": {"icon": "⚠️", "template": "{icon} <b>{title}</b>\n{body}"},
        "job_failed": {"icon": "❌", "template": "{icon} <b>{title}</b>\n{body}\n<code>exit_code: {exit_code}</code>"},
        "job_done": {"icon": "✅", "template": "{icon} <b>{title}</b>\n{body}"},
        "build_complete": {"icon": "🏗️", "template": "{icon} <b>{title}</b>\n{body}"},
        "research_complete": {"icon": "💡", "template": "{icon} <b>{title}</b>\n{body}"},
        "low_wine": {"icon": "🍷", "template": "{icon} <b>{title}</b>\n{body}"},
        "daily_summary": {"icon": "📊", "template": "{icon} <b>{title}</b>\n{body}"},
    }
    for key, vals in defaults.items():
        NotificationTemplate.objects.get_or_create(
            event_key=key,
            defaults={"icon": vals["icon"], "template": vals["template"]},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("telegram", "0004_global_chat_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotificationTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event_key", models.CharField(max_length=32, unique=True)),
                ("icon", models.CharField(default="🔔", max_length=8)),
                (
                    "template",
                    models.TextField(
                        help_text="Formato da mensagem. Variaveis: {icon}, {title}, {body}, {ga_name}, {exit_code}",
                    ),
                ),
            ],
            options={
                "verbose_name": "Template de Notificacao",
                "verbose_name_plural": "Templates de Notificacao",
                "ordering": ["event_key"],
            },
        ),
        migrations.RunPython(seed_defaults, migrations.RunPython.noop),
    ]
