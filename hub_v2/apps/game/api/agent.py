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
from apps.game.services.dashboard_cache import bump_dashboard_cache_version

from .serializers import (
    CaptchaSolveSerializer,
    CurrentSnapshotResponseSerializer,
    SnapshotResponseSerializer,
    SnapshotUpdateSerializer,
)

logger = logging.getLogger(__name__)

# Timeout for proxied requests to the ikabotapi container
_IKABOTAPI_TIMEOUT = 90  # Blackbox token via Playwright can take 30-60s

# Helper: resolve Telegram chat_id for a game_account (or global fallback)


def _resolve_telegram_chat_id(game_account=None) -> str | None:
    """Return the Telegram chat_id for the given game_account, or the global chat."""
    from apps.telegram.models import TelegramAccountConfig, TelegramBotConfig

    if game_account:
        try:
            ga_cfg = TelegramAccountConfig.objects.get(game_account=game_account)
            if ga_cfg.enabled and ga_cfg.chat_id:
                return ga_cfg.chat_id
        except TelegramAccountConfig.DoesNotExist:
            pass

    try:
        bot = TelegramBotConfig.objects.get(pk=1)
        if bot.is_active and bot.chat_id:
            return bot.chat_id
    except TelegramBotConfig.DoesNotExist:
        pass

    return None


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

        base_snapshot = dict(data["base_snapshot"] or {})
        cities = data["cities"]
        military = data.get("military", {})
        source_job_id = data.get("source_job_id")

        from django.utils import timezone as _tz
        lookup = {"game_account": game_account} if game_account else {"account": account, "game_account__isnull": True}
        existing_snapshot = AccountSnapshot.objects.filter(**lookup).first()
        existing_base_snapshot = dict(getattr(existing_snapshot, "base_snapshot", {}) or {})

        # Preserve incremental fields populated by specialized runners when a full
        # check_status payload does not include them.
        if "unit_improvements" not in base_snapshot and existing_base_snapshot.get("unit_improvements"):
            base_snapshot["unit_improvements"] = existing_base_snapshot["unit_improvements"]

        # Upsert current snapshot
        snapshot, created = AccountSnapshot.objects.update_or_create(
            **lookup,
            defaults={
                "account": account,
                "game_account": game_account,
                "base_snapshot": base_snapshot,
                "cities": cities,
                "military": military,
                "source_job_id": source_job_id,
                # Full check_status snapshot — mark this as a complete refresh
                "full_snapshot_updated_at": _tz.now(),
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
        bump_dashboard_cache_version()

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
        bump_dashboard_cache_version()

        logger.info("Snapshot building patched: ga=%s city=%s pos=%d", game_account_id, city_id, position)
        return Response({"ok": True, "patched": True})


class PatchSnapshotResourcesView(APIView):
    """PATCH /api/agent/snapshots/patch-resources/

    Updates resource fields for one city without running a full check_status.
    Body: {
      game_account_id, city_id,
      resources: {wood, wine, marble, crystal, sulfur},
      incoming_delta: {wood, wine, marble, crystal, sulfur}
    }
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    RESOURCE_KEYS = ("wood", "wine", "marble", "crystal", "sulfur")
    CURRENT_RESOURCE_KEYS = {
        "wood": "resource",
        "wine": "1",
        "marble": "2",
        "crystal": "3",
        "sulfur": "4",
    }

    def patch(self, request):
        from django.db import transaction
        from django.utils import timezone

        game_account_id = str(request.data.get("game_account_id") or "").strip()
        city_id = str(request.data.get("city_id") or "").strip()
        resources = request.data.get("resources") or {}
        incoming_delta = request.data.get("incoming_delta") or {}

        if not game_account_id or not city_id:
            return Response(
                {"error": "game_account_id and city_id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(resources, dict):
            return Response({"error": "resources must be an object"}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(incoming_delta, dict):
            return Response({"error": "incoming_delta must be an object"}, status=status.HTTP_400_BAD_REQUEST)

        patch: dict[str, int] = {}
        for key in self.RESOURCE_KEYS:
            if key not in resources:
                continue
            try:
                patch[key] = max(0, int(float(resources.get(key) or 0)))
            except (TypeError, ValueError):
                return Response({"error": f"{key} must be numeric"}, status=status.HTTP_400_BAD_REQUEST)

        incoming_patch: dict[str, int] = {}
        for key in self.RESOURCE_KEYS:
            if key not in incoming_delta:
                continue
            try:
                incoming_patch[key] = int(float(incoming_delta.get(key) or 0))
            except (TypeError, ValueError):
                return Response({"error": f"incoming_delta.{key} must be numeric"}, status=status.HTTP_400_BAD_REQUEST)

        if not patch and not incoming_patch:
            return Response({"error": "no resource fields to patch"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            try:
                snapshot = AccountSnapshot.objects.select_for_update().get(game_account__id=game_account_id)
            except AccountSnapshot.DoesNotExist:
                return Response({"error": "snapshot not found"}, status=status.HTTP_404_NOT_FOUND)

            cities = snapshot.cities or []
            patched = False
            for city in cities:
                if str(city.get("id") or "") != city_id:
                    continue
                city.update(patch)
                current_resources = city.get("current_resources")
                if not isinstance(current_resources, dict):
                    current_resources = {}
                    city["current_resources"] = current_resources
                for key, amount in patch.items():
                    current_resources[self.CURRENT_RESOURCE_KEYS[key]] = amount
                incoming_resources = city.get("incoming_resources")
                if not isinstance(incoming_resources, dict):
                    incoming_resources = {}
                    city["incoming_resources"] = incoming_resources
                for key, delta in incoming_patch.items():
                    incoming_resources[key] = max(0, int(incoming_resources.get(key) or 0) + int(delta))
                patched = True
                break

            if not patched:
                return Response({"error": "city not found"}, status=status.HTTP_404_NOT_FOUND)

            snapshot.cities = cities
            snapshot.updated_at = timezone.now()
            snapshot.save(update_fields=["cities", "updated_at"])
            bump_dashboard_cache_version()

        logger.info(
            "Snapshot resources patched: ga=%s city=%s resources=%s",
            game_account_id,
            city_id,
            {"resources": sorted(patch.keys()), "incoming_delta": incoming_patch},
        )
        return Response({"ok": True, "patched": True, "resources": patch, "incoming_delta": incoming_patch})


class PatchSnapshotBaseView(APIView):
    """PATCH /api/agent/snapshots/patch-base/

    Updates selected base_snapshot fields without running a full check_status.
    Body: {game_account_id, patch: {gold, income, ...}}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def patch(self, request):
        from django.db import transaction
        from django.utils import timezone

        game_account_id = str(request.data.get("game_account_id") or "").strip()
        patch = request.data.get("patch") or {}

        if not game_account_id:
            return Response({"error": "game_account_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(patch, dict) or not patch:
            return Response({"error": "patch must be a non-empty object"}, status=status.HTTP_400_BAD_REQUEST)

        normalized_patch: dict[str, object] = {}
        for key, value in patch.items():
            if key in {"gold", "income", "upkeep", "scientists_upkeep"}:
                try:
                    normalized_patch[key] = int(float(value or 0))
                except (TypeError, ValueError):
                    return Response({"error": f"{key} must be numeric"}, status=status.HTTP_400_BAD_REQUEST)
            else:
                normalized_patch[key] = value

        with transaction.atomic():
            try:
                snapshot = AccountSnapshot.objects.select_for_update().get(game_account__id=game_account_id)
            except AccountSnapshot.DoesNotExist:
                return Response({"error": "snapshot not found"}, status=status.HTTP_404_NOT_FOUND)

            base_snapshot = dict(snapshot.base_snapshot or {})
            base_snapshot.update(normalized_patch)
            snapshot.base_snapshot = base_snapshot
            snapshot.updated_at = timezone.now()
            snapshot.save(update_fields=["base_snapshot", "updated_at"])
            bump_dashboard_cache_version()

        logger.info("Snapshot base patched: ga=%s keys=%s", game_account_id, sorted(normalized_patch.keys()))
        return Response({"ok": True, "patched": True, "base_snapshot": normalized_patch})


class PatchSnapshotShipsView(APIView):
    """PATCH /api/agent/snapshots/patch-ships/

    Aplica deltas atomicamente nos contadores free_transporters/free_freighters
    do base_snapshot. Permite refletir dispatches/retornos sem esperar ac=100.

    Body: {game_account_id, delta_transporters: int, delta_freighters: int}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def patch(self, request):
        from django.db import transaction
        from django.utils import timezone

        ga_id = str(request.data.get("game_account_id") or "").strip()
        try:
            delta_t = int(request.data.get("delta_transporters") or 0)
            delta_f = int(request.data.get("delta_freighters") or 0)
        except (TypeError, ValueError):
            return Response({"error": "deltas must be int"}, status=status.HTTP_400_BAD_REQUEST)
        if not ga_id:
            return Response({"error": "game_account_id required"}, status=status.HTTP_400_BAD_REQUEST)
        if delta_t == 0 and delta_f == 0:
            return Response({"ok": True, "no_change": True})

        with transaction.atomic():
            try:
                snapshot = AccountSnapshot.objects.select_for_update().get(game_account__id=ga_id)
            except AccountSnapshot.DoesNotExist:
                return Response({"error": "snapshot not found"}, status=status.HTTP_404_NOT_FOUND)

            base = dict(snapshot.base_snapshot or {})
            free_t = max(0, int(base.get("free_transporters") or 0) + delta_t)
            free_f = max(0, int(base.get("free_freighters") or 0) + delta_f)
            max_t = int(base.get("max_transporters") or 0)
            max_f = int(base.get("max_freighters") or 0)
            # Clamp to max
            if max_t > 0:
                free_t = min(free_t, max_t)
            if max_f > 0:
                free_f = min(free_f, max_f)
            base["free_transporters"] = free_t
            base["free_freighters"] = free_f
            snapshot.base_snapshot = base
            snapshot.updated_at = timezone.now()
            snapshot.save(update_fields=["base_snapshot", "updated_at"])
            bump_dashboard_cache_version()

        logger.info("Snapshot ships patched: ga=%s dt=%+d df=%+d → t=%d f=%d",
                    ga_id, delta_t, delta_f, free_t, free_f)
        return Response({"ok": True, "free_transporters": free_t, "free_freighters": free_f})


class PatchSnapshotGoldView(APIView):
    """PATCH /api/agent/snapshots/patch-gold/

    Aplica delta atomico no ouro do base_snapshot (compra interna debita o
    comprador, venda credita o vendedor) sem esperar um check_status completo.

    Idempotente por op_key: a mesma operacao (ex. mesma leg de compra em retry)
    nao aplica o delta duas vezes.

    Body: {game_account_id, delta_gold: int, op_key: str}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    _MAX_OP_KEYS = 30

    def patch(self, request):
        from django.db import transaction
        from django.utils import timezone

        ga_id = str(request.data.get("game_account_id") or "").strip()
        op_key = str(request.data.get("op_key") or "").strip()
        try:
            delta_gold = int(request.data.get("delta_gold") or 0)
        except (TypeError, ValueError):
            return Response({"error": "delta_gold must be int"}, status=status.HTTP_400_BAD_REQUEST)
        if not ga_id:
            return Response({"error": "game_account_id required"}, status=status.HTTP_400_BAD_REQUEST)
        if delta_gold == 0:
            return Response({"ok": True, "no_change": True})

        with transaction.atomic():
            try:
                snapshot = AccountSnapshot.objects.select_for_update().get(game_account__id=ga_id)
            except AccountSnapshot.DoesNotExist:
                return Response({"error": "snapshot not found"}, status=status.HTTP_404_NOT_FOUND)

            base = dict(snapshot.base_snapshot or {})
            op_keys = list(base.get("gold_op_keys") or [])
            if op_key and op_key in op_keys:
                return Response({"ok": True, "duplicate": True, "gold": int(base.get("gold") or 0)})

            gold = max(0, int(base.get("gold") or 0) + delta_gold)
            base["gold"] = gold
            if op_key:
                op_keys.append(op_key)
                base["gold_op_keys"] = op_keys[-self._MAX_OP_KEYS:]
            snapshot.base_snapshot = base
            snapshot.updated_at = timezone.now()
            snapshot.save(update_fields=["base_snapshot", "updated_at"])
            bump_dashboard_cache_version()

        logger.info("Snapshot gold patched: ga=%s delta=%+d → gold=%d", ga_id, delta_gold, gold)
        return Response({"ok": True, "gold": gold})


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
                    "full_snapshot_updated_at": snapshot.full_snapshot_updated_at,
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

        import base64 as _b64
        captcha_type = data["type"]
        images = data["images"]
        endpoint = f"/v1/decaptcha/{captcha_type}"

        # ikabotapi expects multipart/form-data with file(s), not JSON
        # For pirate: field "image" with the PNG file
        # For lobby: fields "text_image" and "icons_image"
        files = {}
        if captcha_type == "pirate":
            img_b64 = images.get("image", "")
            if not img_b64:
                return Response({"error": "image base64 required for pirate captcha"}, status=400)
            files = {"image": ("captcha.png", _b64.b64decode(img_b64), "image/png")}
        elif captcha_type == "lobby":
            for field in ("text_image", "icons_image"):
                b64 = images.get(field, "")
                if b64:
                    files[field] = (f"{field}.png", _b64.b64decode(b64), "image/png")
        else:
            # Fallback: send as JSON for unknown types
            pass

        try:
            if files:
                resp = http_requests.post(
                    f"{settings.IKABOTAPI_URL}{endpoint}",
                    files=files,
                    timeout=_IKABOTAPI_TIMEOUT,
                )
            else:
                resp = http_requests.post(
                    f"{settings.IKABOTAPI_URL}{endpoint}",
                    json=images,
                    timeout=_IKABOTAPI_TIMEOUT,
                )
            resp.raise_for_status()
            result = resp.json()
            # Normalise: always return {"solution": "..."} regardless of ikabotapi format
            if isinstance(result, str):
                return Response({"solution": result})
            if isinstance(result, dict) and "solution" not in result:
                # ikabotapi might return the string directly or {"result": ...}
                sol = result.get("result") or result.get("text") or ""
                return Response({"solution": sol, **result})
            return Response(result)
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


class CaptchaChallengeCreateView(APIView):
    """
    POST /api/agent/captcha/challenge/

    Tenta resolver o captcha automaticamente via ikabotapi.
    Se falhar, cria um CaptchaChallenge pendente e envia a imagem via Telegram
    (se configurado). Retorna a solução imediata ou o ID do desafio para polling.

    Body: {"type": "pirate", "image_b64": "...", "game_account_id": "optional-uuid"}
    Response success: {"solution": "XXXXX", "challenge_id": null}
    Response pending: {"solution": null, "challenge_id": 42}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        import base64 as _b64
        from datetime import timedelta
        from django.utils import timezone
        from apps.captcha.models import CaptchaChallenge, CaptchaConfig
        from apps.telegram.services.bot_api import send_photo

        captcha_type = str(request.data.get("type") or "pirate").strip()
        display_type = str(request.data.get("display_type") or "").strip()
        challenge_type = display_type or ("piracy" if captcha_type == "pirate" else captcha_type)
        image_b64 = str(request.data.get("image_b64") or "").strip()
        game_account_id = str(request.data.get("game_account_id") or "").strip()
        extra_data = request.data.get("extra_data") or {}

        if not image_b64:
            return Response({"error": "image_b64 obrigatorio."}, status=status.HTTP_400_BAD_REQUEST)

        # Decode image for ikabotapi
        try:
            img_bytes = _b64.b64decode(image_b64)
        except Exception:
            return Response({"error": "image_b64 invalido."}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Attempt auto-solve via ikabotapi
        solution = ""
        auto_failed = False
        try:
            endpoint = f"/v1/decaptcha/{captcha_type}"
            files = {"image": ("captcha.png", img_bytes, "image/png")}
            resp = http_requests.post(
                f"{settings.IKABOTAPI_URL}{endpoint}",
                files=files,
                timeout=_IKABOTAPI_TIMEOUT,
            )
            resp.raise_for_status()
            result = resp.json()
            if isinstance(result, str):
                solution = result.strip().upper()
            elif isinstance(result, dict):
                solution = str(result.get("solution") or result.get("result") or "").strip().upper()
        except Exception as exc:
            logger.warning("CaptchaChallenge: auto-solve falhou: %s", exc)
            auto_failed = True

        if solution:
            logger.info("CaptchaChallenge: resolvido automaticamente type=%s", captcha_type)
            game_account = None
            if game_account_id:
                try:
                    game_account = GameAccount.objects.get(pk=game_account_id)
                except GameAccount.DoesNotExist:
                    logger.warning("CaptchaChallenge: GameAccount %s nao encontrado", game_account_id)
            CaptchaChallenge.objects.create(
                captcha_type=challenge_type,
                image_data=image_b64,
                status="solved",
                solution=solution,
                solve_method="auto",
                game_account=game_account,
                solved_at=timezone.now(),
                extra_data=extra_data if isinstance(extra_data, dict) else {},
            )
            return Response({"solution": solution, "challenge_id": None})

        # 2. Auto-solve failed — create a pending challenge
        captcha_cfg, _ = CaptchaConfig.objects.get_or_create(pk=1)

        game_account = None
        if game_account_id:
            try:
                game_account = GameAccount.objects.get(pk=game_account_id)
            except GameAccount.DoesNotExist:
                logger.warning("CaptchaChallenge: GameAccount %s nao encontrado", game_account_id)

        expires_at = timezone.now() + timedelta(seconds=captcha_cfg.timeout_sec)
        challenge = CaptchaChallenge.objects.create(
            captcha_type=challenge_type,
            image_data=image_b64,
            status="pending",
            game_account=game_account,
            expires_at=expires_at,
            extra_data=extra_data if isinstance(extra_data, dict) else {},
        )

        logger.info(
            "CaptchaChallenge: criado pk=%d type=%s ga=%s",
            challenge.pk, captcha_type, game_account_id or "global",
        )

        # 3. Send via Telegram if configured
        use_telegram = captcha_cfg.solve_method == "telegram" or captcha_cfg.fallback_method == "telegram"
        if use_telegram:
            chat_id = _resolve_telegram_chat_id(game_account)
            if chat_id:
                ga_label = game_account.name or game_account_id if game_account else "global"
                caption = (
                    f"🏴‍☠️ <b>Captcha #{challenge.pk}</b> — {captcha_type}\n"
                    f"Conta: <b>{ga_label}</b>\n\n"
                    f"Clique no botão abaixo e digite o texto da imagem."
                )
                result = send_photo(
                    chat_id,
                    img_bytes,
                    caption=caption,
                    reply_markup={
                        "inline_keyboard": [[{
                            "text": "🔑 Digitar resposta",
                            "callback_data": f"captcha_reply:{challenge.pk}",
                        }]]
                    },
                )
                if not result.get("ok"):
                    logger.warning(
                        "CaptchaChallenge: falha ao enviar foto Telegram pk=%d: %s",
                        challenge.pk, result.get("description"),
                    )
            else:
                logger.warning("CaptchaChallenge: Telegram configurado mas sem chat_id disponivel")

        return Response({"solution": None, "challenge_id": challenge.pk})


class CaptchaChallengePollView(APIView):
    """
    GET /api/agent/captcha/challenge/<pk>/

    Consulta o status de um CaptchaChallenge. Expira automaticamente se necessario.

    Response: {"status": "pending"|"solved"|"failed"|"expired", "solution": ""}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request, pk):
        from apps.captcha.models import CaptchaChallenge

        try:
            challenge = CaptchaChallenge.objects.get(pk=pk)
        except CaptchaChallenge.DoesNotExist:
            return Response({"error": "Desafio nao encontrado."}, status=status.HTTP_404_NOT_FOUND)

        # Auto-expire if past deadline
        if challenge.status == "pending" and challenge.is_expired:
            challenge.mark_expired()

        return Response({
            "status": challenge.status,
            "solution": challenge.solution or "",
        })


class MilitaryMovementsUpdateView(APIView):
    """POST /api/agent/game/military-movements/

    Persists military advisor state for a game account (used by ac=13 runner).
    Stored on AccountSnapshot.movements JSONField.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request):
        from apps.accounts.models import GameAccount
        from apps.game.models import AccountSnapshot

        ga_id = str(request.query_params.get("game_account_id") or "").strip()
        if not ga_id:
            return Response({"error": "game_account_id required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            ga = GameAccount.objects.get(pk=ga_id)
        except GameAccount.DoesNotExist:
            return Response({"error": "GameAccount not found."}, status=status.HTTP_404_NOT_FOUND)

        snap = AccountSnapshot.objects.filter(game_account=ga).first()
        return Response({
            "ok": True,
            "game_account_id": ga_id,
            "movements": (snap.movements if snap else {}) or {},
        })

    def post(self, request):
        from apps.accounts.models import GameAccount
        from apps.game.models import AccountSnapshot

        ga_id = str(request.data.get("game_account_id") or "").strip()
        movements = request.data.get("movements") or {}
        probe_key = str(request.data.get("probe_key") or "").strip()
        if not ga_id:
            return Response({"error": "game_account_id required."}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(movements, dict):
            return Response({"error": "movements must be a dict."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            ga = GameAccount.objects.get(pk=ga_id)
        except GameAccount.DoesNotExist:
            return Response({"error": "GameAccount not found."}, status=status.HTTP_404_NOT_FOUND)

        snap, _ = AccountSnapshot.objects.get_or_create(
            game_account=ga,
            defaults={"account": ga.account},
        )
        current = dict(snap.movements or {})
        current.update(movements)
        if probe_key:
            probes = current.get("probes") or {}
            if not isinstance(probes, dict):
                probes = {}
            probes[probe_key] = movements
            current["probes"] = probes
        snap.movements = current
        snap.save(update_fields=["movements", "updated_at"])
        return Response({"ok": True})
