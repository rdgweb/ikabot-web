"""
Runner de espionagem — ação 15 (SpyRunner).

State machine:
  accumulating → executing → recalling → done

  accumulating: infiltra espiões em lote máximo até ter suficiente estacionado.
                Espera risco decair entre lotes. Conta ciclos sem progresso.
  executing:    executa missões internas uma a uma, detecta perdas, adapta risco.
  recalling:    envia missão 8 para retirar todos os espiões.
  done:         notifica Telegram, treina reposição, encerra.

Princípios de design:
  - Lote máximo: a cada infiltração, manda o MÁXIMO de agentes possível num único envio.
    Quanto mais agentes por lote, menos viagens, menos remaining_risk acumulado.
  - Risco projetado: calcula o total necessário considerando o risco que as infiltrações
    futuras vão adicionar (não assume remaining=0 ao executar).
  - Decay obrigatório: após qualquer infiltração ou missão interna, espera o riskAfter
    decair antes de agir de novo.
  - Recuperação de perdas: se espiões são capturados numa missão interna, volta a acumular.
  - Escape de deadlock: após MAX_NO_PROGRESS ciclos sem progresso, relaxa o risco
    progressivamente e eventualmente executa o que tem ou encerra.
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
    find_best_score_combo,
    find_minimum_agents_decoys,
)
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

ARRIVAL_WAIT     = 20 * 60   # fallback quando return_timestamp indisponível
ERROR_RESCHEDULE = 10 * 60
MAX_RETRIES      = 3         # tentativas por missão interna antes de pular
MAX_NO_PROGRESS  = 8         # ciclos sem progresso antes de tentar escape
RISK_AFTER_M1    = 5.0       # riskAfter de missão 1 (infiltração)
RISK_DECAY_RATE  = 1.2       # pontos de risco que decaem por minuto (observado)


@register_runner(15)
class SpyRunner(BaseRunner):
    """State machine de espionagem inteligente."""

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
        city_id          = str(inputs.get("city_id") or "").strip()
        target_city_id   = str(inputs.get("target_city_id") or "").strip()
        island_id        = str(inputs.get("island_id") or "").strip()
        target_city_name = str(inputs.get("target_city_name") or "").strip()
        target_owner     = str(inputs.get("target_owner") or "").strip()
        target_owner_id  = str(inputs.get("target_owner_id") or "").strip()
        save_reports     = bool(inputs.get("save_reports", True))
        delete_after     = bool(inputs.get("delete_after_save", False))
        recall_after     = bool(inputs.get("recall_after", True))
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

        # ── Lista de missões ──────────────────────────────────────────────────
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
        no_progress      = int(recovery.get("no_progress_cycles") or 0)
        last_stationed   = int(recovery.get("last_stationed") or 0)
        r_sent_total     = int(recovery.get("sent_total") or 0)
        r_arrival_at     = int(recovery.get("arrival_at") or 0)
        prev_risk        = float(recovery.get("prev_remaining_risk") or 0)
        prev_risk_ts     = int(recovery.get("prev_remaining_risk_ts") or 0)
        committed_res: dict = dict(recovery.get("committed_resources") or {})
        mission_risks: dict = dict(recovery.get("mission_risks") or {})
        current_risk: float = 0.0
        now_ts:       int   = 0

        def _rec(**kw) -> dict:
            base = {
                "sent_total":              r_sent_total,
                "arrival_at":              r_arrival_at,
                "mission_retries":         mission_retries,
                "prev_remaining_risk":     current_risk,
                "prev_remaining_risk_ts":  now_ts or prev_risk_ts,
                "committed_resources":     committed_res,
                "no_progress_cycles":      no_progress,
                "last_stationed":          last_stationed,
                "mission_risks":           mission_risks,
            }
            base.update(kw)
            return base

        def _risk_wait(risk: float) -> int:
            """Segundos para risk decair a ~0 (RISK_DECAY_RATE/min)."""
            if risk <= 0:
                return 3 * 60
            return max(5 * 60, int(risk / RISK_DECAY_RATE * 60) + 60)

        def _effective_max_risk() -> float:
            """max_risk com relaxamento progressivo após no_progress ciclos."""
            relaxation = max(0, no_progress - 3) * 5.0  # +5% a cada ciclo após 3
            return min(max_risk + 20.0, max_risk + relaxation)

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
                    target_owner_id=target_owner_id,
                    safehouse_position=safehouse_position,
                )

            # ── Estado da safehouse ───────────────────────────────────────────
            self.log(jid, "info", f"[{phase.upper()}] Lendo safehouse {city_id}")
            state     = client.get_safehouse_state(city_id, position=safehouse_position)
            available = int(state.get("available_spies") or 0)
            total     = int(state.get("total_spies") or 0)
            in_use    = int(state.get("in_use_spies") or 0)
            capacity  = int(state.get("spy_capacity") or 0)
            secs_per  = int(state.get("training_secs_per_spy") or 250)
            slots_free = max(0, capacity - total)
            self.log(jid, "info",
                f"Safehouse: total={total} disponíveis={available} em_uso={in_use} "
                f"capacidade={capacity} vagas={slots_free}")

            tgroups   = self._get_target_groups(state, target_city_id, target_city_name, target_owner)
            tgroup    = self._select_target_group(tgroups)
            stationed = sum(int(g.get("count_in_use") or 0) for g in tgroups if g.get("is_waiting"))
            in_transit = sum(int(g.get("count_in_use") or 0) for g in tgroups if g.get("is_travelling"))
            self.log(jid, "info",
                f"Alvo {target_owner}/{target_city_name}: "
                f"estacionados={stationed} em_transito={in_transit} "
                f"(ant={last_stationed} no_progress={no_progress})")

            # Detectar perda de espiões entre ciclos
            if stationed < last_stationed and last_stationed > 0:
                lost = last_stationed - stationed
                self.log(jid, "warn",
                    f"⚠ Detectada perda de {lost} espião(ões) desde o último ciclo "
                    f"(era {last_stationed}, agora {stationed}). Voltando para acumulação.")
                if phase == "executing":
                    phase = "accumulating"
                    no_progress = 0

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
                    rate = (prev_risk - current_risk) / elapsed_min if current_risk < prev_risk else 0
                    decay_info = (f" | decaiu {prev_risk:.1f}→{current_risk:.1f} em {elapsed_min:.0f}min"
                                  f" ({rate:.2f}/min)")
                else:
                    decay_info = f" | risco={current_risk:.1f}"

                self.log(jid, "info",
                    f"Alvo: lv={ti.get('city_level')} inativo={ti.get('is_inactive')} "
                    f"free_spies={ti.get('free_spies')} remaining_risk={current_risk}{decay_info}")

                # TODO: detectar férias/cidade sumida via missionData real do game
                # Precisamos capturar o HTML de uma cidade em férias para saber
                # exatamente o que o game retorna antes de implementar essa lógica.

                # Log raw missionData for debugging formula discrepancies
                raw_md = live_params.get("missionData", {})
                if raw_md:
                    for mid_str, mval in raw_md.items():
                        if isinstance(mval, dict):
                            self.log(jid, "debug",
                                f"missionData[{mid_str}]: successChance={mval.get('successChance')} "
                                f"riskBefore={mval.get('riskBefore')} riskPerSpy={mval.get('riskPerSpy')} "
                                f"riskAfter={mval.get('riskAfter')}")
            except Exception as exc:
                self.log(jid, "warn", f"Não foi possível buscar riscos ao vivo: {exc}")

            # ══ FASE: infiltrating_only ═══════════════════════════════════════
            if phase == "infiltrating_only":
                eff_risk = _effective_max_risk()
                opt = find_minimum_agents_decoys(live_params, 1, eff_risk, eff_risk + 25, 30.0, available)
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
                self.log(jid, "warn",
                    f"Infiltração impossível com {available} disponíveis e risco≤{eff_risk}%.")
                self.save_game_client(ga_id, client)
                return RunnerResult(success=True, reschedule_seconds=random.randint(5*60, 15*60))

            # ══ FASE: accumulating ════════════════════════════════════════════
            if phase == "accumulating":
                eff_risk = _effective_max_risk()

                # Calcula agentes necessários usando risco projetado
                needed = self._total_needed_projected(
                    missions_pending, live_params, stationed, available,
                    max(capacity, 20), eff_risk)

                # Calcular e persistir riscos por missão (para exibição no hub)
                if live_params and missions_pending:
                    new_mission_risks = {}
                    for mid in missions_pending:
                        opt = find_best_score_combo(live_params, mid, max(capacity, 20))
                        if opt:
                            new_mission_risks[str(mid)] = {
                                "agents":     opt["agents"],
                                "decoys":     opt.get("decoys", 0),
                                "agent_risk": round(opt.get("agent_risk", 0), 1),
                                "decoy_risk": round(opt.get("decoy_risk", 0), 1),
                                "success":    round(opt.get("success", 0), 1),
                                "score":      round(opt.get("score", 0), 2),
                            }
                        else:
                            new_mission_risks[str(mid)] = None
                    mission_risks = new_mission_risks  # atualiza var local → _rec() pega

                # Recovery tracking: espiões em-trânsito para cidades distantes
                known_transit = 0
                if r_sent_total > 0 and r_arrival_at > 0:
                    if time.time() < r_arrival_at:
                        known_transit = max(0, r_sent_total - stationed)
                        eta = int(r_arrival_at - time.time())
                        if known_transit > 0:
                            self.log(jid, "info",
                                f"Recovery: {known_transit} em trânsito não detectados (chegam em {eta}s)")
                    else:
                        r_sent_total = 0
                        r_arrival_at = 0

                effective_transit = max(in_transit, known_transit)
                current_total = stationed + effective_transit

                self.log(jid, "info",
                    f"Acumulação: necessários={needed} estacionados={stationed} "
                    f"em_transito={effective_transit} total={current_total}/{needed} "
                    f"eff_risk={eff_risk:.0f}%")

                # Suficientes estacionados → executar
                if stationed >= needed and stationed > 0:
                    self.log(jid, "info",
                        f"Espiões suficientes ({stationed}/{needed}). Passando para execução.")
                    # Esperar remaining_risk decair antes de executar se necessário
                    if current_risk > 5:
                        wait = _risk_wait(current_risk)
                        self.log(jid, "info",
                            f"remaining_risk={current_risk:.1f} antes de executar. "
                            f"Aguardando {wait}s ({wait//60}min) para decair.")
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=wait,
                            reschedule_inputs={**inputs, "__recovery": _rec(
                                phase="executing",
                                missions_pending=missions_pending,
                                missions_done=missions_done,
                                last_stationed=stationed)})
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
                            missions_done=missions_done,
                            last_stationed=stationed)})

                # Precisa enviar mais espiões
                else:
                    # Verificar se treino é necessário
                    train_wait = self._maybe_train(
                        jid, client, city_id, available, slots_free, secs_per,
                        live_params, eff_risk, capacity, total,
                        safehouse_position=safehouse_position)
                    if train_wait:
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=train_wait,
                            reschedule_inputs={**inputs, "__recovery": _rec(
                                phase="accumulating",
                                missions_pending=missions_pending,
                                missions_done=missions_done,
                                last_stationed=stationed)})

                    # ── LOTE MÁXIMO: manda o máximo de agentes de uma vez ─────
                    agents_still_needed = max(1, needed - current_total)
                    batch = self._find_best_infiltration_batch(
                        live_params, available, agents_still_needed, eff_risk)

                    if not batch:
                        # Impossível infiltrar com espiões disponíveis
                        new_no_progress = no_progress + 1
                        self.log(jid, "warn",
                            f"Infiltração impossível: {available} disponíveis, risco≤{eff_risk:.0f}%, "
                            f"free_spies={live_params.get('targetFreeSpies',0)} "
                            f"[ciclo_sem_progresso={new_no_progress}/{MAX_NO_PROGRESS}]")

                        if new_no_progress >= MAX_NO_PROGRESS:
                            return self._handle_deadlock(
                                jid, ga_id, client, inputs, live_params,
                                stationed, available, eff_risk,
                                missions_pending, missions_done,
                                capacity, city_id, tgroups, recall_after,
                                _rec, _risk_wait)

                        wait = self._backoff_wait(new_no_progress)
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=wait,
                            reschedule_inputs={**inputs, "__recovery": _rec(
                                phase="accumulating",
                                missions_pending=missions_pending,
                                missions_done=missions_done,
                                no_progress_cycles=new_no_progress,
                                last_stationed=stationed)})

                    ag  = batch["agents"]
                    dec = batch.get("decoys", 0)

                    risk = compute_spy_risks(live_params, 1, ag, dec)
                    self.log(jid, "info",
                        f"Infiltração: {ag}ag+{dec}dec "
                        f"→ sucesso={risk['success']}% risco={risk['agent_risk']}% "
                        f"(precisamos {needed} estacionados no total, temos {stationed})")

                    send_result = client.send_spy(
                        source_city_id=city_id, target_city_id=target_city_id,
                        island_id=island_id, mission_id=1, agents=ag, decoys=dec,
                    )
                    ok, msg = send_result.get("success", False), send_result.get("message", "")
                    if ok:
                        # Pegar return_timestamp diretamente do response do send.
                        # Se não vier no response, fazer fallback com releitura da safehouse.
                        travel = 0
                        rt = int(send_result.get("return_timestamp") or 0)
                        now_ts = int(time.time())
                        if rt and rt - now_ts > 30:
                            travel = rt - now_ts
                        else:
                            # Fallback: re-ler safehouse (pode não ter atualizado ainda)
                            try:
                                time.sleep(3)
                                fs = client.get_safehouse_state(city_id, position=safehouse_position)
                                fg = self._get_target_groups(fs, target_city_id, target_city_name, target_owner)
                                for g in fg:
                                    ts = g.get("return_timestamp")
                                    if ts and int(ts) - now_ts > 30:
                                        travel = int(ts) - now_ts
                                        break
                            except Exception:
                                pass

                        # Depois da infiltração: esperar viagem + riskAfter decair.
                        # Usa riskAfter real de M1 do live_params (default RISK_AFTER_M1=5).
                        _md_m1 = (live_params.get("missionData") or {}).get("1") or {}
                        _m1_ra = float(_md_m1.get("riskAfter") or RISK_AFTER_M1)
                        risk_after_total = current_risk + _m1_ra
                        risk_wait_s = _risk_wait(risk_after_total)
                        # Usa o maior entre: tempo de viagem e tempo de risco
                        wait_total = max(
                            travel + 30 if travel > 0 else ARRIVAL_WAIT,
                            risk_wait_s if risk_after_total > 10 else 0
                        )
                        wait_total = max(60, wait_total)

                        new_sent  = r_sent_total + ag + dec
                        new_arriv = int(time.time()) + travel if travel > 0 else 0
                        self.log(jid, "info",
                            f"Infiltração enviada ({msg}). Viagem={travel}s "
                            f"risk_after={risk_after_total:.1f} → aguardando {wait_total}s "
                            f"({wait_total//60}min) para viagem+risco decaírem.")
                    else:
                        self.log(jid, "warn", f"Infiltração falhou: {msg}")
                        new_sent  = r_sent_total
                        new_arriv = r_arrival_at
                        wait_total = ERROR_RESCHEDULE

                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait_total,
                        reschedule_inputs={**inputs, "__recovery": _rec(
                            phase="accumulating",
                            missions_pending=missions_pending,
                            missions_done=missions_done,
                            sent_total=new_sent,
                            arrival_at=new_arriv,
                            no_progress_cycles=0,   # progresso feito, resetar
                            last_stationed=stationed)})

            # ══ FASE: executing ═══════════════════════════════════════════════
            if phase == "executing":
                # Calcular riscos por missão se ainda não temos (ex: transição sem passar pelo accumulating)
                if live_params and missions_pending and not mission_risks:
                    new_mission_risks = {}
                    for mid in missions_pending:
                        opt = find_best_score_combo(live_params, mid, max(stationed, capacity, 20))
                        if opt:
                            new_mission_risks[str(mid)] = {
                                "agents":     opt["agents"],
                                "decoys":     opt.get("decoys", 0),
                                "agent_risk": round(opt.get("agent_risk", 0), 1),
                                "decoy_risk": round(opt.get("decoy_risk", 0), 1),
                                "success":    round(opt.get("success", 0), 1),
                                "score":      round(opt.get("score", 0), 2),
                            }
                        else:
                            new_mission_risks[str(mid)] = None
                    mission_risks = new_mission_risks

                if not missions_pending:
                    self.log(jid, "info",
                        f"Todas as missões concluídas: {missions_done}. "
                        f"{'Recall.' if recall_after else 'Sem recall.'}")
                    phase = "recalling" if recall_after else "done"

                elif not tgroup.get("is_waiting"):
                    wait = self._arrival_wait(tgroups, ARRIVAL_WAIT)
                    self.log(jid, "info",
                        f"Espiões não estacionados ainda (status='{tgroup.get('status','?')}'). "
                        f"Aguardando {wait}s.")
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=wait,
                        reschedule_inputs={**inputs, "__recovery": _rec(
                            phase="executing",
                            missions_pending=missions_pending,
                            missions_done=missions_done,
                            last_stationed=stationed)})

                else:
                    current_mission = missions_pending[0]
                    mname = MISSION_DATA.get(current_mission, {}).get("name", f"Missão {current_mission}")
                    eff_risk = _effective_max_risk()

                    # Aguardar remaining_risk se estiver alto demais
                    if current_risk > 3:
                        needed_r = self._mission_risk_threshold(live_params, current_mission, stationed, eff_risk)
                        if current_risk > needed_r:
                            wait = _risk_wait(current_risk - needed_r)
                            self.log(jid, "info",
                                f"{mname}: remaining_risk={current_risk:.1f} > threshold={needed_r:.1f}. "
                                f"Aguardando {wait}s ({wait//60}min) para risco decair antes de executar.")
                            self.save_game_client(ga_id, client)
                            return RunnerResult(success=True, reschedule_seconds=wait,
                                reschedule_inputs={**inputs, "__recovery": _rec(
                                    phase="executing",
                                    missions_pending=missions_pending,
                                    missions_done=missions_done,
                                    last_stationed=stationed)})

                    # Scoring: escolhe o combo com melhor custo-benefício entre os estacionados.
                    # Sem thresholds rígidos — pura otimização de score.
                    opt = find_best_score_combo(live_params, current_mission, stationed)

                    if not opt:
                        # Impossível com stationed — verificar se acumulando mais resolve
                        opt_cap = find_best_score_combo(
                            live_params, current_mission, max(available, capacity, 20))
                        if opt_cap:
                            need_ag = opt_cap["agents"]
                            need_dec = opt_cap.get("decoys", 0)
                            self.log(jid, "warn",
                                f"{mname}: impossível com {stationed} estacionados "
                                f"(precisa {need_ag}ag+{need_dec}dec). Voltando para acumulação.")
                        else:
                            self.log(jid, "warn",
                                f"{mname}: impossível mesmo na capacidade máxima com risco≤{eff_risk:.0f}%. "
                                f"Pulando missão.")
                            missions_pending = missions_pending[1:]
                            self.save_game_client(ga_id, client)
                            return RunnerResult(success=True, reschedule_seconds=60,
                                reschedule_inputs={**inputs, "__recovery": _rec(
                                    phase="executing" if missions_pending else "recalling",
                                    missions_pending=missions_pending,
                                    missions_done=missions_done,
                                    last_stationed=stationed)})
                        phase = "accumulating"

                    else:
                        ag  = opt["agents"]
                        dec = opt.get("decoys", 0)

                        # Tabela de decisão — mostra por que escolheu ag/dec
                        self._log_decision_table(jid, live_params, current_mission, eff_risk, stationed, ag, dec)

                        if stationed < ag:
                            self.log(jid, "info",
                                f"{mname}: precisa {ag}ag mas só tem {stationed} estacionados. "
                                f"Voltando para acumulação.")
                            phase = "accumulating"
                        else:
                            # Verificar recursos do chamariz
                            dec, deferred = self._check_decoy_resources(
                                jid, ga_id, city_id, current_mission,
                                live_params, ag, dec, eff_risk, client=client,
                                committed_res=committed_res)

                            if deferred:
                                missions_pending = missions_pending[1:] + [current_mission]
                                self.log(jid, "info",
                                    f"{mname} adiada (recurso solicitado). "
                                    f"Fila: {[MISSION_DATA.get(m,{}).get('name',m) for m in missions_pending]}")
                                self.save_game_client(ga_id, client)
                                return RunnerResult(success=True, reschedule_seconds=60,
                                    reschedule_inputs={**inputs, "__recovery": _rec(
                                        phase="executing",
                                        missions_pending=missions_pending,
                                        missions_done=missions_done,
                                        last_stationed=stationed)})

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
                                        f"{mname}: recursos insuficientes ({msg}). Aguardando 20min.")
                                    self.save_game_client(ga_id, client)
                                    return RunnerResult(success=True, reschedule_seconds=20 * 60,
                                        reschedule_inputs={**inputs, "__recovery": _rec(
                                            phase="executing",
                                            missions_pending=missions_pending,
                                            missions_done=missions_done,
                                            last_stationed=stationed)})
                                self.log(jid, "warn",
                                    f"{mname}: falhou ao enviar ({msg}). Reagendando em {ERROR_RESCHEDULE}s.")
                                self.save_game_client(ga_id, client)
                                return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE,
                                    reschedule_inputs={**inputs, "__recovery": _rec(
                                        phase="executing",
                                        missions_pending=missions_pending,
                                        missions_done=missions_done,
                                        last_stationed=stationed)})

                            # Registrar recurso de chamariz consumido
                            if dec > 0:
                                mdata_cr = (live_params.get("missionData") or {}).get(str(current_mission)) or {}
                                decoy_class_cr = str(mdata_cr.get("decoyMissionClass") or "")
                                rk_cr = self._DECOY_RESOURCE_MAP.get(decoy_class_cr, "")
                                cost_cr = int(list((mdata_cr.get("301") or {}).values())[0]) if mdata_cr.get("301") else 0
                                if rk_cr and cost_cr:
                                    committed_res[rk_cr] = committed_res.get(rk_cr, 0) + dec * cost_cr

                            # Aguarda resultado
                            time.sleep(15)
                            succeeded = True
                            stationed_after = stationed
                            if save_reports:
                                fresh = self._save_reports(
                                    jid, client, ga_id, city_id, delete_after,
                                    return_reports=True,
                                    target_owner_id=target_owner_id,
                                    safehouse_position=safehouse_position,
                                )
                                succeeded = self._mission_succeeded(fresh, current_mission, target_owner)

                            # Verificar se perdeu espiões nesta missão
                            try:
                                state_after = client.get_safehouse_state(city_id, position=safehouse_position)
                                tgroups_after = self._get_target_groups(
                                    state_after, target_city_id, target_city_name, target_owner)
                                stationed_after = sum(
                                    int(g.get("count_in_use") or 0) for g in tgroups_after if g.get("is_waiting"))
                                if stationed_after < stationed:
                                    self.log(jid, "warn",
                                        f"{mname}: perdidos {stationed-stationed_after} espiões na execução "
                                        f"({stationed}→{stationed_after}). "
                                        f"Voltando para acumulação se necessário.")
                            except Exception:
                                stationed_after = stationed

                            # riskAfter da missão
                            mdata_live  = (live_params.get("missionData") or {}).get(str(current_mission)) or {}
                            risk_after  = float(mdata_live.get("riskAfter") or
                                               MISSION_DATA.get(current_mission, {}).get("risk_after", 0) or 0)
                            post_risk   = current_risk + risk_after
                            post_risk_wait = _risk_wait(post_risk) if post_risk > 3 else 30

                            if succeeded:
                                self.log(jid, "info",
                                    f"{mname}: ✓ sucesso. "
                                    f"Concluídas: {missions_done + [current_mission]} | "
                                    f"Pendentes: {missions_pending[1:]}. "
                                    f"riskAfter={risk_after:.0f} → aguardando {post_risk_wait}s ({post_risk_wait//60}min).")
                                missions_done.append(current_mission)
                                missions_pending = missions_pending[1:]
                                mission_retries.pop(str(current_mission), None)
                                next_phase = "executing" if missions_pending else "recalling"
                            else:
                                retries = int(mission_retries.get(str(current_mission), 0))
                                if retries < MAX_RETRIES:
                                    mission_retries[str(current_mission)] = retries + 1
                                    self.log(jid, "warn",
                                        f"{mname}: ✗ falhou (tentativa {retries+1}/{MAX_RETRIES}). "
                                        f"riskAfter={risk_after:.0f} → aguardando {post_risk_wait}s.")
                                    next_phase = "executing"
                                else:
                                    self.log(jid, "warn",
                                        f"{mname}: ✗ falhou {MAX_RETRIES}x. Pulando.")
                                    missions_pending = missions_pending[1:]
                                    mission_retries.pop(str(current_mission), None)
                                    post_risk_wait = 5 * 60
                                    next_phase = "executing" if missions_pending else "recalling"
                                    # Salvar report de "pulado" para que skip_if_valid não re-queie
                                    # a cidade por esta missão durante o TTL padrão.
                                    try:
                                        import uuid as _uuid
                                        self.hub.save_spy_reports(ga_id, [{
                                            "report_id":        f"skipped_{current_mission}_{target_city_id}_{int(time.time())}",
                                            "source_city_id":   city_id,
                                            "target_city_id":   str(target_city_id),
                                            "target_owner":     str(inputs.get("target_owner") or ""),
                                            "target_owner_id":  str(target_owner_id or ""),
                                            "mission_id":       current_mission,
                                            "mission_name":     mname,
                                            "result_status":    "Pulado (risco excessivo)",
                                            "status":           "skipped",
                                            "report_text":      f"Missão {mname} pulada após {MAX_RETRIES} tentativas por risco > {max_risk}%.",
                                        }])
                                    except Exception as _e:
                                        self.log(jid, "warn", f"Falha ao salvar report de missão pulada: {_e}")

                            # Se perdeu espiões e precisa reacumular
                            if stationed_after < stationed:
                                needed_recheck = self._total_needed_projected(
                                    missions_pending if missions_pending else [],
                                    live_params, stationed_after, 0, max(capacity, 20), eff_risk)
                                if missions_pending and stationed_after < needed_recheck:
                                    next_phase = "accumulating"

                            self.save_game_client(ga_id, client)
                            return RunnerResult(success=True, reschedule_seconds=post_risk_wait,
                                reschedule_inputs={**inputs, "__recovery": _rec(
                                    phase=next_phase,
                                    missions_pending=missions_pending,
                                    missions_done=missions_done,
                                    last_stationed=stationed_after)})

                if phase == "accumulating":
                    self.save_game_client(ga_id, client)
                    return RunnerResult(success=True, reschedule_seconds=ARRIVAL_WAIT,
                        reschedule_inputs={**inputs, "__recovery": _rec(
                            phase="accumulating",
                            missions_pending=missions_pending,
                            missions_done=missions_done,
                            last_stationed=stationed)})

            # ══ FASE: recalling ════════════════════════════════════════════════
            if phase == "recalling":
                if stationed > 0:
                    recall_risk = compute_spy_risks(live_params, 8, 1, 0).get("agent_risk", 0) if live_params else 0
                    if recall_risk > 60.0:
                        wait = _risk_wait(recall_risk - 30)
                        self.log(jid, "warn",
                            f"Recall: risco alto ({recall_risk:.1f}% > 60%). "
                            f"Aguardando {wait}s.")
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=wait,
                            reschedule_inputs={**inputs, "__recovery": _rec(
                                phase="recalling", missions_done=missions_done)})

                    self.log(jid, "info",
                        f"Recall: chamando {stationed} espião(ões) de volta (risco={recall_risk:.1f}%)")
                    ok, msg = self._execute_internal_mission(
                        client, city_id, target_city_id, island_id,
                        8, stationed, 0, tgroup.get("spy_id"),
                        safehouse_position=safehouse_position)
                    if ok:
                        wait = self._arrival_wait(tgroups, ARRIVAL_WAIT)
                        self.log(jid, "info", f"Recall enviado. Espiões retornam em ~{wait}s.")
                        self.save_game_client(ga_id, client)
                        return RunnerResult(success=True, reschedule_seconds=wait,
                            reschedule_inputs={**inputs, "__recovery": _rec(
                                phase="done", missions_done=missions_done)})
                    self.log(jid, "warn", f"Recall falhou: {msg}. Tentando em 10min.")
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
                    f"✓ {[MISSION_DATA.get(m,{}).get('name',m) for m in missions_done]} | "
                    f"✗ {[MISSION_DATA.get(m,{}).get('name',m) for m in missions_failed]}")
                self._notify_telegram(jid, ga_id, target_owner, target_city_name,
                                      missions_done, missions_failed)
                self._replenish_spies(
                    jid, client, city_id, capacity, total, secs_per,
                    safehouse_position=safehouse_position,
                )
                self.save_game_client(ga_id, client)
                result_data = {
                    "missions_done":   missions_done,
                    "missions_failed": missions_failed,
                    "target_city_id":  target_city_id,
                    "source_city_id":  city_id,
                }
                self._notify_parent(jid, inputs, success=True, data=result_data)
                return RunnerResult(success=True, data=result_data)

            self.save_game_client(ga_id, client)
            return RunnerResult(success=True, reschedule_seconds=ERROR_RESCHEDULE)

        except Exception as exc:
            self.log(jid, "error", f"Erro inesperado: {type(exc).__name__}: {exc}")
            self._notify_parent(jid, inputs, success=False,
                                data={"error": str(exc), "target_city_id": target_city_id,
                                      "source_city_id": city_id})
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE,
                                data={"error": str(exc)})

    # ═══════════════════════════════════════════════════════════════════════
    # Helpers — Infiltração inteligente
    # ═══════════════════════════════════════════════════════════════════════

    def _log_decision_table(
        self,
        jid: str,
        live_params: dict,
        mission_id: int,
        max_risk: float,
        available: int,
        chosen_ag: int | None = None,
        chosen_dec: int | None = None,
    ) -> None:
        """Loga tabela de ag/dec/sucesso/risco/score para explicar a escolha."""
        mname = MISSION_DATA.get(mission_id, {}).get("name", f"M{mission_id}")
        header = (
            f"[TABELA {mname}] "
            f"disponíveis={available}\n"
            f"  {'ag':>3} {'dec':>4} {'sucesso%':>9} {'risco_ag%':>10} {'risco_dec%':>11} {'score':>7}  notas"
        )
        lines = [header]

        max_show_ag  = min(available, 8)
        max_show_dec = min(available, 10)

        for ag in range(1, max_show_ag + 1):
            for dec in range(0, max_show_dec + 1 - ag):
                r = compute_spy_risks(live_params, mission_id, ag, dec)
                score = (
                    float(r["success"])
                    - float(r["agent_risk"]) * 1.3
                    - float(r.get("decoy_risk", 0)) * 1.0
                    - (ag + dec) * 0.3
                )
                is_chosen = (ag == chosen_ag and dec == chosen_dec)
                marker = " ← ESCOLHA" if is_chosen else ""
                lines.append(
                    f"  {ag:>3} {dec:>4} {r['success']:>8.1f}% {r['agent_risk']:>9.1f}% "
                    f"{r.get('decoy_risk',0):>10.1f}% {score:>7.2f}{marker}"
                )

        self.log(jid, "info", "\n".join(lines))

    def _find_best_infiltration_batch(
        self,
        live_params: dict,
        available: int,
        target_agents: int,
        max_risk: float,
    ) -> dict | None:
        """Acha o lote que manda o MÁXIMO de agentes numa única infiltração (M1).

        Para cada contagem de agentes (do maior possível ao menor), acha o
        mínimo de chamarizes necessário. Retorna o resultado com mais agentes
        dentro dos limites de risco.

        Argumento target_agents: quantos agentes ainda precisamos estacionar.
        """
        best: dict | None = None
        max_to_try = min(available, target_agents)
        for try_ag in range(max_to_try, 0, -1):
            for try_dec in range(0, min(available - try_ag + 1, 15)):
                r = compute_spy_risks(live_params, 1, try_ag, try_dec)
                if (r["agent_risk"] <= max_risk
                        and r.get("decoy_risk", 0) <= max_risk + 25
                        and r["success"] >= 30.0):
                    # Este é o mínimo de chamarizes para try_ag agentes — é o melhor lote possível
                    best = r
                    return best  # não precisa iterar mais: try_ag é o máximo possível
        return best

    def _total_needed_projected(
        self,
        mission_ids: list,
        live_params: dict,
        stationed: int,
        available: int,
        capacity: int,
        max_risk: float,
    ) -> int:
        """Total de espiões necessários para as missões, considerando risco projetado.

        Projeta quantas infiltrações ainda precisamos e o remaining_risk que
        vão acumular (N × RISK_AFTER_M1), depois recalcula o needed com esse
        risco projetado no live_params.
        """
        if not mission_ids:
            return 1

        sim_avail = max(available, capacity)

        # Primeiro: needed sem projeção (limite inferior)
        # Usa scoring para achar o combo ótimo por custo-benefício (não só o mínimo que passa threshold)
        needed_base = 1
        for mid in mission_ids:
            opt = find_best_score_combo(live_params, mid, sim_avail)
            if opt:
                needed_base = max(needed_base, opt["agents"] + opt.get("decoys", 0))

        # Estimar quantas infiltrações em lote ainda precisamos
        still_short = max(0, needed_base - stationed)
        if still_short == 0:
            return needed_base

        # Projetar remainingRisk real que será acumulado ao estacionar still_short agentes.
        # Fórmula: cada agente estacionado (via M1) contribui ~riskPerSpy da missão mais cara.
        # RISK_AFTER_M1=5 é só o residual de UMA execução de M1 — subestima o acumulado real.
        md_map = live_params.get("missionData") or {}
        max_risk_per_spy = max(
            (float(md_map.get(str(mid), {}).get("riskPerSpy") or 0) for mid in mission_ids),
            default=float(RISK_AFTER_M1),
        )
        projected_extra_risk = still_short * max(max_risk_per_spy, float(RISK_AFTER_M1))

        if projected_extra_risk <= 0:
            return needed_base

        # Recalcula needed com risco projetado
        import copy as _copy
        projected_params = _copy.deepcopy(live_params)
        current_remaining = float(live_params.get("remainingRisk", 0))
        projected_params["remainingRisk"] = current_remaining + projected_extra_risk

        needed_proj = 1
        for mid in mission_ids:
            opt = find_best_score_combo(projected_params, mid, sim_avail)
            if opt:
                needed_proj = max(needed_proj, opt["agents"] + opt.get("decoys", 0))

        return max(needed_base, needed_proj)

    def _mission_risk_threshold(
        self,
        live_params: dict,
        mission_id: int,
        stationed: int,
        max_risk: float,
    ) -> float:
        """Risco remanescente máximo tolerado para executar esta missão com os espiões disponíveis.

        Testa com qual nível de remaining_risk a missão ainda é viável com os
        espiões estacionados. Útil para decidir se vale a pena esperar o risco decair.
        """
        import copy as _copy
        for test_remaining in range(0, 101, 2):
            test_params = _copy.deepcopy(live_params)
            test_params["remainingRisk"] = float(test_remaining)
            opt = find_minimum_agents_decoys(test_params, mission_id, max_risk, max_risk + 25, 30.0, stationed)
            if not opt:
                return max(0, test_remaining - 2)
        return 100.0  # sempre viável

    def _backoff_wait(self, no_progress: int) -> int:
        """Espera com backoff exponencial limitado (não lopar em 5min infinitamente)."""
        # ciclo 1-2: 5min, ciclo 3-4: 15min, ciclo 5+: 30min
        if no_progress <= 2:
            return 5 * 60
        elif no_progress <= 4:
            return 15 * 60
        else:
            return 30 * 60

    def _handle_deadlock(
        self,
        jid, ga_id, client, inputs, live_params,
        stationed, available, eff_risk,
        missions_pending, missions_done,
        capacity, city_id, tgroups, recall_after,
        _rec, _risk_wait,
    ) -> RunnerResult:
        """Lógica de escape quando MAX_NO_PROGRESS é atingido."""
        self.log(jid, "warn",
            f"🔒 DEADLOCK: {MAX_NO_PROGRESS} ciclos sem progresso. "
            f"stationed={stationed} available={available} eff_risk={eff_risk:.0f}%")

        # Opção 1: se tem espiões estacionados, tenta executar com risco relaxado
        if stationed > 0 and missions_pending:
            relaxed_risk = min(eff_risk + 15, 75.0)
            self.log(jid, "warn",
                f"Tentando executar com risco relaxado ({relaxed_risk:.0f}%) usando {stationed} estacionados.")
            self.save_game_client(ga_id, client)
            return RunnerResult(success=True, reschedule_seconds=60,
                reschedule_inputs={**inputs, "__recovery": _rec(
                    phase="executing",
                    missions_pending=missions_pending,
                    missions_done=missions_done,
                    no_progress_cycles=0)})

        # Opção 2: sem espiões estacionados e impossível infiltrar → recall + abort
        self.log(jid, "error",
            f"Espionagem abortada: impossível acumular espiões suficientes após {MAX_NO_PROGRESS} ciclos. "
            f"Missões puladas: {[MISSION_DATA.get(m,{}).get('name',m) for m in missions_pending]}")

        # Marcar cidade como inacessível no dump para não re-tentar
        target_city_id = str(inputs.get("target_city_id") or "").strip()
        if target_city_id:
            try:
                self.hub.update_city_state(
                    game_city_id=target_city_id,
                    state="vacation",
                    game_account_id=ga_id,
                    reason=f"Spy deadlock após {MAX_NO_PROGRESS} ciclos — provável modo férias",
                )
                self.log(jid, "warn",
                    f"[Spy] Cidade {target_city_id} marcada como 'vacation' no dump após deadlock.")
            except Exception as _ue:
                self.log(jid, "warn", f"[Spy] Falha ao atualizar estado no dump: {_ue}")

        if recall_after and stationed > 0:
            self.save_game_client(ga_id, client)
            return RunnerResult(success=False, reschedule_seconds=60,
                reschedule_inputs={**inputs, "__recovery": _rec(
                    phase="recalling",
                    missions_pending=[],
                    missions_done=missions_done)})

        self._notify_telegram(jid, ga_id,
            inputs.get("target_owner",""), inputs.get("target_city_name",""),
            missions_done, missions_pending)
        self.save_game_client(ga_id, client)
        return RunnerResult(success=False, data={"error": "deadlock", "missions_done": missions_done})

    # ═══════════════════════════════════════════════════════════════════════
    # Helpers — Safehouse e navegação
    # ═══════════════════════════════════════════════════════════════════════

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

        self.log(job_id, "warn", f"Safehouse não encontrada; usando fallback pos=19")
        return 19

    def _maybe_train(self, jid, client, city_id: str, available: int, slots_free: int,
                     secs_per: int, live_params: dict, max_risk: float,
                     capacity: int, total: int, *,
                     safehouse_position: int) -> int | None:
        """Tenta treinar espiões se necessário. Retorna wait_seconds ou None."""
        if available > 0:
            # Verifica se já dá pra infiltrar
            if self._find_best_infiltration_batch(live_params, available, 1, max_risk):
                return None  # pode infiltrar agora, não precisa treinar

            # Precisa de mais — treinar
            for try_total in range(available + 1, min(capacity + 1, available + 15)):
                if self._find_best_infiltration_batch(live_params, try_total, 1, max_risk):
                    to_train = min(try_total - available, slots_free)
                    if to_train <= 0:
                        self.log(jid, "warn",
                            f"Infiltração impossível com {available} (mín={try_total}) e safehouse cheia.")
                        return None  # deadlock real — deixar _handle_deadlock resolver
                    ok, msg = client.train_spies(city_id, count=to_train, position=safehouse_position)
                    if ok:
                        wait = secs_per * to_train + 60
                        self.log(jid, "info",
                            f"Treinando {to_train} espião(ões) para infiltrar (mín={try_total}) → pronto em {wait}s.")
                    else:
                        wait = random.randint(5*60, 15*60)
                        self.log(jid, "warn", f"Treino falhou: {msg}. Aguardando {wait}s.")
                    return wait
            return None

        # available=0 — treinar para ter espiões em casa
        if slots_free <= 0:
            self.log(jid, "warn", f"Sem espiões disponíveis e safehouse cheia. Aguardando retorno.")
            return random.randint(10*60, 20*60)

        to_train = min(5, slots_free)
        ok, msg = client.train_spies(city_id, count=to_train, position=safehouse_position)
        if ok:
            wait = secs_per * to_train + 60
            self.log(jid, "info",
                f"Treinando {to_train} espião(ões) (custo: {to_train*150} ouro, {to_train*54} cristal) "
                f"→ pronto em {wait}s.")
        else:
            wait = random.randint(10*60, 20*60)
            self.log(jid, "warn", f"Treino falhou ({msg}). Aguardando {wait}s.")
        return wait

    def _arrival_wait(self, groups: list, fallback: int) -> int:
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
        """Compatibilidade: versão simples sem projeção."""
        needed = 1
        for mid in mission_ids:
            opt = find_minimum_agents_decoys(live_params, mid, max_risk, max_risk + 25, 30.0, available)
            if opt:
                needed = max(needed, int(opt.get("agents", 1)) + int(opt.get("decoys", 0)))
        return needed

    def _get_target_groups(self, state: dict, city_id: str,
                           city_name: str = "", owner: str = "") -> list[dict]:
        groups      = state.get("target_groups") or []
        city_id_str = str(city_id or "").strip()
        city_norm   = str(city_name or "").strip().lower()
        owner_norm  = str(owner or "").strip().lower()
        result, seen = [], set()

        for g in groups:
            gid = str(g.get("spy_id") or "")
            if gid and gid in seen:
                continue
            if city_id_str and str(g.get("target_city_id") or "").strip() == city_id_str:
                result.append(g)
                if gid: seen.add(gid)
                continue
            g_city  = str(g.get("city_name") or "").strip().lower()
            g_owner = str(g.get("owner") or "").strip().lower()
            if city_norm and g_city == city_norm and (not owner_norm or g_owner == owner_norm):
                result.append(g)
                if gid: seen.add(gid)
                continue
            if owner_norm and g_owner == owner_norm and city_norm and not g_city:
                result.append(g)
                if gid: seen.add(gid)
        return result

    def _select_target_group(self, groups: list) -> dict:
        if not groups:
            return {}
        waiting    = [g for g in groups if g.get("is_waiting")]
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
                      target_owner_id: str = "",
                      safehouse_position: int = 19) -> list:
        try:
            reports = client.get_spy_reports(city_id, position=safehouse_position)
            if not reports:
                return []
            dicts = [{
                "report_id":        r.get("report_id", ""),
                "source_city_id":   city_id,
                "target_owner":     r.get("target_owner", ""),
                "target_owner_id":  target_owner_id or "",
                "target_city_id":   r.get("target_city_id", ""),
                "target_city_name": r.get("target_city_name", ""),
                "target_x":         r.get("target_x"),
                "target_y":         r.get("target_y"),
                "subject":          r.get("subject", ""),
                "mission_name":     r.get("mission_name", ""),
                "status":           r.get("status", ""),
                "result_status":    r.get("result_status", ""),
                "agents_sent":      r.get("agents_sent", 0),
                "agents_lost":      r.get("agents_lost", 0),
                "decoys_sent":      r.get("decoys_sent", 0),
                "decoys_lost":      r.get("decoys_lost", 0),
                "report_html":      r.get("report_html", ""),
                "report_text":      r.get("report_text", ""),
                "data_json":        r.get("data_json", {}),
                "date_str":         r.get("date_str", ""),
                "mission_id":       r.get("mission_id"),
                "is_read":          r.get("is_read", not r.get("unread", False)),
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
        if not reports:
            return True
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
        if decoys <= 0:
            return 0, False
        try:
            mdata        = (live_params.get("missionData") or {}).get(str(mission_id)) or {}
            decoy_class  = str(mdata.get("decoyMissionClass") or "")
            resource_key = self._DECOY_RESOURCE_MAP.get(decoy_class, "")
            cost_map     = mdata.get("301") or {}
            cost_each    = int(list(cost_map.values())[0]) if cost_map else 0

            if not resource_key or cost_each <= 0:
                return decoys, False

            snap     = self.hub.get_snapshot(game_account_id=ga_id)
            cities   = snap.get("cities") or []
            spy_city = next((c for c in cities if str(c.get("id") or "") == str(city_id)), {})
            # Ouro é recurso global da conta em snap["base_snapshot"]["gold"]
            if resource_key == "gold":
                have_raw = (snap.get("base_snapshot") or {}).get("gold", 0)
            else:
                have_raw = spy_city.get(resource_key)
            committed = int((committed_res or {}).get(resource_key) or 0)
            have   = max(0, int(have_raw or 0) - committed)
            afford = min(decoys, have // cost_each) if cost_each > 0 else decoys

            if afford >= decoys:
                return decoys, False

            mname = MISSION_DATA.get(mission_id, {}).get("name", f"M{mission_id}")
            self.log(jid, "warn",
                f"{mname}: chamarizes precisam de {resource_key} "
                f"({decoy_class}, {cost_each}/cham × {decoys} = {decoys*cost_each}). "
                f"Disponível: {have} → reduzindo para {afford} chamarizes.")

            if client and resource_key != "gold" and afford < decoys:
                source = None
                best   = 0
                for c in cities:
                    if str(c.get("id") or "") == str(city_id):
                        continue
                    amt = int(c.get(resource_key) or 0)
                    transferable = int(amt * 0.8)
                    if transferable >= decoys * cost_each and transferable > best:
                        best, source = transferable, c

                if source:
                    send = min(best, decoys * cost_each * 3)
                    try:
                        client.send_resources(int(source["id"]), int(city_id), {resource_key: send})
                        self.log(jid, "info",
                            f"Transporte solicitado: {send} {resource_key} "
                            f"de {source.get('name', source['id'])} → {spy_city.get('name', city_id)}.")
                        if committed_res is not None:
                            committed_res[resource_key] = committed_res.get(resource_key, 0) + send
                        return afford, True
                    except Exception as te:
                        self.log(jid, "warn", f"Falha no transporte de {resource_key}: {te}")

            return afford, False
        except Exception as exc:
            self.log(jid, "warn", f"Erro ao verificar recursos: {exc}")
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

    def _notify_parent(self, jid: str, inputs: dict, *, success: bool, data: dict) -> None:
        """Notifica runner pai (WorldSpyRunner ac=16) via mailbox pattern.

        Usa __root_job_id (novo) se disponível.
        Fallback para __parent_job_id (legado) via reschedule direto.
        """
        root_jid = str(inputs.get("__root_job_id") or "").strip()
        if root_jid:
            try:
                self.hub.notify_parent(root_jid, {
                    "child_job_id":  jid,
                    "success":       success,
                    **data,
                })
                logger.info("Notificou root %s via mailbox (child=%s success=%s)", root_jid, jid, success)
            except Exception as exc:
                logger.warning("Falha ao notificar root %s: %s", root_jid, exc)
            return

        # Legado: __parent_job_id (reschedule direto — só funciona para o primeiro filho)
        parent_jid = str(inputs.get("__parent_job_id") or "").strip()
        if not parent_jid:
            return
        try:
            self.hub.reschedule_job(
                parent_jid,
                delay_seconds=5,
                inputs={"__child_done": {
                    "child_job_id":  jid,
                    "success":       success,
                    **data,
                }},
            )
            logger.info("Notificou pai legado %s (child=%s success=%s)", parent_jid, jid, success)
        except Exception as exc:
            logger.warning("Falha ao notificar pai legado %s: %s", parent_jid, exc)

    def _replenish_spies(self, jid, client, city_id: str,
                         capacity: int, total: int, secs_per: int, *,
                         safehouse_position: int) -> None:
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
            self.log(jid, "warn", f"Reposição: erro: {exc}")
