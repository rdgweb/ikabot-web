"""
Piracy runners — action 17 (PiracyMissionRunner) and 1018 (CollectPiracyRunner).

Action codes:
    17    piracy_mission   (recurring)
    1018  collect_piracy   (legacy/internal recurring helper)
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from typing import Any

from core.runner_registry import register_runner
from game_client.exceptions import CaptchaRequiredError
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

# Mission duration map: buildingLevel → duration in seconds
_MISSION_DURATIONS: dict[int, int] = {
    1: 150,
    3: 450,
    5: 900,
    7: 1800,
    9: 3600,
    11: 7200,
    13: 14400,
    15: 28800,
    17: 57600,
}

CAPTCHA_RESCHEDULE = 5 * 60       # 5 min — best-effort captcha handling
ERROR_RESCHEDULE = 10 * 60        # 10 min — generic error back-off
MISSION_BUFFER = 60               # seconds of extra buffer after mission completes
COLLECT_INTERVAL = 8 * 3600       # 8 h — legacy collect runner interval


def _mission_level_for_time(inputs: dict, now_hour: int) -> int:
    """Return mission level based on current hour and day/night configuration."""
    try:
        day_start = int(inputs.get("day_start_hour") or 8)
        day_end   = int(inputs.get("day_end_hour")   or 22)
        day_level = int(inputs.get("day_mission_level") or 7)
        night_level = int(inputs.get("night_mission_level") or 13)
    except (ValueError, TypeError):
        return 7

    if day_start <= day_end:
        is_day = day_start <= now_hour < day_end
    else:
        # wraps midnight (e.g. day_start=22, day_end=8)
        is_day = now_hour >= day_start or now_hour < day_end

    return day_level if is_day else night_level


@register_runner(17)
class PiracyMissionRunner(BaseRunner):
    """Send crew on a piracy mission from the pirate fortress.

    Recurring — reschedules to keep piracy running continuously.
    """

    def _select_mission_by_time(self, inputs: dict) -> int:
        return _mission_level_for_time(inputs, datetime.now().hour)

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        game_account_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        city_id = str(inputs.get("city_id") or "").strip()
        if not city_id:
            self.log(jid, "error", "city_id obrigatorio para missao pirata")
            return RunnerResult(success=False, data={"error": "city_id_missing"})

        # Mission level: schedule_by_time overrides simple mission_level
        schedule_by_time = bool(inputs.get("schedule_by_time") or False)
        if schedule_by_time:
            mission_level = self._select_mission_by_time(inputs)
        else:
            try:
                mission_level = int(inputs.get("mission_level") or 7)
            except (ValueError, TypeError):
                mission_level = 7

        # Random wait before starting (anti-detection)
        try:
            max_random_wait = int(inputs.get("max_random_wait") or 0)
        except (ValueError, TypeError):
            max_random_wait = 0

        effective_level = mission_level  # may be updated inside try block
        convert_mode = str(inputs.get("convert_mode") or "all").strip().lower()
        try:
            convert_threshold = int(inputs.get("convert_threshold") or 0)
        except (ValueError, TypeError):
            convert_threshold = 0
        try:
            convert_percent = max(1, min(100, int(inputs.get("convert_percent") or 100)))
        except (ValueError, TypeError):
            convert_percent = 100

        creds = self.resolve_credentials(aid, inputs, game_account_id=game_account_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(
                success=False,
                reschedule_seconds=ERROR_RESCHEDULE,
                data={"error": "credentials_missing"},
            )

        try:
            client = self.get_or_login_game_client(jid, aid, game_account_id, creds)

            # Step 1: get current piracy state
            self.log(jid, "info", f"Lendo estado da fortaleza pirata na cidade {city_id}")
            state = client.get_piracy_state(city_id)

            time_remaining = int(state.get("time_remaining") or 0)
            fortress_level = int(state.get("fortress_level") or 1)
            capture_points = int(state.get("capture_points") or 0)

            # Step 2: mission already active — wait for it
            if time_remaining > 0:
                wait = time_remaining + MISSION_BUFFER
                self.log(
                    jid,
                    "info",
                    f"Missao pirata em andamento. Navio retorna em {time_remaining}s. "
                    f"Reagendando em {wait}s.",
                )
                self.save_game_client(game_account_id, client)
                return RunnerResult(success=True, reschedule_seconds=wait)

            # Step 3: ship in port — optionally convert, then start new mission
            # a) convert capture points to crew based on mode
            if convert_mode == "all":
                should_convert = capture_points > 0
                points_to_convert = capture_points
            elif convert_mode == "percent":
                should_convert = capture_points > 0
                points_to_convert = int(capture_points * convert_percent / 100)
            elif convert_mode == "threshold":
                should_convert = capture_points >= max(convert_threshold, 1)
                points_to_convert = capture_points
            else:
                should_convert = False
                points_to_convert = 0

            if should_convert and points_to_convert > 0:
                conversion_factor = int(state.get("conversion_factor") or 10)
                crew_to_create = points_to_convert // conversion_factor
                if crew_to_create > 0:
                    self.log(
                        jid,
                        "info",
                        f"Convertendo {capture_points} pontos de captura em "
                        f"{crew_to_create} de forca da tripulacao",
                    )
                    try:
                        client.convert_piracy_points(city_id, crew_to_create)
                        self.log(jid, "info", "Pontos de captura convertidos com sucesso")
                    except CaptchaRequiredError:
                        self.log(jid, "warn", "Captcha necessario na conversao de pontos. Reagendando em 5 min.")
                        self.save_game_client(game_account_id, client)
                        return RunnerResult(
                            success=False,
                            reschedule_seconds=CAPTCHA_RESCHEDULE,
                            data={"error": "captcha_convert"},
                        )
                    except Exception as exc:
                        self.log(jid, "warn", f"Conversao de pontos falhou (continuando): {exc}")

            # b) cap mission_level to what the fortress supports
            effective_level = mission_level
            if effective_level > fortress_level:
                # find the highest available level that doesn't exceed fortress_level
                available = sorted(
                    (lvl for lvl in _MISSION_DURATIONS if lvl <= fortress_level),
                    reverse=True,
                )
                effective_level = available[0] if available else 1
                self.log(
                    jid,
                    "info",
                    f"Nível da missão ajustado de {mission_level} para {effective_level} "
                    f"(fortaleza nível {fortress_level})",
                )

            # c) optional random wait before mission (anti-detection)
            if max_random_wait > 0:
                wait_secs = random.randint(0, max_random_wait)
                if wait_secs > 0:
                    self.log(jid, "info", f"Aguardando {wait_secs}s antes de iniciar (espera aleatória)")
                    time.sleep(wait_secs)

            # d) start the mission
            self.log(jid, "info", f"Iniciando missão pirata nível {effective_level}")
            result = client.start_piracy_mission(
                city_id,
                effective_level,
                game_account_id=game_account_id,
            )

            if not result.get("success"):
                self.log(
                    jid,
                    "warn",
                    f"Missão pirata não foi confirmada pelo jogo: "
                    f"{result.get('message') or 'mission_not_confirmed'}",
                )
                self.save_game_client(game_account_id, client)
                return RunnerResult(
                    success=False,
                    reschedule_seconds=ERROR_RESCHEDULE,
                    data={"error": result.get("message") or "mission_not_confirmed"},
                )

            # d) compute reschedule from actual time_remaining returned or mission duration
            new_time_remaining = int(result.get("time_remaining") or 0)
            if new_time_remaining > 0:
                wait = new_time_remaining + MISSION_BUFFER
            else:
                wait = _MISSION_DURATIONS.get(effective_level, 1800) + MISSION_BUFFER

            mission_msg = result.get("message") or "ok"
            self.log(
                jid,
                "info",
                f"Missão pirata iniciada (nível {effective_level}). "
                f"Reagendando em {wait}s. Resposta: {mission_msg}",
            )

            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=wait)

        except CaptchaRequiredError as exc:
            # CaptchaRequiredError from state GET or other non-mission request.
            # Try to fetch and solve the captcha before rescheduling.
            self.log(jid, "warn", f"Captcha detectado fora do flow da missão: {exc}. Tentando resolver via hub.")
            try:
                import base64 as _b64
                from game_client.constants import GAME_AJAX_HEADERS
                img_resp = client.session.get(
                    client._server_url,
                    params={
                        "action": "Options",
                        "function": "createCaptcha",
                        "actionRequest": client._action_request,
                        "ajax": "1",
                    },
                    headers=dict(GAME_AJAX_HEADERS),
                    timeout=20,
                )
                if img_resp.content and len(img_resp.content) > 10:
                    img_b64 = _b64.b64encode(img_resp.content).decode("ascii")
                    result = self.hub.create_captcha_challenge("pirate", img_b64, game_account_id=game_account_id)
                    solution = str(result.get("solution") or "").strip().upper()
                    if not solution and result.get("challenge_id"):
                        solution = self.hub.poll_captcha_solution(result["challenge_id"], timeout_sec=120, interval=10).strip().upper()
                    if solution:
                        self.log(jid, "info", f"Captcha resolvido via hub: {solution}. Reagendando imediatamente.")
                        self.save_game_client(game_account_id, client)
                        return RunnerResult(success=False, reschedule_seconds=30, data={"error": "captcha_solved_retry"})
                    self.log(jid, "warn", "Captcha não resolvido (sem solução). Reagendando em 5 min.")
                else:
                    self.log(jid, "warn", "Imagem do captcha vazia. Reagendando em 5 min.")
            except Exception as cap_exc:
                self.log(jid, "warn", f"Erro ao resolver captcha externo: {cap_exc}. Reagendando em 5 min.")
            self.save_game_client(game_account_id, client)
            return RunnerResult(success=False, reschedule_seconds=CAPTCHA_RESCHEDULE, data={"error": "captcha_unexpected"})
        except Exception as exc:
            self.log(jid, "error", f"Missão pirata falhou: {exc}")
            return RunnerResult(
                success=False,
                reschedule_seconds=ERROR_RESCHEDULE,
                data={"error": str(exc)},
            )


@register_runner(1018)
class CollectPiracyRunner(BaseRunner):
    """Collect loot from completed piracy missions.

    Recurring — reschedules to periodically sweep piracy rewards.

    Inputs:
        city_id — city with the pirate fortress
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        game_account_id = job.get("game_account_id", "")
        inputs = job.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}

        city_id = str(inputs.get("city_id") or "").strip()

        self.log(jid, "info", f"Verificando loot pirata para conta {aid}")

        creds = self.resolve_credentials(aid, inputs, game_account_id=game_account_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(
                success=False,
                reschedule_seconds=COLLECT_INTERVAL,
                data={"error": "credentials_missing"},
            )

        try:
            client = self.get_or_login_game_client(jid, aid, game_account_id, creds)

            if city_id:
                state = client.get_piracy_state(city_id)
                capture_points = int(state.get("capture_points") or 0)
                time_remaining = int(state.get("time_remaining") or 0)
                self.log(
                    jid,
                    "info",
                    f"Fortaleza pirata — pontos: {capture_points}, "
                    f"missao ativa: {time_remaining > 0} ({time_remaining}s restantes)",
                )
            else:
                self.log(jid, "info", "city_id nao fornecido — nada a coletar")

            self.save_game_client(game_account_id, client)
            return RunnerResult(success=True, reschedule_seconds=COLLECT_INTERVAL)

        except Exception as exc:
            self.log(jid, "error", f"Coleta pirata falhou: {exc}")
            return RunnerResult(
                success=False,
                reschedule_seconds=COLLECT_INTERVAL,
                data={"error": str(exc)},
            )
