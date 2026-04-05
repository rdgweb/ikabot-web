"""
Runner: donate_once - donate to forest or luxury project once.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from services.donation_automation import RETRY_BUFFER_SECONDS, build_modify_production_inputs
from services.island_donation import (
    fetch_donation_project_state,
    fetch_global_gold,
    fetch_worker_baseline,
)

logger = logging.getLogger(__name__)

DONATION_TYPE_MAP = {
    "wood": "resource",
    "resource": "resource",
    "tradegood": "tradegood",
}


@register_runner(901)
class DonateOnceRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}

        city_id = str(inputs.get("city_id", "")).strip()
        city_name = str(inputs.get("city_name") or city_id).strip() or city_id
        donation_type = DONATION_TYPE_MAP.get(str(inputs.get("donation_type", "")).strip().lower())
        try:
            requested_amount = int(inputs.get("amount", 0))
        except (ValueError, TypeError):
            requested_amount = 0

        if not city_id:
            self.log(jid, "error", "city_id nao informado")
            return RunnerResult(success=False, data={"error": "missing_city_id"})
        if not donation_type:
            self.log(jid, "error", f"donation_type invalido: {inputs.get('donation_type')!r}")
            return RunnerResult(success=False, data={"error": "invalid_donation_type"})
        if requested_amount <= 0:
            self.log(jid, "error", f"Quantidade invalida: {requested_amount}")
            return RunnerResult(success=False, data={"error": "invalid_amount"})

        target_label = "floresta" if donation_type == "resource" else "bem de luxo"
        self.log(
            jid,
            "info",
            f"Doacao unica: solicitado={requested_amount:,} | destino={target_label} | cidade={city_name} ({city_id})",
        )

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            before = fetch_donation_project_state(client, int(city_id), donation_type)
            if before["is_upgrading"]:
                self._schedule_modify_restore(
                    jid=jid,
                    finish_at_ts=int(before.get("finish_at_ts") or 0),
                    city_id=city_id,
                    city_name=city_name,
                    client=client,
                )
                self.log(
                    jid,
                    "warn",
                    (
                        f"Projeto em progresso: {target_label} | countdown={before['countdown_text'] or before['seconds_until_finish']} "
                        f"| proxima doacao manual bloqueada"
                    ),
                )
                return RunnerResult(
                    success=True,
                    data={
                        "city_id": city_id,
                        "city_name": city_name,
                        "island_id": before["island_id"],
                        "donation_type": donation_type,
                        "skipped": "upgrade_in_progress",
                        "finish_at_ts": before["finish_at_ts"],
                    },
                )

            baseline = fetch_worker_baseline(client, int(city_id))
            donation_amount = min(requested_amount, before["remaining"]) if before["remaining"] > 0 else requested_amount
            carry_over = max(0, requested_amount - donation_amount)
            if donation_amount <= 0:
                self.log(jid, "warn", f"Nada a doar: restante atual={before['remaining']:,}")
                return RunnerResult(success=True, data={"city_id": city_id, "amount": 0, "remaining_after": before["remaining"]})

            self.log(
                jid,
                "info",
                (
                    f"Postando doacao... restante_antes={before['remaining']:,} | "
                    f"solicitado={requested_amount:,} | efetivo={donation_amount:,}"
                ),
            )
            client.donate(
                island_id=before["island_id"],
                donation_type=donation_type,
                amount=donation_amount,
            )
            after = fetch_donation_project_state(client, int(city_id), donation_type)

            if after["is_upgrading"]:
                self._schedule_modify_restore(
                    jid=jid,
                    finish_at_ts=int(after.get("finish_at_ts") or 0),
                    city_id=city_id,
                    city_name=city_name,
                    client=client,
                    baseline=baseline,
                )

            if ga_id:
                self.save_game_client(ga_id, client)

            self.log(
                jid,
                "info",
                (
                    f"Doacao concluida: {donation_amount:,} para {target_label} | "
                    f"faltava_antes={before['remaining']:,} | falta_agora={after['remaining']:,} | sobra={carry_over:,}"
                ),
            )
            return RunnerResult(
                success=True,
                data={
                    "city_id": city_id,
                    "city_name": city_name,
                    "island_id": before["island_id"],
                    "donation_type": donation_type,
                    "requested_amount": requested_amount,
                    "amount": donation_amount,
                    "remaining_before": before["remaining"],
                    "remaining_after": after["remaining"],
                    "carry_over": carry_over,
                    "finish_at_ts": after["finish_at_ts"],
                },
            )
        except Exception as exc:
            logger.exception("DonateOnceRunner failed for job %s", jid)
            self.log(jid, "error", f"Erro: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

    def _schedule_modify_restore(
        self,
        *,
        jid: str,
        finish_at_ts: int,
        city_id: str,
        city_name: str,
        client,
        baseline: dict[str, Any] | None = None,
    ) -> str:
        if finish_at_ts <= 0:
            return ""
        if baseline is None:
            baseline = fetch_worker_baseline(client, int(city_id))
        gold = fetch_global_gold(client)
        if gold < 0:
            self.log(jid, "warn", f"Ouro negativo ({gold:,}); ajuste de producao nao agendado")
            return ""
        delay_seconds = max(60, int(finish_at_ts - time.time()) + RETRY_BUFFER_SECONDS)
        spawned = self.hub.spawn_job(
            jid,
            action_code=23,
            inputs=build_modify_production_inputs(city_id, city_name, baseline),
            delay_seconds=delay_seconds,
        )
        new_job_id = str(spawned.get("new_job_id") or "")
        if new_job_id:
            self.log(
                jid,
                "info",
                f"Ajuste de producao agendado: job={new_job_id} em {delay_seconds}s",
            )
        return new_job_id
