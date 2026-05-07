"""
Runner de espionagem — ação 18 (SpyRunner).

Envia espiões em missões e salva os relatórios no hub.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.spy import MISSION_DATA, compute_agents_for_risk, compute_agents_for_risk_dynamic
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

ERROR_RESCHEDULE = 10 * 60       # 10 min — recuo em caso de erro
MISSION_MIN_WAIT = 5 * 60        # 5 min — missão básica (missão 1)
MISSION_MAX_WAIT = 15 * 60       # 15 min — buffer padrão após missão


@register_runner(15)
class SpyRunner(BaseRunner):
    """Envia espiões em missões e opcionalmente salva os relatórios no hub.

    Recurring — reagenda automaticamente para manter a espionagem contínua.

    Inputs:
        city_id            — cidade com Casa Segura (origem)
        target_city_id     — ID da cidade alvo
        island_id          — ID da ilha da cidade alvo
        mission_id         — tipo da missão (padrão: 1)
        max_detection_risk — risco máximo de detecção em % (padrão: 25)
        auto_agents        — calcular agentes pelo risco (padrão: True)
        agents             — agentes manuais se auto_agents=False (padrão: 1)
        decoys             — número de chamarizes (padrão: 0)
        save_reports       — salvar relatórios no hub (padrão: True)
        delete_after_save  — apagar do jogo após salvar (padrão: False)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        game_account_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        city_id = str(inputs.get("city_id") or "").strip()
        target_city_id = str(inputs.get("target_city_id") or "").strip()
        island_id = str(inputs.get("island_id") or "").strip()

        if not city_id:
            self.log(jid, "error", "city_id obrigatório para espionagem")
            return RunnerResult(success=False, data={"error": "city_id_missing"})
        if not target_city_id:
            self.log(jid, "error", "target_city_id obrigatório para espionagem")
            return RunnerResult(success=False, data={"error": "target_city_id_missing"})
        if not island_id:
            self.log(jid, "error", "island_id obrigatório para espionagem")
            return RunnerResult(success=False, data={"error": "island_id_missing"})

        # mission_id pode ser int ou lista de ints separados por vírgula
        raw_mission = inputs.get("mission_id") or "1"
        try:
            if isinstance(raw_mission, list):
                mission_ids = [int(x) for x in raw_mission]
            else:
                mission_ids = [int(x.strip()) for x in str(raw_mission).split(",") if x.strip()]
        except (ValueError, TypeError):
            mission_ids = [1]
        if not mission_ids:
            mission_ids = [1]
        # Compatibilidade: mission_id singular
        mission_id = mission_ids[0]

        try:
            max_detection_risk = int(inputs.get("max_detection_risk") or 25)
        except (ValueError, TypeError):
            max_detection_risk = 25

        auto_agents = bool(inputs.get("auto_agents") if inputs.get("auto_agents") is not None else True)

        try:
            manual_agents = max(1, int(inputs.get("agents") or 1))
        except (ValueError, TypeError):
            manual_agents = 1

        try:
            decoys = max(0, int(inputs.get("decoys") or 0))
        except (ValueError, TypeError):
            decoys = 0

        save_reports = bool(inputs.get("save_reports") if inputs.get("save_reports") is not None else True)
        delete_after_save = bool(inputs.get("delete_after_save") or False)

        mission_name = MISSION_DATA.get(mission_id, {}).get("name", f"missão {mission_id}")
        missions_label = ", ".join(str(m) for m in mission_ids)

        creds = self.resolve_credentials(aid, inputs, game_account_id=game_account_id)
        if not creds:
            self.log(jid, "error", "Credenciais não encontradas")
            return RunnerResult(
                success=False,
                reschedule_seconds=ERROR_RESCHEDULE,
                data={"error": "credentials_missing"},
            )

        try:
            client = self.get_or_login_game_client(jid, aid, game_account_id, creds)

            # Etapa 1: ler estado da Casa Segura
            self.log(jid, "info", f"Lendo estado da Casa Segura na cidade {city_id}")
            state = client.get_safehouse_state(city_id)

            total_spies = int(state.get("total_spies") or 0)
            available_spies = int(state.get("available_spies") or 0)
            in_use_spies = int(state.get("in_use_spies") or 0)

            self.log(
                jid, "info",
                f"Espiões: total={total_spies} disponíveis={available_spies} em uso={in_use_spies}",
            )

            # Etapa 2: coletar relatórios existentes se houver missão ativa
            if in_use_spies > 0 and save_reports:
                self.log(jid, "info", "Missão ativa detectada — coletando relatórios pendentes")
                self._collect_and_save_reports(
                    jid, client, game_account_id, city_id, delete_after_save
                )
                # Re-ler estado após coleta
                state = client.get_safehouse_state(city_id)
                available_spies = int(state.get("available_spies") or 0)

            # Etapa 3: buscar dados de risco REAIS para este alvo
            live_mission_data: dict = {}
            try:
                self.log(jid, "info", f"Buscando riscos reais para cidade alvo {target_city_id}")
                md_result = client.get_spy_mission_data(city_id, target_city_id, island_id)
                live_mission_data = md_result.get("missions", {})
                target_info = md_result.get("target", {})
                if target_info:
                    self.log(jid, "info",
                        f"Alvo: nível={target_info.get('city_level')} "
                        f"inativo={target_info.get('is_inactive')} "
                        f"espiões livres={target_info.get('free_spies')}")
            except Exception as exc:
                self.log(jid, "warn", f"Não foi possível buscar dados reais: {exc}. Usando valores padrão.")

            # Etapa 4: calcular agentes para a primeira missão da lista
            mission_id = mission_ids[0]
            if auto_agents:
                live_mdata = live_mission_data.get(mission_id)
                if live_mdata:
                    agents = compute_agents_for_risk_dynamic(live_mdata, max_detection_risk)
                    self.log(jid, "info",
                        f"Agentes calculados (dados reais) para risco máximo {max_detection_risk}%: {agents} "
                        f"(riskBefore={live_mdata.get('risk_before')} riskPerSpy={live_mdata.get('risk_per_spy')})")
                else:
                    agents = compute_agents_for_risk(mission_id, max_detection_risk)
                    self.log(jid, "info", f"Agentes calculados (dados padrão) para risco máximo {max_detection_risk}%: {agents}")
            else:
                agents = manual_agents

            # Verificar disponibilidade
            if available_spies < agents:
                wait = random.randint(MISSION_MIN_WAIT, MISSION_MAX_WAIT)
                self.log(jid, "warn",
                    f"Espiões insuficientes: necessário={agents} disponível={available_spies}. "
                    f"Reagendando em {wait}s.")
                self.save_game_client(game_account_id, client)
                return RunnerResult(success=True, reschedule_seconds=wait)

            # Etapa 5: enviar todas as missões da lista
            missions_sent = 0
            for mid in mission_ids:
                # Verificar se missão é executável
                live_md = live_mission_data.get(mid)
                if live_md and not live_md.get("executable", True):
                    self.log(jid, "info", f"Missão {mid} não executável neste alvo — pulando")
                    continue

                # Recalcular agentes para cada missão se auto
                if auto_agents and mid != mission_ids[0]:
                    if live_md:
                        agents_this = compute_agents_for_risk_dynamic(live_md, max_detection_risk)
                    else:
                        agents_this = compute_agents_for_risk(mid, max_detection_risk)
                    if available_spies < agents_this:
                        self.log(jid, "warn", f"Espiões insuficientes para missão {mid} — pulando")
                        continue
                else:
                    agents_this = agents

                mid_name = (live_md or MISSION_DATA.get(mid, {})).get("name", f"missão {mid}")
                self.log(jid, "info",
                    f"Enviando missão {mid} ({mid_name}) com {agents_this} agente(s), {decoys} chamariz(es)")
                result = client.send_spy(
                    source_city_id=city_id,
                    target_city_id=target_city_id,
                    island_id=island_id,
                    mission_id=mid,
                    agents=agents_this,
                    decoys=decoys,
                )
                if result.get("success"):
                    missions_sent += 1
                    self.log(jid, "info", f"Missão {mid} enviada: {result.get('message', 'ok')}")
                else:
                    self.log(jid, "warn", f"Missão {mid} falhou: {result.get('message', '?')}")

            self.log(jid, "info", f"{missions_sent}/{len(mission_ids)} missões enviadas")

            # Etapa 6: salvar novos relatórios (se configurado)
            if save_reports and missions_sent > 0:
                self._collect_and_save_reports(
                    jid, client, game_account_id, city_id, delete_after_save
                )

            # Etapa 7: reagendar
            wait = random.randint(MISSION_MIN_WAIT, MISSION_MAX_WAIT)
            self.log(jid, "info", f"Reagendando em {wait}s")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=wait)

        except Exception as exc:
            self.log(jid, "error", f"Espionagem falhou: {exc}")
            return RunnerResult(
                success=False,
                reschedule_seconds=ERROR_RESCHEDULE,
                data={"error": str(exc)},
            )

    def _collect_and_save_reports(
        self,
        jid: str,
        client: Any,
        game_account_id: str,
        city_id: str,
        delete_after_save: bool,
    ) -> None:
        """Busca relatórios, envia ao hub e opcionalmente apaga do jogo."""
        try:
            reports = client.get_spy_reports(city_id)
            if not reports:
                self.log(jid, "info", "Nenhum relatório encontrado na Casa Segura")
                return

            self.log(jid, "info", f"{len(reports)} relatório(s) encontrado(s) — salvando no hub")
            result = self.hub.save_spy_reports(game_account_id, reports)
            saved = result.get("saved", 0)
            new_count = result.get("new_count", 0)
            self.log(jid, "info", f"Relatórios salvos: {saved} ({new_count} novos)")

            if delete_after_save:
                for rpt in reports:
                    report_id = rpt.get("report_id")
                    if report_id:
                        try:
                            client.delete_spy_report(city_id, report_id)
                        except Exception as del_exc:
                            self.log(
                                jid, "warn",
                                f"Falha ao apagar relatório {report_id}: {del_exc}",
                            )

        except Exception as exc:
            self.log(jid, "warn", f"Falha ao coletar/salvar relatórios: {exc}")
