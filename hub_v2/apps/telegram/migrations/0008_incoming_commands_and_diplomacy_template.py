from django.db import migrations, models


def seed_incoming_commands(apps, schema_editor):
    TelegramIncomingCommand = apps.get_model("telegram", "TelegramIncomingCommand")
    defaults = {
        "link": {
            "command": "/start",
            "description": "Vincula um chat usando o codigo gerado no painel.",
        },
        "diplomacy_reply": {
            "command": "/replyto",
            "description": "Cria job de resposta ou acao para mensagem de diplomacia.",
        },
    }
    for key, values in defaults.items():
        TelegramIncomingCommand.objects.get_or_create(
            key=key,
            defaults={
                "command": values["command"],
                "description": values["description"],
                "enabled": True,
            },
        )


def update_diplomacy_template(apps, schema_editor):
    NotificationTemplate = apps.get_model("telegram", "NotificationTemplate")
    NotificationTemplate.objects.update_or_create(
        event_key="diplomacy_message",
        defaults={
            "icon": "\U0001f4e8",
            "title_template": "Diplomacia - {ga_name}",
            "body_template": (
                "De: {sender}\n"
                "Assunto: {subject}\n"
                "Data: {game_date}\n"
                "{message_body}\n"
                "Responder: <code>{reply_command}</code>\n"
                "Aceitar: <code>{accept_command}</code>\n"
                "Recusar: <code>{decline_command}</code>"
            ),
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("telegram", "0007_notify_diplomacy_message"),
    ]

    operations = [
        migrations.CreateModel(
            name="TelegramIncomingCommand",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("key", models.CharField(choices=[("link", "Vincular chat"), ("diplomacy_reply", "Responder diplomacia")], max_length=32, unique=True)),
                ("command", models.CharField(default="", max_length=32)),
                ("enabled", models.BooleanField(default=True)),
                ("description", models.TextField(blank=True, default="")),
            ],
            options={
                "verbose_name": "Comando de Entrada Telegram",
                "verbose_name_plural": "Comandos de Entrada Telegram",
                "ordering": ["key"],
            },
        ),
        migrations.RunPython(seed_incoming_commands, migrations.RunPython.noop),
        migrations.RunPython(update_diplomacy_template, migrations.RunPython.noop),
    ]
