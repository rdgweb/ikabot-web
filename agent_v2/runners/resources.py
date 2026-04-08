"""
Resource-management runners.

Action codes:
     2  send_resources
    10  send_resources (legacy alias)
    22  collect_resources   (recurring)
    23  modify_production
    24  dump_warehouse
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from math import floor
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from services.island_donation import fetch_city_context, fetch_worker_baseline
from services.resource_transport import (
    RESOURCE_ORDER,
    change_current_city,
    confirm_arrival,
    estimate_next_ship_availability_seconds,
    prepare_transport,
    submit_transport,
)

logger = logging.getLogger(__name__)

COLLECT_INTERVAL = 15 * 60
MIN_DISTRIBUTE_RECHECK = 30 * 60
MAX_DISTRIBUTE_RECHECK = 12 * 3600
RESOURCE_KEYS = tuple(RESOURCE_ORDER)
RESOURCE_PRODUCED_MAP = {
    "wood": 0,
    "wine": 1,
    "marble": 2,
    "crystal": 3,
    "sulfur": 4,
}


def _to_int(raw: Any, default: int = 0, minimum: int | None = None) -> int:
    try:
        value = int(float(raw))
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _to_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "y", "yes", "sim", "on"}


def _as_city_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        return [c for c in raw.values() if isinstance(c, dict)]
    return []


def _resource_amount(city: dict[str, Any], resource: str) -> int:
    return _to_int(city.get(resource, 0), 0, 0)


def _resource_capacity(city: dict[str, Any], resource: str) -> int:
    current = city.get("current_resources") if isinstance(city.get("current_resources"), dict) else {}
    max_resources = city.get("max_resources") if isinstance(city.get("max_resources"), dict) else {}
    key_map = {"wood": "resource", "wine": "1", "marble": "2", "crystal": "3", "sulfur": "4"}
    key = key_map[resource]
    cap = _to_int(max_resources.get(key, 0), 0, 0)
    if cap > 0:
        return cap
    current_amount = _to_int(current.get(key, 0), 0, 0)
    warehouse_capacity = _to_int(city.get("warehouse_capacity", 0), 0, 0)
    return max(current_amount, warehouse_capacity)


def _resource_production_per_hour(city: dict[str, Any], resource: str) -> int:
    if resource == "wood":
        return _to_int(city.get("resource_production_per_hour", 0), 0, 0)
    tradegood = _to_int(city.get("produced_tradegood", city.get("tradegood", 0)), 0, 0)
    if tradegood == RESOURCE_PRODUCED_MAP[resource]:
        return _to_int(city.get("tradegood_production_per_hour", 0), 0, 0)
    return 0


def _resource_net_per_hour(city: dict[str, Any], resource: str) -> int:
    production = _resource_production_per_hour(city, resource)
    if resource == "wine":
        return production - _to_int(city.get("wine_consumption", 0), 0, 0)
    return production


def _city_name(city: dict[str, Any]) -> str:
    return str(city.get("name") or city.get("city_name") or city.get("id") or "?")


def _parse_snapshot_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


@register_runner(2)
@register_runner(10)
class SendResourcesRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}

        try:
            if str(inputs.get("monitor_mode") or "").strip().lower() == "arrival_check":
                return self._confirm_arrival(job, inputs)
            return self._dispatch_transport(job, inputs)
        except Exception as exc:
            self.log(jid, "error", f"Send resources failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

    def _dispatch_transport(self, job: dict[str, Any], inputs: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        recovery = inputs.get("__recovery") if isinstance(inputs.get("__recovery"), dict) else {}
        previous_progress = recovery.get("previous_progress") if isinstance(recovery.get("previous_progress"), dict) else {}
        previous_phase = str(previous_progress.get("phase") or "").strip().lower()
        previous_meta = previous_progress.get("metadata") if isinstance(previous_progress.get("metadata"), dict) else {}

        from_city = str(inputs.get("from_city") or "").strip()
        to_city = str(inputs.get("to_city") or "").strip()
        if not from_city or not to_city:
            return RunnerResult(success=False, data={"error": "missing_city"})
        if from_city == to_city:
            return RunnerResult(success=False, data={"error": "same_city"})

        requested = {name: max(0, int(inputs.get(name, 0) or 0)) for name in RESOURCE_ORDER}
        if sum(requested.values()) <= 0:
            return RunnerResult(success=False, data={"error": "empty_resources"})

        use_freighters = bool(inputs.get("use_freighters"))
        transport_load_percent = int(inputs.get("transport_load_percent", 100) or 100)
        confirm_arrival_enabled = bool(inputs.get("confirm_arrival", True))
        confirmation_margin_minutes = max(0, int(inputs.get("confirmation_margin_minutes", 2) or 0))

        if confirm_arrival_enabled and previous_phase == "dispatched":
            monitor_payload = previous_meta.get("monitor_payload") if isinstance(previous_meta.get("monitor_payload"), dict) else {}
            delay_seconds = _to_int(previous_meta.get("monitor_delay_seconds"), 0, 0)
            if monitor_payload and delay_seconds > 0:
                self.log(jid, "warn", "Recovery: transporte ja havia sido despachado; recriando apenas o monitor de chegada")
                self.hub.spawn_job(
                    jid,
                    action_code=2,
                    delay_seconds=delay_seconds,
                    inputs=monitor_payload,
                )
                return RunnerResult(success=True, data={"status": "recovered_monitor_only"})

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        client = self.get_or_login_game_client(jid, aid, ga_id, creds)
        plan = prepare_transport(
            client,
            from_city_id=from_city,
            to_city_id=to_city,
            requested=requested,
            use_freighters=use_freighters,
            capacity_percent=transport_load_percent,
        )

        self.log(
            jid,
            "info",
            (
                f"Plano de transporte: {plan.origin.city_name} -> {plan.destination.city_name} | "
                f"solicitado={plan.total_requested:,} | despachavel={plan.total_dispatched:,} | "
                f"navios_livres={plan.free_transporters} | capacidade/navio={plan.effective_ship_capacity} "
                f"({plan.capacity_percent}% de {plan.ship_capacity})"
            ),
        )
        self.log(
            jid,
            "info",
            (
                "ETA transporte: "
                f"fila={plan.eta['queue_seconds']}s | "
                f"carregamento={plan.eta['loading_seconds']}s | "
                f"viagem={plan.eta['travel_seconds']}s | "
                f"total={plan.eta['total_seconds']}s"
            ),
        )

        if plan.total_dispatched <= 0:
            wait_seconds = estimate_next_ship_availability_seconds(client)
            if wait_seconds > 0:
                self.log(jid, "warn", f"Sem capacidade imediata; reagendando em {wait_seconds}s")
                if ga_id:
                    self.save_game_client(ga_id, client)
                return RunnerResult(
                    success=True,
                    reschedule_seconds=wait_seconds,
                    reschedule_inputs=inputs,
                    data={"status": "waiting_ships"},
                )
            return RunnerResult(success=False, data={"error": "nothing_dispatchable"})

        response = submit_transport(client, plan, use_freighters=use_freighters)
        if not response["ok"]:
            feedback_text = " | ".join(str(entry.get("text") or entry.get("locakey") or "") for entry in response["feedbacks"])
            if not feedback_text and plan.total_dispatched > 0 and plan.total_requested > plan.total_dispatched:
                wait_seconds = estimate_next_ship_availability_seconds(client)
                if wait_seconds <= 0:
                    wait_seconds = max(60, plan.eta["queue_seconds"] + 30)
                next_inputs = dict(inputs)
                next_inputs.update(plan.remaining)
                self.log(
                    jid,
                    "warn",
                    (
                        "Submit sem feedback conclusivo; assumindo envio parcial por falta de capacidade. "
                        f"Despachavel={plan.total_dispatched:,} | restante={sum(plan.remaining.values()):,} | "
                        f"nova tentativa em {wait_seconds}s"
                    ),
                )
                if ga_id:
                    self.save_game_client(ga_id, client)
                return RunnerResult(
                    success=True,
                    reschedule_seconds=wait_seconds,
                    reschedule_inputs=next_inputs,
                    data={
                        "status": "partial_dispatch_assumed",
                        "dispatched": plan.dispatched,
                        "remaining": plan.remaining,
                        "eta": plan.eta,
                    },
                )

            self.log(
                jid,
                "error",
                f"Falha ao enviar transporte: {feedback_text or 'sem feedback do jogo'}",
            )
            return RunnerResult(
                success=False,
                data={"error": "transport_submit_failed", "feedback": feedback_text},
            )

        self.log(
            jid,
            "info",
            (
                f"Transporte enviado: {plan.origin.city_name} -> {plan.destination.city_name} | "
                f"despachado={plan.total_dispatched:,}"
            ),
        )

        monitor_payload = None
        monitor_delay = 0
        if confirm_arrival_enabled:
            monitor_delay = max(60, plan.eta["total_seconds"] + confirmation_margin_minutes * 60)
            monitor_payload = {
                "from_city": from_city,
                "from_city_name": plan.origin.city_name,
                "to_city": to_city,
                "to_city_name": plan.destination.city_name,
                "monitor_mode": "arrival_check",
                "baseline_destination": plan.destination.available_resources,
                "sent_resources": plan.dispatched,
                "sent_at_ts": int(time.time()),
                "probe_count": 0,
                "eta_queue_seconds": plan.eta["queue_seconds"],
                "eta_loading_seconds": plan.eta["loading_seconds"],
                "eta_travel_seconds": plan.eta["travel_seconds"],
                "eta_total_seconds": plan.eta["total_seconds"],
                "confirmation_margin_minutes": confirmation_margin_minutes,
            }
            self.checkpoint(
                jid,
                phase="dispatched",
                metadata={
                    "from_city": from_city,
                    "to_city": to_city,
                    "dispatched": plan.dispatched,
                    "monitor_delay_seconds": monitor_delay,
                    "monitor_payload": monitor_payload,
                },
            )
            self.hub.spawn_job(
                jid,
                action_code=2,
                delay_seconds=monitor_delay,
                inputs=monitor_payload,
            )
            self.log(
                jid,
                "info",
                (
                    "Monitor de chegada agendado: "
                    f"queue={plan.eta['queue_seconds']}s | "
                    f"loading={plan.eta['loading_seconds']}s | "
                    f"travel={plan.eta['travel_seconds']}s | "
                    f"check_em={monitor_delay}s"
                ),
            )

        remaining_total = sum(plan.remaining.values())
        if remaining_total > 0:
            wait_seconds = estimate_next_ship_availability_seconds(client)
            if wait_seconds <= 0:
                wait_seconds = max(60, plan.eta["queue_seconds"] + 30)
            next_inputs = dict(inputs)
            next_inputs.update(plan.remaining)
            self.log(jid, "warn", f"Restante pendente={remaining_total:,}; reagendando em {wait_seconds}s")
            if ga_id:
                self.save_game_client(ga_id, client)
            return RunnerResult(
                success=True,
                reschedule_seconds=wait_seconds,
                reschedule_inputs=next_inputs,
                data={
                    "status": "partial_dispatch",
                    "dispatched": plan.dispatched,
                    "remaining": plan.remaining,
                    "eta": plan.eta,
                },
            )

        if ga_id:
            self.save_game_client(ga_id, client)
        return RunnerResult(
            success=True,
            data={
                "status": "sent",
                "dispatched": plan.dispatched,
                "eta": plan.eta,
            },
        )

    def _confirm_arrival(self, job: dict[str, Any], inputs: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        to_city = str(inputs.get("to_city") or "").strip()
        baseline = inputs.get("baseline_destination") or {}
        sent = inputs.get("sent_resources") or {}
        probe_count = max(0, int(inputs.get("probe_count", 0) or 0))
        from_city_name = str(inputs.get("from_city_name") or inputs.get("from_city") or "?")
        to_city_name = str(inputs.get("to_city_name") or to_city or "?")

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})
        client = self.get_or_login_game_client(jid, aid, ga_id, creds)
        result = confirm_arrival(client, to_city_id=to_city, baseline=baseline, sent=sent)

        if result.get("pending"):
            delay = max(60, int(result.get("next_check_seconds", 60)))
            self.log(
                jid,
                "info",
                f"Transporte {from_city_name} -> {to_city_name} ainda em rota; novo check em {delay}s",
            )
            if ga_id:
                self.save_game_client(ga_id, client)
            next_inputs = dict(inputs)
            next_inputs["probe_count"] = probe_count + 1
            return RunnerResult(
                success=True,
                reschedule_seconds=delay,
                reschedule_inputs=next_inputs,
                data={"status": "arrival_pending"},
            )

        if result.get("confirmed"):
            self.log(
                jid,
                "info",
                (
                    f"Chegada confirmada: {from_city_name} -> {to_city_name} | "
                    f"modo={result.get('confirmation_mode')} | "
                    f"entregue_detectado={int(result.get('delivered_amount', 0)):,}/"
                    f"{int(result.get('sent_total', 0)):,}"
                ),
            )
            if ga_id:
                self.save_game_client(ga_id, client)
            return RunnerResult(success=True, data={"status": "arrival_confirmed", **result})

        if probe_count < 2:
            self.log(
                jid,
                "warn",
                (
                    f"Chegada inconclusiva: {from_city_name} -> {to_city_name} | "
                    "movimento nao esta mais ativo e o delta de estoque ainda nao confirmou"
                ),
            )
            if ga_id:
                self.save_game_client(ga_id, client)
            next_inputs = dict(inputs)
            next_inputs["probe_count"] = probe_count + 1
            return RunnerResult(
                success=True,
                reschedule_seconds=60,
                reschedule_inputs=next_inputs,
                data={"status": "arrival_inconclusive", **result},
            )

        self.log(
            jid,
            "warn",
            (
                f"Chegada confirmada de forma fraca: {from_city_name} -> {to_city_name} | "
                "movimento nao esta mais ativo"
            ),
        )
        if ga_id:
            self.save_game_client(ga_id, client)
        return RunnerResult(success=True, data={"status": "arrival_confirmed_weak", **result})


@register_runner(3)
@register_runner(1003)
class DistributeResourcesRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        selected_resources = [resource for resource in RESOURCE_KEYS if _to_bool(inputs.get(resource), False)]
        if not selected_resources:
            self.log(jid, "warn", "Nenhum recurso selecionado para distribuir")
            return RunnerResult(success=False, data={"error": "no_resources_selected"})

        selected_city_ids = [str(city_id) for city_id in (inputs.get("cities") or []) if str(city_id).strip()]
        if not selected_city_ids:
            self.log(jid, "warn", "Nenhuma cidade selecionada para distribuir")
            return RunnerResult(success=False, data={"error": "no_cities_selected"})

        strategy = str(inputs.get("strategy") or "targets").strip().lower()
        minimum_stock = _to_int(inputs.get("minimum_stock", 20000), 20000, 0)
        target_stock = max(minimum_stock, _to_int(inputs.get("target_stock", 60000), 60000, 0))
        origin_reserve_stock = _to_int(inputs.get("origin_reserve_stock", 50000), 50000, 0)
        useful_transfer_min = _to_int(inputs.get("useful_transfer_min", 5000), 5000, 1)
        shipment_cap = _to_int(inputs.get("shipment_cap", 50000), 50000, 1)
        max_routes_per_cycle = _to_int(inputs.get("max_routes_per_cycle", 5), 5, 1)
        transport_load_percent = str(inputs.get("transport_load_percent", "100") or "100")
        confirm_arrival_enabled = _to_bool(inputs.get("confirm_arrival"), True)
        confirmation_margin_minutes = _to_int(inputs.get("confirmation_margin_minutes", 2), 2, 0)
        loop_enabled = _to_bool(inputs.get("loop_enabled"), True)
        min_recheck_seconds = _to_int(inputs.get("min_recheck_minutes", 30), 30, 1) * 60
        max_recheck_seconds = _to_int(inputs.get("max_recheck_minutes", 720), 720, 5) * 60
        min_recheck_seconds = max(MIN_DISTRIBUTE_RECHECK, min_recheck_seconds)
        max_recheck_seconds = max(min_recheck_seconds, min(MAX_DISTRIBUTE_RECHECK, max_recheck_seconds))
        refresh_wait_seconds = min(300, min_recheck_seconds)

        snapshot = self._get_snapshot(jid, ga_id)
        if snapshot is None:
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=refresh_wait_seconds, data={"status": "waiting_snapshot"})

        snapshot_updated_at = _parse_snapshot_time(snapshot.get("updated_at"))
        if snapshot_updated_at is None or (datetime.now(timezone.utc) - snapshot_updated_at).total_seconds() > self.get_snapshot_stale_seconds():
            self.log(jid, "warn", "Snapshot antigo demais para distribuir com seguranca; atualizando status")
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=refresh_wait_seconds, data={"status": "stale_snapshot"})

        snapshot_cities = _as_city_list(snapshot.get("cities"))
        city_map = {str(city.get("id")): city for city in snapshot_cities if str(city.get("id") or "").strip()}
        planned_cities = [city_map[city_id] for city_id in selected_city_ids if city_id in city_map]
        if len(planned_cities) < 2:
            self.log(jid, "warn", "Distribuicao exige pelo menos duas cidades com snapshot valido")
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=refresh_wait_seconds, data={"status": "insufficient_cities"})

        allocations, planning = self._build_distribution_plan(
            planned_cities,
            selected_resources=selected_resources,
            strategy=strategy,
            minimum_stock=minimum_stock,
            target_stock=target_stock,
            origin_reserve_stock=origin_reserve_stock,
            useful_transfer_min=useful_transfer_min,
            shipment_cap=shipment_cap,
        )

        for line in planning["logs"]:
            self.log(jid, "info", line)

        route_entries = self._route_entries_from_allocations(
            allocations,
            planned_cities=planned_cities,
            useful_transfer_min=useful_transfer_min,
        )
        route_entries.sort(key=lambda item: (item["critical"], item["total"]), reverse=True)

        total_routes = len(route_entries)
        routes_to_spawn = route_entries[:max_routes_per_cycle]
        if total_routes > max_routes_per_cycle:
            self.log(
                jid,
                "warn",
                f"Plano gerou {total_routes} rotas; limitando a {max_routes_per_cycle} neste ciclo",
            )

        actions_spawned = 0
        for route in routes_to_spawn:
            child_inputs = {
                "from_city": route["from_city"],
                "from_city_name": route["from_city_name"],
                "to_city": route["to_city"],
                "to_city_name": route["to_city_name"],
                "transport_load_percent": transport_load_percent,
                "confirm_arrival": confirm_arrival_enabled,
                "confirmation_margin_minutes": confirmation_margin_minutes,
                **route["resources"],
            }
            self.hub.spawn_job(jid, action_code=2, inputs=child_inputs)
            actions_spawned += 1
            self.log(
                jid,
                "info",
                (
                    f"Remessa criada: {route['from_city_name']} -> {route['to_city_name']} | "
                    f"total={route['total']:,} | "
                    + ", ".join(f"{key}={value:,}" for key, value in route["resources"].items() if value > 0)
                ),
            )

        next_seconds = 0
        if loop_enabled:
            next_seconds = self._choose_next_distribution_check(
                planned_cities,
                selected_resources=selected_resources,
                minimum_stock=minimum_stock,
                actions_spawned=actions_spawned,
                min_recheck_seconds=min_recheck_seconds,
                max_recheck_seconds=max_recheck_seconds,
            )
            if total_routes > max_routes_per_cycle:
                next_seconds = min(next_seconds, min_recheck_seconds)
            self.log(jid, "info", f"Proxima avaliacao de distribuicao em {next_seconds}s")

        data = {
            "status": "ok",
            "actions_spawned": actions_spawned,
            "planned_routes": total_routes,
            "selected_resources": selected_resources,
        }
        return RunnerResult(
            success=True,
            reschedule_seconds=next_seconds if loop_enabled else 0,
            reschedule_inputs=inputs if loop_enabled else None,
            data=data,
        )

    def _build_distribution_plan(
        self,
        cities: list[dict[str, Any]],
        *,
        selected_resources: list[str],
        strategy: str,
        minimum_stock: int,
        target_stock: int,
        origin_reserve_stock: int,
        useful_transfer_min: int,
        shipment_cap: int,
    ) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
        allocations: dict[tuple[str, str], dict[str, Any]] = {}
        logs: list[str] = []

        for resource in selected_resources:
            deficits: dict[str, dict[str, Any]] = {}
            donors: dict[str, dict[str, Any]] = {}
            total_amount = 0
            total_need = 0
            total_available = 0

            for city in cities:
                city_id = str(city.get("id"))
                amount = _resource_amount(city, resource)
                total_amount += amount
                capacity = _resource_capacity(city, resource)
                effective_target = min(target_stock, capacity) if capacity > 0 else target_stock
                effective_minimum = min(minimum_stock, effective_target)
                need = max(0, effective_target - amount)
                critical_gap = max(0, effective_minimum - amount)
                reserve = min(origin_reserve_stock, amount)
                available = max(0, amount - reserve)
                if need > 0:
                    deficits[city_id] = {
                        "city": city,
                        "need": need,
                        "critical_gap": critical_gap,
                        "is_producer": _resource_production_per_hour(city, resource) > 0,
                    }
                    total_need += need
                if available >= useful_transfer_min:
                    donors[city_id] = {
                        "city": city,
                        "available": available,
                        "is_producer": _resource_production_per_hour(city, resource) > 0,
                    }
                    total_available += available

            logs.append(
                (
                    f"Planejamento {resource}: cidades={len(cities)} | estoque_total={total_amount:,} | "
                    f"necessidade_total={total_need:,} | disponivel_total={total_available:,}"
                )
            )

            if not deficits or not donors:
                continue

            recipient_order = sorted(
                deficits.values(),
                key=lambda item: (
                    item["critical_gap"] > 0,
                    not item["is_producer"] if strategy == "producer_to_non_producer" else False,
                    item["need"],
                ),
                reverse=True,
            )
            donor_order = sorted(
                donors.values(),
                key=lambda item: (
                    item["is_producer"] if strategy == "producer_to_non_producer" else False,
                    item["available"],
                ),
                reverse=True,
            )

            if strategy == "evenly":
                avg_stock = floor(total_amount / max(1, len(cities)))
                for recipient in recipient_order:
                    recipient["need"] = max(0, min(recipient["need"], avg_stock - _resource_amount(recipient["city"], resource)))
                recipient_order = [item for item in recipient_order if item["need"] > 0]

            for recipient in recipient_order:
                remaining = int(recipient["need"])
                if remaining <= 0:
                    continue

                for donor in donor_order:
                    if donor["city"]["id"] == recipient["city"]["id"]:
                        continue
                    available = int(donor["available"])
                    if available < useful_transfer_min and remaining >= useful_transfer_min:
                        continue
                    if available <= 0:
                        continue

                    route_key = (str(donor["city"]["id"]), str(recipient["city"]["id"]))
                    route_entry = allocations.setdefault(
                        route_key,
                        {
                            "from_city": str(donor["city"]["id"]),
                            "to_city": str(recipient["city"]["id"]),
                            "from_city_name": _city_name(donor["city"]),
                            "to_city_name": _city_name(recipient["city"]),
                            "resources": defaultdict(int),
                            "total": 0,
                            "critical": False,
                        },
                    )
                    route_remaining = shipment_cap - int(route_entry["total"])
                    if route_remaining <= 0:
                        continue

                    send_amount = min(remaining, available, route_remaining)
                    if send_amount <= 0:
                        continue

                    route_entry["resources"][resource] += int(send_amount)
                    route_entry["total"] += int(send_amount)
                    route_entry["critical"] = bool(route_entry["critical"] or recipient["critical_gap"] > 0)
                    donor["available"] -= int(send_amount)
                    remaining -= int(send_amount)

                    if remaining <= 0:
                        break

        return allocations, {"logs": logs}

    def _route_entries_from_allocations(
        self,
        allocations: dict[tuple[str, str], dict[str, Any]],
        *,
        planned_cities: list[dict[str, Any]],
        useful_transfer_min: int,
    ) -> list[dict[str, Any]]:
        route_entries: list[dict[str, Any]] = []
        known_city_ids = {str(city.get("id")) for city in planned_cities}
        for route in allocations.values():
            if route["from_city"] not in known_city_ids or route["to_city"] not in known_city_ids:
                continue
            total = int(route["total"])
            if total < useful_transfer_min and not route["critical"]:
                continue
            route_entries.append(
                {
                    **route,
                    "resources": {key: int(value) for key, value in route["resources"].items() if int(value) > 0},
                    "total": total,
                    "critical": bool(route["critical"]),
                }
            )
        return route_entries

    def _choose_next_distribution_check(
        self,
        cities: list[dict[str, Any]],
        *,
        selected_resources: list[str],
        minimum_stock: int,
        actions_spawned: int,
        min_recheck_seconds: int,
        max_recheck_seconds: int,
    ) -> int:
        candidates = [max_recheck_seconds]

        for city in cities:
            for resource in selected_resources:
                amount = _resource_amount(city, resource)
                net = _resource_net_per_hour(city, resource)
                if net >= 0 or amount <= minimum_stock:
                    continue
                seconds_to_minimum = int(max(0.0, ((amount - minimum_stock) / max(1.0, abs(net))) * 3600.0))
                if seconds_to_minimum <= 0:
                    candidates.append(min_recheck_seconds)
                else:
                    candidates.append(max(min_recheck_seconds, seconds_to_minimum - 3600))

        if actions_spawned > 0:
            candidates.append(min_recheck_seconds)

        chosen = min(candidates)
        return max(min_recheck_seconds, min(max_recheck_seconds, chosen))

    def _get_snapshot(self, job_id: str, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Falha ao buscar snapshot atual: {exc}")
            return None

    def _ensure_status_refresh(self, job_id: str) -> None:
        try:
            self.hub.spawn_job(job_id, action_code=100, inputs={})
            self.log(job_id, "info", "Check status solicitado para atualizar snapshot")
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel solicitar check_status: {exc}")


@register_runner(22)
class CollectResourcesRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]

        self.log(jid, "info", f"Collecting resources for account {aid}")
        try:
            client = self.get_game_session(aid)
            self.save_game_session(aid, client)
            self.log(jid, "info", "Resource collection complete")
            return RunnerResult(success=True, reschedule_seconds=COLLECT_INTERVAL)
        except Exception as exc:
            self.log(jid, "error", f"Collect resources failed: {exc}")
            return RunnerResult(
                success=False,
                reschedule_seconds=COLLECT_INTERVAL,
                data={"error": str(exc)},
            )


@register_runner(23)
class ModifyProductionRunner(BaseRunner):
    """Adjust sawmill and luxury worker allocation for one or more cities."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs", {})

        city_ids = self._resolve_city_ids(inputs)
        if not city_ids:
            self.log(jid, "error", "Nenhuma cidade informada para alterar producao")
            return RunnerResult(success=False, data={"error": "missing_city_id"})

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        sawmill_raw = str(inputs.get("sawmill_percent", "100")).strip()
        luxury_raw = str(inputs.get("luxury_percent", "100")).strip()

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            updated = []
            for city_id in city_ids:
                change_current_city(client, city_id)
                baseline = fetch_worker_baseline(client, int(city_id))
                context = fetch_city_context(client, int(city_id))

                resource_percent = self._resolve_target_percent(
                    sawmill_raw,
                    baseline["resource"]["current_workers"],
                    baseline["resource"]["max_workers"],
                )
                luxury_percent = self._resolve_target_percent(
                    luxury_raw,
                    baseline["tradegood"]["current_workers"],
                    baseline["tradegood"]["max_workers"],
                )

                resource_workers = self._target_workers(
                    baseline["resource"]["max_workers"], resource_percent
                )
                luxury_workers = self._target_workers(
                    baseline["tradegood"]["max_workers"], luxury_percent
                )

                self._set_workers(
                    client,
                    city_id=city_id,
                    island_id=context["island_id"],
                    resource_type="resource",
                    tradegood_type="resource",
                    workers=resource_workers,
                )
                self._set_workers(
                    client,
                    city_id=city_id,
                    island_id=context["island_id"],
                    resource_type="tradegood",
                    tradegood_type=context.get("tradegood_type") or "1",
                    workers=luxury_workers,
                )

                self.log(
                    jid,
                    "info",
                    (
                        f"Producao ajustada: {context['city_name']} | serraria={resource_percent}% "
                        f"({resource_workers}/{baseline['resource']['max_workers']}) | "
                        f"luxo={luxury_percent}% ({luxury_workers}/{baseline['tradegood']['max_workers']})"
                    ),
                )
                updated.append(
                    {
                        "city_id": city_id,
                        "city_name": context["city_name"],
                        "resource_percent": resource_percent,
                        "resource_workers": resource_workers,
                        "luxury_percent": luxury_percent,
                        "luxury_workers": luxury_workers,
                    }
                )

            if ga_id:
                self.save_game_client(ga_id, client)
            return RunnerResult(success=True, data={"cities": updated})
        except Exception as exc:
            self.log(jid, "error", f"Alterar producao falhou: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

    @staticmethod
    def _resolve_city_ids(inputs: dict[str, Any]) -> list[str]:
        if inputs.get("city_id"):
            return [str(inputs["city_id"])]
        if isinstance(inputs.get("cities"), list):
            return [str(city_id) for city_id in inputs["cities"]]
        return []

    @staticmethod
    def _target_workers(max_workers: int, percent: int) -> int:
        bounded = max(0, min(150, int(percent)))
        return int(max_workers * bounded / 100)

    @staticmethod
    def _resolve_target_percent(raw: str, current_workers: int, max_workers: int) -> int:
        mode = str(raw or "").strip().lower()
        if mode in {"preserve", "preserve_current", "current", "manter", "manter_atual"}:
            if max_workers <= 0:
                return 0
            return max(0, min(150, int(round((current_workers / max_workers) * 100))))
        try:
            return max(0, min(150, int(raw)))
        except Exception:
            return 100

    def _set_workers(
        self,
        client,
        *,
        city_id: str,
        island_id: str,
        resource_type: str,
        tradegood_type: str,
        workers: int,
    ) -> None:
        worker_key = "rw" if resource_type == "resource" else "tw"
        client._request(
            "POST",
            client._server_url,
            data={
                "action": "IslandScreen",
                "function": "workerPlan",
                "cityId": city_id,
                "islandId": island_id,
                "currentIslandId": island_id,
                "backgroundView": "island",
                "type": tradegood_type,
                "templateView": resource_type,
                worker_key: workers,
                "actionRequest": client._action_request,
                "ajax": "1",
            },
        )


@register_runner(24)
class DumpWarehouseRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]

        self.log(jid, "info", f"Dumping warehouse for account {aid}")
        try:
            client = self.get_game_session(aid)
            self.save_game_session(aid, client)
            self.log(jid, "info", "Warehouse dump complete")
            return RunnerResult(success=True)
        except Exception as exc:
            self.log(jid, "error", f"Dump warehouse failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
