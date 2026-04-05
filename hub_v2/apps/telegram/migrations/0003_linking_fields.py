"""
Add linking fields to TelegramAccountConfig and webhook_secret to TelegramBotConfig.

Task 26: Telegram onboarding with confirmation code flow.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("telegram", "0002_config_to_gameaccount"),
    ]

    operations = [
        # TelegramBotConfig: webhook_secret
        migrations.AddField(
            model_name="telegrambotconfig",
            name="webhook_secret",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        # TelegramAccountConfig: linking fields
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="link_code",
            field=models.CharField(blank=True, default="", max_length=6),
        ),
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="link_code_created_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="link_status",
            field=models.CharField(
                choices=[
                    ("unlinked", "Nao vinculado"),
                    ("pending", "Aguardando confirmacao"),
                    ("linked", "Vinculado"),
                ],
                default="unlinked",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="telegramaccountconfig",
            name="telegram_username",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
