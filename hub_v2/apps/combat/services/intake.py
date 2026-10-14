"""What the hub does when a combat report arrives (N-109).

The agent only delivers what the game shows: the rounds it had not sent yet and the summary
page. Here the report is put together, read, and the hub decides what is worth telling: the
newest round while the combat goes on, the outcome when it ends. Each is told once, whoever
delivered the report (the attack check, the combat monitor or a raid).
"""
from __future__ import annotations

import logging
from datetime import datetime

from django.utils import timezone

from .report_parser import ROUND_SEPARATOR, parse_round, parse_rounds, parse_summary, round_losses, split_rounds

logger = logging.getLogger(__name__)


def merge_rounds(existing: str, incoming: str) -> str:
    """The rounds already kept plus the ones that arrived, one page per round, in order."""
    pages: dict[int, str] = {}
    for html in [*split_rounds(existing), *split_rounds(incoming)]:
        parsed = parse_round(html)
        if parsed:
            pages[parsed["round"]] = html           # the same round delivered again replaces the old page
    return f"\n{ROUND_SEPARATOR}\n".join(pages[number] for number in sorted(pages))


def _own_side(names: str, own: str) -> bool:
    own = (own or "").strip().lower()
    return bool(own) and any(part.strip().lower().split("[")[0] == own for part in (names or "").split(","))


def read_into(report, *, finished=None) -> list:
    """Fill the fields of the report that come from its pages. Returns the rounds read."""
    rounds = parse_rounds(report.detailed_html)
    summary = parse_summary(report.summary_html)
    last = rounds[-1] if rounds else None

    report.attacker_name = ((summary or {}).get("attacker", {}).get("name") or (last or {}).get("attacker", {}).get("name") or report.attacker_name or "")[:160]
    report.defender_name = ((summary or {}).get("defender", {}).get("name") or (last or {}).get("defender", {}).get("name") or report.defender_name or "")[:160]
    report.battle_title = ((last or {}).get("title") or (summary or {}).get("title") or report.battle_title or "")[:160]
    if last:
        report.total_rounds = max(last["round"], (summary or {}).get("rounds") or 0)
        report.combat_type = "naval" if last.get("naval") else "land"
    if not report.combat_date:
        # the game writes its own clock; kept as the hub's local time, like the dates the agent sends
        raw = (summary or {}).get("date") or (last or {}).get("date") or ""
        try:
            report.combat_date = timezone.make_aware(datetime.strptime(raw, "%d.%m.%Y %H:%M:%S"))
        except ValueError:
            pass
    # the summary names the winners only when the combat is over
    report.finished = bool(finished) if finished is not None else bool(summary and summary.get("winners"))

    if summary and summary.get("units"):
        report.attacker_losses = {str(item["unit"]): item["attacker"]["lost"] for item in summary["units"] if item["attacker"] and item["attacker"]["lost"]}
        report.defender_losses = {str(item["unit"]): item["defender"]["lost"] for item in summary["units"] if item["defender"] and item["defender"]["lost"]}
    elif rounds:
        for side, field in (("attacker", "attacker_losses"), ("defender", "defender_losses")):
            total: dict[str, int] = {}
            for item in rounds:
                for unit, lost in round_losses(item, side).items():
                    total[str(unit)] = total.get(str(unit), 0) + lost
            setattr(report, field, total)
    if summary and summary.get("loot"):
        report.loot_json = summary["loot"]
        report.total_loot = sum(summary["loot"].values())
    if report.finished and not report.result and summary and report.game_account_id:
        own = report.game_account.name
        if _own_side(summary.get("winners"), own):
            report.result = "victory"
        elif _own_side(summary.get("losers"), own):
            report.result = "defeat"
    return rounds


def take_in(report, *, silent: bool = False, finished=None) -> str:
    """Read a report that was just saved and tell what is new in it. Returns "round", "final" or "".

    `silent` is for old combats delivered to fill the history: kept, marked as told, not sent.
    """
    try:
        rounds = read_into(report, finished=finished)
    except Exception:  # noqa: BLE001 - a page the game wrote differently must not lose the report
        logger.warning("Could not read the combat report %s", report.combat_id, exc_info=True)
        return ""
    latest = rounds[-1]["round"] if rounds else 0

    kind = ""
    if not silent:
        if report.finished and not report.notified_final:
            kind = "final"                           # rounds not told yet are not told one by one after the end
        elif not report.finished and latest > report.notified_round:
            kind = "round"
    # marked before sending: a second delivery arriving meanwhile must not tell it again
    report.notified_round = max(report.notified_round, latest)
    if report.finished:
        report.notified_final = True
    report.save()

    if report.finished:
        # N-110: the combat is over - fight it again in the simulator and keep the comparison
        from .checks import check_safely

        check_safely(report)

    if kind and report.game_account_id:
        try:
            from apps.telegram.services.notifications import notify

            notify(
                event_key="combat_report",
                game_account=report.game_account,
                account=report.game_account.account,
                title="Combate — final" if kind == "final" else "Combate — round",
                phase=kind,
                combat_id=report.combat_id,
                result=report.result,
                city_name=report.target_city_name,
                owner_name=report.target_owner,
                round=latest,
            )
        except Exception:  # noqa: BLE001 - the report is kept even when telling about it fails
            logger.warning("Could not notify the combat %s", report.combat_id, exc_info=True)
    return kind
