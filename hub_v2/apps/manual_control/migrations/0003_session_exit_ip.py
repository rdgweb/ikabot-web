from django.db import migrations, models


class Migration(migrations.Migration):
    """N-99: the exit address of a manual-control session, shown to the operator."""

    dependencies = [
        ("manual_control", "0002_session_proxy"),
    ]

    operations = [
        migrations.AddField(
            model_name="manualcontrolsession",
            name="exit_ip",
            field=models.CharField(blank=True, default="", max_length=45),
        ),
    ]
