"""Watch over ikabotapi (N-132): its health check only looks at the front page, so a frozen browser
inside it looks healthy while every login fails. The blackbox requests themselves are the real test:
after a few failures in a row the platform is told — its Telegram chat and the Dashboard."""
from __future__ import annotations

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

FAILS_KEY = "ikabotapi:blackbox-fails"
STUCK_KEY = "ikabotapi:stuck"
FAILS_IN_A_ROW = 3
WINDOW_SECONDS = 15 * 60


def is_stuck() -> bool:
    try:
        return bool(cache.get(STUCK_KEY))
    except Exception:  # noqa: BLE001
        return False


def record_success() -> None:
    try:
        cache.delete(FAILS_KEY)
        if cache.get(STUCK_KEY):
            cache.delete(STUCK_KEY)
            _tell("✅ IkaHub: o ikabotapi voltou a gerar o blackbox; os logins seguem normalmente.")
    except Exception:  # noqa: BLE001 - the watch never breaks a login
        logger.debug("ikabotapi watch could not record a success")


def record_failure(reason: str) -> None:
    try:
        cache.add(FAILS_KEY, 0, timeout=WINDOW_SECONDS)
        fails = cache.incr(FAILS_KEY)
        if fails < FAILS_IN_A_ROW:
            return
        first_time = cache.add(STUCK_KEY, 1, timeout=6 * 3600)
    except Exception:  # noqa: BLE001
        return
    if first_time:
        _tell(f"⚠️ IkaHub: o ikabotapi não está gerando o blackbox ({fails} falhas seguidas, {reason}): "
              "os logins estão parados. Reinicie o ikabotapi.")
    logger.error("ikabotapi looks frozen (%s blackbox failures in a row, last: %s)", fails, reason)


def _tell(text: str) -> None:
    """The platform's own Telegram chat (the default organization's)."""
    try:
        from apps.orgs.services import default_organization
        from apps.telegram.services.bot_api import send_message
        from apps.telegram.services.config import organization_config, platform_bot

        if not platform_bot().is_active:
            return
        config = organization_config(default_organization())
        if config is not None and config.chat_id:
            send_message(config.chat_id, text)
    except Exception:  # noqa: BLE001
        logger.warning("ikabotapi watch could not send the Telegram notice")
