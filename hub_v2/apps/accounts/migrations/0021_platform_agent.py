import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """N-131: platform agents — a node run by the platform for one customer's GameForge account."""

    dependencies = [
        ("accounts", "0020_platform_proxies_billed"),
    ]

    operations = [
        migrations.AddField(model_name="node", name="platform_managed", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="account", name="platform_agent", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="account", name="platform_agent_ends_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="account", name="platform_agent_billed", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="account", name="platform_agent_payment_id", field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddField(model_name="account", name="platform_agent_payment_url", field=models.URLField(blank=True, default="")),
        migrations.AddField(
            model_name="account", name="platform_agent_node",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.node"),
        ),
        migrations.AddField(
            model_name="account", name="previous_node",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.node"),
        ),
        migrations.CreateModel(
            name="AgentProvisionRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("node_ref", models.UUIDField()),
                ("action", models.CharField(choices=[("create", "Criar"), ("remove", "Remover")], max_length=8)),
                ("status", models.CharField(choices=[("queued", "Na fila"), ("running", "Em andamento"), ("succeeded", "Concluído"), ("failed", "Falhou")], db_index=True, default="queued", max_length=10)),
                ("token_enc", models.TextField(blank=True, default="")),
                ("message", models.TextField(blank=True, default="")),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("docker_host", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="provision_requests", to="accounts.dockerhost")),
                ("node", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="provision_requests", to="accounts.node")),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
