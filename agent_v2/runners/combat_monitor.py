"""Monitor de Combate (ac=34) — N-51 (ao vivo).

Fica DORMENTE (reschedule longo) ate o Alerta de Ataques (701) sinalizar
combate/ataque iminente no attack_alert_state do snapshot. Ao acordar, entra
em modo ativo: acompanha o combate ao vivo, enviando ao Telegram cada ROUND
novo assim que acontece e, ao final, o relatorio final. Dedup por round.
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

DORMANT_SECONDS = 8 * 60    # verificacao esparsa quando nao ha ataque
LIVE_SECONDS = 2 * 60       # verificacao frequente durante a batalha


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


@register_runner(34)
class CombatMonitorRunner(BaseRunner):
    """Acompanha combates ao vivo, round a round, acordado pelo alerta de ataque."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        notify_telegram = bool(inputs.get("notify_telegram", True))
        # estado persistente: rounds ja notificados por combate e combates finalizados
        notified_rounds = dict(inputs.get("notified_rounds") or {})   # {combat_id: last_round}
        finalized = set(str(x) for x in (inputs.get("finalized_combats") or []))

        snapshot = self.hub.get_snapshot(game_account_id=ga_id)
        base = (snapshot or {}).get("base_snapshot") or {}
        attack_state = base.get("attack_alert_state") or {}
        hostile = _to_int(attack_state.get("hostile_count"), 0)
        cities = (snapshot or {}).get("cities") or []
        city_id = str(inputs.get("city_id") or "").strip()
        if not city_id and cities:
            city_id = str((cities[0] or {}).get("id") or "")
        if not city_id:
            return self._sleep(inputs, DORMANT_SECONDS, "no_city")

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            reports = client.fetch_combat_reports(int(city_id), limit=10) or []

            # Combates de interesse: os que ainda nao finalizamos (podem estar ao vivo)
            active_found = False
            for rep in reports:
                combat_id = str(rep.get("combat_id"))
                if combat_id in finalized:
                    continue
                detail = {}
                try:
                    detail = client.fetch_combat_detailed_report(int(city_id), int(combat_id))
                except Exception as exc:
                    self.log(jid, "warn", f"Detalhe do combate {combat_id} falhou: {exc}")
                    continue

                rounds = detail.get("rounds") or []
                total_known = _to_int(detail.get("total_rounds_known"), 0)
                seen_upto = _to_int(notified_rounds.get(combat_id), 0)
                # ainda em andamento se o jogo informa mais rounds do que ja ocorreram
                in_progress = total_known > len(rounds)
                if in_progress or len(rounds) > seen_upto:
                    active_found = True

                # envia cada round novo
                for rd in rounds:
                    rnum = _to_int(rd.get("round"), 0)
                    if rnum <= seen_upto:
                        continue
                    if notify_telegram:
                        self._notify_round(aid, ga_id, job, rep, rd, total_known or len(rounds))
                    notified_rounds[combat_id] = rnum
                    seen_upto = rnum

                # batalha terminou (jogo nao anuncia mais rounds): relatorio final
                if not in_progress and len(rounds) > 0:
                    if notify_telegram:
                        self._notify_final(aid, ga_id, job, rep, detail)
                    finalized.add(combat_id)
                    self.log(jid, "info", f"Combate {combat_id} finalizado ({len(rounds)} rounds).")

            self.save_game_client(ga_id, client)

            out = dict(inputs)
            out["notified_rounds"] = {k: v for k, v in notified_rounds.items() if str(k) not in finalized}
            out["finalized_combats"] = sorted(finalized, key=lambda x: _to_int(x))[-100:]

            # ativo se ha combate rolando OU o alerta indica ataque; senao dorme
            live = active_found or hostile > 0
            next_seconds = LIVE_SECONDS if live else DORMANT_SECONDS
            if live:
                self.log(jid, "info", f"Combate ao vivo — proximo round em {next_seconds}s.")
            return RunnerResult(success=True, reschedule_seconds=next_seconds,
                                reschedule_inputs=out,
                                data={"status": "ok", "live": live, "hostile": hostile})
        except Exception as exc:
            logger.exception("CombatMonitorRunner failed for job %s", jid)
            self.log(jid, "warn", f"Falha no monitor de combate: {exc}")
            return self._sleep(inputs, 60, "retry", error=str(exc))

    def _sleep(self, inputs, seconds, status, **extra):
        data = {"status": status}
        data.update(extra)
        return RunnerResult(success=True, reschedule_seconds=seconds, reschedule_inputs=inputs, data=data)

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
