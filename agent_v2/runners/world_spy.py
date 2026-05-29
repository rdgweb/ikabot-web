"""
Runner de Espionagem Mundial — ação 16 (WorldSpyRunner).

Arquitetura event-driven com mailbox (100% sem race condition):

  1. Primeira execução: root_id = jid (o próprio job é a raiz).
     Cria fallback via reschedule_job com __campaign_root_id=root_id.
     Hub atualiza root.progress_json["current_runner_id"] = fallback.
     Spawna filhos com __root_job_id=root_id.

  2. Filho (ac=15) termina (done phase):
     Chama hub.notify_parent(root_job_id).
     Hub: SELECT FOR UPDATE no current_runner → append inbox → se dormindo
          longe, adianta scheduled_for para +5s e re-dispatcha via Celery.

  3. Fallback acorda (em 5s ou pelo timer de 4h):
     Lê progress_json["inbox"] — notificações acumuladas de todos os filhos.
     Processa, spawna para safehouses livres, cria novo fallback.
     Hub atualiza root.current_runner_id = novo_fallback.

  4. 0 alvos + 0 safehouses ativas → encerra sem reagendar.

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

ERROR_RESCHEDULE = 5  * 60        # 5 min em caso de erro
FALLBACK_DELAY   = 4  * 60 * 60  # 4h — fallback caso notificação falhe

# Missões cujo resultado é global ao jogador (não varia por cidade).
# Basta espiionar UMA cidade por owner_id por ciclo completo.
PLAYER_SCOPE_MISSIONS: frozenset[int] = frozenset({
    3,   # Nível de pesquisa
    7,   # Tropas e frotas (movimentos — global ao player)
    10,  # Observar comunicação
    21,  # Ver estado
    24,  # Cargo na aliança
    25,  # Forma de governo
    26,  # Invenções
    27,  # Colônias
})


def _parse_int(value, default=None):
    try:
        return int(value) if value is not None and str(value).strip() != "" else default
    except (TypeError, ValueError):
        return default


@register_runner(16)
class WorldSpyRunner(BaseRunner):
    """Orquestra espionagem event-driven em múltiplas cidades via ac=15.

    Mailbox pattern: root job é estável, filhos sempre notificam o mesmo ID.
    Hub mantém root.progress_json["current_runner_id"] apontando para o
    fallback ativo. Notificações são enfileiradas atomicamente — 100% correto
    mesmo com filhos terminando simultaneamente.
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid   = job["job_id"]
        ga_id = str(job.get("game_account_id") or "").strip()
        inputs: dict = job.get("inputs") or {}

        # ── Campaign root ID (mailbox pattern) ───────────────────────────────
        # Na primeira execução não existe __campaign_root_id → sou o root.
        # Nas execuções seguintes (fallbacks), o root é preservado nos inputs.
        root_id = str(inputs.get("__campaign_root_id") or "").strip() or jid

        # ── Inputs de alerta de raid ──────────────────────────────────────────
        raid_alert_enabled   = bool(inputs.get("raid_alert_enabled", False))
        raid_threshold       = _parse_int(inputs.get("raid_alert_threshold"), 50000)
        raid_source_city     = str(inputs.get("raid_source_city_id") or "").strip()
        raid_transporters    = _parse_int(inputs.get("raid_transporters"), 0)
        raid_max_trips       = _parse_int(inputs.get("raid_max_trips"), 5)

        # ── Inbox: notificações de filhos acumuladas ──────────────────────────
        progress: dict = job.get("progress") or {}
        inbox: list = progress.get("inbox") or []
        if inbox:
            self.log(jid, "info",
                     f"[WorldSpy] Inbox: {len(inbox)} notificação(ões) de filhos recebida(s)")
            for entry in inbox:
                cid   = str(entry.get("child_job_id", "?"))[:8]
                csrc  = entry.get("source_city_id", "?")
                ctgt  = entry.get("target_city_id", "?")
                cok   = entry.get("success", "?")
                mdone = entry.get("missions_done", [])
                mfail = entry.get("missions_failed", [])
                self.log(jid, "info",
                         f"[WorldSpy]   ↳ job={cid} src={csrc} tgt={ctgt} "
                         f"success={cok} done={mdone} fail={mfail}")

                # ── Raid alert: verificar intel da cidade espionada ───────────
                if raid_alert_enabled and cok and ctgt:
                    try:
                        self._check_raid_alert(
                            jid=jid,
                            ga_id=ga_id,
                            target_city_id=str(ctgt),
                            island_id=str(entry.get("island_id") or ""),
                            target_name=str(entry.get("target_city_name") or ""),
                            target_owner=str(entry.get("target_owner") or ""),
                            threshold=raid_threshold,
                            raid_source_city=raid_source_city,
                            raid_transporters=raid_transporters,
                            raid_max_trips=raid_max_trips,
                        )
                    except Exception as exc:
                        self.log(jid, "warn",
                                 f"[WorldSpy] Falha no raid alert para {ctgt}: {exc}")

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

        missions_raw       = str(inputs.get("missions") or "3,5").strip()
        only_inactive      = bool(inputs.get("only_inactive", True))
        x_min              = _parse_int(inputs.get("region_x_min"))
        x_max              = _parse_int(inputs.get("region_x_max"))
        y_min              = _parse_int(inputs.get("region_y_min"))
        y_max              = _parse_int(inputs.get("region_y_max"))
        max_total_score    = _parse_int(inputs.get("max_total_score"), 0)
        max_army_score     = _parse_int(inputs.get("max_army_score"), 0)
        skip_if_valid      = bool(inputs.get("skip_if_valid", True))
        intel_ttl_hours    = max(0, _parse_int(inputs.get("intel_ttl_hours"), 0))
        max_detection_risk = float(inputs.get("max_detection_risk") or 35)
        recall_after       = bool(inputs.get("recall_after", True))
        save_reports       = bool(inputs.get("save_reports", True))
        delete_after_save  = bool(inputs.get("delete_after_save", False))

        # Jogadores que já receberam missões player-scope neste ciclo.
        # Persiste entre fallbacks via inputs do reschedule.
        spied_player_ids: set[str] = set(inputs.get("__spied_player_ids") or [])

        # Parse missions e separar por escopo
        mission_ids: list[int] = []
        for m in missions_raw.split(","):
            try:
                mission_ids.append(int(m.strip()))
            except (ValueError, TypeError):
                pass
        if not mission_ids:
            mission_ids = [3, 5]

        city_missions:   list[int] = [m for m in mission_ids if m not in PLAYER_SCOPE_MISSIONS]
        player_missions: list[int] = [m for m in mission_ids if m in PLAYER_SCOPE_MISSIONS]
        has_player_scope = bool(player_missions)

        unique_gas  = len({e["ga_pk"] for e in city_entries})
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
                 f"ttl={ttl_desc} root={root_id[:8]}")

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
            return RunnerResult(success=False, reschedule_seconds=ERROR_RESCHEDULE)

        targets: list[dict]    = result.get("targets") or []
        dump_id                = result.get("dump_id") or "?"
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

        # ── Criar fallback ANTES de spawnar ───────────────────────────────────
        # reschedule_job com __campaign_root_id faz hub atualizar
        # root.progress_json["current_runner_id"] = novo_fallback.
        # Filhos notificam o root (ID estável) → hub acorda o fallback correto.
        try:
            resp_next = self.hub.reschedule_job(
                jid,
                delay_seconds=FALLBACK_DELAY,
                inputs={
                    "__campaign_root_id": root_id,
                    "__spied_player_ids": list(spied_player_ids),
                },
            )
            next_jid = resp_next.get("new_job_id", "")
            self.log(jid, "info",
                     f"[WorldSpy] Fallback criado: {str(next_jid)[:8]} "
                     f"(root={root_id[:8]} → pointer atualizado)")
        except Exception as exc:
            self.log(jid, "warn",
                     f"[WorldSpy] Falha ao criar fallback: {exc}. "
                     f"Filhos não conseguirão notificar via mailbox.")
            next_jid = ""

        # ── Apenas aguardando filhos (sem novos alvos) ────────────────────────
        if not targets:
            self.log(jid, "info",
                     f"[WorldSpy] {len(busy_sources)} safehouse(s) ativas, sem novos alvos. "
                     f"Aguardando notificações via mailbox.")
            return RunnerResult(success=True)

        # ── Tem alvos mas todas as safehouses ocupadas ────────────────────────
        if not free_entries:
            self.log(jid, "info",
                     f"[WorldSpy] {len(targets)} alvo(s) pendente(s) mas todas as "
                     f"{len(city_entries)} safehouses ocupadas. Aguardando via mailbox.")
            return RunnerResult(success=True)

        # ── Spawna jobs filhos (ac=15) para safehouses livres ─────────────────
        spawned        = 0
        skipped        = 0
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

            # Determinar missões para esta cidade
            owner_id = str(target.get("owner_id") or "").strip()
            player_already_covered = owner_id in spied_player_ids

            if has_player_scope and not player_already_covered:
                # Primeira cidade deste player: todas as missões (city + player-scope)
                effective_missions = mission_ids
                spied_player_ids.add(owner_id)
            elif city_missions:
                # Demais cidades do mesmo player: só missões por-cidade
                effective_missions = city_missions
            else:
                # Apenas missões player-scope e player já foi coberto → pular cidade
                skipped += 1
                self.log(jid, "info",
                         f"[WorldSpy] Pulando {target.get('city_name')} — player {owner_id[:8]} "
                         f"já coberto (sem missões por-cidade pendentes).")
                continue

            effective_missions_raw = ",".join(str(m) for m in effective_missions)

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
                "mission_id":          effective_missions_raw,
                "max_detection_risk":  max_detection_risk,
                "recall_after":        recall_after,
                "save_reports":        save_reports,
                "delete_after_save":   delete_after_save,
                "__root_job_id":       root_id,  # mailbox: filho notifica sempre o root estável
            }

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
                scope_tag = "city+player" if not player_already_covered and has_player_scope else "city"
                self.log(jid, "info",
                         f"[WorldSpy] → {target.get('city_name')} ({target.get('owner_name')}) "
                         f"missions={effective_missions} [{scope_tag}] "
                         f"safehouse={source_city} ga={source_ga_pk[:8]}… job={str(child_jid)[:8]}")
            except Exception as exc:
                skipped += 1
                remaining_free.append(source_entry)
                self.log(jid, "warn",
                         f"[WorldSpy] Falha ao criar job para {target.get('city_name')}: {exc}")

        self.log(jid, "info",
                 f"[WorldSpy] {spawned} job(s) criado(s)"
                 + (f", {skipped} ignorado(s)" if skipped else "")
                 + f". Aguardando notificações via mailbox (root={root_id[:8]}).")
        return RunnerResult(success=True)

    # ── Raid Alert ────────────────────────────────────────────────────────────

    def _check_raid_alert(
        self,
        *,
        jid: str,
        ga_id: str,
        target_city_id: str,
        island_id: str,
        target_name: str,
        target_owner: str,
        threshold: int,
        raid_source_city: str,
        raid_transporters: int,
        raid_max_trips: int,
    ) -> None:
        """Verifica intel do alvo e envia alerta Telegram se recursos > threshold."""
        from services.combat import (
            format_battle_summary,
            recommend_army,
            transporters_needed,
            trips_needed,
            loot_capacity,
        )

        # Buscar relatório mais recente do alvo no hub
        try:
            intel = self.hub.get_latest_spy_intel(
                target_city_id=target_city_id,
                game_account_id=ga_id,
            )
        except Exception as exc:
            logger.warning("[WorldSpy] Falha ao buscar intel de %s: %s", target_city_id, exc)
            return

        if not intel:
            return

        # Extrair recursos e tropas
        resources: dict[str, int] = intel.get("resources") or {}
        troops:    dict[int, int]  = {int(k): int(v)
                                       for k, v in (intel.get("troops") or {}).items()
                                       if int(v) > 0}
        wall_level = int(intel.get("wall_level") or 1)
        total_res  = sum(resources.values())

        if total_res < threshold:
            logger.debug("[WorldSpy] %s: recursos=%d < threshold=%d — sem alerta.",
                         target_city_id, total_res, threshold)
            return

        # Calcular força recomendada
        rec = recommend_army(troops, wall_level=wall_level)
        n_transporters = raid_transporters or transporters_needed(total_res)
        n_trips        = trips_needed(total_res, n_transporters) if n_transporters else "?"

        # Buscar tempo de viagem real via fetch_plunder_view
        travel_seconds = 0
        if raid_source_city and island_id:
            try:
                from sessions.game_session_service import GameSessionService
                client = GameSessionService(hub=self.hub).get_or_create_client(ga_id, jid=jid)
                view = client.fetch_plunder_view(
                    int(raid_source_city), int(target_city_id), int(island_id)
                )
                travel_seconds = view.get("travel_seconds", 0)
                if travel_seconds:
                    self.log(jid, "info",
                             f"[WorldSpy] ETA para raid em {target_name}: "
                             f"{travel_seconds//3600}h {(travel_seconds%3600)//60}min")
            except Exception as exc:
                logger.warning("[WorldSpy] Falha ao buscar ETA de viagem: %s", exc)

        summary = format_battle_summary(
            enemy_units=troops,
            recommendation=rec,
            resources=resources,
            n_transporters=n_transporters,
            travel_seconds=travel_seconds,
            wall_level=wall_level,
        )

        city_label = target_name or target_city_id
        owner_label = target_owner or "?"
        title  = f"🏴‍☠️ Alvo rico: {city_label} ({owner_label})"
        body   = (
            f"{summary}\n\n"
            f"Para roubar: dispare ac=1007 com\n"
            f"  source_city_id={raid_source_city or '(configurar)'}\n"
            f"  target_city_id={target_city_id}\n"
            f"  island_id={island_id}\n"
            f"  transporters={n_transporters}\n"
            f"  max_trips={raid_max_trips}"
        )

        self.log(jid, "info",
                 f"[WorldSpy] 🏴‍ Alerta de raid: {city_label} ({owner_label}) "
                 f"recursos={total_res:,} > threshold={threshold:,}")

        # Botões inline: Roubar agora / Ignorar
        # callback_data limitado a 64 bytes — usar IDs curtos
        can_win_icon = "✅" if rec["can_win_with_recommended"] else "⚠️"
        raid_cb = f"raid_now:{target_city_id}:{island_id}:{ga_id}"
        skip_cb = f"raid_skip:{target_city_id}"
        reply_markup = {
            "inline_keyboard": [[
                {"text": f"{can_win_icon} Roubar agora", "callback_data": raid_cb},
                {"text": "❌ Ignorar",                   "callback_data": skip_cb},
            ]]
        }

        try:
            self.hub.send_notification(
                event="raid_alert",
                game_account_id=ga_id,
                title=title,
                body=body,
                metadata={
                    "target_city_id":   target_city_id,
                    "island_id":        island_id,
                    "target_name":      target_name,
                    "target_owner":     target_owner,
                    "total_resources":  total_res,
                    "resources":        resources,
                    "troops":           {str(k): v for k, v in troops.items()},
                    "wall_level":       wall_level,
                    "recommended_army": {str(k): v for k, v in rec["recommended"].items()},
                    "raid_source_city": raid_source_city,
                    "raid_transporters": n_transporters,
                    "raid_max_trips":   raid_max_trips,
                    "can_win":          rec["can_win_with_recommended"],
                    "reply_markup":     reply_markup,
                },
            )
        except Exception as exc:
            logger.warning("[WorldSpy] Falha ao enviar notificação de raid: %s", exc)
