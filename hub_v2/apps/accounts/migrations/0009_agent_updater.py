import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_add_login_block_fields_to_gameaccount"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="updater_container",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="node",
            name="updater_last_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="node",
            name="updater_version",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.CreateModel(
            name="AgentUpdateRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("target_version", models.CharField(max_length=64)),
                ("target_image", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("queued", "Na fila"), ("running", "Atualizando"), ("succeeded", "Concluida"), ("failed", "Falhou"), ("cancelled", "Cancelada")], db_index=True, default="queued", max_length=16)),
                ("status_message", models.TextField(blank=True, default="")),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("node", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="update_requests", to="accounts.node")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="agent_update_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
