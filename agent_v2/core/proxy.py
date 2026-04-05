"""Strict proxy session — NEVER fallback to local IP for game requests."""

import logging

import requests

logger = logging.getLogger(__name__)

GAME_DOMAINS = [
    "gameforge.com",
    "ikariam.com",
    "lobby.ikariam",
]


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


