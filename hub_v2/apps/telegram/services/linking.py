"""
Service layer: Telegram account linking logic.

Handles code generation, validation, and unlinking for both
global (TelegramBotConfig) and per-subconta (TelegramAccountConfig).
"""

import secrets
from datetime import timedelta

from django.utils import timezone

from apps.telegram.models import TelegramBotConfig, TelegramAccountConfig

# Link codes expire after 10 minutes
LINK_CODE_TTL = timedelta(minutes=10)


# ── Global linking ───────────────────────────────────────────────────

def generate_global_link_code(organization=None) -> str:
    """
    A linking code for the organization's chat (N-106: long, not guessable).
    """
    from apps.orgs.scope import current_organization
    from apps.orgs.services import default_organization
    from apps.telegram.services.config import new_link_code, organization_config

    config = organization_config(organization or current_organization() or default_organization())
    code = new_link_code()
    config.link_code = code
    config.link_code_created_at = timezone.now()
    config.link_status = "pending"
    config.save(update_fields=[
        "link_code", "link_code_created_at", "link_status", "updated_at",
    ])
    return code


def validate_global_link_code(
    code: str, chat_id: str, username: str
) -> TelegramBotConfig | None:
    """
    Validate a global link code. Sets the global chat_id if valid.
    """
    config = TelegramBotConfig._base_manager.filter(link_code=code, link_status="pending").select_related("organization").first()
    if config is None or not code or _is_code_expired(config.link_code_created_at):
        return None
    # N-106: a chat already linked to another organization is not moved by a code
    from apps.telegram.services.config import organization_for_chat

    owner = organization_for_chat(chat_id)
    if owner is not None and config.organization_id and owner.pk != config.organization_id:
        return None

    config.chat_id = chat_id
    config.telegram_username = username
    config.link_status = "linked"
    config.link_code = ""
    config.link_code_created_at = None
    config.save(update_fields=[
        "chat_id", "telegram_username", "link_status",
        "link_code", "link_code_created_at", "updated_at",
    ])
    return config


def unlink_global(organization=None) -> None:
    """Unlink the organization's Telegram chat."""
    from apps.orgs.scope import current_organization
    from apps.orgs.services import default_organization
    from apps.telegram.services.config import organization_config

    config = organization_config(organization or current_organization() or default_organization())
    if config is None:
        return

    config.chat_id = ""
    config.telegram_username = ""
    config.link_status = "unlinked"
    config.link_code = ""
    config.link_code_created_at = None
    config.save(update_fields=[
        "chat_id", "telegram_username", "link_status",
        "link_code", "link_code_created_at", "updated_at",
    ])


# ── Per-GameAccount linking ──────────────────────────────────────────

def generate_link_code(game_account_id: str) -> str:
    """
    Generate a 6-digit linking code for a GameAccount.
    """
    config, _ = TelegramAccountConfig.objects.get_or_create(
        game_account_id=game_account_id,
    )
    from apps.telegram.services.config import new_link_code

    code = new_link_code()   # N-106: long, not guessable
    config.link_code = code
    config.link_code_created_at = timezone.now()
    config.link_status = "pending"
    config.save(update_fields=[
        "link_code", "link_code_created_at", "link_status", "updated_at",
    ])
    return code


def validate_link_code(
    code: str, chat_id: str, username: str
) -> TelegramAccountConfig | TelegramBotConfig | None:
    """
    Validate a 6-digit code sent via Telegram /start.

    Checks per-subconta codes first, then global code.
    Returns the linked config object or None.
    """
    # Try per-subconta first (any organization's: the code says which)
    try:
        config = TelegramAccountConfig._base_manager.select_related("game_account__account").get(
            link_code=code,
            link_status="pending",
        )
        from apps.telegram.services.config import organization_for_chat

        owner = organization_for_chat(chat_id)
        if owner is not None and owner.pk != config.game_account.account.organization_id:
            return None                      # N-106: a chat of another organization is not taken
        if not _is_code_expired(config.link_code_created_at):
            config.chat_id = chat_id
            config.telegram_username = username
            config.link_status = "linked"
            config.link_code = ""
            config.link_code_created_at = None
            config.enabled = True
            config.save(update_fields=[
                "chat_id", "telegram_username", "link_status",
                "link_code", "link_code_created_at", "enabled", "updated_at",
            ])
            return config
    except TelegramAccountConfig.DoesNotExist:
        pass

    # Try global
    result = validate_global_link_code(code, chat_id, username)
    if result:
        return result

    return None


def unlink_account(game_account_id: str) -> None:
    """
    Unlink a GameAccount from Telegram (remove custom override).
    """
    try:
        config = TelegramAccountConfig.objects.get(
            game_account_id=game_account_id,
        )
    except TelegramAccountConfig.DoesNotExist:
        return

    config.chat_id = ""
    config.telegram_username = ""
    config.link_status = "unlinked"
    config.link_code = ""
    config.link_code_created_at = None
    config.enabled = False
    config.save(update_fields=[
        "chat_id", "telegram_username", "link_status",
        "link_code", "link_code_created_at", "enabled", "updated_at",
    ])


# ── Helpers ──────────────────────────────────────────────────────────

def _is_code_expired(created_at) -> bool:
    """Check if a link code has expired (older than 10 minutes)."""
    if not created_at:
        return True
    return timezone.now() - created_at > LINK_CODE_TTL
