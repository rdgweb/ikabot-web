"""Land battle simulation used by raid alerts and combat recommendations."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from .unit_stats import UNIT_STATS, IMPROVEMENT_BONUS_LAND


LINE_PRINCIPAL = {303, 308, 316, 319}
LINE_FLANCOS = {302, 315, 320, 321}
LINE_LONGA = {301, 304, 313}
LINE_ARTILH = {305, 306, 307}
LINE_BOMB = {309}
LINE_AA = {312}
LINE_BAGAGEM = {310, 311}

PROTECTED_LINES_BY_WALL = {"principal", "flancos"}

FIELD_SLOTS: dict[int, dict[str, tuple[int, int]]] = {
    1: {
        "principal": (3, 30),
        "flancos": (2, 10),
        "longa": (3, 30),
        "artilharia": (1, 30),
        "bomb": (1, 10),
        "aa": (1, 10),
    },
    5: {
        "principal": (5, 30),
        "flancos": (2, 30),
        "longa": (5, 30),
        "artilharia": (2, 30),
        "bomb": (1, 20),
        "aa": (1, 20),
    },
    10: {
        "principal": (7, 30),
        "flancos": (4, 30),
        "longa": (7, 30),
        "artilharia": (3, 30),
        "bomb": (1, 30),
        "aa": (1, 30),
    },
    17: {
        "principal": (7, 40),
        "flancos": (6, 30),
        "longa": (7, 40),
        "artilharia": (4, 30),
        "bomb": (2, 20),
        "aa": (2, 20),
    },
    25: {
        "principal": (7, 50),
        "flancos": (6, 40),
        "longa": (7, 50),
        "artilharia": (5, 30),
        "bomb": (2, 30),
        "aa": (2, 30),
    },
}

DEPLOY_PRIORITY = {
    "principal": [303, 319, 316, 308],
    "principal_fillers": [302, 315, 321, 320],
    "flancos": [302, 321, 315, 320],
    "longa": [304, 313, 301],
    "artilharia": [305, 306, 307],
    "bomb": [309],
    "aa": [312],
    "bagagem": [310, 311],
}

def _wall_hp(level: int) -> int:
    lv = max(0, int(level or 0))
    if lv == 0:
        return 0
    return int(500 + 200 * lv * lv / 10)


def _field_level_from_th(town_hall_level: int) -> int:
    th = int(town_hall_level or 1)
    if th >= 25:
        return 25
    if th >= 17:
        return 17
    if th >= 10:
        return 10
    if th >= 5:
        return 5
    return 1


def _line_capacity(field_level: int, line: str) -> int:
    slots, cap = FIELD_SLOTS.get(field_level, FIELD_SLOTS[1]).get(line, (0, 0))
    return slots * cap


def _classify(unit_id: int) -> str:
    if unit_id in LINE_PRINCIPAL:
        return "principal"
    if unit_id in LINE_FLANCOS:
        return "flancos"
    if unit_id in LINE_LONGA:
        return "longa"
    if unit_id in LINE_ARTILH:
        return "artilharia"
    if unit_id in LINE_BOMB:
        return "bomb"
    if unit_id in LINE_AA:
        return "aa"
    if unit_id in LINE_BAGAGEM:
        return "bagagem"
    return "outros"


def _unit_size(unit_id: int) -> int:
    unit = UNIT_STATS.get(unit_id) or {}
    return max(1, int(unit.get("size", 1)))


def _weapon_damage(unit_id: int, weapon_index: int, off_level: int) -> int:
    unit = UNIT_STATS.get(unit_id) or {}
    weapons = unit.get("weapons") or [{}]
    idx = weapon_index if weapon_index < len(weapons) else len(weapons) - 1
    weapon = weapons[idx]
    base = int(weapon.get("damage", 0))
    precision = float(weapon.get("precision", 100.0)) / 100.0
    bonus = int(off_level or 0) * IMPROVEMENT_BONUS_LAND
    # Precision already influences focus/scatter below. Here we only damp very
    # inaccurate weapons enough to avoid overkilling every round.
    damage_scale = 0.35 + (0.65 * precision)
    return int((base + bonus) * damage_scale)


def _weapon_ammo(unit_id: int, weapon_index: int) -> int | None:
    unit = UNIT_STATS.get(unit_id) or {}
    weapons = unit.get("weapons") or [{}]
    if weapon_index >= len(weapons):
        return None
    raw = weapons[weapon_index].get("ammo")
    return int(raw) if raw is not None else None


def _has_usable_support_ammo(ammo_left: dict[tuple[int, int], int], unit_id: int) -> bool:
    unit = UNIT_STATS.get(unit_id) or {}
    weapons = unit.get("weapons") or []
    for idx, _weapon in enumerate(weapons):
        ammo = _weapon_ammo(unit_id, idx)
        if ammo is None:
            continue
        if int(ammo_left.get((unit_id, idx), 0) or 0) > 0:
            return True
    return False


@dataclass
class Stack:
    unit_id: int
    count: int
    size: int


@dataclass
class AttackProfile:
    total_damage: int = 0
    total_hits: int = 0
    weighted_precision: float = 0.0

    @property
    def avg_precision(self) -> float:
        if self.total_hits <= 0:
            return 0.0
        return max(0.0, min(1.0, self.weighted_precision / self.total_hits))

    @property
    def avg_hit_damage(self) -> float:
        if self.total_hits <= 0:
            return 0.0
        return self.total_damage / self.total_hits


@dataclass
class RoundDeployment:
    lines: dict[str, list[Stack]] = field(default_factory=dict)
    ammo_state: dict[tuple[str, int, int], int] = field(default_factory=dict)

    def line_total(self, line: str) -> int:
        return sum(stack.count for stack in self.lines.get(line, []))


def _serialize_deployment(deployment: RoundDeployment) -> dict[str, dict[int, int]]:
    data: dict[str, dict[int, int]] = {}
    for line, stacks in deployment.lines.items():
        counts: dict[int, int] = {}
        for stack in stacks:
            if stack.count > 0:
                counts[stack.unit_id] = counts.get(stack.unit_id, 0) + stack.count
        data[line] = counts
    return data


def _clone_deployment(deployment: RoundDeployment) -> dict[str, dict[int, int]]:
    return _serialize_deployment(deployment)


def _reinforcement_lines(units: dict[int | str, int] | None) -> set[str]:
    lines: set[str] = set()
    if not units:
        return lines
    for raw_uid, raw_qty in units.items():
        qty = int(raw_qty or 0)
        if qty <= 0:
            continue
        try:
            uid = int(raw_uid)
        except (TypeError, ValueError):
            continue
        line = _classify(uid)
        if line in {"longa", "artilharia", "bomb", "aa"}:
            lines.add(line)
    return lines


def _can_field_support(army: "Army", line: str, support_refill_lines: set[str]) -> bool:
    memory = army.line_memory.get(line) or {}
    if any(int(qty or 0) > 0 for qty in memory.values()):
        return True
    return any(int(army.units.get(uid, 0) or 0) > 0 for uid in DEPLOY_PRIORITY[line])


class Army:
    def __init__(
        self,
        units: dict[int, int],
        upgrades: dict[int, dict] | None,
        field_level: int,
        *,
        is_defender: bool,
        wall_hp: int = 0,
        damage_bonus_pct: float = 0.0,
        armor_bonus: int = 0,
    ):
        self.units = {int(uid): int(qty) for uid, qty in (units or {}).items() if int(qty or 0) > 0}
        self.upgrades = upgrades or {}
        self.field_level = field_level
        self.is_defender = is_defender
        self.wall_hp = int(wall_hp) if is_defender else 0
        self.wall_segments: list[int] = []
        self.damage_bonus_pct = float(damage_bonus_pct or 0.0)
        self.armor_bonus = int(armor_bonus or 0)
        self.ammo_left: dict[tuple[int, int], int] = {}
        self.initial_units = dict(self.units)
        self.hp_pools: dict[int, int] = {}
        self.line_memory: dict[str, dict[int, int]] = {
            "longa": {},
            "artilharia": {},
            "bomb": {},
            "aa": {},
        }
        self.line_ammo_memory: dict[str, dict[tuple[int, int], int]] = {
            "longa": {},
            "artilharia": {},
            "bomb": {},
            "aa": {},
        }
        if self.is_defender and self.wall_hp > 0:
            principal_slots = FIELD_SLOTS.get(self.field_level, FIELD_SLOTS[1]).get("principal", (0, 0))[0]
            principal_slots = max(1, int(principal_slots or 1))
            base_seg = self.wall_hp // principal_slots
            rem = self.wall_hp % principal_slots
            self.wall_segments = [base_seg + (1 if idx < rem else 0) for idx in range(principal_slots)]

        for uid, qty in self.units.items():
            self.hp_pools[uid] = qty * self.unit_max_hp(uid)
            unit = UNIT_STATS.get(uid) or {}
            for idx, _ in enumerate(unit.get("weapons") or []):
                ammo = _weapon_ammo(uid, idx)
                if ammo is not None:
                    self.ammo_left[(uid, idx)] = ammo * qty

    def add_units(self, extra_units: dict[int, int]) -> None:
        for uid, qty in (extra_units or {}).items():
            uid = int(uid)
            qty = int(qty or 0)
            if qty <= 0:
                continue
            self.units[uid] = self.units.get(uid, 0) + qty
            self.initial_units[uid] = self.initial_units.get(uid, 0) + qty
            self.hp_pools[uid] = self.hp_pools.get(uid, 0) + qty * self.unit_max_hp(uid)
            unit = UNIT_STATS.get(uid) or {}
            for idx, _ in enumerate(unit.get("weapons") or []):
                ammo = _weapon_ammo(uid, idx)
                if ammo is not None:
                    key = (uid, idx)
                    self.ammo_left[key] = self.ammo_left.get(key, 0) + ammo * qty

    def off_level(self, unit_id: int) -> int:
        return int((self.upgrades.get(unit_id) or {}).get("offensive", 0))

    def def_level(self, unit_id: int) -> int:
        return int((self.upgrades.get(unit_id) or {}).get("defensive", 0))

    def total_combat_units(self) -> int:
        combat_ids = LINE_PRINCIPAL | LINE_FLANCOS | LINE_LONGA | LINE_ARTILH | LINE_BOMB | LINE_AA
        return sum(self.units.get(uid, 0) for uid in combat_ids)

    def unit_max_hp(self, unit_id: int) -> int:
        return int((UNIT_STATS.get(unit_id) or {}).get("hp", 0))

    def unit_armor(self, unit_id: int) -> int:
        unit = UNIT_STATS.get(unit_id) or {}
        base_armor = int(unit.get("armor", 0))
        bonus_armor = int(self.def_level(unit_id) or 0) * IMPROVEMENT_BONUS_LAND
        return base_armor + bonus_armor + self.armor_bonus

    def unit_hp(self, unit_id: int) -> int:
        return self.unit_max_hp(unit_id)

    def unit_damage(self, unit_id: int, weapon_index: int) -> int:
        base = _weapon_damage(unit_id, weapon_index, self.off_level(unit_id))
        if self.damage_bonus_pct:
            base = int(base * (1.0 + self.damage_bonus_pct / 100.0))
        return base

    def wall_segment_count(self) -> int:
        return len(self.wall_segments)

    def wall_broken_segments(self) -> int:
        if not self.wall_segments:
            return 0
        return sum(1 for hp in self.wall_segments if int(hp or 0) <= 0)

    def wall_exposed_fraction(self) -> float:
        total = self.wall_segment_count()
        if total <= 0:
            return 1.0
        return max(0.0, min(1.0, self.wall_broken_segments() / total))

    def absorb_wall_damage(self, damage: int) -> tuple[int, int, int]:
        if damage <= 0 or not self.wall_segments or self.wall_hp <= 0:
            return damage, self.wall_broken_segments(), self.wall_broken_segments()
        broken_before = self.wall_broken_segments()
        remaining = int(damage)
        for idx, hp in enumerate(self.wall_segments):
            hp = int(hp or 0)
            if hp <= 0:
                continue
            take = min(hp, remaining)
            self.wall_segments[idx] = hp - take
            remaining -= take
            if remaining <= 0:
                break
        self.wall_hp = max(0, sum(max(0, int(hp or 0)) for hp in self.wall_segments))
        broken_after = self.wall_broken_segments()
        return remaining, broken_before, broken_after

    def heal_end_of_round(self) -> None:
        medics = self.units.get(311, 0)
        if medics <= 0:
            return
        heal_points = medics * 180
        if heal_points <= 0:
            return
        priority = ("principal", "flancos", "longa", "artilharia", "bomb", "aa")
        wounded = []
        for uid, qty in self.units.items():
            if _classify(uid) not in priority:
                continue
            max_total = qty * self.unit_max_hp(uid)
            current = self.hp_pools.get(uid, max_total)
            missing = max(0, max_total - current)
            if missing > 0:
                wounded.append((uid, missing))
        wounded.sort(key=lambda item: (priority.index(_classify(item[0])), -item[1]))
        for uid, missing in wounded:
            if heal_points <= 0:
                break
            heal = min(missing, heal_points)
            self.hp_pools[uid] = min(self.units[uid] * self.unit_max_hp(uid), self.hp_pools.get(uid, 0) + heal)
            heal_points -= heal

    def deploy_for_round(
        self,
        *,
        enemy_has_air: bool = True,
        support_refill_lines: set[str] | None = None,
    ) -> RoundDeployment:
        remaining = dict(self.units)
        support_refill_lines = support_refill_lines or set()
        lines: dict[str, list[Stack]] = {
            "principal": [],
            "flancos": [],
            "longa": [],
            "artilharia": [],
            "bomb": [],
            "aa": [],
            "bagagem": [],
        }
        ammo_state: dict[tuple[str, int, int], int] = {}

        def fill(line: str, unit_ids: list[int], capacity: int, unlimited: bool = False) -> None:
            used = sum(stack.count * stack.size for stack in lines[line])
            for uid in unit_ids:
                qty = remaining.get(uid, 0)
                if qty <= 0:
                    continue
                if line in {"longa", "artilharia", "bomb", "aa"}:
                    unit = UNIT_STATS.get(uid) or {}
                    if any(_weapon_ammo(uid, idx) is not None for idx, _ in enumerate(unit.get("weapons") or [])):
                        if not _has_usable_support_ammo(self.ammo_left, uid):
                            continue
                size = _unit_size(uid)
                if unlimited:
                    take = qty
                else:
                    free = capacity - used
                    if free < size:
                        continue
                    take = min(qty, free // size)
                if take <= 0:
                    continue
                lines[line].append(Stack(uid, take, size))
                remaining[uid] -= take
                if remaining[uid] <= 0:
                    remaining.pop(uid, None)
                used += take * size
                if not unlimited and used >= capacity:
                    break

        def fill_from_memory(line: str, capacity: int) -> int:
            memory = self.line_memory.get(line) or {}
            if not memory:
                return 0
            used = 0
            for uid in DEPLOY_PRIORITY[line]:
                desired = int(memory.get(uid, 0) or 0)
                if desired <= 0:
                    continue
                qty = remaining.get(uid, 0)
                if qty <= 0:
                    continue
                size = _unit_size(uid)
                free = capacity - used
                if free < size:
                    continue
                take = min(desired, qty, free // size)
                if take <= 0:
                    continue
                lines[line].append(Stack(uid, take, size))
                remaining[uid] -= take
                if remaining[uid] <= 0:
                    remaining.pop(uid, None)
                used += take * size
                ammo_mem = self.line_ammo_memory.get(line, {})
                for weapon_idx in range(len((UNIT_STATS.get(uid) or {}).get("weapons") or [])):
                    key = (uid, weapon_idx)
                    if key in ammo_mem:
                        ammo_state[(line, uid, weapon_idx)] = min(
                            int(ammo_mem[key]),
                            take * (_weapon_ammo(uid, weapon_idx) or 0),
                        )
                if used >= capacity:
                    break
            return used

        fill("principal", DEPLOY_PRIORITY["principal"], _line_capacity(self.field_level, "principal"))
        principal_used = sum(stack.count * stack.size for stack in lines["principal"])
        principal_cap = _line_capacity(self.field_level, "principal")
        if principal_used < principal_cap:
            fill(
                "principal",
                DEPLOY_PRIORITY["principal_fillers"],
                principal_cap,
            )

        fill("flancos", DEPLOY_PRIORITY["flancos"], _line_capacity(self.field_level, "flancos"))
        longa_cap = _line_capacity(self.field_level, "longa")
        longa_used = fill_from_memory("longa", longa_cap)
        if longa_used < longa_cap:
            fill("longa", DEPLOY_PRIORITY["longa"], longa_cap)

        artilh_cap = _line_capacity(self.field_level, "artilharia")
        artilh_used = fill_from_memory("artilharia", artilh_cap)
        if artilh_used < artilh_cap:
            fill("artilharia", DEPLOY_PRIORITY["artilharia"], artilh_cap)

        bomb_cap = _line_capacity(self.field_level, "bomb")
        bomb_used = fill_from_memory("bomb", bomb_cap)
        if bomb_used < bomb_cap:
            fill("bomb", DEPLOY_PRIORITY["bomb"], bomb_cap)
        if enemy_has_air:
            aa_cap = _line_capacity(self.field_level, "aa")
            aa_used = fill_from_memory("aa", aa_cap)
            if aa_used < aa_cap:
                fill("aa", DEPLOY_PRIORITY["aa"], aa_cap)
        fill("bagagem", DEPLOY_PRIORITY["bagagem"], 10**9, unlimited=True)

        return RoundDeployment(lines=lines, ammo_state=ammo_state)

    def remember_support_lines(self, deployment: RoundDeployment) -> None:
        for line in ("longa", "artilharia", "bomb", "aa"):
            counts: dict[int, int] = {}
            ammo_counts: dict[tuple[int, int], int] = {}
            for stack in deployment.lines.get(line, []):
                if stack.count > 0:
                    unit = UNIT_STATS.get(stack.unit_id) or {}
                    has_ammo_weapon = any(
                        _weapon_ammo(stack.unit_id, weapon_idx) is not None
                        for weapon_idx in range(len(unit.get("weapons") or []))
                    )
                    if has_ammo_weapon and not _has_usable_support_ammo(self.ammo_left, stack.unit_id):
                        continue
                    counts[stack.unit_id] = counts.get(stack.unit_id, 0) + stack.count
                    for weapon_idx in range(len((UNIT_STATS.get(stack.unit_id) or {}).get("weapons") or [])):
                        dep_key = (line, stack.unit_id, weapon_idx)
                        if dep_key in deployment.ammo_state:
                            ammo_counts[(stack.unit_id, weapon_idx)] = deployment.ammo_state[dep_key]
            self.line_memory[line] = counts
            self.line_ammo_memory[line] = ammo_counts



def _line_attack_profile(army: Army, deployment: RoundDeployment, line: str, preferred_weapon: int) -> AttackProfile:
    profile = AttackProfile()
    for stack in deployment.lines.get(line, []):
        uid = stack.unit_id
        qty = stack.count
        if qty <= 0:
            continue
        weapons = (UNIT_STATS.get(uid) or {}).get("weapons") or [{}]
        weapon_idx = preferred_weapon if preferred_weapon < len(weapons) else len(weapons) - 1

        ammo = _weapon_ammo(uid, weapon_idx)
        if ammo is None:
            precision = float(weapons[weapon_idx].get("precision", 100.0)) / 100.0
            profile.total_damage += qty * army.unit_damage(uid, weapon_idx)
            profile.total_hits += qty
            profile.weighted_precision += qty * precision
            continue

        ammo_key = (uid, weapon_idx)
        dep_ammo_key = (line, uid, weapon_idx)
        if dep_ammo_key in deployment.ammo_state:
            shots_left = deployment.ammo_state.get(dep_ammo_key, 0)
            use_deployment_ammo = True
        else:
            shots_left = army.ammo_left.get(ammo_key, 0)
            use_deployment_ammo = False
        shooting = min(qty, shots_left)
        fallback = qty - shooting
        if shooting > 0:
            precision = float(weapons[weapon_idx].get("precision", 100.0)) / 100.0
            profile.total_damage += shooting * army.unit_damage(uid, weapon_idx)
            profile.total_hits += shooting
            profile.weighted_precision += shooting * precision
            if use_deployment_ammo:
                deployment.ammo_state[dep_ammo_key] = shots_left - shooting
            else:
                army.ammo_left[ammo_key] = shots_left - shooting
        if fallback > 0:
            # Ammo-based support units do not keep contributing meaningful damage
            # on the same battle line once their ammunition is depleted; they
            # should effectively open room for replacement on the following round.
            if line in {"longa", "artilharia", "bomb", "aa"}:
                continue
            fallback_idx = 0
            fallback_weapons = (UNIT_STATS.get(uid) or {}).get("weapons") or [{}]
            fallback_precision = float(fallback_weapons[fallback_idx].get("precision", 100.0)) / 100.0
            profile.total_damage += fallback * army.unit_damage(uid, fallback_idx)
            profile.total_hits += fallback
            profile.weighted_precision += fallback * fallback_precision
    return profile


def _line_hp(army: Army, deployment: RoundDeployment, line: str) -> int:
    hp = 0
    for stack in deployment.lines.get(line, []):
        uid = stack.unit_id
        count = army.units.get(uid, 0)
        if count <= 0:
            continue
        hp_total = army.hp_pools.get(uid, count * army.unit_max_hp(uid))
        if stack.count >= count:
            hp += hp_total
        else:
            hp += int(hp_total * (stack.count / count))
    return hp


def _enforce_minimum_raid_force(force: dict[int, int], available_units: dict[int, int]) -> dict[int, int]:
    """Guarantee a sane minimum for land raids: 30 frontline + 6 siege if available."""
    out = {int(uid): int(qty) for uid, qty in (force or {}).items() if int(qty or 0) > 0}

    frontline_ids = (303, 308, 302, 315)
    siege_ids = (305, 306, 307)

    current_front = sum(int(out.get(uid, 0) or 0) for uid in frontline_ids)
    current_siege = sum(int(out.get(uid, 0) or 0) for uid in siege_ids)

    if current_front < 30:
        need = 30 - current_front
        for uid in frontline_ids:
            have = int(available_units.get(uid, 0) or 0)
            used = int(out.get(uid, 0) or 0)
            free = max(0, have - used)
            if free <= 0:
                continue
            add = min(free, need)
            out[uid] = used + add
            need -= add
            if need <= 0:
                break

    if current_siege < 6:
        need = 6 - current_siege
        for uid in siege_ids:
            have = int(available_units.get(uid, 0) or 0)
            used = int(out.get(uid, 0) or 0)
            free = max(0, have - used)
            if free <= 0:
                continue
            add = min(free, need)
            out[uid] = used + add
            need -= add
            if need <= 0:
                break

    return {uid: qty for uid, qty in out.items() if int(qty or 0) > 0}


def _apply_hits_to_stack(army: Army, stack: Stack, hits: int, damage_per_hit: float) -> int:
    if hits <= 0 or damage_per_hit <= 0 or stack.count <= 0:
        return 0
    uid = stack.unit_id
    global_count = army.units.get(uid, 0)
    if global_count <= 0:
        stack.count = 0
        return 0
    max_hp = max(1, army.unit_max_hp(uid))
    global_hp = max(0, army.hp_pools.get(uid, global_count * max_hp))
    stack_hp = min(stack.count * max_hp, int(round(global_hp * (stack.count / max(1, global_count)))))
    effective = max(1, int(round(damage_per_hit - army.unit_armor(uid))))
    hp_loss = min(stack_hp, hits * effective)
    new_stack_hp = max(0, stack_hp - hp_loss)
    new_stack_count = int(math.ceil(new_stack_hp / max_hp)) if new_stack_hp > 0 else 0
    new_stack_count = min(new_stack_count, stack.count)
    losses = max(0, stack.count - new_stack_count)

    stack.count = new_stack_count
    army.units[uid] = max(0, global_count - losses)
    army.hp_pools[uid] = max(0, global_hp - hp_loss)
    if army.units[uid] <= 0 or army.hp_pools[uid] <= 0:
        army.units.pop(uid, None)
        army.hp_pools.pop(uid, None)
        stack.count = 0
    return losses


def _apply_damage_to_line(
    army: Army,
    deployment: RoundDeployment,
    line: str,
    attack: AttackProfile,
    *,
    use_wall: bool,
) -> tuple[int, AttackProfile]:
    if attack.total_damage <= 0 or attack.total_hits <= 0:
        return 0, AttackProfile()

    damage = attack.total_damage
    hits = attack.total_hits
    if damage <= 0 or hits <= 0:
        return 0, AttackProfile()

    avg_precision = attack.avg_precision
    avg_hit_damage = attack.avg_hit_damage

    if use_wall and army.is_defender and army.wall_hp > 0 and line == "principal":
        damage_after_wall, broken_before, broken_after = army.absorb_wall_damage(damage)
        if damage_after_wall <= 0:
            return 0, AttackProfile()
        opened_segments = max(0, broken_after - broken_before)
        exposed_fraction = army.wall_exposed_fraction()
        if opened_segments > 0 and army.wall_segment_count() > 0:
            exposed_fraction = max(
                exposed_fraction,
                opened_segments / max(1, army.wall_segment_count()),
            )
        if exposed_fraction <= 0:
            return 0, AttackProfile()
        damage = max(1, int(round(damage_after_wall * exposed_fraction)))
        ratio = damage / max(1, attack.total_damage)
        hits = max(1, int(round(hits * ratio)))

    alive_stacks = [stack for stack in deployment.lines.get(line, []) if stack.count > 0]
    if not alive_stacks:
        return 0, AttackProfile(total_damage=damage, total_hits=hits, weighted_precision=hits * avg_precision)

    focused_hits = min(hits, int(round(hits * avg_precision)))
    scattered_hits = max(0, hits - focused_hits)
    losses = 0

    focus_remaining = focused_hits
    for stack in alive_stacks:
        if focus_remaining <= 0:
            break
        uid = stack.unit_id
        global_count = max(1, army.units.get(uid, stack.count))
        global_hp = army.hp_pools.get(uid, global_count * army.unit_max_hp(uid))
        hp_total = min(stack.count * army.unit_max_hp(uid), int(round(global_hp * (stack.count / global_count))))
        armor = army.unit_armor(uid)
        hit_value = max(1, int(round(avg_hit_damage - armor)))
        hits_to_clear = max(1, int(math.ceil(hp_total / max(1, hit_value))))
        take = min(focus_remaining, hits_to_clear)
        losses += _apply_hits_to_stack(army, stack, take, avg_hit_damage)
        focus_remaining -= take

    alive_stacks = [stack for stack in alive_stacks if stack.count > 0]
    if scattered_hits > 0 and alive_stacks:
        total_units = sum(stack.count for stack in alive_stacks)
        distributed = 0
        for idx, stack in enumerate(alive_stacks):
            if scattered_hits - distributed <= 0:
                break
            if idx == len(alive_stacks) - 1:
                share_hits = scattered_hits - distributed
            else:
                share_hits = max(0, int(round(scattered_hits * (stack.count / max(1, total_units)))))
                share_hits = min(share_hits, scattered_hits - distributed)
            distributed += share_hits
            losses += _apply_hits_to_stack(army, stack, share_hits, avg_hit_damage)
        if distributed < scattered_hits and alive_stacks:
            losses += _apply_hits_to_stack(army, alive_stacks[-1], scattered_hits - distributed, avg_hit_damage)

    remaining_stacks = [stack for stack in deployment.lines.get(line, []) if stack.count > 0]
    if remaining_stacks:
        return losses, AttackProfile()

    return losses, AttackProfile(total_damage=0, total_hits=0, weighted_precision=0.0)


def _bombard(
    attacker: Army,
    attacker_dep: RoundDeployment,
    defender: Army,
    defender_dep: RoundDeployment,
) -> None:
    bomb_attack = _line_attack_profile(attacker, attacker_dep, "bomb", 0)
    if bomb_attack.total_damage <= 0:
        return
    target_line = _first_alive_line(defender_dep, ["artilharia", "longa", "principal", "flancos"])
    if target_line:
        tuned = _tune_attack_profile(
            bomb_attack,
            damage_scale=1.18 if target_line in {"artilharia", "longa"} else 1.0,
            precision_scale=1.15 if target_line in {"artilharia", "longa"} else 1.0,
        )
        _apply_damage_to_line(defender, defender_dep, target_line, tuned, use_wall=False)


def _tune_attack_profile(
    attack: AttackProfile,
    *,
    damage_scale: float = 1.0,
    precision_scale: float = 1.0,
) -> AttackProfile:
    if damage_scale == 1.0 and precision_scale == 1.0:
        return attack
    tuned_hits = max(0, int(round(attack.total_hits * precision_scale)))
    tuned_weighted_precision = attack.weighted_precision * precision_scale
    return AttackProfile(
        total_damage=max(0, int(round(attack.total_damage * damage_scale))),
        total_hits=tuned_hits,
        weighted_precision=tuned_weighted_precision,
    )


def _spill_attack(
    attack: AttackProfile,
    defender: Army,
    defender_dep: RoundDeployment,
    targets: list[str],
) -> None:
    remaining = attack
    for target in targets:
        if remaining.total_damage <= 0 or remaining.total_hits <= 0:
            break
        line_hp = _line_hp(defender, defender_dep, target)
        if line_hp <= 0:
            continue
        _losses, leftover = _apply_damage_to_line(defender, defender_dep, target, remaining, use_wall=False)
        remaining = leftover


def _trace_line_state(deployment: RoundDeployment, line: str) -> dict[str, Any]:
    units: dict[int, int] = {}
    for stack in deployment.lines.get(line, []):
        if stack.count > 0:
            units[stack.unit_id] = units.get(stack.unit_id, 0) + stack.count
    return {
        "total": sum(units.values()),
        "units": units,
    }


def _first_alive_line(deployment: RoundDeployment, targets: list[str]) -> str | None:
    for target in targets:
        if deployment.line_total(target) > 0:
            return target
    return None


def simulate_land_battle(
    attacker_units: dict[int, int],
    defender_units: dict[int, int],
    *,
    attacker_upgrades: dict[int, dict] | None = None,
    defender_upgrades: dict[int, dict] | None = None,
    town_hall_level: int = 1,
    wall_level: int = 15,
    max_rounds: int = 12,
    attacker_damage_bonus_pct: float = 0.0,
    defender_damage_bonus_pct: float = 0.0,
    attacker_armor_bonus: int = 0,
    defender_armor_bonus: int = 0,
    attacker_reinforcements_by_round: dict[int, dict[int, int]] | None = None,
    defender_reinforcements_by_round: dict[int, dict[int, int]] | None = None,
) -> dict[str, Any]:
    field_level = _field_level_from_th(town_hall_level)
    attacker = Army(
        attacker_units,
        attacker_upgrades,
        field_level,
        is_defender=False,
        damage_bonus_pct=attacker_damage_bonus_pct,
        armor_bonus=attacker_armor_bonus,
    )
    defender = Army(
        defender_units,
        defender_upgrades,
        field_level,
        is_defender=True,
        wall_hp=_wall_hp(wall_level),
        damage_bonus_pct=defender_damage_bonus_pct,
        armor_bonus=defender_armor_bonus,
    )

    attacker_initial_total = sum(attacker.initial_units.values())
    defender_initial_total = sum(defender.initial_units.values())

    if attacker_initial_total == 0:
        return {
            "winner": "defender",
            "rounds": 0,
            "field_level": field_level,
            "attacker_initial": 0,
            "defender_initial": defender_initial_total,
            "attacker_losses": {},
            "defender_losses": {},
            "attacker_survivors_pct": 0.0,
            "defender_survivors_pct": 100.0,
            "details": "Atacante sem tropas.",
        }

    if defender_initial_total == 0:
        return {
            "winner": "attacker",
            "rounds": 0,
            "field_level": field_level,
            "attacker_initial": attacker_initial_total,
            "defender_initial": 0,
            "attacker_losses": {},
            "defender_losses": {},
            "attacker_survivors_pct": 100.0,
            "defender_survivors_pct": 0.0,
            "details": "Defensor sem tropas.",
        }

    winner = "draw"
    round_summaries: list[dict[str, Any]] = []
    rounds_played = 0

    for round_no in range(1, max_rounds + 1):
        round_trace: list[dict[str, Any]] = []
        rounds_played = round_no
        attacker_support_refill_lines = {"longa", "artilharia", "bomb", "aa"} if round_no == 1 else set()
        defender_support_refill_lines = {"longa", "artilharia", "bomb", "aa"} if round_no == 1 else set()
        if attacker_reinforcements_by_round and round_no in attacker_reinforcements_by_round:
            attacker.add_units(attacker_reinforcements_by_round[round_no])
            attacker_support_refill_lines |= _reinforcement_lines(attacker_reinforcements_by_round[round_no])
        if defender_reinforcements_by_round and round_no in defender_reinforcements_by_round:
            defender.add_units(defender_reinforcements_by_round[round_no])
            defender_support_refill_lines |= _reinforcement_lines(defender_reinforcements_by_round[round_no])
        attacker_enemy_has_air = _can_field_support(defender, "bomb", defender_support_refill_lines)
        defender_enemy_has_air = _can_field_support(attacker, "bomb", attacker_support_refill_lines)
        att_dep = attacker.deploy_for_round(
            enemy_has_air=attacker_enemy_has_air,
            support_refill_lines=attacker_support_refill_lines,
        )
        def_dep = defender.deploy_for_round(
            enemy_has_air=defender_enemy_has_air,
            support_refill_lines=defender_support_refill_lines,
        )

        att_start_lines = _clone_deployment(att_dep)
        def_start_lines = _clone_deployment(def_dep)
        wall_start_hp = defender.wall_hp

        def apply_with_trace(
            *,
            actor: str,
            source_line: str,
            target_army: Army,
            target_dep: RoundDeployment,
            target_label: str,
            target_line: str | None,
            attack: AttackProfile,
            use_wall: bool,
            note: str = "",
        ) -> tuple[int, AttackProfile]:
            before_wall = target_army.wall_hp if target_army.is_defender else 0
            before = _trace_line_state(target_dep, target_line) if target_line else {"total": 0, "units": {}}
            if not target_line or attack.total_damage <= 0 or attack.total_hits <= 0:
                round_trace.append(
                    {
                        "actor": actor,
                        "source_line": source_line,
                        "target": target_label,
                        "target_line": target_line,
                        "damage": attack.total_damage,
                        "hits": attack.total_hits,
                        "avg_precision": round(attack.avg_precision, 4),
                        "before": before,
                        "after": before,
                        "wall_before": before_wall,
                        "wall_after": before_wall,
                        "losses": 0,
                        "leftover_damage": attack.total_damage,
                        "leftover_hits": attack.total_hits,
                        "note": note or "sem alvo",
                    }
                )
                return 0, attack
            losses, leftover = _apply_damage_to_line(target_army, target_dep, target_line, attack, use_wall=use_wall)
            after_wall = target_army.wall_hp if target_army.is_defender else 0
            after = _trace_line_state(target_dep, target_line)
            round_trace.append(
                {
                    "actor": actor,
                    "source_line": source_line,
                    "target": target_label,
                    "target_line": target_line,
                    "damage": attack.total_damage,
                    "hits": attack.total_hits,
                    "avg_precision": round(attack.avg_precision, 4),
                    "before": before,
                    "after": after,
                    "wall_before": before_wall,
                    "wall_after": after_wall,
                    "losses": losses,
                    "leftover_damage": leftover.total_damage,
                    "leftover_hits": leftover.total_hits,
                    "note": note,
                }
            )
            return losses, leftover

        def spill_with_trace(
            *,
            actor: str,
            source_line: str,
            target_army: Army,
            target_dep: RoundDeployment,
            target_label: str,
            attack: AttackProfile,
            targets: list[str],
            note: str,
        ) -> None:
            remaining = attack
            for target_line in targets:
                if remaining.total_damage <= 0 or remaining.total_hits <= 0:
                    break
                line_hp = _line_hp(target_army, target_dep, target_line)
                if line_hp <= 0:
                    continue
                _losses, leftover = apply_with_trace(
                    actor=actor,
                    source_line=source_line,
                    target_army=target_army,
                    target_dep=target_dep,
                    target_label=target_label,
                    target_line=target_line,
                    attack=remaining,
                    use_wall=False,
                    note=note,
                )
                remaining = leftover

        att_aa = _line_attack_profile(attacker, att_dep, "aa", 0)
        def_aa = _line_attack_profile(defender, def_dep, "aa", 0)
        if att_aa.total_damage > 0:
            target = "bomb" if def_dep.line_total("bomb") > 0 else "aa"
            apply_with_trace(actor="attacker", source_line="aa", target_army=defender, target_dep=def_dep, target_label="defender", target_line=target, attack=att_aa, use_wall=False)
        if def_aa.total_damage > 0:
            target = "bomb" if att_dep.line_total("bomb") > 0 else "aa"
            apply_with_trace(actor="defender", source_line="aa", target_army=attacker, target_dep=att_dep, target_label="attacker", target_line=target, attack=def_aa, use_wall=False)

        bomb_attack = _line_attack_profile(attacker, att_dep, "bomb", 0)
        if bomb_attack.total_damage > 0:
            target_line = _first_alive_line(def_dep, ["artilharia", "longa", "principal", "flancos"])
            tuned = _tune_attack_profile(
                bomb_attack,
                damage_scale=1.18 if target_line in {"artilharia", "longa"} else 1.0,
                precision_scale=1.15 if target_line in {"artilharia", "longa"} else 1.0,
            )
            apply_with_trace(actor="attacker", source_line="bomb", target_army=defender, target_dep=def_dep, target_label="defender", target_line=target_line, attack=tuned, use_wall=False)
        bomb_attack = _line_attack_profile(defender, def_dep, "bomb", 0)
        if bomb_attack.total_damage > 0:
            target_line = _first_alive_line(att_dep, ["artilharia", "longa", "principal", "flancos"])
            tuned = _tune_attack_profile(
                bomb_attack,
                damage_scale=1.18 if target_line in {"artilharia", "longa"} else 1.0,
                precision_scale=1.15 if target_line in {"artilharia", "longa"} else 1.0,
            )
            apply_with_trace(actor="defender", source_line="bomb", target_army=attacker, target_dep=att_dep, target_label="attacker", target_line=target_line, attack=tuned, use_wall=False)

        att_art = _line_attack_profile(attacker, att_dep, "artilharia", 1)
        def_art = _line_attack_profile(defender, def_dep, "artilharia", 1)
        if att_art.total_damage > 0:
            target = _first_alive_line(def_dep, ["principal", "flancos"])
            if target:
                tuned = _tune_attack_profile(
                    att_art,
                    damage_scale=0.9 if target == "principal" else 0.95,
                    precision_scale=0.9 if target == "principal" else 0.95,
                )
                apply_with_trace(actor="attacker", source_line="artilharia", target_army=defender, target_dep=def_dep, target_label="defender", target_line=target, attack=tuned, use_wall=False)
        if def_art.total_damage > 0:
            target = _first_alive_line(att_dep, ["principal", "flancos"])
            if target:
                tuned = _tune_attack_profile(
                    def_art,
                    damage_scale=0.9 if target == "principal" else 0.95,
                    precision_scale=0.9 if target == "principal" else 0.95,
                )
                apply_with_trace(actor="defender", source_line="artilharia", target_army=attacker, target_dep=att_dep, target_label="attacker", target_line=target, attack=tuned, use_wall=False)

        att_long = _line_attack_profile(attacker, att_dep, "longa", 1)
        def_long = _line_attack_profile(defender, def_dep, "longa", 1)
        if att_long.total_damage > 0:
            target = _first_alive_line(def_dep, ["principal", "flancos", "longa"])
            if target:
                tuned = _tune_attack_profile(
                    att_long,
                    damage_scale=0.94 if target == "principal" else 1.0,
                    precision_scale=0.9 if target == "principal" else 1.0,
                )
                _losses, leftover = apply_with_trace(actor="attacker", source_line="longa", target_army=defender, target_dep=def_dep, target_label="defender", target_line=target, attack=tuned, use_wall=(target == "principal" and defender.wall_hp > 0))
                if leftover.total_damage > 0 and defender.wall_hp <= 0:
                    spill_with_trace(actor="attacker", source_line="longa", target_army=defender, target_dep=def_dep, target_label="defender", attack=leftover, targets=["flancos", "longa"], note="spill")
        if def_long.total_damage > 0:
            target = _first_alive_line(att_dep, ["principal", "flancos", "longa"])
            if target:
                tuned = _tune_attack_profile(
                    def_long,
                    damage_scale=0.94 if target == "principal" else 1.0,
                    precision_scale=0.9 if target == "principal" else 1.0,
                )
                _losses, leftover = apply_with_trace(actor="defender", source_line="longa", target_army=attacker, target_dep=att_dep, target_label="attacker", target_line=target, attack=tuned, use_wall=(target == "principal" and attacker.wall_hp > 0))
                if leftover.total_damage > 0 and attacker.wall_hp <= 0:
                    spill_with_trace(actor="defender", source_line="longa", target_army=attacker, target_dep=att_dep, target_label="attacker", attack=leftover, targets=["flancos", "longa"], note="spill")

        att_main = _line_attack_profile(attacker, att_dep, "principal", 0)
        def_main = _line_attack_profile(defender, def_dep, "principal", 0)
        if att_main.total_damage > 0:
            target = _first_alive_line(def_dep, ["principal", "longa", "artilharia", "flancos"])
            if target:
                tuned = _tune_attack_profile(
                    att_main,
                    damage_scale=0.95 if target == "principal" else 1.0,
                    precision_scale=0.92 if target == "principal" else 1.0,
                )
                _losses, leftover = apply_with_trace(actor="attacker", source_line="principal", target_army=defender, target_dep=def_dep, target_label="defender", target_line=target, attack=tuned, use_wall=(target == "principal" and defender.wall_hp > 0))
                if leftover.total_damage > 0 and defender.wall_hp <= 0:
                    spill_with_trace(actor="attacker", source_line="principal", target_army=defender, target_dep=def_dep, target_label="defender", attack=leftover, targets=["longa", "artilharia", "flancos"], note="spill")
        if def_main.total_damage > 0:
            target = _first_alive_line(att_dep, ["principal", "longa", "artilharia", "flancos"])
            if target:
                tuned = _tune_attack_profile(
                    def_main,
                    damage_scale=0.95 if target == "principal" else 1.0,
                    precision_scale=0.92 if target == "principal" else 1.0,
                )
                _losses, leftover = apply_with_trace(actor="defender", source_line="principal", target_army=attacker, target_dep=att_dep, target_label="attacker", target_line=target, attack=tuned, use_wall=(target == "principal" and attacker.wall_hp > 0))
                if leftover.total_damage > 0 and attacker.wall_hp <= 0:
                    spill_with_trace(actor="defender", source_line="principal", target_army=attacker, target_dep=att_dep, target_label="attacker", attack=leftover, targets=["longa", "artilharia", "flancos"], note="spill")

        att_flanks = _line_attack_profile(attacker, att_dep, "flancos", 0)
        def_flanks = _line_attack_profile(defender, def_dep, "flancos", 0)
        if att_flanks.total_damage > 0 and defender.wall_hp <= 0:
            target = _first_alive_line(def_dep, ["flancos", "longa", "artilharia", "principal"])
            if target:
                tuned = _tune_attack_profile(
                    att_flanks,
                    damage_scale=0.96 if target == "principal" else 1.0,
                    precision_scale=0.92 if target == "principal" else 1.0,
                )
                _losses, leftover = apply_with_trace(actor="attacker", source_line="flancos", target_army=defender, target_dep=def_dep, target_label="defender", target_line=target, attack=tuned, use_wall=False)
                if leftover.total_damage > 0:
                    spill_with_trace(actor="attacker", source_line="flancos", target_army=defender, target_dep=def_dep, target_label="defender", attack=leftover, targets=["longa", "artilharia", "principal"], note="spill")
        if def_flanks.total_damage > 0 and attacker.wall_hp <= 0:
            target = _first_alive_line(att_dep, ["flancos", "longa", "artilharia", "principal"])
            if target:
                tuned = _tune_attack_profile(
                    def_flanks,
                    damage_scale=0.96 if target == "principal" else 1.0,
                    precision_scale=0.92 if target == "principal" else 1.0,
                )
                _losses, leftover = apply_with_trace(actor="defender", source_line="flancos", target_army=attacker, target_dep=att_dep, target_label="attacker", target_line=target, attack=tuned, use_wall=False)
                if leftover.total_damage > 0:
                    spill_with_trace(actor="defender", source_line="flancos", target_army=attacker, target_dep=att_dep, target_label="attacker", attack=leftover, targets=["longa", "artilharia", "principal"], note="spill")

        attacker.heal_end_of_round()
        defender.heal_end_of_round()
        attacker.remember_support_lines(att_dep)
        defender.remember_support_lines(def_dep)

        round_summaries.append(
            {
                "round": round_no,
                "wall_start_hp": wall_start_hp,
                "attacker_start_lines": att_start_lines,
                "defender_start_lines": def_start_lines,
                "attacker_principal": att_dep.line_total("principal"),
                "defender_principal": def_dep.line_total("principal"),
                "attacker_flanks": att_dep.line_total("flancos"),
                "defender_flanks": def_dep.line_total("flancos"),
                "wall_hp": defender.wall_hp,
                "attacker_lines": _serialize_deployment(att_dep),
                "defender_lines": _serialize_deployment(def_dep),
                "trace": round_trace,
            }
        )

        attacker_combat = attacker.total_combat_units()
        defender_combat = defender.total_combat_units()
        attacker_main_alive = att_dep.line_total("principal") + att_dep.line_total("flancos")
        defender_main_alive = def_dep.line_total("principal") + def_dep.line_total("flancos")

        if attacker_combat <= 0 and defender_combat <= 0:
            winner = "draw"
            break
        if attacker_combat <= 0 or attacker_main_alive <= 0:
            winner = "defender"
            break
        if defender_combat <= 0 or (defender_main_alive <= 0 and defender.wall_hp <= 0):
            winner = "attacker"
            break
    else:
        attacker_hp = sum(
            qty * attacker.unit_hp(uid)
            for uid, qty in attacker.units.items()
            if _classify(uid) in {"principal", "flancos", "longa", "artilharia", "bomb", "aa"}
        )
        defender_hp = sum(
            qty * defender.unit_hp(uid)
            for uid, qty in defender.units.items()
            if _classify(uid) in {"principal", "flancos", "longa", "artilharia", "bomb", "aa"}
        ) + defender.wall_hp
        if attacker_hp > defender_hp * 1.15:
            winner = "attacker"
        elif defender_hp > attacker_hp * 1.15:
            winner = "defender"
        else:
            winner = "draw"

    attacker_losses = {
        uid: qty - attacker.units.get(uid, 0)
        for uid, qty in attacker.initial_units.items()
        if qty - attacker.units.get(uid, 0) > 0
    }
    defender_losses = {
        uid: qty - defender.units.get(uid, 0)
        for uid, qty in defender.initial_units.items()
        if qty - defender.units.get(uid, 0) > 0
    }

    attacker_initial_total = sum(attacker.initial_units.values())
    defender_initial_total = sum(defender.initial_units.values())
    attacker_survivors_pct = round(100.0 * sum(attacker.units.values()) / max(1, attacker_initial_total), 1)
    defender_survivors_pct = round(100.0 * sum(defender.units.values()) / max(1, defender_initial_total), 1)

    return {
        "winner": winner,
        "rounds": rounds_played,
        "field_level": field_level,
        "attacker_initial": attacker_initial_total,
        "defender_initial": defender_initial_total,
        "attacker_losses": attacker_losses,
        "defender_losses": defender_losses,
        "attacker_survivors_pct": attacker_survivors_pct,
        "defender_survivors_pct": defender_survivors_pct,
        "wall_hp_left": defender.wall_hp,
        "details": {
            "field_level": field_level,
            "rounds": round_summaries,
        },
    }


def recommend_attack_force(
    available_units: dict[int, int],
    defender_units: dict[int, int],
    *,
    attacker_upgrades: dict[int, dict] | None = None,
    defender_upgrades: dict[int, dict] | None = None,
    town_hall_level: int = 1,
    wall_level: int = 15,
    max_loss_pct: float = 30.0,
) -> dict[str, Any]:
    if not defender_units or sum(int(v or 0) for v in defender_units.values()) == 0:
        recommended: dict[int, int] = {}
        if available_units.get(303, 0) > 0:
            recommended[303] = min(30, int(available_units[303]))
        elif available_units.get(308, 0) > 0:
            recommended[308] = min(10, int(available_units[308]))

        for siege_id in (305, 306, 307):
            have = int(available_units.get(siege_id, 0) or 0)
            if have > 0:
                recommended[siege_id] = min(6, have)
                break

        return {
            "can_win": True,
            "recommended": _enforce_minimum_raid_force(recommended, available_units),
            "simulation": {
                "winner": "attacker",
                "rounds": 1,
                "field_level": _field_level_from_th(town_hall_level),
                "attacker_survivors_pct": 100.0,
                "defender_survivors_pct": 0.0,
                "attacker_losses": {},
                "defender_losses": {},
            },
        }

    defender_has_bomb = any(int(defender_units.get(uid, 0) or 0) > 0 for uid in LINE_BOMB)
    defender_has_aa = any(int(defender_units.get(uid, 0) or 0) > 0 for uid in LINE_AA)
    defender_has_bomb_targets = any(
        int(defender_units.get(uid, 0) or 0) > 0
        for uid in (LINE_BOMB | LINE_ARTILH | LINE_LONGA)
    )
    field_level = _field_level_from_th(town_hall_level)
    principal_cap = _line_capacity(field_level, "principal")

    def apply_preferences(force: dict[int, int]) -> dict[int, int]:
        out = {int(uid): int(qty) for uid, qty in force.items() if int(qty or 0) > 0}

        if not defender_has_bomb_targets:
            for uid in LINE_BOMB:
                out.pop(uid, None)
        if not defender_has_bomb and not defender_has_aa:
            for uid in LINE_AA:
                out.pop(uid, None)

        hop_available = int(available_units.get(303, 0) or 0)
        if hop_available >= principal_cap:
            out.pop(308, None)

        principal_size = sum(out.get(uid, 0) * _unit_size(uid) for uid in LINE_PRINCIPAL)
        if principal_size < principal_cap:
            for uid in LINE_FLANCOS:
                out.pop(uid, None)

        return out

    full_force = _enforce_minimum_raid_force(apply_preferences(available_units), available_units)
    full_sim = simulate_land_battle(
        full_force,
        defender_units,
        attacker_upgrades=attacker_upgrades,
        defender_upgrades=defender_upgrades,
        town_hall_level=town_hall_level,
        wall_level=wall_level,
    )
    if full_sim["winner"] != "attacker":
        return {
            "can_win": False,
            "recommended": {},
            "simulation": full_sim,
            "note": "Mesmo enviando tudo, nao vence.",
        }

    def build_scaled_force(factor: float) -> dict[int, int]:
        scaled = {
            int(uid): max(1, int(int(qty) * factor))
            for uid, qty in available_units.items()
            if int(qty or 0) > 0
        }
        return _enforce_minimum_raid_force(apply_preferences(scaled), available_units)

    def run_scaled(factor: float) -> tuple[dict[int, int], dict[str, Any]] | None:
        scaled = build_scaled_force(factor)
        if not scaled:
            return None
        sim = simulate_land_battle(
            scaled,
            defender_units,
            attacker_upgrades=attacker_upgrades,
            defender_upgrades=defender_upgrades,
            town_hall_level=town_hall_level,
            wall_level=wall_level,
        )
        return scaled, sim

    best_recommended = dict(full_force)
    best_sim = full_sim
    coarse_factors = (0.05, 0.10, 0.20, 0.35, 0.50, 0.70, 1.00)
    previous_factor = 0.0
    winning_factor: float | None = None
    lower_bound = 0.0
    upper_bound = 1.0

    for factor in coarse_factors:
        result = run_scaled(factor)
        if not result:
            previous_factor = factor
            continue
        scaled, sim = result
        loss_pct = 100.0 - sim["attacker_survivors_pct"]
        if sim["winner"] == "attacker" and loss_pct <= max_loss_pct:
            best_recommended = scaled
            best_sim = sim
            winning_factor = factor
            lower_bound = previous_factor
            upper_bound = factor
            break
        previous_factor = factor

    if winning_factor is not None:
        for _ in range(8):
            probe = (lower_bound + upper_bound) / 2.0
            result = run_scaled(probe)
            if not result:
                lower_bound = probe
                continue
            scaled, sim = result
            loss_pct = 100.0 - sim["attacker_survivors_pct"]
            if sim["winner"] == "attacker" and loss_pct <= max_loss_pct:
                best_recommended = scaled
                best_sim = sim
                upper_bound = probe
            else:
                lower_bound = probe

        # Small downward probes around the best threshold to smooth integer jumps.
        for probe in (
            max(0.0, upper_bound - 0.01),
            max(0.0, upper_bound - 0.02),
            max(0.0, upper_bound - 0.03),
        ):
            result = run_scaled(probe)
            if not result:
                continue
            scaled, sim = result
            loss_pct = 100.0 - sim["attacker_survivors_pct"]
            if sim["winner"] == "attacker" and loss_pct <= max_loss_pct:
                current_total = sum(best_recommended.values())
                candidate_total = sum(scaled.values())
                if candidate_total <= current_total:
                    best_recommended = scaled
                    best_sim = sim

    return {
        "can_win": True,
        "recommended": best_recommended,
        "simulation": best_sim,
    }
