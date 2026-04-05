"""
DRF authentication backend for agent workers.
Agents authenticate via X-Agent-Token header.
"""

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

        if token == settings.AGENT_TOKEN:
            auth_kind = "agent"
        else:
            node_id = self._get_node_id(request)
            if not node_id:
                raise AuthenticationFailed("Invalid agent token.")
            if not self._node_token_matches(node_id, token):
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
    def _node_token_matches(node_id: str, token: str) -> bool:
        from apps.accounts.models import Node

        try:
            node = Node.objects.only("deploy_token").get(pk=node_id)
        except Node.DoesNotExist:
            return False
        return bool(node.deploy_token) and token == node.deploy_token

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
