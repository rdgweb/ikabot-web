import secrets
import uuid

import django.db.models.deletion
from django.db import migrations, models


def create_host_tokens(apps, schema_editor):
    DockerHost = apps.get_model("accounts", "DockerHost")
    for host in DockerHost.objects.filter(enrollment_token=""):
        host.enrollment_token = f"host_{secrets.token_hex(32)}"
        host.save(update_fields=["enrollment_token"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_agent_updater")]

    operations = [
        migrations.CreateModel(
            name="DockerHost",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=80, unique=True)),
                ("description", models.TextField(blank=True, default="")),
                ("enrollment_token", models.CharField(blank=True, default="", max_length=80)),
                ("machine_id", models.CharField(blank=True, default="", max_length=128, null=True, unique=True)),
                ("supervisor_version", models.CharField(blank=True, default="", max_length=64)),
                ("supervisor_image", models.CharField(blank=True, default="", max_length=255)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddField(
            model_name="node",
            name="docker_host",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="nodes", to="accounts.dockerhost"),
        ),
        migrations.RunPython(create_host_tokens, migrations.RunPython.noop),
    ]
