"""Generals Bank runners.

Action codes:
    806  generals_bank_manage  — orchestrate a full bank cycle (accumulation or liquidation)
    807  generals_bank_buy     — bank account wakes, buys from producers, re-enters vacation
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SEC = 60   # seconds between cycle status polls
_MAX_POLL_CYCLES = 180    # ~3h max before giving up
_BUY_WAIT_SEC = 90        # seconds to wait after producers list before buying (offer propagation)
_TRAIN_BUFFER_SEC = 90


def _to_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


@register_runner(806)
class GeneralsBankManageRunner(BaseRunner):
    """Orchestrate a full Generals Bank cycle.

    Inputs (set by hub when user clicks 'Iniciar Ciclo'):
        bank_config_id   — UUID of GeneralsBankConfig
        target_units     — dict {unit_id: quantity} for accumulation
        _cycle_id        — internal: UUID of in-progress cycle (set on reschedule)
        _poll_count      — internal: number of polls so far
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}

        bank_config_id = str(inputs.get("bank_config_id") or "").strip()
        target_units = inputs.get("target_units") or {}
        cycle_id = str(inputs.get("_cycle_id") or "").strip()
        poll_count = _to_int(inputs.get("_poll_count"), 0)

        if not bank_config_id:
            return RunnerResult(success=False, data={"error": "bank_config_id required"})

        # Fetch bank config from hub
        config_data = self.hub.bank_get_config(bank_config_id)
        if not config_data.get("ok"):
            self.log(jid, "error", f"Banco config nao encontrado: {bank_config_id}")
            return RunnerResult(success=False, data={"error": "config_not_found"})

        recommended_mode = config_data.get("recommended_mode", "accumulation")
        active_cycle_id = str(config_data.get("active_cycle_id") or "").strip()

        # Use existing cycle or create new one
        if cycle_id:
            effective_cycle_id = cycle_id
        elif active_cycle_id:
            effective_cycle_id = active_cycle_id
            self.log(jid, "info", f"Ciclo existente encontrado: {effective_cycle_id}")
        else:
            # Create new cycle
            self.log(jid, "info", f"Criando ciclo de {recommended_mode}...")
            create_result = self.hub.bank_create_cycle(
                bank_config_id=bank_config_id,
                mode=recommended_mode,
                target_units=target_units,
                manager_job_id=jid,
            )
            if not create_result.get("ok"):
                error = create_result.get("error", "unknown")
                if error == "active_cycle_exists":
                    effective_cycle_id = str(create_result.get("cycle_id") or "")
                    self.log(jid, "warn", f"Ciclo ativo encontrado: {effective_cycle_id}")
                else:
                    self.log(jid, "error", f"Falha ao criar ciclo: {error}")
                    return RunnerResult(success=False, data={"error": error})
            else:
                effective_cycle_id = str(create_result.get("cycle_id") or "")
                mode = create_result.get("mode", recommended_mode)
                tasks = create_result.get("tasks") or []
                self.log(jid, "info", f"Ciclo {mode} criado: {effective_cycle_id} com {len(tasks)} tarefa(s)")

        # Poll cycle status
        status_data = self.hub.bank_get_cycle_status(effective_cycle_id)
        if not status_data.get("ok"):
            self.log(jid, "error", "Nao foi possivel obter status do ciclo")
            return RunnerResult(success=False, data={"error": "cycle_status_failed"})

        cycle_status = status_data.get("status", "")
        cycle_mode = status_data.get("mode", "accumulation")
        is_terminal = status_data.get("is_terminal", False)

        self.log(jid, "info", f"Ciclo {effective_cycle_id}: {cycle_status} (poll #{poll_count})")
        self.checkpoint(jid, phase=cycle_status, metadata={"cycle_id": effective_cycle_id, "poll": poll_count})

        if is_terminal:
            success = cycle_status == "completed"
            self.log(jid, "info" if success else "error", f"Ciclo finalizado: {cycle_status}")
            return RunnerResult(success=success, data={"cycle_id": effective_cycle_id, "final_status": cycle_status})

        if cycle_status in ("sleeping", "bank_buying"):
            # Hub already created the buy job (807); just wait for completion
            if poll_count >= _MAX_POLL_CYCLES:
                self.log(jid, "error", "Timeout aguardando ciclo completar")
                return RunnerResult(success=False, data={"error": "poll_timeout"})
            return RunnerResult(
                success=True,
                reschedule_seconds=_POLL_INTERVAL_SEC,
                reschedule_inputs={**inputs, "_cycle_id": effective_cycle_id, "_poll_count": poll_count + 1},
            )

        if cycle_status in ("training", "consolidating", "listing"):
            # Still waiting for producer tasks — keep polling
            tasks = status_data.get("tasks") or []
            done = sum(1 for t in tasks if t["status"] in ("listed", "sold"))
            total = len(tasks)
            self.log(jid, "info", f"Tarefas: {done}/{total} listadas")

            if poll_count >= _MAX_POLL_CYCLES:
                self.log(jid, "error", "Timeout aguardando produtores listarem")
                return RunnerResult(success=False, data={"error": "producer_poll_timeout"})

            return RunnerResult(
                success=True,
                reschedule_seconds=_POLL_INTERVAL_SEC,
                reschedule_inputs={**inputs, "_cycle_id": effective_cycle_id, "_poll_count": poll_count + 1},
            )

        # Liquidation: bank_listing → hub already created buy jobs for buyers
        if cycle_status in ("bank_listing", "buyers_buying"):
            self.log(jid, "info", "Liquidação em andamento — aguardando compradores")
            if poll_count >= _MAX_POLL_CYCLES:
                return RunnerResult(success=False, data={"error": "liquidation_poll_timeout"})
            return RunnerResult(
                success=True,
                reschedule_seconds=_POLL_INTERVAL_SEC,
                reschedule_inputs={**inputs, "_cycle_id": effective_cycle_id, "_poll_count": poll_count + 1},
            )

        # Unknown/unexpected status — keep polling
        return RunnerResult(
            success=True,
            reschedule_seconds=_POLL_INTERVAL_SEC,
            reschedule_inputs={**inputs, "_cycle_id": effective_cycle_id, "_poll_count": poll_count + 1},
        )


@register_runner(809)
class GeneralsBankProducerTaskRunner(BaseRunner):
    """Execute one producer-side bank task end to end.

    Current supported path is the direct same-city flow:
    city has Barracks/Shipyard + Black Market in the same place.
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}

        cycle_id = str(inputs.get("cycle_id") or "").strip()
        task_id = str(inputs.get("task_id") or "").strip()
        if not cycle_id or not task_id:
            return RunnerResult(success=False, data={"error": "cycle_id and task_id required"})

        status_data = self.hub.bank_get_cycle_status(cycle_id)
        if not status_data.get("ok"):
            return RunnerResult(success=False, data={"error": "cycle_status_failed"})

        task = next((t for t in (status_data.get("tasks") or []) if str(t.get("task_id") or "") == task_id), None)
        if not task:
            return RunnerResult(success=False, data={"error": "task_not_found"})
        if str(task.get("status") or "") in {"listed", "sold", "failed", "cancelled"}:
            return RunnerResult(success=True, data={"task_status": task.get("status")})

        city_id = _to_int((inputs.get("city_id") or task.get("city_id") or task.get("bm_city_id")))
        bm_city_id = _to_int((inputs.get("bm_city_id") or task.get("bm_city_id")))
        unit_id = _to_int(task.get("unit_id"))
        quantity_target = _to_int(task.get("quantity_target"))
        if not city_id or not bm_city_id or not unit_id or not quantity_target:
            self.log(jid, "error", "Tarefa do banco incompleta: city/bm_city/unit/qty ausentes")
            return RunnerResult(success=False, data={"error": "incomplete_task"})
        if city_id != bm_city_id:
            self.log(jid, "error", f"Fluxo com transporte ainda nao suportado (city={city_id}, bm_city={bm_city_id})")
            return RunnerResult(success=False, data={"error": "transport_stage_not_supported"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            building_type = "fleet" if 200 <= unit_id < 300 else "troops"
            training_building = "shipyard" if building_type == "fleet" else "barracks"
            train_info = self._find_building_info(ga_id, city_id, training_building)
            bm_info = self._find_building_info(ga_id, bm_city_id, "blackMarket")
            if not train_info or not bm_info:
                missing = training_building if not train_info else "blackMarket"
                self.log(jid, "error", f"Edificio ausente para tarefa do banco: {missing}")
                return RunnerResult(success=False, data={"error": f"missing_{missing}"})

            train_position = int(train_info["position"])
            bm_position = int(bm_info["position"])

            barracks_state = client.fetch_barracks_state(city_id, train_position, building_type)
            unit_state = next((u for u in (barracks_state.get("units") or []) if _to_int(u.get("unit_id")) == unit_id), None)
            if not unit_state:
                self.log(jid, "error", f"Unidade {unit_id} nao encontrada no {training_building} da cidade {city_id}")
                return RunnerResult(success=False, data={"error": "unit_not_trainable"})

            unit_name = str(unit_state.get("name") or f"unit={unit_id}")
            current_count = _to_int(unit_state.get("current_count"))
            max_build = _to_int(unit_state.get("max_build"))
            train_time = max(0, _to_int(unit_state.get("train_time_seconds")))
            queue_entries = [q for q in (barracks_state.get("training_queue") or []) if _to_int(q.get("unit_id")) == unit_id]
            queued_count = sum(_to_int(q.get("quantity")) for q in queue_entries)
            queued_eta = max((_to_int(q.get("remaining_seconds")) for q in queue_entries), default=0)
            waiting_for_training = bool(inputs.get("_waiting_for_training"))
            expected_ready_at = max(_to_int(inputs.get("_expected_ready_at")), int(time.time()) + queued_eta if queued_eta > 0 else 0)

            self.log(
                jid,
                "info",
                f"Tarefa banco {task_id}: {unit_name} atual={current_count} fila={queued_count} alvo={quantity_target} cidade={city_id}",
            )

            available_total = current_count + queued_count
            if available_total < quantity_target:
                now_ts = int(time.time())
                if waiting_for_training and expected_ready_at > now_ts:
                    wait_seconds = max(30, expected_ready_at - now_ts)
                    self.log(jid, "info", f"Treino em curso para {unit_name}; rechecando em {wait_seconds}s")
                    self.save_game_client(ga_id, client)
                    return RunnerResult(
                        success=True,
                        reschedule_seconds=wait_seconds,
                        reschedule_inputs={**inputs, "city_id": city_id, "bm_city_id": bm_city_id},
                    )

                deficit = quantity_target - available_total
                train_now = min(deficit, max_build) if max_build > 0 else deficit
                if train_now <= 0:
                    self.log(jid, "error", f"{unit_name}: max_build=0 e ainda faltam {deficit}")
                    return RunnerResult(success=False, data={"error": "cannot_train_required_quantity"})

                client.train_units(city_id, train_position, {unit_id: train_now}, building_type)
                expected_wait = max(120, train_now * max(train_time, 1) + _TRAIN_BUFFER_SEC)
                self.log(
                    jid,
                    "info",
                    f"Treino iniciado para {train_now}x {unit_name}; proximo check em {expected_wait}s",
                )
                self.hub.bank_update_task(task_id, status="training", unit_name=unit_name, quantity_done=current_count)
                self.save_game_client(ga_id, client)
                return RunnerResult(
                    success=True,
                    reschedule_seconds=expected_wait,
                    reschedule_inputs={
                        **inputs,
                        "city_id": city_id,
                        "bm_city_id": bm_city_id,
                        "_waiting_for_training": True,
                        "_expected_ready_at": int(time.time()) + expected_wait,
                    },
                )

            if queued_eta > 0 and current_count < quantity_target:
                wait_seconds = max(30, queued_eta + _TRAIN_BUFFER_SEC)
                self.log(jid, "info", f"{unit_name}: lote ja esta na fila; aguardando {wait_seconds}s antes de listar")
                self.save_game_client(ga_id, client)
                return RunnerResult(
                    success=True,
                    reschedule_seconds=wait_seconds,
                    reschedule_inputs={
                        **inputs,
                        "city_id": city_id,
                        "bm_city_id": bm_city_id,
                        "_waiting_for_training": True,
                        "_expected_ready_at": int(time.time()) + wait_seconds,
                    },
                )

            bm_state = client.get_black_market_state(bm_city_id, bm_position, unit_id=unit_id, offer_resource=5)
            selected_unit = bm_state.get("selected_unit") or {}
            available_amount = _to_int(selected_unit.get("available_amount"))
            unit_price = _to_int(selected_unit.get("price_min"), 0)
            if available_amount < quantity_target:
                self.log(
                    jid,
                    "error",
                    f"{unit_name}: BM so permite listar {available_amount}/{quantity_target} agora",
                )
                return RunnerResult(success=False, data={"error": "insufficient_sellable_units"})
            if unit_price <= 0:
                self.log(jid, "error", f"{unit_name}: nao foi possivel obter price_min no BM")
                return RunnerResult(success=False, data={"error": "missing_unit_price"})

            client.add_black_market_offer(
                city_id=bm_city_id,
                position=bm_position,
                unit_id=unit_id,
                amount=quantity_target,
                unit_price=unit_price,
                offer_resource=5,
            )
            self.log(jid, "info", f"Oferta listada: {quantity_target}x {unit_name} por {unit_price} ouro/un")

            active_offers = client.get_my_black_market_offers(city_id=bm_city_id, position=bm_position)
            try:
                active_unit_ids = sorted({_to_int(o.get("unit_id")) for o in active_offers if _to_int(o.get("unit_id")) > 0})
                if active_unit_ids:
                    self.hub.sync_bm_offers(
                        game_account_id=ga_id,
                        city_id=bm_city_id,
                        active_unit_ids=active_unit_ids,
                        active_offers=[
                            {
                                "offer_id": _to_int(o.get("offer_id")),
                                "unit_id": _to_int(o.get("unit_id")),
                                "amount": _to_int(o.get("amount")),
                                "price": _to_int(o.get("price")),
                                "resource_name": str(o.get("resource_name") or ""),
                            }
                            for o in active_offers
                            if _to_int(o.get("unit_id")) > 0
                        ],
                    )
            except Exception as sync_exc:
                self.log(jid, "warn", f"Falha ao sincronizar BM da tarefa do banco: {sync_exc}")

            self.hub.bank_update_task(
                task_id,
                status="listed",
                quantity_done=quantity_target,
                unit_price=unit_price,
                unit_name=unit_name,
                sell_job_id=jid,
            )
            self.save_game_client(ga_id, client)
            return RunnerResult(
                success=True,
                data={"task_status": "listed", "unit_id": unit_id, "quantity": quantity_target, "unit_price": unit_price},
            )

        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
            self.log(jid, "error", f"Tarefa da produtora do banco falhou: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

    def _find_building_info(self, ga_id: str, city_id: int, building_type: str) -> dict[str, Any] | None:
        try:
            snapshot = self.hub.get_snapshot(game_account_id=ga_id)
            cities = snapshot.get("cities") or []
            for city in cities:
                if str(city.get("id")) == str(city_id):
                    for building in (city.get("buildings") or []):
                        if str(building.get("building") or "").strip() == building_type:
                            return {
                                "position": int(building.get("position", -1)),
                                "level": int(building.get("level") or 0),
                            }
        except Exception as exc:
            logger.debug("find_building_info failed: %s", exc)
        return None


@register_runner(807)
class GeneralsBankBuyRunner(BaseRunner):
    """Bank account wakes from vacation, buys listed units from producers, re-enters vacation.

    Inputs (set by hub when all producers have listed):
        cycle_id         — UUID of GeneralsBankCycle
        bank_config_id   — UUID of GeneralsBankConfig
        offers           — list of {producer_ga_id, unit_id, quantity, unit_price, bm_city_id, task_id}
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}

        cycle_id = str(inputs.get("cycle_id") or "").strip()
        bank_config_id = str(inputs.get("bank_config_id") or "").strip()
        offers = inputs.get("offers") or []

        if not cycle_id or not offers:
            return RunnerResult(success=False, data={"error": "cycle_id and offers required"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds, allow_cached=False)
            self.log(jid, "info", "Banco acordou — login ativo sai de ferias automaticamente apos 48h")

            # Fetch bank config for buyer_city_id
            config_data = self.hub.bank_get_config(bank_config_id)
            buyer_city_id = _to_int(inputs.get("buyer_city_id") or config_data.get("buyer_city_id"))
            auto_vacation = bool(config_data.get("auto_vacation", True))

            # Wait for offers to propagate on game servers
            self.log(jid, "info", f"Aguardando {_BUY_WAIT_SEC}s para ofertas propagarem...")
            time.sleep(_BUY_WAIT_SEC)

            purchases = []
            for offer in offers:
                producer_ga_id = str(offer.get("producer_ga_id") or "")
                unit_id = _to_int(offer.get("unit_id"))
                quantity = _to_int(offer.get("quantity"))
                unit_price = _to_int(offer.get("unit_price"))
                seller_city_id = _to_int(offer.get("bm_city_id"))
                task_id = str(offer.get("task_id") or "")

                if not unit_id or not quantity:
                    continue

                # Find Branch Office position for buyer city
                bo_info = self._find_building_info(ga_id, buyer_city_id, "branchOffice") if buyer_city_id else None
                if not bo_info:
                    self.log(jid, "warn", f"Branch Office nao encontrado em cidade {buyer_city_id}. Pulando oferta unit={unit_id}")
                    continue

                bo_position = int(bo_info["position"])

                # Determine unit category from unit_id range
                from game_client.actions.black_market import UNIT_TYPE_MARITIME, UNIT_TYPE_TERRESTRIAL
                if 200 <= unit_id < 300:
                    unit_category = UNIT_TYPE_MARITIME
                elif 300 <= unit_id < 400:
                    unit_category = UNIT_TYPE_TERRESTRIAL
                else:
                    unit_category = UNIT_TYPE_MARITIME

                import math
                num_transporters = max(1, -(-quantity // 5))
                self.log(
                    jid, "info",
                    f"Comprando {quantity}x unit={unit_id} de cidade={seller_city_id} "
                    f"por {unit_price}/un | {num_transporters} navios",
                )

                try:
                    # Get seller info from offer details
                    offer_details = client.get_unit_offer_details(
                        buyer_city_id=buyer_city_id,
                        bo_position=bo_position,
                        seller_city_id=seller_city_id,
                        unit_category=unit_category,
                    )
                    seller_avatar = str(offer_details.get("seller_avatar") or "")
                    seller_city_name = str(offer_details.get("seller_city_name") or "")
                    unit_offer = next((u for u in (offer_details.get("units") or []) if _to_int(u.get("unit_id")) == unit_id), None)
                    offer_key = _to_int((unit_offer or {}).get("offer_key"))

                    client.buy_units_black_market(
                        buyer_city_id=buyer_city_id,
                        bo_position=bo_position,
                        seller_city_id=seller_city_id,
                        seller_avatar=seller_avatar,
                        seller_city_name=seller_city_name,
                        unit_id=unit_id,
                        quantity=quantity,
                        unit_price=unit_price,
                        unit_category=unit_category,
                        num_transporters=num_transporters,
                        offer_key=offer_key,
                    )

                    self.log(jid, "info", f"Compra OK: {quantity}x unit={unit_id} de {seller_city_name or seller_city_id}")
                    purchases.append({
                        "producer_ga_id": producer_ga_id,
                        "unit_id": unit_id,
                        "unit_name": "",
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "job_id": jid,
                    })

                    # Update task status
                    if task_id:
                        self.hub.bank_update_task(task_id, status="sold", quantity_done=quantity)

                except Exception as buy_exc:
                    self.log(jid, "warn", f"Falha ao comprar unit={unit_id} de {seller_city_id}: {buy_exc}")

            # Report purchases to hub
            if purchases:
                self.hub.bank_buy_complete(cycle_id, purchases)
                self.log(jid, "info", f"Banco comprou de {len(purchases)} oferta(s)")
            else:
                self.log(jid, "warn", "Nenhuma compra realizada")
                self.hub.bank_cycle_complete(cycle_id)
                return RunnerResult(success=False, data={"error": "no_purchases_made"})

            self.save_game_client(ga_id, client)

            # Re-enter vacation mode if configured
            if auto_vacation:
                try:
                    self.log(jid, "info", "Entrando em modo ferias...")
                    snap = self.hub.get_snapshot(game_account_id=ga_id)
                    cities = snap.get("cities") or []
                    first_city_id = None
                    if isinstance(cities, list) and cities:
                        first_city_id = _to_int(cities[0].get("id") if isinstance(cities[0], dict) else 0)
                    elif isinstance(cities, dict) and cities:
                        first_city_id = _to_int(next(iter(cities.values()), {}).get("id", 0))
                    if first_city_id:
                        client.activate_vacation_mode(city_id=first_city_id)
                        self.log(jid, "info", "Banco voltou ao modo ferias")
                    else:
                        self.log(jid, "warn", "Nao foi possivel determinar city_id para entrar em ferias")
                except Exception as vac_exc:
                    self.log(jid, "warn", f"Falha ao entrar em ferias (nao critico): {vac_exc}")

            self.hub.bank_cycle_complete(cycle_id)
            return RunnerResult(
                success=True,
                data={"cycle_id": cycle_id, "purchases": len(purchases)},
            )

        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
            self.log(jid, "error", f"Banco buy runner falhou: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

    def _find_building_info(self, ga_id: str, city_id: int, building_type: str) -> dict[str, Any] | None:
        try:
            snapshot = self.hub.get_snapshot(game_account_id=ga_id)
            cities = snapshot.get("cities") or []
            for city in cities:
                if str(city.get("id")) == str(city_id):
                    for building in (city.get("buildings") or []):
                        if str(building.get("building") or "").strip() == building_type:
                            return {
                                "position": int(building.get("position", -1)),
                                "level": int(building.get("level") or 0),
                            }
        except Exception as exc:
            logger.debug("find_building_info failed: %s", exc)
        return None
