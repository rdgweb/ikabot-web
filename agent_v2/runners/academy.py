"""Academy runners: crystal experiments and scientist allocation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from services.resource_transport import change_current_city

logger = logging.getLogger(__name__)


def _to_int(raw: Any, default: int = 0, minimum: int | None = None) -> int:
    try:
        value = int(float(raw))
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _to_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "y", "yes", "sim", "on"}


def _duration_human(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes, rem = divmod(seconds, 60)
        return f"{minutes}min" if rem == 0 else f"{minutes}min {rem}s"
    if seconds < 86400:
        hours, rem = divmod(seconds, 3600)
        minutes = rem // 60
        return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}min"
    days, rem = divmod(seconds, 86400)
    hours = rem // 3600
    return f"{days}d" if hours == 0 else f"{days}d {hours}h"


class _AcademySnapshotMixin(BaseRunner):
    def _get_snapshot(self, job_id: str, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel obter snapshot da academia: {exc}")
            return None

    def _academy_city_map(self, snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        city_map: dict[str, dict[str, Any]] = {}
        for city in (snapshot or {}).get("cities") or []:
            city_id = str(city.get("id") or "").strip()
            if not city_id:
                continue
            academy = None
            for building in city.get("buildings") or []:
                if str(building.get("building") or "").strip() == "academy":
                    academy = dict(building)
                    break
            if academy:
                city_copy = dict(city)
                city_copy["_academy"] = academy
                city_map[city_id] = city_copy
        return city_map

    def _persist_academy_state(
        self,
        *,
        job_id: str,
        account_id: str,
        game_account_id: str,
        snapshot: dict[str, Any] | None,
        city_id: int,
        state: dict[str, Any],
    ) -> None:
        try:
            base_snapshot = dict((snapshot or {}).get("base_snapshot") or {})
            cities = [dict(city) for city in ((snapshot or {}).get("cities") or [])]
            military = (snapshot or {}).get("military") or {}
            now_iso = datetime.now(timezone.utc).isoformat()
            academy_cities = dict(base_snapshot.get("academy_cities") or {})
            academy_cities[str(city_id)] = {
                "city_id": int(state.get("city_id") or city_id),
                "city_name": str(state.get("city_name") or city_id),
                "scientists": dict(state.get("scientists") or {}),
                "experiment": dict(state.get("experiment") or {}),
                "resources": dict(state.get("resources") or {}),
                "updated_at": str(state.get("updated_at") or now_iso),
            }
            base_snapshot["academy_cities"] = academy_cities
            base_snapshot["academy_updated_at"] = now_iso

            for city in cities:
                if str(city.get("id") or "") != str(city_id):
                    continue
                city["academy_state"] = {
                    "scientists": dict(state.get("scientists") or {}),
                    "experiment": dict(state.get("experiment") or {}),
                    "resources": dict(state.get("resources") or {}),
                    "updated_at": str(state.get("updated_at") or now_iso),
                }
                city["crystal"] = _to_int((state.get("resources") or {}).get("crystal"), _to_int(city.get("crystal"), 0))
                break

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
            self.log(job_id, "warn", f"Nao foi possivel persistir estado da academia: {exc}")


@register_runner(26)
class BuyResearchRunner(_AcademySnapshotMixin):
    """Run one academy crystal experiment."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        city_id = _to_int(inputs.get("city") or inputs.get("city_id"), 0, 0)
        if not city_id:
            self.log(jid, "error", "Cidade da academia nao informada")
            return RunnerResult(success=False, data={"error": "missing_city"})

        use_scroll = _to_bool(inputs.get("use_athena_scroll"), False)
        pay_with_ambrosia = _to_bool(inputs.get("pay_with_ambrosia"), False)

        snapshot = self._get_snapshot(jid, ga_id)
        city_map = self._academy_city_map(snapshot)
        city = city_map.get(str(city_id))
        if not city:
            self.log(jid, "error", "Academia nao encontrada na cidade selecionada")
            return RunnerResult(success=False, data={"error": "academy_not_found"})

        position = _to_int((city.get("_academy") or {}).get("position"), 0, 0)
        if not position:
            return RunnerResult(success=False, data={"error": "academy_position_missing"})

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            change_current_city(client, city_id)
            before = client.get_academy_state(city_id, position)
            self._persist_academy_state(
                job_id=jid,
                account_id=aid,
                game_account_id=ga_id,
                snapshot=snapshot,
                city_id=city_id,
                state=before,
            )

            city_name = str(before.get("city_name") or city.get("name") or city_id)
            experiment = dict(before.get("experiment") or {})
            resources = dict(before.get("resources") or {})
            crystal = _to_int(resources.get("crystal"), _to_int(city.get("crystal"), 0))
            crystal_cost = _to_int(
                experiment.get("discounted_crystal_cost") or experiment.get("crystal_cost"),
                0,
                0,
            )
            points_gain = _to_int(experiment.get("research_points_gain"), 0, 0)
            reason = str(experiment.get("reason") or "").strip()

            self.log(
                jid,
                "info",
                (
                    f"Academia: cidade={city_name} | cristal={crystal} | "
                    f"ensaio={points_gain} RP | custo={crystal_cost} | status={reason or '-'}"
                ),
            )

            if not experiment.get("available"):
                self.log(jid, "warn", f"Ensaio indisponivel: cidade={city_name} | motivo={reason or 'Experimento indisponivel'}")
                return RunnerResult(
                    success=False,
                    data={
                        "error": "experiment_unavailable",
                        "city_id": city_id,
                        "city_name": city_name,
                        "reason": reason or "Experimento indisponivel",
                        "cooldown_seconds": _to_int(experiment.get("cooldown_seconds"), 0, 0),
                    },
                )

            if crystal_cost > 0 and crystal < crystal_cost and not pay_with_ambrosia:
                self.log(jid, "warn", f"Cristal insuficiente para ensaio: cidade={city_name} | disponivel={crystal} | custo={crystal_cost}")
                return RunnerResult(
                    success=False,
                    data={
                        "error": "insufficient_crystal",
                        "city_id": city_id,
                        "city_name": city_name,
                        "crystal": crystal,
                        "crystal_cost": crystal_cost,
                    },
                )

            if use_scroll and not experiment.get("use_scroll_available"):
                self.log(jid, "warn", f"Pergaminho de Atena indisponivel em {city_name}")
                return RunnerResult(success=False, data={"error": "athena_scroll_unavailable"})

            client.buy_research(
                city_id,
                position,
                use_athena_scroll=use_scroll,
                pay_with_ambrosia=pay_with_ambrosia,
            )
            after = client.get_academy_state(city_id, position)
            self._persist_academy_state(
                job_id=jid,
                account_id=aid,
                game_account_id=ga_id,
                snapshot=snapshot,
                city_id=city_id,
                state=after,
            )
            self.save_game_client(ga_id, client)

            after_crystal = _to_int((after.get("resources") or {}).get("crystal"), crystal, 0)
            self.log(
                jid,
                "info",
                (
                    f"Ensaio concluido: cidade={city_name} | ganho={points_gain} RP | "
                    f"cristal_antes={crystal} | cristal_depois={after_crystal}"
                ),
            )
            return RunnerResult(
                success=True,
                data={
                    "city_id": city_id,
                    "city_name": city_name,
                    "research_points_gain": points_gain,
                    "crystal_before": crystal,
                    "crystal_after": after_crystal,
                    "use_athena_scroll": use_scroll,
                    "pay_with_ambrosia": pay_with_ambrosia,
                },
            )
        except Exception as exc:
            logger.exception("BuyResearchRunner failed for job %s", jid)
            self.log(jid, "error", f"Falha ao conduzir ensaio: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(27)
class AdjustScientistsRunner(_AcademySnapshotMixin):
    """Set scientists in one or more academies."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        snapshot = self._get_snapshot(jid, ga_id)
        city_map = self._academy_city_map(snapshot)
        requested_ids = [
            str(value).strip()
            for value in (inputs.get("cities") or [])
            if str(value).strip()
        ]
        if not requested_ids:
            fallback_city = str(inputs.get("city_id") or inputs.get("city") or "").strip()
            if fallback_city:
                requested_ids = [fallback_city]
        selected_ids = requested_ids or sorted(city_map.keys())
        targets = [city_map[city_id] for city_id in selected_ids if city_id in city_map]
        if not targets:
            self.log(jid, "error", "Nenhuma cidade com academia foi selecionada")
            return RunnerResult(success=False, data={"error": "academy_city_not_found"})

        target_mode = str(inputs.get("target_mode") or "absolute").strip().lower()
        target_value = _to_int(inputs.get("target_value"), 0, 0)
        reserve_citizens = _to_int(inputs.get("reserve_citizens"), 0, 0)

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        changed = []
        skipped = []
        failed = []

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            for city in targets:
                city_id = _to_int(city.get("id"), 0, 0)
                position = _to_int((city.get("_academy") or {}).get("position"), 0, 0)
                city_name = str(city.get("name") or city_id)
                if not city_id or not position:
                    failed.append({"city_id": city_id, "city_name": city_name, "reason": "academy_position_missing"})
                    continue

                try:
                    change_current_city(client, city_id)
                    before = client.get_academy_state(city_id, position)
                    self._persist_academy_state(
                        job_id=jid,
                        account_id=aid,
                        game_account_id=ga_id,
                        snapshot=snapshot,
                        city_id=city_id,
                        state=before,
                    )

                    scientists = dict(before.get("scientists") or {})
                    current = _to_int(scientists.get("current"), 0, 0)
                    max_value = _to_int(scientists.get("max"), current, 0)
                    citizens = _to_int(scientists.get("citizens"), 0, 0)
                    free_after_reserve = max(0, citizens - reserve_citizens)

                    if target_mode == "percent_max":
                        desired = round(max_value * (target_value / 100.0))
                    else:
                        desired = target_value
                    desired = max(0, min(max_value, desired, current + free_after_reserve))

                    self.log(
                        jid,
                        "info",
                        f"Cientistas: cidade={city_name} | atual={current} | alvo={desired} | max={max_value} | cidadaos={citizens}",
                    )

                    if desired == current:
                        skipped.append({"city_id": city_id, "city_name": city_name, "scientists": current})
                        continue

                    client.set_scientists(city_id, position, desired)
                    after = client.get_academy_state(city_id, position)
                    self._persist_academy_state(
                        job_id=jid,
                        account_id=aid,
                        game_account_id=ga_id,
                        snapshot=snapshot,
                        city_id=city_id,
                        state=after,
                    )
                    final_value = _to_int(((after.get("scientists") or {}).get("current")), desired, 0)
                    changed.append(
                        {
                            "city_id": city_id,
                            "city_name": city_name,
                            "before": current,
                            "after": final_value,
                            "target": desired,
                        }
                    )
                    self.log(
                        jid,
                        "info",
                        f"Cientistas ajustados: cidade={city_name} | antes={current} | depois={final_value}",
                    )
                except Exception as exc:
                    failed.append({"city_id": city_id, "city_name": city_name, "reason": str(exc)})
                    self.log(jid, "warn", f"Falha ao ajustar cientistas em {city_name}: {exc}")

            self.save_game_client(ga_id, client)
            return RunnerResult(
                success=bool(changed or skipped) and not failed,
                data={
                    "target_mode": target_mode,
                    "target_value": target_value,
                    "reserve_citizens": reserve_citizens,
                    "changed": changed,
                    "skipped": skipped,
                    "failed": failed,
                },
            )
        except Exception as exc:
            logger.exception("AdjustScientistsRunner failed for job %s", jid)
            self.log(jid, "error", f"Falha ao ajustar cientistas: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
