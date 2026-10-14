from django.db import migrations, models


class Migration(migrations.Migration):
    """N-106 stage 3b-2: each node says whether its agent uses the broker or only HTTPS."""

    dependencies = [
        ("accounts", "0016_n106_agent_token_hash"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="transport",
            field=models.CharField(choices=[("redis", "Redis (interno)"), ("https", "HTTPS")], default="redis", max_length=8),
        ),
    ]
