"""Black Market runners — sell and buy military units.

Action codes:
    803  black_market_sell   — list units for sale via Black Market building
    804  black_market_buy    — buy units from another player's Black Market offer
"""

from __future__ import annotations

import logging
from typing import Any

from game_client.actions.black_market import UNIT_TYPE_MARITIME, UNIT_TYPE_TERRESTRIAL
from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


def _to_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


@register_runner(803)
class BlackMarketSellUnitsRunner(BaseRunner):
    """List military units for sale on the Black Market.

    Inputs:
        city_id         — city with the Black Market building
        unit_id         — unit type ID to sell (from Black Market dropdown)
        amount          — number of units to offer
        unit_price      — price per unit in gold
        offer_resource  — 5 (gold, default) or other resource index
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}

        city_id = _to_int(inputs.get("city_id"))
        unit_id = _to_int(inputs.get("unit_id"))
        amount = _to_int(inputs.get("amount"))
        unit_price = _to_int(inputs.get("unit_price"))
        offer_resource = _to_int(inputs.get("offer_resource"), 5)

        if not city_id or not unit_id or not amount or not unit_price:
            return RunnerResult(success=False, data={"error": "missing_inputs: city_id, unit_id, amount, unit_price required"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            # Find Black Market position from snapshot
            position = self._find_building_position(ga_id, city_id, "blackMarket")
            if position is None:
                self.log(jid, "error", f"Black Market nao encontrado na cidade {city_id}")
                return RunnerResult(success=False, data={"error": "black_market_not_found"})

            # Determine unit category: 200-299 = fleet, 300-399 = troops
            is_fleet = 200 <= unit_id < 300
            building_type = "fleet" if is_fleet else "troops"

            # Check garrison via cityMilitary view — returns {unit_id: count}
            try:
                stationed = client.fetch_stationed_units(city_id, building_type=building_type)
                garrison = stationed.get("counts", {})
                in_garrison = garrison.get(unit_id, 0)
                self.log(jid, "info", f"Garrison {building_type}: {garrison}")
                if in_garrison <= 0:
                    self.log(jid, "error", f"Unidade id={unit_id} nao encontrada no garrison ({building_type}). Garrison: {garrison}")
                    return RunnerResult(success=False, data={"error": "unit_not_in_garrison", "garrison": garrison})
                if amount > in_garrison:
                    self.log(jid, "warn", f"Quantidade {amount} > garrison {in_garrison}, ajustando")
                    amount = in_garrison
            except Exception as e:
                self.log(jid, "warn", f"Falha ao checar garrison (continuando): {e}")

            # Fetch BM state WITH unit_id to get price limits (minLimit/maxLimit from templateData)
            state = client.get_black_market_state(city_id, position, unit_id=unit_id, offer_resource=offer_resource)
            units_found = state.get("units", [])
            slots_used = state.get("offer_slots_used", 0)
            slots_total = state.get("offer_slots_total", 8)
            selected_unit = state.get("selected_unit")
            own_offers = state.get("own_offers") or []
            market_blocked = bool(state.get("market_blocked"))
            cooldown_active = bool(state.get("cooldown_active"))
            cooldown_timestamp = _to_int(state.get("cooldown_timestamp"))
            self.log(jid, "info", f"BM state: {len(units_found)} unidades, slots {slots_used}/{slots_total}, limits={selected_unit}")
            if own_offers:
                self.log(jid, "info", f"BM ofertas proprias ativas: {own_offers}")
            if market_blocked or cooldown_active:
                self.log(
                    jid,
                    "warn",
                    f"BM flags: market_blocked={market_blocked} cooldown_active={cooldown_active} cooldown_ts={cooldown_timestamp}",
                )

            try:
                snap = self.hub.get_snapshot(game_account_id=ga_id)
                city_name = next(
                    (c.get("name", "") for c in (snap.get("cities") or []) if str(c.get("id")) == str(city_id)),
                    "",
                )
            except Exception:
                city_name = ""

            try:
                quotes_payload = []
                active_offer_by_unit = {
                    _to_int(item.get("unit_id")): item
                    for item in own_offers
                    if _to_int(item.get("unit_id")) > 0
                }
                for item in units_found:
                    q_unit_id = _to_int(item.get("unit_id"))
                    if q_unit_id <= 0:
                        continue
                    active_offer = active_offer_by_unit.get(q_unit_id) or {}
                    quotes_payload.append({
                        "unit_id": q_unit_id,
                        "unit_name": str(item.get("name") or ""),
                        "offer_resource": offer_resource,
                        "price_min": _to_int(item.get("price_min")),
                        "price_max": _to_int(item.get("price_max")),
                        "available_amount": _to_int(item.get("available_amount")),
                        "active_offer_amount": _to_int(active_offer.get("amount")),
                        "active_offer_price": _to_int(active_offer.get("price")),
                    })
                if quotes_payload:
                    self.hub.save_bm_quotes(
                        game_account_id=ga_id,
                        job_id=jid,
                        city_id=city_id,
                        city_name=city_name,
                        quotes=quotes_payload,
                    )
            except Exception as quote_exc:
                self.log(jid, "warn", f"Falha ao persistir cotacoes BM: {quote_exc}")

            # Check if unit is disabled (not sellable in this city)
            unit_in_list = next((u for u in units_found if u["unit_id"] == unit_id), None)
            if units_found and unit_in_list and unit_in_list.get("disabled"):
                self.log(jid, "error", f"Unidade {unit_id} ({unit_in_list.get('name','?')}) esta desabilitada no Mercado Negro (sem garrison suficiente ou nao vendavel)")
                return RunnerResult(success=False, data={"error": "unit_disabled_on_black_market"})

            # Validate price against game limits
            if selected_unit:
                pmin = selected_unit.get("price_min", 1)
                pmax = selected_unit.get("price_max", 999999)
                available_amount = _to_int(selected_unit.get("available_amount"))
                if available_amount > 0 and amount > available_amount:
                    self.log(jid, "warn", f"Quantidade {amount} acima do maximo disponivel {available_amount}, ajustando")
                    amount = available_amount
                if not (pmin <= unit_price <= pmax):
                    self.log(jid, "warn", f"Preco {unit_price} fora dos limites [{pmin}-{pmax}], ajustando para {pmin}")
                    unit_price = max(pmin, min(pmax, unit_price))
                    if unit_price != pmin and pmin <= pmax:
                        unit_price = pmin
                self.log(jid, "info", f"Preco validado: {unit_price} (range {pmin}-{pmax}) | max={available_amount}")
            else:
                self.log(jid, "warn", f"Limites de preco nao obtidos para unit {unit_id}. Prosseguindo sem validacao de preco.")

            if slots_used >= slots_total:
                self.log(jid, "warn", f"Mercado Negro cheio: {slots_used}/{slots_total} slots usados")
                return RunnerResult(success=False, data={"error": "no_offer_slots"})

            # Resolve unit name for logging/storage
            unit_name = str((unit_in_list or {}).get("name") or "").strip() or next(
                (str(u.get("name") or "").strip() for u in units_found if u["unit_id"] == unit_id),
                "",
            )

            result = client.add_black_market_offer(
                city_id=city_id,
                position=position,
                unit_id=unit_id,
                amount=amount,
                unit_price=unit_price,
                offer_resource=offer_resource,
            )

            self.log(jid, "info", f"Oferta criada: {amount}x {unit_name or f'id={unit_id}'} por {unit_price} ouro/un")
            self.save_game_client(ga_id, client)

            active_offers = []
            try:
                active_offers = client.get_my_black_market_offers(city_id=city_id, position=position)
                self.log(jid, "info", f"BM ofertas ativas apos criar: {len(active_offers)}")
            except Exception as sync_exc:
                self.log(jid, "warn", f"Falha ao ler ofertas ativas do BM apos criar: {sync_exc}")

            # Persist offer to hub for tracking + price history
            try:
                active_unit_ids = sorted({int(o.get("unit_id", 0) or 0) for o in active_offers if int(o.get("unit_id", 0) or 0) > 0})
                if active_unit_ids:
                    self.hub.sync_bm_offers(
                        game_account_id=ga_id,
                        city_id=city_id,
                        active_unit_ids=active_unit_ids,
                    )
                matched_offer = next(
                    (
                        o for o in active_offers
                        if _to_int(o.get("unit_id")) == unit_id
                        and _to_int(o.get("price")) == unit_price
                        and _to_int(o.get("amount")) >= amount
                    ),
                    None,
                )
                if matched_offer or not active_offers:
                    self.hub.save_bm_offer(
                        game_account_id=ga_id,
                        job_id=jid,
                        city_id=city_id,
                        city_name=city_name,
                        unit_id=unit_id,
                        unit_name=unit_name,
                        amount=amount,
                        unit_price=unit_price,
                        offer_resource=offer_resource,
                    )
                else:
                    self.log(
                        jid,
                        "warn",
                        f"Oferta criada mas nao confirmada nas ofertas ativas para unit={unit_id} price={unit_price} amount={amount}",
                    )
            except Exception as e:
                logger.warning("save_bm_offer failed (non-fatal): %s", e)

            return RunnerResult(success=True, data={"unit_id": unit_id, "unit_name": unit_name, "amount": amount, "unit_price": unit_price})

        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
            exc_text = str(exc)
            if "Acesso restrito" in exc_text:
                self.log(
                    jid,
                    "error",
                    "Mercado Negro rejeitou a venda com acesso restrito. "
                    "A tela/carregamento esta acessivel, entao o bloqueio parece ser regra do jogo/conta e nao erro de parsing.",
                )
                return RunnerResult(
                    success=False,
                    data={
                        "error": "black_market_access_restricted",
                        "message": exc_text,
                    },
                )
            self.log(jid, "error", f"Mercado Negro venda falhou: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

    def _find_building_position(self, ga_id: str, city_id: int, building_type: str) -> int | None:
        """Find building position from snapshot."""
        try:
            snapshot = self.hub.get_snapshot(game_account_id=ga_id)
            cities = snapshot.get("cities") or []
            for city in cities:
                if str(city.get("id")) == str(city_id):
                    for building in (city.get("buildings") or []):
                        if str(building.get("building") or "").strip() == building_type:
                            return int(building.get("position", -1))
        except Exception as exc:
            logger.debug("find_building_position failed: %s", exc)
        return None


@register_runner(804)
class BlackMarketBuyUnitsRunner(BaseRunner):
    """Buy military units from another player's Black Market offer.

    Inputs:
        buyer_city_id     — buyer's city (must have Branch Office)
        seller_city_id    — seller's city
        seller_avatar     — seller player name
        seller_city_name  — seller city name
        unit_id           — unit type ID to buy
        quantity          — number of units to buy
        max_price         — maximum price per unit (won't buy above this)
        unit_category     — 444 (maritime) or 111 (terrestrial), default 444
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}

        buyer_city_id = _to_int(inputs.get("buyer_city_id"))
        seller_city_id = _to_int(inputs.get("seller_city_id"))
        seller_avatar = str(inputs.get("seller_avatar") or "")
        seller_city_name = str(inputs.get("seller_city_name") or "")
        unit_id = _to_int(inputs.get("unit_id"))
        quantity = _to_int(inputs.get("quantity"))
        max_price = _to_int(inputs.get("max_price"), 999999)
        unit_category = _to_int(inputs.get("unit_category"), UNIT_TYPE_MARITIME)

        if not buyer_city_id or not seller_city_id or not unit_id or not quantity:
            return RunnerResult(success=False, data={"error": "missing_inputs"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            # Find Branch Office position
            bo_position = self._find_building_position(ga_id, buyer_city_id, "branchOffice")
            if bo_position is None:
                self.log(jid, "error", f"Branch Office nao encontrado na cidade {buyer_city_id}")
                return RunnerResult(success=False, data={"error": "branch_office_not_found"})

            # Get offer details to confirm price and transport
            offer_details = client.get_unit_offer_details(
                buyer_city_id=buyer_city_id,
                bo_position=bo_position,
                seller_city_id=seller_city_id,
                unit_category=unit_category,
            )

            # Find the matching unit offer
            unit_offer = next((u for u in offer_details.get("units", []) if u["unit_id"] == unit_id), None)
            if not unit_offer:
                self.log(jid, "warn", f"Oferta para unidade {unit_id} nao encontrada em {seller_city_name}")
                return RunnerResult(success=False, data={"error": "offer_not_found"})

            actual_price = unit_offer.get("price", 0)
            if actual_price > max_price:
                self.log(jid, "warn", f"Preco atual {actual_price} acima do limite {max_price}, abortando")
                return RunnerResult(success=False, data={"error": "price_above_limit", "actual_price": actual_price})

            available_qty = unit_offer.get("amount", 0)
            buy_qty = min(quantity, available_qty)
            if buy_qty <= 0:
                return RunnerResult(success=False, data={"error": "no_units_available"})

            max_ships = offer_details.get("max_transporters", 1)
            eta_min = offer_details.get("eta_minutes", 0)

            self.log(
                jid, "info",
                f"Comprando {buy_qty}x unidade {unit_id} de {seller_city_name} ({seller_avatar}) "
                f"por {actual_price} ouro/un | ETA {eta_min}min"
            )

            result = client.buy_units_black_market(
                buyer_city_id=buyer_city_id,
                bo_position=bo_position,
                seller_city_id=seller_city_id,
                seller_avatar=seller_avatar,
                seller_city_name=seller_city_name,
                unit_id=unit_id,
                quantity=buy_qty,
                unit_price=actual_price,
                unit_category=unit_category,
                num_transporters=max(1, max_ships),
            )

            self.save_game_client(ga_id, client)
            return RunnerResult(
                success=True,
                data={
                    "unit_id": unit_id,
                    "quantity": buy_qty,
                    "unit_price": actual_price,
                    "eta_minutes": eta_min,
                    "total_cost": buy_qty * actual_price,
                },
            )

        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
            self.log(jid, "error", f"Mercado Negro compra falhou: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

    def _find_building_position(self, ga_id: str, city_id: int, building_type: str) -> int | None:
        try:
            snapshot = self.hub.get_snapshot(game_account_id=ga_id)
            cities = snapshot.get("cities") or []
            for city in cities:
                if str(city.get("id")) == str(city_id):
                    for building in (city.get("buildings") or []):
                        if str(building.get("building") or "").strip() == building_type:
                            return int(building.get("position", -1))
        except Exception as exc:
            logger.debug("find_building_position failed: %s", exc)
        return None
