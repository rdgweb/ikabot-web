import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """N-129: a platform proxy lent to one customer's GameForge account."""

    dependencies = [
        ("proxy", "0004_n106_organization"),
        ("accounts", "0019_platform_proxies"),
    ]

    operations = [
        migrations.AddField(
            model_name="proxyprofile",
            name="lent_to",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="lent_proxies", to="accounts.account"),
        ),
    ]
