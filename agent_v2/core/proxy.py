"""Strict proxy session — NEVER fallback to local IP for game requests."""

import logging

import requests

logger = logging.getLogger(__name__)

GAME_DOMAINS = [
    "gameforge.com",
    "ikariam.com",
    "lobby.ikariam",
]

# Default timeout defensivo — sem isso requests fica bloqueado indefinidamente
# se o servidor não responde. Jobs zumbi (48h+ "running" sem heartbeat) foram
# rastreados até chamadas HTTP sem timeout explícito.
DEFAULT_TIMEOUT_SECONDS = 60


class StrictProxySession(requests.Session):
    """
    If a proxy is configured, ALL game requests go through it.
    If the proxy fails → error (no fallback to local IP).
    Non-game requests (hub API, internal) bypass proxy.
    """

    def __init__(self, proxy_url: str = ""):
        super().__init__()
        self._proxy_url = proxy_url

    def send(self, request, **kwargs):
        # Aplica timeout default se caller não passou — evita hang infinito.
        # Session.request sempre propaga timeout (mesmo None), então setdefault
        # não pega — precisa checar None explicitamente.
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = DEFAULT_TIMEOUT_SECONDS
        if self._is_game_request(request.url):
            if self._proxy_url:
                kwargs["proxies"] = {
                    "http": self._proxy_url,
                    "https": self._proxy_url,
                }
                logger.debug(f"Using proxy for: {request.url}")
            else:
                logger.debug(f"No proxy configured, using local IP for: {request.url}")
        return super().send(request, **kwargs)

    @staticmethod
    def _is_game_request(url: str) -> bool:
        return any(domain in url for domain in GAME_DOMAINS)


