from django.db import migrations, models


class Migration(migrations.Migration):
    """N-129: the platform-proxies add-on was already in a paid monthly charge (switching off before it isn't free)."""

    dependencies = [
        ("accounts", "0019_platform_proxies"),
    ]

    operations = [
        migrations.AddField(model_name="account", name="platform_proxies_billed",
                            field=models.BooleanField(default=False)),
    ]
