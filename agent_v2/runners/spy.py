"""
Runner de espionagem — ação 15 (SpyRunner).

State machine:
  accumulating → executing → recalling → done

  accumulating: envia missões de infiltração (1) até ter suficientes na cidade
  executing:    executa missões de inteligência uma a uma
  recalling:    envia missão 8 (chamar) para retirar todos
  done:         termina sem reagendar
"""

from __future__ import annotations

import logging
import random
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.spy import MISSION_DATA, find_optimal_agents_decoys
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

ARRIVAL_WAIT = 7 * 60     # 7 min — tempo para espião chegar
MISSION_WAIT = 10 * 60    # 10 min — tempo para missão completar
ERROR_RESCHEDULE = 10 * 60


@register_runner(15)
class SpyRunner(BaseRunner):
    """State machine de espionagem:

    Fase 1 - ACCUMULATING: infiltra espiões até ter o suficiente para a missão mais exigente
    Fase 2 - EXECUTING: executa cada missão de inteligência com otimizador real
    Fase 3 - RECALLING: chama todos de volta (missão 8)
    Fase 4 - DONE: job concluído

    Durante execução, se perder espiões → volta para accumulating automaticamente.
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        city_id = str(inputs.get("city_id") or "").strip()
        target_city_id = str(inputs.get("target_city_id") or "").strip()
        island_id = str(inputs.get("island_id") or "").strip()
        target_city_name = str(inputs.get("target_city_name") or "").strip()
        target_owner = str(inputs.get("target_owner") or "").strip()
        # Inject game_account_id into inputs for helpers that need it
        inputs = {**inputs, "__ga_id": ga_id}
        save_reports = bool(inputs.get("save_reports") if inputs.get("save_reports") is not None else True)
        delete_after_save = bool(inputs.get("delete_after_save") or False)
        recall_after = bool(inputs.get("recall_after") if inputs.get("recall_after") is not None else True)

        if not city_id or not target_city_id or not island_id:
            self.log(jid, "error", "city_id, target_city_id e island_id obrigatórios")
            return RunnerResult(success=False, data={"error": "missing_inputs"})

        try:
            max_risk = float(inputs.get("max_detection_risk") or 35)
        except (ValueError, TypeError):
            max_risk = 35.0

        auto_agents = bool(inputs.get("auto_agents") if inputs.get("auto_agents") is not None else True)
        try:
            manual_agents = max(1, int(inputs.get("agents") or 1))
        except (ValueError, TypeError):
            manual_agents = 1

        # Parse mission IDs (string or list)
        raw_mission = inputs.get("mission_id") or "1"
        try:
            if isinstance(raw_mission, list):
                all_mission_ids = [int(x) for x in raw_mission]
            else:
                all_mission_ids = [int(x.strip()) for x in str(raw_mission).split(",") if x.strip()]
        except (ValueError, TypeError):
            all_mission_ids = [1]
        if not all_mission_ids:
            all_mission_ids = [1]

        # Determine phases: mission 1 = infiltration; others = intelligence
        infiltration_missions = [m for m in all_mission_ids if m == 1]
        intel_missions = [m for m in all_mission_ids if m != 1 and m != 8]

        # Load state from recovery
        recovery = inputs.get("__recovery") if isinstance(inputs.get("__recovery"), dict) else {}
        phase = recovery.get("phase", "accumulating" if intel_missions else "infiltrating_only")
        missions_pending = recovery.get("missions_pending", list(intel_missions))
        missions_done = recovery.get("missions_done", [])

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais não encontradas")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            # ── Always: collect pending reports ───────────────────────────────
            if save_reports:
                self._collect_reports(jid, client, ga_id, city_id, delete_after_save)

            # ── Read safehouse state ──────────────────────────────────────────
            self.log(jid, "info", f"Lendo safehouse | fase={phase}")
            state = client.get_safehouse_state(city_id)
            available = int(state.get("available_spies") or 0)
            total = int(state.get("total_spies") or 0)
            in_use = int(state.get("in_use_spies") or 0)
            training = int(state.get("training_count") or 0)

            # If configured city has 0 available, scan others
            if available == 0:
                city_id, state, available = self._find_best_city(jid, city_id, client, inputs)

            self.log(jid, "info",
                f"Safehouse: total={total} disponíveis={available} em_uso={in_use} treinando={training}")

            # Stationed at target city — aggregate from ALL safehouses
            stationed = self._get_total_stationed(jid, client, city_id, state,
                                                   target_city_name, target_city_id, inputs)
            self.log(jid, "info",
                f"Estacionados em {target_city_name or target_city_id}: {stationed} (todas as cidades)")

            # ── Get live risk data ─────────────────────────────────────────────
            live_params: dict = {}
            try:
                md_result = client.get_spy_mission_data(city_id, target_city_id, island_id)
                live_params = md_result.get("raw_params", {})
                ti = md_result.get("target", {})
                self.log(jid, "info",
                    f"Alvo: lv={ti.get('city_level')} inativo={ti.get('is_inactive')} "
                    f"free_spies={ti.get('free_spies')} remaining_risk={live_params.get('remainingRisk',0)}")
            except Exception as exc:
                self.log(jid, "warn", f"Não foi possível buscar riscos: {exc}")

            # ── Phase: infiltrating_only (just mission 1, no intelligence) ────
            if phase == "infiltrating_only":
                result = self._send_one_mission(jid, client, city_id, target_city_id, island_id,
                                                1, available, max_risk, manual_agents, auto_agents, live_params)
                if result:
                    wait = random.randint(ARRIVAL_WAIT, ARRIVAL_WAIT + 120)
                    self.log(jid, "info", f"Missão 1 enviada. Reagendando em {wait}s.")
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait,
                        reschedule_inputs={**inputs, "__recovery": {"phase": "done"}})
                else:
                    wait = random.randint(MISSION_MIN_WAIT := 5*60, MISSION_MAX_WAIT := 15*60)
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait)

            # ── Phase: accumulating ────────────────────────────────────────────
            if phase == "accumulating":
                # How many do we need for the most demanding pending mission?
                needed = self._total_needed(missions_pending, live_params, available)
                self.log(jid, "info", f"Necessários: {needed} | estacionados: {stationed}")

                if stationed >= needed and stationed > 0:
                    self.log(jid, "info", f"Suficientes! Passando para execução.")
                    phase = "executing"
                else:
                    # Send optimal infiltration batch
                    to_send = max(1, needed - stationed)
                    opt = find_optimal_agents_decoys(live_params, 1, max_risk, max_risk + 25, 30.0, available)
                    if opt:
                        batch_agents = min(opt["agents"], to_send, available)
                        self.log(jid, "info",
                            f"Infiltrando {batch_agents} agentes (precisamos {needed - stationed} mais) "
                            f"→ sucesso={opt['success']}% risco={opt['agent_risk']}%")
                        ok, msg = self._send_spy_raw(client, city_id, target_city_id, island_id,
                                                     1, batch_agents, 0)
                        if ok:
                            self.log(jid, "info", f"Infiltração enviada: {msg}")
                            wait = ARRIVAL_WAIT + random.randint(0, 60)
                        else:
                            self.log(jid, "warn", f"Infiltração falhou: {msg}")
                            wait = ERROR_RESCHEDULE
                    else:
                        self.log(jid, "warn",
                            f"Sem espiões disponíveis ou impossível com risco≤{max_risk}%. Aguardando.")
                        wait = random.randint(5*60, 15*60)
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait,
                        reschedule_inputs={**inputs, "__recovery": {
                            "phase": "accumulating", "missions_pending": missions_pending,
                            "missions_done": missions_done,
                        }})

            # ── Phase: executing ──────────────────────────────────────────────
            if phase == "executing":
                if not missions_pending:
                    self.log(jid, "info", "Todas as missões concluídas.")
                    phase = "recalling" if recall_after else "done"
                else:
                    current_mission = missions_pending[0]
                    opt = find_optimal_agents_decoys(
                        live_params, current_mission, max_risk, max_risk + 25, 40.0, stationed
                    )
                    if opt:
                        agents_needed = opt["agents"]
                        if stationed < agents_needed:
                            self.log(jid, "info",
                                f"Missão {current_mission} precisa {agents_needed} espiões, "
                                f"só temos {stationed}. Voltando para acumulação.")
                            phase = "accumulating"
                        else:
                            mname = MISSION_DATA.get(current_mission, {}).get("name", f"missão {current_mission}")
                            self.log(jid, "info",
                                f"Executando {mname} com {opt['agents']} agentes + {opt.get('decoys',0)} cham "
                                f"→ sucesso={opt['success']}% risco={opt['agent_risk']}%")
                            ok, msg = self._send_spy_raw(client, city_id, target_city_id, island_id,
                                                         current_mission, opt["agents"], opt.get("decoys", 0))
                            if ok:
                                self.log(jid, "info", f"Missão enviada: {msg}")
                                missions_done.append(current_mission)
                                missions_pending = missions_pending[1:]
                                wait = MISSION_WAIT + random.randint(0, 60)
                                self.save_game_client(ga_id, client)
                                return RunnerResult(success=True, reschedule_seconds=wait,
                                    reschedule_inputs={**inputs, "__recovery": {
                                        "phase": "executing" if missions_pending else "recalling",
                                        "missions_pending": missions_pending,
                                        "missions_done": missions_done,
                                    }})
                            else:
                                self.log(jid, "warn", f"Missão falhou: {msg}")
                                wait = ERROR_RESCHEDULE
                                self.save_game_client(ga_id, client)
                                return RunnerResult(success=False, reschedule_seconds=wait,
                                    reschedule_inputs={**inputs, "__recovery": {
                                        "phase": "accumulating", "missions_pending": missions_pending,
                                        "missions_done": missions_done,
                                    }})
                    else:
                        self.log(jid, "warn",
                            f"Impossível executar missão {current_mission} com {stationed} espiões e risco≤{max_risk}%. "
                            f"Voltando para acumulação.")
                        phase = "accumulating"

                if phase == "accumulating":
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=ARRIVAL_WAIT,
                        reschedule_inputs={**inputs, "__recovery": {
                            "phase": "accumulating", "missions_pending": missions_pending,
                            "missions_done": missions_done,
                        }})

            # ── Phase: recalling ──────────────────────────────────────────────
            if phase == "recalling":
                if stationed > 0:
                    self.log(jid, "info", f"Chamando {stationed} espião(ões) de volta (missão 8)")
                    ok, msg = self._send_spy_raw(client, city_id, target_city_id, island_id, 8, 1, 0)
                    self.log(jid, "info", f"Recall: {msg}")
                    wait = ARRIVAL_WAIT
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait,
                        reschedule_inputs={**inputs, "__recovery": {"phase": "done",
                            "missions_done": missions_done}})
                else:
                    phase = "done"

            # ── Phase: done ───────────────────────────────────────────────────
            if phase == "done":
                self.log(jid, "info",
                    f"Espionagem concluída! Missões realizadas: {missions_done}")
                self.save_game_client(ga_id, client)
                return RunnerResult(success=True, data={"missions_done": missions_done})

            self.save_game_client(ga_id, client)
            return RunnerResult(success=True, reschedule_seconds=ERROR_RESCHEDULE)

        except Exception as exc:
            self.log(jid, "error", f"Erro na espionagem: {exc}")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE, data={"error": str(exc)})

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_total_stationed(self, jid, client, primary_city_id, primary_state,
                             city_name: str, city_id: str, inputs: dict) -> int:
        """Aggregate stationed spies from ALL safehouses for the target city."""
        total = self._get_stationed(primary_state, city_name, city_id)
        # Check other cities with safehouses
        try:
            ga_id = str(inputs.get("__ga_id") or "").strip()
            if not ga_id:
                return total  # no GA ID available, skip multi-safehouse scan
            snap = self.hub.get_snapshot(game_account_id=ga_id)
            for candidate in (snap.get("cities") or []):
                cid = str(candidate.get("id") or "")
                if not cid or cid == primary_city_id:
                    continue
                has_safe = any("safehouse" in str(b.get("building","")).lower()
                              for b in (candidate.get("buildings") or []))
                if not has_safe:
                    continue
                try:
                    cs = client.get_safehouse_state(cid)
                    count = self._get_stationed(cs, city_name, city_id)
                    if count > 0:
                        self.log(jid, "info",
                            f"  +{count} espiões de {candidate.get('name','?')} estacionados no alvo")
                        total += count
                except Exception:
                    continue
        except Exception as exc:
            self.log(jid, "warn", f"Não foi possível agregar estacionados: {exc}")
        return total

    def _get_stationed(self, state: dict, city_name: str, city_id: str) -> int:
        """Count spies stationed at target (fuzzy match on city name)."""
        stationed = state.get("stationed_by_city", {})
        if not stationed:
            return 0
        # Exact match
        for key, count in stationed.items():
            if city_name and city_name.lower() in key.lower():
                return count
            if city_id and city_id in key:
                return count
        # Partial match — take first match
        city_name_lower = (city_name or "").lower()
        for key, count in stationed.items():
            words = city_name_lower.split()
            if words and any(w in key.lower() for w in words if len(w) > 3):
                return count
        return 0

    def _total_needed(self, mission_ids: list, live_params: dict, available: int) -> int:
        """Max optimal agents across all pending intelligence missions."""
        needed = 1
        for mid in mission_ids:
            opt = find_optimal_agents_decoys(live_params, mid, 50.0, 75.0, 30.0, available)
            if opt:
                needed = max(needed, opt["agents"])
        return needed

    def _send_spy_raw(self, client, city_id, target_city_id, island_id, mission_id, agents, decoys):
        """Send a spy mission and return (success, message)."""
        result = client.send_spy(
            source_city_id=city_id, target_city_id=target_city_id,
            island_id=island_id, mission_id=mission_id,
            agents=agents, decoys=decoys,
        )
        return result.get("success", False), result.get("message", "")

    def _send_one_mission(self, jid, client, city_id, target_city_id, island_id,
                          mission_id, available, max_risk, manual_agents, auto_agents, live_params):
        """Send single mission, return True if sent."""
        if auto_agents:
            opt = find_optimal_agents_decoys(live_params, mission_id, max_risk, max_risk+25, 30.0, available)
            if opt:
                agents, decoys = opt["agents"], opt.get("decoys", 0)
                self.log(jid, "info",
                    f"Ótimo: {agents}+{decoys}cham → sucesso={opt['success']}% risco={opt['agent_risk']}%")
            else:
                if available > 0:
                    agents, decoys = 1, 0
                    self.log(jid, "warn", "Impossível atingir limites, usando 1 agente mínimo.")
                else:
                    self.log(jid, "warn", "0 espiões disponíveis.")
                    return False
        else:
            agents, decoys = manual_agents, 0
        ok, msg = self._send_spy_raw(client, city_id, target_city_id, island_id, mission_id, agents, decoys)
        if ok:
            self.log(jid, "info", f"Missão {mission_id} enviada: {msg}")
        else:
            self.log(jid, "warn", f"Missão {mission_id} falhou: {msg}")
        return ok

    def _find_best_city(self, jid, city_id, client, inputs):
        """Find city with most available spies if configured city has 0."""
        ga_id = str(inputs.get("__ga_id") or "").strip()
        if not ga_id:
            state = client.get_safehouse_state(city_id)
            return city_id, state, int(state.get("available_spies") or 0)
        snap = self.hub.get_snapshot(game_account_id=ga_id)
        best_state = client.get_safehouse_state(city_id)
        best_avail = int(best_state.get("available_spies") or 0)
        best_city = city_id
        for candidate in (snap.get("cities") or []):
            cid = str(candidate.get("id") or "")
            if not cid or cid == city_id:
                continue
            has_safe = any("safehouse" in str(b.get("building","")).lower()
                          for b in (candidate.get("buildings") or []))
            if not has_safe:
                continue
            try:
                cs = client.get_safehouse_state(cid)
                ca = int(cs.get("available_spies") or 0)
                if ca > best_avail:
                    self.log(jid, "info",
                        f"Usando {candidate.get('name','?')} (id={cid}) com {ca} disponíveis")
                    best_avail = ca
                    best_state = cs
                    best_city = cid
            except Exception:
                continue
        return best_city, best_state, best_avail

    def _collect_reports(self, jid, client, ga_id, city_id, delete_after_save):
        """Collect and save spy reports from the safehouse."""
        try:
            reports = client.get_spy_reports(city_id)
            if not reports:
                self.log(jid, "info", "Nenhum relatório encontrado na Casa Segura")
                return
            self.log(jid, "info", f"{len(reports)} relatório(s) encontrado(s) — salvando no hub")
            report_dicts = []
            for r in reports:
                report_dicts.append({
                    "report_id": r.get("report_id", ""),
                    "target_owner": r.get("target_owner", ""),
                    "target_city_id": r.get("target_city_id", ""),
                    "target_city_name": r.get("target_city_name", ""),
                    "target_x": r.get("target_x"),
                    "target_y": r.get("target_y"),
                    "subject": r.get("subject", ""),
                    "status": r.get("status", ""),
                    "result_status": r.get("result_status", ""),
                    "agents_sent": r.get("agents_sent", 0),
                    "agents_lost": r.get("agents_lost", 0),
                    "report_html": r.get("report_html", ""),
                    "report_text": r.get("report_text", ""),
                    "data_json": r.get("data_json", {}),
                    "date_str": r.get("date_str", ""),
                    "mission_id": r.get("mission_id"),
                    "unread": r.get("unread", False),
                })
            save_result = self.hub.save_spy_reports(ga_id, report_dicts)
            saved = save_result.get("saved", 0)
            new = save_result.get("new_count", 0)
            self.log(jid, "info", f"Relatórios salvos: {saved} ({new} novos)")
            if delete_after_save and new > 0:
                for r in reports[:new]:
                    try:
                        client.delete_spy_report(city_id, r.get("report_id"))
                    except Exception:
                        pass
        except Exception as exc:
            self.log(jid, "warn", f"Erro ao coletar relatórios: {exc}")

MISSION_MIN_WAIT = 5 * 60
MISSION_MAX_WAIT = 15 * 60
