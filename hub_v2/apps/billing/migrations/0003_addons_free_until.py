from django.db import migrations, models


class Migration(migrations.Migration):
    """N-203: the system's administrator lets an organization use the platform's proxies and agent without charge."""

    dependencies = [("billing", "0002_tier_change")]

    operations = [
        migrations.AddField(
            model_name="billingaccount",
            name="addons_free_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
