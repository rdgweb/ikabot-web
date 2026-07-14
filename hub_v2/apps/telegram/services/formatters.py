"""
Message formatters for Telegram notifications.

Uses customizable templates stored in NotificationTemplate model.
Templates have separate title_template and body_template fields
with variables like {action_name}, {ga_name}, {server_id}, etc.

Supported HTML tags: <b>, <i>, <u>, <s>, <code>, <pre>, <a href="">
"""

import html
import logging

logger = logging.getLogger(__name__)


def _unit_name(unit_id: str) -> str:
    try:
        from core.catalogs import UNIT_CATALOG
        info = UNIT_CATALOG.get(f"s{unit_id}") or UNIT_CATALOG.get(str(unit_id)) or {}
        return str(info.get("name") or f"u{unit_id}")
    except Exception:
        return f"u{unit_id}"


def _format_combat_report(kwargs: dict) -> str:
    def _e(v):
        return html.escape(str(v)) if v is not None else ""

    result = str(kwargs.get("result") or "")
    won = result == "victory"
    icon = "\U0001f6e1️" if won else "⚔️"
    head = "Vitoria" if won else "Derrota"
    city = _e(kwargs.get("city_name"))
    owner = _e(kwargs.get("owner_name"))
    rounds = _e(kwargs.get("total_rounds") or 0)
    date = _e(kwargs.get("date"))

    lines = [f"{icon} <b>Relatorio de combate — {head}</b>"]
    meta = " | ".join(p for p in [f"Cidade: {city}" if city else "", f"vs {owner}" if owner else "", f"{rounds} round(s)"] if p)
    if meta:
        lines.append(meta)
    if date:
        lines.append(f"<i>{date}</i>")

    def _losses_block(title: str, losses: dict) -> list:
        if not isinstance(losses, dict) or not losses:
            return []
        rows = sorted(losses.items(), key=lambda kv: -int(kv[1] or 0))
        body = [f"<b>{title}</b>"]
        for uid, qty in rows:
            if int(qty or 0) <= 0:
                continue
            body.append(f"  {_e(_unit_name(uid))}: <code>-{int(qty)}</code>")
        return body if len(body) > 1 else []

    atk = _losses_block("Perdas do atacante", kwargs.get("attacker_losses") or {})
    dfn = _losses_block("Perdas do defensor", kwargs.get("defender_losses") or {})
    if atk:
        lines.append("")
        lines.extend(atk)
    if dfn:
        lines.append("")
        lines.extend(dfn)
    if not atk and not dfn:
        lines.append("Sem perdas registradas.")
    return "\n".join(lines)


def format_message(event_key: str, **kwargs) -> str:
    """
    Format a notification message for a given event type.

    Loads the NotificationTemplate from DB and renders with context.
    Falls back to a richer generic format if no template exists.
    """
    if event_key == "combat_report":
        return _format_combat_report(kwargs)

    try:
        from apps.telegram.models import NotificationTemplate

        tpl = NotificationTemplate.objects.filter(event_key=event_key).first()
        if tpl:
            return tpl.render(**kwargs)
    except Exception as exc:
        logger.warning("Failed to load template for %s: %s", event_key, exc)

    def _e(v): return html.escape(str(v)) if v else ""

    action_name = _e(kwargs.get("action_name") or event_key)
    ga_name = _e(kwargs.get("ga_name") or "")
    server_id = _e(kwargs.get("server_id") or "")
    account_name = _e(kwargs.get("account_name") or "")
    node_name = _e(kwargs.get("node_name") or "")
    body = _e(kwargs.get("body") or "")
    status = _e(kwargs.get("status") or "")
    exit_code = _e(kwargs.get("exit_code") or "")
    error = _e(kwargs.get("error") or "")
    job_id = _e(kwargs.get("job_id") or "")

    title_map = {
        "attack_alert": ("Alerta de ataque", "\u26a0\ufe0f"),
        "job_failed": (f"{action_name} falhou", "\u274c"),
        "job_done": (f"{action_name} concluido", "\u2705"),
        "build_complete": ("Construcao concluida", "\U0001f3d7\ufe0f"),
        "research_complete": ("Pesquisa concluida", "\U0001f4a1"),
        "low_wine": ("Vinho baixo", "\U0001f377"),
        "daily_summary": ("Resumo diario", "\U0001f4ca"),
        "diplomacy_message": ("Mensagem de diplomacia", "\U0001f4e8"),
        "cinema_available": ("Cineteatro disponivel", "\U0001f3ac"),
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
