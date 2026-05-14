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
import time
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.spy import MISSION_DATA, compute_spy_risks, find_optimal_agents_decoys
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

ARRIVAL_WAIT = 20 * 60    # 20 min fallback — tempo para espião chegar (usa return_timestamp se disponível)
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
        # Tracked spies sent (agents+decoys) en route to target — parser can't detect for distant cities
        recovery_sent_total = int(recovery.get("sent_total") or 0)
        recovery_arrival_at = int(recovery.get("arrival_at") or 0)  # Unix timestamp

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

            capacity = int(state.get("spy_capacity") or 0)
            self.log(jid, "info",
                f"Safehouse: treinados={total} disponíveis={available} em_uso={in_use} treinando={training} capacidade={capacity}")

            # Stationed at target city — aggregate from ALL safehouses
            target_groups = self._get_target_groups(state, target_city_id, target_city_name, target_owner)
            target_group = self._select_target_group(target_groups)
            stationed = sum(
                int(group.get("count_in_use") or 0)
                for group in target_groups
                if group.get("is_waiting")
            )
            in_transit = sum(
                int(group.get("count_in_use") or 0)
                for group in target_groups
                if group.get("is_travelling")
            )
            self.log(
                jid,
                "info",
                f"Alvo na safehouse: target={target_city_id} grupos={len(target_groups)} "
                f"aguardando={stationed} em_transito={in_transit} "
                f"status={target_group.get('status') or 'sem grupo'}",
            )
            # Debug: dump raw HTML when spies show as in-transit but never station
            if in_transit > 0 and stationed == 0:
                raw_html = state.get("_raw_html", "")
                if raw_html:
                    # Log the spy mission section only (first 3000 chars after spyinfo)
                    idx = raw_html.find('class="spyinfo"')
                    snippet = raw_html[max(0, idx-50):idx+3000] if idx >= 0 else raw_html[:2000]
                    self.log(jid, "debug", f"[HTML_DEBUG em_transito] {snippet}")

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
                    wait = self._next_wait_for_group(target_group, ARRIVAL_WAIT, target_groups)
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
                # How many total spies do we need AT TARGET for the most demanding mission?
                # Use a generous available count (capacity) so optimizer finds the real need,
                # not constrained by current home availability.
                needed = self._total_needed(missions_pending, live_params, max(available, capacity, 20), max_risk)

                # If parser can't detect in-transit spies (distant cities), use recovery tracking
                import time as _time_now
                known_in_transit = 0
                if recovery_sent_total > 0 and recovery_arrival_at > 0:
                    if _time_now.time() < recovery_arrival_at:
                        known_in_transit = max(0, recovery_sent_total - stationed)
                        if known_in_transit > 0:
                            self.log(jid, "info",
                                f"Parser não detectou em_transito; usando recovery: "
                                f"{known_in_transit} espiões ainda a caminho "
                                f"(chega em {int(recovery_arrival_at - _time_now.time())}s)")
                    else:
                        # Should have arrived by now — reset tracking
                        recovery_sent_total = 0
                        recovery_arrival_at = 0

                effective_in_transit = max(in_transit, known_in_transit)
                current_total = stationed + effective_in_transit
                self.log(jid, "info",
                    f"Resumo do alvo: aguardando={stationed} em_transito={effective_in_transit} "
                    f"(parser={in_transit} recovery={known_in_transit}) necessarios={needed}")
                self.log(jid, "info", f"Necessários: {needed} | estacionados: {stationed} | total={current_total}")

                if stationed >= needed and stationed > 0:
                    self.log(jid, "info", f"Suficientes! Passando para execução.")
                    phase = "executing"
                elif current_total >= needed and effective_in_transit > 0:
                    wait = self._next_wait_for_group(target_group, ARRIVAL_WAIT, target_groups)
                    # Use tracked arrival time if available
                    if recovery_arrival_at > 0:
                        import time as _t2
                        wait = max(60, int(recovery_arrival_at - _t2.time()) + 60)
                    self.log(jid, "info", f"Reforço em trânsito; aguardando chegada em {wait}s.")
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait,
                        reschedule_inputs={**inputs, "__recovery": {
                            "phase": "accumulating", "missions_pending": missions_pending,
                            "missions_done": missions_done,
                            "sent_total": recovery_sent_total,
                            "arrival_at": recovery_arrival_at,
                        }})
                else:
                    # Send the full missing amount only if that exact batch is still safe.
                    to_send = max(1, needed - current_total)
                    opt = find_optimal_agents_decoys(live_params, 1, max_risk, max_risk + 25, 30.0, available)
                    if opt:
                        batch_agents = min(to_send, available)
                        batch_decoys = 0
                        batch_risk = compute_spy_risks(live_params, 1, batch_agents, batch_decoys)
                        unsafe_batch = (
                            float(batch_risk["agent_risk"]) > max_risk
                            or float(batch_risk["decoy_risk"]) > max_risk + 25
                            or float(batch_risk["success"]) < 30.0
                        )
                        if unsafe_batch:
                            batch_agents = min(int(opt["agents"]), to_send, available)
                            batch_decoys = min(int(opt.get("decoys") or 0), max(0, available - batch_agents))
                            batch_risk = compute_spy_risks(live_params, 1, batch_agents, batch_decoys)
                            unsafe_batch = (
                                float(batch_risk["agent_risk"]) > max_risk
                                or float(batch_risk["decoy_risk"]) > max_risk + 25
                                or float(batch_risk["success"]) < 30.0
                            )
                        if unsafe_batch:
                            self.log(jid, "warn",
                                f"Lote de infiltração inseguro ({batch_agents}+{batch_decoys}): "
                                f"sucesso={batch_risk['success']}% risco={batch_risk['agent_risk']}%. Aguardando.")
                            wait = random.randint(5*60, 15*60)
                            self.save_game_client(ga_id, client)
                            return RunnerResult(success=True, reschedule_seconds=wait,
                                reschedule_inputs={**inputs, "__recovery": {
                                    "phase": "accumulating", "missions_pending": missions_pending,
                                    "missions_done": missions_done,
                                }})
                        opt = batch_risk
                        self.log(jid, "info",
                            f"Infiltrando {batch_agents} agentes e {batch_decoys} chamarizes "
                            f"(precisamos {needed - current_total} agentes mais) "
                            f"→ sucesso={opt['success']}% risco={opt['agent_risk']}%")
                        ok, msg = self._send_spy_raw(client, city_id, target_city_id, island_id,
                                                     1, batch_agents, batch_decoys)
                        if ok:
                            self.log(jid, "info", f"Infiltração enviada: {msg}")
                            # Re-read safehouse after brief delay to get return_timestamp
                            import time as _tsleep; _tsleep.sleep(3)
                            travel_secs = 0
                            try:
                                fresh_state = client.get_safehouse_state(city_id)
                                fresh_groups = self._get_target_groups(fresh_state, target_city_id, target_city_name, target_owner)
                                fresh_group = self._select_target_group(fresh_groups)
                                wait = self._next_wait_for_group(fresh_group, ARRIVAL_WAIT, fresh_groups)
                                import time as _tnow
                                # Extract travel_secs from best timestamp
                                for fg in fresh_groups:
                                    ts = fg.get("return_timestamp")
                                    if ts:
                                        remaining = int(ts) - int(_tnow.time())
                                        if remaining > 30:
                                            travel_secs = remaining
                                            break
                            except Exception:
                                wait = ARRIVAL_WAIT + random.randint(0, 60)
                            # Track in recovery: how many sent + when they arrive
                            import time as _timport
                            new_sent_total = recovery_sent_total + batch_agents + batch_decoys
                            new_arrival_at = int(_timport.time()) + travel_secs if travel_secs > 0 else 0
                            self.log(jid, "info", f"Reagendando em {wait}s pela chegada dos espiões.")
                        else:
                            self.log(jid, "warn", f"Infiltração falhou: {msg}")
                            wait = ERROR_RESCHEDULE
                    else:
                        self.log(jid, "warn",
                            f"Sem espiões disponíveis ou impossível com risco≤{max_risk}%. Aguardando.")
                        wait = random.randint(5*60, 15*60)
                    self.save_game_client(ga_id, client)
                    _rec = {
                        "phase": "accumulating", "missions_pending": missions_pending,
                        "missions_done": missions_done,
                        "sent_total": locals().get("new_sent_total", recovery_sent_total),
                        "arrival_at": locals().get("new_arrival_at", recovery_arrival_at),
                    }
                    return RunnerResult(success=True, reschedule_seconds=wait,
                        reschedule_inputs={**inputs, "__recovery": _rec})

            # ── Phase: executing ──────────────────────────────────────────────
            if phase == "executing":
                if not missions_pending:
                    self.log(jid, "info", "Todas as missões concluídas.")
                    phase = "recalling" if recall_after else "done"
                else:
                    if not target_group.get("is_waiting"):
                        wait = self._next_wait_for_group(target_group, ARRIVAL_WAIT, target_groups)
                        self.log(jid, "info", "Grupo ainda nÃ£o estÃ¡ em 'esperam novas ordens'; aguardando antes da missÃ£o interna.")
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=wait,
                            reschedule_inputs={**inputs, "__recovery": {
                                "phase": "executing",
                                "missions_pending": missions_pending,
                                "missions_done": missions_done,
                            }})
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
                            ok, msg = self._execute_internal_mission(
                                client, city_id, target_city_id, island_id, current_mission,
                                opt["agents"], opt.get("decoys", 0), target_group.get("spy_id"),
                            )
                            if ok:
                                self.log(jid, "info", f"Missão enviada: {msg}")
                                if save_reports:
                                    time.sleep(2)
                                    self._collect_reports(jid, client, ga_id, city_id, delete_after_save)
                                missions_done.append(current_mission)
                                missions_pending = missions_pending[1:]
                                wait = random.randint(45, 120)
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
                        # optimizer returned None — find how many we need with full available pool
                        opt_needed = find_optimal_agents_decoys(live_params, current_mission, max_risk, max_risk + 25, 40.0, available)
                        if opt_needed:
                            self.log(jid, "warn",
                                f"Impossível executar missão {current_mission} com {stationed} estacionados "
                                f"(risco≤{max_risk}%). Precisa {opt_needed['agents']} ag + {opt_needed.get('decoys',0)} cham "
                                f"— infiltrando mais.")
                        else:
                            self.log(jid, "warn",
                                f"Missão {current_mission} impossível mesmo com risco≤{max_risk}% e {available} disponíveis. "
                                f"Considere aumentar max_detection_risk.")
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
                    ok, msg = self._execute_internal_mission(
                        client,
                        city_id,
                        target_city_id,
                        island_id,
                        8,
                        stationed,
                        0,
                        target_group.get("spy_id"),
                    )
                    self.log(jid, "info", f"Recall: {msg}")
                    wait = self._next_wait_for_group(target_group, ARRIVAL_WAIT, target_groups)
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait,
                        reschedule_inputs={**inputs, "__recovery": {"phase": "done",
                            "missions_done": missions_done}})
                else:
                    phase = "done"

            # ── Phase: done ───────────────────────────────────────────────────
            if phase == "done":
                self._train_missing_spies(jid, client, city_id)
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

    def _get_target_groups(
        self,
        state: dict,
        city_id: str,
        city_name: str = "",
        owner: str = "",
    ) -> list[dict[str, Any]]:
        """Return ALL groups for this target — both stationed (has city_id) and in-transit (has city_name only).

        In-transit spies going to a distant city don't have target_city_id in the HTML block,
        but DO have city_name matching the target and return_timestamp for arrival time.
        We must include both sets to correctly count total spies and get arrival timestamp.
        """
        groups = state.get("target_groups") or []
        city_id_str = str(city_id or "").strip()
        city_name_norm = str(city_name or "").strip().lower()
        owner_norm = str(owner or "").strip().lower()

        result: list[dict[str, Any]] = []
        seen_spy_ids: set[str] = set()

        for group in groups:
            gid = str(group.get("spy_id") or "")
            if gid in seen_spy_ids:
                continue
            # Match by target_city_id
            if city_id_str and str(group.get("target_city_id") or "").strip() == city_id_str:
                result.append(group)
                if gid:
                    seen_spy_ids.add(gid)
                continue
            # Match by city_name (for in-transit groups without target_city_id)
            g_city = str(group.get("city_name") or "").strip().lower()
            g_owner = str(group.get("owner") or "").strip().lower()
            if city_name_norm and g_city == city_name_norm:
                if not owner_norm or g_owner == owner_norm:
                    result.append(group)
                    if gid:
                        seen_spy_ids.add(gid)
                    continue
            # Also match by owner if city_name not available (some groups have owner in city_name field)
            if owner_norm and g_owner == owner_norm and city_name_norm and not g_city:
                result.append(group)
                if gid:
                    seen_spy_ids.add(gid)

        return result

    def _select_target_group(self, groups: list[dict[str, Any]]) -> dict[str, Any]:
        if not groups:
            return {}
        waiting_groups = [group for group in groups if group.get("is_waiting")]
        if waiting_groups:
            return max(waiting_groups, key=lambda group: int(group.get("count_in_use") or 0))
        travelling_groups = [group for group in groups if group.get("is_travelling")]
        if travelling_groups:
            return max(travelling_groups, key=lambda group: int(group.get("count_in_use") or 0))
        return max(groups, key=lambda group: int(group.get("count_in_use") or 0))

    def _total_needed(self, mission_ids: list, live_params: dict, available: int, max_risk: float = 50.0) -> int:
        """Total spies needed (agents + decoys) for the most demanding pending mission."""
        needed = 1
        for mid in mission_ids:
            opt = find_optimal_agents_decoys(live_params, mid, max_risk, max_risk + 25, 30.0, available)
            if opt:
                total = int(opt.get("agents", 1)) + int(opt.get("decoys", 0))
                needed = max(needed, total)
        return needed

    def _find_no_decoy_option(
        self,
        live_params: dict,
        mission_id: int,
        max_agent_risk: float,
        min_success: float,
        stationed: int,
    ) -> dict | None:
        candidates: list[dict] = []
        for agents in range(1, max(1, int(stationed)) + 1):
            risk = compute_spy_risks(live_params, mission_id, agents, 0)
            if float(risk["agent_risk"]) <= max_agent_risk and float(risk["success"]) >= min_success:
                candidates.append(risk)
        if not candidates:
            return None
        max_success = max(float(c["success"]) for c in candidates)
        success_floor = max(float(min_success), max_success - 5.0)
        plateau = [c for c in candidates if float(c["success"]) >= success_floor]
        return min(plateau, key=lambda c: (int(c["agents"]), float(c["agent_risk"]), -float(c["success"])))

    def _send_spy_raw(self, client, city_id, target_city_id, island_id, mission_id, agents, decoys):
        """Send a spy mission and return (success, message)."""
        result = client.send_spy(
            source_city_id=city_id, target_city_id=target_city_id,
            island_id=island_id, mission_id=mission_id,
            agents=agents, decoys=decoys,
        )
        return result.get("success", False), result.get("message", "")

    def _execute_internal_mission(self, client, city_id, target_city_id, island_id, mission_id, agents, decoys, spy_id):
        result = client.execute_spy_mission(
            source_city_id=city_id,
            target_city_id=target_city_id,
            mission_id=mission_id,
            agents=agents,
            decoys=decoys,
            spy_id=spy_id,
            island_id=island_id,
        )
        return result.get("success", False), result.get("message", "")

    def _retreat_target_group(self, client, city_id, target_city_id, spy_id):
        result = client.retreat_spy_group(
            source_city_id=city_id,
            target_city_id=target_city_id,
            spy_id=spy_id,
        )
        return result.get("success", False), result.get("message", "")

    def _train_missing_spies(self, jid, client, city_id) -> None:
        try:
            state = client.get_safehouse_state(city_id)
            capacity = int(state.get("spy_capacity") or 0)
            total = int(state.get("total_spies") or 0)
            training = int(state.get("training_count") or 0)
            missing = max(0, capacity - total - training)
            if missing <= 0:
                return
            result = client.train_spies(city_id, missing)
            level = "info" if result.get("success") else "warn"
            self.log(jid, level, f"Treino de reposicao: {missing} espiao(oes) - {result.get('message', '')}")
        except Exception as exc:
            self.log(jid, "warn", f"Nao foi possivel treinar reposicao de espioes: {exc}")

    def _next_wait_for_group(self, target_group: dict, fallback_seconds: int,
                             all_groups: list | None = None) -> int:
        """Return seconds to wait until spies arrive/complete. Uses actual countdown if available."""
        import time as _t
        now = int(_t.time())
        # Check all groups for return_timestamp (in-transit groups have it, stationed don't)
        best_ts = None
        for g in (all_groups or ([target_group] if target_group else [])):
            ts = g.get("return_timestamp") if g else None
            if ts:
                remaining = int(ts) - now
                if remaining > 30:
                    if best_ts is None or remaining < best_ts:
                        best_ts = remaining
        if best_ts is not None:
            return best_ts + 30  # 30s buffer after arrival
        return fallback_seconds + random.randint(0, 60)

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
                    "source_city_id": city_id,
                    "target_owner": r.get("target_owner", ""),
                    "target_city_id": r.get("target_city_id", ""),
                    "target_city_name": r.get("target_city_name", ""),
                    "target_x": r.get("target_x"),
                    "target_y": r.get("target_y"),
                    "subject": r.get("subject", ""),
                    "mission_name": r.get("mission_name", ""),
                    "status": r.get("status", ""),
                    "result_status": r.get("result_status", ""),
                    "agents_sent": r.get("agents_sent", 0),
                    "agents_lost": r.get("agents_lost", 0),
                    "decoys_sent": r.get("decoys_sent", 0),
                    "decoys_lost": r.get("decoys_lost", 0),
                    "report_html": r.get("report_html", ""),
                    "report_text": r.get("report_text", ""),
                    "data_json": r.get("data_json", {}),
                    "date_str": r.get("date_str", ""),
                    "mission_id": r.get("mission_id"),
                    "is_read": r.get("is_read", not r.get("unread", False)),
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
