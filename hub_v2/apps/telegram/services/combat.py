"""The combat notice (N-109): its variables and the picture of the battlefield.

The agent says which combat and which moment (a round, or the end). Everything else is read
from the report of the game the hub keeps for that combat; when the hub has none, the notice
still goes out with what the agent sent, without a picture.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

NO_LOSSES = "sem perdas"


def _int(raw) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _number(value) -> str:
    return f"{_int(value):,}".replace(",", ".")


def _losses_text(losses) -> str:
    """ "Hoplita -30, Morteiro -2", the biggest loss first."""
    from apps.combat.services.report_parser import unit_name

    if not isinstance(losses, dict):
        return ""
    rows = sorted(((_int(amount), unit) for unit, amount in losses.items() if _int(amount) > 0), key=lambda row: -row[0])
    return ", ".join(f"{unit_name(unit)} -{_number(amount)}" for amount, unit in rows)


def _stored(combat_id):
    """(rounds, summary) read from the report kept for this combat, or ([], None)."""
    from apps.combat.models import CombatReport
    from apps.combat.services.report_parser import parse_rounds, parse_summary

    if not _int(combat_id):
        return [], None
    report = CombatReport.objects.filter(combat_id=_int(combat_id)).first()
    if report is None:
        return [], None
    try:
        return parse_rounds(report.detailed_html), parse_summary(report.summary_html)
    except Exception:  # noqa: BLE001 - a report the game wrote differently must not stop the notice
        logger.warning("Could not read the combat report %s", combat_id, exc_info=True)
        return [], None


def _round_of(rounds: list, number: int):
    if not rounds:
        return None
    return next((item for item in rounds if item["round"] == number), None) or rounds[-1]


def combat_context(context: dict) -> dict:
    """The variables of the combat text. Safe to call on a context that already has them."""
    from apps.combat.services.report_parser import RESOURCE_LABELS, round_losses

    context = dict(context or {})
    if context.get("headline"):
        return context
    final = str(context.get("phase") or "final") != "round"
    rounds, summary = _stored(context.get("combat_id"))
    current = _round_of(rounds, _int(context.get("round"))) if not final else (rounds[-1] if rounds else None)

    total = _int(context.get("total_rounds")) or (current or {}).get("total_rounds") or len(rounds)
    number = _int(context.get("round")) or (current or {}).get("round") or 0
    result = str(context.get("result") or "")
    result_label = {"victory": "Vitória", "defeat": "Derrota"}.get(result, "")
    if final:
        headline = result_label or "Combate encerrado"
        if total:
            headline += f" em {total} round" + ("s" if total != 1 else "")
    else:
        headline = f"Round {number}"            # how many there will be is only known at the end

    if final and summary:
        attacker = {item["unit"]: item["attacker"]["lost"] for item in summary["units"] if item["attacker"]}
        defender = {item["unit"]: item["defender"]["lost"] for item in summary["units"] if item["defender"]}
    elif current:
        attacker, defender = round_losses(current, "attacker"), round_losses(current, "defender")
    else:
        attacker, defender = context.get("attacker_losses"), context.get("defender_losses")
    attacker_text, defender_text = _losses_text(attacker) or NO_LOSSES, _losses_text(defender) or NO_LOSSES

    names = {"attacker": "", "defender": ""}
    for side in names:
        names[side] = (summary or {}).get(side, {}).get("name") or (current or {}).get(side, {}).get("name") or ""
    title = (current or {}).get("title") or (summary or {}).get("title") or ""
    city = str(context.get("city_name") or "") or title.replace("Batalha por", "").strip()
    loot = (summary or {}).get("loot") or {}
    loot_text = " · ".join(f"{_number(amount)} {RESOURCE_LABELS.get(key, key).lower()}" for key, amount in loot.items()) if final else ""

    context.update({
        "phase": "final" if final else "round",
        "headline": headline,
        "city": city,
        "attacker": names["attacker"],
        "defender": names["defender"] or str(context.get("owner_name") or ""),
        "result": result_label,
        "round": number,
        "total_rounds": total,
        "attacker_losses": attacker_text,
        "defender_losses": defender_text,
        "losses": f"Atacante: {attacker_text}\nDefensor: {defender_text}",
        "loot": loot_text,
        "loot_line": f"Saque: {loot_text}" if loot_text else "",
        "winners": (summary or {}).get("winners", "") if final else "",
        "date": (current or {}).get("date") or (summary or {}).get("date") or str(context.get("date") or ""),
    })
    # both names are only known from the report; half a line ("? × Fulano") says less than none
    context["versus"] = f"{context['attacker']} × {context['defender']}" if context["attacker"] and context["defender"] else ""
    return context


def combat_picture(context: dict, game_account=None) -> bytes | None:
    """PNG of the battlefield this notice talks about; None when the hub has no report of it."""
    rounds, summary = _stored((context or {}).get("combat_id"))
    if not rounds:
        return None
    final = str(context.get("phase") or "final") != "round"
    current = rounds[-1] if final else _round_of(rounds, _int(context.get("round")))
    try:
        from apps.combat.services.battle_image import render_battle

        return render_battle(current, summary if final else None, own_name=getattr(game_account, "name", "") or "")
    except Exception:  # noqa: BLE001 - the text still goes out when the picture cannot be drawn
        logger.warning("Could not draw the combat %s", context.get("combat_id"), exc_info=True)
        return None


def sample_combat():
    """(context, game account) of the latest stored combat, for the preview and the test; (None, None) without one."""
    from apps.combat.models import CombatReport

    report = CombatReport.objects.exclude(detailed_html="").select_related("game_account").order_by("-combat_date", "-created_at").first()
    if report is None:
        return None, None
    context = {
        "phase": "final", "combat_id": report.combat_id, "result": report.result,
        "city_name": report.target_city_name, "owner_name": report.target_owner, "total_rounds": report.total_rounds,
        "ga_name": getattr(report.game_account, "name", "") or "", "server_id": str(getattr(report.game_account, "server_id", "") or ""),
    }
    return context, report.game_account
