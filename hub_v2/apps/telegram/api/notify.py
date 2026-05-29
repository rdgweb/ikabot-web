"""
Agent notification endpoint.

POST /api/agent/notify/

Allows agents to send Telegram notifications for events they detect
in the game (attacks, low wine, build complete, etc.).
"""

import logging

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent
from apps.accounts.models import Account, GameAccount

from apps.telegram.services.notifications import notify
from apps.telegram.models import TelegramIncomingCommand

logger = logging.getLogger(__name__)


class AgentNotifySerializer(serializers.Serializer):
    event = serializers.CharField(help_text="Event key (e.g. attack_alert, low_wine)")
    game_account_id = serializers.UUIDField(required=False, allow_null=True)
    account_id = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(required=False, default="", allow_blank=True)
    body = serializers.CharField(required=False, default="", allow_blank=True)
    agent_name = serializers.CharField(required=False, default="", allow_blank=True)
    metadata = serializers.DictField(required=False, default=dict)


class AgentNotifyView(APIView):
    """
    POST /api/agent/notify/

    Agent sends a notification event. The hub resolves toggles, chat_id,
    formats the message, and sends it via Telegram.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = AgentNotifySerializer

    def post(self, request):
        serializer = AgentNotifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Resolve models
        game_account = None
        account = None

        if data.get("game_account_id"):
            try:
                game_account = GameAccount.objects.select_related("account").get(
                    pk=data["game_account_id"]
                )
                account = game_account.account
            except GameAccount.DoesNotExist:
                pass

        if not account and data.get("account_id"):
            try:
                account = Account.objects.get(pk=data["account_id"])
            except Account.DoesNotExist:
                pass

        metadata = dict(data.get("metadata", {}))

        if data["event"] == "raid_alert":
            # Build inline keyboard with Roubar/Ignorar buttons
            tc  = str(metadata.get("target_city_id") or "").strip()
            iid = str(metadata.get("island_id") or "").strip()
            ga_str = str(game_account.pk) if game_account else ""
            can_win = metadata.get("can_win", True)
            win_icon = "✅" if can_win else "⚠️"
            if tc and iid and ga_str:
                cb_raid = f"raid_now:{tc}:{iid}:{ga_str}"
                cb_skip = f"raid_skip:{tc}"
                metadata["reply_markup"] = {
                    "inline_keyboard": [[
                        {"text": f"{win_icon} Roubar agora", "callback_data": cb_raid},
                        {"text": "❌ Ignorar",               "callback_data": cb_skip},
                    ]]
                }

        if data["event"] == "diplomacy_message":
            reply_command = TelegramIncomingCommand.command_for(
                TelegramIncomingCommand.COMMAND_DIPLOMACY_REPLY
            )
            db_uuid = str(metadata.get("db_uuid") or "").strip()
            if db_uuid:
                metadata.setdefault("reply_command", f"{reply_command} {db_uuid} <texto>")
                metadata.setdefault("accept_command", f"{reply_command} {db_uuid} yes")
                metadata.setdefault("decline_command", f"{reply_command} {db_uuid} no")

        sent = notify(
            event_key=data["event"],
            game_account=game_account,
            account=account,
            title=data.get("title", ""),
            body=data.get("body", ""),
            agent_name=data.get("agent_name", ""),
            **metadata,
        )

        return Response(
            {"ok": True, "sent": sent},
            status=status.HTTP_200_OK,
        )
