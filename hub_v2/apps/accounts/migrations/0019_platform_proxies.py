from django.db import migrations, models


class Migration(migrations.Migration):
    """N-129: the platform-proxies add-on per GameForge account (and when a switched-off one ends)."""

    dependencies = [
        ("accounts", "0018_n106_host_token_hash"),
    ]

    operations = [
        migrations.AddField(model_name="account", name="platform_proxies",
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name="account", name="platform_proxies_ends_at",
                            field=models.DateTimeField(blank=True, null=True)),
    ]
