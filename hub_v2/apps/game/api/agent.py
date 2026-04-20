"""
Agent API views: account snapshots, blackbox token proxy, captcha proxy.

All endpoints require X-Agent-Token authentication.
"""

import logging

import requests as http_requests
from django.conf import settings
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers as drf_serializers

from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent
from apps.accounts.models import Account, GameAccount
from apps.game.models import AccountSnapshot, AccountSnapshotHistory

from .serializers import (
    CaptchaSolveSerializer,
    CurrentSnapshotResponseSerializer,
    SnapshotResponseSerializer,
    SnapshotUpdateSerializer,
)

logger = logging.getLogger(__name__)

# Timeout for proxied requests to the ikabotapi container
_IKABOTAPI_TIMEOUT = 90  # Blackbox token via Playwright can take 30-60s


class UpdateSnapshotView(APIView):
    """
    POST /api/agent/snapshots/

    Agent sends the latest game state for an account.
    Creates or updates the current ``AccountSnapshot`` and appends
    a record to ``AccountSnapshotHistory`` for analytics.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = SnapshotUpdateSerializer

    @extend_schema(
        request=SnapshotUpdateSerializer,
        responses={200: SnapshotResponseSerializer, 201: SnapshotResponseSerializer},
    )
    def post(self, request):
        serializer = SnapshotUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        game_account = None
        account = None

        # Resolve game_account and account from the payload
        game_account_id = data.get("game_account_id")
        account_id = data.get("account_id")

        if game_account_id:
            try:
                game_account = GameAccount.objects.select_related(
                    "account__node"
                ).get(pk=game_account_id)
                account = game_account.account
            except GameAccount.DoesNotExist:
                return Response(
                    {"error": "GameAccount not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        elif account_id:
            # Backward compat: old agents send only account_id
            try:
                account = Account.objects.select_related("node").get(
                    pk=account_id,
                )
            except Account.DoesNotExist:
                return Response(
                    {"error": "Account not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            return Response(
                {"error": "Either account_id or game_account_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        base_snapshot = data["base_snapshot"]
        cities = data["cities"]
        military = data.get("military", {})
        source_job_id = data.get("source_job_id")

        # Upsert current snapshot
        lookup = {"game_account": game_account} if game_account else {"account": account, "game_account__isnull": True}
        snapshot, created = AccountSnapshot.objects.update_or_create(
            **lookup,
            defaults={
                "account": account,
                "game_account": game_account,
                "base_snapshot": base_snapshot,
                "cities": cities,
                "military": military,
                "source_job_id": source_job_id,
            },
        )

        # Append to history for resource tracking
        from django.utils import timezone

        AccountSnapshotHistory.objects.create(
            account=account,
            game_account=game_account,
            base_snapshot=base_snapshot,
            cities=cities,
            military=military,
            captured_at=timezone.now(),
        )

        logger.info(
            "Snapshot %s for %s (created=%s)",
            "created" if created else "updated",
            game_account.pk if game_account else account.pk,
            created,
        )

        return Response(
            SnapshotResponseSerializer({
                "ok": True,
                "account_id": str(account.pk),
                "created": created,
            }).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PatchSnapshotBuildingView(APIView):
    """PATCH /api/agent/snapshots/patch-building/

    Surgically updates one building entry in the snapshot after a construction
    action, so the snapshot immediately reflects the ongoing build without
    waiting for the next full check_status run.

    Body: {game_account_id, city_id, position, patch: {building, level, is_upgrading, construction_end_at}}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def patch(self, request):
        from django.utils import timezone

        game_account_id = str(request.data.get("game_account_id") or "").strip()
        city_id = str(request.data.get("city_id") or "").strip()
        position = request.data.get("position")
        patch = request.data.get("patch") or {}

        if not game_account_id or not city_id or position is None:
            return Response(
                {"error": "game_account_id, city_id and position are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            position = int(position)
        except (TypeError, ValueError):
            return Response({"error": "position must be an integer"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            snapshot = AccountSnapshot.objects.get(game_account__id=game_account_id)
        except AccountSnapshot.DoesNotExist:
            return Response({"error": "snapshot not found"}, status=status.HTTP_404_NOT_FOUND)

        cities = snapshot.cities or []
        patched = False
        for city in cities:
            if str(city.get("id") or "") == city_id:
                for building in city.get("buildings") or []:
                    if int(building.get("position", -1)) == position:
                        building.update({k: v for k, v in patch.items() if v is not None})
                        patched = True
                        break
                break

        if not patched:
            return Response({"error": "city or building position not found"}, status=status.HTTP_404_NOT_FOUND)

        snapshot.cities = cities
        snapshot.updated_at = timezone.now()
        snapshot.save(update_fields=["cities", "updated_at"])

        logger.info("Snapshot building patched: ga=%s city=%s pos=%d", game_account_id, city_id, position)
        return Response({"ok": True, "patched": True})


class CurrentSnapshotView(APIView):
    """GET /api/agent/snapshots/current/?game_account_id=<uuid>"""

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = None

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="game_account_id",
                location=OpenApiParameter.QUERY,
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="account_id",
                location=OpenApiParameter.QUERY,
                required=False,
                type=str,
            ),
        ],
        responses={
            200: CurrentSnapshotResponseSerializer,
            404: OpenApiResponse(description="Snapshot not found."),
        },
    )
    def get(self, request):
        game_account_id = request.query_params.get("game_account_id")
        account_id = request.query_params.get("account_id")

        snapshot = None
        if game_account_id:
            snapshot = AccountSnapshot.objects.select_related("account", "game_account").filter(
                game_account_id=game_account_id
            ).first()
        elif account_id:
            snapshot = AccountSnapshot.objects.select_related("account", "game_account").filter(
                account_id=account_id,
                game_account__isnull=True,
            ).first()
        else:
            return Response(
                {"error": "game_account_id or account_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if snapshot is None:
            return Response({"error": "Snapshot not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            CurrentSnapshotResponseSerializer(
                {
                    "ok": True,
                    "account_id": str(snapshot.account_id),
                    "game_account_id": str(snapshot.game_account_id) if snapshot.game_account_id else None,
                    "updated_at": snapshot.updated_at,
                    "base_snapshot": snapshot.base_snapshot,
                    "cities": snapshot.cities,
                    "military": snapshot.military,
                }
            ).data
        )


class BlackboxTokenView(APIView):
    """
    GET /api/agent/blackbox/token/?user_agent=<string>

    Proxies the request to the internal ikabotapi container to obtain
    a blackbox token for browser fingerprint simulation.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = None

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="user_agent",
                location=OpenApiParameter.QUERY,
                required=True,
                type=str,
                description="User-Agent utilizado para solicitar o token blackbox.",
            )
        ],
        responses={
            200: inline_serializer(
                name="BlackboxTokenResponse",
                fields={"token": drf_serializers.CharField()},
            ),
            502: OpenApiResponse(description="Falha de comunicação com o ikabotapi."),
            504: OpenApiResponse(description="Timeout ao chamar o ikabotapi."),
        },
    )
    def get(self, request):
        user_agent = request.query_params.get("user_agent", "")
        if not user_agent:
            return Response(
                {"error": "user_agent query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            resp = http_requests.get(
                f"{settings.IKABOTAPI_URL}/v1/token",
                params={"user_agent": user_agent},
                timeout=_IKABOTAPI_TIMEOUT,
            )
            resp.raise_for_status()
            token_data = resp.json()
            # ikabotapi returns a raw string, normalize to {"token": "..."}
            if isinstance(token_data, str):
                return Response({"token": token_data})
            return Response(token_data)
        except http_requests.ConnectionError:
            logger.error("ikabotapi unreachable for blackbox token request")
            return Response(
                {"error": "ikabotapi service is unreachable."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except http_requests.Timeout:
            logger.error("ikabotapi timed out for blackbox token request")
            return Response(
                {"error": "ikabotapi request timed out."},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except http_requests.RequestException as exc:
            logger.error("ikabotapi error (blackbox): %s", exc)
            return Response(
                {"error": f"ikabotapi error: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )


class SolveCaptchaView(APIView):
    """
    POST /api/agent/captcha/solve/

    Proxies a captcha-solving request to the internal ikabotapi container.
    Supports ``pirate`` and ``lobby`` captcha types.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = CaptchaSolveSerializer

    @extend_schema(
        request=CaptchaSolveSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="SolveCaptchaResponse",
                    fields={
                        "result": drf_serializers.JSONField(required=False),
                        "token": drf_serializers.CharField(required=False),
                    },
                ),
                description="Resposta proxy do ikabotapi. O formato varia conforme o tipo de captcha.",
            ),
            502: OpenApiResponse(description="Falha de comunicação com o ikabotapi."),
            504: OpenApiResponse(description="Timeout ao chamar o ikabotapi."),
        },
    )
    def post(self, request):
        serializer = CaptchaSolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        captcha_type = data["type"]
        images = data["images"]
        endpoint = f"/v1/decaptcha/{captcha_type}"

        try:
            resp = http_requests.post(
                f"{settings.IKABOTAPI_URL}{endpoint}",
                json=images,
                timeout=_IKABOTAPI_TIMEOUT,
            )
            resp.raise_for_status()
            return Response(resp.json())
        except http_requests.ConnectionError:
            logger.error("ikabotapi unreachable for captcha solve request")
            return Response(
                {"error": "ikabotapi service is unreachable."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except http_requests.Timeout:
            logger.error("ikabotapi timed out for captcha solve request")
            return Response(
                {"error": "ikabotapi request timed out."},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except http_requests.RequestException as exc:
            logger.error("ikabotapi error (captcha): %s", exc)
            return Response(
                {"error": f"ikabotapi error: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
