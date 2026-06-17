"""Runner: revolt - trigger a revolt to break city occupation or port blockade.

Action code: 9002

Inputs:
    city_id     (int, required)  - ID of the occupied city
    city_name   (str, optional)  - display name
    revolt_type (str, optional)  - "troops" | "ships" | "both" (default: "both")
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs
from typing import Any

from core.runner_registry import register_runner
from game_client.constants import GAME_AJAX_HEADERS
from runners.base import BaseRunner, RunnerResult
from sessions.game_session_service import LoginCooldownActive

logger = logging.getLogger(__name__)


@register_runner(9002)
class RevoltRunner(BaseRunner):
    def _patch_snapshot_after_success(
        self,
        *,
        job_id: str,
        account_id: str,
        game_account_id: str,
        city_id: str,
        revolt_type: str,
    ) -> None:
        snapshot = self.get_snapshot(job_id, game_account_id)
        if not snapshot:
            return

        cities = [dict(city) for city in (snapshot.get("cities") or [])]
        military = dict(snapshot.get("military") or {})
        changed = False

        for city in cities:
            if str(city.get("id") or "").strip() != str(city_id).strip():
                continue
            if revolt_type == "ships":
                if city.get("harbour_occupied") or city.get("port_controller_name"):
                    city["harbour_occupied"] = None
                    city["port_controller_name"] = None
                    changed = True
            else:
                if city.get("city_occupied") or city.get("occupier_name"):
                    city["city_occupied"] = None
                    city["occupier_name"] = None
                    changed = True
            break

        by_city = []
        for entry in (military.get("by_city") or []):
            item = dict(entry) if isinstance(entry, dict) else entry
            if not isinstance(item, dict):
                by_city.append(item)
                continue
            if str(item.get("city_id") or "").strip() == str(city_id).strip():
                if revolt_type == "ships":
                    if item.get("blockade_forces"):
                        item["blockade_forces"] = []
                        changed = True
                else:
                    if item.get("occupation_forces"):
                        item["occupation_forces"] = []
                        changed = True
            by_city.append(item)
        if by_city:
            military["by_city"] = by_city

        if not changed:
            return

        try:
            self.hub.update_snapshot(
                account_id,
                {
                    "base_snapshot": snapshot.get("base_snapshot") or {},
                    "cities": cities,
                    "military": military,
                    "source_job_id": job_id,
                },
                game_account_id=game_account_id,
            )
            self.log(job_id, "info", "Snapshot ajustado apos revolta bem-sucedida")
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel ajustar snapshot apos revolta: {exc}")

    def _schedule_snapshot_refresh(self, *, job_id: str, game_account_id: str) -> None:
        try:
            self.ensure_status_refresh(
                job_id,
                game_account_id=game_account_id,
                delay_seconds=15,
                message="Check Status agendado para reconciliar o snapshot",
            )
        except LoginCooldownActive:
            self.log(job_id, "warn", "Check Status pos-revolta nao agendado: login em cooldown")
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel agendar Check Status apos revolta: {exc}")

    @staticmethod
    def _extract_action_request(text: str) -> str:
        for pattern in (
            r'"actionRequest"\s*:\s*"([a-f0-9]{32})"',
            r"actionRequest.*?([a-f0-9]{32})",
        ):
            match = re.search(pattern, text or "", re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1)
        return ""

    def _resolve_revolt_href(
        self,
        *,
        session,
        url: str,
        city_id: str,
        action_request: str,
        revolt_function: str,
    ) -> tuple[str, str]:
        resp = session.get(
            url,
            params={
                "view": "cityMilitary",
                "cityId": city_id,
                "currentCityId": city_id,
                "backgroundView": "city",
                "actionRequest": action_request,
                "ajax": "1",
            },
            timeout=30,
        )

        new_action_request = self._extract_action_request(resp.text) or action_request

        html = ""
        try:
            data = resp.json()
            for entry in data:
                if not isinstance(entry, list) or len(entry) < 2:
                    continue
                items = entry[1]
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, str) and len(item) > 500:
                        html = item
                        break
                if html:
                    break
        except Exception:
            html = resp.text or ""

        href_match = re.search(
            rf'href="([^"]*function={re.escape(revolt_function)}[^"]*)"',
            html,
            re.IGNORECASE,
        )
        return (href_match.group(1) if href_match else ""), new_action_request

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
        explicit_error = False
        try:
            revolt_href, action_request = self._resolve_revolt_href(
                session=session,
                url=url,
                city_id=city_id,
                action_request=action_request,
                revolt_function=revolt_function,
            )
            client._action_request = action_request

            if not revolt_href:
                msg = (
                    "O porto nao esta ocupado segundo a pagina do jogo."
                    if revolt_type == "ships"
                    else "Esta cidade nao esta ocupada segundo a pagina do jogo."
                )
                self.log(jid, "error", msg)
                self.save_game_client(game_account_id, client)
                return RunnerResult(success=False, data={"error": "revolt_link_not_available"})

            request_params = {k: values[0] for k, values in parse_qs(revolt_href.lstrip("?")).items()}
            request_params["actionRequest"] = client._action_request or request_params.get("actionRequest", "")
            request_params["ajax"] = "1"
            request_params["currentCityId"] = city_id
            request_params["backgroundView"] = "city"
            request_params["templateView"] = "cityMilitary"
            request_params["currentTab"] = "tabUnits"

            resp = session.post(url, data=request_params, headers=headers, timeout=20)

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
                            explicit_error = True
                            self.log(jid, "error", f"Revolta falhou: {feedback_text}")
            except Exception as parse_exc:
                logger.debug("Revolt parse error: %s", parse_exc)

            if explicit_error:
                self.save_game_client(game_account_id, client)
                return RunnerResult(success=False, data={"error": "game_rejected_revolt"})

            if not ok:
                self.log(
                    jid,
                    "warn",
                    f"Revolta ({type_label}) sem confirmacao explicita do jogo para {city_name}",
                )
                self.save_game_client(game_account_id, client)
                return RunnerResult(success=False, data={"error": "revolt_unconfirmed"})

        except Exception as exc:
            self.log(jid, "error", f"Revolta falhou: {exc}")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=False, data={"error": str(exc)})

        self.save_game_client(game_account_id, client)
        if game_account_id:
            self._patch_snapshot_after_success(
                job_id=jid,
                account_id=aid,
                game_account_id=game_account_id,
                city_id=city_id,
                revolt_type=revolt_type,
            )
            self._schedule_snapshot_refresh(job_id=jid, game_account_id=game_account_id)
        return RunnerResult(
            success=ok,
            data={"city_id": city_id, "city_name": city_name, "revolt_type": revolt_type},
        )
