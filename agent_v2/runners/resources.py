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

from game_client.constants import GAME_AJAX_HEADERS
from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from sessions.game_session_service import LoginCooldownActive
from services.island_donation import fetch_city_context
from services.resource_transport import (
    split_shipment,
    RESOURCE_ORDER,
    change_current_city,
    confirm_arrival,
    estimate_next_ship_availability,
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


def _normalize_reserved_map(raw: Any) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    if not isinstance(raw, dict):
        return out
    for city_id, bucket in raw.items():
        if not isinstance(bucket, dict):
            continue
        out[str(city_id).strip()] = {
            "wood": _to_int(bucket.get("wood"), 0, 0),
            "wine": _to_int(bucket.get("wine"), 0, 0),
            "marble": _to_int(bucket.get("marble"), 0, 0),
            "crystal": _to_int(bucket.get("crystal", bucket.get("glas")), 0, 0),
            "sulfur": _to_int(bucket.get("sulfur"), 0, 0),
        }
    return out


def _parse_snapshot_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def _positive_resource_signature(resources: dict[str, Any]) -> tuple[str, ...]:
    return tuple(name for name in RESOURCE_ORDER if _to_int(resources.get(name, 0), 0, 0) > 0)


def _seconds_until(raw: Any) -> int | None:
    target = _parse_snapshot_time(raw)
    if target is None:
        return None
    now = datetime.now(timezone.utc)
    delta = int((target - now).total_seconds())
    return max(0, delta)


def _entry_delay_seconds(entry: dict[str, Any]) -> int | None:
    scheduled_delay = _seconds_until(entry.get("scheduled_for"))
    if scheduled_delay is not None and scheduled_delay > 0:
        return scheduled_delay
    eta_delay = _to_int(entry.get("eta_total_seconds"), 0, 0)
    if eta_delay > 0:
        return eta_delay
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
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
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
        min_dispatch_total = max(0, int(inputs.get("min_dispatch_total", 0) or 0))

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

        ship_kind = "cargueiro" if use_freighters else "mercante"
        self.log(
            jid,
            "info",
            (
                f"Plano de transporte ({ship_kind}): {plan.origin.city_name} -> {plan.destination.city_name} | "
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

        if min_dispatch_total > 0 and 0 < plan.total_dispatched < min_dispatch_total:
            self.log(
                jid,
                "warn",
                (
                    f"Despachavel abaixo do minimo util; envio cancelado. "
                    f"Solicitado={plan.total_requested:,} | despachavel={plan.total_dispatched:,} | "
                    f"minimo={min_dispatch_total:,}"
                ),
            )
            if ga_id:
                self.save_game_client(ga_id, client)
            return RunnerResult(
                success=True,
                data={
                    "status": "dispatch_below_minimum",
                    "requested": plan.requested,
                    "remaining": plan.requested,
                    "minimum": min_dispatch_total,
                },
            )

        if plan.total_dispatched <= 0:
            wait_seconds = self._estimate_ship_wait_seconds(
                jid=jid,
                client=client,
                use_freighters=use_freighters,
            )
            if wait_seconds > 0:
                self.log(jid, "warn", f"Sem capacidade imediata; reagendando em {wait_seconds}s")
                self._retime_distribution_followup(jid=jid, job=job, delay_seconds=wait_seconds)
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
                wait_seconds = self._estimate_ship_wait_seconds(
                    jid=jid,
                    client=client,
                    use_freighters=use_freighters,
                )
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
                self._retime_distribution_followup(jid=jid, job=job, delay_seconds=wait_seconds)
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
                f"Transporte enviado ({ship_kind}): {plan.origin.city_name} -> {plan.destination.city_name} | "
                f"despachado={plan.total_dispatched:,}"
            ),
        )

        # Desconta navios no snapshot (delta atômico no hub)
        if ga_id and plan.total_dispatched > 0:
            from math import ceil
            ship_cap = plan.ship_capacity or (50000 if use_freighters else 500)
            ships_used = max(1, ceil(plan.total_dispatched / max(1, ship_cap)))
            try:
                if use_freighters:
                    self.hub.patch_snapshot_ships(ga_id, delta_freighters=-ships_used)
                    self.log(jid, "info", f"  ↳ {ships_used} cargueiro(s) reservado(s) no snapshot")
                else:
                    self.hub.patch_snapshot_ships(ga_id, delta_transporters=-ships_used)
                    self.log(jid, "info", f"  ↳ {ships_used} mercante(s) reservado(s) no snapshot")
            except Exception as _exc:
                self.log(jid, "warn", f"  ↳ Falha ao patch snapshot ships: {_exc}")

        if ga_id:
            origin_after_dispatch = {
                name: max(0, int(plan.origin.available_resources.get(name, 0)) - int(plan.dispatched.get(name, 0)))
                for name in RESOURCE_ORDER
            }
            self._patch_city_resources(
                jid,
                ga_id,
                from_city,
                origin_after_dispatch,
                reason=f"saida de transporte para {plan.destination.city_name}",
            )
            self._patch_city_resources(
                jid,
                ga_id,
                to_city,
                {},
                incoming_delta=plan.dispatched,
                reason=f"transporte em rota de {plan.origin.city_name}",
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
                "use_freighters": use_freighters,
            }
            internal_order_id = str(inputs.get("internal_order_id") or "").strip()
            if internal_order_id:
                monitor_payload["internal_order_id"] = internal_order_id
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
            self._retime_distribution_followup(jid=jid, job=job, delay_seconds=monitor_delay)
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
            wait_seconds = 0
            if monitor_delay > 0:
                wait_seconds = monitor_delay
                ship_kind = "cargueiro" if use_freighters else "mercante"
                self.log(
                    jid,
                    "info",
                    (
                        f"[ShipAvail:{ship_kind}] usando ETA conhecido da propria remessa "
                        f"(monitor agendado) = {wait_seconds}s"
                    ),
                )
            else:
                wait_seconds = self._estimate_ship_wait_seconds(
                    jid=jid,
                    client=client,
                    use_freighters=use_freighters,
                )
            if wait_seconds <= 0:
                wait_seconds = max(60, plan.eta["queue_seconds"] + 30)
            next_inputs = dict(inputs)
            next_inputs.update(plan.remaining)
            self.log(jid, "warn", f"Restante pendente={remaining_total:,}; reagendando em {wait_seconds}s")
            self._retime_distribution_followup(jid=jid, job=job, delay_seconds=wait_seconds)
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

    def _estimate_known_chain_ship_delay(
        self,
        *,
        job_id: str,
        use_freighters: bool,
    ) -> tuple[int, dict[str, Any]] | tuple[None, None]:
        try:
            payload = self.hub.get_transport_support(job_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel consultar ETAs da cadeia de transporte: {exc}")
            return None, None

        raw_entries = payload.get("entries") if isinstance(payload, dict) else []
        if not isinstance(raw_entries, list):
            return None, None

        candidates: list[tuple[int, dict[str, Any]]] = []
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("monitor_mode") or "").strip().lower() != "arrival_check":
                continue
            if bool(entry.get("use_freighters")) != bool(use_freighters):
                continue
            delay = _seconds_until(entry.get("scheduled_for"))
            if delay is None or delay <= 0:
                continue
            candidates.append((delay, entry))

        if not candidates:
            return None, None

        delay, chosen = min(candidates, key=lambda item: item[0])
        return max(60, int(delay)), chosen

    def _estimate_ship_wait_seconds(
        self,
        *,
        jid: str,
        client: Any,
        use_freighters: bool,
    ) -> int:
        known_delay, known_entry = self._estimate_known_chain_ship_delay(
            job_id=jid,
            use_freighters=use_freighters,
        )
        ship_kind = "cargueiro" if use_freighters else "mercante"
        if known_delay and known_entry:
            self.log(
                jid,
                "info",
                (
                    f"[ShipAvail:{ship_kind}] usando ETA conhecido da cadeia = {known_delay}s | "
                    f"origem={known_entry.get('from_city_name') or known_entry.get('from_city') or '?'} -> "
                    f"destino={known_entry.get('to_city_name') or known_entry.get('to_city') or '?'} | "
                    f"job={known_entry.get('job_id') or '?'}"
                ),
            )
            return known_delay

        availability = estimate_next_ship_availability(client, use_freighters=use_freighters)
        wait_seconds = int(availability.get("wait_seconds") or 0)
        if wait_seconds > 0:
            self._log_ship_availability_debug(jid, availability=availability, use_freighters=use_freighters)
        return wait_seconds

    def _log_ship_availability_debug(
        self,
        job_id: str,
        *,
        availability: dict[str, Any],
        use_freighters: bool,
    ) -> None:
        chosen = availability.get("chosen") if isinstance(availability.get("chosen"), dict) else {}
        entries = availability.get("entries") if isinstance(availability.get("entries"), list) else []
        fallback_used = bool(availability.get("fallback_used"))
        ship_kind = "cargueiro" if use_freighters else "mercante"
        if chosen:
            self.log(
                job_id,
                "info",
                (
                    f"[ShipAvail:{ship_kind}] wait={int(availability.get('wait_seconds') or 0)}s | "
                    f"fallback={'sim' if fallback_used else 'nao'} | "
                    f"escolhido mission={chosen.get('mission_text') or '?'} | "
                    f"returning={chosen.get('is_returning')} | "
                    f"origem={chosen.get('origin_city_name') or chosen.get('origin_city_id') or '?'} -> "
                    f"destino={chosen.get('target_city_name') or chosen.get('target_city_id') or '?'} | "
                    f"eta={int(chosen.get('eta_seconds') or 0)}s | "
                    f"fleet={chosen.get('fleet') or {}} | "
                    f"carga={int(chosen.get('resource_total') or 0):,}"
                ),
            )
        for idx, entry in enumerate(entries[:5], start=1):
            self.log(
                job_id,
                "info",
                (
                    f"[ShipAvail:{ship_kind}] cand#{idx} mission={entry.get('mission_text') or '?'} | "
                    f"returning={entry.get('is_returning')} | "
                    f"origem={entry.get('origin_city_name') or entry.get('origin_city_id') or '?'} -> "
                    f"destino={entry.get('target_city_name') or entry.get('target_city_id') or '?'} | "
                    f"eta={int(entry.get('eta_seconds') or 0)}s | "
                    f"fleet={entry.get('fleet') or {}} | "
                    f"carga={int(entry.get('resource_total') or 0):,}"
                ),
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
            self._retime_distribution_followup(jid=jid, job=job, delay_seconds=delay)
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
            if ga_id and isinstance(result.get("current"), dict):
                self._patch_city_resources(
                    jid,
                    ga_id,
                    to_city,
                    result["current"],
                    incoming_delta={name: -int(sent.get(name, 0) or 0) for name in RESOURCE_ORDER},
                    reason=f"chegada de transporte de {from_city_name}",
                )
            if sent:
                try:
                    self.hub.apply_construction_reservation_arrival(
                        job_id=str(job.get("root_job_id") or job.get("source_job_id") or jid),
                        city_id=to_city,
                        resources={name: int(sent.get(name, 0) or 0) for name in RESOURCE_ORDER if int(sent.get(name, 0) or 0) > 0},
                    )
                except Exception as exc:
                    self.log(jid, "warn", f"Nao foi possivel abater shortfall de construcao apos chegada: {exc}")
            # Libera navios no snapshot — chegada = barcos retornam (aprox)
            if ga_id:
                try:
                    used_freighters = bool(inputs.get("use_freighters"))
                    sent_total = sum(int(sent.get(n, 0) or 0) for n in RESOURCE_ORDER)
                    if sent_total > 0:
                        from math import ceil
                        cap = 50000 if used_freighters else 500
                        ships = max(1, ceil(sent_total / cap))
                        if used_freighters:
                            self.hub.patch_snapshot_ships(ga_id, delta_freighters=ships)
                            self.log(jid, "info", f"  ↳ {ships} cargueiro(s) liberado(s) no snapshot")
                        else:
                            self.hub.patch_snapshot_ships(ga_id, delta_transporters=ships)
                            self.log(jid, "info", f"  ↳ {ships} mercante(s) liberado(s) no snapshot")
                except Exception as _exc:
                    self.log(jid, "warn", f"  ↳ Falha ao liberar ships no snapshot: {_exc}")
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
            self._retime_distribution_followup(jid=jid, job=job, delay_seconds=60)
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
        if ga_id and isinstance(result.get("current"), dict):
            self._patch_city_resources(
                jid,
                ga_id,
                to_city,
                result["current"],
                incoming_delta={name: -int(sent.get(name, 0) or 0) for name in RESOURCE_ORDER},
                reason=f"chegada fraca de transporte de {from_city_name}",
            )
        if sent:
            try:
                self.hub.apply_construction_reservation_arrival(
                    job_id=str(job.get("root_job_id") or job.get("source_job_id") or jid),
                    city_id=to_city,
                    resources={name: int(sent.get(name, 0) or 0) for name in RESOURCE_ORDER if int(sent.get(name, 0) or 0) > 0},
                )
            except Exception as exc:
                self.log(jid, "warn", f"Nao foi possivel abater shortfall de construcao apos chegada fraca: {exc}")
        if ga_id:
            self.save_game_client(ga_id, client)
        return RunnerResult(success=True, data={"status": "arrival_confirmed_weak", **result})

    def _retime_distribution_followup(
        self,
        *,
        jid: str,
        job: dict[str, Any],
        delay_seconds: int,
    ) -> None:
        root_job_id = str(job.get("root_job_id") or "").strip()
        if not root_job_id:
            return
        try:
            resp = self.hub.retime_root_followup_job(
                root_job_id=root_job_id,
                action_code=3,
                delay_seconds=max(0, int(delay_seconds)),
                exclude_job_id=str(job.get("job_id") or ""),
            )
            if resp.get("updated"):
                self.log(jid, "info", f"Proximo 3 reagendado pela cadeia raiz em {delay_seconds}s.")
        except Exception as exc:
            self.log(jid, "warn", f"Nao foi possivel reagendar o proximo 3 pelo ETA do transporte: {exc}")

    def _patch_city_resources(
        self,
        job_id: str,
        game_account_id: str,
        city_id: str,
        resources: dict[str, int],
        *,
        incoming_delta: dict[str, int] | None = None,
        reason: str,
    ) -> None:
        try:
            clean = {
                name: max(0, int(resources.get(name, 0) or 0))
                for name in RESOURCE_ORDER
                if name in resources
            }
            clean_delta = {
                name: int((incoming_delta or {}).get(name, 0) or 0)
                for name in RESOURCE_ORDER
            }
            self.hub.patch_snapshot_resources(
                game_account_id,
                city_id,
                resources=clean,
                incoming_delta=clean_delta,
            )
            self.log(job_id, "debug", f"Snapshot de recursos atualizado: cidade={city_id} | motivo={reason}")
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel atualizar snapshot de recursos: {exc}")


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
        # strategy=evenly: ignore origin_reserve_stock — all surplus is available to send
        origin_reserve_stock = 0 if strategy == "evenly" else _to_int(inputs.get("origin_reserve_stock", 50000), 50000, 0)
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
            self._ensure_status_refresh(jid, ga_id)
            return RunnerResult(success=True, reschedule_seconds=refresh_wait_seconds, data={"status": "waiting_snapshot"})

        if self.is_snapshot_stale(snapshot):
            self.log(jid, "warn", "Snapshot antigo demais para distribuir com seguranca; atualizando status")
            self._ensure_status_refresh(jid, ga_id)
            return RunnerResult(success=True, reschedule_seconds=refresh_wait_seconds, data={"status": "stale_snapshot"})

        snapshot_cities = _as_city_list(snapshot.get("cities"))
        city_map = {str(city.get("id")): city for city in snapshot_cities if str(city.get("id") or "").strip()}
        planned_cities = [city_map[city_id] for city_id in selected_city_ids if city_id in city_map]
        if len(planned_cities) < 2:
            self.log(jid, "warn", "Distribuicao exige pelo menos duas cidades com snapshot valido")
            self._ensure_status_refresh(jid, ga_id)
            return RunnerResult(success=True, reschedule_seconds=refresh_wait_seconds, data={"status": "insufficient_cities"})

        reserved_by_city = self._get_construction_reservations(
            jid,
            ga_id,
            [str(city.get("id")) for city in planned_cities],
        )
        if reserved_by_city:
            touched = sum(1 for bucket in reserved_by_city.values() if any(int(v or 0) > 0 for v in bucket.values()))
            self.log(jid, "info", f"Reservas ativas de construcao detectadas em {touched} cidade(s); estoque reservado sera preservado")

        allocations, planning = self._build_distribution_plan(
            planned_cities,
            selected_resources=selected_resources,
            strategy=strategy,
            minimum_stock=minimum_stock,
            target_stock=target_stock,
            origin_reserve_stock=origin_reserve_stock,
            useful_transfer_min=useful_transfer_min,
            shipment_cap=shipment_cap,
            reserved_by_city=reserved_by_city,
        )

        for line in planning["logs"]:
            self.log(jid, "info", line)

        route_entries = self._route_entries_from_allocations(
            allocations,
            planned_cities=planned_cities,
            useful_transfer_min=useful_transfer_min,
        )
        route_entries.sort(key=lambda item: (item["critical"], item["total"]), reverse=True)
        active_transport_entries = self._get_active_transport_entries(jid)
        base_snap = (snapshot.get("base_snapshot") or {}) if isinstance(snapshot, dict) else {}
        free_freighters = int(base_snap.get("free_freighters") or 0)

        total_routes = len(route_entries)
        if total_routes > max_routes_per_cycle:
            self.log(
                jid,
                "warn",
                f"Plano gerou {total_routes} rotas; selecionando até {max_routes_per_cycle} viáveis neste ciclo",
            )

        actions_spawned = 0
        skipped_full = 0
        for route in route_entries:
            if actions_spawned >= max_routes_per_cycle:
                break

            dest_city = city_map.get(str(route["to_city"]))
            wh_cap = _to_int((dest_city or {}).get("warehouse_capacity"), 0) if dest_city else 0

            blocked_resources: list[str] = []
            viable_resources: dict[str, int] = {}
            for res, amount in route["resources"].items():
                if amount <= 0:
                    continue
                if wh_cap > 0:
                    current = _to_int((dest_city or {}).get(res), 0)
                    free = max(0, wh_cap - current)
                    if free < useful_transfer_min:
                        blocked_resources.append(f"{res} (livre={free:,})")
                        continue
                    viable_resources[res] = min(amount, free)
                else:
                    viable_resources[res] = amount

            if blocked_resources:
                self.log(
                    jid, "info",
                    f"Armazém cheio em {route['to_city_name']}: {', '.join(blocked_resources)} — tentando próxima rota",
                )
                skipped_full += 1

            if not viable_resources:
                continue

            # Cargueiros globais: split em N spawns (cargueiros + sobra mercantes)
            freighter_threshold = _to_int(inputs.get("freighter_threshold", 30000), 30000, 0)

            splits = split_shipment(
                viable_resources,
                threshold=freighter_threshold,
                free_freighters=free_freighters,
            )
            if not splits:
                splits = [(viable_resources, False)]

            for chunk, use_freighters in splits:
                if self._route_chunk_is_active(
                    active_transport_entries,
                    from_city=str(route["from_city"]),
                    to_city=str(route["to_city"]),
                    use_freighters=use_freighters,
                    resources=chunk,
                ):
                    self.log(
                        jid,
                        "info",
                        (
                            f"Rota ja coberta por transporte ativo: "
                            f"{route['from_city_name']} -> {route['to_city_name']} | "
                            f"modal={'cargueiro' if use_freighters else 'mercante'} | "
                            + ", ".join(f"{key}={value:,}" for key, value in chunk.items() if value > 0)
                        ),
                    )
                    continue
                child_inputs = {
                    "from_city": route["from_city"],
                    "from_city_name": route["from_city_name"],
                    "to_city": route["to_city"],
                    "to_city_name": route["to_city_name"],
                    "transport_load_percent": transport_load_percent,
                    "use_freighters": use_freighters,
                    "confirm_arrival": confirm_arrival_enabled,
                    "confirmation_margin_minutes": confirmation_margin_minutes,
                    **chunk,
                }
                self.hub.spawn_job(jid, action_code=2, inputs=child_inputs)
                active_transport_entries.append(
                    {
                        "from_city": str(route["from_city"]),
                        "to_city": str(route["to_city"]),
                        "use_freighters": bool(use_freighters),
                        "resources": {key: int(value) for key, value in chunk.items() if int(value) > 0},
                        "scheduled_for": "",
                    }
                )
                chunk_total = sum(chunk.values())
                if use_freighters:
                    n = max(1, chunk_total // 50000 + (1 if chunk_total % 50000 else 0))
                    free_freighters = max(0, free_freighters - n)
                    self.log(jid, "info",
                        f"  ↳ cargueiro × {n} ({chunk_total:,}) — cargueiros restantes: {free_freighters}")
                else:
                    n = max(1, chunk_total // 500 + (1 if chunk_total % 500 else 0))
                    self.log(jid, "info",
                        f"  ↳ mercante × {n} ({chunk_total:,})")
            actions_spawned += 1
            self.log(
                jid,
                "info",
                (
                    f"Remessa criada: {route['from_city_name']} -> {route['to_city_name']} | "
                    f"total={sum(viable_resources.values()):,} | "
                    + ", ".join(f"{key}={value:,}" for key, value in viable_resources.items() if value > 0)
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
            concrete_delay = self._choose_next_transport_followup_delay(active_transport_entries)
            if concrete_delay is not None:
                next_seconds = max(min_recheck_seconds, concrete_delay)
            elif total_routes > max_routes_per_cycle:
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
        reserved_by_city: dict[str, dict[str, int]] | None = None,
    ) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
        allocations: dict[tuple[str, str], dict[str, Any]] = {}
        logs: list[str] = []
        reserved_by_city = reserved_by_city or {}

        for resource in selected_resources:
            deficits: dict[str, dict[str, Any]] = {}
            donors: dict[str, dict[str, Any]] = {}
            total_amount = 0
            total_need = 0
            total_available = 0

            # Pre-compute totals for evenly
            if strategy == "evenly":
                total_amount = sum(
                    max(
                        0,
                        _resource_amount(c, resource)
                        - _to_int((reserved_by_city.get(str(c.get("id")) or {}) or {}).get(resource), 0, 0),
                    )
                    for c in cities
                )
                avg_stock = floor(total_amount / max(1, len(cities)))
                for city in cities:
                    city_id = str(city.get("id"))
                    reserved_amount = _to_int((reserved_by_city.get(city_id) or {}).get(resource), 0, 0)
                    amount = max(0, _resource_amount(city, resource) - reserved_amount)
                    is_producer = _resource_production_per_hour(city, resource) > 0
                    need = max(0, avg_stock - amount)
                    available = max(0, amount - avg_stock)
                    if need > 0:
                        deficits[city_id] = {
                            "city": city,
                            "need": need,
                            "critical_gap": 0,
                            "is_producer": is_producer,
                        }
                        total_need += need
                    if available >= useful_transfer_min:
                        donors[city_id] = {
                            "city": city,
                            "available": available,
                            "is_producer": is_producer,
                        }
                        total_available += available
            else:
                for city in cities:
                    city_id = str(city.get("id"))
                    reserved_amount = _to_int((reserved_by_city.get(city_id) or {}).get(resource), 0, 0)
                    amount = max(0, _resource_amount(city, resource) - reserved_amount)
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

                    route_key = (str(donor["city"]["id"]), str(recipient["city"]["id"]), resource)
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

    def _get_active_transport_entries(self, job_id: str) -> list[dict[str, Any]]:
        try:
            payload = self.hub.get_transport_support(job_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Falha ao buscar transportes ativos da cadeia: {exc}")
            return []
        raw_entries = payload.get("entries") if isinstance(payload, dict) else []
        entries: list[dict[str, Any]] = []
        if not isinstance(raw_entries, list):
            return entries
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            resources = raw.get("resources") if isinstance(raw.get("resources"), dict) else {}
            normalized = {
                "job_id": str(raw.get("job_id") or ""),
                "status": str(raw.get("status") or ""),
                "monitor_mode": str(raw.get("monitor_mode") or ""),
                "from_city": str(raw.get("from_city") or ""),
                "to_city": str(raw.get("to_city") or ""),
                "use_freighters": bool(raw.get("use_freighters")),
                "scheduled_for": str(raw.get("scheduled_for") or ""),
                "eta_total_seconds": _to_int(raw.get("eta_total_seconds"), 0, 0),
                "resources": {name: _to_int(resources.get(name), 0, 0) for name in RESOURCE_ORDER},
            }
            if normalized["from_city"] and normalized["to_city"] and any(normalized["resources"].values()):
                entries.append(normalized)
        return entries

    def _route_chunk_is_active(
        self,
        entries: list[dict[str, Any]],
        *,
        from_city: str,
        to_city: str,
        use_freighters: bool,
        resources: dict[str, int],
    ) -> bool:
        wanted_signature = _positive_resource_signature(resources)
        if not wanted_signature:
            return False
        for entry in entries:
            if str(entry.get("from_city") or "") != from_city:
                continue
            if str(entry.get("to_city") or "") != to_city:
                continue
            if bool(entry.get("use_freighters")) != bool(use_freighters):
                continue
            if _positive_resource_signature(entry.get("resources") or {}) != wanted_signature:
                continue
            return True
        return False

    def _choose_next_transport_followup_delay(self, entries: list[dict[str, Any]]) -> int | None:
        delays: list[int] = []
        for entry in entries:
            scheduled_delay = _seconds_until(entry.get("scheduled_for"))
            if scheduled_delay is not None and scheduled_delay > 0:
                delays.append(scheduled_delay)
                continue
            eta_delay = _to_int(entry.get("eta_total_seconds"), 0, 0)
            if eta_delay > 0:
                delays.append(eta_delay)
        if not delays:
            return None
        return min(delays)

    def _get_snapshot(self, job_id: str, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Falha ao buscar snapshot atual: {exc}")
            return None

    def _get_construction_reservations(
        self,
        job_id: str,
        game_account_id: str,
        city_ids: list[str],
    ) -> dict[str, dict[str, int]]:
        try:
            payload = self.hub.get_construction_reservations(
                game_account_id=game_account_id,
                city_ids=city_ids,
            )
        except Exception as exc:
            self.log(job_id, "warn", f"Falha ao buscar reservas de construcao: {exc}")
            return {}
        return _normalize_reserved_map((payload or {}).get("reservations"))

    def _ensure_status_refresh(self, job_id: str, game_account_id: str | None = None) -> None:
        try:
            self.ensure_status_refresh(job_id, game_account_id=game_account_id)
        except LoginCooldownActive:
            raise
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

        snapshot_city_map: dict[str, dict[str, Any]] = {}
        if ga_id:
            snapshot = self.get_snapshot(jid, ga_id)
            snapshot_cities = _as_city_list((snapshot or {}).get("cities"))
            snapshot_city_map = {
                str(city.get("id") or "").strip(): city
                for city in snapshot_cities
                if str(city.get("id") or "").strip()
            }

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
                # fetch_city_context for island_id and tradegood_type only
                context = fetch_city_context(client, int(city_id))
                island_id = context["island_id"]
                tradegood_type = context.get("tradegood_type") or "1"
                snapshot_city = snapshot_city_map.get(str(city_id).strip()) or {}
                snapshot_free_citizens = _to_int(snapshot_city.get("free_citizens"), 0, 0)

                # --- Serraria (resource) ---
                # Replicates ikabot: open the island resource view, read slider,
                # then immediately set workers while that view is still "active".
                resource_slider = self._open_view(
                    client,
                    city_id=city_id,
                    island_id=island_id,
                    resource_type="resource",
                    page_type="resource",
                )
                resource_percent = self._resolve_target_percent(
                    sawmill_raw,
                    resource_slider["current_workers"],
                    resource_slider["max_workers"],
                )
                requested_resource_workers = self._target_workers(
                    resource_slider["max_workers"], resource_percent
                )
                resource_cap_workers = resource_slider["current_workers"] + resource_slider["citizens"]
                resource_workers = min(requested_resource_workers, resource_cap_workers)
                resource_feedback = self._set_workers(
                    client,
                    city_id=city_id,
                    island_id=island_id,
                    resource_type="resource",
                    page_type="resource",
                    workers=resource_workers,
                )
                resource_after = self._open_view(
                    client,
                    city_id=city_id,
                    island_id=island_id,
                    resource_type="resource",
                    page_type="resource",
                )
                resource_applied_workers = resource_after["current_workers"]
                resource_partial = (
                    resource_applied_workers != resource_workers
                    and self._is_population_limited_feedback(resource_feedback)
                    and resource_applied_workers < resource_workers
                )
                if resource_applied_workers != resource_workers and not resource_partial:
                    raise RuntimeError(
                        f"serraria nao aplicada em {context['city_name']}: "
                        f"esperado={resource_workers} atual={resource_applied_workers} "
                        f"feedback={resource_feedback.get('feedback') or []} "
                        f"errors={resource_feedback.get('errors') or []}"
                    )

                # --- Bem de luxo (tradegood) ---
                tradegood_slider = self._open_view(
                    client,
                    city_id=city_id,
                    island_id=island_id,
                    resource_type="tradegood",
                    page_type=tradegood_type,
                )
                luxury_percent = self._resolve_target_percent(
                    luxury_raw,
                    tradegood_slider["current_workers"],
                    tradegood_slider["max_workers"],
                )
                requested_luxury_workers = self._target_workers(
                    tradegood_slider["max_workers"], luxury_percent
                )
                luxury_cap_workers = tradegood_slider["current_workers"] + tradegood_slider["citizens"]
                luxury_workers = min(requested_luxury_workers, luxury_cap_workers)
                luxury_feedback = self._set_workers(
                    client,
                    city_id=city_id,
                    island_id=island_id,
                    resource_type="tradegood",
                    page_type=tradegood_type,
                    workers=luxury_workers,
                )
                luxury_after = self._open_view(
                    client,
                    city_id=city_id,
                    island_id=island_id,
                    resource_type="tradegood",
                    page_type=tradegood_type,
                )
                luxury_applied_workers = luxury_after["current_workers"]
                luxury_partial = (
                    luxury_applied_workers != luxury_workers
                    and self._is_population_limited_feedback(luxury_feedback)
                    and luxury_applied_workers < luxury_workers
                )
                if luxury_applied_workers != luxury_workers and not luxury_partial:
                    raise RuntimeError(
                        f"luxo nao aplicado em {context['city_name']}: "
                        f"esperado={luxury_workers} atual={luxury_applied_workers} "
                        f"feedback={luxury_feedback.get('feedback') or []} "
                        f"errors={luxury_feedback.get('errors') or []}"
                    )

                if resource_partial:
                    self.log(
                        jid,
                        "warn",
                        (
                            f"Serraria limitada pelo jogo em {context['city_name']}: "
                            f"solicitado={resource_percent}% ({resource_workers}) | "
                            f"aplicado={self._workers_to_percent(resource_applied_workers, resource_slider['max_workers'])}% "
                            f"({resource_applied_workers}/{resource_slider['max_workers']})"
                        ),
                    )
                elif resource_workers < requested_resource_workers:
                    self.log(
                        jid,
                        "warn",
                        (
                            f"Serraria limitada antes do envio em {context['city_name']}: "
                            f"solicitado={resource_percent}% ({requested_resource_workers}) | "
                            f"max_viavel={resource_workers} "
                            f"(atuais={resource_slider['current_workers']} + livres={resource_slider['citizens']})"
                        ),
                    )
                if luxury_partial:
                    self.log(
                        jid,
                        "warn",
                        (
                            f"Luxo limitado pelo jogo em {context['city_name']}: "
                            f"solicitado={luxury_percent}% ({luxury_workers}) | "
                            f"aplicado={self._workers_to_percent(luxury_applied_workers, tradegood_slider['max_workers'])}% "
                            f"({luxury_applied_workers}/{tradegood_slider['max_workers']})"
                        ),
                    )
                elif luxury_workers < requested_luxury_workers:
                    self.log(
                        jid,
                        "warn",
                        (
                            f"Luxo limitado antes do envio em {context['city_name']}: "
                            f"solicitado={luxury_percent}% ({requested_luxury_workers}) | "
                            f"max_viavel={luxury_workers} "
                            f"(atuais={tradegood_slider['current_workers']} + livres={tradegood_slider['citizens']})"
                        ),
                    )

                self.log(
                    jid,
                    "info",
                    (
                        f"Producao ajustada: {context['city_name']} | serraria={resource_percent}% "
                        f"({resource_applied_workers}/{resource_slider['max_workers']}) | "
                        f"luxo={luxury_percent}% ({luxury_applied_workers}/{tradegood_slider['max_workers']}) | "
                        f"cidadaos={{resource:header={resource_slider['header_citizens']},template={resource_slider['template_citizens']},usado={resource_slider['citizens']}; "
                        f"luxo:header={tradegood_slider['header_citizens']},template={tradegood_slider['template_citizens']},usado={tradegood_slider['citizens']}}} | "
                        f"snapshot={snapshot_free_citizens}"
                    ),
                )
                updated.append(
                    {
                        "city_id": city_id,
                        "city_name": context["city_name"],
                        "resource_percent": resource_percent,
                        "resource_requested_workers": requested_resource_workers,
                        "resource_workers": resource_applied_workers,
                        "resource_applied_percent": self._workers_to_percent(
                            resource_applied_workers, resource_slider["max_workers"]
                        ),
                        "resource_partial": resource_partial,
                        "luxury_percent": luxury_percent,
                        "luxury_requested_workers": requested_luxury_workers,
                        "luxury_workers": luxury_applied_workers,
                        "luxury_applied_percent": self._workers_to_percent(
                            luxury_applied_workers, tradegood_slider["max_workers"]
                        ),
                        "luxury_partial": luxury_partial,
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

    @staticmethod
    def _workers_to_percent(workers: int, max_workers: int) -> int:
        if max_workers <= 0:
            return 0
        return max(0, min(150, int(round((workers / max_workers) * 100))))

    @staticmethod
    def _is_population_limited_feedback(feedback: dict[str, Any]) -> bool:
        texts = [str(item or "").strip().lower() for item in feedback.get("feedback") or []]
        return any("muitas pessoas vivendo" in text for text in texts)

    def _open_view(
        self,
        client,
        *,
        city_id: str,
        island_id: str,
        resource_type: str,
        page_type: str,
    ) -> dict[str, int]:
        """Open the island resource/tradegood view and return slider data.

        Replicates ikabot's fetch step: the game requires the island view to be
        opened immediately before the workerPlan call for the same resource type.
        """
        resp = client._request(
            "POST",
            client._server_url,
            data={
                "view": resource_type,
                "type": page_type,
                "islandId": island_id,
                "cityId": city_id,
                "backgroundView": "island",
                "currentIslandId": island_id,
                "actionRequest": client._action_request,
                "ajax": "1",
            },
            headers=GAME_AJAX_HEADERS,
        )
        payload = resp.json()
        self._update_action_request_from_payload(client, payload)
        template_data = payload[2][1]
        slider = template_data["js_ResourceSlider"]["slider"]
        callback = slider.get("callback_data") or {}
        header_data = {}
        if isinstance(payload, list) and len(payload) > 0:
            first = payload[0]
            if (
                isinstance(first, list)
                and len(first) > 1
                and isinstance(first[1], dict)
            ):
                header_data = first[1].get("headerData") or {}
        current_resources = header_data.get("currentResources") or {}
        max_workers = int(slider.get("max_value", callback.get("max_value", 0)) or 0)
        current_workers = int(slider.get("ini_value", callback.get("ini_workers", 0)) or 0)
        header_citizens = _to_int(current_resources.get("citizens"), 0, 0)
        template_citizens = _to_int(
            template_data.get("valueCitizens", callback.get("ini_citizens", callback.get("startCitizens", 0))),
            0,
            0,
        )
        citizens = _to_int(
            header_citizens or template_citizens,
            0,
            0,
        )
        return {
            "max_workers": max_workers,
            "current_workers": current_workers,
            "citizens": citizens,
            "header_citizens": header_citizens,
            "template_citizens": template_citizens,
        }

    def _set_workers(
        self,
        client,
        *,
        city_id: str,
        island_id: str,
        resource_type: str,
        page_type: str,
        workers: int,
    ) -> dict[str, Any]:
        worker_key = "rw" if resource_type == "resource" else "tw"
        resp = client._request(
            "POST",
            client._server_url,
            data={
                "action": "IslandScreen",
                "function": "workerPlan",
                "cityId": city_id,
                "islandId": island_id,
                "type": resource_type,
                "screen": resource_type,
                "backgroundView": "island",
                "currentCityId": city_id,
                "currentIslandId": island_id,
                "templateView": resource_type,
                "pageType": page_type,
                worker_key: workers,
                "actionRequest": client._action_request,
                "ajax": "1",
            },
            headers=GAME_AJAX_HEADERS,
        )
        payload = resp.json()
        self._update_action_request_from_payload(client, payload)
        return self._extract_worker_plan_feedback(payload)

    @staticmethod
    def _update_action_request_from_payload(client, payload: Any) -> None:
        if not isinstance(payload, list):
            return
        for item in payload:
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] != "updateGlobalData" or not isinstance(item[1], dict):
                continue
            token = str(item[1].get("actionRequest") or "").strip()
            if token:
                client._action_request = token
                return

    @staticmethod
    def _extract_worker_plan_feedback(payload: Any) -> dict[str, Any]:
        result: dict[str, Any] = {"feedback": [], "errors": []}
        if not isinstance(payload, list):
            return result
        for item in payload:
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] == "provideFeedback" and isinstance(item[1], list):
                for entry in item[1]:
                    if isinstance(entry, dict):
                        result["feedback"].append(str(entry.get("text") or "").strip())
            elif item[0] == "error" and item[1]:
                result["errors"].append(str(item[1]).strip())
        return result


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
