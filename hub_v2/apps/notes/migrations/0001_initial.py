import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Note",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=180)),
                ("body", models.TextField(blank=True, default="")),
                ("note_type", models.CharField(choices=[("bug", "Bug"), ("task", "Task"), ("idea", "Ideia"), ("note", "Nota")], default="task", max_length=16)),
                ("status", models.CharField(choices=[("open", "Aberta"), ("doing", "Em andamento"), ("done", "Concluida"), ("archived", "Arquivada")], default="open", max_length=16)),
                ("priority", models.CharField(choices=[("low", "Baixa"), ("normal", "Normal"), ("high", "Alta"), ("urgent", "Urgente")], default="normal", max_length=16)),
                ("source_url", models.URLField(blank=True, default="")),
                ("tags", models.CharField(blank=True, default="", max_length=240)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ikabot_notes", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["status", "-updated_at"],
            },
        ),
        migrations.CreateModel(
            name="ChangeLogEntry",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("component", models.CharField(choices=[("hub", "Hub"), ("agent", "Agent"), ("api", "API"), ("infra", "Infra"), ("docs", "Docs")], default="hub", max_length=16)),
                ("version", models.CharField(blank=True, default="", max_length=40)),
                ("title", models.CharField(max_length=180)),
                ("body", models.TextField(blank=True, default="")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ikabot_changelog_entries", to=settings.AUTH_USER_MODEL)),
                ("note", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="changes", to="notes.note")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
