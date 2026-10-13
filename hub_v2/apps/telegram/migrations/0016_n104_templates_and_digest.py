import django.db.models.deletion
from django.db import migrations, models

OLD_ISLAND = ("{title}", "{ga_name} | {server_id}\n{body}")
NEW_ISLAND = ("Mudança na ilha {coords}", "{island_line}\nVisto por {ga_name}\n{changes}")
CINEMA = ("🎬", "Cineteatro disponível em {count} conta(s)", "{accounts}\nO dia do jogo vira às {reset_time}. A sessão é manual no jogo.")


def fill(apps, schema_editor):
    Template = apps.get_model("telegram", "NotificationTemplate")
    for template in Template.objects.all():
        template.scope_key = f"{template.event_key}:{template.game_account_id or 'global'}"
        # the island text nobody edited moves to the structured one; an edited one is left alone
        if template.event_key == "island_monitor" and (template.title_template, template.body_template) == OLD_ISLAND:
            template.title_template, template.body_template = NEW_ISLAND
        template.save(update_fields=["scope_key", "title_template", "body_template"])
    Template.objects.get_or_create(
        scope_key="cinema_available:global",
        defaults={"event_key": "cinema_available", "icon": CINEMA[0], "title_template": CINEMA[1], "body_template": CINEMA[2]},
    )


class Migration(migrations.Migration):
    """N-104: a notice text per account (besides the global one) and the once-a-day digest.

    Adds columns and a table; no existing row is removed. The data step only fills the
    new scope key, upgrades the island text when it is still the old default, and
    creates the cinema text.
    """

    dependencies = [
        ("accounts", "0013_n98_accented_labels"),
        ("telegram", "0015_n98_accented_labels"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notificationtemplate",
            name="event_key",
            field=models.CharField(db_index=True, max_length=32),
        ),
        migrations.AddField(
            model_name="notificationtemplate",
            name="game_account",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name="telegram_templates", to="accounts.gameaccount"),
        ),
        migrations.AddField(
            model_name="notificationtemplate",
            name="scope_key",
            field=models.CharField(blank=True, editable=False, max_length=80, null=True, unique=True),
        ),
        migrations.CreateModel(
            name="DigestEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event_key", models.CharField(max_length=32)),
                ("day_key", models.CharField(help_text="Game day the entry belongs to (date of its reset, UTC)", max_length=16)),
                ("active", models.BooleanField(default=True)),
                ("due_at", models.DateTimeField(blank=True, help_text="When the digest should go out", null=True)),
                ("reset_at", models.DateTimeField(blank=True, null=True)),
                ("data_json", models.TextField(blank=True, default="{}")),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("game_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                   related_name="telegram_digest_entries", to="accounts.gameaccount")),
            ],
            options={
                "indexes": [models.Index(fields=["event_key", "sent_at", "due_at"], name="tg_digest_due_idx")],
                "unique_together": {("event_key", "game_account", "day_key")},
            },
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
