"""
Agent API: salva batch de relatórios de espionagem.

POST /api/agent/espionage/reports/
Auth: AgentTokenAuthentication + IsAgent
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GameAccount
from apps.settings_app.models import AppSetting
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from ..models import SpyReport
from .serializers import SpyReportsSaveSerializer

logger = logging.getLogger(__name__)


class SpyReportsSaveView(APIView):
    """
    POST /api/agent/espionage/reports/

    Upserta relatórios de espionagem vindos do runner.
    Retorna: {"saved": N, "new_count": N}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        serializer = SpyReportsSaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            ga = GameAccount.objects.get(pk=data["game_account_id"])
        except GameAccount.DoesNotExist:
            return Response({"error": "GameAccount not found."}, status=status.HTTP_404_NOT_FOUND)

        # Calcula expires_at com base na configuração global (default 48h)
        try:
            expiry_hours = int(AppSetting.objects.get(key="spy_report_expiry_hours").value)
        except (AppSetting.DoesNotExist, ValueError):
            expiry_hours = 48
        expires_at = timezone.now() + timedelta(hours=expiry_hours)

        saved = 0
        new_count = 0

        for report_data in data["reports"]:
            defaults = {
                "game_account": ga,
                "source_city_id": report_data.get("source_city_id") or "",
                "target_city_id": report_data.get("target_city_id") or "",
                "target_city_name": report_data.get("target_city_name") or "",
                "target_x": report_data.get("target_x"),
                "target_y": report_data.get("target_y"),
                "target_owner": report_data.get("target_owner") or "",
                "target_owner_id": report_data.get("target_owner_id") or "",
                "expires_at": expires_at,
                "mission_id": report_data.get("mission_id"),
                "mission_name": report_data.get("mission_name") or "",
                "subject": report_data.get("subject") or "",
                "status": report_data.get("status") or "",
                "result_status": report_data.get("result_status") or "",
                "agents_sent": report_data.get("agents_sent") or 0,
                "agents_lost": report_data.get("agents_lost") or 0,
                "decoys_sent": report_data.get("decoys_sent") or 0,
                "decoys_lost": report_data.get("decoys_lost") or 0,
                "report_html": report_data.get("report_html") or "",
                "report_text": report_data.get("report_text") or "",
                "data_json": report_data.get("data_json") or {},
                "date_str": report_data.get("date_str") or "",
                "is_read": report_data.get("is_read") or False,
            }

            obj, created = SpyReport.objects.get_or_create(
                report_id=report_data["report_id"],
                defaults=defaults,
            )

            if not created:
                for field, value in defaults.items():
                    setattr(obj, field, value)
                update_fields = list(defaults.keys()) + ["updated_at"]
                obj.save(update_fields=update_fields)
            else:
                new_count += 1

            saved += 1

        logger.info(
            "SpyReports: %d salvos (%d novos) para GA %s", saved, new_count, ga.pk
        )
        return Response(
            {"saved": saved, "new_count": new_count},
            status=status.HTTP_200_OK,
        )
