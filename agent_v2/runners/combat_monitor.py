"""Monitor de Combate (ac=34) — N-51.

Detecta relatorios de combate novos e envia ao Telegram um resumo com a
tabela de perdas de cada lado. Reaproveita os parsers de combate existentes.
Dedup por combat_id no estado do proprio job (reschedule_inputs).
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


@register_runner(34)
class CombatMonitorRunner(BaseRunner):
    """Vigia os relatorios de combate da conta e notifica no Telegram."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        interval_minutes = max(3, _to_int(inputs.get("interval_minutes"), 10))
        notify_telegram = bool(inputs.get("notify_telegram", True))
        with_rounds = bool(inputs.get("with_rounds", True))
        seen = list(inputs.get("seen_combat_ids") or [])
        seen_set = {str(x) for x in seen}

        snapshot = self.hub.get_snapshot(game_account_id=ga_id)
        cities = (snapshot or {}).get("cities") or []
        city_id = str(inputs.get("city_id") or "").strip()
        if not city_id and cities:
            city_id = str((cities[0] or {}).get("id") or "")
        if not city_id:
            return RunnerResult(success=True, reschedule_seconds=interval_minutes * 60,
                                reschedule_inputs=inputs, data={"status": "no_city"})

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            reports = client.fetch_combat_reports(int(city_id), limit=10) or []

            new_reports = [r for r in reports if str(r.get("combat_id")) not in seen_set]
            new_reports.sort(key=lambda r: _to_int(r.get("combat_id")))

            sent = 0
            for rep in new_reports:
                combat_id = _to_int(rep.get("combat_id"))
                losses = {"attacker_losses": {}, "defender_losses": {}, "total_rounds": rep.get("rounds", 0)}
                if with_rounds:
                    try:
                        losses = client.fetch_combat_detailed_report(int(city_id), combat_id)
                    except Exception as exc:
                        self.log(jid, "warn", f"Detalhe do combate {combat_id} falhou: {exc}")

                self.log(
                    jid, "info",
                    f"Combate {combat_id}: {rep.get('result')} em {rep.get('city_name')} vs {rep.get('owner_name')} "
                    f"| rounds={losses.get('total_rounds') or rep.get('rounds')}",
                )
                if notify_telegram:
                    try:
                        self.hub.send_notification(
                            event="combat_report",
                            game_account_id=ga_id,
                            account_id=aid,
                            title="Relatorio de combate",
                            agent_name=str(job.get("agent") or ""),
                            metadata={
                                "combat_id": combat_id,
                                "result": rep.get("result"),
                                "city_name": rep.get("city_name"),
                                "owner_name": rep.get("owner_name"),
                                "date": rep.get("date"),
                                "total_rounds": losses.get("total_rounds") or rep.get("rounds") or 0,
                                "attacker_losses": losses.get("attacker_losses") or {},
                                "defender_losses": losses.get("defender_losses") or {},
                            },
                        )
                        sent += 1
                    except Exception as exc:
                        self.log(jid, "warn", f"Falha ao notificar combate {combat_id}: {exc}")

                seen_set.add(str(combat_id))

            self.save_game_client(ga_id, client)

            # mantem os ultimos 200 combat_ids no estado
            seen_out = sorted(seen_set, key=lambda x: _to_int(x))[-200:]
            out_inputs = dict(inputs)
            out_inputs["seen_combat_ids"] = seen_out
            return RunnerResult(
                success=True,
                reschedule_seconds=interval_minutes * 60,
                reschedule_inputs=out_inputs,
                data={"status": "ok", "new_reports": len(new_reports), "notified": sent},
            )
        except Exception as exc:
            logger.exception("CombatMonitorRunner failed for job %s", jid)
            self.log(jid, "warn", f"Falha no monitor de combate: {exc}")
            return RunnerResult(success=True, reschedule_seconds=60, reschedule_inputs=inputs,
                                data={"status": "retry", "error": str(exc)})
