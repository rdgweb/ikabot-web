"""
Split NotificationTemplate.template into title_template + body_template.
"""

from django.db import migrations, models


DEFAULTS = {
    "attack_alert": {
        "icon": "⚠️",
        "title_template": "Alerta de Ataque",
        "body_template": "{ga_name} esta sob ataque!",
    },
    "job_failed": {
        "icon": "❌",
        "title_template": "{action_name} falhou",
        "body_template": "Conta: {ga_name}\nExit code: {exit_code}",
    },
    "job_done": {
        "icon": "✅",
        "title_template": "{action_name} concluido",
        "body_template": "Conta: {ga_name}",
    },
    "build_complete": {
        "icon": "🏗️",
        "title_template": "Construcao concluida",
        "body_template": "{ga_name} — {server_id}",
    },
    "research_complete": {
        "icon": "💡",
        "title_template": "Pesquisa concluida",
        "body_template": "{ga_name} — {server_id}",
    },
    "low_wine": {
        "icon": "🍷",
        "title_template": "Vinho baixo",
        "body_template": "{ga_name} esta com estoque baixo de vinho.",
    },
    "daily_summary": {
        "icon": "📊",
        "title_template": "Resumo diario",
        "body_template": "{account_name}",
    },
}


def migrate_templates(apps, schema_editor):
    """Populate title_template and body_template from defaults, drop old template."""
    NotificationTemplate = apps.get_model("telegram", "NotificationTemplate")
    for tpl in NotificationTemplate.objects.all():
        defaults = DEFAULTS.get(tpl.event_key)
        if defaults:
            tpl.icon = defaults["icon"]
            tpl.title_template = defaults["title_template"]
            tpl.body_template = defaults["body_template"]
            tpl.save()


class Migration(migrations.Migration):

    dependencies = [
        ("telegram", "0005_notificationtemplate"),
    ]

    operations = [
        # Add new fields
        migrations.AddField(
            model_name="notificationtemplate",
            name="title_template",
            field=models.CharField(
                default="{action_name}",
                help_text="Titulo da mensagem. Aceita variaveis como {action_name}, {ga_name}, etc.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="notificationtemplate",
            name="body_template",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Corpo da mensagem. Aceita variaveis e quebras de linha.",
            ),
        ),
        # Populate new fields
        migrations.RunPython(migrate_templates, migrations.RunPython.noop),
        # Remove old field
        migrations.RemoveField(
            model_name="notificationtemplate",
            name="template",
        ),
    ]
