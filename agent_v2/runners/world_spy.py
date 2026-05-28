"""
Runner de Espionagem Mundial — ação 16 (WorldSpyRunner).

Orquestra espionagem em massa:
  1. Busca alvos no hub (WorldDump filtrado, excluindo contas próprias).
  2. Para cada alvo (até max_targets por ciclo), spawna um job filho ac=15.
  3. Reschedula para o próximo ciclo após interval_minutes.

Opções de alvo (target_mode):
  all                — todos os jogadores no dump (exceto próprios)
  inactive           — apenas inativos (state=inactive)
  vacation           — apenas em férias (state=vacation)
  inactive_or_vacation — inativos ou em férias
  ally_tag           — aliança específica (requer campo ally_tag)
  owner_id           — jogador específico (requer campo owner_id)

skip_if_valid:
  Se True, pula cidades que já têm relatórios válidos para TODAS as missões configuradas.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

ERROR_RESCHEDULE = 5 * 60   # 5 min em caso de erro


@register_runner(16)
class WorldSpyRunner(BaseRunner):
    """Orquestra espionagem em múltiplas cidades via ac=15."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        ga_id = str(job.get("game_account_id") or "").strip()
        inputs: dict = job.get("inputs") or {}

        # ── Inputs ────────────────────────────────────────────────────────────
        city_id = str(inputs.get("city_id") or "").strip()
        missions_raw = str(inputs.get("missions") or "3,5").strip()
        target_mode = str(inputs.get("target_mode") or "inactive").strip()
        ally_tag = str(inputs.get("ally_tag") or "").strip()
        owner_id_filter = str(inputs.get("target_owner_id") or "").strip()
        skip_if_valid = bool(inputs.get("skip_if_valid", True))
        interval_minutes = max(5, int(inputs.get("interval_minutes") or 120))
        max_targets = max(1, min(100, int(inputs.get("max_targets") or 20)))
        max_detection_risk = float(inputs.get("max_detection_risk") or 35)
        recall_after = bool(inputs.get("recall_after", True))
        save_reports = bool(inputs.get("save_reports", True))
        delete_after_save = bool(inputs.get("delete_after_save", False))

        # Parse missions list
        mission_ids: list[int] = []
        for m in missions_raw.split(","):
            try:
                mission_ids.append(int(m.strip()))
            except (ValueError, TypeError):
                pass
        if not mission_ids:
            mission_ids = [3, 5]

        if not city_id:
            return RunnerResult(success=False, message="city_id não configurado.")

        self.log(jid, "info",
                 f"[WorldSpy] mode={target_mode} missions={mission_ids} "
                 f"max_targets={max_targets} skip_valid={skip_if_valid} "
                 f"interval={interval_minutes}min")

        # ── Busca alvos no hub ─────────────────────────────────────────────────
        try:
            result = self.hub.get_spy_targets(
                target_mode=target_mode,
                ally_tag=ally_tag,
                owner_id=owner_id_filter,
                skip_if_valid=skip_if_valid,
                missions=mission_ids,
                limit=max_targets,
                game_account_id=ga_id,
            )
        except Exception as exc:
            self.log(jid, "error", f"Erro ao buscar alvos: {exc}")
            self.hub.reschedule_job(jid, delay_seconds=ERROR_RESCHEDULE)
            return RunnerResult(success=False, message=f"Erro ao buscar alvos: {exc}")

        targets: list[dict] = result.get("targets") or []
        dump_id = result.get("dump_id") or ""
        self.log(jid, "info",
                 f"[WorldSpy] Dump {dump_id}: {len(targets)} alvos encontrados")

        if not targets:
            self.log(jid, "info", "[WorldSpy] Nenhum alvo disponível. Reagendando.")
            self.hub.reschedule_job(jid, delay_seconds=interval_minutes * 60)
            return RunnerResult(success=True, message="Nenhum alvo disponível.")

        # ── Spawna jobs filhos (ac=15) para cada alvo ─────────────────────────
        spawned = 0
        skipped = 0
        for target in targets:
            city_game_id = str(target.get("game_city_id") or "").strip()
            if not city_game_id:
                skipped += 1
                continue

            target_owner = str(target.get("owner_name") or "").strip()
            target_owner_id = str(target.get("owner_id") or "").strip()
            target_city_name = str(target.get("city_name") or "").strip()
            island_id = str(target.get("island_id") or "").strip()

            child_inputs = {
                "city_id": city_id,
                "target_city_id": city_game_id,
                "target_city_name": target_city_name,
                "target_owner": target_owner,
                "target_owner_id": target_owner_id,
                "island_id": island_id,
                "mission_id": missions_raw,
                "max_detection_risk": max_detection_risk,
                "recall_after": recall_after,
                "save_reports": save_reports,
                "delete_after_save": delete_after_save,
            }

            try:
                spawned_resp = self.hub.spawn_job(
                    jid,
                    action_code=15,
                    inputs=child_inputs,
                    delay_seconds=spawned * 30,  # escalonar 30s por job filho
                )
                spawned += 1
                self.log(jid, "info",
                         f"[WorldSpy] Job filho criado: {target_city_name} ({target_owner}) "
                         f"→ {spawned_resp.get('job_id', '?')}")
            except Exception as exc:
                skipped += 1
                self.log(jid, "warn",
                         f"[WorldSpy] Falha ao spawnar job para {target_city_name}: {exc}")

        summary = (
            f"[WorldSpy] Ciclo concluído: {spawned} jobs criados"
            + (f", {skipped} ignorados" if skipped else "")
            + f". Próximo ciclo em {interval_minutes}min."
        )
        self.log(jid, "info", summary)

        # ── Reagenda para o próximo ciclo ──────────────────────────────────────
        self.hub.reschedule_job(jid, delay_seconds=interval_minutes * 60)
        return RunnerResult(success=True, message=summary)
