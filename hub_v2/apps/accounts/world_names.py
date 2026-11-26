"""N-241: the official name of each world ("Gigas" for s78-br), read from the game lobby's public server list.

The list is cached for a day; the last good copy is kept for a month so a slow or offline lobby never empties the
names, and a failed read is not retried for ten minutes. A world the list does not know keeps its code.
"""
from __future__ import annotations

import logging
import re
import sys
import threading

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

LOBBY_SERVERS_URL = "https://lobby.ikariam.gameforge.com/api/servers"
CACHE_KEY = "world_names:v1"
LAST_GOOD_KEY = "world_names:v1:last_good"
CACHE_SECONDS = 24 * 3600
LAST_GOOD_SECONDS = 30 * 24 * 3600
FAILURE_SECONDS = 10 * 60
TIMEOUT_SECONDS = 4
REFRESH_LOCK_KEY = "world_names:v1:refreshing"

_CODE_RE = re.compile(r"^s(\d+)-([a-z]{2,3})$", re.IGNORECASE)


def _from_payload(payload) -> dict[str, str]:
    names = {}
    for server in payload if isinstance(payload, list) else []:
        if not isinstance(server, dict):
            continue
        language = str(server.get("language") or "").strip().lower()
        number = server.get("number")
        name = str(server.get("name") or "").strip()
        if language and isinstance(number, int) and name:
            names[f"s{number}-{language}"] = name[:40]
    return names


def refresh_world_names() -> dict[str, str]:
    """Read the lobby's list now (blocking) and cache it."""
    try:
        response = requests.get(LOBBY_SERVERS_URL, timeout=TIMEOUT_SECONDS, headers={"Accept": "application/json"})
        response.raise_for_status()
        names = _from_payload(response.json())
        if not names:
            raise ValueError("lista de servidores vazia")
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.info("world names unavailable: %s", exc)
        names = cache.get(LAST_GOOD_KEY)
        names = names if isinstance(names, dict) else {}
        cache.set(CACHE_KEY, names, FAILURE_SECONDS)
        return names
    cache.set(CACHE_KEY, names, CACHE_SECONDS)
    cache.set(LAST_GOOD_KEY, names, LAST_GOOD_SECONDS)
    return names


def _refresh_in_background() -> None:
    if sys.argv[1:2] == ["test"]:          # never reach the lobby from the test suite
        return
    if not cache.add(REFRESH_LOCK_KEY, 1, FAILURE_SECONDS):
        return                               # another request is already reading it
    threading.Thread(target=refresh_world_names, name="world-names", daemon=True).start()


def world_names() -> dict[str, str]:
    """{"s78-br": "Gigas", ...} from the cache — a page never waits for the lobby; a missing or old list is read
    again in the background and the last good copy is used meanwhile (empty only before the first read)."""
    cached = cache.get(CACHE_KEY)
    if isinstance(cached, dict):
        return cached
    _refresh_in_background()
    last = cache.get(LAST_GOOD_KEY)
    return last if isinstance(last, dict) else {}


def world_name(server_id) -> str:
    """The world's name, or "" when unknown."""
    code = str(server_id or "").strip().lower()
    if not _CODE_RE.match(code):
        return ""
    return world_names().get(code, "")


def world_label(server_id) -> str:
    """"Gigas · s78-br" — the code alone when the name is unknown."""
    code = str(server_id or "").strip()
    name = world_name(code)
    return f"{name} · {code}" if name else code
