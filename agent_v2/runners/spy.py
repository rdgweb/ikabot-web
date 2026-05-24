"""
Runner de espionagem — ação 15 (SpyRunner).

State machine:
  accumulating → executing → recalling → done

  accumulating: treina/infiltra espiões até ter o suficiente estacionado
  executing:    executa missões de inteligência uma a uma
  recalling:    envia missão 8 para retirar todos os espiões
  done:         notifica Telegram, treina reposição, encerra
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.spy import (
    MISSION_DATA,
    compute_spy_risks,
    find_minimum_agents_decoys,
)
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

ARRIVAL_WAIT    = 20 * 60   # fallback quando return_timestamp indisponível
ERROR_RESCHEDULE = 10 * 60
MAX_RETRIES     = 3         # tentativas por missão antes de pular


@register_runner(15)
class SpyRunner(BaseRunner):
    """State machine de espionagem.

    Fases: accumulating → executing → recalling → done
    """

    # Mapa decoyMissionClass → chave de recurso do snapshot
    _DECOY_RESOURCE_MAP = {
        "decoy_mission_wine":    "wine",
        "decoy_mission_gold":    "gold",
        "decoy_mission_sulfur":  "sulfur",
        "decoy_mission_marble":  "marble",
        "decoy_mission_crystal": "crystal",
        "decoy_mission_wood":    "wood",
    }

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid   = job["job_id"]
        aid   = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        # ── Inputs ────────────────────────────────────────────────────────────
        city_id         = str(inputs.get("city_id") or "").strip()
        target_city_id  = str(inputs.get("target_city_id") or "").strip()
        island_id       = str(inputs.get("island_id") or "").strip()
        target_city_name = str(inputs.get("target_city_name") or "").strip()
        target_owner    = str(inputs.get("target_owner") or "").strip()
        save_reports    = bool(inputs.get("save_reports", True))
        delete_after    = bool(inputs.get("delete_after_save", False))
        recall_after    = bool(inputs.get("recall_after", True))
        inputs = {**inputs, "__ga_id": ga_id}
        safehouse_position = self._resolve_safehouse_position(jid, ga_id, city_id, inputs)
        inputs["safehouse_position"] = safehouse_position

        try:
            max_risk = float(inputs.get("max_detection_risk") or 35)
        except (ValueError, TypeError):
            max_risk = 35.0

        if not city_id or not target_city_id or not island_id:
            self.log(jid, "error", "Inputs obrigatórios ausentes: city_id, target_city_id, island_id")
            return RunnerResult(success=False, data={"error": "missing_inputs"})

        # ── Mission list ──────────────────────────────────────────────────────
        raw_mission = inputs.get("mission_id") or "1"
        try:
            if isinstance(raw_mission, list):
                all_mission_ids = [int(x) for x in raw_mission]
            else:
                all_mission_ids = [int(x.strip()) for x in str(raw_mission).split(",") if x.strip()]
        except (ValueError, TypeError):
            all_mission_ids = [1]
        intel_missions = [m for m in all_mission_ids if m not in (1, 8)]

        # ── Recovery state ────────────────────────────────────────────────────
        recovery = inputs.get("__recovery") if isinstance(inputs.get("__recovery"), dict) else {}
        phase            = recovery.get("phase", "accumulating" if intel_missions else "infiltrating_only")
        missions_pending = recovery.get("missions_pending", list(intel_missions))
        missions_done    = recovery.get("missions_done", [])
        mission_retries  = recovery.get("mission_retries", {})
        # Tracking de espiões em-trânsito para cidades distantes (parser não detecta)
        r_sent_total  = int(recovery.get("sent_total") or 0)
        r_arrival_at  = int(recovery.get("arrival_at") or 0)
        # Risk decay tracking
        prev_risk     = float(recovery.get("prev_remaining_risk") or 0)
        prev_risk_ts  = int(recovery.get("prev_remaining_risk_ts") or 0)
        # Recursos comprometidos em missões anteriores (ainda não refletidos no snapshot)
        committed_res: dict = dict(recovery.get("committed_resources") or {})
        # Atualizado após live_params; closure captura por referência de nome
        current_risk: float = 0.0
        now_ts:       int   = 0

        def _rec(**kw) -> dict:
            """Recovery dict com campos persistentes sempre propagados."""
            base = {
                "sent_total":              r_sent_total,
                "arrival_at":              r_arrival_at,
                "mission_retries":         mission_retries,
                "prev_remaining_risk":     current_risk,
                "prev_remaining_risk_ts":  now_ts or prev_risk_ts,
                "committed_resources":     committed_res,
            }
            base.update(kw)
            return base

        def _risk_wait(risk: float) -> int:
            """Segundos para remainingRisk decair a ~0 (1.2/min observado)."""
            if risk <= 0:
                return 5 * 60
            return max(10 * 60, int(risk / 1.2 * 60) + 60)

        def _train(count: int) -> tuple[bool, str]:
            """Treina count espiões, retorna (success, msg)."""
            r = client.train_spies(city_id, count=count, position=safehouse_position)
            return r.get("success", False), r.get("message", "")

        # ── Credentials + client ──────────────────────────────────────────────
        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais não encontradas para a conta")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            # ── Relatórios pendentes ──────────────────────────────────────────
            if save_reports:
                self._save_reports(
                    jid, client, ga_id, city_id, delete_after,
                    safehouse_position=safehouse_position,
                )

            # ── Estado da safehouse ───────────────────────────────────────────
            self.log(jid, "info", f"[{phase.upper()}] Lendo safehouse {city_id}")
            state    = client.get_safehouse_state(city_id, position=safehouse_position)
            available = int(state.get("available_spies") or 0)
            total     = int(state.get("total_spies") or 0)
            in_use    = int(state.get("in_use_spies") or 0)
            capacity  = int(state.get("spy_capacity") or 0)
            secs_per  = int(state.get("training_secs_per_spy") or 250)
            slots_free = max(0, capacity - total)
            self.log(jid, "info",
                f"Safehouse: total={total} disponíveis={available} em_uso={in_use} "
                f"capacidade={capacity} vagas={slots_free} tempo_treino={secs_per}s/espião")

            # Grupos do alvo (stationed + em-trânsito para cidade distante)
            tgroups   = self._get_target_groups(state, target_city_id, target_city_name, target_owner)
            tgroup    = self._select_target_group(tgroups)
            stationed = sum(int(g.get("count_in_use") or 0) for g in tgroups if g.get("is_waiting"))
            in_transit = sum(int(g.get("count_in_use") or 0) for g in tgroups if g.get("is_travelling"))
            self.log(jid, "info",
                f"Alvo {target_owner}/{target_city_name} (id={target_city_id}): "
                f"estacionados={stationed} em_transito={in_transit} "
                f"grupos={len(tgroups)} status='{tgroup.get('status') or 'sem grupo'}'")

            # ── Live risk data ────────────────────────────────────────────────
            live_params: dict = {}
            md_result:   dict = {}
            try:
                md_result   = client.get_spy_mission_data(city_id, target_city_id, island_id)
                live_params = md_result.get("raw_params", {})
                ti          = md_result.get("target", {})
                current_risk = float(live_params.get("remainingRisk") or 0)
                now_ts       = int(time.time())

                decay_info = ""
                if prev_risk > 0 and prev_risk_ts > 0:
                    elapsed_min = max(0.1, (now_ts - prev_risk_ts) / 60)
                    if current_risk < prev_risk:
                        rate = (prev_risk - current_risk) / elapsed_min
                        decay_info = f" | decaimento={rate:.2f}/min ({prev_risk:.1f}→{current_risk:.1f} em {elapsed_min:.0f}min)"
                    else:
                        decay_info = f" | risco={current_risk:.1f} (sem decaimento em {elapsed_min:.0f}min)"

                self.log(jid, "info",
                    f"Alvo: lv={ti.get('city_level')} inativo={ti.get('is_inactive')} "
                    f"free_spies={ti.get('free_spies')} remaining_risk={current_risk}{decay_info}")
            except Exception as exc:
                self.log(jid, "warn", f"Não foi possível buscar riscos ao vivo: {exc}")

            # ══ FASE: infiltrating_only ═══════════════════════════════════════
            if phase == "infiltrating_only":
                opt = find_minimum_agents_decoys(live_params, 1, max_risk, max_risk + 25, 30.0, available)
                if opt:
                    agents, decoys = opt["agents"], opt.get("decoys", 0)
                    self.log(jid, "info",
                        f"[INFILTRATING_ONLY] Enviando {agents}ag+{decoys}dec "
                        f"→ sucesso={opt['success']}% risco={opt['agent_risk']}%")
                    ok, msg = self._send_spy_raw(client, city_id, target_city_id, island_id, 1, agents, decoys)
                    if ok:
                        wait = self._arrival_wait(tgroups, ARRIVAL_WAIT)
                        self.log(jid, "info", f"Infiltração enviada. Retorno estimado em {wait}s.")
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=wait,
                            reschedule_inputs={**inputs, "__recovery": _rec(phase="done")})
                self.log(jid, "warn", f"Infiltração impossível com {available} disponíveis e risco≤{max_risk}%.")
                self.save_game_client(ga_id, client)
                return RunnerResult(success=True, reschedule_seconds=random.randint(5*60, 15*60))

            # ══ FASE: accumulating ════════════════════════════════════════════
            if phase == "accumulating":
                needed = self._total_needed(missions_pending, live_params,
                                            max(available, capacity, 20), max_risk)

                # Recovery tracking: espiões enviados mas não detectáveis (cidades distantes)
                known_transit = 0
                if r_sent_total > 0 and r_arrival_at > 0:
                    if time.time() < r_arrival_at:
                        known_transit = max(0, r_sent_total - stationed)
                        eta = int(r_arrival_at - time.time())
                        if known_transit > 0:
                            self.log(jid, "info",
                                f"Recovery: {known_transit} espiões em trânsito não detectados pelo parser "
                                f"(chegam em {eta}s)")
                    else:
                        r_sent_total = 0
                        r_arrival_at = 0

                effective_transit = max(in_transit, known_transit)
                current_total = stationed + effective_transit

                self.log(jid, "info",
                    f"Acumulação: necessários={needed} estacionados={stationed} "
                    f"em_transito={effective_transit} (parser={in_transit} recovery={known_transit}) "
                    f"total={current_total}/{needed}")

                # Suficientes estacionados → executar
                if stationed >= needed and stationed > 0:
                    self.log(jid, "info",
                        f"Espiões suficientes estacionados ({stationed}/{needed}). Passando para execução.")
                    phase = "executing"

                # Em-trânsito chegará ao necessário → aguardar
                elif current_total >= needed and effective_transit > 0:
                    wait = self._arrival_wait(tgroups, ARRIVAL_WAIT)
                    if r_arrival_at > 0:
                        wait = max(60, int(r_arrival_at - time.time()) + 60)
                    self.log(jid, "info",
                        f"Reforço em trânsito suficiente ({current_total}/{needed}). "
                        f"Aguardando chegada em {wait}s.")
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait,
                        reschedule_inputs={**inputs, "__recovery": _rec(
                            phase="accumulating",
                            missions_pending=missions_pending,
                            missions_done=missions_done)})

                # Precisa enviar mais espiões
                else:
                    # Tentar treinar se necessário
                    train_wait = self._maybe_train(
                        jid, client, city_id, available, slots_free, secs_per,
                        live_params, max_risk, capacity, total,
                        safehouse_position=safehouse_position)
                    if train_wait:
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=train_wait,
                            reschedule_inputs={**inputs, "__recovery": _rec(
                                phase="accumulating",
                                missions_pending=missions_pending,
                                missions_done=missions_done)})

                    # Tentar infiltração
                    to_send = max(1, needed - current_total)
                    opt = find_minimum_agents_decoys(live_params, 1, max_risk, max_risk + 25, 30.0, available)
                    if not opt:
                        self.log(jid, "warn",
                            f"Infiltração impossível: {available} disponíveis, risco≤{max_risk}%, alvo free_spies={live_params.get('targetFreeSpies',0)}.")
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=random.randint(5*60, 15*60),
                            reschedule_inputs={**inputs, "__recovery": _rec(
                                phase="accumulating",
                                missions_pending=missions_pending,
                                missions_done=missions_done)})

                    ag = min(int(opt["agents"]), to_send, available)
                    dec = min(int(opt.get("decoys") or 0), max(0, available - ag))
                    risk = compute_spy_risks(live_params, 1, ag, dec)
                    if (risk["agent_risk"] > max_risk or risk["decoy_risk"] > max_risk + 25
                            or risk["success"] < 30.0):
                        self.log(jid, "warn",
                            f"Lote inseguro ({ag}ag+{dec}dec): sucesso={risk['success']}% risco={risk['agent_risk']}%.")
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=random.randint(5*60, 15*60),
                            reschedule_inputs={**inputs, "__recovery": _rec(
                                phase="accumulating",
                                missions_pending=missions_pending,
                                missions_done=missions_done)})

                    self.log(jid, "info",
                        f"Infiltrando {ag}ag+{dec}dec (precisamos {needed-current_total} mais) "
                        f"→ sucesso={risk['success']}% risco={risk['agent_risk']}%")
                    ok, msg = self._send_spy_raw(client, city_id, target_city_id, island_id, 1, ag, dec)
                    if ok:
                        time.sleep(3)
                        travel = 0
                        try:
                            fs = client.get_safehouse_state(city_id, position=safehouse_position)
                            fg = self._get_target_groups(fs, target_city_id, target_city_name, target_owner)
                            wait = self._arrival_wait(fg, ARRIVAL_WAIT)
                            for g in fg:
                                ts = g.get("return_timestamp")
                                if ts and int(ts) - int(time.time()) > 30:
                                    travel = int(ts) - int(time.time())
                                    break
                        except Exception:
                            wait = ARRIVAL_WAIT + random.randint(0, 60)
                        new_sent  = r_sent_total + ag + dec
                        new_arriv = int(time.time()) + travel if travel > 0 else 0
                        self.log(jid, "info",
                            f"Infiltração enviada ({msg}). "
                            f"Viagem estimada: {travel}s. Reagendando em {wait}s.")
                    else:
                        self.log(jid, "warn", f"Infiltração falhou: {msg}")
                        new_sent  = r_sent_total
                        new_arriv = r_arrival_at
                        wait = ERROR_RESCHEDULE

                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait,
                        reschedule_inputs={**inputs, "__recovery": _rec(
                            phase="accumulating",
                            missions_pending=missions_pending,
                            missions_done=missions_done,
                            sent_total=new_sent,
                            arrival_at=new_arriv)})

            # ══ FASE: executing ═══════════════════════════════════════════════
            if phase == "executing":
                if not missions_pending:
                    self.log(jid, "info",
                        f"Todas as missões concluídas: {missions_done}. "
                        f"{'Passando para recall.' if recall_after else 'Encerrando sem recall.'}")
                    phase = "recalling" if recall_after else "done"

                elif not tgroup.get("is_waiting"):
                    wait = self._arrival_wait(tgroups, ARRIVAL_WAIT)
                    self.log(jid, "info",
                        f"Espiões ainda não estacionados no alvo (status='{tgroup.get('status','?')}'). "
                        f"Aguardando {wait}s.")
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait,
                        reschedule_inputs={**inputs, "__recovery": _rec(
                            phase="executing",
                            missions_pending=missions_pending,
                            missions_done=missions_done)})

                else:
                    current_mission = missions_pending[0]
                    mname = MISSION_DATA.get(current_mission, {}).get("name", f"Missão {current_mission}")
                    opt = find_minimum_agents_decoys(
                        live_params, current_mission, max_risk, max_risk + 25, 40.0, stationed)

                    if not opt:
                        opt_cap = find_minimum_agents_decoys(
                            live_params, current_mission, max_risk, max_risk + 25, 40.0,
                            max(available, capacity, 20))
                        if opt_cap:
                            need_ag = opt_cap["agents"]
                            need_dec = opt_cap.get("decoys", 0)
                            self.log(jid, "warn",
                                f"{mname}: impossível com {stationed} estacionados. "
                                f"Precisa {need_ag}ag+{need_dec}dec ({need_ag+need_dec} total). "
                                f"Voltando para acumulação.")
                        else:
                            self.log(jid, "warn",
                                f"{mname}: impossível mesmo na capacidade máxima com risco≤{max_risk}%. "
                                f"Aumente max_detection_risk ou verifique o alvo.")
                        phase = "accumulating"

                    else:
                        ag  = opt["agents"]
                        dec = opt.get("decoys", 0)

                        if stationed < ag:
                            self.log(jid, "info",
                                f"{mname}: precisa {ag}ag mas só tem {stationed} estacionados. "
                                f"Voltando para acumulação.")
                            phase = "accumulating"
                        else:
                            # Verificar recursos do chamariz
                            dec, deferred = self._check_decoy_resources(
                                jid, ga_id, city_id, current_mission,
                                live_params, ag, dec, max_risk, client=client,
                                committed_res=committed_res)

                            if deferred:
                                missions_pending = missions_pending[1:] + [current_mission]
                                self.log(jid, "info",
                                    f"{mname} adiada (recurso solicitado). "
                                    f"Nova fila: {[MISSION_DATA.get(m,{}).get('name',m) for m in missions_pending]}")
                                self.save_game_client(ga_id, client)
                                return RunnerResult(success=True, reschedule_seconds=60,
                                    reschedule_inputs={**inputs, "__recovery": _rec(
                                        phase="executing",
                                        missions_pending=missions_pending,
                                        missions_done=missions_done)})

                            actual = compute_spy_risks(live_params, current_mission, ag, dec)
                            self.log(jid, "info",
                                f"Executando {mname} ({current_mission}) com {ag}ag+{dec}dec "
                                f"→ sucesso={actual['success']}% risco_agente={actual['agent_risk']}% "
                                f"risco_chamariz={actual.get('decoy_risk',0)}%")

                            ok, msg = self._execute_internal_mission(
                                client, city_id, target_city_id, island_id,
                                current_mission, ag, dec, tgroup.get("spy_id"),
                                safehouse_position=safehouse_position)

                            if not ok:
                                if "insuficiente" in msg.lower() or "insufficient" in msg.lower():
                                    self.log(jid, "warn",
                                        f"{mname}: recursos insuficientes ({msg}). "
                                        f"Aguardando 20min para reposição.")
                                    self.save_game_client(ga_id, client)
                                    return RunnerResult(success=True, reschedule_seconds=20 * 60,
                                        reschedule_inputs={**inputs, "__recovery": _rec(
                                            phase="executing",
                                            missions_pending=missions_pending,
                                            missions_done=missions_done)})
                                self.log(jid, "warn",
                                    f"{mname}: falhou ao enviar ({msg}). Reagendando em {ERROR_RESCHEDULE}s.")
                                self.save_game_client(ga_id, client)
                                return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE,
                                    reschedule_inputs={**inputs, "__recovery": _rec(
                                        phase="executing",
                                        missions_pending=missions_pending,
                                        missions_done=missions_done)})

                            self.log(jid, "info", f"{mname}: enviada ({msg}). Aguardando 15s para resultado.")
                            # Registrar recurso de chamariz consumido
                            if dec > 0:
                                mdata_cr = (live_params.get("missionData") or {}).get(str(current_mission)) or {}
                                decoy_class_cr = str(mdata_cr.get("decoyMissionClass") or "")
                                rk_cr = self._DECOY_RESOURCE_MAP.get(decoy_class_cr, "")
                                cost_cr = int(list((mdata_cr.get("301") or {}).values())[0]) if mdata_cr.get("301") else 0
                                if rk_cr and cost_cr:
                                    spent = dec * cost_cr
                                    committed_res[rk_cr] = committed_res.get(rk_cr, 0) + spent
                            time.sleep(15)
                            succeeded = True
                            if save_reports:
                                fresh = self._save_reports(
                                    jid, client, ga_id, city_id, delete_after,
                                    return_reports=True, safehouse_position=safehouse_position,
                                )
                                succeeded = self._mission_succeeded(fresh, current_mission, target_owner)

                            # After any mission (success or fail), riskAfter is added to remaining.
                            # Wait for that to decay before next mission.
                            mdata_live  = (live_params.get("missionData") or {}).get(str(current_mission)) or {}
                            risk_after  = float(mdata_live.get("riskAfter") or 0)
                            # Fallback: MISSION_DATA tem riskAfter conhecido se live_params não trouxe
                            if risk_after <= 0:
                                risk_after = float(MISSION_DATA.get(current_mission, {}).get("risk_after") or 0)
                            post_risk = current_risk + risk_after
                            post_risk_wait = _risk_wait(post_risk) if post_risk > 2 else random.randint(45, 120)

                            if succeeded:
                                self.log(jid, "info",
                                    f"{mname}: confirmada com sucesso. "
                                    f"Concluídas: {missions_done + [current_mission]} | "
                                    f"Pendentes: {missions_pending[1:]}. "
                                    f"Aguardando {post_risk_wait}s para risco decair (riskAfter={risk_after:.0f}).")
                                missions_done.append(current_mission)
                                missions_pending = missions_pending[1:]
                                mission_retries.pop(str(current_mission), None)
                                wait = post_risk_wait
                                next_phase = "executing" if missions_pending else "recalling"
                            else:
                                retries = int(mission_retries.get(str(current_mission), 0))
                                if retries < MAX_RETRIES:
                                    mission_retries[str(current_mission)] = retries + 1
                                    wait = post_risk_wait
                                    self.log(jid, "warn",
                                        f"{mname}: falhou (tentativa {retries+1}/{MAX_RETRIES}). "
                                        f"riskAfter={risk_after:.0f} + remaining={current_risk:.1f} → "
                                        f"aguardando {wait}s ({wait//60}min) para risco decair.")
                                    next_phase = "executing"
                                else:
                                    self.log(jid, "warn",
                                        f"{mname}: falhou {MAX_RETRIES}x seguidas. Pulando. "
                                        f"Pendentes restantes: {missions_pending[1:]}")
                                    missions_pending = missions_pending[1:]
                                    mission_retries.pop(str(current_mission), None)
                                    wait = 5 * 60
                                    next_phase = "executing" if missions_pending else "recalling"

                            self.save_game_client(ga_id, client)
                            return RunnerResult(success=True, reschedule_seconds=wait,
                                reschedule_inputs={**inputs, "__recovery": _rec(
                                    phase=next_phase,
                                    missions_pending=missions_pending,
                                    missions_done=missions_done)})

                if phase == "accumulating":
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=ARRIVAL_WAIT,
                        reschedule_inputs={**inputs, "__recovery": _rec(
                            phase="accumulating",
                            missions_pending=missions_pending,
                            missions_done=missions_done)})

            # ══ FASE: recalling ════════════════════════════════════════════════
            if phase == "recalling":
                if stationed > 0:
                    recall_risk = compute_spy_risks(live_params, 8, 1, 0).get("agent_risk", 0) if live_params else 0
                    if recall_risk > 60.0:
                        wait = _risk_wait(recall_risk)
                        self.log(jid, "warn",
                            f"Recall: risco muito alto ({recall_risk:.1f}% > 60%). "
                            f"Aguardando {wait}s ({wait//60}min) para risco baixar.")
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=wait,
                            reschedule_inputs={**inputs, "__recovery": _rec(
                                phase="recalling", missions_done=missions_done)})

                    self.log(jid, "info",
                        f"Recall: chamando {stationed} espião(ões) de volta "
                        f"(missão 8, risco={recall_risk:.1f}%)")
                    ok, msg = self._execute_internal_mission(
                        client, city_id, target_city_id, island_id,
                        8, 1, 0, tgroup.get("spy_id"),
                        safehouse_position=safehouse_position)
                    if ok:
                        wait = self._arrival_wait(tgroups, ARRIVAL_WAIT)
                        self.log(jid, "info",
                            f"Recall enviado ({msg}). Espiões retornam em ~{wait}s.")
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=wait,
                            reschedule_inputs={**inputs, "__recovery": _rec(
                                phase="done", missions_done=missions_done)})
                    self.log(jid, "warn", f"Recall falhou: {msg}. Tentando novamente em 10min.")
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=ERROR_RESCHEDULE,
                        reschedule_inputs={**inputs, "__recovery": _rec(
                            phase="recalling", missions_done=missions_done)})
                phase = "done"

            # ══ FASE: done ══════════════════════════════════════════════════
            if phase == "done":
                missions_failed = [m for m in intel_missions if m not in missions_done]
                self.log(jid, "info",
                    f"Espionagem concluída! "
                    f"Missões realizadas: {[MISSION_DATA.get(m,{}).get('name',m) for m in missions_done]} | "
                    f"Falharam: {[MISSION_DATA.get(m,{}).get('name',m) for m in missions_failed]}")
                self._notify_telegram(jid, ga_id, target_owner, target_city_name,
                                      missions_done, missions_failed)
                self._replenish_spies(
                    jid, client, city_id, capacity, total, secs_per,
                    safehouse_position=safehouse_position,
                )
                self.save_game_client(ga_id, client)
                return RunnerResult(success=True, data={"missions_done": missions_done})

            self.save_game_client(ga_id, client)
            return RunnerResult(success=True, reschedule_seconds=ERROR_RESCHEDULE)

        except Exception as exc:
            self.log(jid, "error", f"Erro inesperado na espionagem: {type(exc).__name__}: {exc}")
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE,
                                data={"error": str(exc)})

    def _resolve_safehouse_position(self, job_id: str, game_account_id: str, city_id: str, inputs: dict[str, Any]) -> int:
        raw_position = inputs.get("safehouse_position")
        try:
            position = int(raw_position)
            if position > 0:
                return position
        except (TypeError, ValueError):
            pass

        snapshot = self.get_snapshot(job_id, game_account_id)
        cities = (snapshot or {}).get("cities") or []
        iterable = cities.values() if isinstance(cities, dict) else cities
        city = next((c for c in iterable if str(c.get("id") or "") == str(city_id)), None)
        buildings = (city or {}).get("buildings") or []
        for building in buildings:
            name = str(building.get("building") or building.get("type") or "").strip().lower()
            if name not in {"safehouse", "hideout"}:
                continue
            for key in ("position", "building_position", "slot"):
                try:
                    position = int(building.get(key))
                except (TypeError, ValueError):
                    continue
                if position > 0:
                    self.log(job_id, "info", f"Safehouse resolvida pelo snapshot: cidade={city_id} pos={position}")
                    return position

        self.log(job_id, "warn", f"Safehouse não encontrada no snapshot da cidade {city_id}; usando fallback pos=19")
        return 19

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _maybe_train(self, jid, client, city_id: str, available: int, slots_free: int,
                     secs_per: int, live_params: dict, max_risk: float,
                     capacity: int, total: int, *,
                     safehouse_position: int) -> int | None:
        """Tenta treinar espiões se necessário. Retorna wait_seconds ou None."""
        if available > 0:
            current_opt = find_minimum_agents_decoys(
                live_params, 1, max_risk, max_risk + 25, 30.0, available
            )
            if current_opt:
                return None

            # Tem disponíveis mas não consegue infiltrar — treinar mínimo adicional
            for try_total in range(available + 1, min(capacity + 1, available + 15)):
                if find_minimum_agents_decoys(live_params, 1, max_risk, max_risk + 25, 30.0, try_total):
                    to_train = min(try_total - available, slots_free)
                    if to_train <= 0:
                        self.log(jid, "warn",
                            f"Infiltração impossível com {available} espiões (mín={try_total}) "
                            f"e safehouse cheia ({total}/{capacity}). "
                            f"Alvo muito seguro para max_risk={max_risk}%.")
                        return None
                    ok, msg = client.train_spies(city_id, count=to_train, position=safehouse_position)
                    if ok:
                        wait = secs_per * to_train + 60
                        self.log(jid, "info",
                            f"Infiltração impossível com {available} disponíveis (mín={try_total}). "
                            f"Treinando {to_train} espião(ões) → pronto em {wait}s.")
                    else:
                        wait = random.randint(5*60, 15*60)
                        self.log(jid, "warn",
                            f"Treino falhou: {msg}. Aguardando {wait}s.")
                    return wait
            self.log(jid, "warn",
                f"Infiltração impossível mesmo na capacidade máxima ({capacity}). "
                f"Alvo muito seguro para max_risk={max_risk}%.")
            return None

        # available=0 — treinar para ter espiões em casa
        if slots_free <= 0:
            self.log(jid, "warn",
                f"Sem espiões disponíveis e safehouse cheia ({total}/{capacity}). "
                f"Aguardando retorno de espiões em missão.")
            return random.randint(10*60, 20*60)

        to_train = min(5, slots_free)  # treina até 5 por vez
        ok, msg = client.train_spies(city_id, count=to_train, position=safehouse_position)
        if ok:
            wait = secs_per * to_train + 60
            custo_ouro    = to_train * 150
            custo_cristal = to_train * 54
            self.log(jid, "info",
                f"Sem espiões disponíveis. Treinando {to_train} espião(ões) "
                f"(custo: {custo_ouro} ouro + {custo_cristal} cristal) → pronto em {wait}s.")
        else:
            wait = random.randint(10*60, 20*60)
            self.log(jid, "warn",
                f"Treino falhou ({msg}). Sem recursos ou acesso restrito. "
                f"Aguardando {wait}s.")
        return wait

    def _arrival_wait(self, groups: list, fallback: int) -> int:
        """Segundos até o primeiro grupo em-trânsito chegar. Usa return_timestamp se disponível."""
        now = int(time.time())
        best = None
        for g in groups:
            ts = g.get("return_timestamp") if g else None
            if ts:
                rem = int(ts) - now
                if rem > 30 and (best is None or rem < best):
                    best = rem
        return (best + 30) if best is not None else (fallback + random.randint(0, 60))

    def _total_needed(self, mission_ids: list, live_params: dict,
                      available: int, max_risk: float) -> int:
        """Total de espiões (agentes+chamarizes) necessários para a missão mais exigente."""
        needed = 1
        for mid in mission_ids:
            opt = find_minimum_agents_decoys(live_params, mid, max_risk, max_risk + 25, 30.0, available)
            if opt:
                needed = max(needed, int(opt.get("agents", 1)) + int(opt.get("decoys", 0)))
        return needed

    def _get_target_groups(self, state: dict, city_id: str,
                           city_name: str = "", owner: str = "") -> list[dict]:
        """Retorna todos os grupos do alvo — estacionados E em-trânsito."""
        groups       = state.get("target_groups") or []
        city_id_str  = str(city_id or "").strip()
        city_norm    = str(city_name or "").strip().lower()
        owner_norm   = str(owner or "").strip().lower()
        result, seen = [], set()

        for g in groups:
            gid = str(g.get("spy_id") or "")
            if gid and gid in seen:
                continue
            # Prioridade: match por city_id
            if city_id_str and str(g.get("target_city_id") or "").strip() == city_id_str:
                result.append(g)
                if gid: seen.add(gid)
                continue
            # Fallback: match por city_name (em-trânsito não tem city_id)
            g_city  = str(g.get("city_name") or "").strip().lower()
            g_owner = str(g.get("owner") or "").strip().lower()
            if city_norm and g_city == city_norm and (not owner_norm or g_owner == owner_norm):
                result.append(g)
                if gid: seen.add(gid)
                continue
            # Fallback: match por owner quando city_name ausente
            if owner_norm and g_owner == owner_norm and city_norm and not g_city:
                result.append(g)
                if gid: seen.add(gid)

        return result

    def _select_target_group(self, groups: list) -> dict:
        """Seleciona grupo principal: prefere estacionado, depois em-trânsito."""
        if not groups:
            return {}
        waiting   = [g for g in groups if g.get("is_waiting")]
        travelling = [g for g in groups if g.get("is_travelling")]
        pool = waiting or travelling or groups
        return max(pool, key=lambda g: int(g.get("count_in_use") or 0))

    def _send_spy_raw(self, client, city_id, target_city_id, island_id, mission_id, agents, decoys):
        r = client.send_spy(source_city_id=city_id, target_city_id=target_city_id,
                            island_id=island_id, mission_id=mission_id,
                            agents=agents, decoys=decoys)
        return r.get("success", False), r.get("message", "")

    def _execute_internal_mission(self, client, city_id, target_city_id, island_id,
                                  mission_id, agents, decoys, spy_id, *,
                                  safehouse_position: int):
        r = client.execute_spy_mission(source_city_id=city_id, target_city_id=target_city_id,
                                       mission_id=mission_id, agents=agents, decoys=decoys,
                                       spy_id=spy_id, island_id=island_id, position=safehouse_position)
        return r.get("success", False), r.get("message", "")

    def _save_reports(self, jid, client, ga_id, city_id, delete_after,
                      return_reports: bool = False,
                      safehouse_position: int = 19) -> list:
        """Coleta, salva e opcionalmente retorna relatórios da safehouse."""
        try:
            reports = client.get_spy_reports(city_id, position=safehouse_position)
            if not reports:
                return []
            dicts = [{
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
            } for r in reports]
            res = self.hub.save_spy_reports(ga_id, dicts)
            saved, new = res.get("saved", 0), res.get("new_count", 0)
            self.log(jid, "info", f"Relatórios: {saved} total ({new} novos)")
            if delete_after and new > 0:
                for r in reports[:new]:
                    try:
                        client.delete_spy_report(city_id, r.get("report_id"), position=safehouse_position)
                    except Exception:
                        pass
            return reports if return_reports else []
        except Exception as exc:
            self.log(jid, "warn", f"Erro ao coletar relatórios: {exc}")
            return []

    def _mission_succeeded(self, reports: list, mission_id: int, target_owner: str) -> bool:
        """True se o relatório mais recente para esta missão mostra sucesso."""
        if not reports:
            return True  # sem relatório ainda = assume sucesso (pode ter atrasado)
        owner_l = (target_owner or "").lower()
        for r in reports[:5]:
            if owner_l and (r.get("target_owner") or "").lower() != owner_l:
                continue
            if r.get("mission_id") is not None and r.get("mission_id") != mission_id:
                continue
            status = (r.get("result_status") or "").lower()
            if any(w in status for w in ("falhada", "fail", "capturado")):
                return False
            if any(w in status for w in ("sucesso", "success")):
                return True
        return True

    def _check_decoy_resources(self, jid, ga_id, city_id, mission_id,
                                live_params, agents, decoys, max_risk,
                                client=None, committed_res: dict | None = None) -> tuple[int, bool]:
        """Verifica recursos para chamarizes. Retorna (decoys_acessíveis, recurso_solicitado)."""
        if decoys <= 0:
            return 0, False
        try:
            mdata = (live_params.get("missionData") or {}).get(str(mission_id)) or {}
            decoy_class  = str(mdata.get("decoyMissionClass") or "")
            resource_key = self._DECOY_RESOURCE_MAP.get(decoy_class, "")
            cost_map     = mdata.get("301") or {}
            cost_each    = int(list(cost_map.values())[0]) if cost_map else 0

            if not resource_key or cost_each <= 0:
                return decoys, False

            snap   = self.hub.get_snapshot(game_account_id=ga_id)
            cities = snap.get("cities") or []
            spy_city = next((c for c in cities if str(c.get("id") or "") == str(city_id)), {})
            # Recursos estão direto no dict da cidade (city["sulfur"]), não em city["resources"]
            have_raw = spy_city.get(resource_key)
            # Subtrair o que já foi comprometido em missões anteriores desta execução
            committed = int((committed_res or {}).get(resource_key) or 0)

            have   = max(0, int(have_raw or 0) - committed)
            need   = decoys * cost_each
            afford = min(decoys, have // cost_each) if cost_each > 0 else decoys

            if afford >= decoys:
                return decoys, False

            mname = MISSION_DATA.get(mission_id, {}).get("name", f"M{mission_id}")
            self.log(jid, "warn",
                f"{mname}: chamarizes precisam de {resource_key} "
                f"({decoy_class}, {cost_each}/cham × {decoys} = {need} total). "
                f"Disponível: {have} → reduzindo de {decoys} para {afford} chamarizes.")

            # Tentar transporte de outra cidade
            if client and resource_key != "gold" and afford < decoys:
                source = None
                best   = 0
                for c in cities:
                    if str(c.get("id") or "") == str(city_id):
                        continue
                    amt = int(c.get(resource_key) or 0)  # recursos em city["sulfur"] etc.
                    transferable = int(amt * 0.8)
                    if transferable >= need and transferable > best:
                        best, source = transferable, c

                if source:
                    send = min(best, need * 3)
                    try:
                        client.send_resources(int(source["id"]), int(city_id), {resource_key: send})
                        self.log(jid, "info",
                            f"Transporte solicitado: {send} {resource_key} "
                            f"de {source.get('name', source['id'])} → {spy_city.get('name', city_id)}. "
                            f"Missão {mname} adiada para depois das outras.")
                        # Registrar comprometido para que próxima missão saiba
                        if committed_res is not None:
                            committed_res[resource_key] = committed_res.get(resource_key, 0) + send
                        return afford, True
                    except Exception as te:
                        self.log(jid, "warn", f"Falha ao solicitar transporte de {resource_key}: {te}")

            return afford, False
        except Exception as exc:
            self.log(jid, "warn", f"Erro ao verificar recursos de chamarizes: {exc}")
            return decoys, False

    def _notify_telegram(self, jid, ga_id, target_owner, target_city_name,
                         missions_done: list, missions_failed: list) -> None:
        try:
            done_names   = [MISSION_DATA.get(m, {}).get("name", f"M{m}") for m in missions_done]
            failed_names = [MISSION_DATA.get(m, {}).get("name", f"M{m}") for m in missions_failed]
            lines = [
                "🕵️ *Espionagem concluída*",
                f"Alvo: *{target_owner}* — {target_city_name}",
                f"✅ {', '.join(done_names) if done_names else '—'}",
            ]
            if failed_names:
                lines.append(f"❌ Falharam: {', '.join(failed_names)}")
            self.hub.send_notification(game_account_id=ga_id,
                                       title="Espionagem concluída",
                                       body="\n".join(lines),
                                       event_key="spy_done")
        except Exception as exc:
            logger.warning("Telegram spy_done falhou: %s", exc)

    def _replenish_spies(self, jid, client, city_id: str,
                         capacity: int, total: int, secs_per: int, *,
                         safehouse_position: int) -> None:
        """Treina espiões de reposição ao fim do ciclo."""
        try:
            missing = max(0, capacity - total)
            if missing <= 0:
                return
            ok, msg = client.train_spies(city_id, count=missing, position=safehouse_position)
            if ok:
                wait = secs_per * missing
                self.log(jid, "info",
                    f"Reposição: treinando {missing} espião(ões) → prontos em ~{wait}s ({wait//60}min).")
            else:
                self.log(jid, "warn", f"Reposição: treino falhou ({msg}).")
        except Exception as exc:
            self.log(jid, "warn", f"Reposição: erro ao treinar: {exc}")
