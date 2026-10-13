"""Notices that go out once for all the accounts together (N-104).

Cinema ("Cineteatro disponível") was more than half of everything the bot sent: one
message per account per day. Rodrigo asked for a single message a day with every
account that has a session, an hour before the game day turns.

Each daily-login run of an account reports the state of its cinema (record_cinema);
flush_due(), called from the agents' heartbeat, sends what is due.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.utils import timezone

logger = logging.getLogger("telegram.digests")

CINEMA = "cinema_available"
LEAD = timedelta(hours=1)                 # how long before the game day turns the digest goes out
FALLBACK_DUE_HOUR_UTC = 23                # when no account reported the reset time


def record_cinema(game_account, *, available: bool = True, reset_in_seconds: int = 0, city_name: str = "") -> None:
    """Keep the latest cinema state of an account for the current game day."""
    from apps.telegram.models import DigestEntry

    now = timezone.now()
    reset_at = now + timedelta(seconds=reset_in_seconds) if reset_in_seconds and reset_in_seconds > 0 else None
    day_key = (reset_at or now).astimezone(dt_timezone.utc).date().isoformat()
    entry, _created = DigestEntry.objects.get_or_create(
        event_key=CINEMA, game_account=game_account, day_key=day_key,
        defaults={"active": bool(available)},
    )
    entry.active = bool(available)
    if reset_at is not None:
        entry.reset_at = reset_at
        entry.due_at = reset_at - LEAD
    entry.data_json = json.dumps({"city_name": str(city_name or "")})
    entry.save(update_fields=["active", "reset_at", "due_at", "data_json", "updated_at"])


def _due_at(entries, day_key: str):
    """When the digest of a game day goes out: an hour before the reset the accounts reported."""
    known = [entry.due_at for entry in entries if entry.due_at is not None]
    if known:
        return min(known)
    year, month, day = (int(part) for part in day_key.split("-"))
    return datetime(year, month, day, FALLBACK_DUE_HOUR_UTC, tzinfo=dt_timezone.utc)


def _human(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}min"
    return f"{hours}h" if hours else f"{minutes} min"


def flush_due(now=None) -> int:
    """Send the digests that are due. Returns how many messages went out."""
    from apps.telegram.models import DigestEntry, TelegramBotConfig
    from apps.telegram.services.bot_api import send_message
    from apps.telegram.services.formatters import format_message
    from apps.telegram.services.notifications import _create_audit, _resolve_destination

    now = now or timezone.now()
    pending = list(
        DigestEntry.objects.filter(event_key=CINEMA, sent_at__isnull=True, created_at__gte=now - timedelta(days=3))
        .select_related("game_account__account")
    )
    if not pending:
        return 0
    by_day: dict[str, list] = {}
    for entry in pending:
        by_day.setdefault(entry.day_key, []).append(entry)

    sent_messages = 0
    for day_key, entries in by_day.items():
        if _due_at(entries, day_key) > now:
            continue
        ids = [entry.pk for entry in entries]
        # claim first: two heartbeats arriving together must not send the same digest twice
        claimed = DigestEntry.objects.filter(pk__in=ids, sent_at__isnull=True).update(sent_at=now)
        if claimed != len(ids):
            continue
        # the digest of this game day went out already: what arrives later is not sent on its own
        if DigestEntry.objects.filter(event_key=CINEMA, day_key=day_key, sent_at__lt=now).exclude(pk__in=ids).exists():
            continue
        bot = TelegramBotConfig.objects.filter(pk=1).first()
        if bot is None or not bot.is_active:
            continue
        by_chat: dict[str, list] = {}
        for entry in entries:
            if not entry.active:
                continue
            chat_id, _source = _resolve_destination(bot, entry.game_account, CINEMA)
            if chat_id:
                by_chat.setdefault(chat_id, []).append(entry)
        reset_at = next((entry.reset_at for entry in entries if entry.reset_at), None)
        for chat_id, listed in by_chat.items():
            listed.sort(key=lambda entry: (entry.game_account.name or "").lower())
            lines = []
            for entry in listed:
                city = (json.loads(entry.data_json or "{}") or {}).get("city_name") or ""
                name = entry.game_account.name or entry.game_account.server_id
                lines.append(f"• {name}" + (f" — {city}" if city else ""))
            context = {
                "count": len(listed),
                "accounts": "\n".join(lines),
                "reset_time": timezone.localtime(reset_at).strftime("%H:%M") if reset_at else "",
                "reset_in": _human(reset_at - now) if reset_at and reset_at > now else "",
                "ga_name": "", "server_id": "", "account_name": "", "body": "", "title": "",
            }
            text = format_message(CINEMA, **context)
            result = send_message(chat_id, text)
            ok = bool(result.get("ok"))
            sent_messages += int(ok)
            _create_audit(
                event_key=CINEMA, title=f"Cineteatro disponível em {len(listed)} conta(s)", body="\n".join(lines),
                status="sent" if ok else "failed", error="" if ok else str(result.get("description", "Unknown error")),
                external_ref=str(result.get("result", {}).get("message_id", "")) if ok else "", context=context,
            )
            if not ok:
                logger.warning("Cinema digest failed: chat=%s error=%s", chat_id, result.get("description"))
    return sent_messages


ISLAND_LINES = {
    "new": "➕ {owner}{ally} colonizou {city}",
    "removed": "➖ {city} sumiu (era de {owner})",
    "owner_change": "🔄 {city}: {prev_owner} → {owner}{ally}",
    "fight_start": "⚔️ Batalha em {city} (de {owner})",
    "fight_end": "🏳 Batalha encerrada em {city}",
}


def island_changes_text(changes) -> tuple[str, int]:
    """(one line per change, how many) from what the island monitor reports."""
    lines = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        pattern = ISLAND_LINES.get(str(change.get("kind") or ""))
        if not pattern:
            continue
        ally = f" [{change['ally']}]" if change.get("ally") else ""
        lines.append(pattern.format(
            owner=change.get("owner") or "?", prev_owner=change.get("prev_owner") or "?",
            city=change.get("city") or "?", ally=ally,
        ))
    return "\n".join(lines), len(lines)


def island_context(context: dict) -> dict:
    """The variables of the island notice, whatever the sender gave.

    An agent with N-104 sends the changes as data. An older one, and a message sent again
    from the history, have only the ready-made text: its first line is the island, the
    others are the changes (the Markdown stars it carried show as plain stars in Telegram).
    """
    import re

    context = dict(context)
    changes = context.get("changes")
    if isinstance(changes, (list, tuple)):
        context["changes"], context["changes_count"] = island_changes_text(changes)
    if not context.get("changes"):
        lines = [line.replace("*", "").strip() for line in str(context.get("body") or "").splitlines() if line.strip()]
        if len(lines) > 1:
            context.setdefault("island", lines[0].lstrip("🏝").strip())
            lines = lines[1:]
        context["changes"] = "\n".join(lines)
        context["changes_count"] = len(lines)
    if not context.get("coords"):
        found = re.search(r"\[\d+:\d+\]", f"{context.get('title') or ''} {context.get('island') or ''}")
        context["coords"] = found.group(0) if found else ""
    for name in ("island", "resource", "miracle"):
        context.setdefault(name, "")
    # the island with what is known about it, without loose separators when a part is missing
    context["island_line"] = " · ".join(part for part in (context["island"], context["resource"], context["miracle"]) if part)
    return context
