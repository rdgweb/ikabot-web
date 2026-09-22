"""Usage telemetry: on by default, announced on first use, every item can be switched off.

How it behaves:

* Nothing is sent until an admin has been shown the notice (banner) at least once.
* From then on a summary is sent once a day, unless an admin turns telemetry off
  (Settings -> Telemetria) or ``TELEMETRY_DISABLED=true`` is set in the environment.
* Each optional item (see ``ITEMS``) can be switched off individually; an item that
  is off is neither sent nor recorded by the receiver.
* The payload is built by ``build_payload`` and is exactly what ``/telemetry/preview/``
  shows. The receiver lives in ``telemetry_v1`` and is documented publicly at
  ``<TELEMETRY_URL>/transparencia``.
"""

from __future__ import annotations

import logging
import platform
import re
import threading
import uuid
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.settings_app.utils import get_setting, set_setting

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SEND_INTERVAL = timedelta(hours=24)
RETRY_INTERVAL = timedelta(hours=1)
TICK_CACHE_KEY = "ikabot:telemetry:tick"
TICK_SECONDS = 900
LOCK_CACHE_KEY = "ikabot:telemetry:lock"

STATUS_UNSET = "unset"  # nobody decided yet: on by default once the notice was shown
STATUS_ENABLED = "enabled"  # an admin acknowledged / confirmed
STATUS_DISABLED = "disabled"

KEY_STATUS = "telemetry_status"
KEY_INSTALL_ID = "telemetry_install_id"
KEY_LAST_SENT = "telemetry_last_sent_at"
KEY_LAST_ATTEMPT = "telemetry_last_attempt_at"
KEY_LAST_ERROR = "telemetry_last_error"
KEY_DECIDED_AT = "telemetry_decided_at"
KEY_NOTICE_SHOWN = "telemetry_notice_shown_at"
ITEM_KEY_PREFIX = "telemetry_item_"

# Always sent while telemetry is on (they identify the installation and its release):
# schema version, random install id, hub version.
ITEMS = [
    {
        "key": "versions",
        "label": "Versões e arquitetura",
        "help": "Versão de cada agent e arquitetura da máquina (amd64/arm64).",
    },
    {
        "key": "counts",
        "label": "Quantidades",
        "help": "Número de contas do lobby, contas de jogo e nós ativos.",
    },
    {
        "key": "worlds",
        "label": "Servidores e mundos do Ikariam",
        "help": "Servidor e mundo (ex.: br / 61) com o número de contas em cada um.",
    },
    {
        "key": "usage",
        "label": "Uso por categoria",
        "help": "Quantos jobs por categoria (militar, mercado…) nos últimos 30 dias.",
    },
    {
        "key": "timezone",
        "label": "Fuso horário",
        "help": "O fuso horário configurado neste hub (ex.: America/Cuiaba).",
    },
    {
        "key": "ip",
        "label": "Endereço IP e localização aproximada",
        "help": (
            "O servidor registra o IP de onde o resumo chega (guardado por até 90 dias) "
            "e deriva país, região e cidade aproximados. Desligado: sem IP, só o país do fuso."
        ),
    },
]
ITEM_KEYS = [item["key"] for item in ITEMS]

TELEMETRY_KEYS = [
    KEY_STATUS, KEY_INSTALL_ID, KEY_LAST_SENT, KEY_LAST_ATTEMPT, KEY_LAST_ERROR,
    KEY_DECIDED_AT, KEY_NOTICE_SHOWN, *[ITEM_KEY_PREFIX + key for key in ITEM_KEYS],
]

_CATEGORY_RE = re.compile(r"[^a-z_]")


def endpoint() -> str:
    return str(getattr(settings, "TELEMETRY_URL", "") or "").rstrip("/")


def kill_switch_on() -> bool:
    """TELEMETRY_DISABLED=true (or an empty endpoint) overrides everything else."""
    return bool(getattr(settings, "TELEMETRY_DISABLED", False)) or not endpoint()


def get_status() -> str:
    if kill_switch_on():
        return STATUS_DISABLED
    status = get_setting(KEY_STATUS, STATUS_UNSET)
    return status if status in (STATUS_ENABLED, STATUS_DISABLED) else STATUS_UNSET


def notice_shown() -> bool:
    return bool(get_setting(KEY_NOTICE_SHOWN, ""))


def is_active() -> bool:
    """True when a ping may be sent: not disabled, and (confirmed or notice already shown)."""
    status = get_status()
    if status == STATUS_ENABLED:
        return True
    return status == STATUS_UNSET and notice_shown()


def needs_notice() -> bool:
    """The first-use banner is shown while nobody has decided yet."""
    return get_status() == STATUS_UNSET


def mark_notice_shown() -> None:
    if not notice_shown():
        set_setting(KEY_NOTICE_SHOWN, timezone.now().isoformat())


def get_install_id() -> str:
    value = get_setting(KEY_INSTALL_ID, "")
    if not value:
        value = str(uuid.uuid4())
        set_setting(KEY_INSTALL_ID, value)
    return value


# --- items ----------------------------------------------------------------------------


def item_enabled(key: str) -> bool:
    return get_setting(ITEM_KEY_PREFIX + key, "1") != "0"


def enabled_items() -> list[str]:
    return [key for key in ITEM_KEYS if item_enabled(key)]


def save_items(selected: set[str]) -> None:
    for key in ITEM_KEYS:
        set_setting(ITEM_KEY_PREFIX + key, "1" if key in selected else "0")


def items_context() -> list[dict]:
    return [dict(item, enabled=item_enabled(item["key"])) for item in ITEMS]


# --- payload ----------------------------------------------------------------------------


def _arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return "other"


def _category_for(action_code: int) -> str:
    from core.actions import get_action_info

    info = get_action_info(action_code) or {}
    return _CATEGORY_RE.sub("", str(info.get("category") or "other").lower())[:32] or "other"


def build_payload(install_id: str | None = None, items: list[str] | None = None) -> dict:
    """Build the exact JSON document that is sent (schema v1). No credentials or game content."""
    from apps.accounts.models import Account, GameAccount, Node
    from apps.jobs.models import Job

    items = enabled_items() if items is None else items
    payload: dict = {
        "schema": SCHEMA_VERSION,
        "install_id": install_id or get_install_id(),
        "hub_version": str(settings.VERSION),
        # Tells the receiver whether it may record the caller's IP (and geolocate it).
        "share_ip": "ip" in items,
    }
    nodes = Node.objects.filter(active=True)

    if "versions" in items:
        agent_versions = (
            nodes.exclude(agent_version="").values("agent_version").annotate(n=Count("id")).order_by("agent_version")
        )
        payload["arch"] = _arch()
        payload["agents"] = [{"version": row["agent_version"], "nodes": row["n"]} for row in agent_versions]
    if "timezone" in items:
        payload["timezone"] = str(settings.TIME_ZONE)
    if "counts" in items:
        payload["counts"] = {
            "lobby_accounts": Account.objects.filter(active=True).count(),
            "game_accounts": GameAccount.objects.filter(active=True).count(),
            "nodes": nodes.count(),
        }
    if "worlds" in items:
        worlds = (
            GameAccount.objects.filter(active=True)
            .values("server_language", "server_number")
            .annotate(n=Count("id"))
            .order_by("server_language", "server_number")
        )
        payload["worlds"] = [
            {"server": str(row["server_language"]).lower(), "world": row["server_number"], "accounts": row["n"]}
            for row in worlds
        ]
    if "usage" in items:
        since = timezone.now() - timedelta(days=30)
        usage: dict[str, int] = {}
        for row in Job.objects.filter(created_at__gte=since).values("action_code").annotate(n=Count("id")):
            category = _category_for(row["action_code"])
            usage[category] = usage.get(category, 0) + row["n"]
        payload["usage_30d"] = [{"category": key, "jobs": value} for key, value in sorted(usage.items())]
    return payload


# --- transport ------------------------------------------------------------------------


def _url(path: str) -> str:
    return f"{endpoint()}{path}"


def _headers() -> dict:
    return {"User-Agent": f"ikabot-hub/{settings.VERSION}"}


def send_now() -> tuple[bool, str]:
    """Send one ping. Returns (ok, message). Never raises."""
    if not is_active():
        return False, "Telemetria desativada."
    set_setting(KEY_LAST_ATTEMPT, timezone.now().isoformat())
    try:
        response = requests.post(
            _url("/v1/ping"), json=build_payload(), headers=_headers(), timeout=(3, 8),
        )
        if response.status_code in (200, 204):
            set_setting(KEY_LAST_SENT, timezone.now().isoformat())
            set_setting(KEY_LAST_ERROR, "")
            return True, "Resumo enviado."
        message = f"Servidor respondeu HTTP {response.status_code}."
    except requests.RequestException as exc:
        message = f"Falha de rede: {exc.__class__.__name__}."
    except Exception as exc:  # noqa: BLE001 - telemetry must never break the hub
        logger.exception("telemetry payload build failed")
        message = f"Erro interno: {exc.__class__.__name__}."
    set_setting(KEY_LAST_ERROR, message)
    return False, message


def enable() -> None:
    """Confirm telemetry (also used to turn it back on after it was disabled)."""
    get_install_id()
    set_setting(KEY_STATUS, STATUS_ENABLED)
    set_setting(KEY_DECIDED_AT, timezone.now().isoformat())
    set_setting(KEY_LAST_ERROR, "")


def decline() -> None:
    set_setting(KEY_STATUS, STATUS_DISABLED)
    set_setting(KEY_DECIDED_AT, timezone.now().isoformat())


def disable_and_erase() -> tuple[bool, str]:
    """Turn telemetry off and ask the receiver to erase everything this install sent."""
    install_id = get_setting(KEY_INSTALL_ID, "")
    set_setting(KEY_STATUS, STATUS_DISABLED)
    set_setting(KEY_DECIDED_AT, timezone.now().isoformat())
    erased, message = True, "Telemetria desativada e dados enviados anteriormente apagados no servidor."
    if install_id and endpoint():
        try:
            response = requests.delete(
                _url(f"/v1/installs/{install_id}"), headers=_headers(), timeout=(3, 8),
            )
            if response.status_code not in (200, 204):
                erased, message = False, f"Telemetria desativada, mas o servidor respondeu HTTP {response.status_code} ao apagar os dados."
        except requests.RequestException:
            erased, message = False, "Telemetria desativada, mas não foi possível contatar o servidor para apagar os dados enviados."
    if erased:
        # Forget the identifier: turning it on again later starts from a fresh, unrelated ID.
        set_setting(KEY_INSTALL_ID, "")
    set_setting(KEY_LAST_SENT, "")
    return erased, message


def _parse(value: str):
    return parse_datetime(value) if value else None


def is_due(now=None) -> bool:
    if not is_active():
        return False
    now = now or timezone.now()
    last_sent = _parse(get_setting(KEY_LAST_SENT, ""))
    last_attempt = _parse(get_setting(KEY_LAST_ATTEMPT, ""))
    if last_attempt and now - last_attempt < RETRY_INTERVAL:
        return False
    return last_sent is None or now - last_sent >= SEND_INTERVAL


def _send_in_background() -> None:
    try:
        if cache.add(LOCK_CACHE_KEY, "1", 120):
            try:
                send_now()
            finally:
                cache.delete(LOCK_CACHE_KEY)
    except Exception:  # noqa: BLE001
        logger.exception("telemetry background send failed")
    finally:
        connections.close_all()


def tick() -> bool:
    """Called from the middleware; cheap, throttled, and safe across workers."""
    try:
        if not cache.add(TICK_CACHE_KEY, "1", TICK_SECONDS):
            return False
        if not is_due():
            return False
    except Exception:  # noqa: BLE001 - cache/db hiccups must not affect requests
        return False
    threading.Thread(target=_send_in_background, name="telemetry-ping", daemon=True).start()
    return True


def status_context() -> dict:
    """Template context for the settings card."""
    return {
        "telemetry_status": get_status(),
        "telemetry_active": is_active(),
        "telemetry_kill_switch": kill_switch_on(),
        "telemetry_endpoint": endpoint(),
        "telemetry_items": items_context(),
        "telemetry_last_sent": _parse(get_setting(KEY_LAST_SENT, "")),
        "telemetry_last_error": get_setting(KEY_LAST_ERROR, ""),
        "telemetry_install_id": get_setting(KEY_INSTALL_ID, ""),
    }
