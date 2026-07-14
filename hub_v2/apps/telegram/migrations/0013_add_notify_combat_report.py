from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("telegram", "0012_add_notify_cinema_available"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegrambotconfig",
            name="notify_combat_report",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="notify_combat_report",
            field=models.BooleanField(default=False),
        ),
    ]
