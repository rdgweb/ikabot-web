"""Monitor de Combate (ac=34).

Acompanha os combates de uma conta de perto: olha a lista de combates do jogo a cada poucos
minutos enquanto houver combate em andamento e entrega ao hub os rounds novos e o resumo
(services.combat_watch). O hub guarda o relatório, desenha o campo e decide o que avisar no
Telegram.

N-109: a mesma vigia roda dentro do Alerta de Ataques (701) a cada verificação; este job só
existe para quem quer o acompanhamento mais frequente. Ele se encerra sozinho quando nada
mais está em andamento.
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from services.combat_watch import watch_combats

logger = logging.getLogger(__name__)

POLL_SECONDS = 5 * 60              # a round of the game takes 15 minutes
IDLE_POLL_SECONDS = 90
GIVE_UP_AFTER_ARRIVAL = 30 * 60    # with nothing in progress for this long after the arrival, the monitor ends


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


@register_runner(34)
class CombatMonitorRunner(BaseRunner):
    """Entrega ao hub os combates da conta enquanto houver um em andamento."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        arrival_eta = _to_int(inputs.get("arrival_eta_seconds"), 0)   # seconds until an expected attack lands
        waited = _to_int(inputs.get("waited_after_arrival"), 0)
        city_id = str(inputs.get("target_city_id") or inputs.get("city_id") or "").strip()
        if not city_id:
            cities = (self.hub.get_snapshot(game_account_id=ga_id) or {}).get("cities") or []
            city_id = str((cities[0] or {}).get("id") or "") if cities else ""
        if not city_id:
            return RunnerResult(success=True, data={"status": "no_city"})

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            watched = watch_combats(
                client, self.hub, game_account_id=ga_id, city_id=city_id, state=inputs.get("combat_watch"),
                log=lambda level, message: self.log(jid, level, message),
            )
            self.save_game_client(ga_id, client)
        except Exception as exc:
            logger.exception("CombatMonitorRunner failed for job %s", jid)
            self.log(jid, "warn", f"Falha no monitor de combate: {exc}")
            return RunnerResult(success=True, reschedule_seconds=IDLE_POLL_SECONDS, reschedule_inputs=inputs,
                                data={"status": "retry", "error": str(exc)})

        out = dict(inputs)
        out["combat_watch"] = watched["state"]
        out.pop("arrival_eta_seconds", None)
        counts = {"delivered": watched["delivered"], "open": watched["open"]}
        if watched["open"]:
            out["waited_after_arrival"] = 0
            # right after the next round when its time is known, else every 5 minutes
            ahead = watched.get("next_round_in")
            wait = max(60, min(ahead + 45, 16 * 60)) if ahead else POLL_SECONDS
            self.log(jid, "info", f"{watched['open']} combate(s) em andamento — próxima olhada em {max(1, round(wait / 60))} min.")
            return RunnerResult(success=True, reschedule_seconds=wait, reschedule_inputs=out, data={"status": "live", **counts})
        if arrival_eta > IDLE_POLL_SECONDS:
            self.log(jid, "info", f"Aguardando a chegada do ataque em ~{arrival_eta}s.")
            return RunnerResult(success=True, reschedule_seconds=arrival_eta, reschedule_inputs=out, data={"status": "awaiting_arrival", **counts})
        waited += IDLE_POLL_SECONDS
        if waited < GIVE_UP_AFTER_ARRIVAL:
            out["waited_after_arrival"] = waited
            return RunnerResult(success=True, reschedule_seconds=IDLE_POLL_SECONDS, reschedule_inputs=out, data={"status": "waiting_combat", **counts})
        self.log(jid, "info", "Nenhum combate em andamento — encerrando o monitor.")
        return RunnerResult(success=True, data={"status": "no_combat_finished", **counts})
