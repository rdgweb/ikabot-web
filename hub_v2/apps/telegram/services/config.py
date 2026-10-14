"""Telegram per organization (N-106, stage 4).

One bot for the whole platform (the token, the webhook) and, for each organization, its own linked
chat and its own choice of notices. The bot lives in row pk=1, which is also the default
organization's chat; every other organization has a row of its own.

Every update that arrives is tied to the organization of its chat and handled inside that
organization's scope; a chat that is not linked can only link itself.
"""
from __future__ import annotations

import secrets

from django.core.cache import cache

from apps.telegram.models import TelegramAccountConfig, TelegramBotConfig

LINK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I
LINK_CODE_LENGTH = 10
LINK_FAILURES_ALLOWED = 5
LINK_FAILURE_WINDOW_SECONDS = 3600


def new_link_code() -> str:
    """Long enough that nobody guesses another organization's pending code."""
    return "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))


def platform_bot() -> TelegramBotConfig:
    """The bot itself: token, on/off, webhook secret."""
    bot, _ = TelegramBotConfig._base_manager.get_or_create(pk=1)
    return bot


def organization_config(organization) -> TelegramBotConfig | None:
    """The organization's chat and notice choices (created on first use)."""
    if organization is None:
        return None
    found = TelegramBotConfig._base_manager.filter(organization=organization).order_by("pk").first()
    if found is not None:
        return found
    from apps.orgs.services import DEFAULT_SLUG

    if organization.slug == DEFAULT_SLUG:
        # the bot's own row, before it had an owner, is the default organization's chat
        bot = platform_bot()
        if bot.organization_id is None:
            bot.organization = organization
            bot.save(update_fields=["organization", "updated_at"])
            return bot
    return TelegramBotConfig._base_manager.create(organization=organization, enabled=True)


def _organization_of(game_account=None, account=None):
    from apps.accounts.models import Account
    from apps.orgs.scope import current_organization
    from apps.orgs.services import default_organization

    if game_account is not None:
        account = Account._base_manager.select_related("organization").filter(pk=game_account.account_id).first()
    if account is not None and getattr(account, "organization_id", None):
        return account.organization
    return current_organization() or default_organization()


def config_for(game_account=None, account=None) -> TelegramBotConfig | None:
    return organization_config(_organization_of(game_account=game_account, account=account))


def destination_chat(game_account) -> str:
    """Where a notice about this game account goes: its own chat, else its organization's."""
    if not platform_bot().is_active:
        return ""
    override = TelegramAccountConfig._base_manager.filter(game_account=game_account).first()
    if override and override.enabled and override.chat_id:
        return str(override.chat_id)
    config = config_for(game_account=game_account)
    return str(config.chat_id or "") if config else ""


def organization_for_chat(chat_id):
    """The organization a Telegram chat belongs to, or None (not linked)."""
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return None
    config = (TelegramBotConfig._base_manager.filter(chat_id=chat_id, link_status="linked")
              .exclude(organization=None).select_related("organization").first())
    if config is not None:
        return config.organization
    override = (TelegramAccountConfig._base_manager.filter(chat_id=chat_id)
                .select_related("game_account__account__organization").first())
    if override is not None:
        return override.game_account.account.organization
    return None


# ── link codes: limited attempts per chat ──────────────────────────────────────

def _failures_key(chat_id) -> str:
    return f"telegram:link-failures:{chat_id}"


def link_blocked(chat_id) -> bool:
    return int(cache.get(_failures_key(chat_id)) or 0) >= LINK_FAILURES_ALLOWED


def link_failed(chat_id) -> None:
    key = _failures_key(chat_id)
    if cache.add(key, 1, timeout=LINK_FAILURE_WINDOW_SECONDS):
        return
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=LINK_FAILURE_WINDOW_SECONDS)
