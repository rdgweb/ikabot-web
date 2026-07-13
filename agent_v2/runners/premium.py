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

            # mode == "trade"
            send_key = str(inputs.get("premium_trade_send") or "").strip()
            recv_key = str(inputs.get("premium_trade_receive") or "").strip()
            amount = int(inputs.get("premium_trade_amount") or 0)
            if send_key not in _RESOURCE_KEYS or recv_key not in _RESOURCE_KEYS:
                self.log(jid, "error", f"Recursos invalidos: send={send_key} receive={recv_key}")
                return RunnerResult(success=False, data={"error": "invalid_resources"})
            if amount <= 0:
                self.log(jid, "error", "Quantidade de troca deve ser > 0")
                return RunnerResult(success=False, data={"error": "invalid_amount"})

            trader = client.get_premium_trader_state(int(city_id))
            price = trader.get("payment_price") or 0
            method = trader.get("payment_method")
            available = trader.get("payment_available") or 0
            if available < price:
                self.log(jid, "error", f"Sem saldo para a troca: precisa {price} ({method}), tem {available}")
                return RunnerResult(success=False, data={"error": "insufficient_payment"})

            send = {k: (amount if k == send_key else 0) for k in _RESOURCE_KEYS}
            receive = {k: (amount if k == recv_key else 0) for k in _RESOURCE_KEYS}
            result = client.premium_trade(
                int(city_id),
                send=send,
                receive=receive,
                displayed_price=int(trader.get("trade_price_ambrosia") or 0),
            )
            fb = _feedback_text(result)
            self.log(jid, "info", f"Troca no negociante: {amount} {send_key} -> {amount} {recv_key} (paga {price} {method}) {('| ' + fb) if fb else ''}")
            return RunnerResult(success=True, data={"status": "traded", "send": send_key, "receive": recv_key, "amount": amount, "feedback": fb})

        except Exception as exc:
            logger.exception("PremiumResourcesRunner failed for job %s", jid)
            self.log(jid, "error", f"Falha na acao premium: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
