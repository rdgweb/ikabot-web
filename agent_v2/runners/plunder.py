"""
Runner de Saque de Cidade (ac=1008) — RaidCityRunner.

Máquina de estados baseada em polling do Military Advisor:

  Phase "send":
    1. Auto-selecionar cidade de origem se não especificada
    2. Enviar blockade (se configurado) + plunder terrestre
    3. Parsear ETA de chegada do scatteredUnitsSidebar
    4. Reagendar para ETA + buffer (fase "check_battle")

  Phase "check_battle":
    5. Fetch militaryAdvisor → has_active_battle?
    6. Se sim → reagendar +15min (ciclo de batalha)
    7. Se não → batalha resolvida → buscar relatório de combate
    8. Parsear saque real (loot dict)
    9. Se derrota → encerrar ou tentar com mais tropas
    10. Se vitória → checar multi_trip e recursos restantes → "send" ou encerrar

Inputs:
    source_city_id          str  — cidade de origem (auto-selecionada se vazio)
    target_city_id          str  — cidade alvo (obrigatório)
    island_id               str  — ilha da cidade alvo (obrigatório)
    units                   dict — {unit_id: qty}; vazio = auto
    transporters            int  — navios mercantes (0 = live count)
    needs_blockade          bool — enviar blockade antes do plunder
    blockade_fleet_units    dict — {ship_id: qty} para blockade (vazio = auto)
    multi_trip              bool — continuar após vitória
    max_trips               int  — limite de viagens
    min_resources_to_continue int — parar se recursos < X
    # Internos (gerenciados pelo runner)
    _phase                  str  — "send" | "check_battle"
    _trips_done             int  — contador de viagens
    _travel_seconds         int  — ETA em segundos (calculado na 1ª viagem)
    _eta_timestamp          str  — "DD.MM.YYYY H:MM:SS" do advisor
    _source_city_id         str  — cidade escolhida automaticamente
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from services.combat import (
    format_battle_summary,
    loot_capacity,
    pick_minimum_siege,
    recommend_army,
    transporters_needed,
    trips_needed,
)

logger = logging.getLogger(__name__)

BATTLE_CYCLE_SECONDS  = 15 * 60   # cada ciclo de batalha = 15 min
RETURN_BUFFER_SECONDS = 5  * 60   # buffer após estimativa de retorno
ERROR_RESCHEDULE      = 5  * 60


def _parse_units(raw) -> dict[int, int]:
    """Parse units from dict (str or int keys) → {int(unit_id): qty}."""
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return {int(k): int(v) for k, v in raw.items() if int(v) > 0}


def _parse_int(value, default=0) -> int:
    try:
        return int(value) if value is not None and str(value).strip() != "" else default
    except (TypeError, ValueError):
        return default


@register_runner(1008)
class RaidCityRunner(BaseRunner):
    """Ataca e saqueia cidade inimiga. Máquina de estados via Military Advisor."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid    = job["job_id"]
        aid    = str(job.get("account_id") or "").strip()
        ga_id  = str(job.get("game_account_id") or "").strip()
        inputs = job.get("inputs") or {}

        target_city_id = str(inputs.get("target_city_id") or "").strip()
        island_id      = str(inputs.get("island_id")      or "").strip()
        if not target_city_id or not island_id:
            self.log(jid, "error", "target_city_id e island_id são obrigatórios.")
            return RunnerResult(success=False)

        phase = str(inputs.get("_phase") or "send").strip()

        if phase == "check_battle":
            return self._phase_check_battle(jid, ga_id, aid, inputs)
        elif phase == "wait_blockade":
            return self._phase_wait_blockade(jid, ga_id, aid, inputs)
        elif phase == "recall_fleet":
            return self._phase_recall_fleet(jid, ga_id, aid, inputs)
        else:
            return self._phase_send(jid, ga_id, aid, inputs)

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE: send — selecionar tropas, enviar plunder + blockade, agendar check
    # ══════════════════════════════════════════════════════════════════════════

    def _phase_send(self, jid: str, ga_id: str, aid: str, inputs: dict) -> RunnerResult:
        target_city_id = str(inputs.get("target_city_id") or "").strip()
        island_id      = str(inputs.get("island_id")      or "").strip()
        multi_trip     = str(inputs.get("multi_trip", "true")).lower() not in {"false", "0", "no"}
        max_trips      = _parse_int(inputs.get("max_trips"), 10)
        min_res_cont   = _parse_int(inputs.get("min_resources_to_continue"), 0)
        trips_done     = _parse_int(inputs.get("_trips_done"), 0)
        source_city_id = str(inputs.get("_source_city_id") or inputs.get("source_city_id") or "").strip()

        if trips_done >= max_trips:
            self.log(jid, "info", f"[Raid] Limite {max_trips} viagens atingido. Encerrando.")
            return RunnerResult(success=True)

        snap  = self._get_snapshot(jid, ga_id)
        units = _parse_units(inputs.get("units") or {})

        # Auto-selecionar cidade de origem
        if not source_city_id:
            sel = self._auto_select_source_city(jid, snap, units, inputs)
            if not sel:
                self.log(jid, "error", "[Raid] Sem cidade/tropas disponíveis.")
                return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)
            source_city_id = sel["city_id"]
            if not units:
                units = sel["units"]
            self.log(jid, "info",
                     f"[Raid] Origem: {sel.get('city_name', source_city_id)} "
                     f"tropas={sum(units.values())} mercantes={sel.get('merchants',0)}")

        available = self._get_available_units(jid, snap, source_city_id)
        if not units:
            units = self._auto_calculate_units(jid, inputs, available)
            if not units:
                self.log(jid, "error", "[Raid] Sem tropas para enviar.")
                return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)

        # Verificar recursos restantes (viagens 2+)
        if trips_done > 0 and min_res_cont > 0:
            intel = self._check_remaining_resources(jid, ga_id, target_city_id)
            if intel is not None and intel.get("total", 0) < min_res_cont:
                self.log(jid, "info",
                         f"[Raid] Recursos {intel.get('total',0):,} < mínimo {min_res_cont:,}. "
                         f"Encerrando após {trips_done} viagem(ns).")
                return RunnerResult(success=True)

        try:
            client = self._get_client(jid, ga_id, aid, inputs)
        except Exception as exc:
            self.log(jid, "error", f"[Raid] Falha de sessão: {exc}")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)

        # Transportadores live
        transporters = _parse_int(inputs.get("transporters"), 0)
        if transporters <= 0:
            transporters = self._get_transporters(snap, source_city_id, client=client, jid=jid)

        # Blockade PRIMEIRO (se configurado) — sempre na primeira viagem.
        # Não depende de enemy_fleet — se needs_blockade=True, vai bloquear.
        needs_blockade = bool(inputs.get("needs_blockade", False))
        if needs_blockade and _parse_int(inputs.get("_trips_done"), 0) == 0:
            blockade_fleet = _parse_units(inputs.get("blockade_fleet_units") or {})
            if not blockade_fleet:
                blockade_fleet = self._auto_select_fleet(jid, snap, source_city_id)
            if blockade_fleet:
                try:
                    # 1. Pegar ETA da frota ANTES de enviar (do HTML do form de blockade)
                    fleet_travel_secs = 0
                    try:
                        bv = client.fetch_blockade_view(
                            from_city_id=int(source_city_id),
                            to_city_id=int(target_city_id),
                            island_id=int(island_id),
                        )
                        fleet_travel_secs = bv.get("travel_seconds", 0)
                        if fleet_travel_secs:
                            self.log(jid, "info",
                                     f"[Raid] ETA frota (do form): {fleet_travel_secs//60}min {fleet_travel_secs%60}s")
                    except Exception as exc:
                        self.log(jid, "warn", f"[Raid] Falha ao ler ETA blockade: {exc}")

                    # 2. Enviar blockade (fetch_blockade_view já navega para a cidade)
                    client.blockade_fleet(
                        from_city_id=int(source_city_id),
                        to_city_id=int(target_city_id),
                        island_id=int(island_id),
                        fleet_units=blockade_fleet,
                    )
                    fleet_wait = (fleet_travel_secs + 120) if fleet_travel_secs > 30 else (5 * 60)
                    self.log(jid, "info",
                             f"[Raid] ⚓ Blockade enviado: {blockade_fleet}. "
                             f"Verificando porto em {fleet_wait//60}min.")
                    return RunnerResult(
                        success=True,
                        reschedule_seconds=fleet_wait,
                        reschedule_inputs={
                            **inputs,
                            "_phase": "wait_blockade",
                            "_source_city_id": source_city_id,
                            "units": {str(k): v for k, v in units.items()},
                        },
                    )
                except Exception as exc:
                    self.log(jid, "warn",
                             f"[Raid] Blockade falhou: {exc}. Prosseguindo sem bloqueio.")

        # Enviar plunder
        cap = loot_capacity(transporters)
        self.log(jid, "info",
                 f"[Raid] Viagem {trips_done+1}/{max_trips} | alvo={target_city_id} | "
                 f"tropas={dict(units)} | transportadores={transporters} ({cap:,} cap)")
        try:
            client.plunder_land(
                from_city_id=int(source_city_id),
                to_city_id=int(target_city_id),
                island_id=int(island_id),
                units=units,
                transporters=transporters,
            )
        except Exception as exc:
            self.log(jid, "error", f"[Raid] Plunder falhou: {exc}")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)

        # ETA das tropas vem do fetch_plunder_view (missionController.transportJourneyTime)
        # NÃO usar o military advisor aqui — ele mistura frotas e tropas no mesmo timestamp.
        # O advisor é usado apenas para checar ESTADO (batalha ativa, porto ocupado).
        travel_seconds = _parse_int(inputs.get("_travel_seconds"), 0)
        if not travel_seconds:
            # Buscar do form de plunder (mais preciso que advisor)
            try:
                pv = client.fetch_plunder_view(
                    int(source_city_id), int(target_city_id), int(island_id)
                )
                travel_seconds = pv.get("travel_seconds", 0)
                if travel_seconds:
                    self.log(jid, "info",
                             f"[Raid] Tempo de viagem das tropas: {travel_seconds//60}min {travel_seconds%60}s")
            except Exception as exc:
                self.log(jid, "warn", f"[Raid] Falha ao ler ETA das tropas: {exc}")

        # Agendar check_battle: tempo de ida + 2min buffer (chegada → início da batalha)
        check_delay = max((travel_seconds + 120) if travel_seconds > 30 else 300, 300)
        self.log(jid, "info",
                 f"[Raid] Exército enviado. Verificando batalha em {check_delay//60}min {check_delay%60}s.")
        return RunnerResult(
            success=True,
            reschedule_seconds=int(check_delay),
            reschedule_inputs={
                **inputs,
                "_phase":           "check_battle",
                "_trips_done":      trips_done,
                "_travel_seconds":  travel_seconds,
                "_source_city_id":  source_city_id,
                "units":            {str(k): v for k, v in units.items()},
            },
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE: recall_fleet — chamar frota de volta após raids concluídos
    # ══════════════════════════════════════════════════════════════════════════

    def _phase_recall_fleet(self, jid: str, ga_id: str, aid: str, inputs: dict) -> RunnerResult:
        source_city_id = str(inputs.get("_source_city_id") or inputs.get("source_city_id") or "").strip()
        target_city_id = str(inputs.get("target_city_id") or "").strip()

        self.log(jid, "info",
                 f"[Raid] Chamando frota de volta do porto bloqueado ({target_city_id}).")
        try:
            client = self._get_client(jid, ga_id, aid, inputs)
            result = client.recall_blockade_fleet(
                source_city_id=int(source_city_id),
                enemy_city_id=int(target_city_id),
            )
            if result.get("ok"):
                self.log(jid, "info",
                         f"[Raid] ✓ Frota chamada de volta. Passos: {result.get('steps')}")
            else:
                self.log(jid, "warn",
                         f"[Raid] Falha ao chamar frota: {result.get('error')}. "
                         f"Passos: {result.get('steps')}. Precisará ser chamada manualmente.")
        except Exception as exc:
            self.log(jid, "warn", f"[Raid] Erro ao chamar frota: {exc}")

        return RunnerResult(success=True)

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE: wait_blockade — aguardar porto ser ocupado antes de enviar plunder
    # ══════════════════════════════════════════════════════════════════════════

    def _phase_wait_blockade(self, jid: str, ga_id: str, aid: str, inputs: dict) -> RunnerResult:
        source_city_id = str(inputs.get("_source_city_id") or inputs.get("source_city_id") or "").strip()

        try:
            client = self._get_client(jid, ga_id, aid, inputs)
            advisor = client.fetch_military_advisor(int(source_city_id))
        except Exception as exc:
            self.log(jid, "warn", f"[Raid] Falha ao verificar advisor (wait_blockade): {exc}")
            # Prosseguir mesmo sem confirmar — melhor tentar do que travar
            return RunnerResult(
                success=True,
                reschedule_seconds=60,
                reschedule_inputs={**inputs, "_phase": "send"},
            )

        port_occupied = advisor.get("port_occupied", False)
        self.log(jid, "info", f"[Raid] wait_blockade: porto_ocupado={port_occupied}")

        if port_occupied:
            self.log(jid, "info", "[Raid] Porto ocupado ✓ Enviando plunder terrestre.")
            return RunnerResult(
                success=True,
                reschedule_seconds=30,
                reschedule_inputs={**inputs, "_phase": "send"},
            )

        # Frota ainda viajando — verificar de novo em 5min
        self.log(jid, "info", "[Raid] Porto ainda não ocupado. Aguardando 5min.")
        return RunnerResult(
            success=True,
            reschedule_seconds=5 * 60,
            reschedule_inputs=inputs,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE: check_battle — polling do Military Advisor após chegada
    # ══════════════════════════════════════════════════════════════════════════

    def _phase_check_battle(self, jid: str, ga_id: str, aid: str, inputs: dict) -> RunnerResult:
        source_city_id = str(inputs.get("_source_city_id") or inputs.get("source_city_id") or "").strip()
        target_city_id = str(inputs.get("target_city_id") or "").strip()
        trips_done     = _parse_int(inputs.get("_trips_done"), 0)
        travel_seconds = _parse_int(inputs.get("_travel_seconds"), 3600)
        multi_trip     = str(inputs.get("multi_trip", "true")).lower() not in {"false", "0", "no"}

        try:
            client = self._get_client(jid, ga_id, aid, inputs)
        except Exception as exc:
            self.log(jid, "error", f"[Raid] Falha de sessão em check_battle: {exc}")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)

        try:
            advisor = client.fetch_military_advisor(int(source_city_id))
        except Exception as exc:
            self.log(jid, "warn", f"[Raid] Falha ao buscar advisor: {exc}. Retry em 5min.")
            return RunnerResult(success=True, reschedule_seconds=ERROR_RESCHEDULE, reschedule_inputs=inputs)

        has_battle = advisor.get("has_active_battle", False)
        eta_ts     = advisor.get("eta_timestamp")

        self.log(jid, "info",
                 f"[Raid] check_battle: batalha_ativa={has_battle} "
                 f"eta={eta_ts} porto_bloqueado={advisor.get('port_occupied',False)}")

        # Se ainda tem batalha ativa → reagendar +15min (ciclo de batalha)
        if has_battle:
            self.log(jid, "info", "[Raid] Batalha em andamento. Aguardando 15min (próximo ciclo).")
            return RunnerResult(
                success=True,
                reschedule_seconds=BATTLE_CYCLE_SECONDS,
                reschedule_inputs=inputs,
            )

        # Batalha resolvida — buscar relatório mais recente contra o alvo
        report = self._find_latest_combat_report(jid, client, source_city_id, target_city_id, ga_id=ga_id)
        if report:
            if report.get("army_lost"):
                self.log(jid, "warn",
                         f"[Raid] Derrota no combate {report.get('combat_id')}. "
                         f"Exército perdido. Encerrando.")
                return RunnerResult(success=False)

            loot = report.get("loot") or {}
            total_loot = report.get("total_loot", 0)
            self.log(jid, "info",
                     f"[Raid] Vitória! combatId={report.get('combat_id')} "
                     f"rounds={report.get('rounds')} saque={loot} total={total_loot:,}")

            # Se navios cheios → provavelmente mais recursos → re-raid
            snap = self._get_snapshot(jid, ga_id)
            transporters_live = self._get_transporters(snap, source_city_id, client=client, jid=jid)
            capacity = loot_capacity(transporters_live or 1)
            ships_full = total_loot >= capacity * 0.9 if capacity > 0 else False
        else:
            self.log(jid, "info", "[Raid] Sem relatório recente. Assumindo que terminou.")
            ships_full = False

        # Se ETA ainda no futuro (exército voltando) → aguardar retorno antes do próximo raid
        if eta_ts:
            return_secs = self._parse_eta_to_seconds(eta_ts)
            if return_secs > 60:
                self.log(jid, "info",
                         f"[Raid] Exército voltando. Aguardando {return_secs//60}min.")
                next_inputs = {**inputs, "_phase": "send", "_trips_done": trips_done + 1}
                return RunnerResult(
                    success=True,
                    reschedule_seconds=int(return_secs + RETURN_BUFFER_SECONDS),
                    reschedule_inputs=next_inputs,
                )

        # Exército voltou — decidir sobre próxima viagem
        trips_done += 1
        max_trips = _parse_int(inputs.get("max_trips"), 10)
        if multi_trip and trips_done < max_trips and ships_full:
            self.log(jid, "info",
                     f"[Raid] Navios cheios ({total_loot:,}). Enviando nova viagem ({trips_done+1}/{max_trips}).")
            next_inputs = {**inputs, "_phase": "send", "_trips_done": trips_done}
            return RunnerResult(success=True, reschedule_seconds=60, reschedule_inputs=next_inputs)

        self.log(jid, "info",
                 f"[Raid] Concluído. {trips_done} viagem(ns). "
                 f"{'Sem navios cheios para continuar.' if not ships_full else 'Limite de viagens atingido.'}")

        # Se enviou blockade → chamar frota de volta
        needs_blockade = bool(inputs.get("needs_blockade", False))
        if needs_blockade and advisor.get("port_occupied"):
            self.log(jid, "info", "[Raid] Porto ainda bloqueado → agendando recall da frota.")
            return RunnerResult(
                success=True,
                reschedule_seconds=60,
                reschedule_inputs={**inputs, "_phase": "recall_fleet"},
            )

        return RunnerResult(success=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_snapshot(self, jid: str, ga_id: str) -> dict:
        try:
            return self.hub.get_snapshot(game_account_id=ga_id) or {}
        except Exception as exc:
            self.log(jid, "warn", f"[Raid] Falha ao obter snapshot: {exc}")
            return {}

    def _get_military_by_city(self, snap: dict) -> list[dict]:
        """Return by_city list from snapshot.military."""
        return (snap.get("military") or {}).get("by_city") or []

    # Name → unit_id map (from UNIT_STATS)
    _UNIT_NAME_TO_ID: dict[str, int] = {}

    @classmethod
    def _build_name_map(cls) -> None:
        if cls._UNIT_NAME_TO_ID:
            return
        try:
            from game_client.unit_stats import UNIT_STATS
            cls._UNIT_NAME_TO_ID = {
                str(v.get("name", "")): k
                for k, v in UNIT_STATS.items()
                if v.get("name")
            }
        except Exception:
            pass

    def _get_available_units(self, jid: str, snap: dict, city_id: str) -> dict[int, int]:
        """Extract available land units from snapshot.military.by_city.

        Handles both name-keyed ('Morteiro': 1) and id-keyed (305: 1) snapshots.
        """
        self._build_name_map()
        for city_mil in self._get_military_by_city(snap):
            if str(city_mil.get("city_id") or "") == str(city_id):
                troops = city_mil.get("troops") or {}
                result = {}
                for k, v in troops.items():
                    qty = int(v or 0)
                    if qty <= 0:
                        continue
                    try:
                        uid = int(k)
                    except (ValueError, TypeError):
                        # Name-based key → look up ID
                        uid = self._UNIT_NAME_TO_ID.get(str(k), 0)
                    if uid > 0:
                        result[uid] = result.get(uid, 0) + qty
                return result
        return {}

    def _get_transporters(self, snap: dict, city_id: str, client=None, jid: str = "") -> int:
        """Count available merchant ships.

        Merchant ships (transportadores) are a GLOBAL resource — not per-city.
        Use base_snapshot.free_transporters for the total available count.
        Live query via updateGlobalData as fallback.
        """
        # Global transporters from base_snapshot (most reliable)
        base = snap.get("base_snapshot") or {}
        free = int(base.get("free_transporters") or 0)
        if free > 0:
            if jid:
                self.log(jid, "info",
                         f"[Raid] Mercantes globais disponíveis: {free} / {base.get('max_transporters',0)}")
            return free

        # Live query fallback — fetch from game header
        if client is not None:
            try:
                # fetch any city page to get updateGlobalData with freeTransporters
                resp = client._request("GET", client._server_url,
                    params={"view": "city", "cityId": int(city_id),
                            "backgroundView": "city", "actionRequest": client._action_request,
                            "ajax": "1"}, timeout=15)
                data = resp.json()
                for entry in data:
                    if isinstance(entry, list) and entry[0] == "updateGlobalData" and isinstance(entry[1], dict):
                        ft = int(entry[1].get("freeTransporters") or 0)
                        if ft > 0:
                            if jid:
                                self.log(jid, "info", f"[Raid] Mercantes live (header): {ft}")
                            return ft
            except Exception as exc:
                if jid:
                    self.log(jid, "warn", f"[Raid] Falha ao buscar mercantes live: {exc}")

        return 0

    def _auto_calculate_units(
        self, jid: str, inputs: dict, available: dict[int, int]
    ) -> dict[int, int]:
        """Auto-calculate troops from spy intel stored in inputs."""
        enemy_units_raw = inputs.get("enemy_units") or {}
        enemy_units = _parse_units(enemy_units_raw)
        wall_level  = _parse_int(inputs.get("wall_level"), 1)

        if not enemy_units:
            # Sem intel — usar o que tem disponível.
            # SEMPRE incluir: 1) artilharia para muralha + 2) linha de frente (Hoplita/Gigante)
            result: dict[int, int] = {}

            # Artilharia (siege) — pega o que tiver, na ordem de prioridade
            for uid in (305, 306, 307):  # Morteiro, Catapulta, Ariete
                have = available.get(uid, 0)
                if have > 0:
                    result[uid] = have
                    break

            # Linha de frente — OBRIGATÓRIA (Hoplita preferido, Gigante, Espadachim)
            for uid in (303, 308, 302, 315):
                have = available.get(uid, 0)
                if have > 0:
                    result[uid] = result.get(uid, 0) + have
                    break

            self.log(jid, "warn",
                     f"[Raid] Sem intel. Usando disponíveis (siege+frente): {result}")
            return result

        rec = recommend_army(
            enemy_units,
            wall_level=wall_level,
            available_units=available,
        )
        self.log(jid, "info",
                 f"[Raid] Força recomendada: {rec['recommended']} "
                 f"({'vitória estimada' if rec['can_win_with_recommended'] else 'RISCO'}) "
                 f"HP restante: {rec['battle_estimate']['surviving_hp_pct']:.0f}%")

        if rec.get("missing_units"):
            self.log(jid, "warn",
                     f"[Raid] Unidades insuficientes: {rec['missing_units']}")

        # Clamp to available
        result = {}
        for uid, qty in rec["recommended"].items():
            have = available.get(uid, 0)
            result[uid] = min(qty, have)
        return {k: v for k, v in result.items() if v > 0}

    def _auto_select_source_city(
        self, jid: str, snap: dict, explicit_units: dict, inputs: dict
    ) -> dict | None:
        """Pick best city with troops + merchants for the raid.

        Returns dict with city_id, city_name, units, merchants — or None if nothing viable.
        """
        enemy_units = _parse_units(inputs.get("enemy_units") or {})
        wall_level  = _parse_int(inputs.get("wall_level"), 15)

        from services.combat import recommend_army, pick_minimum_siege

        # city_name lookup from snap.cities
        city_names = {str(c.get("id","")): c.get("name","") for c in (snap.get("cities") or [])}

        by_city = self._get_military_by_city(snap)
        best = None
        best_score = -1

        for city_mil in by_city:
            cid = str(city_mil.get("city_id") or "")
            if not cid:
                continue

            army = self._get_available_units(jid, snap, cid)
            if not army:
                continue

            total_troops = sum(army.values())
            if total_troops <= 0:
                continue

            # Check has minimum siege
            has_siege = any(army.get(uid, 0) >= min_q for uid, min_q in [(305,6),(306,12),(307,18)])

            # Merchant ships from snapshot (live will be fetched separately)
            fleet = city_mil.get("fleet") or {}
            merchants = sum(int(fleet.get(str(uid), 0)) for uid in (201, 202, 204))

            # Score: prefer siege available, then most troops, then merchants
            score = (10000 if has_siege else 0) + total_troops + merchants * 5

            if score > best_score:
                if enemy_units:
                    rec = recommend_army(enemy_units, wall_level=wall_level, available_units=army)
                    if not rec["can_win_with_recommended"] and score < 5000:
                        continue

                best_score = score
                rec_units = explicit_units or (recommend_army(enemy_units, wall_level=wall_level, available_units=army)["recommended"] if enemy_units else army)
                # Clamp to available
                clamped = {k: min(v, army.get(k, 0)) for k, v in rec_units.items() if army.get(k, 0) > 0}
                best = {
                    "city_id":   cid,
                    "city_name": city_names.get(cid, cid),
                    "units":     clamped or army,
                    "merchants": merchants,
                }

        return best

    def _parse_eta_to_seconds(self, eta_str: str) -> int:
        """Convert 'DD.MM.YYYY H:MM:SS' from game to seconds from now."""
        import time as _time
        from datetime import datetime
        try:
            dt = datetime.strptime(eta_str.strip(), "%d.%m.%Y %H:%M:%S")
            now = datetime.now()
            diff = (dt - now).total_seconds()
            return max(0, int(diff))
        except Exception:
            return 0

    def _find_latest_combat_report(
        self, jid: str, client, source_city_id: str, target_city_id: str,
        ga_id: str = "",
    ) -> dict | None:
        """Find most recent combat report against target, fetch detail + detailed rounds, save to hub."""
        try:
            reports = client.fetch_combat_reports(int(source_city_id), limit=10)
        except Exception as exc:
            self.log(jid, "warn", f"[Raid] Falha ao buscar relatórios: {exc}")
            return None

        for r in reports:
            if str(r.get("city_id_target", "")) == str(target_city_id):
                combat_id = r["combat_id"]
                try:
                    detail = client.fetch_combat_report_detail(int(source_city_id), combat_id)
                    detail["rounds"] = r.get("rounds", 0)
                except Exception as exc:
                    self.log(jid, "warn", f"[Raid] Falha ao buscar detalhe {combat_id}: {exc}")
                    return {"combat_id": combat_id, "army_lost": r.get("result") == "defeat"}

                # Buscar relatório detalhado round por round
                detailed: dict = {}
                try:
                    detailed = client.fetch_combat_detailed_report(int(source_city_id), combat_id)
                    self.log(jid, "info",
                             f"[Raid] Relatório detalhado: {detailed.get('total_rounds',0)} round(s) "
                             f"perdas_atacante={detailed.get('attacker_losses')} "
                             f"perdas_defensor={detailed.get('defender_losses')}")
                except Exception as exc:
                    self.log(jid, "warn", f"[Raid] Falha relatório detalhado: {exc}")

                # Salvar no hub
                if ga_id:
                    try:
                        self.hub.save_combat_report(ga_id, {
                            "combat_id":        combat_id,
                            "combat_type":      "land",
                            "result":           r.get("result", ""),
                            "combat_date":      detail.get("date", ""),
                            "total_rounds":     r.get("rounds", 1),
                            "source_city_id":   source_city_id,
                            "target_city_id":   target_city_id,
                            "target_city_name": r.get("city_name", ""),
                            "target_owner":     r.get("owner_name", ""),
                            "loot_json":        detail.get("loot") or {},
                            "total_loot":       detail.get("total_loot", 0),
                            "attacker_losses":  detailed.get("attacker_losses") or {},
                            "defender_losses":  detailed.get("defender_losses") or {},
                            "summary_html":     detail.get("html", ""),
                            "detailed_html":    detailed.get("combined_html", ""),
                        })
                        self.log(jid, "info", f"[Raid] Relatório de combate {combat_id} salvo no hub.")
                    except Exception as exc:
                        self.log(jid, "warn", f"[Raid] Falha ao salvar relatório no hub: {exc}")

                return detail
        return None

    def _check_remaining_resources(self, jid: str, ga_id: str, target_city_id: str) -> dict | None:
        """Query hub for latest spy intel and return total resources. None if unavailable."""
        try:
            intel = self.hub.get_latest_spy_intel(
                target_city_id=target_city_id,
                game_account_id=ga_id,
            )
            if not intel:
                return None
            resources = intel.get("resources") or {}
            total = sum(int(v) for v in resources.values() if v)
            return {"total": total, "resources": resources}
        except Exception as exc:
            self.log(jid, "warn", f"[Raid] Falha ao verificar recursos restantes: {exc}")
            return None

    def _auto_select_fleet(self, jid: str, snap: dict, city_id: str) -> dict[int, int]:
        """Auto-select all available combat fleet at source city for blockade."""
        self._build_name_map()
        merchant_ids = {201, 202, 204, 220}  # exclude transport/support ships
        for city_mil in self._get_military_by_city(snap):
            if str(city_mil.get("city_id") or "") == str(city_id):
                fleet = city_mil.get("fleet") or {}
                combat = {}
                for k, v in fleet.items():
                    qty = int(v or 0)
                    if qty <= 0:
                        continue
                    try:
                        uid = int(k)
                    except (ValueError, TypeError):
                        uid = self._UNIT_NAME_TO_ID.get(str(k), 0)
                    if uid > 0 and uid not in merchant_ids:
                        combat[uid] = combat.get(uid, 0) + qty
                return combat
        return {}

    def _get_client(self, jid: str, ga_id: str, aid: str = "", inputs: dict | None = None):
        """Get authenticated game client via BaseRunner's login flow."""
        creds = self.resolve_credentials(aid, inputs or {}, game_account_id=ga_id)
        if not creds:
            raise ValueError("Credenciais não encontradas para a conta")
        return self.get_or_login_game_client(jid, aid, ga_id, creds)
