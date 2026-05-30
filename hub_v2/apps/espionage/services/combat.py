"""
Combat calculator for land battles.

Given an enemy army (from spy report) and available own units,
returns whether the battle can be won and recommends a minimum force.

Minimum siege rules (user-defined):
    - 6 Morteiros (305)   OR
    - 12 Catapultas (306) OR
    - 18 Arietes (307)
At least one of these must be included in every land attack.

Battle model (simplified):
    - Each round all units fire simultaneously.
    - Expected damage per round = sum(weapon_damage × precision/100 × qty).
    - Wall (if present) must be broken by artillery before enemy troops are exposed.
    - After wall falls, artillery switches to attacking main line.
    - Rounds to kill = target_total_hp / attacker_dps.
    - Attacker wins if (rounds_to_kill_enemy × enemy_dps) < own_total_hp.
"""

from __future__ import annotations

import math
from typing import Any

from .unit_stats import UNIT_STATS

# ── Constants ────────────────────────────────────────────────────────────────

MERCHANT_SHIP_ID   = 201
MERCHANT_CAPACITY  = 500   # resources per merchant ship (base; actual may be higher with upgrades)

# Minimum siege to always include in every land attack
MIN_SIEGE_OPTIONS: list[tuple[int, int]] = [
    (305, 6),   # 6 Morteiros
    (306, 12),  # 12 Catapultas
    (307, 18),  # 18 Arietes
]

# Preferred front-line unit (hoplita)
FRONT_LINE_UNIT = 303  # Hoplita

# Wall HP per level (approximate game values)
def wall_hp(level: int) -> int:
    if level <= 0:
        return 0
    return level * 2250

def wall_armor(level: int) -> int:
    return level * 20


# ── Helpers ──────────────────────────────────────────────────────────────────

def _unit_dps(unit_id: int, qty: int, off_level: int = 0, weapon_index: int = 0) -> float:
    """Expected damage per round from qty units, using their primary weapon."""
    stat = UNIT_STATS.get(int(unit_id))
    if not stat or qty <= 0:
        return 0.0
    weapons = stat.get("weapons", [])
    if weapon_index >= len(weapons):
        return 0.0
    w = weapons[weapon_index]
    bonus = off_level * stat.get("improvements", {}).get("offensive", {}).get("bonus_per_level", 0)
    return (w["damage"] + bonus) * (w["precision"] / 100.0) * qty


def _unit_total_hp(unit_id: int, qty: int, def_level: int = 0) -> int:
    stat = UNIT_STATS.get(int(unit_id))
    if not stat or qty <= 0:
        return 0
    armor_bonus = def_level * stat.get("improvements", {}).get("defensive", {}).get("bonus_per_level", 0)
    return stat["hp"] * qty  # armor reduces incoming damage, not HP directly


def _siege_dps_vs_wall(siege: dict[int, int], off_level: int = 0) -> float:
    """DPS of siege units against the wall (siege uses primary weapon vs wall first)."""
    total = 0.0
    for uid, qty in siege.items():
        total += _unit_dps(uid, qty, off_level)
    return total


def _army_dps(units: dict[int, int], off_level: int = 0) -> float:
    total = 0.0
    for uid, qty in units.items():
        total += _unit_dps(uid, qty, off_level)
    return total


def _army_hp(units: dict[int, int], def_level: int = 0) -> int:
    total = 0
    for uid, qty in units.items():
        total += _unit_total_hp(uid, qty, def_level)
    return total


# ── Siege selection ──────────────────────────────────────────────────────────

def pick_minimum_siege(available: dict[int, int] | None) -> dict[int, int]:
    """Return the first viable minimum siege option from available units.

    Priority: 305 (morteiro) → 306 (catapulta) → 307 (ariete).
    If nothing available, recommend morteiros as default.
    """
    for uid, min_qty in MIN_SIEGE_OPTIONS:
        avail = (available or {}).get(uid, 0)
        if avail >= min_qty:
            return {uid: min_qty}
    # Nothing available → return the recommendation anyway (user must train them)
    return {305: 6}


def max_siege_available(available: dict[int, int] | None) -> dict[int, int]:
    """Return the strongest available siege option fully available."""
    for uid, min_qty in MIN_SIEGE_OPTIONS:
        avail = (available or {}).get(uid, min_qty)
        if avail >= min_qty:
            return {uid: avail}
    return {305: (available or {}).get(305, 6)}


# ── Core calculator ──────────────────────────────────────────────────────────

def calculate(
    enemy_units: dict[int, int],
    *,
    enemy_off_level: int = 0,
    enemy_def_level: int = 0,
    wall_level: int = 1,
    own_units: dict[int, int],
    own_off_level: int = 0,
    own_def_level: int = 0,
) -> dict[str, Any]:
    """Calculate battle outcome for an attacker vs a defender.

    Returns:
        {
            "can_win": bool,
            "rounds_to_break_wall": int,
            "rounds_to_kill_enemy": float,
            "own_dps": float,
            "enemy_dps": float,
            "own_total_hp": int,
            "enemy_total_hp": int,
            "damage_taken": float,       # estimated total damage we take
            "surviving_hp_pct": float,   # % of own HP remaining after battle
        }
    """
    own_dps   = _army_dps(own_units, own_off_level)
    enemy_dps = _army_dps(enemy_units, enemy_off_level)
    own_hp    = _army_hp(own_units, own_def_level)
    enemy_hp  = _army_hp(enemy_units, enemy_def_level)

    # Wall phase — siege must break wall before main troops engage
    wh = wall_hp(wall_level)
    siege_only = {uid: qty for uid, qty in own_units.items() if uid in {305, 306, 307}}
    siege_dps  = _siege_dps_vs_wall(siege_only, own_off_level)
    rounds_wall = math.ceil(wh / siege_dps) if (wh > 0 and siege_dps > 0) else 0

    # During wall phase, enemy fires at us but wall blocks some — simplified: enemy fires freely
    damage_during_wall = enemy_dps * rounds_wall

    # After wall falls: full combat
    remaining_own_hp = max(0, own_hp - damage_during_wall)
    rounds_to_kill_enemy = (enemy_hp / own_dps) if own_dps > 0 else float("inf")
    damage_after_wall    = enemy_dps * rounds_to_kill_enemy
    total_damage_taken   = damage_during_wall + damage_after_wall

    surviving_hp = max(0, own_hp - total_damage_taken)
    can_win = surviving_hp > 0 and rounds_to_kill_enemy < float("inf")

    return {
        "can_win":               can_win,
        "rounds_to_break_wall":  rounds_wall,
        "rounds_to_kill_enemy":  round(rounds_to_kill_enemy, 1),
        "own_dps":               round(own_dps, 1),
        "enemy_dps":             round(enemy_dps, 1),
        "own_total_hp":          own_hp,
        "enemy_total_hp":        enemy_hp,
        "damage_taken":          round(total_damage_taken, 1),
        "surviving_hp_pct":      round(100 * surviving_hp / own_hp, 1) if own_hp > 0 else 0.0,
    }


def recommend_army(
    enemy_units: dict[int, int],
    *,
    enemy_off_level: int = 0,
    enemy_def_level: int = 0,
    wall_level: int = 1,
    available_units: dict[int, int] | None = None,
    safety_margin: float = 1.5,
) -> dict[str, Any]:
    """Recommend minimum attack force to win against enemy_units.

    Args:
        enemy_units:      {unit_id: qty} from spy report (mission 5).
        enemy_off_level:  enemy offensive invention level (mission 26, or 0 if unknown).
        enemy_def_level:  enemy defensive invention level.
        wall_level:       city wall level (or 1 if unknown).
        available_units:  own units available at source city.
        safety_margin:    multiplier on minimum required troops (default 1.5×).

    Returns:
        {
            "recommended": {unit_id: qty},
            "siege": {unit_id: qty},
            "front_line": {unit_id: qty},
            "can_win_with_recommended": bool,
            "battle_estimate": dict,     # from calculate()
            "missing_units": list[str],  # units needed but not available
        }
    """
    # 1. Pick minimum siege
    siege = pick_minimum_siege(available_units)

    # 2. Enemy total HP and DPS
    enemy_total_hp = _army_hp(enemy_units, enemy_def_level)
    enemy_dps      = _army_dps(enemy_units, enemy_off_level)

    # 3. Determine minimum front-line (Hoplitas) to survive
    #    Math: need qty such that:
    #      (enemy_hp / (qty × hoplita_dps)) × enemy_dps < qty × hoplita_hp
    #      → qty² > enemy_hp × enemy_dps / (hoplita_dps × hoplita_hp)
    #      → qty > sqrt(enemy_hp × enemy_dps / (hoplita_dps × hoplita_hp))
    front_line: dict[int, int] = {}
    hoplita     = UNIT_STATS.get(FRONT_LINE_UNIT, {})
    h_hp        = hoplita.get("hp", 1120)
    h_weapons   = hoplita.get("weapons", [{"damage": 360, "precision": 90.0}])
    h_dps       = h_weapons[0]["damage"] * h_weapons[0]["precision"] / 100.0

    if enemy_total_hp > 0 and h_dps > 0 and h_hp > 0:
        if enemy_dps > 0:
            min_qty = math.ceil(math.sqrt(enemy_total_hp * enemy_dps / (h_dps * h_hp)) * safety_margin)
        else:
            min_qty = math.ceil((enemy_total_hp / (h_hp * h_dps)) * safety_margin)
        min_qty = max(min_qty, 1)
    else:
        min_qty = 1

    front_line[FRONT_LINE_UNIT] = min_qty

    # 4. Merge siege + front_line
    recommended = {**siege, **front_line}

    # 5. Check availability and track missing
    missing = []
    if available_units is not None:
        for uid, needed in recommended.items():
            have = available_units.get(uid, 0)
            if have < needed:
                stat = UNIT_STATS.get(uid, {})
                name = stat.get("name", f"Unit {uid}")
                missing.append(f"{name}: precisa {needed}, tem {have}")

    # 6. Simulate battle with recommended force
    estimate = calculate(
        enemy_units,
        enemy_off_level=enemy_off_level,
        enemy_def_level=enemy_def_level,
        wall_level=wall_level,
        own_units=recommended,
    )

    return {
        "recommended":                 recommended,
        "siege":                       siege,
        "front_line":                  front_line,
        "can_win_with_recommended":    estimate["can_win"],
        "battle_estimate":             estimate,
        "missing_units":               missing,
    }


# ── Loot capacity ────────────────────────────────────────────────────────────

def loot_capacity(n_transporters: int) -> int:
    """Total resources a fleet of merchant ships can carry."""
    return n_transporters * MERCHANT_CAPACITY


def transporters_needed(total_resources: int) -> int:
    """Minimum merchant ships to carry all available loot in one trip."""
    if total_resources <= 0:
        return 0
    return math.ceil(total_resources / MERCHANT_CAPACITY)


def trips_needed(total_resources: int, n_transporters: int) -> int:
    """Number of raids needed to loot all resources with given transporter count."""
    if n_transporters <= 0 or total_resources <= 0:
        return 0
    return math.ceil(total_resources / loot_capacity(n_transporters))


def format_battle_summary(
    enemy_units: dict[int, int],
    recommendation: dict,
    resources: dict[str, int],
    n_transporters: int,
    travel_seconds: int,
    wall_level: int = 1,
) -> str:
    """Format a human-readable battle summary for Telegram notifications."""
    from math import ceil

    total_res  = sum(resources.values())
    capacity   = loot_capacity(n_transporters)
    trips      = trips_needed(total_res, n_transporters)
    travel_h   = travel_seconds // 3600
    travel_m   = (travel_seconds % 3600) // 60

    # Enemy description
    enemy_lines = []
    for uid, qty in sorted(enemy_units.items()):
        stat = UNIT_STATS.get(int(uid), {})
        name = stat.get("name", f"Unit {uid}")
        enemy_lines.append(f"{qty}× {name}")
    enemy_str = ", ".join(enemy_lines) if enemy_lines else "Desconhecido"

    # Recommended attack
    rec_lines = []
    for uid, qty in sorted(recommendation["recommended"].items()):
        stat = UNIT_STATS.get(int(uid), {})
        name = stat.get("name", f"Unit {uid}")
        rec_lines.append(f"{qty}× {name}")
    rec_str = ", ".join(rec_lines) if rec_lines else "—"

    est = recommendation["battle_estimate"]
    can_win = "✅ Estimativa: vitória" if est["can_win"] else "⚠️ Risco de derrota"
    surv = est["surviving_hp_pct"]

    res_lines = []
    labels = {"wood": "🪵 Madeira", "wine": "🍷 Vinho", "marble": "🏛️ Mármore",
              "glass": "🔮 Cristal", "sulfur": "💛 Enxofre"}
    for k, v in resources.items():
        if v > 0:
            res_lines.append(f"{labels.get(k, k)}: {v:,}")
    res_str = "\n".join(res_lines) if res_lines else "—"

    lines = [
        f"💎 Recursos disponíveis:",
        res_str,
        f"Total: {total_res:,}",
        "",
        f"⚔️  Defesa: {enemy_str}",
        f"🧱 Muralha nível {wall_level}",
        "",
        f"🗡️  Força recomendada: {rec_str}",
        f"{can_win} ({surv:.0f}% HP restante)",
        "",
        f"🚢 {n_transporters} barco(s) mercante(s) × 500 = {capacity:,}/viagem",
        f"   → {trips} viagem(ns) para levar tudo",
        f"⏱️  Tempo de viagem: {travel_h}h {travel_m:02d}m" if travel_seconds else "⏱️  Tempo: não calculado",
    ]
    if recommendation.get("missing_units"):
        lines.append("")
        lines.append("⚠️ Unidades insuficientes:")
        for m in recommendation["missing_units"]:
            lines.append(f"  • {m}")

    return "\n".join(lines)
