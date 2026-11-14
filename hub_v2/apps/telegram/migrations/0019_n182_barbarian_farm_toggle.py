"""N-182: the barbarian farm's reinforcement notice gets its own toggle (it was sent as an attack alert)."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("telegram", "0018_n106_telegram_per_organization")]

    operations = [
        migrations.AddField(
            model_name="telegrambotconfig",
            name="notify_barbarian_farm",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="notify_barbarian_farm",
            field=models.BooleanField(default=True),
        ),
    ]
