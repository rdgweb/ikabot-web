from django.db import migrations, models


class Migration(migrations.Migration):
    """N-99: which proxy a manual-control session leaves through (label and check outcome, never an address)."""

    dependencies = [
        ("manual_control", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="manualcontrolsession",
            name="proxy_label",
            field=models.CharField(blank=True, default="", max_length=24),
        ),
        migrations.AddField(
            model_name="manualcontrolsession",
            name="proxy_scope",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="manualcontrolsession",
            name="exit_check",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
