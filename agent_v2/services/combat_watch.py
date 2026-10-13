"""Watching the combat reports of an account (N-109).

One look at the game's list of combats. A combat that is new there, or that gained rounds
since the last look, has its new rounds and its summary page delivered to the hub. The hub
keeps the report, draws it, and decides what to tell on Telegram: the agent tells nothing.

The state is small and lives with the job that watches: for each listed combat, how many
rounds were delivered and whether it is still open. The first time an account is watched,
the newest combats its list already has are delivered as history, flagged so that the hub
keeps them without telling.
"""
from __future__ import annotations

import calendar
import re
import time
from typing import Any, Callable


IMPORT_LIMIT = 5            # combats per account brought in as history (each round of each is a request to the game)
ROUND_SECONDS = 15 * 60     # the game fights one round every 15 minutes
# missions of one's own that end in a fight when they arrive (the class the game gives the movement)
ATTACK_MISSIONS = ("plunder", "occupy", "blockade", "attack", "defend")


def round_epoch(html: str, now: float) -> int | None:
    """When the round of this page was fought, as a clock time of ours.

    The page carries the game's own clock ("13.10.2026 3:04:49"), a whole number of hours
    away from ours. A round that was just read is minutes old, so the difference to now,
    rounded to the hour, is that distance. None when the date is not there or does not fit.
    """
    found = re.search(
        r"Round\s*<br\s*/?>\s*\d+\s*/\s*\d+\s*</span>\s*(?:<br\s*/?>)?\s*\(\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s*\)",
        html or "",
    )
    if not found:
        return None
    day, month, year, hour, minute, second = (int(part) for part in found.groups())
    try:
        local = calendar.timegm((year, month, day, hour, minute, second))
    except (ValueError, OverflowError):
        return None
    epoch = local - round((local - now) / 3600) * 3600
    return int(epoch) if -120 <= now - epoch <= 1800 else None


def next_own_attack_arrival(movements: list, time_now: int) -> int | None:
    """Seconds until the first of one's own attacks on its way lands; None when there is none."""
    best = None
    for movement in movements or []:
        if not isinstance(movement, dict) or not movement.get("isOwnArmyOrFleet"):
            continue
        event = movement.get("event") or {}
        if event.get("isReturning") or event.get("isFleetReturning"):
            continue
        mission = str(event.get("missionIconClass") or "").lower()
        troops = _to_int((movement.get("army") or {}).get("amount"), 0)
        if troops <= 0 and not any(kind in mission for kind in ATTACK_MISSIONS):
            continue                            # a transport, a trade ship
        left = _to_int(movement.get("eventTime"), 0) - _to_int(time_now, 0)
        if left > 0 and (best is None or left < best):
            best = left
    return best


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


def watch_combats(
    client,
    hub,
    *,
    game_account_id: str,
    city_id: int | str,
    state: dict | None,
    log: Callable[[str, str], None] | None = None,
    limit: int = 20,
    now: float | None = None,
) -> dict:
    """Returns {"state": new state, "delivered": reports sent to the hub, "open": combats still going on,
    "next_round_in": seconds until the next round of an open combat, when the game's clock tells}."""
    say = log or (lambda _level, _message: None)
    now = float(now or time.time())
    state = dict(state or {})
    seen = {str(key): dict(value) for key, value in (state.get("seen") or {}).items() if isinstance(value, dict)}
    reports = client.fetch_combat_reports(int(city_id), limit=limit) or []
    # once per account, what the list already has goes to the hub as history: kept, not told
    importing = not state.get("imported")
    imported = 0
    delivered = 0
    still_open = 0

    for rep in reports:
        combat_id = str(rep.get("combat_id") or "")
        if not combat_id:
            continue
        listed = _to_int(rep.get("rounds"), 0)
        known = seen.get(combat_id)
        silent = importing and not (known and known.get("sent"))
        if silent:
            # only a few of the newest: each one is brought whole, however many rounds it had
            if imported >= IMPORT_LIMIT:
                seen.setdefault(combat_id, {"rounds": listed, "open": False})
                continue
            imported += 1
        elif known and not known.get("open") and listed <= _to_int(known.get("rounds"), 0):
            continue
        # a combat that was only noted, never delivered, goes whole when it moves
        upto = _to_int(known.get("rounds"), 0) if known and known.get("sent") and not silent else 0
        try:
            detail = client.fetch_combat_detailed_report(int(city_id), int(combat_id), known_upto=upto) or {}
        except Exception as exc:
            say("warn", f"Combate {combat_id}: o relatório detalhado falhou ({exc}); fica para a próxima verificação.")
            if known and known.get("open"):
                still_open += 1
            continue
        new_rounds = [item for item in (detail.get("rounds") or []) if _to_int(item.get("round"), 0) > upto]
        if not new_rounds:
            if silent:
                seen.setdefault(combat_id, {"rounds": listed, "open": False})
            elif known is None:
                # listed, but the game shows no field of it yet
                seen[combat_id] = {"rounds": 0, "open": True}
                still_open += 1
            else:
                known["rounds"] = max(_to_int(known.get("rounds"), 0), listed if not known.get("open") else 0)
                still_open += 1 if known.get("open") else 0
            continue

        summary: dict = {}
        try:
            summary = client.fetch_combat_report_detail(int(city_id), int(combat_id)) or {}
        except Exception as exc:
            say("warn", f"Combate {combat_id}: o resumo falhou ({exc}); os rounds seguem sem ele.")
        payload = {
            "combat_id": int(combat_id),
            "result": rep.get("result") or "",
            "target_city_id": str(rep.get("city_id_target") or ""),
            "target_city_name": rep.get("city_name") or "",
            "target_owner": rep.get("owner_name") or "",
            "detailed_html": "\n<!-- ROUND SEPARATOR -->\n".join(str(item.get("html") or "") for item in new_rounds),
            "append_rounds": True,
        }
        if summary.get("html"):
            payload["summary_html"] = summary["html"]
            payload["combat_date"] = summary.get("date") or ""
        if silent:
            payload["silent"] = True
        try:
            response = hub.save_combat_report(game_account_id, payload) or {}
        except Exception as exc:
            say("warn", f"Combate {combat_id}: o hub não guardou o relatório ({exc}); será entregue de novo.")
            if silent:
                seen.setdefault(combat_id, {"rounds": listed, "open": False})
            elif known and known.get("open"):
                still_open += 1
            continue
        finished = bool(response.get("finished", True))
        top = max(_to_int(item.get("round"), 0) for item in new_rounds)
        seen[combat_id] = {"rounds": max(upto, top, listed if finished else 0), "open": not finished, "sent": True}
        if not finished:
            # the last round carries its time: the next one is fought 15 minutes later
            newest = max(new_rounds, key=lambda item: _to_int(item.get("round"), 0))
            fought_at = round_epoch(str(newest.get("html") or ""), now)
            if fought_at:
                seen[combat_id]["next_round_at"] = fought_at + ROUND_SECONDS
        delivered += 0 if silent else 1
        still_open += 0 if finished else 1
        if silent:
            say("info", f"Combate {combat_id} ({rep.get('city_name') or '?'}): {len(new_rounds)} round(s) levado(s) ao hub como histórico, sem aviso.")
        else:
            say("info", f"Combate {combat_id} ({rep.get('city_name') or '?'}): {len(new_rounds)} round(s) novo(s) entregue(s) ao hub"
                        f"{' — encerrado' if finished else ' — em andamento'}.")

    listed_ids = {str(rep.get("combat_id") or "") for rep in reports}
    if reports:                                 # an empty list is more likely a page that failed than a clean slate
        seen = {combat_id: value for combat_id, value in seen.items() if combat_id in listed_ids}
    # A list that came back empty on the very first look is tried once more (it may have been a
    # page that failed). After that an empty list is just an account without combats: its
    # history is taken as brought, so that the first combat it ever has is told, not filed.
    done = bool(state.get("imported")) or bool(reports) or bool(state.get("started"))
    waits = [_to_int(value.get("next_round_at"), 0) - now for value in seen.values() if value.get("open") and value.get("next_round_at")]
    ahead = [wait for wait in waits if wait > 0]
    return {
        "state": {"started": True, "imported": done, "seen": seen},
        "delivered": delivered, "imported": imported, "open": still_open,
        "next_round_in": int(min(ahead)) if ahead else None,
    }
