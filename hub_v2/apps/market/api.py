"""
Agent API views for internal market operations.

Endpoints:
  POST /api/agent/market/orders/<uuid>/sell-complete/
    — Runner 802 calls this when the sell offer has been created in-game.
      Hub creates the buy_job (801) on the buyer's node.

  POST /api/agent/market/orders/
    — Hub-side: create an InternalMarketOrder (from construction runner or UI).
"""

from __future__ import annotations

import logging

from rest_framework import serializers
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GameAccount
from apps.jobs.models import Job
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from .models import ConstructionMarketIntervention, InternalMarketOrder
from .services import (
    complete_internal_order,
    create_buy_job,
    create_construction_market_intervention,
    create_internal_order_result,
)

logger = logging.getLogger(__name__)


class ConstructionMarketInterventionRequestSerializer(serializers.Serializer):
    game_account_id = serializers.UUIDField()
    source_job_id = serializers.UUIDField(required=False, allow_null=True)
    wait_reason = serializers.CharField()
    eta_seconds = serializers.IntegerField(min_value=0)
    city_id = serializers.IntegerField(required=False, allow_null=True)
    city_name = serializers.CharField(required=False, allow_blank=True, default="")
    building_name = serializers.CharField(required=False, allow_blank=True, default="")
    needed_resource_idx = serializers.IntegerField(min_value=0, max_value=4)
    needed_amount = serializers.IntegerField(min_value=1)
    available_gold = serializers.IntegerField(required=False, default=0)
    min_gold = serializers.IntegerField(required=False, default=0)
    estimated_buy_unit_min = serializers.IntegerField(required=False, default=0)
    estimated_buy_unit_avg = serializers.IntegerField(required=False, default=0)
    estimated_buy_cost_min = serializers.IntegerField(required=False, default=0)
    estimated_buy_cost_avg = serializers.IntegerField(required=False, default=0)
    sale_city_id = serializers.IntegerField()
    sale_city_name = serializers.CharField(required=False, allow_blank=True, default="")
    sale_branchoffice_pos = serializers.IntegerField(min_value=0)
    sale_resource_idx = serializers.IntegerField(min_value=0, max_value=4)
    sale_amount = serializers.IntegerField(min_value=1)
    sale_price_min = serializers.IntegerField(min_value=0)
    sale_price_max = serializers.IntegerField(min_value=0)
    sale_price_target = serializers.IntegerField(min_value=0)
    estimated_sale_gold = serializers.IntegerField(min_value=0)
    can_fund_min = serializers.BooleanField(required=False, default=False)
    can_fund_avg = serializers.BooleanField(required=False, default=False)


class MarketSellCompleteView(APIView):
    """POST /api/agent/market/orders/<uuid>/sell-complete/

    Called by Runner 802 when the sell offer has been placed in-game.
    Creates the buy_job (801) on the buyer's node.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request, order_id):
        try:
            order = InternalMarketOrder.objects.select_related(
                "buyer_game_account",
                "buyer_game_account__account",
                "buyer_game_account__account__node",
            ).get(pk=order_id)
        except InternalMarketOrder.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status not in ("matched", "jobs_created"):
            return Response(
                {"error": f"Order is in status '{order.status}', expected matched/jobs_created."},
                status=status.HTTP_409_CONFLICT,
            )

        price_used = request.data.get("unit_price")
        price_min = request.data.get("price_min")
        price_max = request.data.get("price_max")
        updates = []
        if price_used not in (None, ""):
            order.unit_price = int(price_used)
            updates.append("unit_price")
        if price_min not in (None, ""):
            order.price_min = int(price_min)
            updates.append("price_min")
        if price_max not in (None, ""):
            order.price_max = int(price_max)
            updates.append("price_max")
        if updates:
            updates.append("updated_at")
            order.save(update_fields=updates)

        buy_job = create_buy_job(order)
        if buy_job is None:
            return Response(
                {"error": "Could not create buy_job — buyer_game_account missing."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "ok": True,
                "order_id": str(order.pk),
                "buy_job_id": str(buy_job.pk),
                "status": order.status,
            },
            status=status.HTTP_201_CREATED,
        )


class MarketOrderCompleteView(APIView):
    """POST /api/agent/market/orders/<uuid>/complete/

    Called by Runner 801 when the purchase has been executed in-game.
    Marks the order as completed.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request, order_id):
        try:
            order = InternalMarketOrder.objects.get(pk=order_id)
        except InternalMarketOrder.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = complete_internal_order(order)
        payload = {
            "ok": bool(result.get("ok")),
            "order_id": str(order.pk),
            "status": str(result.get("status") or order.status),
            "completed": bool(result.get("completed")),
            "created_redistribution": bool(result.get("created_redistribution")),
        }
        if result.get("redistribution_job_id"):
            payload["redistribution_job_id"] = result["redistribution_job_id"]
        return Response(payload)


class MarketOrderCreateView(APIView):
    """POST /api/agent/market/orders/create/

    Create an InternalMarketOrder from an agent runner (e.g., construction
    runner requesting resource transport via internal market).

    Body: { "game_account_id": "<uuid>", "resource_idx": 2, "amount": 5000, "unit_price": 0 }
    unit_price=0 means auto (agent fetches limits from game and uses midpoint).
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        game_account_id = request.data.get("game_account_id")
        resource_idx = request.data.get("resource_idx")
        amount = request.data.get("amount")
        unit_price = int(request.data.get("unit_price", 0))
        preferred_buyer_city_id = request.data.get("preferred_buyer_city_id")
        target_city_id = request.data.get("target_city_id")
        source_job_id = request.data.get("source_job_id")
        source_action_code = request.data.get("source_action_code")
        source_reason = str(request.data.get("source_reason") or "").strip()
        reason_detail = str(request.data.get("reason_detail") or "").strip()
        production_eta_seconds = request.data.get("production_eta_seconds")
        missing_resource_keys = str(request.data.get("missing_resource_keys") or "").strip()

        if not game_account_id or resource_idx is None or not amount:
            return Response(
                {"error": "game_account_id, resource_idx and amount are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            buyer_ga = GameAccount.objects.select_related(
                "account", "account__node"
            ).get(pk=game_account_id)
        except GameAccount.DoesNotExist:
            return Response(
                {"error": "GameAccount not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = create_internal_order_result(
            buyer_ga=buyer_ga,
            resource_idx=int(resource_idx),
            amount=int(amount),
            unit_price=unit_price,
            preferred_buyer_city_id=int(preferred_buyer_city_id) if preferred_buyer_city_id not in (None, "") else None,
            target_city_id=int(target_city_id) if target_city_id not in (None, "") else None,
            source_job_id=str(source_job_id).strip() if source_job_id not in (None, "") else None,
            source_action_code=int(source_action_code) if source_action_code not in (None, "") else None,
            source_reason=source_reason,
            reason_detail=reason_detail,
            production_eta_seconds=int(production_eta_seconds) if production_eta_seconds not in (None, "") else None,
            missing_resource_keys=missing_resource_keys,
        )

        if not result.ok or result.order is None:
            return Response(
                {"ok": False, "error": result.error, "detail": result.detail},
                status=status.HTTP_200_OK,
            )
        order = result.order

        return Response(
            {
                "ok": True,
                "order_id": str(order.pk),
                "sell_job_id": str(order.sell_job_id) if order.sell_job_id else None,
                "status": order.status,
                "buyer_city_id": order.buyer_city_id,
                "target_city_id": order.target_city_id,
            },
            status=status.HTTP_201_CREATED,
        )


class ConstructionMarketInterventionRequestView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = ConstructionMarketInterventionRequestSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            game_account = GameAccount.objects.select_related("account", "account__node").get(pk=data["game_account_id"])
        except GameAccount.DoesNotExist:
            return Response({"error": "GameAccount not found."}, status=status.HTTP_404_NOT_FOUND)

        source_job = None
        if data.get("source_job_id"):
            source_job = Job.objects.filter(pk=data["source_job_id"]).first()

        intervention, created, sent = create_construction_market_intervention(
            game_account=game_account,
            source_job=source_job,
            payload=data,
        )
        return Response(
            {
                "ok": True,
                "request_id": str(intervention.pk),
                "status": intervention.status,
                "created": created,
                "sent": sent,
                "sell_job_id": str(intervention.sell_job_id) if intervention.sell_job_id else None,
            },
            status=status.HTTP_200_OK,
        )
