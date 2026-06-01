"""Simulação de batalha terrestre do Ikariam.

Modela:
  - Linhas do campo (slots × capacidade) conforme CM (Câmara Municipal)
  - Classes de unidades (infantaria pesada/ligeira, longa distância, artilharia,
    bombardeiros, anti-aérea, bagagem)
  - Reserva: tropas excedentes esperam vagas
  - Bombardeiros priorizam artilharia → longa → principal → flancos
  - Anti-aérea só intercepta bombardeiros
  - Artilharia ataca muralha (ignorada aqui) ou linha principal
  - Flancos: ignorados quando há muralha; defensor sem flancos válidos por simplificação
  - Cozinheiros/Médicos: aplicam bônus de HP às tropas vivas (efeito %)
  - Munição finita pra unidades com ammo (longa distância, bombardeiros, artilharia)
  - Round-by-round até alguém sem unidades de combate
"""

from __future__ import annotations

import math
from typing import Any

from .unit_stats import UNIT_STATS, IMPROVEMENT_BONUS_LAND


# ── Classificação por linha ─────────────────────────────────────────────────
LINE_PRINCIPAL = {303, 308}             # Hoplita, Gigante a Vapor
LINE_FLANCOS   = {302, 315, 320, 321}   # Espadachim, Lanceiro, Membro Bárb, Faqueiro
LINE_LONGA     = {301, 304, 313}        # Fundeiro, Carabineiro, Arqueiro
LINE_ARTILH    = {305, 306, 307}        # Morteiro, Catapulta, Aríete
LINE_BOMB      = {309}                  # Balão-Bombardeiro
LINE_AA        = {312}                  # Girocóptero
LINE_BAGAGEM   = {310, 311}             # Cozinheiro (HP+) / Médico (cura)


# ── Capacidade por nível de campo (= nível da Câmara Municipal) ─────────────
# {field_level: {linha: (n_slots, cap_por_slot)}}
# Flancos têm 2 lados; aqui combinamos cap_total = n_slots×cap×2 (esquerdo+direito).
FIELD_SLOTS: dict[int, dict[str, tuple[int, int]]] = {
    1:  {"principal": (3, 30), "flancos": (2, 10), "longa": (3, 30), "artilharia": (1, 30),
         "bomb": (1, 10), "aa": (1, 10)},
    5:  {"principal": (5, 30), "flancos": (2, 30), "longa": (5, 30), "artilharia": (2, 30),
         "bomb": (1, 20), "aa": (1, 20)},
    10: {"principal": (7, 30), "flancos": (4, 30), "longa": (7, 30), "artilharia": (3, 30),
         "bomb": (1, 30), "aa": (1, 30)},
    17: {"principal": (7, 40), "flancos": (6, 30), "longa": (7, 40), "artilharia": (4, 30),
         "bomb": (2, 20), "aa": (2, 20)},
    25: {"principal": (7, 50), "flancos": (6, 40), "longa": (7, 50), "artilharia": (5, 30),
         "bomb": (2, 30), "aa": (2, 30)},
}


# Muralha HP por nível (aproximado — escala quadrática)
def _wall_hp(level: int) -> int:
    lv = max(0, int(level or 0))
    if lv == 0:
        return 0
    return int(500 + 200 * lv * lv / 10)  # lv1≈520, lv5≈1000, lv10≈2500, lv15≈5000, lv20≈8500


def _field_level_from_th(town_hall_level: int) -> int:
    """CM level → field level threshold."""
    th = int(town_hall_level or 1)
    if th >= 25: return 25
    if th >= 17: return 17
    if th >= 10: return 10
    if th >= 5:  return 5
    return 1


def _line_capacity(field_level: int, line: str) -> int:
    f = FIELD_SLOTS.get(field_level) or FIELD_SLOTS[1]
    s, cap = f.get(line) or (0, 0)
    return s * cap


def _classify(unit_id: int) -> str:
    if unit_id in LINE_PRINCIPAL: return "principal"
    if unit_id in LINE_FLANCOS:   return "flancos"
    if unit_id in LINE_LONGA:     return "longa"
    if unit_id in LINE_ARTILH:    return "artilharia"
    if unit_id in LINE_BOMB:      return "bomb"
    if unit_id in LINE_AA:        return "aa"
    if unit_id in LINE_BAGAGEM:   return "bagagem"
    return "outros"


# ── Stats por unit com upgrades ─────────────────────────────────────────────
def _hp(unit_id: int, def_level: int) -> int:
    u = UNIT_STATS.get(unit_id) or {}
    base = int(u.get("hp", 0))
    armor_bonus = int(def_level) * IMPROVEMENT_BONUS_LAND
    return base + armor_bonus * 10  # armor ≈ HP equivalente (simplificação)


def _damage_per_unit(unit_id: int, weapon_index: int, off_level: int) -> int:
    u = UNIT_STATS.get(unit_id) or {}
    weapons = u.get("weapons") or [{}]
    w = weapons[weapon_index] if weapon_index < len(weapons) else weapons[0]
    base = int(w.get("damage", 0))
    precision = float(w.get("precision", 100)) / 100.0
    bonus = int(off_level) * IMPROVEMENT_BONUS_LAND
    return int((base + bonus) * precision)


def _ammo(unit_id: int, weapon_index: int) -> int | None:
    u = UNIT_STATS.get(unit_id) or {}
    weapons = u.get("weapons") or [{}]
    w = weapons[weapon_index] if weapon_index < len(weapons) else weapons[0]
    if "ammo" in w:
        return int(w["ammo"])
    return None


# ── Army state ──────────────────────────────────────────────────────────────
class _Army:
    """Estado de exército com posicionamento por linha + reserva + dano cumulativo."""

    def __init__(self, units: dict[int, int], upgrades: dict[int, dict],
                 field_level: int, is_defender: bool, wall_hp: int = 0):
        self.units = {uid: int(qty) for uid, qty in units.items() if int(qty) > 0}
        self.upgrades = upgrades or {}
        self.field_level = field_level
        self.is_defender = is_defender
        # Muralha (só defender): HP buffer absorve dano direcionado à linha principal.
        self.wall_hp = int(wall_hp) if is_defender else 0
        # Munição restante (apenas por unit_id que tem ammo)
        self.ammo_left: dict[int, int] = {}
        for uid in self.units:
            a = _ammo(uid, 1) if (UNIT_STATS.get(uid, {}).get("weapons", [{}])[0].get("ammo") is None
                                  and len((UNIT_STATS.get(uid, {}).get("weapons") or [])) > 1) else _ammo(uid, 0)
            if a is not None:
                self.ammo_left[uid] = a * self.units[uid]
        # Dano residual por linha (acumula entre rounds quando < HP de 1 unidade)
        self.pending_damage: dict[str, int] = {}
        self._initial_count = dict(self.units)

    def total_units_alive(self) -> int:
        return sum(self.units.values())

    def _classify_promoted(self, unit_id: int) -> str:
        """Classifica unidade considerando promoção: flancos sobe pra principal
        se não há unidades principais (regra Ikariam — flancos fillam main vazia).
        """
        cls = _classify(unit_id)
        if cls == "flancos":
            has_principal = any(self.units.get(u, 0) > 0 for u in LINE_PRINCIPAL)
            if not has_principal:
                return "principal"
        return cls

    def line_units(self, line: str) -> dict[int, int]:
        """Retorna unidades em uma linha (limitado pela capacidade).
        Aplica promoção: flancos sobem pra principal se principal vazia.
        """
        cap = _line_capacity(self.field_level, line) if line != "bagagem" else 10**9
        result: dict[int, int] = {}
        used = 0
        for uid, qty in self.units.items():
            if self._classify_promoted(uid) != line:
                continue
            available = max(0, cap - used)
            if available <= 0:
                break
            take = min(qty, available)
            if take > 0:
                result[uid] = take
                used += take
        return result

    def _off_level(self, uid: int) -> int:
        return int((self.upgrades.get(uid) or {}).get("offensive", 0))

    def _def_level(self, uid: int) -> int:
        return int((self.upgrades.get(uid) or {}).get("defensive", 0))

    def line_dps(self, line: str, weapon_index: int = 0) -> tuple[float, int]:
        """DPS total da linha (com upgrades) + número de unidades efetivas (com munição).
        Quando ammo zera, unidade usa weapon[0] (corpo-a-corpo) — Ikariam real.
        """
        line_units = self.line_units(line)
        dps = 0.0
        active = 0
        for uid, qty in line_units.items():
            actual_weapon = weapon_index
            # ammo check — só relevante pra weapon_index >= 1 (arma com ammo)
            if uid in self.ammo_left and weapon_index >= 1:
                shots_left = self.ammo_left[uid]
                if shots_left <= 0:
                    # Sem ammo → cai pro corpo-a-corpo (weapon[0]) com dano bem menor
                    actual_weapon = 0
                    effective_qty = qty
                else:
                    effective_qty = min(qty, shots_left)
                    self.ammo_left[uid] = shots_left - effective_qty
            else:
                effective_qty = qty
            dmg = _damage_per_unit(uid, actual_weapon, self._off_level(uid))
            dps += effective_qty * dmg
            active += effective_qty
        return dps, active

    def line_hp(self, line: str) -> int:
        units = self.line_units(line)
        total = 0
        for uid, qty in units.items():
            total += _hp(uid, self._def_level(uid)) * qty
        # Bagagem (cozinheiro = +HP %, médico = cura): aumenta HP em ~5% por bagagem
        bagagem_count = sum(self.units.get(uid, 0) for uid in LINE_BAGAGEM)
        if bagagem_count > 0:
            # Cap 30% bônus
            bonus = min(0.30, 0.005 * bagagem_count)
            total = int(total * (1 + bonus))
        return total

    def apply_damage_to_line(self, line: str, damage: int) -> int:
        """Aplica dano à linha, removendo unidades. Acumula resto entre rounds. Retorna baixas."""
        if damage <= 0:
            return 0
        # Soma resto do round anterior
        remaining_dmg = damage + self.pending_damage.get(line, 0)
        # Muralha absorve antes (só defender, só linha principal/flancos/longa)
        if self.is_defender and self.wall_hp > 0 and line in ("principal", "flancos", "longa"):
            absorbed = min(self.wall_hp, remaining_dmg)
            self.wall_hp -= absorbed
            remaining_dmg -= absorbed
        line_units_dict = self.line_units(line)
        killed_total = 0
        for uid in list(line_units_dict.keys()):
            qty_in_line = line_units_dict[uid]
            unit_hp = _hp(uid, self._def_level(uid))
            if unit_hp <= 0:
                continue
            killed = min(qty_in_line, remaining_dmg // unit_hp)
            if killed > 0:
                self.units[uid] = max(0, self.units[uid] - killed)
                if self.units[uid] == 0:
                    del self.units[uid]
                remaining_dmg -= killed * unit_hp
                killed_total += killed
        # Guarda resíduo pro próximo round
        self.pending_damage[line] = max(0, remaining_dmg)
        return killed_total


# ── Simulação principal ─────────────────────────────────────────────────────
def simulate_land_battle(
    attacker_units: dict[int, int],
    defender_units: dict[int, int],
    *,
    attacker_upgrades: dict[int, dict] | None = None,
    defender_upgrades: dict[int, dict] | None = None,
    town_hall_level: int = 1,
    wall_level: int = 15,
    max_rounds: int = 12,
) -> dict[str, Any]:
    """Simula batalha terrestre round-by-round.

    Args:
        attacker_units, defender_units: {unit_id: qty}
        attacker_upgrades, defender_upgrades: {unit_id: {offensive: N, defensive: N}}
        town_hall_level: CM do defensor (define field_level)

    Returns:
        {
            "winner": "attacker" | "defender" | "draw",
            "rounds": int,
            "attacker_initial": int (total units),
            "defender_initial": int,
            "attacker_losses": dict[unit_id, qty_morta],
            "defender_losses": dict[unit_id, qty_morta],
            "attacker_survivors_pct": float (0-100),
            "defender_survivors_pct": float (0-100),
            "details": str (resumo legível)
        }
    """
    field = _field_level_from_th(town_hall_level)
    wall_hp = _wall_hp(wall_level)
    att = _Army(dict(attacker_units), attacker_upgrades or {}, field, is_defender=False)
    dfn = _Army(dict(defender_units), defender_upgrades or {}, field, is_defender=True, wall_hp=wall_hp)

    att_init = {uid: qty for uid, qty in att.units.items()}
    dfn_init = {uid: qty for uid, qty in dfn.units.items()}
    att_init_total = sum(att_init.values())
    dfn_init_total = sum(dfn_init.values())

    if att_init_total == 0:
        return {"winner": "defender", "rounds": 0, "attacker_initial": 0, "defender_initial": dfn_init_total,
                "attacker_losses": {}, "defender_losses": {},
                "attacker_survivors_pct": 0.0, "defender_survivors_pct": 100.0,
                "details": "Atacante sem tropas"}

    if dfn_init_total == 0:
        return {"winner": "attacker", "rounds": 0, "attacker_initial": att_init_total, "defender_initial": 0,
                "attacker_losses": {}, "defender_losses": {},
                "attacker_survivors_pct": 100.0, "defender_survivors_pct": 0.0,
                "details": "Defensor sem tropas"}

    round_num = 0
    while round_num < max_rounds:
        round_num += 1

        # ── 1. Bombardeiros vs alvos prioritários (art → longa → principal → flancos) ─
        # AA intercepta primeiro
        att_bomb_dps, _ = att.line_dps("bomb")
        dfn_aa_dps,  _  = dfn.line_dps("aa")
        # AA reduz bomb damage proporcionalmente (DPS AA / HP médio bomb)
        if dfn_aa_dps > 0 and att_bomb_dps > 0:
            bomb_hp = att.line_hp("bomb")
            if bomb_hp > 0:
                aa_intercept_ratio = min(1.0, dfn_aa_dps / bomb_hp)
                att_bomb_dps *= (1 - aa_intercept_ratio)
            att.apply_damage_to_line("bomb", int(dfn_aa_dps))

        # Mesmo do lado defensor
        dfn_bomb_dps, _ = dfn.line_dps("bomb")
        att_aa_dps,  _  = att.line_dps("aa")
        if att_aa_dps > 0 and dfn_bomb_dps > 0:
            bomb_hp_d = dfn.line_hp("bomb")
            if bomb_hp_d > 0:
                ratio = min(1.0, att_aa_dps / bomb_hp_d)
                dfn_bomb_dps *= (1 - ratio)
            dfn.apply_damage_to_line("bomb", int(att_aa_dps))

        # Bombardeiros aplicam dano em cascata
        for target_line in ("artilharia", "longa", "principal", "flancos"):
            if att_bomb_dps <= 0: break
            hp_remaining = dfn.line_hp(target_line)
            if hp_remaining <= 0: continue
            dmg = min(att_bomb_dps, hp_remaining)
            dfn.apply_damage_to_line(target_line, int(dmg))
            att_bomb_dps -= dmg
        for target_line in ("artilharia", "longa", "principal", "flancos"):
            if dfn_bomb_dps <= 0: break
            hp_remaining = att.line_hp(target_line)
            if hp_remaining <= 0: continue
            dmg = min(dfn_bomb_dps, hp_remaining)
            att.apply_damage_to_line(target_line, int(dmg))
            dfn_bomb_dps -= dmg

        # ── 2. Artilharia → linha principal inimiga (muralha ignorada por simplificação) ─
        att_art_dps, _ = att.line_dps("artilharia", weapon_index=1)
        dfn_art_dps, _ = dfn.line_dps("artilharia", weapon_index=1)
        if att_art_dps > 0:
            dfn.apply_damage_to_line("principal", int(att_art_dps))
        if dfn_art_dps > 0:
            att.apply_damage_to_line("principal", int(dfn_art_dps))

        # ── 3. Longa distância → linha principal inimiga ─
        att_lng_dps, _ = att.line_dps("longa", weapon_index=1)
        dfn_lng_dps, _ = dfn.line_dps("longa", weapon_index=1)
        if att_lng_dps > 0:
            dfn.apply_damage_to_line("principal", int(att_lng_dps))
        if dfn_lng_dps > 0:
            att.apply_damage_to_line("principal", int(dfn_lng_dps))

        # ── 4. Linha principal vs principal ─
        att_pri_dps, _ = att.line_dps("principal")
        dfn_pri_dps, _ = dfn.line_dps("principal")
        if att_pri_dps > 0:
            dfn.apply_damage_to_line("principal", int(att_pri_dps))
        if dfn_pri_dps > 0:
            att.apply_damage_to_line("principal", int(dfn_pri_dps))

        # ── 5. Flancos (sem muralha — simplificação) — atacam flancos inimigo
        att_fl_dps, _ = att.line_dps("flancos")
        dfn_fl_dps, _ = dfn.line_dps("flancos")
        if att_fl_dps > 0:
            dfn.apply_damage_to_line("flancos", int(att_fl_dps))
        if dfn_fl_dps > 0:
            att.apply_damage_to_line("flancos", int(dfn_fl_dps))

        # ── Critério de fim ─
        # Real Ikariam: batalha termina quando um lado fica sem PRINCIPAL (incl. flancos
        # promovidos). Sem main, exército sem proteção e bate em retirada.
        att_principal = sum(att.line_units("principal").values())
        dfn_principal = sum(dfn.line_units("principal").values())
        att_combat = sum(att.units.get(u, 0) for u in (
            list(LINE_PRINCIPAL) + list(LINE_FLANCOS) + list(LINE_LONGA)
            + list(LINE_ARTILH) + list(LINE_BOMB) + list(LINE_AA)
        ))
        dfn_combat = sum(dfn.units.get(u, 0) for u in (
            list(LINE_PRINCIPAL) + list(LINE_FLANCOS) + list(LINE_LONGA)
            + list(LINE_ARTILH) + list(LINE_BOMB) + list(LINE_AA)
        ))
        # Sem main de nenhum lado → empate
        if att_combat == 0 and dfn_combat == 0:
            winner = "draw"
            break
        # Atacante sem principal/flancos (sem main promovida) = retira
        if att_principal == 0:
            winner = "defender"
            break
        # Defensor sem principal (e sem flancos pra promover) = exército quebra
        if dfn_principal == 0:
            winner = "attacker"
            break
        if att_combat == 0:
            winner = "defender"
            break
        if dfn_combat == 0:
            winner = "attacker"
            break
    else:
        # max_rounds atingido — winner por HP de combate restante (ignora bagagem)
        combat_ids = LINE_PRINCIPAL | LINE_FLANCOS | LINE_LONGA | LINE_ARTILH | LINE_BOMB | LINE_AA
        att_hp_left = sum(_hp(u, att._def_level(u)) * att.units.get(u, 0) for u in combat_ids)
        dfn_hp_left = sum(_hp(u, dfn._def_level(u)) * dfn.units.get(u, 0) for u in combat_ids) + dfn.wall_hp
        if att_hp_left > dfn_hp_left * 1.2:
            winner = "attacker"
        elif dfn_hp_left > att_hp_left * 1.2:
            winner = "defender"
        else:
            winner = "draw"

    # Calcular perdas
    att_losses: dict[int, int] = {}
    for uid, init in att_init.items():
        rem = att.units.get(uid, 0)
        if init - rem > 0:
            att_losses[uid] = init - rem
    dfn_losses: dict[int, int] = {}
    for uid, init in dfn_init.items():
        rem = dfn.units.get(uid, 0)
        if init - rem > 0:
            dfn_losses[uid] = init - rem

    att_surv_pct = round(100 * sum(att.units.values()) / att_init_total, 1) if att_init_total > 0 else 0.0
    dfn_surv_pct = round(100 * sum(dfn.units.values()) / dfn_init_total, 1) if dfn_init_total > 0 else 0.0

    return {
        "winner": winner,
        "rounds": round_num,
        "field_level": field,
        "attacker_initial": att_init_total,
        "defender_initial": dfn_init_total,
        "attacker_losses": att_losses,
        "defender_losses": dfn_losses,
        "attacker_survivors_pct": att_surv_pct,
        "defender_survivors_pct": dfn_surv_pct,
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
    """Recomenda força mínima de ataque (bottom-up): escala de pouco até vencer com margem.

    Estratégia: testa 5%, 10%, 20%, 35%, 50%, 70%, 100% do disponível. Para no menor que
    ganha com perdas ≤ max_loss_pct. Garante mínimo de bagagem (cozinheiro/médico) e bomb/AA
    se disponíveis (utilitários).
    """
    # Sanity check: defender vazio? envia força simbólica
    if not defender_units or sum(defender_units.values()) == 0:
        token = {}
        for uid in (303, 301):  # 1 hop + 1 fundeiro só pra plunder
            if available_units.get(uid, 0) > 0:
                token[uid] = min(10, available_units[uid])
        return {"can_win": True, "recommended": token, "simulation": {
            "winner": "attacker", "rounds": 1, "attacker_survivors_pct": 100.0,
            "attacker_losses": {}, "defender_losses": {}, "field_level": _field_level_from_th(town_hall_level),
        }}

    # Tenta 100% primeiro só pra confirmar viabilidade
    full = simulate_land_battle(
        available_units, defender_units,
        attacker_upgrades=attacker_upgrades,
        defender_upgrades=defender_upgrades,
        town_hall_level=town_hall_level,
        wall_level=wall_level,
    )
    if full["winner"] != "attacker":
        return {
            "can_win": False,
            "recommended": {},
            "simulation": full,
            "note": "Mesmo enviando tudo, não vence.",
        }

    # Capacidade da linha principal (pra decidir se flancos valem a pena)
    field = _field_level_from_th(town_hall_level)
    principal_cap = _line_capacity(field, "principal")

    # Regras (preferência do usuário):
    # 1. Hoplita preferido sobre Gigante — Gigante só se Hoplita não chega na cap
    # 2. Flanco só com principal cheia
    # 3. Balão só se defensor tem Balão (Balão é lento, dobra viagem)
    # 4. Carabineiro/Fundeiro/Arqueiro (longa) só se útil
    defender_has_bomb = any(defender_units.get(u, 0) > 0 for u in LINE_BOMB)

    def _apply_preferences(force: dict[int, int]) -> dict[int, int]:
        out = dict(force)
        # Regra 3: Balão só se inimigo tem Balão (Balão é lento, dobra viagem)
        if not defender_has_bomb:
            for u in LINE_BOMB:
                out.pop(u, None)
        # Regra 1: Hoplita preferido — se Hop disponível ≥ principal_cap, remove Gigante.
        # NÃO força mais Hop; deixa o bottom-up encontrar a quantidade mínima.
        hop_avail = available_units.get(303, 0)
        if hop_avail >= principal_cap:
            out.pop(308, None)
        # Regra 2: flanco só com principal cheia
        principal_qty = sum(out.get(u, 0) for u in LINE_PRINCIPAL)
        if principal_qty < principal_cap:
            for u in LINE_FLANCOS:
                out.pop(u, None)
        return {u: q for u, q in out.items() if q > 0}

    # Bottom-up: menor força que vence com perdas aceitáveis
    best_recommended = _apply_preferences(dict(available_units))
    best_sim = full
    for factor in (0.05, 0.10, 0.20, 0.35, 0.50, 0.70):
        scaled = {uid: max(1, int(qty * factor)) for uid, qty in available_units.items() if qty > 0}
        scaled = _apply_preferences(scaled)
        sim = simulate_land_battle(
            scaled, defender_units,
            attacker_upgrades=attacker_upgrades,
            defender_upgrades=defender_upgrades,
            town_hall_level=town_hall_level,
            wall_level=wall_level,
        )
        att_loss_pct = 100 - sim["attacker_survivors_pct"]
        if sim["winner"] == "attacker" and att_loss_pct <= max_loss_pct:
            best_recommended = scaled
            best_sim = sim
            break

    return {
        "can_win": True,
        "recommended": best_recommended,
        "simulation": best_sim,
    }
