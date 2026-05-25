"""Barbarian village raiding runner — action code 18.

State machine (via reschedule_inputs["phase"]):

  check      → Read island; decide if barbarians can be attacked.
               If village is destroyed (cooldown): wait for respawn.
               If not enough troops: wait and retry.
               Else: fire attack → phase="attacking"

  attacking  → Troops sent. Poll island every N minutes.
               When barbarians.destroyed==True → phase="looting" (if enabled).
               When barbarians.destroyed==False and cooldown==0 → still fighting.
               After max_poll attempts without resolution → fall back to check.

  looting    → Send light loot troops to collect resources.
               Phase → "loot_waiting"

  loot_waiting → Poll island. When troops return / cooldown resets → phase="check".
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.barbarians import ATTACK_SCHEMATICS, LOOT_SCHEMATICS, get_schematic, calculate_transporters
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

# Reschedule back-offs
ERROR_RESCHEDULE = 10 * 60           # 10 min on generic errors
NO_TROOPS_RESCHEDULE = 30 * 60      # 30 min if troops not available
BATTLE_POLL_INTERVAL = 15 * 60      # 15 min per battle round (Ikariam battle phases)
LOOT_POLL_INTERVAL = 8 * 60         # 8 min for loot trip
MAX_ATTACK_POLLS = 8                 # give up after 8 × 15 min = 2 hours


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


@register_runner(18)
class BarbariansRunner(BaseRunner):
    """Raid barbarian villages automatically.

    Inputs:
        city_id          — departing city ID (required)
        island_ids       — comma-separated island IDs to attack in rotation (required)
        troop_mode       — "schematic" | "available" | "custom" (default: schematic)
        custom_troops    — JSON dict {unit_id: qty} used when troop_mode="custom"
        loot             — bool: send loot troops after defeating village (default: True)
        min_barb_level   — minimum barbarian level to attack (default: 1)
        max_barb_level   — maximum barbarian level to attack (default: 99)
        min_resources    — minimum total resources in village to bother attacking (default: 0)
        extra_ships      — extra cargo ships beyond auto-calculated minimum (default: 0)
        recheck_minutes  — how often to poll during "attacking" phase (default: 15)
        max_random_wait  — random anti-detection wait in seconds before each attack (default: 0)

    State (persisted in reschedule_inputs):
        phase            — current phase: "check" | "attacking" | "looting" | "loot_waiting"
        island_index     — current rotation index into island_ids list
        attack_polls     — how many times we've polled the "attacking" phase
        last_barb_level  — barbarian level at time of last attack
        total_attacks    — lifetime attack counter
        total_loots      — lifetime loot counter
        last_attack_at   — unix timestamp of last attack sent
        last_loot_at     — unix timestamp of last loot sent
        last_resources_looted — last recorded total_resources from barbarian dict
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        game_account_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        # ── Configuration ──
        city_id = str(inputs.get("city_id") or "").strip()
        island_ids_raw = str(inputs.get("island_ids") or inputs.get("island_id") or "").strip()

        if not city_id:
            self.log(jid, "error", "city_id obrigatório para roubar bárbaros")
            return RunnerResult(success=False, data={"error": "city_id_missing"})
        if not island_ids_raw:
            self.log(jid, "error", "island_ids obrigatório — informe IDs das ilhas separados por vírgula")
            return RunnerResult(success=False, data={"error": "island_ids_missing"})

        island_ids = [s.strip() for s in island_ids_raw.split(",") if s.strip()]
        troop_mode = str(inputs.get("troop_mode") or "schematic").strip().lower()
        custom_troops_json = str(inputs.get("custom_troops") or "")
        do_loot = bool(inputs.get("loot") if inputs.get("loot") is not None else True)
        min_barb_level = _parse_int(inputs.get("min_barb_level"), 1)
        max_barb_level = _parse_int(inputs.get("max_barb_level"), 99)
        min_resources = _parse_int(inputs.get("min_resources"), 0)
        extra_ships = _parse_int(inputs.get("extra_ships"), 0)
        max_random_wait = _parse_int(inputs.get("max_random_wait"), 0)

        try:
            recheck_minutes = max(5, _parse_int(inputs.get("recheck_minutes"), 15))
        except Exception:
            recheck_minutes = 15
        poll_interval = recheck_minutes * 60

        # ── State ──
        phase = str(inputs.get("phase") or "check").strip()
        island_index = _parse_int(inputs.get("island_index"), 0) % len(island_ids)
        attack_polls = _parse_int(inputs.get("attack_polls"), 0)
        total_attacks = _parse_int(inputs.get("total_attacks"), 0)
        total_loots = _parse_int(inputs.get("total_loots"), 0)

        def _state_carry(**extra) -> dict:
            """Build reschedule_inputs with all persistent state."""
            return {
                "city_id": city_id,
                "island_ids": island_ids_raw,
                "troop_mode": troop_mode,
                "custom_troops": custom_troops_json,
                "loot": do_loot,
                "min_barb_level": min_barb_level,
                "max_barb_level": max_barb_level,
                "min_resources": min_resources,
                "extra_ships": extra_ships,
                "recheck_minutes": recheck_minutes,
                "max_random_wait": max_random_wait,
                "island_index": island_index,
                "attack_polls": attack_polls,
                "total_attacks": total_attacks,
                "total_loots": total_loots,
                **extra,
            }

        current_island_id = island_ids[island_index]

        # ── Login ──
        creds = self.resolve_credentials(aid, inputs, game_account_id=game_account_id)
        if not creds:
            self.log(jid, "error", "Credenciais não encontradas")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE,
                                data={"error": "credentials_missing"})

        try:
            client = self.get_or_login_game_client(jid, aid, game_account_id, creds)

            # ─── Phase: check ───────────────────────────────────────────────
            if phase == "check":
                return self._phase_check(
                    jid, client, city_id, current_island_id, island_ids, island_index,
                    troop_mode, custom_troops_json, do_loot, min_barb_level, max_barb_level,
                    min_resources, extra_ships, max_random_wait, poll_interval,
                    total_attacks, total_loots, _state_carry, game_account_id,
                )

            # ─── Phase: attacking ────────────────────────────────────────────
            elif phase == "attacking":
                return self._phase_attacking(
                    jid, client, current_island_id, island_ids, island_index,
                    do_loot, extra_ships, max_random_wait, poll_interval,
                    attack_polls, total_attacks, total_loots, city_id, _state_carry,
                    game_account_id, inputs,
                )

            # ─── Phase: looting ──────────────────────────────────────────────
            elif phase == "looting":
                return self._phase_loot(
                    jid, client, city_id, current_island_id, island_ids, island_index,
                    extra_ships, poll_interval, total_attacks, total_loots, _state_carry,
                    inputs, game_account_id,
                )

            # ─── Phase: loot_waiting ─────────────────────────────────────────
            elif phase == "loot_waiting":
                return self._phase_loot_waiting(
                    jid, client, current_island_id, island_ids, island_index,
                    poll_interval, total_attacks, total_loots, city_id, _state_carry,
                    game_account_id,
                )

            else:
                self.log(jid, "warn", f"Fase desconhecida '{phase}' — resetando para check")
                self.save_game_client(game_account_id, client)
                return RunnerResult(success=True, reschedule_seconds=60,
                                    reschedule_inputs=_state_carry(phase="check", attack_polls=0))

        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
            self.log(jid, "error", f"Bárbaros: erro inesperado: {exc}")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE,
                                data={"error": str(exc)})

    # ─────────────────────────────────────────────────────────────────────────
    # Internal phase handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _phase_check(
        self, jid, client, city_id, island_id, island_ids, island_index,
        troop_mode, custom_troops_json, do_loot, min_barb_level, max_barb_level,
        min_resources, extra_ships, max_random_wait, poll_interval,
        total_attacks, total_loots, state, game_account_id,
    ) -> RunnerResult:
        self.log(jid, "info", f"[check] Lendo bárbaros da ilha {island_id}")

        barb = client.get_barbarian_state(island_id)
        if not barb:
            self.log(jid, "warn", f"Ilha {island_id} sem dados de bárbaros — aguardando 30min")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=30 * 60,
                                reschedule_inputs=state(phase="check"))

        barb_level = int(barb.get("level") or 0)
        destroyed = bool(barb.get("destroyed"))
        cooldown = int(barb.get("cooldown_seconds") or 0)
        total_res = int(barb.get("total_resources") or 0)

        self.log(
            jid, "info",
            f"[check] Bárbaros nível {barb_level} — "
            f"{'destruídos' if destroyed else 'ativos'} — "
            f"recursos: {total_res} — cooldown: {cooldown}s",
        )

        # Village still in cooldown (destroyed by us or someone else)
        if destroyed and cooldown > 0:
            wait = min(cooldown + 60, 3 * 3600)
            self.log(jid, "info", f"[check] Vila destruída, respawn em {cooldown}s. Aguardando.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=wait,
                                reschedule_inputs=state(phase="check"))

        # Level filter
        if barb_level < min_barb_level:
            self.log(jid, "info", f"[check] Nível {barb_level} < mínimo {min_barb_level}. Aguardando 1h.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=3600,
                                reschedule_inputs=state(phase="check"))

        if barb_level > max_barb_level:
            self.log(jid, "info", f"[check] Nível {barb_level} > máximo {max_barb_level}. Aguardando 1h.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=3600,
                                reschedule_inputs=state(phase="check"))

        # Resource threshold
        if min_resources > 0 and total_res < min_resources:
            self.log(jid, "info",
                     f"[check] Recursos {total_res} < mínimo {min_resources}. Aguardando 2h.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=2 * 3600,
                                reschedule_inputs=state(phase="check"))

        # Resolve troops — need current counts from barracks
        available = self._get_available_troops(jid, client, city_id)
        troops = _resolve_troops(troop_mode, barb_level, custom_troops_json, available)

        if troops is None:
            self.log(jid, "warn",
                     f"[check] Tropas insuficientes para nível {barb_level}. "
                     f"Aguardando {NO_TROOPS_RESCHEDULE // 60}min.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=NO_TROOPS_RESCHEDULE,
                                reschedule_inputs=state(phase="check"))

        # Anti-detection random wait
        if max_random_wait > 0:
            wait_secs = random.randint(0, max_random_wait)
            if wait_secs > 0:
                self.log(jid, "info", f"[check] Espera aleatória anti-detecção: {wait_secs}s")
                time.sleep(wait_secs)

        # Fire attack
        transporters = calculate_transporters(troops, extra_ships)
        self.log(
            jid, "info",
            f"[check] Atacando! Ilha {island_id}, nível {barb_level}, "
            f"tropas={troops}, navios={transporters}",
        )

        result = client.attack_barbarian_village(
            from_city_id=city_id,
            island_id=island_id,
            troops=troops,
            transporters=transporters,
        )

        if not result.get("ok"):
            err = result.get("error") or "erro desconhecido"
            self.log(jid, "warn", f"[check] Ataque falhou: {err}. Reagendando em 10min.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE,
                                data={"error": err},
                                reschedule_inputs=state(phase="check"))

        total_attacks += 1
        self.log(jid, "info",
                 f"[check] Ataque enviado! Total ataques: {total_attacks}. "
                 f"Aguardando {poll_interval // 60}min para verificar resultado.")

        self.save_game_client(game_account_id, client)
        return RunnerResult(
            success=True,
            reschedule_seconds=poll_interval,
            reschedule_inputs=state(
                phase="attacking",
                attack_polls=0,
                total_attacks=total_attacks,
                last_barb_level=barb_level,
                last_attack_at=int(time.time()),
            ),
        )

    def _phase_attacking(
        self, jid, client, island_id, island_ids, island_index,
        do_loot, extra_ships, max_random_wait, poll_interval,
        attack_polls, total_attacks, total_loots, city_id, state,
        game_account_id, inputs,
    ) -> RunnerResult:
        self.log(jid, "info", f"[attacking] Verificando ilha {island_id} (poll {attack_polls + 1}/{MAX_ATTACK_POLLS})")

        barb = client.get_barbarian_state(island_id)
        if not barb:
            self.log(jid, "warn", "[attacking] Sem dados de bárbaros. Aguardando.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=poll_interval,
                                reschedule_inputs=state(phase="attacking", attack_polls=attack_polls + 1))

        destroyed = bool(barb.get("destroyed"))
        cooldown = int(barb.get("cooldown_seconds") or 0)
        total_res = int(barb.get("total_resources") or 0)

        self.log(jid, "info",
                 f"[attacking] destroyed={destroyed} cooldown={cooldown}s recursos={total_res}")

        if destroyed:
            if do_loot:
                self.log(jid, "info", "[attacking] Vila destruída. Iniciando fase de saque.")
                self.save_game_client(game_account_id, client)
                return RunnerResult(
                    success=True, reschedule_seconds=30,
                    reschedule_inputs=state(phase="looting", attack_polls=0,
                                            last_resources_looted=total_res),
                )
            else:
                # No loot phase — rotate island and go back to check
                next_index = (island_index + 1) % len(island_ids)
                self.log(jid, "info",
                         f"[attacking] Vila destruída. Saque desativado. "
                         f"Próxima ilha: {island_ids[next_index]}")
                self.save_game_client(game_account_id, client)
                return RunnerResult(
                    success=True, reschedule_seconds=60,
                    reschedule_inputs=state(phase="check", attack_polls=0, island_index=next_index),
                )

        attack_polls += 1
        if attack_polls >= MAX_ATTACK_POLLS:
            self.log(jid, "warn",
                     f"[attacking] Máximo de {MAX_ATTACK_POLLS} polls atingido sem resultado. "
                     "Resetando para check.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=poll_interval,
                                reschedule_inputs=state(phase="check", attack_polls=0))

        self.log(jid, "info", f"[attacking] Batalha ainda em curso. Verificando em {poll_interval // 60}min.")
        self.save_game_client(game_account_id, client)
        return RunnerResult(success=True, reschedule_seconds=poll_interval,
                            reschedule_inputs=state(phase="attacking", attack_polls=attack_polls))

    def _phase_loot(
        self, jid, client, city_id, island_id, island_ids, island_index,
        extra_ships, poll_interval, total_attacks, total_loots, state,
        inputs, game_account_id,
    ) -> RunnerResult:
        barb_level = _parse_int(inputs.get("last_barb_level"), 1)
        self.log(jid, "info",
                 f"[looting] Enviando saqueadores para ilha {island_id} (nível {barb_level})")

        # Read current barbarian state to verify it's destroyed
        barb = client.get_barbarian_state(island_id)
        if barb and not barb.get("destroyed"):
            self.log(jid, "warn", "[looting] Bárbaros não estão destruídos. Voltando para check.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=poll_interval,
                                reschedule_inputs=state(phase="check", attack_polls=0))

        # Send light loot troops
        loot_troops = get_schematic(barb_level, LOOT_SCHEMATICS)
        transporters = calculate_transporters(loot_troops, extra_ships)

        result = client.attack_barbarian_village(
            from_city_id=city_id,
            island_id=island_id,
            troops=loot_troops,
            transporters=transporters,
        )

        if not result.get("ok"):
            err = result.get("error") or "erro"
            self.log(jid, "warn", f"[looting] Saque falhou: {err}. Tentando novamente em 10min.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=False, reschedule_seconds=10 * 60,
                                data={"error": err},
                                reschedule_inputs=state(phase="looting"))

        total_loots += 1
        self.log(jid, "info",
                 f"[looting] Saqueadores enviados! Total saques: {total_loots}. "
                 f"Aguardando {LOOT_POLL_INTERVAL // 60}min.")

        self.save_game_client(game_account_id, client)
        return RunnerResult(
            success=True,
            reschedule_seconds=LOOT_POLL_INTERVAL,
            reschedule_inputs=state(phase="loot_waiting", total_loots=total_loots,
                                    last_loot_at=int(time.time())),
        )

    def _phase_loot_waiting(
        self, jid, client, island_id, island_ids, island_index,
        poll_interval, total_attacks, total_loots, city_id, state, game_account_id,
    ) -> RunnerResult:
        self.log(jid, "info", f"[loot_waiting] Verificando conclusão do saque na ilha {island_id}")

        barb = client.get_barbarian_state(island_id)
        cooldown = int((barb or {}).get("cooldown_seconds") or 0)
        destroyed = bool((barb or {}).get("destroyed"))

        if not destroyed or cooldown <= 30:
            # Village respawned or loot complete — move to next island
            next_index = (island_index + 1) % len(island_ids)
            self.log(jid, "info",
                     f"[loot_waiting] Saque concluído! Próxima ilha: {island_ids[next_index]}")
            self.save_game_client(game_account_id, client)
            return RunnerResult(
                success=True,
                reschedule_seconds=30,
                reschedule_inputs=state(phase="check", attack_polls=0, island_index=next_index),
            )

        self.log(jid, "info",
                 f"[loot_waiting] Vila ainda destruída (cooldown={cooldown}s). "
                 f"Verificando em {LOOT_POLL_INTERVAL // 60}min.")
        self.save_game_client(game_account_id, client)
        return RunnerResult(success=True, reschedule_seconds=LOOT_POLL_INTERVAL,
                            reschedule_inputs=state(phase="loot_waiting"))

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
