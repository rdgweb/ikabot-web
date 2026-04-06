"""
Agent API views: register, heartbeat, config.

All endpoints require X-Agent-Token authentication.
"""

import logging

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers as drf_serializers

from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent
from core.encryption import decrypt, encrypt
from apps.accounts.models import GameAccount, Node
from apps.settings_app.utils import get_int_setting
from apps.jobs.services.recovery import recover_stale_running_jobs

from .serializers import (
    AgentHeartbeatSerializer,
    AgentRegisterSerializer,
    AgentRegisterResponseSerializer,
    NodeConfigResponseSerializer,
)

logger = logging.getLogger(__name__)


class AgentRegisterView(APIView):
    """
    POST /api/agent/register/

    Agent registers itself with the hub, reporting its name, host, and version.
    The hub validates the deploy token (if provided) and updates the Node record.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = AgentRegisterSerializer

    @extend_schema(
        request=AgentRegisterSerializer,
        responses={200: AgentRegisterResponseSerializer},
    )
    def post(self, request):
        serializer = AgentRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        node, created = Node.objects.get_or_create(
            pk=data["node_id"],
            defaults={
                "name": data.get("agent_name", "") or str(data["node_id"])[:8],
                "active": True,
            },
        )
        if created:
            logger.info("Auto-created node %s on first registration", node.pk)

        # Validate deploy token if provided
        deploy_token = data.get("deploy_token")
        if deploy_token and deploy_token != node.deploy_token:
            return Response(
                {"error": "Invalid deploy token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        node.agent_name = data.get("agent_name", "")
        node.agent_host = data.get("agent_host", "")
        node.agent_version = data.get("agent_version", "")
        node.agent_image = data.get("agent_image", "")
        node.agent_last_seen_at = timezone.now()
        node.save(update_fields=[
            "agent_name", "agent_host", "agent_version",
            "agent_image", "agent_last_seen_at",
        ])

        logger.info(
            "Agent registered: node=%s name=%s version=%s",
            node.pk, node.agent_name, node.agent_version,
        )

        response_data = {
            "node_id": str(node.pk),
            "node_name": node.name,
            "proxy": node.proxy,
            "active": node.active,
        }
        return Response(
            AgentRegisterResponseSerializer(response_data).data,
        )


class AgentHeartbeatView(APIView):
    """
    POST /api/agent/heartbeat/

    Agent sends periodic heartbeat to keep the node marked as online.
    Updates Node.agent_last_seen_at timestamp.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = AgentHeartbeatSerializer

    @extend_schema(
        request=AgentHeartbeatSerializer,
        responses={
            200: inline_serializer(
                name="AgentHeartbeatResponse",
                fields={"ok": drf_serializers.BooleanField()},
            )
        },
    )
    def post(self, request):
        serializer = AgentHeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        node_id = serializer.validated_data["node_id"]
        external_ip = serializer.validated_data.get("external_ip", "")

        update_fields = {"agent_last_seen_at": timezone.now()}
        if external_ip:
            update_fields["external_ip"] = external_ip
            update_fields["ip_source"] = "agent"
            update_fields["ip_checked_at"] = timezone.now()

        updated = Node.objects.filter(pk=node_id).update(**update_fields)

        if not updated:
            return Response(
                {"error": "Node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            node = Node.objects.get(pk=node_id)
            recover_stale_running_jobs(node=node)
        except Exception as exc:
            logger.warning("Failed to recover stale jobs for node %s: %s", node_id, exc)

        return Response({"ok": True})


class AgentConfigView(APIView):
    """
    GET /api/agent/config/?node_id=<uuid>

    Returns node configuration and a list of active lobby accounts
    with their game accounts (sub-accounts/characters) and decrypted
    credentials for game login.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    serializer_class = NodeConfigResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="node_id",
                location=OpenApiParameter.QUERY,
                required=True,
                type=str,
                description="UUID do node a ser consultado.",
            )
        ],
        responses={200: NodeConfigResponseSerializer},
    )
    def get(self, request):
        node_id = request.query_params.get("node_id")
        if not node_id:
            return Response(
                {"error": "node_id query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            node = Node.objects.get(pk=node_id)
        except Node.DoesNotExist:
            return Response(
                {"error": "Node not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        accounts = []
        for acc in node.accounts.filter(active=True).prefetch_related("game_accounts"):
            # Decrypt sensitive fields — fall back gracefully on errors
            try:
                password = decrypt(acc.password_enc) if acc.password_enc else ""
            except Exception:
                logger.warning("Failed to decrypt password for account %s", acc.pk)
                password = ""

            try:
                gf_token = decrypt(acc.gf_token_enc) if acc.gf_token_enc else ""
            except Exception:
                logger.warning("Failed to decrypt gf_token for account %s", acc.pk)
                gf_token = ""

            # Build game accounts list
            game_accounts_data = []
            for ga in acc.game_accounts.filter(active=True):
                # Decrypt cached session cookies if available
                cached_session = ""
                if ga.session_cookies_enc:
                    try:
                        cached_session = decrypt(ga.session_cookies_enc)
                    except Exception:
                        logger.warning("Failed to decrypt session for game_account %s", ga.pk)

                game_accounts_data.append({
                    "id": str(ga.pk),
                    "lobby_account_id": ga.lobby_account_id,
                    "server_id": ga.server_id,
                    "server_language": ga.server_language,
                    "server_number": ga.server_number,
                    "server": ga.server,
                    "name": ga.name,
                    "account_group": ga.account_group,
                    "blocked": ga.blocked,
                    "active": ga.active,
                    "cached_session": cached_session,
                })

            accounts.append({
                "id": str(acc.pk),
                "label": acc.label,
                "email": acc.email,
                "password": password,
                "gf_token": gf_token,
                "game_accounts": game_accounts_data,
            })

        # Determine proxy verification status from ProxyProfile
        proxy_verified = False
        try:
            pp = node.proxy_profile  # reverse OneToOne from ProxyProfile.assigned_node
            if pp and pp.active and pp.last_test_status is True:
                proxy_verified = True
        except Exception:
            pass

        response_data = {
            "node_id": str(node.pk),
            "node_name": node.name,
            "proxy": node.proxy,
            "proxy_verified": proxy_verified,
            "active": node.active,
            "system_settings": {
                "snapshot_stale_seconds": get_int_setting("snapshot_stale_seconds", 2 * 60 * 60),
                "building_options_stale_seconds": get_int_setting("building_options_stale_seconds", 6 * 60 * 60),
                "running_job_lease_seconds": get_int_setting("running_job_lease_seconds", 180),
                "running_job_recovery_grace_seconds": get_int_setting("running_job_recovery_grace_seconds", 300),
            },
            "accounts": accounts,
        }
        return Response(
            NodeConfigResponseSerializer(response_data).data,
        )


class AgentSessionView(APIView):
    """
    POST /api/agent/sessions/

    Agent reports game session cookies after a successful login.
    Cookies are Fernet-encrypted and stored in GameAccount.session_cookies_enc.
    Optionally updates the lobby token (Account.gf_token_enc).
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        game_account_id = request.data.get("game_account_id")
        cookies = request.data.get("cookies")
        lobby_token = request.data.get("lobby_token", "")

        if not game_account_id or not cookies:
            return Response(
                {"error": "game_account_id and cookies are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            ga = GameAccount.objects.select_related("account").get(pk=game_account_id)
        except GameAccount.DoesNotExist:
            return Response(
                {"error": "GameAccount not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Encrypt and save session cookies
        import json
        cookies_json = json.dumps(cookies) if isinstance(cookies, dict) else str(cookies)
        ga.session_cookies_enc = encrypt(cookies_json)
        ga.session_updated_at = timezone.now()
        ga.save(update_fields=["session_cookies_enc", "session_updated_at"])

        logger.info("Session saved for game_account %s (%s)", ga.pk, ga.server_id)

        # Optionally update lobby token on the parent Account
        if lobby_token:
            acc = ga.account
            current_token = ""
            if acc.gf_token_enc:
                try:
                    current_token = decrypt(acc.gf_token_enc)
                except Exception:
                    logger.warning(
                        "Failed to decrypt existing lobby token for account %s",
                        acc.pk,
                    )

            if current_token != lobby_token:
                acc.__class__.objects.filter(pk=acc.pk).update(
                    gf_token_enc=encrypt(lobby_token),
                    updated_at=timezone.now(),
                )
                logger.info("Lobby token updated for account %s", acc.pk)
            else:
                logger.debug(
                    "Lobby token unchanged for account %s; skipping save",
                    acc.pk,
                )

        return Response({"ok": True})
