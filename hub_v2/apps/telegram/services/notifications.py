"""
Central notification dispatcher for Telegram.

Usage:
    from apps.telegram.services.notifications import notify

    # From a signal or view:
    notify(
        event_key="job_failed",
        game_account=job.game_account,
        account=job.account,
        title="Check Status falhou",
        body="Erro ao conectar no servidor s61-br.",
        node=job.node,
        job=job,
        agent_name=job.agent,
    )

Resolution logic:
    1. Bot must be active (enabled + token)
    2. If game_account provided:
       a. Check per-subconta toggle → if ON, use subconta chat_id (or global fallback)
       b. If subconta toggle OFF, check global toggle → if ON, use global chat_id
    3. If no game_account (system event):
       a. Check global toggle → if ON, use global chat_id
    4. Format message using event-specific template
    5. Send via Telegram API
    6. Log to MessageAudit
"""

import logging
from typing import Any

from .bot_api import send_message
from .formatters import format_message

logger = logging.getLogger("telegram.notifications")


def notify(
    event_key: str,
    game_account=None,
    account=None,
    title: str = "",
    body: str = "",
    node=None,
    job=None,
    agent_name: str = "",
    **extra,
) -> bool:
    """
    Send a Telegram notification if the event is enabled.

    Returns True if a message was sent, False otherwise.
    """
    from apps.telegram.models import TelegramBotConfig, TelegramAccountConfig

    # 1. Bot must be active
    try:
        bot = TelegramBotConfig.objects.get(pk=1)
    except TelegramBotConfig.DoesNotExist:
        return False

    if not bot.is_active:
        return False

    if not bot.chat_id and not game_account:
        # No global chat and no subconta — nowhere to send
        return False

    # 2. Resolve toggle + chat_id
    chat_id, source = _resolve_destination(bot, game_account, event_key)

    if not chat_id:
        logger.debug(
            "Notification suppressed: event=%s, ga=%s (toggle off or no chat)",
            event_key, game_account,
        )
        return False

    # 3. Build template context from all available objects
    # reply_markup is a Telegram-specific payload — extract before template rendering
    reply_markup = extra.pop("reply_markup", None) if extra else None

    tpl_ctx = _build_template_context(
        game_account=game_account,
        account=account,
        node=node,
        job=job,
        agent_name=agent_name,
        title=title,
        body=body,
        **extra,
    )

    text = format_message(event_key, **tpl_ctx)

    # Auto-build inline keyboard for diplomacy_message when not overridden
    if event_key == "diplomacy_message" and reply_markup is None:
        reply_markup = _build_diplomacy_keyboard(tpl_ctx)

    # 4. Send
    result = send_message(chat_id, text, reply_markup=reply_markup)
    ok = result.get("ok", False)

    # 5. Audit
    _create_audit(
        event_key=event_key,
        title=title,
        body=body,
        status="sent" if ok else "failed",
        error="" if ok else result.get("description", "Unknown error"),
        external_ref=str(result.get("result", {}).get("message_id", "")) if ok else "",
        account=account,
        game_account=game_account,
        node=node,
        job=job,
        agent_name=agent_name,
        context=tpl_ctx,
    )

    if ok:
        logger.info(
            "Notification sent: event=%s, chat=%s, source=%s",
            event_key, chat_id, source,
        )
    else:
        logger.warning(
            "Notification failed: event=%s, chat=%s, error=%s",
            event_key, chat_id, result.get("description"),
        )

    return ok


def _build_template_context(
    game_account=None,
    account=None,
    node=None,
    job=None,
    agent_name: str = "",
    title: str = "",
    body: str = "",
    **extra,
) -> dict:
    """
    Extract all available variables from the given objects.

    Returns a flat dict ready for template rendering.
    """
    ctx = {
        "action_name": "",
        "ga_name": "",
        "server_id": "",
        "account_name": "",
        "node_name": "",
        "agent_name": agent_name or "",
        "title": title or "",
        "body": body or "",
        "exit_code": str(extra.get("exit_code", "")) if extra.get("exit_code") is not None else "",
        "job_id": "",
        "status": "",
        "error": "",
    }

    if game_account:
        ctx["ga_name"] = game_account.name or str(game_account.server_id or "")
        ctx["server_id"] = str(game_account.server_id or "")

    if account:
        ctx["account_name"] = getattr(account, "label", "") or getattr(account, "name", "") or str(account)

    if node:
        ctx["node_name"] = getattr(node, "name", "") or str(node)

    if job:
        ctx["job_id"] = str(job.pk)
        ctx["status"] = getattr(job, "status", "")
        ctx["exit_code"] = str(job.exit_code) if getattr(job, "exit_code", None) is not None else ctx["exit_code"]
        ctx["error"] = getattr(job, "error_message", "") or ""

        # Resolve action_name from catalog
        action_code = getattr(job, "action_code", None)
        if action_code is not None:
            try:
                from core.contracts import ACTION_CATALOG
                action_info = ACTION_CATALOG.get(action_code)
                ctx["action_name"] = action_info["name"] if action_info else f"Acao #{action_code}"
            except Exception:
                ctx["action_name"] = f"Acao #{action_code}"

    # Override with explicit title/body if provided (for backward compat)
    if title and not ctx["action_name"]:
        ctx["action_name"] = title

    for key, value in extra.items():
        if value is None:
            ctx[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            ctx[key] = value
        else:
            ctx[key] = str(value)

    return ctx


def _resolve_destination(bot, game_account, event_key: str) -> tuple[str, str]:
    """
    Resolve which chat_id to send to and whether the toggle allows it.

    Returns (chat_id, source_label) or ("", "") if suppressed.
    """
    from apps.telegram.models import TelegramAccountConfig

    toggle_field = f"notify_{event_key}"

    if game_account:
        # Try per-subconta config
        try:
            ga_config = TelegramAccountConfig.objects.get(game_account=game_account)

            if ga_config.enabled and _get_toggle(ga_config, toggle_field):
                # Subconta allows this event
                if ga_config.chat_id:
                    return ga_config.chat_id, "subconta"
                elif bot.chat_id:
                    return bot.chat_id, "global (fallback)"
                return "", ""

        except TelegramAccountConfig.DoesNotExist:
            pass

    # Fall back to global config
    if _get_toggle(bot, toggle_field) and bot.chat_id:
        return bot.chat_id, "global"

    return "", ""


def _get_toggle(obj, field_name: str) -> bool:
    """Safely get a boolean toggle field, defaulting to False."""
    return getattr(obj, field_name, False)


def _create_audit(
    event_key: str,
    title: str,
    body: str,
    status: str,
    error: str = "",
    external_ref: str = "",
    account=None,
    game_account=None,
    node=None,
    job=None,
    agent_name: str = "",
    context: dict | None = None,
):
    """Create a MessageAudit record."""
    import json as _json
    from apps.telegram.models import MessageAudit

    try:
        MessageAudit.objects.create(
            account=account,
            game_account=game_account,
            node=node,
            job=job,
            provider="telegram",
            channel=event_key,
            status=status,
            agent_name=agent_name,
            title=title,
            body=body,
            error=error,
            external_ref=external_ref,
            context_json=_json.dumps(context or {}),
        )
    except Exception as e:
        logger.exception("Failed to create MessageAudit: %s", e)


def _build_diplomacy_keyboard(ctx: dict) -> dict | None:
    """Build Telegram inline keyboard for diplomacy_message notifications."""
    import re

    accept_cmd = str(ctx.get("accept_command") or "")
    decline_cmd = str(ctx.get("decline_command") or "")
    reply_cmd = str(ctx.get("reply_command") or "")

    # Extract UUID from any available command
    uuid_match = re.search(r"[\w-]{36}", accept_cmd or reply_cmd or decline_cmd)
    if not uuid_match:
        return None
    uuid = uuid_match.group()

    row1 = []
    if accept_cmd:
        row1.append({"text": "✅ Aceitar", "callback_data": f"accept:{uuid}"})
    if decline_cmd:
        row1.append({"text": "❌ Recusar", "callback_data": f"decline:{uuid}"})

    row2 = []
    if reply_cmd:
        row2.append({"text": "↩️ Responder", "callback_data": f"reply:{uuid}"})

    keyboard = [r for r in [row1, row2] if r]
    return {"inline_keyboard": keyboard} if keyboard else None
