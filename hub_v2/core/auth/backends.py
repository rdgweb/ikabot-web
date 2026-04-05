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

    def authenticate(self, request):
        token = request.META.get(self.HEADER)
        if not token:
            return None

        if token != settings.AGENT_TOKEN:
            raise AuthenticationFailed("Invalid agent token.")

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

        return (AnonymousUser(), "agent")

    def authenticate_header(self, request):
        return "AgentToken"

    @staticmethod
    def _get_client_ip(request) -> str:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")

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
