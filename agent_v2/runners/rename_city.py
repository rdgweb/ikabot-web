"""Runner Renomear Cidade (ac=33).

Muda o nome de uma cidade no jogo e reflete no snapshot.
Inputs: city_id, new_name (max 15 chars).
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from services.resource_transport import change_current_city

logger = logging.getLogger(__name__)


@register_runner(33)
class RenameCityRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        city_id = str(inputs.get("city_id") or "").strip()
        new_name = str(inputs.get("new_name") or "").strip()[:15]
        if not city_id or not new_name:
            self.log(jid, "error", "city_id e new_name obrigatorios")
            return RunnerResult(success=False, data={"error": "missing_inputs"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            change_current_city(client, int(city_id))
            result = client.rename_city(int(city_id), new_name)
            self.save_game_client(ga_id, client)
            self.log(jid, "info", f"Cidade {city_id} renomeada para '{new_name}'.")

            # Reflete o novo nome no snapshot
            try:
                snapshot = self.hub.get_snapshot(game_account_id=ga_id)
                cities = [dict(c) for c in ((snapshot or {}).get("cities") or [])]
                for c in cities:
                    if str(c.get("id") or "") == str(city_id):
                        c["name"] = new_name
                self.hub.update_snapshot(
                    aid,
                    {
                        "base_snapshot": (snapshot or {}).get("base_snapshot") or {},
                        "cities": cities,
                        "military": (snapshot or {}).get("military") or {},
                        "source_job_id": jid,
                    },
                    game_account_id=ga_id,
                )
            except Exception as exc:
                self.log(jid, "warn", f"Falha ao refletir nome no snapshot: {exc}")

            return RunnerResult(success=True, data={"status": "renamed", "city_id": city_id, "name": new_name})
        except Exception as exc:
            logger.exception("RenameCityRunner failed for job %s", jid)
            self.log(jid, "error", f"Falha ao renomear: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
