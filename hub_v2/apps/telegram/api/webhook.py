"""
Telegram webhook endpoint.

Receives updates from Telegram, processes:
  - /start <6-digit-code>                     — account linking
  - /replyto <db_uuid> <text>                 — reply to regular diplomacy message
  - /replyto <db_uuid> yes [text]             — accept treaty/action (optional extra text)
  - /replyto <db_uuid> no [text]              — decline treaty/action (optional extra text)
"""

import json
import logging
import re

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.telegram.models import (
    TelegramBotConfig,
    TelegramAccountConfig,
    TelegramIncomingCommand,
)
from apps.telegram.services.bot_api import send_message
from apps.telegram.services.linking import validate_link_code

logger = logging.getLogger(__name__)


def _create_diplomacy_send_job_from_uuid(
    db_uuid: str,
    yes_no: str,
    extra_text: str,
) -> tuple[bool, str]:
    """Create a Job for action 31 (DiplomacySendRunner) using a DiplomacyMessage UUID.

    Looks up receiver_id, game_account, and available actions from the DB record.

    Args:
        db_uuid:    UUID of the DiplomacyMessage.
        yes_no:     "yes", "no", or "" (empty = regular text reply).
        extra_text: Text to include in the message (optional for yes/no, required for plain reply).

    Returns:
        (success: bool, error_msg: str)
    """
    from apps.accounts.models import GameAccount
    from apps.diplomacy.models import DiplomacyMessage
    from apps.jobs.services.workflows import create_job_with_workflow

    try:
        dm = DiplomacyMessage.objects.select_related(
            "game_account__account__node"
        ).get(pk=db_uuid)
    except DiplomacyMessage.DoesNotExist:
        logger.warning("DiplomacySend: DiplomacyMessage %s not found", db_uuid)
        return False, "Mensagem não encontrada. O ID pode ter expirado."

    ga = dm.game_account
    actions = dm.actions  # list of {"msg_type": int, "receiver_id": str}

    if yes_no == "yes":
        # Find the accept action (msg_type 79, or first available)
        accept = next((a for a in actions if a["msg_type"] == 79), None) or (actions[0] if actions else None)
        if not accept:
            return False, "Esta mensagem não tem ação de aceitar disponível."
        msg_type = accept["msg_type"]
        receiver_id = accept["receiver_id"]

    elif yes_no == "no":
        # Find the decline action (msg_type 80, or second available)
        decline = next((a for a in actions if a["msg_type"] == 80), None)
        if not decline and len(actions) > 1:
            decline = actions[1]
        if not decline:
            return False, "Esta mensagem não tem ação de recusar disponível."
        msg_type = decline["msg_type"]
        receiver_id = decline["receiver_id"]

    else:
        # Regular text reply
        if not extra_text:
            return False, "Forneça um texto para responder."
        if not dm.receiver_id:
            return False, "Esta mensagem não tem receiverId para resposta."
        msg_type = 50
        receiver_id = dm.receiver_id

    inputs: dict = {
        "receiver_id": int(receiver_id),
        "msg_type": msg_type,
    }
    if extra_text:
        inputs["content"] = extra_text
    if dm.reply_to_game_id:
        inputs["reply_to"] = int(dm.reply_to_game_id)

    create_job_with_workflow(
        account=ga.account,
        game_account=ga,
        node=ga.account.node,
        action_code=31,
        inputs=inputs,
        status="queued",
    )
    logger.info(
        "Created diplomacy_send job: ga=%s receiver=%s msg_type=%s uuid=%s",
        ga.pk, receiver_id, msg_type, db_uuid,
    )
    return True, ""


class TelegramWebhookView(APIView):
    """
    POST /api/telegram/webhook/<secret>/

    Receives Telegram Update JSON. Validates the webhook secret,
    processes all supported commands, and always returns 200 OK.
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

        data = request.data

        # Callback queries no longer used (inline keyboard removed)
        if data.get("callback_query"):
            return Response({"status": "ok"})

        # ── Text message ───────────────────────────────────────────────────
        message = data.get("message", {})
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        from_user = message.get("from", {})
        username = from_user.get("username", "")

        if not text or not chat_id:
            return Response({"status": "ok"})

        text_stripped = text.strip()
        link_command = TelegramIncomingCommand.command_for(
            TelegramIncomingCommand.COMMAND_LINK
        )
        reply_command = TelegramIncomingCommand.command_for(
            TelegramIncomingCommand.COMMAND_DIPLOMACY_REPLY
        )
        code_re = (
            re.compile(rf"^{re.escape(link_command)}\s+(\d{{6}})$")
            if link_command else None
        )
        replyto_re = (
            re.compile(
                rf"^{re.escape(reply_command)}\s+([\w-]{{36}})\s+([\s\S]+)$",
                re.IGNORECASE,
            )
            if reply_command else None
        )

        # Handle /start <code>
        match = code_re.match(text_stripped) if code_re else None
        if match:
            self._handle_start(match.group(1), chat_id, username)
            return Response({"status": "ok"})

        # Handle configured reply command: <command> <db_uuid> [yes|no] [text]
        match = replyto_re.match(text_stripped) if replyto_re else None
        if match:
            self._handle_replyto(match, chat_id)
            return Response({"status": "ok"})

        return Response({"status": "ok"})

    # ── Handlers ──────────────────────────────────────────────────────────

    def _handle_start(self, code: str, chat_id: str, username: str) -> None:
        config = validate_link_code(code, chat_id, username)

        if config:
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

    def _handle_replyto(self, match: re.Match, chat_id: str) -> None:
        """Handle /replyto <db_uuid> [yes|no] [text]."""
        db_uuid = match.group(1)
        rest = match.group(2).strip()

        # Parse yes/no keyword
        yes_no = ""
        extra_text = rest
        lower = rest.lower()
        if lower.startswith("yes") and (len(lower) == 3 or lower[3] in (" ", "\n")):
            yes_no = "yes"
            extra_text = rest[3:].strip()
        elif lower.startswith("no") and (len(lower) == 2 or lower[2] in (" ", "\n")):
            yes_no = "no"
            extra_text = rest[2:].strip()

        ok, error = _create_diplomacy_send_job_from_uuid(db_uuid, yes_no, extra_text)
        if ok:
            if yes_no == "yes":
                send_message(chat_id, "✅ Aceitar — job criado na fila.")
            elif yes_no == "no":
                send_message(chat_id, "❌ Recusar — job criado na fila.")
            else:
                send_message(chat_id, "↩️ Resposta enviada para o agente.")
        else:
            send_message(chat_id, f"❌ {error}")
