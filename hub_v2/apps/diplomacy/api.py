"""
Agent API: salva batch de mensagens de diplomacia.

POST /api/agent/diplomacy/messages/
Auth: AgentTokenAuthentication + IsAgent
"""

from __future__ import annotations

import json
import logging

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GameAccount
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from .models import DiplomacyMessage

logger = logging.getLogger(__name__)


class _ActionSerializer(serializers.Serializer):
    msg_type = serializers.IntegerField()
    receiver_id = serializers.CharField()


class _MessageSerializer(serializers.Serializer):
    game_msg_id = serializers.CharField(max_length=32)
    sender = serializers.CharField(required=False, default="", allow_blank=True)
    subject = serializers.CharField(required=False, default="", allow_blank=True)
    body = serializers.CharField(required=False, default="", allow_blank=True)
    game_date = serializers.CharField(required=False, default="", allow_blank=True)
    receiver_id = serializers.CharField(required=False, default="", allow_blank=True)
    reply_to = serializers.CharField(required=False, default="", allow_blank=True)
    actions = _ActionSerializer(many=True, required=False, default=list)


class _SaveSerializer(serializers.Serializer):
    game_account_id = serializers.UUIDField()
    messages = _MessageSerializer(many=True)


class DiplomacyMessagesSaveView(APIView):
    """
    POST /api/agent/diplomacy/messages/

    Upserta mensagens de diplomacia vindas do runner.
    Retorna: {"saved": N, "new_count": N}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        serializer = _SaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            ga = GameAccount.objects.get(pk=data["game_account_id"])
        except GameAccount.DoesNotExist:
            return Response({"error": "GameAccount not found."}, status=status.HTTP_404_NOT_FOUND)

        saved = 0
        new_count = 0
        # game_msg_id → str(db_uuid) — so the runner can reference messages by UUID
        message_ids: dict[str, str] = {}

        for msg_data in data["messages"]:
            actions_json = json.dumps(msg_data.get("actions") or [])
            defaults = {
                "sender": msg_data.get("sender") or "",
                "subject": msg_data.get("subject") or "",
                "body": msg_data.get("body") or "",
                "game_date": msg_data.get("game_date") or "",
                "receiver_id": msg_data.get("receiver_id") or "",
                "reply_to_game_id": msg_data.get("reply_to") or "",
                "actions_json": actions_json,
            }

            obj, created = DiplomacyMessage.objects.get_or_create(
                game_account=ga,
                game_msg_id=msg_data["game_msg_id"],
                defaults={**defaults, "status": "new"},
            )

            if not created:
                # Atualiza campos mutáveis mas preserva status (pode ter sido marcado como lido)
                for field, value in defaults.items():
                    setattr(obj, field, value)
                obj.save(update_fields=list(defaults.keys()) + ["updated_at"])
            else:
                new_count += 1

            message_ids[msg_data["game_msg_id"]] = str(obj.pk)
            saved += 1

        logger.info("DiplomacyMessages: %d salvas (%d novas) para GA %s", saved, new_count, ga.pk)
        return Response(
            {"saved": saved, "new_count": new_count, "message_ids": message_ids},
            status=status.HTTP_200_OK,
        )
