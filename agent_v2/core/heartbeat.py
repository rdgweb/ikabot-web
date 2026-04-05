"""Periodic heartbeat to hub with proxy IP watchdog.

The heartbeat checks the exit IP every cycle. If the IP doesn't match
the expected proxy IP (proxy down, rotated, fell back to local) the
agent is **paused** immediately — no jobs are dispatched until a valid
proxy is restored (either the current one recovers or the hub assigns
a new one).
"""

import logging
import threading
import time

import requests as _requests

from .config import settings
from .hub_client import HubClient

logger = logging.getLogger(__name__)

# How often to force a fresh IP check (even when everything is OK)
_IP_REFRESH_INTERVAL = 300  # 5 min (was 30 min — tighter now for safety)

# When paused (proxy mismatch), retry every N seconds
_RECOVERY_INTERVAL = 30

_IP_SERVICES = [
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://checkip.amazonaws.com",
]


def _fetch_external_ip(proxy_url: str = "") -> str:
    """Try each IP-check service and return the first successful result.

    When *proxy_url* is set the check goes through the proxy so the
    reported IP matches the one that game servers will actually see.
    """
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    for url in _IP_SERVICES:
        try:
            resp = _requests.get(url, timeout=8, proxies=proxies)
            if resp.status_code == 200:
                return resp.text.strip()
        except Exception:
            continue
    return ""


def _extract_ip(proxy_url: str) -> str:
    """Extract the host/IP from a proxy URL like http://user:pass@1.2.3.4:port."""
    if not proxy_url:
        return ""
    try:
        # Remove scheme
        rest = proxy_url.split("://", 1)[-1]
        # Remove credentials
        if "@" in rest:
            rest = rest.split("@", 1)[-1]
        # Remove port
        host = rest.split(":")[0]
        return host
    except Exception:
        return ""


class HeartbeatThread(threading.Thread):
    """Heartbeat + proxy IP watchdog.

    Exposes ``proxy_ok`` (threading.Event) that the dispatcher checks
    before running any job.  When the exit IP doesn't match the expected
    proxy IP, the event is **cleared** → dispatcher blocks.
    """

    def __init__(self, hub: HubClient, proxy_url: str = ""):
        super().__init__(daemon=True)
        self.hub = hub
        self.proxy_url = proxy_url
        self._expected_ip = _extract_ip(proxy_url)
        self._running = True
        self._external_ip = ""
        self._ip_last_fetched = 0.0

        # Dispatcher waits on this event before running jobs.
        # Starts as "set" (OK) — cleared when IP mismatch is detected.
        self.proxy_ok = threading.Event()
        self.proxy_ok.set()

    # ── IP check ────────────────────────────────────────────────────

    def _check_ip(self, force: bool = False) -> str:
        """Fetch exit IP via proxy. Returns the IP or '' on failure."""
        now = time.monotonic()
        if not force and (now - self._ip_last_fetched < _IP_REFRESH_INTERVAL) and self._external_ip:
            return self._external_ip

        ip = _fetch_external_ip(self.proxy_url)
        if ip:
            old = self._external_ip
            self._external_ip = ip
            self._ip_last_fetched = now
            if not old:
                logger.info("External IP (via %s): %s",
                            "proxy" if self.proxy_url else "direct", ip)
            elif old != ip:
                logger.warning("External IP changed: %s → %s", old, ip)
        return ip

    # ── Proxy recovery ──────────────────────────────────────────────

    def _try_recover(self) -> bool:
        """Try to get a working proxy. Returns True if recovered."""
        # 1. Re-fetch config from hub — maybe a new proxy was assigned
        try:
            config = self.hub.get_config()
            new_proxy = config.get("proxy", "")
        except Exception as e:
            logger.warning("Failed to fetch config during recovery: %s", e)
            return False

        if not new_proxy:
            logger.warning("Hub returned no proxy — agent stays paused")
            return False

        # Update proxy if hub assigned a different one
        new_ip = _extract_ip(new_proxy)
        if new_proxy != self.proxy_url:
            logger.info("Hub assigned new proxy: %s (was %s)",
                        new_ip, self._expected_ip)
            self.proxy_url = new_proxy
            self._expected_ip = new_ip

        # 2. Test the (possibly new) proxy
        ip = _fetch_external_ip(self.proxy_url)
        if not ip:
            logger.warning("Proxy unreachable (%s) — agent stays paused", self._expected_ip)
            return False

        if ip != self._expected_ip:
            logger.warning("Proxy IP mismatch: expected %s, got %s — agent stays paused",
                           self._expected_ip, ip)
            return False

        # Recovered
        self._external_ip = ip
        self._ip_last_fetched = time.monotonic()
        return True

    # ── Main loop ───────────────────────────────────────────────────

    def run(self):
        while self._running:
            try:
                if self.proxy_ok.is_set():
                    self._normal_cycle()
                else:
                    self._recovery_cycle()
            except Exception as e:
                logger.warning("Heartbeat error: %s", e)
            time.sleep(
                settings.heartbeat_interval if self.proxy_ok.is_set()
                else _RECOVERY_INTERVAL
            )

    def _normal_cycle(self):
        """Regular heartbeat: check IP, send heartbeat, pause if mismatch."""
        ip = self._check_ip()

        if not ip:
            # Can't reach any IP service — proxy might be down
            logger.warning("IP check failed — forcing re-check")
            ip = self._check_ip(force=True)

        if not ip:
            logger.error("All IP services unreachable via proxy — PAUSING agent")
            self.proxy_ok.clear()
            return

        # If we have an expected proxy IP, verify it matches
        if self._expected_ip and ip != self._expected_ip:
            logger.error(
                "EXIT IP CHANGED: expected %s (proxy), got %s — PAUSING agent immediately",
                self._expected_ip, ip,
            )
            self.proxy_ok.clear()
            return

        # All good — send heartbeat
        self.hub.heartbeat(external_ip=ip)
        logger.debug("Heartbeat sent (IP: %s)", ip)

    def _recovery_cycle(self):
        """Agent is paused — try to recover a working proxy."""
        logger.info("Attempting proxy recovery...")

        if self._try_recover():
            logger.info("Proxy recovered! Exit IP: %s — RESUMING agent", self._external_ip)
            self.proxy_ok.set()
            # Send heartbeat with restored IP
            self.hub.heartbeat(external_ip=self._external_ip)
        else:
            # Still broken — send heartbeat without IP to keep agent_last_seen_at alive
            try:
                self.hub.heartbeat()
            except Exception:
                pass

    def stop(self):
        self._running = False
