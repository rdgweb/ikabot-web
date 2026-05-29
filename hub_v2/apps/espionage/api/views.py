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

_RESOURCE_NAMES = {
    "Madeira": "wood", "Wood": "wood",
    "Vinho": "wine", "Wine": "wine",
    "Mármore": "marble", "Marble": "marble",
    "Cristal": "glass", "Crystal": "glass", "Glass": "glass",
    "Enxofre": "sulfur", "Sulfur": "sulfur",
}


def _parse_resources_from_data(data_json: dict) -> dict[str, int]:
    """Extract resource amounts from spy report data_json (mission 3/stocks)."""
    resources: dict[str, int] = {}
    # Mission 3 (Estoques): data_json may contain {"stocks": {"Madeira": "12.345", ...}}
    stocks = data_json.get("stocks") or data_json.get("resources") or {}
    if isinstance(stocks, dict):
        for name, val in stocks.items():
            key = _RESOURCE_NAMES.get(str(name).strip())
            if key:
                raw = str(val).replace(".", "").replace(",", "").strip()
                try:
                    resources[key] = int(raw)
                except ValueError:
                    pass
    # Fallback: look for direct resource keys
    for name, key in _RESOURCE_NAMES.items():
        if name in data_json and key not in resources:
            raw = str(data_json[name]).replace(".", "").replace(",", "").strip()
            try:
                resources[key] = int(raw)
            except ValueError:
                pass
    return resources


def _parse_troops_from_data(data_json: dict) -> dict[str, int]:
    """Extract troop counts {unit_id: qty} from spy report data_json (mission 5/6)."""
    troops: dict[str, int] = {}
    raw = data_json.get("troops") or data_json.get("army") or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                troops[str(int(k))] = int(v)
            except (ValueError, TypeError):
                pass
    return troops


class SpyIntelView(APIView):
    """GET /api/agent/espionage/intel/?target_city_id=X&game_account_id=Y

    Returns consolidated intel for target city from the latest valid spy reports.
    {
        "resources": {"wood": N, ...},
        "troops":    {"315": 4},
        "fleet":     {},
        "wall_level": 1,
        "last_updated": "ISO datetime",
    }
    Returns {} if no intel found.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request):
        target_city_id = request.query_params.get("target_city_id", "").strip()
        ga_id          = request.query_params.get("game_account_id", "").strip()

        if not target_city_id:
            return Response({"error": "target_city_id required."}, status=status.HTTP_400_BAD_REQUEST)

        qs = SpyReport.objects.filter(
            target_city_id=target_city_id,
            result_status__icontains="sucess",  # only successful reports
        ).order_by("-created_at")
        if ga_id:
            qs = qs.filter(game_account_id=ga_id)

        # Also include reports without explicit result_status filter as fallback
        if not qs.exists():
            qs = SpyReport.objects.filter(target_city_id=target_city_id).order_by("-created_at")
            if ga_id:
                qs = qs.filter(game_account_id=ga_id)

        if not qs.exists():
            return Response({})

        resources: dict[str, int] = {}
        troops:    dict[str, int] = {}
        fleet:     dict[str, int] = {}
        wall_level = 1
        last_updated = None

        # Consolidate from most recent reports per mission
        seen_missions: set[int | None] = set()
        for report in qs[:20]:
            mid = report.mission_id
            if mid in seen_missions:
                continue
            seen_missions.add(mid)

            data = report.data_json or {}
            if not last_updated or report.created_at > last_updated:
                last_updated = report.created_at

            parsed_res = _parse_resources_from_data(data)
            if parsed_res and not resources:
                resources = parsed_res

            parsed_troops = _parse_troops_from_data(data)
            if parsed_troops and not troops:
                troops = parsed_troops

            # Wall level from data_json if present
            if "wall_level" in data:
                try:
                    wall_level = int(data["wall_level"])
                except (TypeError, ValueError):
                    pass

        return Response({
            "resources":    resources,
            "troops":       troops,
            "fleet":        fleet,
            "wall_level":   wall_level,
            "last_updated": last_updated.isoformat() if last_updated else None,
        })


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
