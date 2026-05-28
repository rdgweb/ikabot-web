"""
Runner de Espionagem Mundial — ação 16 (WorldSpyRunner).

Orquestra espionagem em massa numa região do mapa:
  1. Busca alvos no hub filtrando por região, estado (inativo) e pontuação.
  2. Distribui alvos entre safehouses configuradas (round-robin por city_ids).
  3. Spawna um job filho ac=15 por alvo com a safehouse atribuída.
  4. Reschedula para o próximo ciclo após interval_minutes.

Safehouses:
  city_ids — IDs de cidades com Casa de Espionagem, separados por vírgula.
  O runner distribui alvos ciclicamente entre elas.

Filtros de alvo:
  only_inactive    — apenas inativos (mode férias não pode ser espionado)
  region_x_min/max — retângulo X no mapa
  region_y_min/max — retângulo Y no mapa
  max_total_score  — pontuação total (construção+pesquisa+exército) — 0=sem limite
  max_army_score   — pontuação de exército — 0=sem limite

Inteligência:
  skip_if_valid — pula cidades com intel válida para todas as missões configuradas
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
    """Orquestra espionagem em múltiplas cidades via ac=15."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        ga_id = str(job.get("game_account_id") or "").strip()
        inputs: dict = job.get("inputs") or {}

        # ── Inputs ────────────────────────────────────────────────────────────
        city_ids_raw = str(inputs.get("city_ids") or "").strip()
        city_ids = [cid.strip() for cid in city_ids_raw.split(",") if cid.strip()]

        missions_raw = str(inputs.get("missions") or "3,5").strip()
        only_inactive = bool(inputs.get("only_inactive", True))
        x_min = _parse_int(inputs.get("region_x_min"))
        x_max = _parse_int(inputs.get("region_x_max"))
        y_min = _parse_int(inputs.get("region_y_min"))
        y_max = _parse_int(inputs.get("region_y_max"))
        max_total_score = _parse_int(inputs.get("max_total_score"), 0)
        max_army_score  = _parse_int(inputs.get("max_army_score"), 0)
        skip_if_valid = bool(inputs.get("skip_if_valid", True))
        interval_minutes = max(5, _parse_int(inputs.get("interval_minutes"), 120))
        max_targets = max(1, min(100, _parse_int(inputs.get("max_targets"), 20)))
        max_detection_risk = float(inputs.get("max_detection_risk") or 35)
        recall_after = bool(inputs.get("recall_after", True))
        save_reports = bool(inputs.get("save_reports", True))
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

        if not city_ids:
            return RunnerResult(success=False, message="city_ids não configurado.")

        region_desc = (
            f"[{x_min}:{y_min}→{x_max}:{y_max}]"
            if any(v is not None for v in (x_min, x_max, y_min, y_max))
            else "mapa inteiro"
        )
        self.log(jid, "info",
                 f"[WorldSpy] safehouses={len(city_ids)} missions={mission_ids} "
                 f"inactive={only_inactive} region={region_desc} "
                 f"max_total={max_total_score or '—'} max_army={max_army_score or '—'} "
                 f"max_targets={max_targets} interval={interval_minutes}min")

        # ── Busca alvos ────────────────────────────────────────────────────────
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
            )
        except Exception as exc:
            self.log(jid, "error", f"Erro ao buscar alvos: {exc}")
            self.hub.reschedule_job(jid, delay_seconds=ERROR_RESCHEDULE)
            return RunnerResult(success=False, message=f"Erro ao buscar alvos: {exc}")

        targets: list[dict] = result.get("targets") or []
        dump_id = result.get("dump_id") or "?"
        self.log(jid, "info", f"[WorldSpy] Dump {dump_id}: {len(targets)} alvos")

        if not targets:
            self.log(jid, "info", "[WorldSpy] Nenhum alvo. Reagendando.")
            self.hub.reschedule_job(jid, delay_seconds=interval_minutes * 60)
            return RunnerResult(success=True, message="Nenhum alvo disponível.")

        # ── Spawna jobs filhos (ac=15) — round-robin entre safehouses ─────────
        spawned = 0
        skipped = 0
        for i, target in enumerate(targets):
            city_game_id = str(target.get("game_city_id") or "").strip()
            if not city_game_id:
                skipped += 1
                continue

            # Distribui safehouses ciclicamente
            source_city = city_ids[i % len(city_ids)]

            child_inputs = {
                "city_id":          source_city,
                "target_city_id":   city_game_id,
                "target_city_name": str(target.get("city_name") or ""),
                "target_owner":     str(target.get("owner_name") or ""),
                "target_owner_id":  str(target.get("owner_id") or ""),
                "island_id":        str(target.get("island_id") or ""),
                "mission_id":       missions_raw,
                "max_detection_risk": max_detection_risk,
                "recall_after":     recall_after,
                "save_reports":     save_reports,
                "delete_after_save": delete_after_save,
            }

            try:
                resp = self.hub.spawn_job(
                    jid,
                    action_code=15,
                    inputs=child_inputs,
                    delay_seconds=spawned * 30,  # escalonar 30s
                )
                spawned += 1
                self.log(jid, "info",
                         f"[WorldSpy] → {target.get('city_name')} ({target.get('owner_name')}) "
                         f"safehouse={source_city} job={resp.get('job_id', '?')}")
            except Exception as exc:
                skipped += 1
                self.log(jid, "warn",
                         f"[WorldSpy] Falha ao criar job para {target.get('city_name')}: {exc}")

        summary = (
            f"[WorldSpy] {spawned} jobs criados"
            + (f", {skipped} ignorados" if skipped else "")
            + f". Próximo em {interval_minutes}min."
        )
        self.log(jid, "info", summary)
        self.hub.reschedule_job(jid, delay_seconds=interval_minutes * 60)
        return RunnerResult(success=True, message=summary)
