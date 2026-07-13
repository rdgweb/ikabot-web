from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("telegram", "0011_add_notify_raid_alert"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegrambotconfig",
            name="notify_cinema_available",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="notify_cinema_available",
            field=models.BooleanField(default=False),
        ),
    ]
