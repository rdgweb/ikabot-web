"""Runner: revolt — trigger a revolt to break city occupation or port blockade.

Action code: 9002

Inputs:
    city_id     (int, required)  — ID of the occupied city
    city_name   (str, optional)  — display name
    revolt_type (str, optional)  — "troops" | "ships" | "both" (default: "both")
"""
from __future__ import annotations

import logging
import time
from typing import Any

from core.runner_registry import register_runner
from game_client.constants import GAME_AJAX_HEADERS
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


@register_runner(9002)
class RevoltRunner(BaseRunner):
    def execute(self, job: dict) -> RunnerResult:
        jid = job["job_id"]
        game_account_id = job.get("game_account_id", "")
        inputs: dict[str, Any] = {}
        try:
            import json
            inputs = json.loads(job.get("inputs_json") or "{}")
        except Exception:
            pass

        city_id = str(inputs.get("city_id") or "").strip()
        city_name = str(inputs.get("city_name") or city_id)

        if not city_id:
            self.log(jid, "error", "city_id obrigatório")
            return RunnerResult(success=False, data={"error": "city_id_missing"})

        self.log(jid, "info", f"Revolt: iniciando para {city_name} (id={city_id})")
        client = self.get_game_client(game_account_id)
        session = client.session
        url = client._server_url
        ar = client._action_request

        headers = dict(GAME_AJAX_HEADERS)

        ok = False
        try:
            resp = session.post(url, data={
                "action": "transportOperations",
                "function": "revolting",
                "cityId": city_id,
                "currentCityId": city_id,
                "backgroundView": "city",
                "templateView": "cityMilitary",
                "actionRequest": ar,
                "ajax": "1",
            }, headers=headers, timeout=20)

            import re, json as _json
            ar_m = re.search(r'"actionRequest"\s*:\s*"([a-f0-9]{32})"', resp.text)
            if ar_m:
                client._action_request = ar_m.group(1)

            # Check for success/error in response
            try:
                data = resp.json()
                for entry in data:
                    if isinstance(entry, list) and len(entry) >= 2:
                        if entry[0] == "provideFeedback":
                            for fb in (entry[1] or []):
                                if isinstance(fb, dict):
                                    fb_type = int(fb.get("type", 0))
                                    fb_text = fb.get("text", "")
                                    if fb_type == 10:
                                        self.log(jid, "info", f"Revolta iniciada: {fb_text}")
                                        ok = True
                                    elif fb_type == 11:
                                        self.log(jid, "error", f"Revolta falhou: {fb_text}")
            except Exception as parse_exc:
                logger.debug("Revolt parse error: %s", parse_exc)

            if not ok:
                self.log(jid, "info", f"Revolta enviada para {city_name} (verificar resultado no jogo)")
                ok = True

        except Exception as exc:
            self.log(jid, "error", f"Revolta falhou: {exc}")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=False, data={"error": str(exc)})

        self.save_game_client(game_account_id, client)
        return RunnerResult(success=ok, data={"city_id": city_id, "city_name": city_name})
