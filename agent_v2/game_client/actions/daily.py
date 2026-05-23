"""Daily login and daily tasks actions."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction


class DailyTasksAction(BaseAction):
    """Interact with the daily login bonus and daily tasks screens."""

    def get_state(self, *, city_id: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "view": "dailyTasks",
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("GET", self.client._server_url, params=params)
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError("Invalid dailyTasks response", action="daily_tasks_state") from exc
        return self._parse_daily_payload(payload)

    def collect_daily_bonus(self, *, city_id: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "action": "AvatarAction",
            "function": "giveDailyActivityBonus",
            "dailyActivityBonusCitySelect": str(city_id),
            "startPageShown": "1",
            "detectedDevice": "1",
            "autoLogin": "on",
            "cityId": str(city_id),
            "activeTab": "multiTab2",
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        return self._parse_generic_payload(resp)

    def collect_task_favor(self, *, city_id: int, task_id: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "action": "CollectDailyTasksFavor",
            "taskId": str(task_id),
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "dailyTasks",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        return self._parse_generic_payload(resp)

    def collect_ambrosia_fountain(self, *, city_id: int, **kwargs: Any) -> dict[str, Any]:
        params = {
            "action": "AmbrosiaFountainActions",
            "function": "collect",
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "ambrosiaFountain",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        return self._parse_generic_payload(resp)

    def get_city_overview(self, *, city_id: int, **kwargs: Any) -> dict[str, Any]:
        resp = self.client._request("GET", self.client._server_url, params={"view": "city", "cityId": str(city_id)})
        html = resp.text
        return {
            "ambrosia_fountain_active": '"ambrosiaFountain":"fountain_active"' in html or 'class="fountain_active' in html,
            "city_cinema_active": '"cityCinema":"active"' in html or 'id="cityCinema"' in html,
            "html": html,
        }

    def activate_vacation_mode(self, *, city_id: int, **kwargs: Any) -> dict[str, Any]:
        """Activate vacation mode via action=Options&function=activateVacationMode."""
        params = {
            "action": "Options",
            "function": "activateVacationMode",
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "templateView": "options_umod_confirm",
            "actionRequest": self.client._action_request,
            "ajax": "1",
        }
        resp = self.client._request("POST", self.client._server_url, data=params, headers=GAME_AJAX_HEADERS)
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError("Invalid vacation mode response", action="activateVacationMode") from exc

        parsed = self.client.ajax_parser.parse_response(payload)
        token = parsed.get("new_action_request")
        if token:
            self.client._action_request = token

        if parsed.get("reload"):
            raise ActionError(
                "Server rejected activateVacationMode with reload directive",
                action="activateVacationMode",
            )
        if parsed["errors"]:
            raise ActionError(
                f"Server errors for activateVacationMode: {parsed['errors']}",
                action="activateVacationMode",
                server_errors=parsed["errors"],
            )

        return {
            "ok": bool(
                parsed.get("success_feedback")
                or parsed.get("updates")
                or parsed.get("background")
                or parsed.get("redirect")
            ),
            "parsed": parsed,
        }

    @staticmethod
    def _normalize_payload(payload: Any) -> list[Any]:
        if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], list):
            first = payload[0]
            if first and isinstance(first[0], list):
                return first
        return payload if isinstance(payload, list) else []

    def _parse_generic_payload(self, resp) -> dict[str, Any]:
        try:
            payload = resp.json()
        except Exception:
            return {"ok": True}
        updates = self._normalize_payload(payload)
        for item in updates:
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token
        return {"ok": True, "payload": updates}

    def _parse_daily_payload(self, payload: Any) -> dict[str, Any]:
        updates = self._normalize_payload(payload)
        template_data: dict[str, Any] = {}
        html = ""
        global_data: dict[str, Any] = {}

        for item in updates:
            if not isinstance(item, list) or len(item) < 2:
                continue
            name = item[0]
            data = item[1]
            if name == "updateTemplateData" and isinstance(data, dict):
                template_data = data
            elif name == "changeView" and isinstance(data, list) and len(data) >= 2 and data[0] == "dailyTasks":
                html = str(data[1] or "")
            elif name == "updateGlobalData" and isinstance(data, dict):
                global_data = data
                token = str(data.get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token

        if not template_data:
            raise ActionError("Daily tasks template data missing", action="daily_tasks_state")

        current_favor = int(template_data.get("currentFavor") or 0)
        favor_limit = int(template_data.get("favorLimit") or 2500)
        tasks_done = int(template_data.get("tasksDone") or 0)
        tasks_count = int(template_data.get("tasksCount") or 0)

        countdown = (template_data.get("dailyTasksCountdown") or {}).get("countdown") or {}
        enddate = int(countdown.get("enddate") or 0)
        currentdate = int(countdown.get("currentdate") or 0)
        countdown_seconds = max(0, enddate - currentdate) if enddate and currentdate else 0

        background_data = global_data.get("backgroundData") or {}
        city_name = str(background_data.get("name") or "")
        city_id = int(background_data.get("id") or 0)
        fountain_active = str(background_data.get("ambrosiaFountain") or "").strip() == "fountain_active"
        city_cinema_active = bool(background_data.get("cityCinema"))

        task_meta: dict[int, dict[str, Any]] = {}
        normalized_html = html.replace('\\"', '"').replace("\\/", "/")
        if normalized_html:
            for match in re.finditer(r'<tr id="task_row_(\d+)"([\s\S]*?)</tr>', normalized_html):
                task_id = int(match.group(1))
                row_html = match.group(0)
                name_match = re.search(r'class="left taskName">\s*([\s\S]*?)<', row_html)
                current_match = re.search(rf'id="task_amount_{task_id}">\s*([\s\S]*?)<', row_html)
                target_matches = re.findall(r'class="left small progress details">\s*([\s\S]*?)<', row_html)
                target_raw = target_matches[1] if len(target_matches) >= 2 else (target_matches[0] if target_matches else "0")
                reward_match = re.search(r'data-task-favor="(\d+)"', row_html)
                difficulty_match = re.search(r'dailyTaskDifficulty([A-Za-z]+)', row_html)
                task_meta[task_id] = {
                    "task_id": task_id,
                    "name": self._clean_html_text(name_match.group(1) if name_match else ""),
                    "current": self._to_int_text(current_match.group(1) if current_match else "0"),
                    "target": self._to_int_text(target_raw),
                    "reward_favor": self._to_int_text(reward_match.group(1) if reward_match else "0"),
                    "difficulty": difficulty_match.group(1).lower() if difficulty_match else "",
                }

        tasks: list[dict[str, Any]] = []
        collectible_count = 0
        for key, value in template_data.items():
            match = re.match(r"task_row_(\d+) \.taskName$", str(key))
            if not match:
                continue
            task_id = int(match.group(1))
            css_class = str((template_data.get(f"task_row_{task_id}") or {}).get("class") or "")
            favor_button_display = (((template_data.get(f"task_row_{task_id} .favorWithButton") or {}).get("style")) or {}).get("display", "")
            collectible = favor_button_display == "table-cell"
            claimed = "textLineThrough" in css_class
            meta = task_meta.get(task_id, {"task_id": task_id})
            task_name = self._clean_html_text(value)
            if task_name:
                meta["name"] = task_name
            current_raw = template_data.get(f"task_amount_{task_id}")
            if current_raw not in (None, ""):
                meta["current"] = self._to_int_text(str(current_raw))
            task = {
                **meta,
                "collectible": collectible,
                "claimed": claimed,
                "progress_label": f"{meta.get('current', 0)}/{meta.get('target', 0)}" if meta.get("target", 0) else "",
            }
            tasks.append(task)
            if collectible:
                collectible_count += 1

        tasks.sort(key=lambda item: (not item.get("collectible", False), item.get("task_id", 0)))
        return {
            "city_id": city_id,
            "city_name": city_name,
            "current_favor": current_favor,
            "favor_limit": favor_limit,
            "tasks_done": tasks_done,
            "tasks_count": tasks_count,
            "countdown_seconds": countdown_seconds,
            "countdown_end_at": datetime.fromtimestamp(enddate, tz=timezone.utc).isoformat() if enddate else "",
            "collectible_tasks_count": collectible_count,
            "tasks": tasks,
            "ambrosia_fountain_active": fountain_active,
            "city_cinema_active": city_cinema_active,
        }

    @staticmethod
    def _clean_html_text(raw: str) -> str:
        text = re.sub(r"<[^>]+>", " ", str(raw or ""))
        text = text.replace("&nbsp;", " ").replace("\\/", "/").replace("\\n", " ").replace("\\", "")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _to_int_text(raw: str) -> int:
        text = DailyTasksAction._clean_html_text(raw)
        digits = re.sub(r"[^\d]", "", text)
        return int(digits or "0")
