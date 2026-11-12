"""Roubar Bárbaros — action code 19 (N-179, rebuilt on what the game showed).

Every island where the player has a town has its own barbarian village, with its own level (1 to 50). A town attacks
the village of its own island: its troops weigh nothing there (no ship for them) and arrive in under a minute; the
merchant ships only bring back the loot (500 each). After a win the village is rebuilt; while it is destroyed what
was left can still be collected. From level 10 the barbarians may take revenge (raid a town, occupy the mine).

One job looks after several islands at once. Each round, for each island (from the town picked for it):
  under attack       -> the battle is on, wait;
  destroyed          -> once per destruction, if "loot" is on, send one unit with ships to collect what was left;
  ready              -> within the levels chosen, attack with the troops chosen and ships for the loot.
Nothing in the game is instant: the troops walk to the village, the battle goes in rounds, the merchant ships come
back with the loot. While any of that is on its way (the military advisor's movements from or to the town) the island
waits, the loot is counted from the cargo the ships bring home, and the job wakes up when the next movement lands or
the village is rebuilt (never later than `recheck_minutes`).
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.barbarians import (
    ATTACK_SCHEMATICS, SHIP_CAPACITY, calculate_transporters, fetch_village_info, get_schematic, loot_ships_for_level,
)
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

ERROR_RESCHEDULE = 10 * 60
LOOT_UNIT_ORDER = (315, 301, 302, 303, 313, 304)   # the cheapest unit that can walk to collect the loot
SUPPORT_UNITS = {310, 311}                          # cook and doctor do not fight
VILLAGE_SCOUTS = (1, 2, 5, 8, 15, 20, 25, 35, 50)    # defenders of levels 1-9 (wiki: Barbarian Kings)
FRONT_ORDER = (303, 302, 315, 316, 319, 301, 313, 304)  # who holds the front line against the scouts


def _parse_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _resolve_troops(
    troop_mode: str,
    barb_level: int,
    custom_troops_json: str,
    available_counts: dict[int, int],
) -> dict[int, int] | None:
    """Return troop dict to send, or None if not enough troops available.

    Returns None when available_counts doesn't satisfy minimum requirements.
    """
    if troop_mode == "custom":
        try:
            custom = json.loads(custom_troops_json or "{}")
            troops = {int(k): int(v) for k, v in custom.items() if int(v) > 0}
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("Barbarians: invalid custom_troops_json — falling back to schematic")
            troops = get_schematic(barb_level, ATTACK_SCHEMATICS)
    elif troop_mode == "available":
        # Use everything available (all unit types with count > 0)
        troops = {uid: cnt for uid, cnt in available_counts.items() if cnt > 0 and 302 <= uid <= 315}
        if not troops:
            return None
        return troops
    else:
        # Default: schematic
        troops = get_schematic(barb_level, ATTACK_SCHEMATICS)

    if not troops:
        return None

    # Check availability: every required unit must be present in sufficient quantity
    for uid, required in troops.items():
        have = available_counts.get(uid, 0)
        if have < required:
            return None  # Not enough troops

    return troops


def _choose_troops(mode: str, level: int, custom_json: str, available: dict[int, int]) -> dict[int, int] | None:
    """The troops of one attack: the ones chosen (custom), everything that fights (available, and any village of
    levels 1-9, which only has a few scouts), or the table for the level (from 10 on)."""
    fighters = {uid: n for uid, n in available.items() if n > 0 and 301 <= uid <= 321 and uid not in SUPPORT_UNITS}
    if mode == "custom":
        return _resolve_troops("custom", level, custom_json, available)
    if mode == "available":
        return fighters or None
    if level <= 9:
        # Olaf's scouts (wiki): twice their number plus five, from the front-line infantry first
        need = 2 * VILLAGE_SCOUTS[level - 1] + 5
        picked = {}
        for uid in FRONT_ORDER:
            take = min(fighters.get(uid, 0), need - sum(picked.values()))
            if take > 0:
                picked[uid] = take
        return picked if sum(picked.values()) >= VILLAGE_SCOUTS[level - 1] + 1 else None
    return _resolve_troops("schematic", level, custom_json, available)


def _n(value) -> str:
    """1234567 -> 1.234.567, as the game writes it."""
    return f"{int(value or 0):,}".replace(",", ".")


_GOODS_PT = {"wood": "madeira", "wine": "vinho", "marble": "mármore", "glass": "cristal", "sulfur": "enxofre", "gold": "ouro"}


def _units_text(units: dict) -> str:
    return ", ".join(f"{_n(qty)} {_GOODS_PT.get(name, name)}" for name, qty in (units or {}).items())


def _cargo_total(cargo: dict) -> tuple[int, int]:
    """(resources, gold) carried by one movement."""
    gold = sum(int(v or 0) for k, v in (cargo or {}).items() if "gold" in str(k).lower())
    return sum(int(v or 0) for v in (cargo or {}).values()) - gold, gold


def _barbarian_movements(movements: list[dict], town: str) -> list[dict]:
    """The movements of one town against its village: they leave from the town (or come back to it) and the other end
    is not a player's town (the village has no city id) or says "barbarian"."""
    out = []
    for m in movements or []:
        if not isinstance(m, dict) or m.get("is_own") is False:
            continue
        origin, target = m.get("origin") or {}, m.get("target") or {}
        o_city, t_city = str(origin.get("cityId") or ""), str(target.get("cityId") or "")
        if town not in (o_city, t_city):
            continue
        other = t_city if o_city == town else o_city
        text = json.dumps([m.get("mission"), m.get("mission_text"), target, origin], ensure_ascii=False).lower()
        if other in ("", "0") or "barb" in text or "bárb" in text:
            out.append(m)
    return out


def _loot_unit(available: dict[int, int]) -> dict[int, int] | None:
    for uid in LOOT_UNIT_ORDER:
        if available.get(uid, 0) > 0:
            return {uid: 1}
    fighters = [uid for uid, n in available.items() if n > 0 and uid not in SUPPORT_UNITS]
    return {fighters[0]: 1} if fighters else None


@register_runner(19)
class BarbariansRunner(BaseRunner):
    """Raid the barbarian villages of the account's islands, each from a town of that island."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        game_account_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        city_id = str(inputs.get("city_id") or "").strip()
        island_ids = [s.strip() for s in str(inputs.get("island_ids") or inputs.get("island_id") or "").split(",") if s.strip()]
        try:
            island_cities = {str(k): str(v) for k, v in json.loads(inputs.get("island_cities") or "{}").items()}
        except (TypeError, ValueError, AttributeError):
            island_cities = {}
        if not island_ids:
            self.log(jid, "error", "Escolha ao menos uma ilha.")
            return RunnerResult(success=False, data={"error": "island_ids_missing"})
        troop_mode = str(inputs.get("troop_mode") or "schematic").strip().lower()
        custom_troops_json = str(inputs.get("custom_troops") or "")
        do_loot = str(inputs.get("loot", True)).lower() not in {"false", "0", "no", "off"}
        min_level = _parse_int(inputs.get("min_barb_level"), 1)
        max_level = _parse_int(inputs.get("max_barb_level"), 99)
        extra_ships = _parse_int(inputs.get("extra_ships"), 0)
        max_random_wait = _parse_int(inputs.get("max_random_wait"), 0)
        recheck = max(5, _parse_int(inputs.get("recheck_minutes"), 15)) * 60
        dry_run = str(inputs.get("dry_run") or "").lower() in {"1", "true", "on", "yes"}
        state = inputs.get("islands_state") if isinstance(inputs.get("islands_state"), dict) else {}
        wake: list[int] = []          # when a movement lands or a village is rebuilt
        totals = {key: _parse_int(inputs.get(key), 0) for key in ("total_attacks", "total_loots", "loot_total", "gold_total")}

        creds = self.resolve_credentials(aid, inputs, game_account_id=game_account_id)
        if not creds:
            self.log(jid, "error", "Credenciais não encontradas")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE, data={"error": "credentials_missing"})
        try:
            client = self.get_or_login_game_client(jid, aid, game_account_id, creds)
            if dry_run:
                return self._dry_run(jid, client, city_id, island_ids, troop_mode, custom_troops_json, extra_ships,
                                     game_account_id)
            for island_id in island_ids:
                town = island_cities.get(island_id) or city_id
                if not town:
                    self.log(jid, "warn", f"Ilha {island_id}: nenhuma cidade escolhida para atacar.")
                    continue
                try:
                    st = state.setdefault(island_id, {})
                    if self._on_the_way(jid, client, island_id, town, st, totals, wake):
                        continue
                    self._island_round(jid, client, island_id, town, st, totals,
                                       troop_mode, custom_troops_json, do_loot, min_level, max_level, extra_ships,
                                       max_random_wait)
                    if st.get("status") == "rebuilding" and int(st.get("rebuild_end") or 0) > 0:
                        wake.append(int(st["rebuild_end"]) + 30)
                    if st.get("status") in ("attacking", "collecting"):
                        wake.append(int(time.time()) + 90)        # look at the movement it just started
                except Exception as exc:  # noqa: BLE001 - one island must not stop the others
                    if self.is_network_error(exc):
                        raise
                    self.log(jid, "warn", f"Ilha {island_id}: {exc}")
            self.save_game_client(game_account_id, client)
        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
            self.log(jid, "error", f"Bárbaros: erro inesperado: {exc}")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE, data={"error": str(exc)})

        now = int(time.time())
        soon = [t - now for t in wake if t > now]
        next_in = max(60, min([recheck] + soon))
        if next_in < recheck:
            self.log(jid, "info", f"Próxima olhada em {next_in // 60} min {next_in % 60} s (quando o próximo movimento chega).")
        return RunnerResult(success=True, reschedule_seconds=next_in,
                            reschedule_inputs={**inputs, "islands_state": state, **totals, "phase": "rounds"})

    def _on_the_way(self, jid, client, island_id, town, st, totals, wake) -> bool:
        """True while troops or ships of this town are going to or coming back from its village: count the loot the
        ships bring home (once per movement) and wait for the next arrival."""
        try:
            advisor = client.fetch_military_advisor(int(town))
        except Exception as exc:  # noqa: BLE001 - without the movements the village state still guides the round
            if self.is_network_error(exc):
                raise
            self.log(jid, "warn", f"Ilha {island_id}: movimentos não abriram ({exc}).")
            return False
        moving = _barbarian_movements(advisor.get("movement_details") or [], town)
        if moving and not st.get("movement_logged"):
            st["movement_logged"] = True
            self.log(jid, "info", f"Ilha {island_id}: movimento lido {json.dumps(moving[0], ensure_ascii=False)[:700]}")
        counted = list(st.get("counted_events") or [])
        now = int(time.time())
        lines = []
        for m in moving:
            eta = int(m.get("event_time") or 0)
            if eta > now:
                wake.append(eta + 20)
            resources, gold = _cargo_total(m.get("cargo") or {})
            key = str(m.get("event_id") or "")
            if m.get("is_returning") and (resources or gold) and key and key not in counted:
                counted.append(key)
                st["looted"] = int(st.get("looted") or 0) + resources
                st["gold"] = int(st.get("gold") or 0) + gold
                st["cargo_counted"] = True
                totals["loot_total"] = int(totals.get("loot_total") or 0) + resources
                totals["gold_total"] = int(totals.get("gold_total") or 0) + gold
                self.log(jid, "info", f"Ilha {island_id}: os mercantes voltam com {_n(resources)} recursos"
                                      f"{f' e {_n(gold)} de ouro' if gold else ''} ({_units_text(m.get('cargo'))}).")
            when = time.strftime("%H:%M:%S", time.localtime(eta)) if eta else "?"
            lines.append(f"{'voltando' if m.get('is_returning') else 'indo'} (chega {when}, faltam {max(0, eta - now) // 60} min)")
        st["counted_events"] = counted[-20:]
        if not moving:
            return False
        st["status"] = "moving"
        self.log(jid, "info", f"Ilha {island_id}: tropas/mercantes em movimento: " + "; ".join(lines) + ". Espero chegarem.")
        return True

    def _island_round(self, jid, client, island_id, town, st, totals, troop_mode, custom_json, do_loot, min_level,
                      max_level, extra_ships, max_random_wait) -> None:
        barb = client.get_barbarian_state(island_id)
        if not barb:
            self.log(jid, "warn", f"Ilha {island_id}: sem vila de bárbaros visível (a conta precisa ter cidade lá).")
            return
        level = int(barb.get("level") or 1)
        st["level"] = level
        st["town"] = town
        if barb.get("under_attack"):
            st["status"] = "battle"
            self.log(jid, "info", f"Ilha {island_id}: batalha em andamento contra a vila nível {level}.")
            return
        village = {}
        try:
            village = fetch_village_info(client, island_id)
        except Exception as exc:  # noqa: BLE001 - without it the level table estimates the loot
            self.log(jid, "warn", f"Ilha {island_id}: tela da vila não abriu ({exc}); uso a tabela de saque.")
        if barb.get("destroyed"):
            st["status"] = "rebuilding"
            st["rebuild_end"] = village.get("rebuild_end") or st.get("rebuild_end") or 0
            left = village.get("total")
            if left is not None and st.get("left_before") is not None and not st.get("cargo_counted"):
                # what disappeared from the village since the last look was taken by us
                taken = max(0, int(st["left_before"]) - int(left))
                st["looted"] = int(st.get("looted") or 0) + taken
                totals["loot_total"] = int(totals.get("loot_total") or 0) + taken
                if st.get("gold_pending"):
                    st["gold"] = int(st.get("gold") or 0) + int(st["gold_pending"])
                    totals["gold_total"] = int(totals.get("gold_total") or 0) + int(st["gold_pending"])
                    st["gold_pending"] = 0
            if left is not None:
                st["left_before"] = int(left)
            if do_loot and left and int(left) > 0:
                available = self._get_available_troops(jid, client, town)
                unit = _loot_unit(available)
                if unit:
                    ships = max(1, -(-int(left) // SHIP_CAPACITY))
                    result = client.attack_barbarian_village(from_city_id=town, island_id=island_id, troops=unit,
                                                             transporters=ships)
                    totals["total_loots"] += 1
                    st["loots"] = int(st.get("loots") or 0) + 1
                    st["status"] = "collecting"
                    st["cargo_counted"] = False
                    self.log(jid, "info", f"Ilha {island_id}: vila destruída com {_n(left)} recursos; coleta com "
                                          f"{result['transporters']} mercantes.")
                    return
            self.log(jid, "info", f"Ilha {island_id}: vila em reconstrução (nível {level})"
                                  + (f", {_n(left)} recursos ainda lá." if left else "."))
            return
        st["status"] = "ready"
        st["cargo_counted"] = False
        if village.get("defenders"):
            st["defenders"] = village["defenders"]
        st["left_before"] = None
        st["rebuild_end"] = 0
        if level < min_level or level > max_level:
            self.log(jid, "info", f"Ilha {island_id}: vila nível {level} fora da faixa {min_level}-{max_level}.")
            return
        available = self._get_available_troops(jid, client, town)
        troops = _choose_troops(troop_mode, level, custom_json, available)
        if not troops:
            self.log(jid, "info", f"Ilha {island_id}: tropas insuficientes em {town} para o nível {level} ({available}).")
            return
        if max_random_wait > 0:
            time.sleep(random.randint(0, max_random_wait))
        on_hand = int(village.get("total") or 0)
        ships = (max(1, -(-on_hand // SHIP_CAPACITY)) if on_hand else loot_ships_for_level(level)) + max(0, extra_ships)
        result = client.attack_barbarian_village(from_city_id=town, island_id=island_id, troops=troops, transporters=ships)
        totals["total_attacks"] += 1
        st.update(attacked_at=int(time.time()), attacked_level=level, status="attacking", attacks=int(st.get("attacks") or 0) + 1,
                  last_troops={str(k): v for k, v in troops.items()}, last_ships=result["transporters"],
                  village_resources=on_hand, village_gold=int(village.get("gold") or 0),
                  left_before=on_hand if on_hand else None, gold_pending=int(village.get("gold") or 0))
        warn = " Atenção: a partir do nível 10 os bárbaros podem se vingar." if level >= 10 else ""
        if village.get("defenders"):
            warn = " Defensores: " + ", ".join(f"{_n(q)} {name}" for name, q in village["defenders"]) + "." + warn
        self.log(jid, "info", f"Ilha {island_id}: ataque à vila nível {level} (tem {_n(on_hand)} recursos e "
                              f"{_n(village.get('gold'))} de ouro) saindo de {town} com {troops} e "
                              f"{result['transporters']} mercantes (de {result.get('free_ships')} livres).{warn}")

    def _dry_run(self, jid, client, city_id, island_ids, troop_mode, custom_troops_json, extra_ships,
                 game_account_id) -> RunnerResult:
        """N-179: read everything an attack would use and log it, sending nothing (only to check the game)."""
        from game_client.actions.barbarians import read_attack_view

        available = self._get_available_troops(jid, client, city_id)
        self.log(jid, "info", f"[só conferir] Tropas na cidade {city_id}: {available}")
        for island_id in island_ids[:3]:
            self.log(jid, "info", f"[só conferir] Ilha {island_id}: tela da vila={json.dumps(fetch_village_info(client, island_id), ensure_ascii=False)[:900]}")
            barb = client.get_barbarian_state(island_id)
            self.log(jid, "info", f"[só conferir] Ilha {island_id}: bárbaros={json.dumps(barb, ensure_ascii=False)[:900]}")
            level = int((barb or {}).get("level") or 1)
            troops = _resolve_troops(troop_mode, level, custom_troops_json, available)
            ships = calculate_transporters(troops, extra_ships) if troops else 0
            self.log(jid, "info", f"[só conferir] Ilha {island_id}: mandaria tropas={troops} navios={ships} (tabela atual)")
            for view in read_attack_view(client, city_id, island_id):
                self.log(jid, "info", f"[só conferir] Tela {json.dumps(view, ensure_ascii=False)[:2800]}")
        self.save_game_client(game_account_id, client)
        self.log(jid, "info", "[só conferir] Nada foi enviado. Fim.")
        return RunnerResult(success=True, data={"status": "dry_run"})

    def _get_available_troops(
        self, jid: str, client, city_id: str
    ) -> dict[int, int]:
        """Fetch stationed troop counts from cityMilitary."""
        try:
            result = client.fetch_stationed_units(int(city_id), building_type="troops")
            return result.get("counts") or {}
        except Exception as exc:
            self.log(jid, "warn", f"Falha ao ler tropas disponíveis: {exc}")
            return {}
