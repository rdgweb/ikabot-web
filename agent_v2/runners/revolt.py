"""Runner: revolt - trigger a revolt to break city occupation or port blockade.

Action code: 9002

Inputs:
    city_id     (int, required)  - ID of the occupied city
    city_name   (str, optional)  - display name
    revolt_type (str, optional)  - "troops" | "ships" | "both" (default: "both")
"""
from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from game_client.constants import GAME_AJAX_HEADERS
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)


@register_runner(9002)
class RevoltRunner(BaseRunner):
    def execute(self, job: dict) -> RunnerResult:
        jid = job["job_id"]
        aid = job.get("account_id", "")
        game_account_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        city_id = str(inputs.get("city_id") or "").strip()
        city_name = str(inputs.get("city_name") or city_id)
        revolt_type = str(inputs.get("revolt_type") or "troops").strip()

        if not city_id:
            self.log(jid, "error", "city_id obrigatorio")
            return RunnerResult(success=False, data={"error": "city_id_missing"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=game_account_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "credentials_missing"})

        type_label = "frotas" if revolt_type == "ships" else "tropas"
        revolt_function = "revoltPort" if revolt_type == "ships" else "revolting"
        self.log(
            jid,
            "info",
            f"Revolt: iniciando contra {type_label} em {city_name} (id={city_id}) via {revolt_function}",
        )

        client = self.get_or_login_game_client(jid, aid, game_account_id, creds)
        session = client.session
        url = client._server_url
        action_request = client._action_request
        headers = dict(GAME_AJAX_HEADERS)

        ok = False
        try:
            resp = session.post(
                url,
                data={
                    "action": "transportOperations",
                    "function": revolt_function,
                    "cityId": city_id,
                    "currentCityId": city_id,
                    "backgroundView": "city",
                    "templateView": "cityMilitary",
                    "actionRequest": action_request,
                    "ajax": "1",
                },
                headers=headers,
                timeout=20,
            )

            import re

            action_request_match = re.search(r'"actionRequest"\s*:\s*"([a-f0-9]{32})"', resp.text)
            if action_request_match:
                client._action_request = action_request_match.group(1)

            try:
                data = resp.json()
                for entry in data:
                    if not isinstance(entry, list) or len(entry) < 2 or entry[0] != "provideFeedback":
                        continue
                    for feedback in entry[1] or []:
                        if not isinstance(feedback, dict):
                            continue
                        feedback_type = int(feedback.get("type", 0))
                        feedback_text = feedback.get("text", "")
                        if feedback_type == 10:
                            self.log(jid, "info", f"Revolta iniciada: {feedback_text}")
                            ok = True
                        elif feedback_type == 11:
                            self.log(jid, "error", f"Revolta falhou: {feedback_text}")
            except Exception as parse_exc:
                logger.debug("Revolt parse error: %s", parse_exc)

            if not ok:
                self.log(
                    jid,
                    "info",
                    f"Revolta ({type_label}) enviada para {city_name} - verificar resultado no jogo",
                )
                ok = True

        except Exception as exc:
            self.log(jid, "error", f"Revolta falhou: {exc}")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=False, data={"error": str(exc)})

        self.save_game_client(game_account_id, client)
        return RunnerResult(
            success=ok,
            data={"city_id": city_id, "city_name": city_name, "revolt_type": revolt_type},
        )
