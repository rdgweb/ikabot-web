"""
Telegram webhook endpoint.

Receives updates from Telegram, processes /start commands for
account linking — both global and per-subconta.
"""

import logging
import re

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.telegram.models import TelegramBotConfig, TelegramAccountConfig
from apps.telegram.services.bot_api import send_message
from apps.telegram.services.linking import validate_link_code

logger = logging.getLogger(__name__)

# Regex for 6-digit code after /start
CODE_RE = re.compile(r"^/start\s+(\d{6})$")


class TelegramWebhookView(APIView):
    """
    POST /api/telegram/webhook/<secret>/

    Receives Telegram Update JSON. Validates the webhook secret,
    processes /start commands for linking, and always returns 200 OK.
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # No auth — Telegram sends raw POSTs

    def post(self, request, secret):
        # Validate webhook secret
        try:
            bot_config = TelegramBotConfig.objects.get(pk=1)
        except TelegramBotConfig.DoesNotExist:
            logger.warning("Webhook called but no TelegramBotConfig exists.")
            return Response({"status": "ok"})

        if not bot_config.webhook_secret or secret != bot_config.webhook_secret:
            logger.warning("Webhook called with invalid secret.")
            return Response({"status": "ok"})

        # Parse Telegram Update
        data = request.data
        message = data.get("message", {})
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        from_user = message.get("from", {})
        username = from_user.get("username", "")

        if not text or not chat_id:
            return Response({"status": "ok"})

        # Handle /start <code>
        match = CODE_RE.match(text.strip())
        if match:
            code = match.group(1)
            config = validate_link_code(code, chat_id, username)

            if config:
                # Determine which type was linked
                if isinstance(config, TelegramBotConfig):
                    send_message(
                        chat_id,
                        "Vinculado com sucesso!\n\n"
                        "Este chat recebera <b>todas</b> as notificacoes "
                        "do ikabot hub.",
                    )
                    logger.info(
                        "Telegram GLOBAL linked -> chat %s (@%s)",
                        chat_id, username,
                    )
                elif isinstance(config, TelegramAccountConfig):
                    ga_name = (
                        config.game_account.name
                        or config.game_account.server_id
                    )
                    send_message(
                        chat_id,
                        f"Vinculado com sucesso!\n\n"
                        f"Subconta: <b>{ga_name}</b>\n"
                        f"Este chat recebera notificacoes desta subconta.",
                    )
                    logger.info(
                        "Telegram linked: GA %s -> chat %s (@%s)",
                        config.game_account_id, chat_id, username,
                    )
            else:
                send_message(
                    chat_id,
                    "Codigo invalido ou expirado.\n\n"
                    "Gere um novo codigo no painel e tente novamente.",
                )
                logger.info(
                    "Invalid/expired link code attempted: %s from chat %s",
                    code, chat_id,
                )

        return Response({"status": "ok"})
