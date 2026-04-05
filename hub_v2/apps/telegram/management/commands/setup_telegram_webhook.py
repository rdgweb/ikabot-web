"""
Management command: register the Telegram webhook URL.

Usage:
    python manage.py setup_telegram_webhook --url https://example.com

This will call Telegram's setWebhook API with:
    https://example.com/api/telegram/webhook/<webhook_secret>/
"""

from django.core.management.base import BaseCommand

from apps.telegram.models import TelegramBotConfig
from apps.telegram.services.bot_api import set_webhook


class Command(BaseCommand):
    help = "Registra o webhook do Telegram com a URL fornecida."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            required=True,
            help="URL base do hub (ex: https://ikabot.example.com)",
        )

    def handle(self, *args, **options):
        base_url = options["url"].rstrip("/")

        # Ensure bot config exists and has a webhook secret
        config, _ = TelegramBotConfig.objects.get_or_create(pk=1)
        if not config.bot_token:
            self.stderr.write(self.style.ERROR(
                "Bot token nao configurado. Configure em /telegram/ primeiro."
            ))
            return

        # save() auto-generates webhook_secret if empty
        if not config.webhook_secret:
            config.save()

        webhook_url = f"{base_url}/api/telegram/webhook/{config.webhook_secret}/"
        self.stdout.write(f"Registrando webhook: {webhook_url}")

        result = set_webhook(webhook_url)

        if result.get("ok"):
            self.stdout.write(self.style.SUCCESS(
                "Webhook registrado com sucesso!"
            ))
            self.stdout.write(f"  URL: {webhook_url}")
        else:
            desc = result.get("description", "Erro desconhecido")
            self.stderr.write(self.style.ERROR(
                f"Falha ao registrar webhook: {desc}"
            ))
