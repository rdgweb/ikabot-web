import django.db.models.deletion
import uuid
from django.db import migrations, models


def backfill_note_sequences(apps, schema_editor):
    Note = apps.get_model("notes", "Note")
    for index, note in enumerate(Note.objects.order_by("created_at", "id"), start=1):
        note.sequence = index
        note.save(update_fields=["sequence"])


class Migration(migrations.Migration):

    dependencies = [
        ("notes", "0002_authorized_notes_and_changelog_visibility"),
    ]

    operations = [
        migrations.AddField(
            model_name="note",
            name="sequence",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="note",
            name="claimed_by_label",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="note",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_note_sequences, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="note",
            name="sequence",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.CreateModel(
            name="NoteEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event_type", models.CharField(choices=[("created", "Criada"), ("authorized", "Autorizada"), ("claimed", "Assumida"), ("progress", "Progresso"), ("blocked", "Bloqueio"), ("decision", "Decisao"), ("validated", "Validada"), ("completed", "Concluida"), ("changelog", "Changelog"), ("status_change", "Status alterado")], default="progress", max_length=24)),
                ("message", models.TextField(blank=True, default="")),
                ("actor_label", models.CharField(blank=True, default="", max_length=120)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("note", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="notes.note")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
