import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DiplomacyMessage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("game_msg_id", models.CharField(max_length=32)),
                ("sender", models.CharField(blank=True, default="", max_length=255)),
                ("subject", models.CharField(blank=True, default="", max_length=255)),
                ("body", models.TextField(blank=True, default="")),
                ("game_date", models.CharField(blank=True, default="", max_length=64)),
                ("receiver_id", models.CharField(blank=True, default="", max_length=32)),
                ("reply_to_game_id", models.CharField(blank=True, default="", max_length=32)),
                ("actions_json", models.TextField(default="[]")),
                ("status", models.CharField(
                    choices=[
                        ("new", "Nova"),
                        ("notified", "Notificada"),
                        ("read", "Lida"),
                        ("replied", "Respondida"),
                        ("action_taken", "Ação tomada"),
                    ],
                    db_index=True,
                    default="new",
                    max_length=32,
                )),
                ("game_account", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="diplomacy_messages",
                    to="accounts.gameaccount",
                )),
            ],
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Mensagem de Diplomacia",
                "verbose_name_plural": "Mensagens de Diplomacia",
            },
        ),
        migrations.AddConstraint(
            model_name="diplomacymessage",
            constraint=models.UniqueConstraint(
                fields=["game_account", "game_msg_id"],
                name="uq_diplomacymessage_account_msgid",
            ),
        ),
    ]
