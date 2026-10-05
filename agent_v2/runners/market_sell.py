"""Runner Vender para Pedido de Compra (ac=811, N-87).

Atende um pedido de compra visto no Mercado geral: manda os barcos da nossa cidade
para a cidade de quem esta pedindo. A venda so se completa na chegada.

Inputs:
    city_id           nossa cidade (com mercado) que vende
    branchoffice_pos  posicao do mercado nela (preenchido pelo hub)
    buyer_city_id     cidade de quem pede
    resource_idx      0=madeira 1=vinho 2=marmore 3=cristal 4=enxofre
    amount            quanto vender (limitado ao pedido, ao estoque e aos barcos livres)
    min_unit_price    nao vende se o comprador pagar menos que isso (0 = aceita o preco atual)
    dry_run           so confere e mostra o que seria enviado
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.market_sell import SellToRequestAction
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

RESOURCE_LABELS = {0: "Madeira", 1: "Vinho", 2: "Marmore", 3: "Cristal", 4: "Enxofre"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@register_runner(811)
class MarketSellToRequestRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        city_id = _to_int(inputs.get("city_id"))
        bo_pos = inputs.get("branchoffice_pos")
        buyer_city_id = _to_int(inputs.get("buyer_city_id"))
        resource_idx = _to_int(inputs.get("resource_idx"), -1)
        amount = _to_int(inputs.get("amount"))
        min_unit_price = max(0, _to_int(inputs.get("min_unit_price")))
        dry_run = str(inputs.get("dry_run")).strip().lower() in ("1", "true", "on", "yes")
        resource_label = RESOURCE_LABELS.get(resource_idx, f"res={resource_idx}")
        city_label = str(inputs.get("city_name") or city_id).strip()
        buyer_label = str(inputs.get("buyer_label") or buyer_city_id).strip()

        if not city_id or bo_pos is None or not buyer_city_id or resource_idx not in RESOURCE_LABELS or amount <= 0:
            self.log(jid, "error", "Faltam dados: cidade com mercado, cidade do comprador, recurso e quantidade.")
            return RunnerResult(success=False, data={"error": "missing inputs"})

        self.log(
            jid, "info",
            f"{'[SIMULACAO] ' if dry_run else ''}Venda para pedido de compra: ate {amount} de {resource_label} "
            f"de {city_label} para {buyer_label}"
            + (f", aceitando no minimo {min_unit_price} de ouro por unidade." if min_unit_price else " (preco atual do comprador)."),
        )

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            result = SellToRequestAction(client).execute(
                city_id=city_id, branchoffice_pos=_to_int(bo_pos), buyer_city_id=buyer_city_id,
                resource_idx=resource_idx, amount=amount, min_unit_price=min_unit_price, dry_run=dry_run,
            )
            self.save_game_client(ga_id or aid, client)
            if result["amount"] < amount:
                self.log(
                    jid, "warn",
                    f"Pedido de venda de {amount}, limitado a {result['amount']}: o comprador quer {result['wanted']}, "
                    f"estoque na cidade {result['stock'] if result['stock'] is not None else '?'}, "
                    f"barcos usados {result['ships']}.",
                )
            total = result["amount"] * result["unit_price"]
            if dry_run:
                self.log(
                    jid, "info",
                    f"[SIMULACAO] Seriam enviados {result['amount']} de {resource_label} em {result['ships']} barco(s) "
                    f"para {result['buyer_city']} ({result['buyer_player']}), que paga {result['unit_price']} por unidade "
                    f"({total} de ouro); preco minimo {result['min_unit_price']}. Nada foi enviado.",
                )
                return RunnerResult(success=True, data={"status": "dry_run", **result})
            self.log(
                jid, "info",
                f"Venda enviada: {result['amount']} de {resource_label} em {result['ships']} barco(s) para "
                f"{result['buyer_city']} ({result['buyer_player']}) a {result['unit_price']} por unidade "
                f"({total} de ouro, preco minimo {result['min_unit_price']}). O ouro entra quando os barcos chegarem.",
            )
            return RunnerResult(success=True, data={"status": "sold", **result})
        except Exception as exc:
            logger.exception("MarketSellToRequestRunner failed for job %s", jid)
            self.log(jid, "error", f"Venda para pedido de compra falhou: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
