"""
DRF authentication backend for agent workers.
Agents authenticate via X-Agent-Token header.
"""

import hmac
from ipaddress import ip_address, ip_network

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class AgentTokenAuthentication(BaseAuthentication):
    """
    Authenticates agent workers using the X-Agent-Token header.
    Returns (AnonymousUser, "agent") on success.
    """

    HEADER = "HTTP_X_AGENT_TOKEN"
    NODE_HEADER = "HTTP_X_AGENT_NODE_ID"

    def authenticate(self, request):
        token = request.META.get(self.HEADER)
        if not token:
            return None

        node = None
        shared = settings.AGENT_TOKEN
        if shared and getattr(settings, "AGENT_SHARED_TOKEN_ENABLED", True) and hmac.compare_digest(token, shared):
            # N-106: the installation's single key, kept while the agents move to their own keys
            auth_kind = "agent"
            self._remember_shared_key_use(request)
        else:
            node_id = self._get_node_id(request)
            if not node_id:
                raise AuthenticationFailed("Invalid agent token.")
            node = self._node_for_token(node_id, token)
            if node is None:
                raise AuthenticationFailed("Invalid agent token.")
            auth_kind = "agent"

        # Optional IP allow-list
        allowed_raw = settings.AGENT_ALLOWED_IPS
        if allowed_raw:
            remote_ip = self._get_client_ip(request)
            allowed = [
                s.strip() for s in allowed_raw.split(",") if s.strip()
            ]
            if not self._ip_allowed(remote_ip, allowed):
                raise AuthenticationFailed(
                    f"Agent IP {remote_ip} not in allow-list."
                )

        if node is not None:
            # N-106: this request is this node's: the permission refuses any other node id, and the
            # data access is narrowed to the node's organization (the scope middleware resets it)
            from apps.orgs import scope

            request._request.agent_node_id = str(node.pk)
            scope.activate(node.organization, deny=node.organization is None)
        return (AnonymousUser(), auth_kind)

    def authenticate_header(self, request):
        return "AgentToken"

    @staticmethod
    def _get_client_ip(request) -> str:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")

    def _get_node_id(self, request) -> str:
        header_value = request.META.get(self.NODE_HEADER, "").strip()
        if header_value:
            return header_value
        query_value = request.query_params.get("node_id", "").strip()
        if query_value:
            return query_value
        data = getattr(request, "data", None)
        if isinstance(data, dict):
            body_value = str(data.get("node_id", "")).strip()
            if body_value:
                return body_value
        return ""

    @staticmethod
    def _node_for_token(node_id: str, token: str):
        from django.core.exceptions import ValidationError

        from apps.accounts.models import Node

        try:
            node = Node._base_manager.select_related("organization").get(pk=node_id)
        except (Node.DoesNotExist, ValidationError, ValueError):
            return None
        return node if node.agent_token_matches(token) else None

    @staticmethod
    def _remember_shared_key_use(request) -> None:
        """Which nodes still use the single key (Configurações → Saúde do sistema shows them)."""
        node_id = request.META.get("HTTP_X_AGENT_NODE_ID", "").strip()[:64]
        if not node_id:
            return
        try:
            from django.core.cache import cache

            cache.set(f"agent:shared-key:{node_id}", 1, timeout=15 * 60)
        except Exception:  # noqa: BLE001 - only a hint for the health page
            pass

    @staticmethod
    def _ip_allowed(ip: str, allowed: list[str]) -> bool:
        try:
            addr = ip_address(ip)
        except ValueError:
            return False
        for entry in allowed:
            try:
                if "/" in entry:
                    if addr in ip_network(entry, strict=False):
                        return True
                else:
                    if addr == ip_address(entry):
                        return True
            except ValueError:
                continue
        return False
