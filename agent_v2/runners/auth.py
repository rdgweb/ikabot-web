"""Authentication runners — login and daily-login bonus."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

DAILY_LOGIN_INTERVAL = 24 * 3600


@register_runner(1)
class LoginRunner(BaseRunner):
    """Authenticate an account and cache the session.

    Inputs:
        server   — game server hostname (e.g. ``s1-br.ikariam.gameforge.com``)
        email    — account e-mail
        password — account password
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Starting login for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.login(server, email, password)
            # server   = inputs["server"]
            # email    = inputs["email"]
            # password = inputs["password"]
            # client.login(server, email, password)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Login successful")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Login failed: {exc}")
            self.sessions.invalidate(aid)
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(6)
class DailyLoginRunner(BaseRunner):
    """Collect daily login bonus, daily-task favor and ambrosia fountain."""

    def _get_snapshot(self, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception:
            return None

    def _city_name_from_snapshot(self, snapshot: dict[str, Any] | None, city_id: int) -> str:
        for city in (snapshot or {}).get("cities") or []:
            if str(city.get("id") or "") == str(city_id):
                return str(city.get("name") or city_id)
        return str(city_id)

    def _persist_daily_state(
        self,
        *,
        job_id: str,
        account_id: str,
        game_account_id: str,
        snapshot: dict[str, Any] | None,
        daily_state: dict[str, Any],
        bonus_city_id: int,
        bonus_city_name: str,
        collect_favor: bool,
        collect_fountain: bool,
        collected_task_ids: list[int],
        fountain_collected: bool,
        next_delay: int,
    ) -> None:
        if not snapshot:
            return
        try:
            base_snapshot = dict(snapshot.get("base_snapshot") or {})
            cities = [dict(city) for city in (snapshot.get("cities") or [])]
            military = snapshot.get("military") or {}
            now_iso = datetime.now(timezone.utc).isoformat()
            daily_payload = {
                "bonus_city_id": bonus_city_id,
                "bonus_city_name": bonus_city_name,
                "current_favor": int(daily_state.get("current_favor") or 0),
                "favor_limit": int(daily_state.get("favor_limit") or 2500),
                "tasks_done": int(daily_state.get("tasks_done") or 0),
                "tasks_count": int(daily_state.get("tasks_count") or 0),
                "collectible_tasks_count": int(daily_state.get("collectible_tasks_count") or 0),
                "countdown_seconds": int(daily_state.get("countdown_seconds") or 0),
                "countdown_end_at": str(daily_state.get("countdown_end_at") or ""),
                "collect_favor": bool(collect_favor),
                "collect_fountain": bool(collect_fountain),
                "collected_task_ids": list(collected_task_ids),
                "fountain_collected": bool(fountain_collected),
                "tasks": list(daily_state.get("tasks") or []),
                "updated_at": now_iso,
                "next_delay_seconds": int(next_delay or 0),
            }
            base_snapshot["daily_login_state"] = daily_payload
            base_snapshot["current_favor"] = daily_payload["current_favor"]

            for city in cities:
                if str(city.get("id") or "") == str(bonus_city_id):
                    city["daily_login_bonus_city"] = True
                if city.get("shrine_state"):
                    shrine_state = dict(city.get("shrine_state") or {})
                    shrine_state["current_favor"] = daily_payload["current_favor"]
                    shrine_state["updated_at"] = now_iso
                    city["shrine_state"] = shrine_state
                    city["current_favor"] = daily_payload["current_favor"]

            self.hub.update_snapshot(
                account_id,
                {
                    "base_snapshot": base_snapshot,
                    "cities": cities,
                    "military": military,
                    "source_job_id": job_id,
                },
                game_account_id=game_account_id,
            )
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel persistir estado do login diario: {exc}")

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        self.log(jid, "info", f"Collecting daily login bonus for account {aid}")

        try:
            if not ga_id:
                return RunnerResult(success=False, data={"error": "missing_game_account"})

            bonus_city_id = int(inputs.get("city") or inputs.get("bonus_city_id") or 0)
            if not bonus_city_id:
                return RunnerResult(success=False, data={"error": "missing_city"})

            collect_favor = bool(inputs.get("collect_favor", True))
            collect_fountain = bool(inputs.get("collect_fountain", True))
            fallback_interval_hours = max(1, int(inputs.get("fallback_interval_hours") or 24))
            reschedule_margin_minutes = max(0, int(inputs.get("reschedule_margin_minutes") or 15))

            snapshot = self._get_snapshot(ga_id)
            bonus_city_name = self._city_name_from_snapshot(snapshot, bonus_city_id)

            creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
            if not creds:
                return RunnerResult(success=False, data={"error": "missing_credentials"})

            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            before_state = client.get_daily_tasks_state(bonus_city_id)
            self.log(
                jid,
                "info",
                (
                    f"Daily tasks: cidade={before_state.get('city_name') or bonus_city_name} "
                    f"| favor={before_state.get('current_favor', 0)}/{before_state.get('favor_limit', 2500)} "
                    f"| tarefas={before_state.get('tasks_done', 0)}/{before_state.get('tasks_count', 0)} "
                    f"| coletaveis={before_state.get('collectible_tasks_count', 0)}"
                ),
            )

            client.collect_daily_login_bonus(bonus_city_id)
            self.log(jid, "info", f"Bonus diario enviado para {before_state.get('city_name') or bonus_city_name}")

            collected_task_ids: list[int] = []
            state = before_state
            if collect_favor:
                while True:
                    collectible = [task for task in (state.get("tasks") or []) if task.get("collectible")]
                    if not collectible:
                        break
                    if int(state.get("current_favor") or 0) >= int(state.get("favor_limit") or 2500):
                        self.log(jid, "info", "Favor cheio; tarefas restantes nao foram recolhidas")
                        break
                    task = collectible[0]
                    task_id = int(task.get("task_id") or 0)
                    if not task_id:
                        break
                    client.collect_daily_task_favor(bonus_city_id, task_id)
                    collected_task_ids.append(task_id)
                    self.log(jid, "info", f"Favor recolhido da task {task_id}: {task.get('name')}")
                    state = client.get_daily_tasks_state(bonus_city_id)

            fountain_collected = False
            overview = None
            if collect_fountain:
                overview = client.get_daily_city_overview(bonus_city_id)
                if overview.get("ambrosia_fountain_active"):
                    client.collect_ambrosia_fountain(bonus_city_id)
                    fountain_collected = True
                    self.log(jid, "info", "Fonte de ambrosia coletada")
                else:
                    self.log(jid, "info", "Fonte de ambrosia nao estava ativa")

            # Cineteatro: coleta e manual (ad-gate). So avisa no Telegram quando
            # disponivel, no maximo 1x por ciclo de reset (dedup por data UTC).
            if overview is None:
                overview = client.get_daily_city_overview(bonus_city_id)
            if overview.get("city_cinema_active"):
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if str(inputs.get("cinema_notified_date") or "") != today:
                    try:
                        self.hub.send_notification(
                            event="cinema_available",
                            game_account_id=ga_id,
                            account_id=aid,
                            title="Cineteatro disponivel",
                            body=(
                                f"O cineteatro tem sessao disponivel em {before_state.get('city_name') or bonus_city_name}. "
                                f"A coleta e manual no jogo (o bonus exige assistir um video)."
                            ),
                            agent_name=str(job.get("agent") or ""),
                        )
                        inputs["cinema_notified_date"] = today
                        self.log(jid, "info", "Cineteatro disponivel — notificacao Telegram enviada")
                    except Exception as exc:
                        self.log(jid, "warn", f"Falha ao notificar cineteatro: {exc}")

            final_state = client.get_daily_tasks_state(bonus_city_id)
            next_delay = int(final_state.get("countdown_seconds") or 0) + (reschedule_margin_minutes * 60)
            if next_delay <= 0:
                next_delay = fallback_interval_hours * 3600

            self._persist_daily_state(
                job_id=jid,
                account_id=aid,
                game_account_id=ga_id,
                snapshot=snapshot,
                daily_state=final_state,
                bonus_city_id=bonus_city_id,
                bonus_city_name=before_state.get("city_name") or bonus_city_name,
                collect_favor=collect_favor,
                collect_fountain=collect_fountain,
                collected_task_ids=collected_task_ids,
                fountain_collected=fountain_collected,
                next_delay=next_delay,
            )

            self.save_game_session(ga_id, client)
            self.log(
                jid,
                "info",
                (
                    f"Login diario concluido: favor={final_state.get('current_favor', 0)}/{final_state.get('favor_limit', 2500)} "
                    f"| tarefas={final_state.get('tasks_done', 0)}/{final_state.get('tasks_count', 0)} "
                    f"| coletadas={len(collected_task_ids)} | proximo_em={next_delay}s"
                ),
            )

            return RunnerResult(
                success=True,
                reschedule_seconds=max(3600, next_delay),
                reschedule_inputs=inputs,
                data={
                    "city_id": bonus_city_id,
                    "city_name": before_state.get("city_name") or bonus_city_name,
                    "current_favor": int(final_state.get("current_favor") or 0),
                    "favor_limit": int(final_state.get("favor_limit") or 2500),
                    "tasks_done": int(final_state.get("tasks_done") or 0),
                    "tasks_count": int(final_state.get("tasks_count") or 0),
                    "collectible_tasks_count": int(final_state.get("collectible_tasks_count") or 0),
                    "collected_task_ids": collected_task_ids,
                    "fountain_collected": fountain_collected,
                    "next_delay_seconds": max(3600, next_delay),
                },
            )

        except Exception as exc:
            self.log(jid, "error", f"Daily login failed: {exc}")
            return RunnerResult(
                success=True,
                reschedule_seconds=3600,
                reschedule_inputs=inputs,
                data={"error": str(exc)},
            )
