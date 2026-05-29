"""
Runner de Saque de Cidade (ac=1007) — RaidCityRunner.

Envia um exército para roubar recursos de uma cidade inimiga.
Suporta múltiplas viagens até esgotar os recursos disponíveis.

Inputs:
    source_city_id          str  — cidade de origem (onde estão as tropas)
    target_city_id          str  — cidade alvo
    island_id               str  — ilha da cidade alvo
    mode                    str  — "land" (padrão) | "fleet" (futuro: bloqueio naval)
    units                   dict — {unit_id: qty}; se vazio, usa cálculo automático
    transporters            int  — navios mercantes para carregar saque (0 = auto)
    multi_trip              bool — continuar atacando até esgotar recursos
    max_trips               int  — limite de segurança (padrão 10)
    min_resources_to_continue int — para de atacar se recursos < X
    # Internos (gerenciados pelo runner entre reexecuções)
    _trips_done             int  — contador de viagens completadas
    _travel_seconds         int  — tempo de viagem calculado na 1ª vez
    # Gancho futuro (bloqueio naval)
    needs_blockade          bool — se True, enviar frota antes de atacar
    blockade_fleet_units    dict — {ship_unit_id: qty} para bloqueio
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

# Extra buffer after army is expected to return before we re-check/re-dispatch
RETURN_BUFFER_SECONDS = 300   # 5 min after estimated return
BATTLE_DURATION_EST   = 1800  # ~30 min battle estimate
ERROR_RESCHEDULE      = 5 * 60


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
    """Ataca e saqueia cidade inimiga. Suporta múltiplas viagens."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid    = job["job_id"]
        ga_id  = str(job.get("game_account_id") or "").strip()
        inputs = job.get("inputs") or {}

        # ── Inputs básicos ────────────────────────────────────────────────────
        source_city_id = str(inputs.get("source_city_id") or "").strip()
        target_city_id = str(inputs.get("target_city_id") or "").strip()
        island_id      = str(inputs.get("island_id")      or "").strip()
        mode           = str(inputs.get("mode")           or "land").strip().lower()

        if not target_city_id or not island_id:
            self.log(jid, "error", "target_city_id e island_id são obrigatórios.")
            return RunnerResult(success=False)

        # ── Config de viagens ─────────────────────────────────────────────────
        multi_trip   = str(inputs.get("multi_trip", "true")).lower() not in {"false", "0", "no"}
        max_trips    = _parse_int(inputs.get("max_trips"), 10)
        min_res_cont = _parse_int(inputs.get("min_resources_to_continue"), 0)
        trips_done    = _parse_int(inputs.get("_trips_done"), 0)
        travel_cached = _parse_int(inputs.get("_travel_seconds"), 0)

        # ── Checar limite de viagens ──────────────────────────────────────────
        if trips_done >= max_trips:
            self.log(jid, "info",
                     f"[Raid] Limite de {max_trips} viagem(ns) atingido. Encerrando.")
            return RunnerResult(success=True)

        # ── Obter snapshot ────────────────────────────────────────────────────
        snap = self._get_snapshot(jid, ga_id)

        # ── Auto-selecionar cidade de origem se não especificada ──────────────
        # O runner decide quem vai roubar com base nas tropas disponíveis.
        # Prioridade: 1) source_city_id explícito  2) cidade com mais tropas + mercantes
        raw_units    = inputs.get("units") or {}
        units        = _parse_units(raw_units)
        transporters = _parse_int(inputs.get("transporters"), 0)

        if not source_city_id:
            selection = self._auto_select_source_city(jid, snap, units, inputs)
            if not selection:
                self.log(jid, "error",
                         "[Raid] Nenhuma cidade com tropas suficientes encontrada. "
                         "Verifique snapshot ou especifique source_city_id.")
                return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)
            source_city_id = selection["city_id"]
            if not units:
                units = selection["units"]
            self.log(jid, "info",
                     f"[Raid] Cidade selecionada automaticamente: {selection.get('city_name',source_city_id)} "
                     f"(tropas={sum(units.values())} mercantes={selection.get('merchants',0)})")

        available_units        = self._get_available_units(jid, snap, source_city_id)

        # ── Auto-calcular tropas se ainda não definido ────────────────────────
        if not units:
            units = self._auto_calculate_units(jid, inputs, available_units)
            if not units:
                self.log(jid, "error", "[Raid] Sem tropas disponíveis e sem especificação manual.")
                return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)

        # ── Transportadores: snapshot → se 0, buscar live ────────────────────
        if transporters <= 0:
            available_transporters = self._get_transporters(snap, source_city_id)
            if available_transporters <= 0:
                # Buscar live — mercantes podem ter voltado desde último snapshot
                try:
                    live_client = self._get_client(jid, ga_id)
                    available_transporters = self._get_transporters(
                        snap, source_city_id, client=live_client, jid=jid
                    )
                except Exception:
                    pass
            transporters = available_transporters

        if transporters <= 0:
            self.log(jid, "warn", "[Raid] Sem navios mercantes disponíveis para carregar saque.")

        # ── Verificar se ainda vale continuar (viagens 2+) ───────────────────
        if trips_done > 0 and min_res_cont > 0:
            intel = self._check_remaining_resources(jid, ga_id, target_city_id)
            if intel is not None:
                remaining = intel.get("total", 0)
                self.log(jid, "info",
                         f"[Raid] Recursos restantes no alvo: {remaining:,} "
                         f"(mínimo para continuar: {min_res_cont:,})")
                if remaining < min_res_cont:
                    self.log(jid, "info",
                             f"[Raid] Recursos abaixo do mínimo. Encerrando após {trips_done} viagem(ns).")
                    return RunnerResult(success=True)

        # ── Bloqueio naval (se alvo tem frota) ───────────────────────────────
        # Detecta frota inimiga via intel e dispara blockade antes do ataque.
        # O blockade é enviado e o raid terrestre segue imediatamente depois.
        # A frota fica bloqueando indefinidamente — chamada de volta não implementada.
        needs_blockade      = bool(inputs.get("needs_blockade", False))
        blockade_fleet_raw  = inputs.get("blockade_fleet_units") or {}
        blockade_fleet      = _parse_units(blockade_fleet_raw)

        if needs_blockade:
            if not blockade_fleet:
                # Auto-selecionar frota de combate disponível na cidade de origem
                blockade_fleet = self._auto_select_fleet(jid, snap, source_city_id)

            if blockade_fleet:
                try:
                    client_for_blockade = self._get_client(jid, ga_id)
                    client_for_blockade.blockade_fleet(
                        from_city_id=int(source_city_id),
                        to_city_id=int(target_city_id),
                        island_id=int(island_id),
                        fleet_units=blockade_fleet,
                    )
                    self.log(jid, "info",
                             f"[Raid] ⚓ Frota enviada para bloqueio: {blockade_fleet}")
                except Exception as exc:
                    self.log(jid, "warn",
                             f"[Raid] Falha ao enviar bloqueio naval: {exc}. "
                             f"Prosseguindo com ataque terrestre mesmo assim.")
            else:
                self.log(jid, "warn",
                         "[Raid] needs_blockade=True mas sem frota disponível para bloqueio.")

        # ── Modo Land (padrão) ────────────────────────────────────────────────
        if mode != "land":
            self.log(jid, "error", f"[Raid] Modo '{mode}' não suportado ainda.")
            return RunnerResult(success=False)

        try:
            client = self._get_client(jid, ga_id)
        except Exception as exc:
            self.log(jid, "error", f"[Raid] Falha ao obter sessão: {exc}")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)

        # ── Fetch plunder view → tempo de viagem ─────────────────────────────
        travel_seconds = travel_cached
        if not travel_seconds:
            try:
                view = client.fetch_plunder_view(
                    int(source_city_id), int(target_city_id), int(island_id)
                )
                travel_seconds = view.get("travel_seconds", 0)
                if travel_seconds:
                    self.log(jid, "info",
                             f"[Raid] Tempo de viagem: {travel_seconds // 60}min {travel_seconds % 60}s")
            except Exception as exc:
                self.log(jid, "warn", f"[Raid] Não foi possível obter tempo de viagem: {exc}")

        if not travel_seconds:
            travel_seconds = 3600  # fallback 1h

        # ── Log da operação ───────────────────────────────────────────────────
        cap = loot_capacity(transporters)
        self.log(jid, "info",
                 f"[Raid] Viagem {trips_done + 1}/{max_trips} | "
                 f"alvo={target_city_id} ilha={island_id} | "
                 f"tropas={dict(units)} | transportadores={transporters} (cap={cap:,}) | "
                 f"viagem={travel_seconds // 60}min")

        # ── Enviar exército ───────────────────────────────────────────────────
        try:
            result = client.plunder_land(
                from_city_id=int(source_city_id),
                to_city_id=int(target_city_id),
                island_id=int(island_id),
                units=units,
                transporters=transporters,
            )
        except Exception as exc:
            self.log(jid, "error", f"[Raid] Falha ao enviar exército: {exc}")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)

        self.log(jid, "info", f"[Raid] Exército enviado. Aguardando retorno.")

        # ── Agendar próxima viagem ou encerrar ────────────────────────────────
        trips_done += 1

        if multi_trip and trips_done < max_trips:
            # Retornar = 2× tempo de viagem + duração da batalha + buffer
            return_delay = (travel_seconds * 2) + BATTLE_DURATION_EST + RETURN_BUFFER_SECONDS
            self.log(jid, "info",
                     f"[Raid] Multi-trip: reagendando viagem {trips_done + 1} em "
                     f"{return_delay // 60}min.")
            return RunnerResult(
                success=True,
                reschedule_seconds=int(return_delay),
                reschedule_inputs={
                    **inputs,
                    "_trips_done":      trips_done,
                    "_travel_seconds":  travel_seconds,
                    "units":            {str(k): v for k, v in units.items()},
                },
            )

        self.log(jid, "info", f"[Raid] {trips_done} viagem(ns) concluída(s). Encerrando.")
        return RunnerResult(success=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_snapshot(self, jid: str, ga_id: str) -> dict:
        try:
            return self.hub.get_snapshot(ga_id) or {}
        except Exception as exc:
            self.log(jid, "warn", f"[Raid] Falha ao obter snapshot: {exc}")
            return {}

    def _get_available_units(self, jid: str, snap: dict, city_id: str) -> dict[int, int]:
        """Extract available land units at source city from snapshot."""
        cities = snap.get("cities") or []
        for city in cities:
            if str(city.get("id") or city.get("game_city_id") or "") == str(city_id):
                army = city.get("army") or city.get("military", {}).get("army") or {}
                return {int(k): int(v) for k, v in army.items() if int(v) > 0}
        return {}

    def _get_transporters(self, snap: dict, city_id: str, client=None, jid: str = "") -> int:
        """Count available merchant ships at source city.

        Tries snapshot first. If 0, queries live from game (FetchStationedUnitsAction)
        since merchants may have returned since last snapshot update.
        """
        # Try snapshot
        cities = snap.get("cities") or []
        for city in cities:
            if str(city.get("id") or city.get("game_city_id") or "") == str(city_id):
                fleet = city.get("fleet") or city.get("military", {}).get("fleet") or {}
                snap_count = int(fleet.get("201", 0)) + int(fleet.get("201 ", 0)) + int(fleet.get(201, 0))
                if snap_count > 0:
                    return snap_count

        # Fallback: query live from game
        if client is None:
            return 0
        try:
            result = client.fetch_stationed_units(int(city_id), building_type="fleet")
            counts = result.get("counts") or {}
            live_count = int(counts.get(201, 0)) + int(counts.get(202, 0))
            if jid and live_count > 0:
                self.log(jid, "info",
                         f"[Raid] Mercantes (live): {live_count} na cidade {city_id}")
            return live_count
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
            # No intel → use minimum siege only as fallback
            siege = pick_minimum_siege(available)
            self.log(jid, "warn",
                     "[Raid] Sem intel de tropas inimigas. Usando apenas artilharia mínima.")
            return siege

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

        cities = snap.get("cities") or []
        best = None
        best_score = -1

        for city in cities:
            cid = str(city.get("id") or city.get("game_city_id") or "")
            if not cid:
                continue

            army = self._get_available_units(jid, snap, cid)
            if not army:
                continue

            total_troops = sum(army.values())
            if total_troops <= 0:
                continue

            # Check has minimum siege
            siege = pick_minimum_siege(army)
            has_siege = any(army.get(uid, 0) >= min_q for uid, min_q in [(305,6),(306,12),(307,18)])

            # Merchant ships from snapshot
            fleet = city.get("fleet") or city.get("military", {}).get("fleet") or {}
            merchants = int(fleet.get("201", 0)) + int(fleet.get(201, 0))

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
                    "city_name": city.get("name", cid),
                    "units":     clamped or army,
                    "merchants": merchants,
                }

        return best

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
        cities = snap.get("cities") or []
        for city in cities:
            if str(city.get("id") or city.get("game_city_id") or "") == str(city_id):
                fleet = city.get("fleet") or city.get("military", {}).get("fleet") or {}
                # Exclude merchants (201, 202, 204) and support (220=Reparador)
                combat = {
                    int(k): int(v)
                    for k, v in fleet.items()
                    if int(v or 0) > 0 and int(k) not in {201, 202, 204, 220}
                }
                return combat
        return {}

    def _get_client(self, jid: str, ga_id: str):
        """Get authenticated game client."""
        from sessions.game_session_service import GameSessionService
        svc = GameSessionService(hub=self.hub)
        return svc.get_or_create_client(ga_id, jid=jid)
