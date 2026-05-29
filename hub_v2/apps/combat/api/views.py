"""Agent API — save combat reports from the raid runner."""
from __future__ import annotations

import logging
from datetime import datetime

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GameAccount
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from ..models import CombatReport

logger = logging.getLogger(__name__)


class CombatReportSaveView(APIView):
    """POST /api/agent/combat/reports/

    Upserts a combat report from the raid runner.
    Payload fields (all optional except combat_id and game_account_id):
        game_account_id, combat_id, combat_type, result, combat_date,
        total_rounds, source_city_id, source_city_name, target_city_id,
        target_city_name, target_owner, target_owner_id,
        loot_json, attacker_losses, defender_losses,
        summary_html, detailed_html
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        data = request.data
        game_account_id = str(data.get("game_account_id") or "").strip()
        combat_id_raw   = data.get("combat_id")

        if not combat_id_raw:
            return Response({"error": "combat_id required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            combat_id = int(combat_id_raw)
        except (TypeError, ValueError):
            return Response({"error": "combat_id must be integer."}, status=status.HTTP_400_BAD_REQUEST)

        ga = None
        if game_account_id:
            try:
                ga = GameAccount.objects.get(pk=game_account_id)
            except GameAccount.DoesNotExist:
                pass

        # Parse combat_date
        combat_date = None
        date_raw = str(data.get("combat_date") or "").strip()
        if date_raw:
            for fmt in ("%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    combat_date = timezone.make_aware(datetime.strptime(date_raw, fmt))
                    break
                except ValueError:
                    continue

        loot_json       = data.get("loot_json") or {}
        attacker_losses = data.get("attacker_losses") or {}
        defender_losses = data.get("defender_losses") or {}
        total_loot      = int(data.get("total_loot") or sum(int(v) for v in loot_json.values()))

        defaults = {
            "game_account":   ga,
            "combat_type":    str(data.get("combat_type") or "land").strip(),
            "result":         str(data.get("result") or "").strip(),
            "combat_date":    combat_date,
            "total_rounds":   int(data.get("total_rounds") or 1),
            "source_city_id":  str(data.get("source_city_id") or "").strip(),
            "source_city_name": str(data.get("source_city_name") or "").strip(),
            "target_city_id":  str(data.get("target_city_id") or "").strip(),
            "target_city_name": str(data.get("target_city_name") or "").strip(),
            "target_owner":    str(data.get("target_owner") or "").strip(),
            "target_owner_id": str(data.get("target_owner_id") or "").strip(),
            "loot_json":       loot_json,
            "total_loot":      total_loot,
            "attacker_losses": attacker_losses,
            "defender_losses": defender_losses,
            "summary_html":    str(data.get("summary_html") or ""),
            "detailed_html":   str(data.get("detailed_html") or ""),
        }

        obj, created = CombatReport.objects.update_or_create(
            combat_id=combat_id,
            defaults=defaults,
        )

        logger.info(
            "CombatReport %s %s (combat_id=%s result=%s loot=%s)",
            "criado" if created else "atualizado",
            obj.pk, combat_id, obj.result, total_loot,
        )

        return Response(
            {"ok": True, "combat_report_id": str(obj.pk), "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
