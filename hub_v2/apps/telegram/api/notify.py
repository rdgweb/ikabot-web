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

        if data["event"] == "attack_alert":
            tc = str(metadata.get("target_city_id") or "").strip()
            ga_str = str(game_account.pk) if game_account else ""
            # tipo de defesa: se ha frota atacando e nenhuma tropa, sugere frota
            troops = int(metadata.get("troops") or 0)
            fleet = int(metadata.get("fleet") or 0)
            kind = "fleet" if (fleet > 0 and troops == 0) else "troops"
            if tc and ga_str:
                metadata["reply_markup"] = {
                    "inline_keyboard": [[
                        {"text": "🛡️ Defender", "callback_data": f"defend:{tc}:{ga_str}:{kind}"},
                        {"text": "❌ Ignorar", "callback_data": f"defend_skip:{tc}"},
                    ]]
                }
            if game_account is not None:
                # N-141: Atena and Hefesto as the attack is coming (when the organization keeps the rule on),
                # and a button for each defense miracle the account can activate
                from apps.game.services import miracle_rules

                try:
                    asked = miracle_rules.on_attack_alert(game_account, metadata)
                    buttons = miracle_rules.defense_buttons(game_account, asked)
                except Exception:  # noqa: BLE001 - the alert goes out even when the miracles cannot be read
                    logger.warning("Miracle rules failed for the attack on %s", tc, exc_info=True)
                    buttons = []
                if buttons:
                    markup = metadata.setdefault("reply_markup", {"inline_keyboard": []})
                    markup["inline_keyboard"].append(buttons[:3])

        if data["event"] == "cinema_available" and game_account is not None:
            # N-104: not sent per account any more. The state is kept and one message a day
            # lists every account that has a session, an hour before the game day turns.
            # An agent that does not send the state yet only reports when a session exists.
            from apps.telegram.services.digests import record_cinema

            try:
                reset_in = int(metadata.get("reset_in_seconds") or 0)
            except (TypeError, ValueError):
                reset_in = 0
            record_cinema(
                game_account,
                available=bool(metadata.get("available", True)),
                reset_in_seconds=reset_in,
                city_name=str(metadata.get("city_name") or ""),
            )
            return Response({"ok": True, "sent": False, "queued": True}, status=status.HTTP_200_OK)

        if data["event"] == "island_monitor" and isinstance(metadata.get("changes"), list):
            # N-104: what changed arrives as data; the lines are written here (and kept as text
            # in the history, so a message sent again from there reads the same)
            from apps.telegram.services.digests import island_changes_text

            metadata["changes"], metadata["changes_count"] = island_changes_text(metadata["changes"])

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
