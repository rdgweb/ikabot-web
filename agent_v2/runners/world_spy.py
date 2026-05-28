"""
Runner de Espionagem Mundial — ação 16 (WorldSpyRunner).

Orquestra espionagem em massa consultando o hub periodicamente para verificar
progresso e spawnar novos filhos ac=15 conforme safehouses ficam livres.

Fluxo por ciclo de intel:
  1. Consulta hub: alvos pendentes + safehouses ocupadas (busy_source_cities).
  2. Se 0 alvos E 0 safehouses ocupadas → ciclo completo → aguarda interval_minutes.
  3. Se 0 alvos mas safehouses ocupadas → filhos ainda correndo → aguarda check_interval_min.
  4. Se tem alvos E tem safehouses livres → spawna filhos → aguarda check_interval_min.
  5. Se tem alvos mas nenhuma safehouse livre → aguarda check_interval_min.

O hub é a fonte de verdade:
  - busy_source_cities: safehouses com ac=15 ativo (evita 2 jobs por safehouse).
  - occupied targets: alvos com ac=15 ativo (lock global — nunca 2 contas no mesmo alvo).
  - skip_if_valid: exclui alvos com intel fresca (TTL via AppSetting ou intel_ttl_hours).

Safehouses:
  city_ids — IDs de cidades com Casa de Espionagem, separados por vírgula.
  Suporte ao formato multi-GA: "{ga_pk}:{city_id}" para safehouses em contas diferentes.

Filtros de alvo:
  only_inactive    — apenas inativos (mode férias não pode ser espionado)
  region_x_min/max — retângulo X no mapa
  region_y_min/max — retângulo Y no mapa
  max_total_score  — pontuação total (construção+pesquisa+exército) — 0=sem limite
  max_army_score   — pontuação de exército — 0=sem limite

Inteligência:
  skip_if_valid    — pula cidades com intel válida para todas as missões configuradas
  intel_ttl_hours  — validade da intel (horas). 0 = usa AppSetting spy_report_expiry_hours.
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

ERROR_RESCHEDULE = 5 * 60   # 5 min em caso de erro


def _parse_int(value, default=None):
    try:
        return int(value) if value is not None and str(value).strip() != "" else default
    except (TypeError, ValueError):
        return default


@register_runner(16)
class WorldSpyRunner(BaseRunner):
    """Orquestra espionagem em múltiplas cidades via ac=15.

    Acorda a cada check_interval_min para verificar progresso.
    Não mantém estado próprio — o hub rastreia busy_source_cities e
    occupied targets em tempo real através dos jobs ac=15 ativos.
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        ga_id = str(job.get("game_account_id") or "").strip()
        inputs: dict = job.get("inputs") or {}

        # ── Inputs ────────────────────────────────────────────────────────────
        city_ids_raw = str(inputs.get("city_ids") or "").strip()

        # Parse city entries — suporta legado "city_id" e multi-GA "{ga_pk}:{city_id}"
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
            return RunnerResult(success=False, message="city_ids não configurado.")

        missions_raw = str(inputs.get("missions") or "3,5").strip()
        only_inactive   = bool(inputs.get("only_inactive", True))
        x_min           = _parse_int(inputs.get("region_x_min"))
        x_max           = _parse_int(inputs.get("region_x_max"))
        y_min           = _parse_int(inputs.get("region_y_min"))
        y_max           = _parse_int(inputs.get("region_y_max"))
        max_total_score = _parse_int(inputs.get("max_total_score"), 0)
        max_army_score  = _parse_int(inputs.get("max_army_score"), 0)
        skip_if_valid   = bool(inputs.get("skip_if_valid", True))
        intel_ttl_hours = max(0, _parse_int(inputs.get("intel_ttl_hours"), 0))
        interval_minutes    = max(5, _parse_int(inputs.get("interval_minutes"), 120))
        check_interval_min  = max(2, _parse_int(inputs.get("check_interval_min"), 5))
        max_targets         = max(1, min(200, _parse_int(inputs.get("max_targets"), 50)))
        max_detection_risk  = float(inputs.get("max_detection_risk") or 35)
        recall_after        = bool(inputs.get("recall_after", True))
        save_reports        = bool(inputs.get("save_reports", True))
        delete_after_save   = bool(inputs.get("delete_after_save", False))

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
        city_ids_flat = [e["city_game_id"] for e in city_entries]

        region_desc = (
            f"[{x_min}:{y_min}→{x_max}:{y_max}]"
            if any(v is not None for v in (x_min, x_max, y_min, y_max))
            else "mapa inteiro"
        )
        ttl_desc = f"{intel_ttl_hours}h" if intel_ttl_hours else "padrão"
        self.log(jid, "info",
                 f"[WorldSpy] safehouses={len(city_entries)} ({unique_gas} conta(s)) "
                 f"missions={mission_ids} inactive={only_inactive} region={region_desc} "
                 f"max_total={max_total_score or '—'} max_army={max_army_score or '—'} "
                 f"max_targets={max_targets} check={check_interval_min}min "
                 f"interval={interval_minutes}min ttl={ttl_desc}")

        # ── Consulta hub: alvos pendentes + safehouses ocupadas ───────────────
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
                limit=max_targets,
                game_account_id=ga_id,
                intel_ttl_hours=intel_ttl_hours,
            )
        except Exception as exc:
            self.log(jid, "error", f"Erro ao buscar alvos: {exc}")
            self.hub.reschedule_job(jid, delay_seconds=ERROR_RESCHEDULE)
            return RunnerResult(success=False, message=f"Erro ao buscar alvos: {exc}")

        targets: list[dict] = result.get("targets") or []
        dump_id = result.get("dump_id") or "?"
        # Safehouses com ac=15 ativo — o hub calcula isso em tempo real
        busy_sources: set[str] = set(result.get("busy_source_cities") or [])

        # Safehouses livres = todas as configuradas menos as ocupadas
        free_entries = [e for e in city_entries if e["city_game_id"] not in busy_sources]

        self.log(jid, "info",
                 f"[WorldSpy] Dump {dump_id}: {len(targets)} alvos pendentes | "
                 f"safehouses ocupadas={len(busy_sources)}/{len(city_entries)} "
                 f"livres={len(free_entries)}")

        # ── Decisão de estado ─────────────────────────────────────────────────
        # Ciclo completo: nenhum alvo restante E nenhuma safehouse ativa
        if not targets and not busy_sources:
            msg = (f"[WorldSpy] Ciclo completo. "
                   f"Aguardando {interval_minutes}min para próximo ciclo.")
            self.log(jid, "info", msg)
            self.hub.reschedule_job(jid, delay_seconds=interval_minutes * 60)
            return RunnerResult(success=True, message=msg)

        # Sem alvos mas ainda tem safehouses ativas → filhos ainda correndo
        if not targets and busy_sources:
            msg = (f"[WorldSpy] Nenhum alvo novo. "
                   f"{len(busy_sources)} safehouse(s) ainda ativa(s). "
                   f"Verificando em {check_interval_min}min.")
            self.log(jid, "info", msg)
            self.hub.reschedule_job(jid, delay_seconds=check_interval_min * 60)
            return RunnerResult(success=True, message=msg)

        # Tem alvos mas todas as safehouses estão ocupadas → aguardar liberação
        if targets and not free_entries:
            msg = (f"[WorldSpy] {len(targets)} alvo(s) pendente(s) mas "
                   f"todas as {len(city_entries)} safehouses ocupadas. "
                   f"Verificando em {check_interval_min}min.")
            self.log(jid, "info", msg)
            self.hub.reschedule_job(jid, delay_seconds=check_interval_min * 60)
            return RunnerResult(success=True, message=msg)

        # ── Spawna jobs filhos (ac=15) para safehouses livres ─────────────────
        # Regras garantidas pelo hub:
        #   - 1 job ac=15 por source city (busy_sources)
        #   - 1 job ac=15 por target city (occupied_targets, filtrado antes)
        #   - skip_if_valid exclui alvos com intel fresca
        spawned = 0
        skipped = 0

        # Trabalha com cópias locais para não mutar durante iteração
        remaining_free = list(free_entries)

        for target in targets:
            if not remaining_free:
                self.log(jid, "info",
                         f"[WorldSpy] Safehouses livres esgotadas após {spawned} spawns. "
                         f"{len(targets) - spawned - skipped} alvos ficam para próximo check.")
                break

            target_city_id = str(target.get("game_city_id") or "").strip()
            if not target_city_id:
                skipped += 1
                continue

            # Round-robin: pega próxima safehouse livre
            source_entry = remaining_free[spawned % len(remaining_free)]
            source_city  = source_entry["city_game_id"]
            source_ga_pk = source_entry["ga_pk"]

            # Remove da lista de livres para este ciclo (1 target por safehouse por spawn)
            remaining_free = [e for e in remaining_free if e["city_game_id"] != source_city]

            child_inputs = {
                "city_id":            source_city,
                "target_city_id":     target_city_id,
                "target_city_name":   str(target.get("city_name") or ""),
                "target_owner":       str(target.get("owner_name") or ""),
                "target_owner_id":    str(target.get("owner_id") or ""),
                "island_id":          str(target.get("island_id") or ""),
                "mission_id":         missions_raw,
                "max_detection_risk": max_detection_risk,
                "recall_after":       recall_after,
                "save_reports":       save_reports,
                "delete_after_save":  delete_after_save,
            }

            try:
                resp = self.hub.spawn_job(
                    jid,
                    action_code=15,
                    inputs=child_inputs,
                    delay_seconds=spawned * 30,   # escalonar 30s para não sobrecarregar
                    game_account_id=source_ga_pk,
                )
                spawned += 1
                child_jid = resp.get("new_job_id", "?")
                self.log(jid, "info",
                         f"[WorldSpy] → {target.get('city_name')} ({target.get('owner_name')}) "
                         f"safehouse={source_city} ga={source_ga_pk[:8]}… job={child_jid}")
            except Exception as exc:
                skipped += 1
                # Devolve safehouse à lista de livres se o spawn falhou
                remaining_free.append(source_entry)
                self.log(jid, "warn",
                         f"[WorldSpy] Falha ao criar job para {target.get('city_name')}: {exc}")

        summary = (
            f"[WorldSpy] {spawned} job(s) criado(s)"
            + (f", {skipped} ignorado(s)" if skipped else "")
            + f". Próxima verificação em {check_interval_min}min."
        )
        self.log(jid, "info", summary)
        self.hub.reschedule_job(jid, delay_seconds=check_interval_min * 60)
        return RunnerResult(success=True, message=summary)
