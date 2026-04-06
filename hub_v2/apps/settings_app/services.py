"""
Settings services — connectivity tests for external integrations.
"""

import logging

import requests

logger = logging.getLogger(__name__)


def test_webshare(api_key: str) -> dict:
    """Test Webshare API connectivity. Returns {success, proxy_count, error}."""
    if not api_key:
        return {"success": False, "proxy_count": 0, "error": "Chave API não configurada."}

    try:
        resp = requests.get(
            "https://proxy.webshare.io/api/v2/proxy/list/",
            params={"mode": "direct", "page": 1, "page_size": 1},
            headers={"Authorization": f"Token {api_key}"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            count = data.get("count", 0)
            return {"success": True, "proxy_count": count, "error": ""}
        elif resp.status_code == 401:
            return {"success": False, "proxy_count": 0, "error": "Chave API inválida (401)."}
        else:
            return {"success": False, "proxy_count": 0, "error": f"HTTP {resp.status_code}"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "proxy_count": 0, "error": "Erro de conexão com Webshare."}
    except Exception as exc:
        return {"success": False, "proxy_count": 0, "error": str(exc)}


def test_ikabotapi(url: str) -> dict:
    """Test ikabotapi (captcha solver) connectivity. Returns {success, detail, error}."""
    if not url:
        return {"success": False, "detail": "", "error": "URL não configurada."}

    try:
        resp = requests.get(f"{url.rstrip('/')}/health", timeout=10)
        if resp.status_code == 200:
            return {"success": True, "detail": "Serviço ativo", "error": ""}
        else:
            return {"success": False, "detail": "", "error": f"HTTP {resp.status_code}"}
    except requests.exceptions.ConnectionError:
        # Try root path as fallback
        try:
            resp = requests.get(url.rstrip("/"), timeout=10)
            if resp.status_code < 500:
                return {"success": True, "detail": f"Respondeu (HTTP {resp.status_code})", "error": ""}
            return {"success": False, "detail": "", "error": f"HTTP {resp.status_code}"}
        except Exception:
            return {"success": False, "detail": "", "error": "Serviço inacessível."}
    except Exception as exc:
        return {"success": False, "detail": "", "error": str(exc)}
