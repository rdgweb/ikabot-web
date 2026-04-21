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

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GameAccount
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from .models import InternalMarketOrder
from .services import create_buy_job, create_internal_order

logger = logging.getLogger(__name__)


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

        if order.status == "completed":
            return Response({"ok": True, "order_id": str(order.pk), "status": "completed"})

        order.status = "completed"
        order.save(update_fields=["status", "updated_at"])
        logger.info("InternalMarketOrder %s marked as completed", order.pk)

        return Response({"ok": True, "order_id": str(order.pk), "status": "completed"})


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

        order = create_internal_order(
            buyer_ga=buyer_ga,
            resource_idx=int(resource_idx),
            amount=int(amount),
            unit_price=unit_price,
            preferred_buyer_city_id=int(preferred_buyer_city_id) if preferred_buyer_city_id not in (None, "") else None,
            source_action_code=int(source_action_code) if source_action_code not in (None, "") else None,
            source_reason=source_reason,
            reason_detail=reason_detail,
            production_eta_seconds=int(production_eta_seconds) if production_eta_seconds not in (None, "") else None,
            missing_resource_keys=missing_resource_keys,
        )

        if order is None:
            return Response(
                {"ok": False, "error": "No eligible seller found."},
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "ok": True,
                "order_id": str(order.pk),
                "sell_job_id": str(order.sell_job_id) if order.sell_job_id else None,
                "status": order.status,
            },
            status=status.HTTP_201_CREATED,
        )
