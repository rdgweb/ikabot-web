"""
Webshare.io API client for proxy management.
https://proxy.webshare.io/docs/
"""

import requests
from django.conf import settings


class WebshareClient:
    BASE_URL = "https://proxy.webshare.io/api/v2"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.WEBSHARE_API_KEY
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Token {self.api_key}"

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self.session.get(f"{self.BASE_URL}{path}", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, data: dict | None = None) -> dict:
        resp = self.session.post(f"{self.BASE_URL}{path}", json=data, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def list_proxies(self, page: int = 1, page_size: int = 25) -> dict:
        """List all proxies from the account."""
        return self._get("/proxy/list/", {"mode": "direct", "page": page, "page_size": page_size})

    def get_proxy(self, proxy_id: int) -> dict:
        """Get details of a specific proxy."""
        return self._get(f"/proxy/list/{proxy_id}/")

    def get_proxy_config(self) -> dict:
        """Get proxy configuration (username, password)."""
        return self._get("/proxy/config/")

    def format_proxy_url(self, proxy: dict, config: dict) -> str:
        """
        Format a proxy as URL: http://user:pass@host:port
        Used to configure agent proxy settings.
        """
        username = config.get("username", "")
        password = config.get("password", "")
        host = proxy.get("proxy_address", "")
        port = proxy.get("port", "")
        return f"http://{username}:{password}@{host}:{port}"

    def is_configured(self) -> bool:
        """Check if API key is set."""
        return bool(self.api_key)
