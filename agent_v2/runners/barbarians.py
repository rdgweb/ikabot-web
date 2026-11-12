"""Roubar Bárbaros — action code 19 (N-179, rebuilt on what the game showed).

Every island where the player has a town has its own barbarian village, with its own level (1 to 50). A town attacks
the village of its own island: its troops weigh nothing there (no ship for them) and arrive in under a minute; the
merchant ships only bring back the loot (500 each). After a win the village is rebuilt; while it is destroyed what
was left can still be collected. From level 10 the barbarians may take revenge (raid a town, occupy the mine).

One job looks after several islands at once. Each round, for each island (from the town picked for it):
  under attack       -> the battle is on, wait;
  destroyed          -> once per destruction, if "loot" is on, send one unit with ships to collect what was left;
  ready              -> within the levels chosen, attack with the troops chosen and ships for the loot.
Then it comes back after `recheck_minutes`.
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
                    self._island_round(jid, client, island_id, town, state.setdefault(island_id, {}), totals,
                                       troop_mode, custom_troops_json, do_loot, min_level, max_level, extra_ships,
                                       max_random_wait)
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

        return RunnerResult(success=True, reschedule_seconds=recheck,
                            reschedule_inputs={**inputs, "islands_state": state, **totals, "phase": "rounds"})

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
            if left is not None and st.get("left_before") is not None:
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
                    self.log(jid, "info", f"Ilha {island_id}: vila destruída com {int(left):,} recursos; coleta com "
                                          f"{result['transporters']} mercantes.".replace(",", "."))
                    return
            self.log(jid, "info", f"Ilha {island_id}: vila em reconstrução (nível {level})"
                                  + (f", {int(left):,} recursos ainda lá.".replace(",", ".") if left else "."))
            return
        st["status"] = "ready"
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
        self.log(jid, "info", f"Ilha {island_id}: ataque à vila nível {level} (tem {on_hand:,} recursos e "
                              f"{int(village.get('gold') or 0):,} de ouro) saindo de {town} com {troops} e "
                              f"{result['transporters']} mercantes (de {result.get('free_ships')} livres).{warn}".replace(",", "."))

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
