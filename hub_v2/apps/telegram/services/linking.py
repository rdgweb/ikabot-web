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

def generate_global_link_code() -> str:
    """
    Generate a 6-digit linking code for the global bot config.
    """
    config, _ = TelegramBotConfig.objects.get_or_create(pk=1)
    code = str(secrets.randbelow(900000) + 100000)
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
    try:
        config = TelegramBotConfig.objects.get(
            pk=1,
            link_code=code,
            link_status="pending",
        )
    except TelegramBotConfig.DoesNotExist:
        return None

    if _is_code_expired(config.link_code_created_at):
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


def unlink_global() -> None:
    """Unlink the global Telegram chat."""
    try:
        config = TelegramBotConfig.objects.get(pk=1)
    except TelegramBotConfig.DoesNotExist:
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
    code = str(secrets.randbelow(900000) + 100000)
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
    # Try per-subconta first
    try:
        config = TelegramAccountConfig.objects.get(
            link_code=code,
            link_status="pending",
        )
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
