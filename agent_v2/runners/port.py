"""Runner Comprar Barcos (ac=29).

Compra N barcos de transporte (mercante ou cargueiro) numa cidade, checando
ouro e limite antes de cada compra. Reflete o resultado no snapshot (ouro e
contadores de barco) sem esperar o Verificar Status.
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.port import ship_cost
from runners.base import BaseRunner, RunnerResult
from services.resource_transport import change_current_city

logger = logging.getLogger(__name__)


@register_runner(29)
class BuyShipsRunner(BaseRunner):
    """Compra barcos mercantes/cargueiros numa cidade."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        kind = str(inputs.get("ship_kind") or "transporter").strip()
        if kind not in ("transporter", "freighter"):
            kind = "transporter"
        try:
            amount = int(inputs.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            self.log(jid, "error", "Quantidade de barcos deve ser > 0")
            return RunnerResult(success=False, data={"error": "invalid_amount"})

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

        label = "mercante" if kind == "transporter" else "cargueiro"
        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            change_current_city(client, int(city_id))
            state = client.get_port_state(int(city_id))
            gold = int(state.get("gold") or 0)
            if not state.get(f"{kind}_buyable"):
                self.log(jid, "error", f"Compra de {label} indisponivel no jogo agora.")
                return RunnerResult(success=False, data={"error": "not_buyable"})

            count = int(state.get(f"{kind}_count") or 0)
            bonus = int(state.get(f"{kind}_bonus") or 0)
            max_ships = int(state.get(f"{kind}_max") or 0)
            # Ancora no custo real do proximo barco reportado pelo jogo; os
            # seguintes seguem a curva (x1.03) a partir dele.
            anchor_cost = int(state.get(f"{kind}_next_cost") or 0)

            bought = 0
            spent = 0
            for i in range(amount):
                if max_ships and count + bonus + bought >= max_ships:
                    self.log(jid, "info", f"Limite de {label} atingido ({max_ships}).")
                    break
                # custo do i-esimo barco desta compra
                if anchor_cost > 0:
                    next_cost = int(anchor_cost * (1.03 ** bought))
                else:
                    next_cost = ship_cost(count + bought + 1)
                if gold - spent < next_cost:
                    self.log(jid, "warn", f"Ouro insuficiente para o proximo {label}: precisa {next_cost:,}, tem {gold - spent:,}.")
                    break

                client.buy_ship(int(city_id), kind)
                bought += 1
                spent += next_cost
                self.log(jid, "info", f"Comprado {label} {bought}/{amount} (#{count + bought}) por ~{next_cost:,} ouro.")

            if bought == 0:
                self.log(jid, "error", f"Nenhum {label} comprado.")
                return RunnerResult(success=False, data={"error": "none_bought"})

            self.save_game_client(ga_id, client)

            # Reflete no snapshot: ouro e contadores de barco
            try:
                self.hub.patch_snapshot_gold(str(ga_id), delta_gold=-spent, op_key=f"buyships:{jid}")
            except Exception as exc:
                self.log(jid, "warn", f"Falha ao refletir ouro no snapshot: {exc}")
            try:
                if kind == "transporter":
                    self.hub.patch_snapshot_ships(str(ga_id), delta_transporters=bought)
                else:
                    self.hub.patch_snapshot_ships(str(ga_id), delta_freighters=bought)
            except Exception as exc:
                self.log(jid, "warn", f"Falha ao refletir barcos no snapshot: {exc}")

            self.log(jid, "info", f"Concluido: {bought} {label}(s) por ~{spent:,} ouro.")
            return RunnerResult(success=True, data={"status": "bought", "kind": kind, "bought": bought, "spent": spent})
        except Exception as exc:
            logger.exception("BuyShipsRunner failed for job %s", jid)
            self.log(jid, "error", f"Falha ao comprar barcos: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
