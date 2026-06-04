"""
Proxy services — connectivity testing, IP verification, and Webshare sync.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.encryption import encrypt

logger = logging.getLogger(__name__)

IP_CHECK_SERVICES = [
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://checkip.amazonaws.com",
]

PROXY_TEST_TIMEOUT = 15
MAX_LOBBY_PROXIES_PER_ACCOUNT = 3


def test_proxy(proxy_profile) -> dict:
    """
    Test proxy connectivity by making a request to an IP check service.

    Returns dict with: success, ip, latency_ms, error.
    Updates ProxyProfile.last_test_* fields.
    If proxy is assigned to a node, also updates Node.external_ip.
    """
    proxy_url = proxy_profile.proxy_url
    proxies = {"http": proxy_url, "https": proxy_url}

    result = {"success": False, "ip": "", "latency_ms": 0, "error": ""}

    for service_url in IP_CHECK_SERVICES:
        try:
            start = time.monotonic()
            resp = requests.get(
                service_url,
                proxies=proxies,
                timeout=PROXY_TEST_TIMEOUT,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code == 200:
                ip = resp.text.strip()
                result = {
                    "success": True,
                    "ip": ip,
                    "latency_ms": elapsed_ms,
                    "error": "",
                }
                break
            else:
                result["error"] = f"HTTP {resp.status_code} de {service_url}"

        except requests.exceptions.ProxyError as exc:
            result["error"] = f"Erro de proxy: {exc}"
        except requests.exceptions.ConnectTimeout:
            result["error"] = "Timeout de conexão"
        except requests.exceptions.ConnectionError as exc:
            result["error"] = f"Erro de conexão: {exc}"
        except Exception as exc:
            result["error"] = str(exc)

    # Update ProxyProfile test fields
    proxy_profile.last_test_at = timezone.now()
    proxy_profile.last_test_status = result["success"]
    proxy_profile.last_test_ip = result["ip"]
    proxy_profile.last_test_latency_ms = result["latency_ms"] if result["success"] else None
    proxy_profile.save(update_fields=[
        "last_test_at", "last_test_status", "last_test_ip", "last_test_latency_ms",
    ])

    # Sync IP to assigned node
    if result["success"] and proxy_profile.assigned_node:
        node = proxy_profile.assigned_node
        node.external_ip = result["ip"]
        node.ip_source = "proxy_test"
        node.ip_checked_at = timezone.now()
        node.save(update_fields=["external_ip", "ip_source", "ip_checked_at"])

    logger.info(
        "Proxy test %s:%d → %s (ip=%s, %dms)",
        proxy_profile.address,
        proxy_profile.port,
        "OK" if result["success"] else "FAIL",
        result["ip"] or result["error"],
        result["latency_ms"],
    )

    return result


def reserve_lobby_proxies(*, account, limit: int = MAX_LOBBY_PROXIES_PER_ACCOUNT) -> list:
    """Return up to ``limit`` stable proxies reserved for one lobby account.

    Existing reservations are reused. When the lobby has fewer than ``limit``
    total reservations, new proxies are allocated from the free pool.
    A proxy reserved for one lobby is never handed to another lobby.
    """
    from .models import AccountProxyReservation, ProxyProfile

    limit = max(1, min(int(limit or MAX_LOBBY_PROXIES_PER_ACCOUNT), MAX_LOBBY_PROXIES_PER_ACCOUNT))

    with transaction.atomic():
        reservations = list(
            AccountProxyReservation.objects.select_for_update()
            .filter(account=account)
            .select_related("proxy_profile")
            .order_by("created_at", "proxy_profile__address")[:limit]
        )

        missing = limit - len(reservations)
        if missing > 0:
            free_proxies = list(
                ProxyProfile.objects.select_for_update()
                .filter(active=True, last_test_status=True, account_reservation__isnull=True)
                .order_by("last_test_latency_ms", "address")[:missing]
            )
            for proxy in free_proxies:
                reservations.append(
                    AccountProxyReservation.objects.create(account=account, proxy_profile=proxy)
                )

    reservations.sort(key=lambda item: (item.created_at, item.proxy_profile.address))
    usable = [
        reservation.proxy_profile
        for reservation in reservations[:limit]
        if reservation.proxy_profile.active and reservation.proxy_profile.last_test_status is True
    ]
    return usable


def sync_webshare(api_key: str | None = None) -> dict:
    """
    Sync proxies from Webshare.io API into ProxyProfile.

    Fetches all proxies + config (username/password), then upserts into DB.
    Returns: {success, created, updated, deactivated, total, error}
    """
    from core.webshare import WebshareClient
    from .models import ProxyProfile

    if not api_key:
        # Try AppSetting, then env var
        from apps.settings_app.views import _get_encrypted_setting
        api_key = _get_encrypted_setting("webshare_api_key", "")
        if not api_key:
            api_key = getattr(settings, "WEBSHARE_API_KEY", "")

    if not api_key:
        return {"success": False, "created": 0, "updated": 0, "deactivated": 0, "total": 0,
                "error": "Chave API Webshare não configurada."}

    client = WebshareClient(api_key=api_key)
    result = {"success": False, "created": 0, "updated": 0, "deactivated": 0, "total": 0, "error": ""}

    try:
        # Fetch proxy config (shared username/password)
        config = client.get_proxy_config()
        ws_username = config.get("username", "")
        ws_password = config.get("password", "")

        # Fetch all pages of proxies
        all_proxies = []
        page = 1
        while True:
            data = client.list_proxies(page=page, page_size=100)
            proxies = data.get("results", [])
            all_proxies.extend(proxies)
            if not data.get("next"):
                break
            page += 1

        result["total"] = len(all_proxies)
        seen_ws_ids = set()

        for ws_proxy in all_proxies:
            ws_id = str(ws_proxy.get("id", ""))
            if not ws_id:
                continue
            seen_ws_ids.add(ws_id)

            address = ws_proxy.get("proxy_address", "")
            port = ws_proxy.get("port", 0)
            country = ws_proxy.get("country_code", "")

            try:
                existing = ProxyProfile.objects.get(webshare_id=ws_id)
                changed = False
                rotated = False  # address or port changed → proxy rotated

                if existing.address != address:
                    logger.info(
                        "Proxy %s rotated: %s:%d → %s:%d",
                        ws_id, existing.address, existing.port, address, port,
                    )
                    existing.address = address
                    changed = True
                    rotated = True
                if existing.port != port:
                    existing.port = port
                    changed = True
                    rotated = True
                if existing.country_code != country:
                    existing.country_code = country
                    changed = True
                if existing.username != ws_username:
                    existing.username = ws_username
                    changed = True

                if changed:
                    existing.password_enc = encrypt(ws_password) if ws_password else ""

                    # Proxy rotated → invalidate test results
                    if rotated:
                        existing.last_test_status = None
                        existing.last_test_ip = ""
                        existing.last_test_latency_ms = None
                        result["rotated"] = result.get("rotated", 0) + 1

                    existing.save()
                    result["updated"] += 1

                    # Sync proxy URL to assigned node
                    if existing.assigned_node:
                        node = existing.assigned_node
                        node.proxy = existing.proxy_url
                        if rotated:
                            # Clear validated IP — it's no longer trustworthy
                            node.external_ip = ""
                            node.ip_source = ""
                            node.save(update_fields=[
                                "proxy", "external_ip", "ip_source",
                            ])
                        else:
                            node.save(update_fields=["proxy"])
            except ProxyProfile.DoesNotExist:
                ProxyProfile.objects.create(
                    webshare_id=ws_id,
                    provider=ProxyProfile.Provider.WEBSHARE,
                    address=address,
                    port=port,
                    username=ws_username,
                    password_enc=encrypt(ws_password) if ws_password else "",
                    country_code=country,
                    active=True,
                )
                result["created"] += 1

        # Deactivate Webshare proxies no longer in the API response
        stale_qs = ProxyProfile.objects.filter(
            provider=ProxyProfile.Provider.WEBSHARE,
            active=True,
        ).exclude(webshare_id__in=seen_ws_ids).select_related("assigned_node")

        deactivated_count = 0
        for stale_proxy in stale_qs:
            # Clear proxy from assigned node before deactivating
            if stale_proxy.assigned_node:
                node = stale_proxy.assigned_node
                node.proxy = ""
                node.external_ip = ""
                node.ip_source = ""
                node.save(update_fields=["proxy", "external_ip", "ip_source"])
                logger.warning(
                    "Proxy %s removed from Webshare — cleared node %s",
                    stale_proxy.webshare_id, node.name,
                )
            stale_proxy.active = False
            stale_proxy.last_test_status = None
            stale_proxy.save(update_fields=["active", "last_test_status"])
            deactivated_count += 1

        result["deactivated"] = deactivated_count

        result["success"] = True
        logger.info(
            "Webshare sync: %d total, %d created, %d updated, %d deactivated",
            result["total"], result["created"], result["updated"], result["deactivated"],
        )

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 401:
            result["error"] = "Chave API inválida (401)."
        else:
            result["error"] = f"Erro HTTP {status} da Webshare."
        logger.error("Webshare sync failed: %s", result["error"])
    except Exception as exc:
        result["error"] = str(exc)
        logger.error("Webshare sync failed: %s", exc)

    return result


def test_all_proxies(
    *,
    auto_inactivate: bool = True,
    auto_reassign: bool = True,
    max_workers: int = 10,
) -> dict:
    """
    Test all active proxies in parallel.

    - auto_inactivate: mark proxies that fail as inactive.
    - auto_reassign: if a failing proxy has a node assigned, find the best
      passing proxy without a node and reassign it.

    Returns: {tested, ok, failed, inactivated, reassigned, results[]}
    """
    from .models import ProxyProfile

    proxies = list(ProxyProfile.objects.select_related("assigned_node").filter(active=True))

    result = {
        "tested": 0,
        "ok": 0,
        "failed": 0,
        "inactivated": 0,
        "reassigned": 0,
        "results": [],
    }

    def _run_test(proxy):
        r = test_proxy(proxy)
        return proxy, r

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_test, p): p for p in proxies}
        for future in as_completed(futures):
            proxy, r = future.result()
            result["tested"] += 1
            entry = {
                "proxy_id": proxy.pk,
                "address": proxy.address,
                "port": proxy.port,
                "success": r["success"],
                "ip": r.get("ip", ""),
                "latency_ms": r.get("latency_ms", 0),
                "error": r.get("error", ""),
            }
            if r["success"]:
                result["ok"] += 1
            else:
                result["failed"] += 1
                if auto_inactivate:
                    ProxyProfile.objects.filter(pk=proxy.pk).update(active=False)
                    result["inactivated"] += 1
                    entry["inactivated"] = True
            result["results"].append(entry)

    if auto_reassign:
        # For each failing proxy that has an assigned node, find the best
        # available passing proxy (no node, lowest latency) and reassign.
        failing_with_node = ProxyProfile.objects.filter(
            assigned_node__isnull=False,
            last_test_status=False,
        ).select_related("assigned_node").order_by("assigned_node__name")

        for failing in failing_with_node:
            node = failing.assigned_node
            best = (
                ProxyProfile.objects.filter(
                    assigned_node__isnull=True,
                    last_test_status=True,
                    active=True,
                )
                .exclude(pk=failing.pk)
                .order_by("last_test_latency_ms")
                .first()
            )
            if not best:
                continue

            best.assigned_node = node
            best.save(update_fields=["assigned_node"])
            failing.assigned_node = None
            failing.save(update_fields=["assigned_node"])

            node.proxy = best.proxy_url
            node.save(update_fields=["proxy"])

            result["reassigned"] += 1
            logger.info(
                "Node %s reassigned from failing proxy %s to %s (%sms)",
                node.name, failing.address, best.address, best.last_test_latency_ms,
            )

    return result
