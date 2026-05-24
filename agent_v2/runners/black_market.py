"""Black Market runners — sell and buy military units.

Action codes:
    803  black_market_sell   — list units for sale via Black Market building
    804  black_market_buy    — buy units from another player's Black Market offer
"""

from __future__ import annotations

import logging
import math
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
                matched_offer = next(
                    (
                        o for o in active_offers
                        if _to_int(o.get("unit_id")) == unit_id
                        and _to_int(o.get("price")) == unit_price
                        and _to_int(o.get("amount")) >= amount
                    ),
                    None,
                )
                game_offer_id = _to_int(matched_offer.get("offer_id")) if matched_offer and matched_offer.get("offer_id") else None
                if game_offer_id:
                    self.log(jid, "info", f"game_offer_id capturado: {game_offer_id}")
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
                        game_offer_id=game_offer_id,
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
        buyer_city_id  — buyer's city (must have Branch Office)
        unit_id        — numeric unit type ID to buy (shown in Black Market)
        quantity       — number of units desired
        max_price      — maximum price per unit (won't buy above this)
        unit_category  — 444 (maritime, default) or 111 (terrestrial)
        seller_city_id — optional: buy from a specific city; if omitted, auto-discover cheapest offer
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}

        buyer_city_id  = _to_int(inputs.get("buyer_city_id"))
        unit_id        = _to_int(inputs.get("unit_id"))
        quantity       = _to_int(inputs.get("quantity"))
        max_price      = _to_int(inputs.get("max_price"), 999999)
        seller_city_id = _to_int(inputs.get("seller_city_id"))  # optional

        # Derive category from unit_id range (reliable), fall back to input
        if 200 <= unit_id < 300:
            unit_category = UNIT_TYPE_MARITIME
        elif 300 <= unit_id < 400:
            unit_category = UNIT_TYPE_TERRESTRIAL
        else:
            unit_category = _to_int(inputs.get("unit_category"), UNIT_TYPE_MARITIME)

        if not buyer_city_id or not unit_id or not quantity:
            return RunnerResult(success=False, data={"error": "missing_inputs: buyer_city_id, unit_id, quantity required"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            bo_info = self._find_building_info(ga_id, buyer_city_id, "branchOffice")
            if not bo_info:
                self.log(jid, "error", f"Branch Office nao encontrado na cidade {buyer_city_id}")
                return RunnerResult(success=False, data={"error": "branch_office_not_found"})
            bo_position = int(bo_info["position"])
            market_range = math.ceil(int(bo_info.get("level") or 0) / 2) if int(bo_info.get("level") or 0) > 0 else None

            # If seller not specified, auto-discover cheapest matching offer
            seller_avatar = str(inputs.get("seller_avatar") or "")
            seller_city_name = str(inputs.get("seller_city_name") or "")

            if not seller_city_id:
                self.log(jid, "info", f"Buscando ofertas para unidade {unit_id} (categoria {unit_category})...")
                offers = client.get_available_unit_offers(
                    buyer_city_id=buyer_city_id,
                    bo_position=bo_position,
                    unit_category=unit_category,
                    market_range=market_range,
                )
                matching = [
                    o for o in offers
                    if o.get("unit_id") == unit_id and _to_int(o.get("price_per_unit")) <= max_price
                ]
                if not matching:
                    self.log(jid, "warn", f"Nenhuma oferta para unit={unit_id} com preco <= {max_price}")
                    return RunnerResult(success=False, data={"error": "no_offers_found", "unit_id": unit_id, "max_price": max_price})

                # Sort by price asc, then qty desc — buy from multiple sellers if needed
                matching.sort(key=lambda o: (_to_int(o.get("price_per_unit")), -_to_int(o.get("amount"))))

                remaining = quantity
                total_bought = 0
                total_cost = 0
                purchase_log = []

                for offer in matching:
                    if remaining <= 0:
                        break
                    s_city_id = _to_int(offer.get("seller_city_id"))
                    s_avatar = str(offer.get("seller_avatar") or "")
                    s_city_name = str(offer.get("seller_city_name") or "")
                    listed_price = _to_int(offer.get("price_per_unit"))
                    listed_qty = _to_int(offer.get("amount"))
                    if listed_qty <= 0:
                        continue

                    # Refresh the takeOffer page per seller to get a fresh actionRequest
                    # and live transport/price details before each individual buy.
                    offer_details = client.get_unit_offer_details(
                        buyer_city_id=buyer_city_id,
                        bo_position=bo_position,
                        seller_city_id=s_city_id,
                        unit_category=unit_category,
                    )
                    unit_offer = next((u for u in offer_details.get("units", []) if _to_int(u.get("unit_id")) == unit_id), None)
                    if not unit_offer:
                        self.log(jid, "warn", f"Oferta para unidade {unit_id} nao encontrada em cidade {s_city_id}")
                        continue

                    o_price = _to_int(unit_offer.get("price"), listed_price)
                    o_qty = _to_int(unit_offer.get("amount"), listed_qty)
                    offer_key = _to_int(unit_offer.get("offer_key"))
                    buy_qty = min(remaining, o_qty)
                    if buy_qty <= 0:
                        continue
                    num_transporters = max(1, -(-buy_qty // 5))
                    max_transporters = _to_int(offer_details.get("max_transporters"))
                    if max_transporters and num_transporters > max_transporters:
                        self.log(
                            jid,
                            "warn",
                            f"Barcos insuficientes para comprar {buy_qty}x unidade {unit_id}: precisa {num_transporters}, disponiveis {max_transporters}",
                        )
                        return RunnerResult(
                            success=False,
                            data={
                                "error": "not_enough_cargo_ships",
                                "required_transporters": num_transporters,
                                "available_transporters": max_transporters,
                                "quantity_remaining": remaining,
                            },
                        )
                    self.log(
                        jid, "info",
                        f"Comprando {buy_qty}x unidade {unit_id} de {s_city_name or f'id={s_city_id}'} "
                        f"por {o_price} ouro/un | {num_transporters} navios",
                    )
                    try:
                        client.buy_units_black_market(
                            buyer_city_id=buyer_city_id,
                            bo_position=bo_position,
                            seller_city_id=s_city_id,
                            seller_avatar=s_avatar,
                            seller_city_name=s_city_name,
                            unit_id=unit_id,
                            quantity=buy_qty,
                            unit_price=o_price,
                            unit_category=unit_category,
                            num_transporters=num_transporters,
                            offer_key=offer_key,
                        )
                        remaining -= buy_qty
                        total_bought += buy_qty
                        total_cost += buy_qty * o_price
                        purchase_log.append({"seller": s_city_name or str(s_city_id), "qty": buy_qty, "price": o_price})
                    except Exception as buy_exc:
                        self.log(jid, "warn", f"Compra de {s_city_name} falhou: {buy_exc}. Tentando proximo vendedor.")

                if total_bought == 0:
                    return RunnerResult(success=False, data={"error": "all_purchases_failed"})
                if remaining > 0:
                    self.log(jid, "warn", f"Comprado {total_bought}/{quantity} — {remaining} unidades nao disponiveis no mercado")

                self.save_game_client(ga_id, client)
                return RunnerResult(
                    success=True,
                    data={
                        "unit_id": unit_id,
                        "quantity_requested": quantity,
                        "quantity_bought": total_bought,
                        "total_cost": total_cost,
                        "purchases": purchase_log,
                    },
                )
            else:
                # Specific seller — verify price via takeOffer details
                offer_details = client.get_unit_offer_details(
                    buyer_city_id=buyer_city_id,
                    bo_position=bo_position,
                    seller_city_id=seller_city_id,
                    unit_category=unit_category,
                )
                unit_offer = next((u for u in offer_details.get("units", []) if u["unit_id"] == unit_id), None)
                if not unit_offer:
                    self.log(jid, "warn", f"Oferta para unidade {unit_id} nao encontrada em cidade {seller_city_id}")
                    return RunnerResult(success=False, data={"error": "offer_not_found"})
                actual_price = _to_int(unit_offer.get("price"))
                available_qty = _to_int(unit_offer.get("amount"))
                offer_key = _to_int(unit_offer.get("offer_key"))
                seller_avatar = seller_avatar or str(offer_details.get("seller_avatar") or "")
                seller_city_name = seller_city_name or str(offer_details.get("seller_city_name") or "")

                if actual_price > max_price:
                    self.log(jid, "warn", f"Preco {actual_price} acima do limite {max_price}")
                    return RunnerResult(success=False, data={"error": "price_above_limit", "actual_price": actual_price})

                buy_qty = min(quantity, available_qty)
                if buy_qty <= 0:
                    return RunnerResult(success=False, data={"error": "no_units_available"})

                num_transporters = max(1, -(-buy_qty // 5))
                self.log(
                    jid, "info",
                    f"Comprando {buy_qty}x unidade {unit_id} de {seller_city_name or f'id={seller_city_id}'} "
                    f"por {actual_price} ouro/un | {num_transporters} navios",
                )
                client.buy_units_black_market(
                    buyer_city_id=buyer_city_id,
                    bo_position=bo_position,
                    seller_city_id=seller_city_id,
                    seller_avatar=seller_avatar,
                    seller_city_name=seller_city_name,
                    unit_id=unit_id,
                    quantity=buy_qty,
                    unit_price=actual_price,
                    unit_category=unit_category,
                    num_transporters=num_transporters,
                    offer_key=offer_key,
                )
                self.save_game_client(ga_id, client)
                return RunnerResult(
                    success=True,
                    data={
                        "unit_id": unit_id,
                        "quantity_requested": quantity,
                        "quantity_bought": buy_qty,
                        "unit_price": actual_price,
                        "total_cost": buy_qty * actual_price,
                        "seller_city_id": seller_city_id,
                        "seller_avatar": seller_avatar,
                    },
                )

        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
            self.log(jid, "error", f"Mercado Negro compra falhou: {exc}")
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
            logger.debug("find_building_position failed: %s", exc)
        return None


@register_runner(808)
class CheckBlackMarketOffersRunner(BaseRunner):
    """Scan available unit offers at a buyer city's Branch Office and save to hub.

    Inputs:
        buyer_city_id  — city with Branch Office (required)
        unit_category  — 444 (maritime), 111 (terrestrial), or 0 (both, default)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}

        buyer_city_id = _to_int(inputs.get("buyer_city_id"))
        unit_category = _to_int(inputs.get("unit_category"), 0)

        if not buyer_city_id:
            return RunnerResult(success=False, data={"error": "missing_inputs: buyer_city_id required"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            bo_info = self._find_building_info(ga_id, buyer_city_id, "branchOffice")
            if not bo_info:
                self.log(jid, "error", f"Branch Office nao encontrado na cidade {buyer_city_id}")
                return RunnerResult(success=False, data={"error": "branch_office_not_found"})
            bo_position = int(bo_info["position"])
            market_range = math.ceil(int(bo_info.get("level") or 0) / 2) if int(bo_info.get("level") or 0) > 0 else None

            self.log(
                jid,
                "info",
                f"Buscando ofertas no Branch Office pos={bo_position} cidade={buyer_city_id} range={market_range or 'default'}",
            )

            if unit_category == 0:
                maritime = client.get_available_unit_offers(
                    buyer_city_id=buyer_city_id,
                    bo_position=bo_position,
                    unit_category=UNIT_TYPE_MARITIME,
                    market_range=market_range,
                )
                terrestrial = client.get_available_unit_offers(
                    buyer_city_id=buyer_city_id,
                    bo_position=bo_position,
                    unit_category=UNIT_TYPE_TERRESTRIAL,
                    market_range=market_range,
                )
                offers = maritime + terrestrial
            else:
                offers = client.get_available_unit_offers(
                    buyer_city_id=buyer_city_id,
                    bo_position=bo_position,
                    unit_category=unit_category,
                    market_range=market_range,
                )

            self.log(jid, "info", f"Encontradas {len(offers)} ofertas")

            self.hub.save_bm_available_offers(
                game_account_id=ga_id,
                job_id=jid,
                buyer_city_id=buyer_city_id,
                offers=offers,
            )

            self.save_game_client(ga_id, client)
            return RunnerResult(success=True, data={"offers_found": len(offers), "buyer_city_id": buyer_city_id})

        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
            self.log(jid, "error", f"Verificar ofertas falhou: {exc}")
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

    def _find_building_info(self, ga_id: str, city_id: int, building_type: str) -> dict[str, int] | None:
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


@register_runner(8031)
class BlackMarketCancelOfferRunner(BaseRunner):
    """Remove an active Black Market offer and return units to garrison.

    Inputs:
        offer_hub_id   — hub UUID of the BlackMarketOffer record
        game_offer_id  — game's numeric offer ID (from offerId in URL)
        city_id        — city where the Black Market is
        unit_id        — optional fallback matcher when game_offer_id is missing
        amount         — optional fallback matcher when game_offer_id is missing
        unit_price     — optional fallback matcher when game_offer_id is missing
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}

        offer_hub_id = str(inputs.get("offer_hub_id") or "").strip()
        game_offer_id = _to_int(inputs.get("game_offer_id"))
        city_id = _to_int(inputs.get("city_id"))
        unit_id = _to_int(inputs.get("unit_id"))
        amount = _to_int(inputs.get("amount"))
        unit_price = _to_int(inputs.get("unit_price"))

        if not offer_hub_id or not city_id:
            return RunnerResult(success=False, data={"error": "missing_inputs: offer_hub_id, city_id required"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            position = self._find_building_position(ga_id, city_id, "blackMarket")
            if position is None:
                self.log(jid, "error", f"Black Market nao encontrado na cidade {city_id}")
                return RunnerResult(success=False, data={"error": "black_market_not_found"})

            if not game_offer_id:
                self.log(jid, "info", "game_offer_id ausente; resolvendo oferta ativa no jogo")
                active_offers = client.get_my_black_market_offers(city_id=city_id, position=position)
                matched_offer = next(
                    (
                        o for o in active_offers
                        if (not unit_id or _to_int(o.get("unit_id")) == unit_id)
                        and (not unit_price or _to_int(o.get("price")) == unit_price)
                        and (not amount or _to_int(o.get("amount")) == amount)
                        and _to_int(o.get("offer_id")) > 0
                    ),
                    None,
                )
                if not matched_offer:
                    self.log(jid, "error", "Nao foi possivel resolver a oferta ativa no jogo")
                    return RunnerResult(success=False, data={"error": "game_offer_id_not_found_live"})
                game_offer_id = _to_int(matched_offer.get("offer_id"))
                self.log(jid, "info", f"game_offer_id resolvido ao vivo: {game_offer_id}")

            self.log(jid, "info", f"Removendo oferta game_offer_id={game_offer_id} da cidade {city_id} pos={position}")
            client.cancel_black_market_offer(city_id=city_id, position=position, offer_id=game_offer_id)
            self.log(jid, "info", "Oferta removida com sucesso")

            try:
                self.hub.close_bm_offer(offer_hub_id)
            except Exception as e:
                self.log(jid, "warn", f"Falha ao atualizar status no hub (nao critico): {e}")

            self.save_game_client(ga_id, client)
            return RunnerResult(success=True, data={"game_offer_id": game_offer_id, "offer_hub_id": offer_hub_id})

        except Exception as exc:
            if self.is_network_error(exc):
                return self.network_error_result(jid, exc)
            self.log(jid, "error", f"Mercado Negro cancelamento falhou: {exc}")
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
