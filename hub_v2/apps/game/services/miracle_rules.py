"""Miracles at the right moment (N-141, phase 2).

The organization switches each rule on or off in Templos e Fé (defaults: all on, as agreed with the owner):
A miracle works for every city of the account that activates it ("+80% ... em todos os armazéns", "... em suas
cidades"), so any of the account's temples will do:
- defense_on_attack: an attack is coming to one of our cities -> Templo de Atena (+80% safe resources) and
  Forja de Hefesto (+armour, +damage), when available.
- colossus_when_losing: a combat in one of our cities where we defend and have lost more units than the attacker
  so far -> Colosso (scatters part of the enemy troops).
- speed_on_shipments: a shipment using at least `ship_share`% of the account's cargo ships -> Templo de Poseidon or
  Templo de Hermes (applied by the agent, which knows the ships of each shipment).
A miracle is activated through the usual Ativar Milagre job (ac=11), never twice within the same hour.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULTS = {"defense_on_attack": True, "colossus_when_losing": True, "speed_on_shipments": True, "ship_share": 50,
            "min_ships": 10}
DEFENSE_MIRACLES = ("Templo de Atena", "Forja de Hefesto")
COLOSSUS = ("Colosso",)
SPEED_MIRACLES = ("Templo de Poseidon", "Templo de Hermes")
SHORT_NAMES = {"Templo de Atena": "Atena", "Forja de Hefesto": "Hefesto", "Colosso": "Colosso",
               "Templo de Poseidon": "Poseidon", "Templo de Hermes": "Hermes"}


def rules_for(organization) -> dict:
    from apps.orgs.models import OrganizationPreferences

    rules = dict(DEFAULTS)
    prefs = OrganizationPreferences.objects.filter(organization=organization).first() if organization else None
    if prefs and isinstance(prefs.miracle_rules, dict) and prefs.miracle_rules:
        rules.update({k: v for k, v in prefs.miracle_rules.items() if k in DEFAULTS})
    return rules



def save_rules(organization, data) -> dict:
    from apps.orgs.services import preferences_of

    try:
        share = max(10, min(100, int(data.get("ship_share") or DEFAULTS["ship_share"])))
    except (TypeError, ValueError):
        share = DEFAULTS["ship_share"]
    try:
        min_ships = max(1, min(500, int(data.get("min_ships") or DEFAULTS["min_ships"])))
    except (TypeError, ValueError):
        min_ships = DEFAULTS["min_ships"]
    rules = {
        "min_ships": min_ships,
        "defense_on_attack": bool(data.get("defense_on_attack")),
        "colossus_when_losing": bool(data.get("colossus_when_losing")),
        "speed_on_shipments": bool(data.get("speed_on_shipments")),
        "ship_share": share,
    }
    prefs = preferences_of(organization)
    prefs.miracle_rules = {**(prefs.miracle_rules or {}), **rules}
    prefs.save(update_fields=["miracle_rules", "updated_at"])
    return rules


def _organization_of(game_account):
    return getattr(game_account.account, "organization", None)


def available_miracles(game_account) -> list[dict]:
    """The miracles the account can activate now (last temple reading), one city per miracle."""
    from apps.game.models import AccountSnapshot

    snapshot = AccountSnapshot.objects.filter(game_account=game_account).first()
    cities = snapshot.cities if snapshot and isinstance(snapshot.cities, list) else []
    found = []
    for city in cities:
        if not isinstance(city, dict):
            continue
        state = city.get("miracle_state") or {}
        if str(state.get("activate_state") or "").lower() != "enabled":
            continue
        name = str(state.get("wonder_name") or "").split(" (")[0].strip()
        if name and all(m["name"] != name for m in found):
            found.append({"name": name, "city_id": str(city.get("id")), "city_name": str(city.get("name") or "")})
    return found


def activate(game_account, city_id, *, reason: str, trigger: str = "miracle_rule") -> bool:
    """Ativar Milagre (ac=11) for that city, unless one was asked for it within the last hour."""
    from apps.jobs.models import Job
    from apps.jobs.services.workflows import create_job_with_workflow

    recent = timezone.now() - timedelta(hours=1)
    for job in Job.objects.filter(game_account=game_account, action_code=11, created_at__gte=recent).only("inputs_json"):
        try:
            asked = json.loads(job.inputs_json or "{}")
        except ValueError:
            asked = {}
        if str(asked.get("city") or asked.get("city_id") or "") == str(city_id):
            return False
    create_job_with_workflow(
        account=game_account.account, game_account=game_account, node=game_account.account.node, action_code=11,
        inputs={"city": str(city_id), "reason": reason}, status="queued", trigger_type=trigger,
    )
    logger.info("Miracle rule: Ativar Milagre in city %s of %s (%s)", city_id, game_account.pk, reason)
    return True


def on_attack_alert(game_account, metadata: dict) -> list[str]:
    """An attack is coming: Atena and Hefesto of the attacked city's island. Returns the miracles asked for."""
    from apps.game.services.attacks import is_pirate

    if is_pirate(metadata) or not rules_for(_organization_of(game_account)).get("defense_on_attack"):
        return []                               # N-149: no miracle helps against a pirate raid
    city_id = str((metadata or {}).get("target_city_id") or "")
    if not city_id:
        return []
    asked = []
    for miracle in available_miracles(game_account):
        if miracle["name"] in DEFENSE_MIRACLES:
            if activate(game_account, miracle["city_id"], reason=f"ataque chegando em {metadata.get('target_city') or city_id}"):
                asked.append(miracle["name"])
    return asked


def defense_buttons(game_account, asked: list[str]) -> list[dict]:
    """Telegram buttons for the defense miracles the account can activate (the ones already asked say so)."""
    buttons = []
    for miracle in available_miracles(game_account):
        if miracle["name"] not in DEFENSE_MIRACLES + COLOSSUS:
            continue
        label = SHORT_NAMES.get(miracle["name"], miracle["name"])
        buttons.append({"text": f"✨ {label}{' (pedido)' if miracle['name'] in asked else ''}",
                        "callback_data": f"miracle:{miracle['city_id']}:{game_account.pk}"})
    return buttons


def activate_from_telegram(ga_id: str, city_id: str) -> tuple[bool, str]:
    """A miracle button pressed on Telegram (the chat's organization scope is active)."""
    from apps.accounts.models import GameAccount

    ga = GameAccount.objects.select_related("account__node").filter(pk=ga_id).first() if ga_id else None
    if ga is None or ga.account.node is None:
        return False, "Conta não encontrada."
    miracle = next((m for m in available_miracles(ga) if m["city_id"] == str(city_id)), None)
    if miracle is None:
        return False, "Milagre indisponível nesta cidade."
    if not activate(ga, city_id, reason="pedido pelo Telegram", trigger="telegram_callback"):
        return False, f"{miracle['name']} já foi pedido na última hora."
    return True, f"{miracle['name']} pedido em {miracle['city_name']}."


def losing(report) -> bool:
    lost_ours = sum(int(v or 0) for v in (report.defender_losses or {}).values())
    lost_theirs = sum(int(v or 0) for v in (report.attacker_losses or {}).values())
    return lost_ours > lost_theirs


def on_combat_round(report) -> list[str]:
    """A combat in one of our cities, defending and losing: Colosso of that island."""
    from apps.combat.services.intake import _own_side

    ga = report.game_account
    if ga is None or report.finished or not report.target_city_id:
        return []
    if not _own_side(report.defender_name, ga.name):
        return []
    if not rules_for(_organization_of(ga)).get("colossus_when_losing") or not losing(report):
        return []
    asked = []
    for miracle in available_miracles(ga):
        if miracle["name"] in COLOSSUS and activate(ga, miracle["city_id"], reason=f"perdendo o combate em {report.target_city_name}"):
            asked.append(miracle["name"])
    return asked


def agent_rules(organization) -> dict:
    """What the agent needs to apply the shipment rule itself."""
    rules = rules_for(organization)
    return {"speed_on_shipments": bool(rules["speed_on_shipments"]), "ship_share": int(rules["ship_share"]),
            "min_ships": int(rules["min_ships"]), "speed_miracles": list(SPEED_MIRACLES)}
