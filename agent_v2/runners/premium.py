"""Runner de recursos premium (ac=28) — LEITURA.

Le o inventario de itens premium e o estado do negociante premium e persiste
no base_snapshot para a UI exibir. NAO ativa itens nem faz trocas: o uso e
sempre sob confirmacao explicita do usuario (N-32).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


@register_runner(28)
class PremiumResourcesRunner(BaseRunner):
    """Sincroniza inventario premium + negociante premium no snapshot."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            self.log(jid, "error", "game_account_id ausente")
            return RunnerResult(success=False, data={"error": "missing_game_account"})

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

            inventory = client.get_premium_inventory(int(city_id))
            items = inventory.get("items") or []

            trader = {}
            try:
                trader = client.get_premium_trader_state(int(city_id))
            except Exception as exc:
                self.log(jid, "warn", f"Negociante premium indisponivel: {exc}")

            usable_now = sum(1 for it in items if it.get("can_be_activated") and it.get("can_use_from_inventory"))
            need_city = sum(1 for it in items if it.get("require_city"))
            self.log(
                jid,
                "info",
                (
                    f"Premium: {len(items)} itens | usaveis direto={usable_now} | exigem cidade={need_city} | "
                    f"ambrosia negociante={trader.get('ambrosia_available', 0)}"
                ),
            )

            payload = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "city_id": int(city_id),
                "items": items,
                "trader": trader,
            }
            try:
                self.hub.patch_snapshot_base(str(ga_id), {"premium_state": payload})
            except Exception as exc:
                self.log(jid, "warn", f"Falha ao persistir premium_state: {exc}")

            self.save_game_client(ga_id, client)
            return RunnerResult(
                success=True,
                data={
                    "status": "synced",
                    "items_count": len(items),
                    "usable_now": usable_now,
                    "require_city": need_city,
                    "trader_ambrosia": trader.get("ambrosia_available", 0),
                },
            )
        except Exception as exc:
            logger.exception("PremiumResourcesRunner failed for job %s", jid)
            self.log(jid, "error", f"Falha ao sincronizar premium: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
