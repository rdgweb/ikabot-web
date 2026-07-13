"""Runner de recursos premium (ac=28) — ACAO sob confirmacao.

Executa UMA acao escolhida pelo usuario no form:
  - premium_mode=use_item: ativa o item <premium_item_id> do inventario
    (apenas itens usaveis direto — sem cidade/deus).
  - premium_mode=trade: troca recursos no negociante premium.

Nao roda em loop e nao decide nada sozinho: a leitura do inventario e do
negociante e feita pelo Verificar Status (ac=100); aqui so agimos com o que
o usuario selecionou e confirmou ao criar o job.
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

_RESOURCE_KEYS = ("resource", "wine", "marble", "crystal", "sulfur")


def _feedback_text(result: Any) -> str:
    texts = []
    for entry in (result or {}).get("feedbacks") or []:
        if isinstance(entry, dict) and entry.get("text"):
            texts.append(str(entry["text"]).strip())
    return " | ".join(texts)


@register_runner(28)
class PremiumResourcesRunner(BaseRunner):
    """Ativa um item premium usavel direto ou troca no negociante."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            self.log(jid, "error", "game_account_id ausente")
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        mode = str(inputs.get("premium_mode") or "").strip()
        if mode not in ("use_item", "trade"):
            self.log(jid, "error", f"premium_mode invalido: {mode!r}")
            return RunnerResult(success=False, data={"error": "invalid_mode"})

        snapshot = self.get_snapshot(jid, ga_id)
        cities = (snapshot or {}).get("cities") or []
        city_id = str(inputs.get("city_id") or "").strip()
        if not city_id and cities:
            city_id = str((cities[0] or {}).get("id") or "")
        if not city_id:
            self.log(jid, "error", "Nenhuma cidade disponivel")
            return RunnerResult(success=False, data={"error": "missing_city"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            if mode == "use_item":
                item_id = int(inputs.get("premium_item_id") or 0)
                if not item_id:
                    self.log(jid, "error", "premium_item_id ausente")
                    return RunnerResult(success=False, data={"error": "missing_item_id"})

                # Confirma que o item ainda esta no inventario e e usavel direto
                inv = client.get_premium_inventory(int(city_id))
                target = next((it for it in inv.get("items") or [] if it.get("item_id") == item_id), None)
                if target is None:
                    self.log(jid, "error", f"Item {item_id} nao esta mais no inventario")
                    return RunnerResult(success=False, data={"error": "item_not_found"})
                if not target.get("usable_direct"):
                    self.log(jid, "error", f"Item {item_id} ({target.get('name')}) nao e usavel direto")
                    return RunnerResult(success=False, data={"error": "item_not_usable_direct"})

                result = client.activate_premium_item(int(city_id), item_id)
                fb = _feedback_text(result)
                self.log(jid, "info", f"Item ativado: {target.get('name')} (id={item_id}) {('| ' + fb) if fb else ''}")
                return RunnerResult(success=True, data={"status": "item_used", "item_id": item_id, "name": target.get("name"), "feedback": fb})

            # mode == "trade" — troca 1:1 multi-recurso por % do estoque real
            trade_city_id = str(inputs.get("premium_trade_city_id") or city_id).strip()
            send_pct = dict(inputs.get("premium_send_pct") or {})
            receive_weights = dict(inputs.get("premium_receive_weights") or {})
            send_pct = {k: max(0, min(100, int(v or 0))) for k, v in send_pct.items() if k in _RESOURCE_KEYS}
            receive_weights = {k: max(0, int(v or 0)) for k, v in receive_weights.items() if k in _RESOURCE_KEYS}
            if not any(v > 0 for v in send_pct.values()):
                self.log(jid, "error", "Nenhum recurso para enviar")
                return RunnerResult(success=False, data={"error": "nothing_to_send"})
            total_weight = sum(receive_weights.values())
            if total_weight <= 0:
                self.log(jid, "error", "Sem distribuicao de recebimento")
                return RunnerResult(success=False, data={"error": "no_receive_weights"})

            trader = client.get_premium_trader_state(int(trade_city_id))
            price = trader.get("payment_price") or 0
            method = trader.get("payment_method")
            available = trader.get("payment_available") or 0
            if available < price:
                self.log(jid, "error", f"Sem saldo para a troca: precisa {price} ({method}), tem {available}")
                return RunnerResult(success=False, data={"error": "insufficient_payment"})

            # Estoque real na hora (do trader) + capacidade (do snapshot da cidade)
            stock = {k: int((trader.get("stock") or {}).get(k, 0)) for k in _RESOURCE_KEYS}
            city = next((c for c in cities if str(c.get("id") or "") == str(trade_city_id)), {}) or {}
            capacity = int(city.get("warehouse_capacity") or 0)
            city_key = {"resource": "wood", "wine": "wine", "marble": "marble", "crystal": "crystal", "sulfur": "sulfur"}
            free_space = {k: max(0, capacity - int(city.get(city_key[k]) or 0)) for k in _RESOURCE_KEYS} if capacity else {k: 10**12 for k in _RESOURCE_KEYS}

            send = {k: (stock[k] * send_pct.get(k, 0)) // 100 for k in _RESOURCE_KEYS}
            total_send = sum(send.values())
            if total_send <= 0:
                self.log(jid, "error", "Total a enviar ficou zero (estoque real insuficiente)")
                return RunnerResult(success=False, data={"error": "zero_send"})

            # Distribui o total pelos pesos, limitado pelo espaco livre; ajusta pra 1:1
            receive = {k: 0 for k in _RESOURCE_KEYS}
            remaining = total_send
            ordered = sorted((k for k in _RESOURCE_KEYS if receive_weights.get(k, 0) > 0),
                             key=lambda k: receive_weights[k], reverse=True)
            for k in ordered:
                want = (total_send * receive_weights[k]) // total_weight
                give = min(want, free_space.get(k, 0), remaining)
                receive[k] = give
                remaining -= give
            # sobra por arredondamento/limite: joga no primeiro recurso com espaco
            if remaining > 0:
                for k in ordered:
                    room = min(free_space.get(k, 0) - receive[k], remaining)
                    if room > 0:
                        receive[k] += room
                        remaining -= room
                    if remaining <= 0:
                        break
            total_receive = sum(receive.values())
            if total_receive <= 0:
                self.log(jid, "error", "Sem espaco no armazem para receber")
                return RunnerResult(success=False, data={"error": "no_free_space"})
            # forca 1:1: envia exatamente o que consegue receber
            if total_receive < total_send:
                # reduz o envio proporcionalmente para igualar ao recebido
                factor_num, factor_den = total_receive, total_send
                send = {k: (v * factor_num) // factor_den for k, v in send.items()}
                total_send = sum(send.values())
                self.log(jid, "info", f"Envio ajustado para 1:1 pelo espaco disponivel: total={total_send}")

            result = client.premium_trade(
                int(trade_city_id),
                send=send,
                receive=receive,
                displayed_price=int(trader.get("trade_price_ambrosia") or 0),
            )
            fb = _feedback_text(result)
            send_str = ", ".join(f"{v} {k}" for k, v in send.items() if v > 0)
            recv_str = ", ".join(f"{v} {k}" for k, v in receive.items() if v > 0)
            self.log(jid, "info", f"Troca 1:1 no negociante ({total_send}): enviou [{send_str}] recebeu [{recv_str}] (paga {price} {method}) {('| ' + fb) if fb else ''}")
            return RunnerResult(success=True, data={"status": "traded", "send": send, "receive": receive, "total": total_send, "feedback": fb})

        except Exception as exc:
            logger.exception("PremiumResourcesRunner failed for job %s", jid)
            self.log(jid, "error", f"Falha na acao premium: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
