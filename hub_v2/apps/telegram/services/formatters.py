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
    Falls back to a richer generic format if no template exists.
    """
    try:
        from apps.telegram.models import NotificationTemplate

        tpl = NotificationTemplate.objects.filter(event_key=event_key).first()
        if tpl:
            return tpl.render(**kwargs)
    except Exception as exc:
        logger.warning("Failed to load template for %s: %s", event_key, exc)

    action_name = str(kwargs.get("action_name") or event_key)
    ga_name = str(kwargs.get("ga_name") or "")
    server_id = str(kwargs.get("server_id") or "")
    account_name = str(kwargs.get("account_name") or "")
    node_name = str(kwargs.get("node_name") or "")
    body = str(kwargs.get("body") or "")
    status = str(kwargs.get("status") or "")
    exit_code = str(kwargs.get("exit_code") or "")
    error = str(kwargs.get("error") or "")
    job_id = str(kwargs.get("job_id") or "")

    title_map = {
        "attack_alert": ("Alerta de ataque", "\u26a0\ufe0f"),
        "job_failed": (f"{action_name} falhou", "\u274c"),
        "job_done": (f"{action_name} concluido", "\u2705"),
        "build_complete": ("Construcao concluida", "\U0001f3d7\ufe0f"),
        "research_complete": ("Pesquisa concluida", "\U0001f4a1"),
        "low_wine": ("Vinho baixo", "\U0001f377"),
        "daily_summary": ("Resumo diario", "\U0001f4ca"),
        "diplomacy_message": ("Mensagem de diplomacia", "\U0001f4e8"),
    }
    title, icon = title_map.get(event_key, (action_name, "\U0001f514"))

    lines = [f"{icon} <b>{title}</b>"]
    meta_line = " | ".join(part for part in [ga_name or account_name, server_id, node_name] if part)
    if meta_line:
        lines.append(meta_line)
    if body:
        lines.extend(line for line in body.splitlines() if line.strip())
    if status:
        lines.append(f"Status: <code>{status}</code>")
    if exit_code:
        lines.append(f"Exit: <code>{exit_code}</code>")
    if error:
        lines.append(f"Erro: <code>{error}</code>")
    if job_id:
        lines.append(f"Job: <code>{job_id}</code>")
    return "\n".join(lines)
