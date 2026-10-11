"""Proxy maintenance (N-96): the hub keeps its proxy list true by itself.

The provider replaces proxies on its own schedule. A proxy that left the provider's
list answers "407 Proxy Authentication Required" forever, and until now the hub only
found out when someone pressed "Sincronizar": accounts whose reserved proxies were
all replaced could not log in, each failed attempt added an hour of waiting, and an
agent whose node proxy was replaced stayed paused.

`run_proxy_maintenance()` does, in order:

1. syncs the list with the provider (new proxies in, replaced ones marked inactive);
2. tests every active proxy now;
3. gives a node whose proxy is gone or dead the fastest free proxy that passed;
4. replaces an account's reservation when its proxy left the provider's list (a
   proxy that is still listed but failing keeps its reservation, as before: the
   account's addresses stay stable; the exception is an account left with no usable
   proxy at all, which gets its failing ones replaced too);
5. clears the login block of accounts that were blocked by a proxy failure and have
   a working proxy again.

It is started in the background by the things that reveal a problem (an agent
reporting a proxy-side login failure, a paused agent's heartbeat) and by any
heartbeat when the last run is old. One run at a time; never more often than
`min_interval`.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import requests
from django.core.cache import cache
from django.db import connections, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

LAST_RUN_SETTING = "proxy_maintenance_last_run"
LAST_RESULT_SETTING = "proxy_maintenance_last_result"
LOCK_KEY = "proxy_maintenance_running"
LOCK_SECONDS = 300

PERIODIC_INTERVAL = timedelta(hours=6)       # a heartbeat starts a run when the last one is older
FAILURE_INTERVAL = timedelta(minutes=10)     # a reported failure starts one at most this often
NODE_SEEN_WINDOW = timedelta(hours=24)       # nodes without a live agent are left alone
PROBE_TIMEOUT = 8
PROBE_WORKERS = 10
MAX_PROXIES_PER_ACCOUNT = 3

# a login failure that is the proxy's doing, not the game's
_PROXY_SIDE = re.compile(
    r"407|proxy authentication required|tunnel connection failed|unable to connect to proxy|proxyerror|cannot connect to proxy",
    re.IGNORECASE,
)


def is_proxy_side_failure(reason: str) -> bool:
    return bool(_PROXY_SIDE.search(str(reason or "")))


def probe_proxy(proxy_url: str, timeout: int = PROBE_TIMEOUT) -> dict:
    """One request through the proxy. {"success", "ip", "latency_ms", "error"}; touches no database."""
    from .services import IP_CHECK_SERVICES

    result = {"success": False, "ip": "", "latency_ms": 0, "error": ""}
    for service_url in IP_CHECK_SERVICES:
        started = time.monotonic()
        try:
            response = requests.get(service_url, proxies={"http": proxy_url, "https": proxy_url}, timeout=timeout)
        except requests.exceptions.ProxyError as exc:
            # the proxy itself said no: another IP-check service will hear the same
            code = re.search(r"Tunnel connection failed: (\d+)", str(exc))
            result["error"] = f"proxy recusou ({code.group(1)})" if code else "proxy inacessivel"
            return result
        except requests.exceptions.RequestException as exc:
            result["error"] = type(exc).__name__
            continue
        if response.status_code == 200:
            return {"success": True, "ip": response.text.strip()[:64], "latency_ms": int((time.monotonic() - started) * 1000), "error": ""}
        result["error"] = f"HTTP {response.status_code}"
    return result


def _probe_all(proxies) -> dict:
    """{proxy.pk: probe result} for the given profiles, in parallel; results are stored by the caller."""
    targets = [(proxy.pk, proxy.proxy_url) for proxy in proxies]
    if not targets:
        return {}
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        results = list(pool.map(lambda item: (item[0], probe_proxy(item[1])), targets))
    return dict(results)


def _store_probe(proxy, result: dict, now) -> None:
    proxy.last_test_at = now
    proxy.last_test_status = bool(result["success"])
    proxy.last_test_ip = result["ip"]
    proxy.last_test_latency_ms = result["latency_ms"] if result["success"] else None
    proxy.save(update_fields=["last_test_at", "last_test_status", "last_test_ip", "last_test_latency_ms"])


def _free_pool():
    """Proxies anyone may take: listed, passed the last test, not an account's and not a node's; fastest first."""
    from .models import ProxyProfile

    return ProxyProfile.objects.filter(
        active=True, last_test_status=True, account_reservation__isnull=True, assigned_node__isnull=True,
    ).order_by("last_test_latency_ms", "address")


def _heal_nodes(report: dict, now) -> None:
    from apps.accounts.models import Node

    from .models import ProxyProfile

    for node in Node.objects.filter(active=True, agent_last_seen_at__gte=now - NODE_SEEN_WINDOW):
        current = ProxyProfile.objects.filter(assigned_node=node).first()
        if current is not None and current.active and current.last_test_status is True:
            continue
        if current is None and not node.proxy:
            continue                            # a node that never had a proxy is not ours to give one
        best = _free_pool().first()
        if best is None:
            report["without_proxy"].append(f"no {node.name}")
            continue
        with transaction.atomic():
            if current is not None:
                current.assigned_node = None
                current.save(update_fields=["assigned_node"])
            best.assigned_node = node
            best.save(update_fields=["assigned_node"])
            node.proxy = best.proxy_url
            node.external_ip = best.last_test_ip
            node.ip_source = "proxy_test"
            node.ip_checked_at = now
            node.save(update_fields=["proxy", "external_ip", "ip_source", "ip_checked_at"])
        report["nodes_replaced"].append({"node": node.name, "from": current.pk if current else None, "to": best.pk})
        logger.warning("Proxy maintenance: node %s moved from proxy profile %s to %s", node.name, current.pk if current else "-", best.pk)


def _heal_accounts(report: dict) -> list:
    """Replaces what must be replaced; returns the accounts that have a usable proxy now."""
    from apps.accounts.models import Account

    from .models import AccountProxyReservation

    healthy_accounts = []
    for account in Account.objects.filter(active=True):
        reservations = list(
            AccountProxyReservation.objects.filter(account=account).select_related("proxy_profile").order_by("created_at", "proxy_profile__address")
        )
        if not reservations:
            continue                            # an account that never asked for proxies gets them when it does
        usable = [r for r in reservations if r.proxy_profile.active and r.proxy_profile.last_test_status is True]
        gone = [r for r in reservations if not r.proxy_profile.active]
        failing = [r for r in reservations if r.proxy_profile.active and r.proxy_profile.last_test_status is not True]
        # still listed but failing: the reservation stays (stable addresses), unless nothing else is left
        to_replace = gone + (failing if not usable else [])
        if not to_replace:
            if usable:
                healthy_accounts.append(account)
            continue
        removed, added = [], []
        with transaction.atomic():
            for reservation in to_replace:
                removed.append(reservation.proxy_profile_id)
                reservation.delete()
            keep = len(reservations) - len(to_replace)
            for proxy in list(_free_pool()[: max(0, MAX_PROXIES_PER_ACCOUNT - keep)]):
                AccountProxyReservation.objects.create(account=account, proxy_profile=proxy)
                added.append(proxy.pk)
        report["accounts_replaced"].append({"account": account.label, "removed": removed, "added": added})
        logger.warning("Proxy maintenance: account %s lost proxy profiles %s, got %s", account.label, removed, added)
        if usable or added:
            healthy_accounts.append(account)
        else:
            report["without_proxy"].append(f"conta {account.label}")
    return healthy_accounts


def _clear_proxy_login_blocks(accounts, report: dict, now) -> None:
    from apps.accounts.models import GameAccount

    blocked = GameAccount.objects.filter(account__in=accounts, login_blocked_until__gt=now)
    for game_account in blocked:
        if not is_proxy_side_failure(game_account.login_block_reason):
            continue                            # blocked by the game (loginLink 400): not ours to lift
        game_account.login_blocked_until = None
        game_account.login_block_backoff_hours = 0
        game_account.login_block_reason = ""
        game_account.save(update_fields=["login_blocked_until", "login_block_backoff_hours", "login_block_reason", "updated_at"])
        report["blocks_cleared"].append(game_account.name or str(game_account.pk))


def announcement(report: dict) -> str:
    """What changed, in the operator's words; "" when nothing did. Never an address or a credential."""
    lines = []
    for item in report.get("accounts_replaced") or []:
        removed, added = len(item.get("removed") or []), len(item.get("added") or [])
        lines.append(f"Conta {item.get('account')}: {removed} proxy(s) fora de uso, {added} novo(s) reservado(s).")
    for item in report.get("nodes_replaced") or []:
        lines.append(f"No {item.get('node')}: proxy trocado.")
    if report.get("blocks_cleared"):
        lines.append("Bloqueio de login liberado: " + ", ".join(report["blocks_cleared"]) + ".")
    if report.get("without_proxy"):
        lines.append("SEM proxy livre para: " + ", ".join(report["without_proxy"]) + ".")
    if not lines:
        return ""
    sync = report.get("sync") or {}
    if sync.get("success"):
        lines.append(f"Lista do provedor: {sync.get('created') or 0} novo(s), {sync.get('deactivated') or 0} retirado(s).")
    else:
        lines.append("Nao foi possivel consultar o provedor nesta execucao.")
    lines.append(f"Teste: {report.get('passed', 0)} de {report.get('tested', 0)} proxies ok.")
    if report.get("reason"):
        lines.append(f"Motivo da verificacao: {report['reason']}.")
    return "\n".join(lines)


def _announce(report: dict) -> None:
    body = announcement(report)
    if not body:
        return
    try:
        from apps.telegram.services.notifications import notify

        notify(event_key="proxy_change", title="Proxies: verificacao automatica", body=body)
    except Exception:  # noqa: BLE001 - telling about it must not undo the repair
        logger.warning("Could not announce the proxy maintenance", exc_info=True)


def run_proxy_maintenance(*, reason: str = "") -> dict:
    """Sync, test, replace what is gone, lift proxy-caused login blocks. Returns (and stores) a summary."""
    from apps.settings_app.utils import set_setting

    from .models import ProxyProfile
    from .services import sync_webshare

    now = timezone.now()
    report = {
        "at": now.isoformat(), "reason": reason[:120], "sync": {}, "tested": 0, "passed": 0,
        "nodes_replaced": [], "accounts_replaced": [], "blocks_cleared": [], "without_proxy": [],
    }
    try:
        synced = sync_webshare()
        report["sync"] = {key: synced.get(key) for key in ("success", "total", "created", "updated", "deactivated", "error")}
    except Exception as exc:  # noqa: BLE001 - without the provider we still test and heal with what is known
        report["sync"] = {"success": False, "error": type(exc).__name__}

    active = list(ProxyProfile.objects.filter(active=True))
    probes = _probe_all(active)
    for proxy in active:
        result = probes.get(proxy.pk)
        if result is None:
            continue
        _store_probe(proxy, result, now)
        report["tested"] += 1
        report["passed"] += int(result["success"])

    _heal_nodes(report, now)
    healthy_accounts = _heal_accounts(report)
    _clear_proxy_login_blocks(healthy_accounts, report, now)

    set_setting(LAST_RUN_SETTING, now.isoformat())
    set_setting(LAST_RESULT_SETTING, json.dumps(report))
    _announce(report)
    logger.info(
        "Proxy maintenance (%s): sync=%s tested=%d passed=%d nodes=%d accounts=%d blocks=%d without=%s",
        reason or "-", report["sync"].get("success"), report["tested"], report["passed"],
        len(report["nodes_replaced"]), len(report["accounts_replaced"]), len(report["blocks_cleared"]), report["without_proxy"],
    )
    return report


def last_run():
    from django.utils.dateparse import parse_datetime

    from apps.settings_app.utils import get_setting

    raw = get_setting(LAST_RUN_SETTING, "")
    return parse_datetime(raw) if raw else None


def last_result() -> dict:
    from apps.settings_app.utils import get_setting

    try:
        data = json.loads(get_setting(LAST_RESULT_SETTING, "") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _spawn(work) -> None:
    """Run `work` off the request. Tests replace this to run it inline."""
    threading.Thread(target=work, name="proxy-maintenance", daemon=True).start()


def trigger_proxy_maintenance(*, reason: str, min_interval: timedelta = FAILURE_INTERVAL) -> bool:
    """Start a run in the background unless one is running or one ran within `min_interval`. True if started."""
    previous = last_run()
    if previous is not None and timezone.now() - previous < min_interval:
        return False
    if not cache.add(LOCK_KEY, 1, LOCK_SECONDS):
        return False

    caller = threading.get_ident()

    def work():
        try:
            run_proxy_maintenance(reason=reason)
        except Exception:  # noqa: BLE001 - a maintenance run never takes a request down with it
            logger.exception("Proxy maintenance failed (%s)", reason)
        finally:
            cache.delete(LOCK_KEY)
            if threading.get_ident() != caller:
                connections.close_all()         # this thread's own connections, opened for the run

    try:
        _spawn(work)
    except Exception:
        cache.delete(LOCK_KEY)
        raise
    return True


def trigger_if_stale(*, reason: str = "periodico") -> bool:
    """The periodic safety net: any heartbeat calls this; it only runs when the last run is old."""
    return trigger_proxy_maintenance(reason=reason, min_interval=PERIODIC_INTERVAL)
