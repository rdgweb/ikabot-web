"""
Piracy runners — action 17 (PiracyMissionRunner) and 1018 (CollectPiracyRunner).

Action codes:
    17    piracy_mission   (recurring)
    1018  collect_piracy   (legacy/internal recurring helper)
"""

from __future__ import annotations

import logging
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


@register_runner(17)
class PiracyMissionRunner(BaseRunner):
    """Send crew on a piracy mission from the pirate fortress.

    Recurring — reschedules to keep piracy running continuously.

    Inputs:
        city_id       (required) — ID of city with pirate fortress
        mission_level (int, default=7) — buildingLevel of mission to run
                      (1,3,5,7,9,11,13,15,17). Capped to fortress level.
        auto_convert  (bool, default=True) — convert capture points to crew
                      after each mission completes
    """

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

        try:
            mission_level = int(inputs.get("mission_level") or 7)
        except (ValueError, TypeError):
            mission_level = 7

        auto_convert = inputs.get("auto_convert")
        if auto_convert is None:
            auto_convert = True
        elif isinstance(auto_convert, str):
            auto_convert = auto_convert.lower() not in ("false", "0", "no", "")
        else:
            auto_convert = bool(auto_convert)

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
            # a) auto-convert capture points to crew
            if auto_convert and capture_points > 0:
                conversion_factor = int(state.get("conversion_factor") or 10)
                crew_to_create = capture_points // conversion_factor
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

            # c) start the mission
            self.log(jid, "info", f"Iniciando missão pirata nível {effective_level}")
            result = client.start_piracy_mission(city_id, effective_level)

            if not result.get("success"):
                msg = result.get("message") or "Sem resposta de sucesso do jogo"
                self.log(jid, "warn", f"Missão pirata pode ter falhado: {msg}")

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
            self.log(
                jid,
                "warn",
                f"Captcha necessário na missão pirata. Reagendando em 5 min. ({exc})",
            )
            return RunnerResult(
                success=False,
                reschedule_seconds=CAPTCHA_RESCHEDULE,
                data={"error": "captcha_required"},
            )
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
