"""
Message formatters for Telegram notifications.

Uses customizable templates stored in NotificationTemplate model.
Templates have separate title_template and body_template fields
with variables like {action_name}, {ga_name}, {server_id}, etc.

Supported HTML tags: <b>, <i>, <u>, <s>, <code>, <pre>, <a href="">
"""

import logging

logger = logging.getLogger(__name__)


def format_message(event_key: str, **kwargs) -> str:
    """
    Format a notification message for a given event type.

    Loads the NotificationTemplate from DB and renders with context.
    Falls back to a simple generic format if no template exists.
    """
    try:
        from apps.telegram.models import NotificationTemplate

        tpl = NotificationTemplate.objects.filter(event_key=event_key).first()
        if tpl:
            return tpl.render(**kwargs)
    except Exception as e:
        logger.warning("Failed to load template for %s: %s", event_key, e)

    # Fallback: simple generic message
    title = kwargs.get("action_name") or event_key
    ga_name = kwargs.get("ga_name", "")
    body = f"Conta: {ga_name}" if ga_name else ""
    lines = [f"🔔 <b>{title}</b>"]
    if body:
        lines.append(body)
    return "\n".join(lines)
