"""
Telegram webhook endpoint.

Receives updates from Telegram, processes:
  - /start <6-digit-code>         — account linking
  - /replyto <msg_id>:<receiver_id>:<ga_id> <text>  — reply to diplomacy message
  - callback_query dip_action:<msg_type>:<receiver_id>:<ga_id>  — diplomacy action (any type)
  - callback_query treaty_accept:<receiver_id>:<ga_id>   — legacy format (backwards compat)
  - callback_query treaty_decline:<receiver_id>:<ga_id>  — legacy format (backwards compat)
"""

import json
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

# /replyto <msg_id>:<receiver_id>:<ga_id> <text>
# ga_id is a UUID (36 chars)
REPLYTO_RE = re.compile(
    r"^/replyto\s+(\d+):(\d+):([\w-]{36})\s+([\s\S]+)$",
    re.IGNORECASE,
)

# callback_data formats:
#   dip_action:<msg_type>:<receiver_id>:<ga_id>   — generic (new)
#   treaty_accept:<receiver_id>:<ga_id>            — legacy (backwards compat)
#   treaty_decline:<receiver_id>:<ga_id>           — legacy (backwards compat)
DIP_ACTION_CB_RE = re.compile(
    r"^dip_action:(\d+):(\d+):([\w-]{36})$"
)
TREATY_CB_RE = re.compile(
    r"^treaty_(accept|decline):(\d+):([\w-]{36})$"
)


def _create_diplomacy_send_job(ga_id: str, receiver_id: str, msg_type: int, content: str = "", reply_to: str = "") -> bool:
    """Create a Job for action 31 (DiplomacySendRunner).

    Returns True on success, False on failure.
    """
    from apps.accounts.models import GameAccount
    from apps.jobs.models import Job

    try:
        ga = GameAccount.objects.select_related("account__node").get(pk=ga_id)
    except GameAccount.DoesNotExist:
        logger.warning("DiplomacySend: GameAccount %s not found", ga_id)
        return False

    inputs: dict = {
        "receiver_id": int(receiver_id),
        "msg_type": msg_type,
    }
    if content:
        inputs["content"] = content
    if reply_to:
        inputs["reply_to"] = int(reply_to)

    # city_id is not strictly required in runner 31 (it will load action_request
    # after login), but pass 0 as a placeholder — the runner ignores it
    inputs["city_id"] = 0

    Job.objects.create(
        account=ga.account,
        game_account=ga,
        node=ga.account.node,
        action_code=31,
        inputs_json=json.dumps(inputs),
        status="queued",
    )
    logger.info(
        "Created diplomacy_send job: ga=%s receiver=%s msg_type=%s",
        ga_id, receiver_id, msg_type,
    )
    return True


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

        # ── Callback query (inline keyboard button click) ──────────────────
        callback_query = data.get("callback_query")
        if callback_query:
            self._handle_callback_query(callback_query)
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

        # Handle /start <code>
        match = CODE_RE.match(text_stripped)
        if match:
            self._handle_start(match.group(1), chat_id, username)
            return Response({"status": "ok"})

        # Handle /replyto <msg_id>:<receiver_id>:<ga_id> <text>
        match = REPLYTO_RE.match(text_stripped)
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
        """Handle /replyto <msg_id>:<receiver_id>:<ga_id> <text>."""
        msg_id = match.group(1)
        receiver_id = match.group(2)
        ga_id = match.group(3)
        reply_text = match.group(4).strip()

        ok = _create_diplomacy_send_job(
            ga_id=ga_id,
            receiver_id=receiver_id,
            msg_type=50,
            content=reply_text,
            reply_to=msg_id,
        )
        if ok:
            send_message(chat_id, "✅ Resposta enviada para o agente.")
        else:
            send_message(chat_id, "❌ Falha ao criar job de resposta. Verifique o ID da subconta.")

    def _handle_callback_query(self, callback_query: dict) -> None:
        """Handle inline keyboard callback (treaty accept/decline)."""
        callback_id = callback_query.get("id", "")
        callback_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))

        # Try new generic dip_action format first
        match = DIP_ACTION_CB_RE.match(callback_data)
        if match:
            msg_type = int(match.group(1))
            receiver_id = match.group(2)
            ga_id = match.group(3)
            ok = _create_diplomacy_send_job(
                ga_id=ga_id,
                receiver_id=receiver_id,
                msg_type=msg_type,
            )
            if ok:
                self._answer_callback(callback_id, text="✅ Job criado!")
                if chat_id:
                    send_message(chat_id, f"✅ Ação registrada (tipo {msg_type}). Job criado na fila.")
            else:
                self._answer_callback(callback_id, text="❌ Falha ao criar job.")
                if chat_id:
                    send_message(chat_id, "❌ Falha ao criar job. Verifique o painel.")
            return

        # Legacy treaty_accept / treaty_decline format (backwards compat)
        match = TREATY_CB_RE.match(callback_data)
        if match:
            action = match.group(1)        # "accept" or "decline"
            receiver_id = match.group(2)
            ga_id = match.group(3)
            msg_type = 79 if action == "accept" else 80
            action_label = "Aceitar" if action == "accept" else "Recusar"
            ok = _create_diplomacy_send_job(
                ga_id=ga_id,
                receiver_id=receiver_id,
                msg_type=msg_type,
            )
            if ok:
                self._answer_callback(callback_id, text=f"✅ {action_label} — job criado!")
                if chat_id:
                    verb = "aceito" if action == "accept" else "recusado"
                    send_message(chat_id, f"✅ Tratado cultural {verb}. Job criado na fila.")
            else:
                self._answer_callback(callback_id, text="❌ Falha ao criar job.")
                if chat_id:
                    send_message(chat_id, "❌ Falha ao criar job de tratado. Verifique o painel.")
            return

        logger.debug("Unhandled callback_data: %s", callback_data)
        self._answer_callback(callback_id)

    def _answer_callback(self, callback_query_id: str, text: str = "") -> None:
        """Answer a callback query to dismiss the loading spinner in Telegram."""
        from apps.telegram.services.bot_api import _get_token
        import requests as _requests

        token = _get_token()
        if not token or not callback_query_id:
            return
        try:
            from apps.telegram.services.bot_api import API_BASE, TIMEOUT
            url = f"{API_BASE.format(token=token)}/answerCallbackQuery"
            payload: dict = {"callback_query_id": callback_query_id}
            if text:
                payload["text"] = text
            _requests.post(url, json=payload, timeout=TIMEOUT)
        except Exception:
            pass
