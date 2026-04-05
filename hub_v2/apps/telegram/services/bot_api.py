"""
Telegram Bot API client.

Thin wrapper around the Telegram Bot HTTP API for sending messages
and retrieving bot info.
"""

import logging

import requests

from apps.telegram.models import TelegramBotConfig

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}"
TIMEOUT = 10  # seconds


def _get_token() -> str | None:
    """Retrieve bot token from TelegramBotConfig singleton."""
    try:
        config = TelegramBotConfig.objects.get(pk=1)
        return config.bot_token or None
    except TelegramBotConfig.DoesNotExist:
        return None


def send_message(chat_id: str, text: str, parse_mode: str = "HTML") -> dict:
    """
    Send a text message to a Telegram chat.

    Returns the Telegram API response dict, or an error dict on failure.
    """
    token = _get_token()
    if not token:
        logger.error("Telegram bot token not configured.")
        return {"ok": False, "description": "Bot token not configured"}

    url = f"{API_BASE.format(token=token)}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        resp = requests.post(url, json=payload, timeout=TIMEOUT)
        return resp.json()
    except requests.RequestException as exc:
        logger.exception("Failed to send Telegram message: %s", exc)
        return {"ok": False, "description": str(exc)}


def get_bot_info() -> dict | None:
    """
    Call getMe to retrieve bot information.

    Returns the bot info dict or None on failure.
    """
    token = _get_token()
    if not token:
        return None

    url = f"{API_BASE.format(token=token)}/getMe"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        data = resp.json()
        if data.get("ok"):
            return data.get("result")
        return None
    except requests.RequestException as exc:
        logger.exception("Failed to get bot info: %s", exc)
        return None


def set_webhook(webhook_url: str, secret_token: str = "") -> dict:
    """
    Register a webhook URL with Telegram.

    Returns the Telegram API response dict.
    """
    token = _get_token()
    if not token:
        return {"ok": False, "description": "Bot token not configured"}

    url = f"{API_BASE.format(token=token)}/setWebhook"
    payload = {"url": webhook_url}
    if secret_token:
        payload["secret_token"] = secret_token

    try:
        resp = requests.post(url, json=payload, timeout=TIMEOUT)
        return resp.json()
    except requests.RequestException as exc:
        logger.exception("Failed to set webhook: %s", exc)
        return {"ok": False, "description": str(exc)}
