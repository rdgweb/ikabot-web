"""Monitor de Combate (ac=34) — N-51 (ao vivo, sob demanda).

Dormente de verdade: NAO fica reagendando sozinho. E' spawnado pelo Alerta
de Ataques (701) quando um ataque e detectado, recebendo o ETA da chegada.
A partir dai ele se agenda:
  - antes do ataque chegar: reagenda para o ETA da chegada (nada a fazer ainda);
  - durante o combate: envia cada round novo e reagenda pelo tempo do round;
  - quando a batalha termina: envia o relatorio final e ENCERRA (nao reagenda).
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

ROUND_INTERVAL_SECONDS = 15 * 60   # estimativa do tempo entre rounds (afinar com batalha real)
MIN_POLL_SECONDS = 90
GIVE_UP_AFTER_ARRIVAL = 30 * 60    # se apos a chegada nao aparecer combate nesse tempo, encerra


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


@register_runner(34)
class CombatMonitorRunner(BaseRunner):
    """Acompanha um combate ao vivo, round a round. Spawnado pelo alerta de ataque."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            return self._done(data={"error": "missing_game_account"})

        notify_telegram = bool(inputs.get("notify_telegram", True))
        arrival_eta = _to_int(inputs.get("arrival_eta_seconds"), 0)   # segundos ate a chegada (do 701)
        waited = _to_int(inputs.get("waited_after_arrival"), 0)
        notified_rounds = dict(inputs.get("notified_rounds") or {})   # {combat_id: last_round}
        finalized = set(str(x) for x in (inputs.get("finalized_combats") or []))
        target_city_id = str(inputs.get("target_city_id") or "").strip()

        snapshot = self.hub.get_snapshot(game_account_id=ga_id)
        cities = (snapshot or {}).get("cities") or []
        city_id = target_city_id or str(inputs.get("city_id") or "").strip()
        if not city_id and cities:
            city_id = str((cities[0] or {}).get("id") or "")
        if not city_id:
            return self._done(data={"status": "no_city"})

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return self._done(data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            reports = client.fetch_combat_reports(int(city_id), limit=10) or []

            active_found = False
            for rep in reports:
                combat_id = str(rep.get("combat_id"))
                if combat_id in finalized:
                    continue
                try:
                    detail = client.fetch_combat_detailed_report(int(city_id), int(combat_id))
                except Exception as exc:
                    self.log(jid, "warn", f"Detalhe do combate {combat_id} falhou: {exc}")
                    continue

                rounds = detail.get("rounds") or []
                total_known = _to_int(detail.get("total_rounds_known"), 0)
                seen_upto = _to_int(notified_rounds.get(combat_id), 0)
                in_progress = total_known > len(rounds)
                if in_progress or len(rounds) > seen_upto:
                    active_found = True

                for rd in rounds:
                    rnum = _to_int(rd.get("round"), 0)
                    if rnum <= seen_upto:
                        continue
                    if notify_telegram:
                        self._notify_round(aid, ga_id, job, rep, rd, total_known or len(rounds))
                    notified_rounds[combat_id] = rnum
                    seen_upto = rnum

                if not in_progress and len(rounds) > 0:
                    if notify_telegram:
                        self._notify_final(aid, ga_id, job, rep, detail)
                    finalized.add(combat_id)
                    self.log(jid, "info", f"Combate {combat_id} finalizado ({len(rounds)} rounds).")

            self.save_game_client(ga_id, client)

            out = dict(inputs)
            out["notified_rounds"] = {k: v for k, v in notified_rounds.items() if str(k) not in finalized}
            out["finalized_combats"] = sorted(finalized, key=lambda x: _to_int(x))[-100:]
            out.pop("arrival_eta_seconds", None)

            # Combate em andamento -> proximo round
            if active_found:
                out["waited_after_arrival"] = 0
                self.log(jid, "info", f"Combate ao vivo — proximo round em {ROUND_INTERVAL_SECONDS}s.")
                return RunnerResult(success=True, reschedule_seconds=ROUND_INTERVAL_SECONDS, reschedule_inputs=out,
                                    data={"status": "live"})

            # Ataque ainda nao chegou -> espera ate a chegada
            if arrival_eta > MIN_POLL_SECONDS:
                self.log(jid, "info", f"Aguardando chegada do ataque em ~{arrival_eta}s.")
                return RunnerResult(success=True, reschedule_seconds=arrival_eta, reschedule_inputs=out,
                                    data={"status": "awaiting_arrival"})

            # Chegou (ou ETA baixo) mas sem combate ainda -> poll curto por um tempo
            waited += MIN_POLL_SECONDS
            if waited < GIVE_UP_AFTER_ARRIVAL:
                out["waited_after_arrival"] = waited
                return RunnerResult(success=True, reschedule_seconds=MIN_POLL_SECONDS, reschedule_inputs=out,
                                    data={"status": "waiting_combat"})

            # Nada aconteceu -> encerra (dorme de verdade ate o 701 spawnar de novo)
            self.log(jid, "info", "Sem combate apos a janela de espera — encerrando o monitor.")
            return self._done(data={"status": "no_combat_finished"})
        except Exception as exc:
            logger.exception("CombatMonitorRunner failed for job %s", jid)
            self.log(jid, "warn", f"Falha no monitor de combate: {exc}")
            # erro transitorio enquanto o ataque ainda pode chegar: tenta de novo
            if arrival_eta > 0 or notified_rounds:
                return RunnerResult(success=True, reschedule_seconds=MIN_POLL_SECONDS, reschedule_inputs=inputs,
                                    data={"status": "retry", "error": str(exc)})
            return self._done(data={"status": "error", "error": str(exc)})

    def _done(self, *, data: dict) -> RunnerResult:
        # sem reschedule_seconds => job termina (monitor volta a dormir)
        success = "error" not in data
        return RunnerResult(success=success, data=data)

    def _notify_round(self, aid, ga_id, job, rep, rd, total):
        try:
            self.hub.send_notification(
                event="combat_report",
                game_account_id=ga_id, account_id=aid,
                title="Combate — round", agent_name=str(job.get("agent") or ""),
                metadata={
                    "phase": "round",
                    "combat_id": _to_int(rep.get("combat_id")),
                    "result": rep.get("result"),
                    "city_name": rep.get("city_name"),
                    "owner_name": rep.get("owner_name"),
                    "round": _to_int(rd.get("round")),
                    "total_rounds": _to_int(total),
                    "attacker_losses": rd.get("attacker_losses") or {},
                    "defender_losses": rd.get("defender_losses") or {},
                },
            )
        except Exception as exc:
            logger.warning("notify_round failed: %s", exc)

    def _notify_final(self, aid, ga_id, job, rep, detail):
        try:
            self.hub.send_notification(
                event="combat_report",
                game_account_id=ga_id, account_id=aid,
                title="Combate — final", agent_name=str(job.get("agent") or ""),
                metadata={
                    "phase": "final",
                    "combat_id": _to_int(rep.get("combat_id")),
                    "result": rep.get("result"),
                    "city_name": rep.get("city_name"),
                    "owner_name": rep.get("owner_name"),
                    "total_rounds": _to_int(detail.get("total_rounds")),
                    "attacker_losses": detail.get("attacker_losses") or {},
                    "defender_losses": detail.get("defender_losses") or {},
                },
            )
        except Exception as exc:
            logger.warning("notify_final failed: %s", exc)
