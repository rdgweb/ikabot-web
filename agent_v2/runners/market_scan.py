"""Runner Varrer Mercado (ac=810, N-84).

Mostra o que existe no mercado geral: le as ofertas ao alcance dos mercados
(Branch Office) da conta e manda para o hub. Nao compra nem vende nada.

Inputs:
    city_ids              cidades para varrer; vazio = a de maior mercado da conta
    include_buy_requests  tambem listar quem quer comprar (type=333)
    max_pages             paginas por busca (10 ofertas cada), padrao 5

Em TODAS as cidades com mercado o alcance salvo e conferido e, se estiver abaixo
do maximo do nivel atual, e subido: o jogo nao aumenta o alcance sozinho quando
o mercado evolui.
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.market_scan import (
    OFFER_TYPE_BUY,
    OFFER_TYPE_SELL,
    MarketScanAction,
)
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

RESOURCE_LABELS = {0: "Madeira", 1: "Vinho", 2: "Marmore", 3: "Cristal", 4: "Enxofre"}
DEFAULT_MAX_PAGES = 5
MAX_PAGES_LIMIT = 20


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def market_cities(cities: Any) -> list[dict[str, Any]]:
    """Cities of the snapshot that have a Branch Office: {id, name, bo_pos, level}."""
    out: list[dict[str, Any]] = []
    for city in cities if isinstance(cities, list) else []:
        if not isinstance(city, dict) or city.get("id") is None:
            continue
        for building in city.get("buildings") or []:
            if isinstance(building, dict) and building.get("building") == "branchOffice" and building.get("position") is not None:
                out.append({
                    "id": _to_int(city.get("id")),
                    "name": str(city.get("name") or city.get("id")),
                    "bo_pos": _to_int(building.get("position")),
                    "level": _to_int(building.get("level")),
                })
                break
    return out


def parse_city_ids(raw: Any) -> list[int]:
    if isinstance(raw, str):
        raw = [part for part in raw.replace(";", ",").split(",")]
    ids: list[int] = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        value = _to_int(str(item).strip(), 0)
        if value > 0 and value not in ids:
            ids.append(value)
    return ids


def choose_scan_cities(markets: list[dict[str, Any]], wanted: list[int]) -> list[dict[str, Any]]:
    """The requested cities that have a market; by default the one with the biggest market."""
    if wanted:
        chosen = [m for m in markets if m["id"] in wanted]
        if chosen:
            return chosen
    if not markets:
        return []
    return [max(markets, key=lambda m: (m["level"], -m["id"]))]


@register_runner(810)
class MarketScanRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        include_buy = bool(inputs.get("include_buy_requests"))
        max_pages = max(1, min(MAX_PAGES_LIMIT, _to_int(inputs.get("max_pages"), DEFAULT_MAX_PAGES) or DEFAULT_MAX_PAGES))
        wanted = parse_city_ids(inputs.get("city_ids") or inputs.get("city_id"))

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            snapshot = self.hub.get_snapshot(game_account_id=ga_id) or {}
            markets = market_cities(snapshot.get("cities"))
            if not markets:
                self.log(jid, "error", "Nenhuma cidade com mercado (Branch Office) no snapshot desta conta.")
                return RunnerResult(success=False, data={"error": "no_market_city"})
            scan_cities = choose_scan_cities(markets, wanted)
            scan_ids = {m["id"] for m in scan_cities}

            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            action = MarketScanAction(client)
            scans: list[dict[str, Any]] = []
            raised = 0

            # 1. every market: the saved range has to follow the building level
            for market in markets:
                try:
                    state = action.sync_range(market["id"], market["bo_pos"])
                except Exception as exc:
                    self.log(jid, "warn", f"{market['name']}: nao foi possivel conferir o alcance do mercado: {exc}")
                    state = {"before": 0, "max": 0, "changed": 0}
                market["range_before"], market["range_max"] = state["before"], state["max"]
                if state["changed"]:
                    raised += 1
                    self.log(
                        jid, "info",
                        f"{market['name']} (mercado nv {market['level']}): alcance salvo era {state['before']}, "
                        f"o nivel permite {state['max']} -> ajustado para {state['max']}.",
                    )
                elif state["max"] and state["before"] != state["max"]:
                    self.log(
                        jid, "warn",
                        f"{market['name']} (mercado nv {market['level']}): alcance salvo e {state['before']} e o nivel "
                        f"permite {state['max']}, mas o jogo nao confirmou o ajuste.",
                    )
                if market["id"] not in scan_ids:
                    scans.append({
                        "city_id": market["id"], "city_name": market["name"], "kind": "range_sync",
                        "bo_level": market["level"], "max_range": state["max"], "range_before": state["before"],
                        "offers": [],
                    })

            # 2. the chosen cities: what is on offer within reach
            total = 0
            for market in scan_cities:
                offers: list[dict[str, Any]] = []
                truncated: list[str] = []
                summary: list[str] = []
                for resource_idx, label in RESOURCE_LABELS.items():
                    for offer_type in (OFFER_TYPE_SELL, OFFER_TYPE_BUY) if include_buy else (OFFER_TYPE_SELL,):
                        result = action.search(
                            market["id"], market["bo_pos"], resource_idx, offer_type=offer_type, max_pages=max_pages,
                        )
                        offers.extend(result["offers"])
                        market["range_max"] = result["max_range"] or market.get("range_max", 0)
                        if result["truncated"]:
                            truncated.append(label)
                        if offer_type == OFFER_TYPE_SELL:
                            prices = [o["unit_price"] for o in result["offers"] if o["unit_price"] > 0]
                            summary.append(
                                f"{label}: {len(result['offers'])} oferta(s)"
                                + (f", a partir de {min(prices)} de ouro" if prices else "")
                            )
                total += len(offers)
                self.log(
                    jid, "info",
                    f"{market['name']} (mercado nv {market['level']}, alcance {market.get('range_max', 0)}): "
                    + " | ".join(summary),
                )
                if truncated:
                    self.log(
                        jid, "warn",
                        f"{market['name']}: a lista de {', '.join(truncated)} tem mais de {max_pages} pagina(s); "
                        "aumente 'paginas por busca' para ver tudo.",
                    )
                scans.append({
                    "city_id": market["id"], "city_name": market["name"], "kind": "full",
                    "bo_level": market["level"], "max_range": market.get("range_max", 0),
                    "range_before": market.get("range_before", 0), "offers": offers,
                })

            self.save_game_client(ga_id, client)
            saved = self.hub.save_public_market_scan(game_account_id=str(ga_id), job_id=jid, scans=scans)
            self.log(
                jid, "info",
                f"Varredura concluida: {total} oferta(s) em {len(scan_cities)} cidade(s); "
                f"alcance ajustado em {raised} de {len(markets)} mercado(s). Veja em Mercado Interno > Mercado geral.",
            )
            return RunnerResult(success=True, data={
                "status": "scanned", "offers": total, "cities": [m["id"] for m in scan_cities],
                "ranges_raised": raised, "saved": (saved or {}).get("created"),
            })
        except Exception as exc:
            logger.exception("MarketScanRunner failed for job %s", jid)
            self.log(jid, "error", f"Falha ao varrer o mercado: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
