import hashlib

from django.db import migrations, models


def hash_existing(apps, schema_editor):
    """The supervisors keep working with the key they have; the hub stops keeping it."""
    DockerHost = apps.get_model("accounts", "DockerHost")
    for host in DockerHost.objects.exclude(enrollment_token=""):
        host.enrollment_token_hash = hashlib.sha256(host.enrollment_token.encode("utf-8")).hexdigest()
        host.enrollment_token = ""
        host.save(update_fields=["enrollment_token_hash", "enrollment_token"])


class Migration(migrations.Migration):
    """N-106: the supervisor's key is kept only as a hash."""

    dependencies = [
        ("accounts", "0017_n106_node_transport"),
    ]

    operations = [
        migrations.AddField(
            model_name="dockerhost",
            name="enrollment_token_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RunPython(hash_existing, migrations.RunPython.noop),
    ]
