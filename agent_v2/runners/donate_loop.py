"""Runner: donation loop executed as a single cycle plus reschedule."""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from services.donation_automation import (
    RETRY_BUFFER_SECONDS,
    build_modify_production_inputs,
    compute_due_delay,
)
from services.island_donation import (
    clean_number,
    extract_city_data,
    fetch_city_context,
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


@register_runner(1006)
@register_runner(902)
class DonateLoopRunner(BaseRunner):
    """Run one donation cycle and schedule the next one."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}

        city_id = str(inputs.get("city_id", "")).strip()
        city_name = str(inputs.get("city_name") or city_id).strip() or city_id
        donation_type = DONATION_TYPE_MAP.get(str(inputs.get("donation_type", "")).strip().lower())
        method = self._to_int(inputs.get("donation_method"), 3)
        method_value = self._to_int(inputs.get("method_value"), 1)
        interval_minutes = max(1, self._to_int(inputs.get("interval_minutes"), 24 * 60))
        random_wait_minutes = max(0, self._to_int(inputs.get("random_wait_minutes"), 60))
        carry_over_amount = max(0, self._to_int(inputs.get("carry_over_amount"), 0))
        target_level = max(0, self._to_int(inputs.get("target_level"), 0))

        if not city_id:
            self.log(jid, "error", "city_id nao informado")
            return RunnerResult(success=False, data={"error": "missing_city_id"})
        if not donation_type:
            self.log(jid, "error", f"donation_type invalido: {inputs.get('donation_type')!r}")
            return RunnerResult(success=False, data={"error": "invalid_donation_type"})
        if method not in (1, 2, 3):
            self.log(jid, "error", f"donation_method invalido: {method}")
            return RunnerResult(success=False, data={"error": "invalid_donation_method"})
        if method_value <= 0:
            self.log(jid, "error", f"method_value invalido: {method_value}")
            return RunnerResult(success=False, data={"error": "invalid_method_value"})

        target_label = "floresta" if donation_type == "resource" else "bem de luxo"
        base_delay = interval_minutes * 60
        if random_wait_minutes > 0:
            base_delay += random.randint(0, random_wait_minutes * 60)

        self.log(
            jid,
            "info",
            (
                f"Loop de doacao: {city_name} ({city_id}) | destino={target_label} "
                f"| metodo={method} | valor={method_value} | carry_over={carry_over_amount:,} "
                f"| intervalo={interval_minutes}min | jitter_max={random_wait_minutes}min"
            ),
        )

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            before = fetch_donation_project_state(client, int(city_id), donation_type)
            next_inputs = self._next_inputs(inputs, carry_over_amount)

            if target_level > 0 and self._target_level_reached(before, target_level):
                scheduled_job_id = self._schedule_modify_restore(
                    jid=jid,
                    current_inputs=inputs,
                    finish_at_ts=int(before.get("finish_at_ts") or 0),
                    city_id=city_id,
                    city_name=city_name,
                    client=client,
                )
                self.log(
                    jid,
                    "info",
                    f"Loop encerrado: alvo de nivel atingido | atual={before['level']} | proximo={before['next_level']} | alvo={target_level}",
                )
                return RunnerResult(
                    success=True,
                    data={
                        "city_id": city_id,
                        "city_name": city_name,
                        "island_id": before["island_id"],
                        "target_level": target_level,
                        "stopped": "target_level_reached",
                        "modify_job_id": scheduled_job_id,
                    },
                )

            if before["is_upgrading"]:
                scheduled_job_id = self._schedule_modify_restore(
                    jid=jid,
                    current_inputs=inputs,
                    finish_at_ts=int(before.get("finish_at_ts") or 0),
                    city_id=city_id,
                    city_name=city_name,
                    client=client,
                )
                if scheduled_job_id:
                    next_inputs["modify_restore_finish_at_ts"] = int(before.get("finish_at_ts") or 0)
                next_delay = compute_due_delay(base_delay, before)
                self.log(
                    jid,
                    "info",
                    (
                        f"Ciclo adiado: {target_label} em progresso | countdown={before['countdown_text'] or before['seconds_until_finish']} "
                        f"| proximo_em={next_delay}s"
                    ),
                )
                return RunnerResult(
                    success=True,
                    reschedule_seconds=next_delay,
                    reschedule_inputs=next_inputs,
                    data={
                        "city_id": city_id,
                        "city_name": city_name,
                        "island_id": before["island_id"],
                        "skipped": "upgrade_in_progress",
                        "finish_at_ts": before["finish_at_ts"],
                        "modify_job_id": scheduled_job_id,
                    },
                )

            baseline = fetch_worker_baseline(client, int(city_id))
            city_context = fetch_city_context(client, int(city_id))
            computed_amount = self._compute_donation_amount(
                city_context=city_context,
                before_state=before,
                method=method,
                method_value=method_value,
                interval_minutes=interval_minutes,
            )
            planned_amount = computed_amount + carry_over_amount
            donation_amount = min(planned_amount, before["remaining"]) if before["remaining"] > 0 else planned_amount
            next_carry_over = max(0, planned_amount - donation_amount)

            if donation_amount <= 0:
                next_delay = max(60, base_delay)
                self.log(
                    jid,
                    "info",
                    f"Ciclo sem doacao: cidade={city_name} | destino={target_label} | motivo=sem_excedente",
                )
                if ga_id:
                    self.save_game_client(ga_id, client)
                return RunnerResult(
                    success=True,
                    reschedule_seconds=next_delay,
                    reschedule_inputs=self._next_inputs(inputs, next_carry_over),
                    data={
                        "city_id": city_id,
                        "city_name": city_name,
                        "island_id": before["island_id"],
                        "amount": 0,
                        "skipped": True,
                    },
                )

            self.log(
                jid,
                "info",
                (
                    f"Postando doacao: planejado={planned_amount:,} | efetivo={donation_amount:,} | "
                    f"faltava_antes={before['remaining']:,} | sobra_proximo={next_carry_over:,}"
                ),
            )
            client.donate(island_id=before["island_id"], donation_type=donation_type, amount=donation_amount)
            after = fetch_donation_project_state(client, int(city_id), donation_type)

            scheduled_job_id = ""
            if after["is_upgrading"]:
                scheduled_job_id = self._schedule_modify_restore(
                    jid=jid,
                    current_inputs=inputs,
                    finish_at_ts=int(after.get("finish_at_ts") or 0),
                    city_id=city_id,
                    city_name=city_name,
                    client=client,
                    baseline=baseline,
                )

            if target_level > 0 and self._target_level_reached(after, target_level):
                if ga_id:
                    self.save_game_client(ga_id, client)
                self.log(
                    jid,
                    "info",
                    (
                        f"Loop encerrado: alvo de nivel atingido | atual={after['level']} | "
                        f"proximo={after['next_level']} | alvo={target_level}"
                    ),
                )
                return RunnerResult(
                    success=True,
                    data={
                        "city_id": city_id,
                        "city_name": city_name,
                        "island_id": before["island_id"],
                        "donation_type": donation_type,
                        "planned_amount": planned_amount,
                        "amount": donation_amount,
                        "remaining_before": before["remaining"],
                        "remaining_after": after["remaining"],
                        "carry_over": next_carry_over,
                        "finish_at_ts": after["finish_at_ts"],
                        "modify_job_id": scheduled_job_id,
                        "target_level": target_level,
                        "stopped": "target_level_reached",
                    },
                )

            next_delay = compute_due_delay(base_delay, after)
            next_inputs = self._next_inputs(inputs, next_carry_over)
            if scheduled_job_id:
                next_inputs["modify_restore_finish_at_ts"] = int(after.get("finish_at_ts") or 0)
            if ga_id:
                self.save_game_client(ga_id, client)

            self.log(
                jid,
                "info",
                (
                    f"Ciclo concluido: cidade={city_name} | destino={target_label} | doado={donation_amount:,} "
                    f"| falta_agora={after['remaining']:,} | carry_over={next_carry_over:,} | proximo_em={next_delay}s"
                ),
            )
            return RunnerResult(
                success=True,
                reschedule_seconds=next_delay,
                reschedule_inputs=next_inputs,
                data={
                    "city_id": city_id,
                    "city_name": city_name,
                    "island_id": before["island_id"],
                    "donation_type": donation_type,
                    "planned_amount": planned_amount,
                    "amount": donation_amount,
                    "remaining_before": before["remaining"],
                    "remaining_after": after["remaining"],
                    "carry_over": next_carry_over,
                    "finish_at_ts": after["finish_at_ts"],
                    "modify_job_id": scheduled_job_id,
                },
            )
        except Exception as exc:
            logger.exception("DonateLoopRunner failed for job %s", jid)
            retry_delay = 60
            self.log(jid, "error", f"Erro no ciclo: {exc} | retry_em={retry_delay}s")
            return RunnerResult(
                success=False,
                reschedule_seconds=retry_delay,
                reschedule_inputs=self._next_inputs(inputs, carry_over_amount),
                data={"error": str(exc), "retry_delay": retry_delay},
            )

    @staticmethod
    def _to_int(raw: Any, default: int) -> int:
        try:
            return int(str(raw).strip())
        except Exception:
            return default

    @staticmethod
    def _next_inputs(inputs: dict[str, Any], carry_over_amount: int) -> dict[str, Any]:
        next_inputs = dict(inputs)
        next_inputs["carry_over_amount"] = max(0, int(carry_over_amount))
        return next_inputs

    def _schedule_modify_restore(
        self,
        *,
        jid: str,
        current_inputs: dict[str, Any],
        finish_at_ts: int,
        city_id: str,
        city_name: str,
        client,
        baseline: dict[str, Any] | None = None,
    ) -> str:
        if finish_at_ts <= 0:
            return ""
        already_scheduled_for = self._to_int(current_inputs.get("modify_restore_finish_at_ts"), 0)
        if already_scheduled_for == finish_at_ts:
            return ""
        if baseline is None:
            baseline = fetch_worker_baseline(client, int(city_id))
        gold = fetch_global_gold(client)
        if gold < 0:
            self.log(jid, "warn", f"Ouro negativo ({gold:,}); ajuste de producao nao agendado")
            return ""
        modify_delay = max(60, int(finish_at_ts - time.time()) + RETRY_BUFFER_SECONDS)
        spawned = self.hub.spawn_job(
            jid,
            action_code=23,
            inputs=build_modify_production_inputs(
                city_id,
                city_name,
                baseline,
                restore_mode=str(current_inputs.get("post_production_mode") or "preserve"),
                custom_sawmill_percent=current_inputs.get("post_sawmill_percent"),
                custom_luxury_percent=current_inputs.get("post_luxury_percent"),
            ),
            delay_seconds=modify_delay,
        )
        new_job_id = str(spawned.get("new_job_id") or "")
        if new_job_id:
            self.log(
                jid,
                "info",
                f"Ajuste de producao agendado: job={new_job_id} em {modify_delay}s",
            )
        return new_job_id

    def _compute_donation_amount(
        self,
        *,
        city_context: dict[str, Any],
        before_state: dict[str, Any],
        method: int,
        method_value: int,
        interval_minutes: int,
    ) -> int:
        if method == 3:
            return max(0, method_value)

        if method == 1:
            city_resp = self._fetch_city_resources(before_state["city_id"])
            wood = city_resp["wood"]
            storage_capacity = city_resp["storage_capacity"]
            keep_percent = max(0, min(100, method_value))
            max_wood = int(storage_capacity * (keep_percent / 100.0))
            return max(0, wood - max_wood)

        city_resp = self._fetch_city_resources(before_state["city_id"])
        production_percent = max(0, min(100, method_value))
        wood_prod_h = int(city_resp.get("wood_production_h") or 0)
        return max(0, int((wood_prod_h * production_percent / 100.0) * (interval_minutes / 60.0)))

    @staticmethod
    def _target_level_reached(project_state: dict[str, Any], target_level: int) -> bool:
        if target_level <= 0:
            return False
        current_level = int(project_state.get("level") or 0)
        next_level = int(project_state.get("next_level") or 0)
        if current_level >= target_level:
            return True
        return bool(project_state.get("is_upgrading")) and next_level >= target_level

    def _fetch_city_resources(self, city_id: str) -> dict[str, int]:
        client = getattr(self, "_current_client", None)
        if client is None:
            raise RuntimeError("client_not_bound")
        resp = client._request("GET", client._server_url, params={"view": "city", "cityId": int(city_id)})
        html = resp.text
        city = extract_city_data(html)
        resource_match = re.search(
            r'\\"resource\\":(\d+),\\"2\\":(\d+),\\"1\\":(\d+),\\"4\\":(\d+),\\"3\\":(\d+)}',
            html,
        )
        storage_match = re.search(r"maxResources:\s*JSON\.parse\('{\\\"resource\\\":(\d+),", html)
        production_match = re.search(r'resourceProduction:\s*([0-9.]+)', html)
        if not resource_match or not storage_match:
            raise ValueError(f"city_resources_not_found:{city_id}")
        return {
            "wood": clean_number(resource_match.group(1)),
            "storage_capacity": clean_number(storage_match.group(1)),
            "wood_production_h": int(float(production_match.group(1)) * 3600) if production_match else 0,
            "island_id": clean_number(city.get("islandId")),
        }

    def get_or_login_game_client(self, job_id: str, account_id: str, game_account_id: str | None, creds: dict[str, Any], *, allow_cached: bool = True):
        client = super().get_or_login_game_client(job_id, account_id, game_account_id, creds, allow_cached=allow_cached)
        self._current_client = client
        return client
