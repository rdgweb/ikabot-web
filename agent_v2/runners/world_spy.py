"""
Runner de Espionagem Mundial — ação 16 (WorldSpyRunner).

Arquitetura event-driven:
  1. Primeira execução: busca alvos → spawna ac=15 para todas as safehouses livres.
     Antes de spawnar, pré-cria o próprio sucessor (J2) com delay longo de fallback.
     Passa __parent_job_id=J2 para cada filho.

  2. Quando filho ac=15 termina (sucesso ou erro): chama reschedule_job(J2, 5s).
     O hub cancela J2 (era "scheduled") e cria J3 com __child_done nos inputs.
     J3 acorda em 5s e executa a próxima rodada.

  3. Runner 16 acorda: verifica alvos restantes → spawna filhos para safehouses livres
     → pré-cria próximo fallback → repete.

  4. Quando ciclo completo (0 alvos + 0 safehouses ativas): job encerra sem reschedule.

Safehouses:
  city_ids — IDs de cidades com Casa de Espionagem, separados por vírgula.
  Suporte ao formato multi-GA: "{ga_pk}:{city_id}".

Filtros de alvo:
  only_inactive    — apenas inativos
  region_x_min/max — retângulo X no mapa
  region_y_min/max — retângulo Y no mapa
  max_total_score  — pontuação total — 0=sem limite
  max_army_score   — pontuação de exército — 0=sem limite

Inteligência:
  skip_if_valid    — pula cidades com intel válida para todas as missões
  intel_ttl_hours  — validade da intel (horas). 0 = usa AppSetting.
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

ERROR_RESCHEDULE   = 5  * 60   # 5 min em caso de erro
FALLBACK_DELAY     = 4  * 60 * 60  # 4h — fallback caso notificação falhe


def _parse_int(value, default=None):
    try:
        return int(value) if value is not None and str(value).strip() != "" else default
    except (TypeError, ValueError):
        return default


@register_runner(16)
class WorldSpyRunner(BaseRunner):
    """Orquestra espionagem event-driven em múltiplas cidades via ac=15.

    Cada filho ac=15 notifica o pai ao terminar via reschedule_job(parent_job_id, 5s).
    Runner 16 pré-cria o próprio sucessor antes de spawnar para que a notificação
    sempre encontre um job válido para acordar.
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid   = job["job_id"]
        ga_id = str(job.get("game_account_id") or "").strip()
        inputs: dict = job.get("inputs") or {}

        # ── Inputs de configuração ────────────────────────────────────────────
        city_ids_raw = str(inputs.get("city_ids") or "").strip()

        # Parse safehouses — suporta legado "city_id" e multi-GA "{ga_pk}:{city_id}"
        city_entries: list[dict] = []
        for entry in city_ids_raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                ga_pk, city_game_id = entry.split(":", 1)
                city_entries.append({"ga_pk": ga_pk.strip(), "city_game_id": city_game_id.strip()})
            else:
                city_entries.append({"ga_pk": ga_id, "city_game_id": entry})

        if not city_entries:
            self.log(jid, "error", "city_ids não configurado.")
            return RunnerResult(success=False)

        missions_raw    = str(inputs.get("missions") or "3,5").strip()
        only_inactive   = bool(inputs.get("only_inactive", True))
        x_min           = _parse_int(inputs.get("region_x_min"))
        x_max           = _parse_int(inputs.get("region_x_max"))
        y_min           = _parse_int(inputs.get("region_y_min"))
        y_max           = _parse_int(inputs.get("region_y_max"))
        max_total_score = _parse_int(inputs.get("max_total_score"), 0)
        max_army_score  = _parse_int(inputs.get("max_army_score"), 0)
        skip_if_valid   = bool(inputs.get("skip_if_valid", True))
        intel_ttl_hours = max(0, _parse_int(inputs.get("intel_ttl_hours"), 0))
        max_detection_risk = float(inputs.get("max_detection_risk") or 35)
        recall_after    = bool(inputs.get("recall_after", True))
        save_reports    = bool(inputs.get("save_reports", True))
        delete_after_save = bool(inputs.get("delete_after_save", False))

        # Parse missions
        mission_ids: list[int] = []
        for m in missions_raw.split(","):
            try:
                mission_ids.append(int(m.strip()))
            except (ValueError, TypeError):
                pass
        if not mission_ids:
            mission_ids = [3, 5]

        unique_gas = len({e["ga_pk"] for e in city_entries})
        region_desc = (
            f"[{x_min}:{y_min}→{x_max}:{y_max}]"
            if any(v is not None for v in (x_min, x_max, y_min, y_max))
            else "mapa inteiro"
        )
        ttl_desc = f"{intel_ttl_hours}h" if intel_ttl_hours else "padrão"

        # ── Notificação de filho (informativo) ────────────────────────────────
        child_done = inputs.get("__child_done")
        if child_done and isinstance(child_done, dict):
            cid    = child_done.get("child_job_id", "?")
            csrc   = child_done.get("source_city_id", "?")
            ctgt   = child_done.get("target_city_id", "?")
            cok    = child_done.get("success", "?")
            mdone  = child_done.get("missions_done", [])
            mfail  = child_done.get("missions_failed", [])
            self.log(jid, "info",
                     f"[WorldSpy] Filho concluído: job={str(cid)[:8]} "
                     f"src={csrc} tgt={ctgt} success={cok} "
                     f"done={mdone} fail={mfail}")

        self.log(jid, "info",
                 f"[WorldSpy] safehouses={len(city_entries)} ({unique_gas} conta(s)) "
                 f"missions={mission_ids} inactive={only_inactive} region={region_desc} "
                 f"max_total={max_total_score or '—'} max_army={max_army_score or '—'} "
                 f"ttl={ttl_desc}")

        # ── Consulta hub ──────────────────────────────────────────────────────
        try:
            result = self.hub.get_spy_targets(
                only_inactive=only_inactive,
                x_min=x_min,
                x_max=x_max,
                y_min=y_min,
                y_max=y_max,
                max_total_score=max_total_score or 0,
                max_army_score=max_army_score or 0,
                skip_if_valid=skip_if_valid,
                missions=mission_ids,
                limit=200,
                game_account_id=ga_id,
                intel_ttl_hours=intel_ttl_hours,
            )
        except Exception as exc:
            self.log(jid, "error", f"Erro ao buscar alvos: {exc}")
            self.hub.reschedule_job(jid, delay_seconds=ERROR_RESCHEDULE)
            return RunnerResult(success=False)

        targets: list[dict]   = result.get("targets") or []
        dump_id               = result.get("dump_id") or "?"
        busy_sources: set[str] = set(result.get("busy_source_cities") or [])
        free_entries           = [e for e in city_entries if e["city_game_id"] not in busy_sources]

        self.log(jid, "info",
                 f"[WorldSpy] Dump {dump_id}: {len(targets)} alvos pendentes | "
                 f"safehouses ocupadas={len(busy_sources)}/{len(city_entries)} "
                 f"livres={len(free_entries)}")

        # ── Ciclo completo? ───────────────────────────────────────────────────
        if not targets and not busy_sources:
            self.log(jid, "info", "[WorldSpy] ✓ Ciclo completo. Sem alvos e sem filhos ativos. Encerrando.")
            return RunnerResult(success=True)

        # ── Apenas aguardando filhos terminarem ───────────────────────────────
        if not targets and busy_sources:
            self.log(jid, "info",
                     f"[WorldSpy] {len(busy_sources)} safehouse(s) ativas, sem novos alvos. "
                     f"Pré-criando fallback e aguardando notificações.")
            # Pré-criar fallback: filhos vão acordar este job quando terminarem
            try:
                resp_fb = self.hub.reschedule_job(jid, delay_seconds=FALLBACK_DELAY)
                fallback_jid = resp_fb.get("new_job_id", "?")
                self.log(jid, "info", f"[WorldSpy] Fallback criado: {str(fallback_jid)[:8]} (em {FALLBACK_DELAY//3600}h)")
            except Exception as exc:
                self.log(jid, "warn", f"[WorldSpy] Falha ao criar fallback: {exc}")
            return RunnerResult(success=True)

        # ── Tem alvos mas todas as safehouses ocupadas ────────────────────────
        if targets and not free_entries:
            self.log(jid, "info",
                     f"[WorldSpy] {len(targets)} alvo(s) pendente(s) mas todas as "
                     f"{len(city_entries)} safehouses ocupadas. Aguardando notificações.")
            try:
                resp_fb = self.hub.reschedule_job(jid, delay_seconds=FALLBACK_DELAY)
                fallback_jid = resp_fb.get("new_job_id", "?")
                self.log(jid, "info", f"[WorldSpy] Fallback criado: {str(fallback_jid)[:8]}")
            except Exception as exc:
                self.log(jid, "warn", f"[WorldSpy] Falha ao criar fallback: {exc}")
            return RunnerResult(success=True)

        # ── Pré-criar fallback ANTES de spawnar ───────────────────────────────
        # O fallback (J_next) é o job que será acordado pelos filhos ao terminarem.
        # Filhos recebem __parent_job_id=J_next e chamam reschedule_job(J_next, 5s).
        # O hub cancela J_next antigo (era "scheduled") e cria o novo job imediato.
        try:
            resp_next = self.hub.reschedule_job(jid, delay_seconds=FALLBACK_DELAY)
            next_jid  = resp_next.get("new_job_id", "")
            self.log(jid, "info",
                     f"[WorldSpy] Fallback event-driven: {str(next_jid)[:8]} (filhos vão acordar este)")
        except Exception as exc:
            self.log(jid, "warn", f"[WorldSpy] Falha ao criar fallback; filhos não conseguirão notificar: {exc}")
            next_jid = ""

        # ── Spawna jobs filhos (ac=15) para safehouses livres ─────────────────
        spawned = 0
        skipped = 0
        remaining_free = list(free_entries)

        for target in targets:
            if not remaining_free:
                self.log(jid, "info",
                         f"[WorldSpy] Safehouses livres esgotadas após {spawned} spawns. "
                         f"{len(targets) - spawned - skipped} alvos restantes para próxima rodada.")
                break

            target_city_id = str(target.get("game_city_id") or "").strip()
            if not target_city_id:
                skipped += 1
                continue

            source_entry = remaining_free[spawned % len(remaining_free)]
            source_city  = source_entry["city_game_id"]
            source_ga_pk = source_entry["ga_pk"]
            remaining_free = [e for e in remaining_free if e["city_game_id"] != source_city]

            child_inputs = {
                "city_id":             source_city,
                "target_city_id":      target_city_id,
                "target_city_name":    str(target.get("city_name") or ""),
                "target_owner":        str(target.get("owner_name") or ""),
                "target_owner_id":     str(target.get("owner_id") or ""),
                "island_id":           str(target.get("island_id") or ""),
                "mission_id":          missions_raw,
                "max_detection_risk":  max_detection_risk,
                "recall_after":        recall_after,
                "save_reports":        save_reports,
                "delete_after_save":   delete_after_save,
            }

            # Passar o ID do job pai (fallback) para que filho possa notificar
            if next_jid:
                child_inputs["__parent_job_id"] = next_jid

            try:
                resp = self.hub.spawn_job(
                    jid,
                    action_code=15,
                    inputs=child_inputs,
                    delay_seconds=spawned * 30,   # escalonar 30s
                    game_account_id=source_ga_pk,
                )
                spawned += 1
                child_jid = resp.get("new_job_id", "?")
                self.log(jid, "info",
                         f"[WorldSpy] → {target.get('city_name')} ({target.get('owner_name')}) "
                         f"safehouse={source_city} ga={source_ga_pk[:8]}… job={str(child_jid)[:8]}")
            except Exception as exc:
                skipped += 1
                remaining_free.append(source_entry)
                self.log(jid, "warn",
                         f"[WorldSpy] Falha ao criar job para {target.get('city_name')}: {exc}")

        self.log(jid, "info",
                 f"[WorldSpy] {spawned} job(s) criado(s)"
                 + (f", {skipped} ignorado(s)" if skipped else "")
                 + f". Aguardando notificações dos filhos.")
        return RunnerResult(success=True)
