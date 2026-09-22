from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_docker_host"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameaccount",
            name="user_agent",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
