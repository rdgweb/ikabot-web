from django.db import migrations, models


class Migration(migrations.Migration):
    """N-124: going up a tier waits for the prorated difference to be paid; going down waits for the next due date."""

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(model_name="billingaccount", name="pending_limit",
                            field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="billingaccount", name="upgrade_limit",
                            field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="billingaccount", name="upgrade_payment_id",
                            field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddField(model_name="billingaccount", name="upgrade_url",
                            field=models.URLField(blank=True, default="")),
        migrations.AddField(model_name="billingaccount", name="upgrade_value",
                            field=models.DecimalField(decimal_places=2, default=0, max_digits=8)),
    ]
