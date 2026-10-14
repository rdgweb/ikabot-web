import hashlib

from django.db import migrations, models


def hash_existing(apps, schema_editor):
    """Keep every key that works today working, but stop keeping the key itself."""
    Node = apps.get_model("accounts", "Node")
    for node in Node.objects.exclude(deploy_token=""):
        node.agent_token_hash = hashlib.sha256(node.deploy_token.encode("utf-8")).hexdigest()
        node.deploy_token = ""
        node.save(update_fields=["agent_token_hash", "deploy_token"])


class Migration(migrations.Migration):
    """N-106 stage 3b: the node's agent key is kept only as a hash."""

    dependencies = [
        ("accounts", "0015_n106_names_per_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="agent_token_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RunPython(hash_existing, migrations.RunPython.noop),
    ]
