"""Economy runners that need richer game-specific flows."""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from sessions.game_session_service import LoginCooldownActive

logger = logging.getLogger(__name__)

MIN_RECHECK_SECONDS = 5 * 60

GOD_META = {
    "pan": {"id": 1, "name": "Pan"},
    "dionysus": {"id": 2, "name": "Dionisio"},
    "tyche": {"id": 3, "name": "Tique"},
    "plutus": {"id": 4, "name": "Pluto"},
    "theia": {"id": 5, "name": "Teia"},
    "hephaestus": {"id": 6, "name": "Hefesto"},
}


def _bool_label(value: Any) -> str:
    return "Sim" if bool(value) else "Nao"


def _snapshot_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        value = str(raw).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _to_int(raw: Any, default: int) -> int:
    try:
        return int(str(raw).strip())
    except Exception:
        return default


class _ShrineRunnerMixin(BaseRunner):
    def _get_snapshot(self, job_id: str, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel obter snapshot: {exc}")
            return None

    def _ensure_status_refresh(self, job_id: str, game_account_id: str | None = None) -> None:
        try:
            self.ensure_status_refresh(
                job_id,
                game_account_id=game_account_id,
                message="Check status solicitado para atualizar snapshot do santuario",
            )
        except LoginCooldownActive:
            raise
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel solicitar check_status: {exc}")

    def _persist_shrine_state(
        self,
        *,
        job_id: str,
        account_id: str,
        game_account_id: str,
        snapshot: dict[str, Any] | None,
        shrine: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        if not snapshot:
            return
        try:
            base_snapshot = dict(snapshot.get("base_snapshot") or {})
            cities = [dict(city) for city in (snapshot.get("cities") or [])]
            military = snapshot.get("military") or {}
            city_id = str(shrine.get("city_id") or "")
            now_iso = datetime.now(timezone.utc).isoformat()

            shrine_state = {
                "city_id": shrine.get("city_id"),
                "city_name": state.get("city_name") or shrine.get("city_name"),
                "position": int(state.get("position") or shrine.get("position") or 0),
                "current_favor": int(state.get("current_favor") or 0),
                "building_level": int(state.get("building_level") or shrine.get("level") or 0),
                "researched_gods": list(state.get("researched_gods") or []),
                "gods": dict(state.get("gods") or {}),
                "additional_cities_possible": int(state.get("additional_cities_possible") or 0),
                "worship_assignments": list(state.get("worship_assignments") or []),
                "updated_at": now_iso,
            }

            for city in cities:
                if str(city.get("id") or "") != city_id:
                    continue
                city["shrine_state"] = shrine_state
                city["current_favor"] = shrine_state["current_favor"]
                break

            base_snapshot["current_favor"] = shrine_state["current_favor"]
            base_snapshot["shrine_city_id"] = shrine_state["city_id"]
            base_snapshot["shrine_city_name"] = shrine_state["city_name"]
            base_snapshot["shrine_level"] = shrine_state["building_level"]
            base_snapshot["shrine_researched_gods"] = shrine_state["researched_gods"]
            base_snapshot["shrine_gods"] = shrine_state["gods"]
            base_snapshot["shrine_updated_at"] = now_iso

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
            self.log(job_id, "warn", f"Nao foi possivel persistir estado do santuario no snapshot: {exc}")

    def _prepare_context(self, job: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            self.log(jid, "error", "game_account_id ausente para ativar santuario")
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        snapshot = self._get_snapshot(jid, ga_id)
        if snapshot is None:
            self._ensure_status_refresh(jid, ga_id)
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "waiting_snapshot"})

        if self.is_snapshot_stale(snapshot):
            self.log(jid, "warn", "Snapshot antigo para santuario; solicitando refresh")
            self._ensure_status_refresh(jid, ga_id)
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "stale_snapshot"})

        shrine = self._find_shrine(snapshot)
        if shrine is None:
            self.log(jid, "error", "Nenhum Santuario dos Deuses encontrado no snapshot")
            return RunnerResult(success=False, data={"error": "shrine_not_found"})

        selected_gods = self._selected_gods(inputs)
        if not selected_gods:
            self.log(jid, "error", "Nenhum deus selecionado para o santuario")
            return RunnerResult(success=False, data={"error": "missing_gods"})

        favor_recheck_seconds = max(5 * 60, _to_int(inputs.get("favor_recheck_minutes"), 180) * 60)
        cycle_seconds = max(6 * 3600, _to_int(inputs.get("cycle_hours"), 72) * 3600)

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas para santuario")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        context = {
            "jid": jid,
            "aid": aid,
            "ga_id": ga_id,
            "inputs": inputs,
            "shrine": shrine,
            "selected_gods": selected_gods,
            "favor_recheck_seconds": favor_recheck_seconds,
            "cycle_seconds": cycle_seconds,
            "creds": creds,
        }
        return context, snapshot, shrine

    def _run_activation_cycle(self, context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        jid = context["jid"]
        aid = context["aid"]
        ga_id = context["ga_id"]
        shrine = context["shrine"]
        creds = context["creds"]

        client = self.get_or_login_game_client(jid, aid, ga_id, creds)
        city_id = int(shrine["city_id"])
        position = int(shrine["position"])
        city_name = shrine["city_name"]

        state = client.get_shrine_state(city_id, position)
        available_gods = [god_id for god_id in context["selected_gods"] if god_id in set(state.get("researched_gods") or [])]
        unavailable_gods = [god_id for god_id in context["selected_gods"] if god_id not in set(state.get("researched_gods") or [])]

        if unavailable_gods:
            self.log(
                jid,
                "warn",
                "Deuses ignorados por pesquisa ausente: " + ", ".join(self._god_name(god_id) for god_id in unavailable_gods),
            )
        if not available_gods:
            raise RuntimeError("gods_not_researched")

        current_favor = int(state.get("current_favor") or 0)
        favor_needed = len(available_gods) * 100
        self.log(
            jid,
            "info",
            (
                f"Santuario: {city_name} | nivel={state.get('building_level') or '?'} "
                f"| favor={current_favor} | deuses={', '.join(self._god_name(g) for g in available_gods)} "
                f"| cidades_extra={state.get('additional_cities_possible') or 0}"
            ),
        )

        if current_favor < favor_needed:
            self.save_game_client(ga_id, client)
            return (
                {"status": "waiting_favor", "current_favor": current_favor, "favor_needed": favor_needed, "selected_gods": available_gods},
                state,
                {},
            )

        for god_id in available_gods:
            client.activate_shrine_god(city_id, position, god_id)
            self.log(jid, "info", f"Favor doado para {self._god_name(god_id)}")

        after_state = client.get_shrine_state(city_id, position)
        self.save_game_client(ga_id, client)
        return (
            {
                "status": "activated",
                "selected_gods": available_gods,
                "before_favor": current_favor,
                "after_favor": int(after_state.get("current_favor") or 0),
                "building_level": after_state.get("building_level"),
                "worship_assignments": after_state.get("worship_assignments") or [],
            },
            state,
            after_state,
        )

    @staticmethod
    def _find_shrine(snapshot: dict[str, Any]) -> dict[str, Any] | None:
        for city in snapshot.get("cities") or []:
            city_id = city.get("id")
            for building in city.get("buildings") or []:
                if str(building.get("building") or "").strip() != "shrineOfOlympus":
                    continue
                return {
                    "city_id": city_id,
                    "city_name": str(city.get("name") or city_id),
                    "position": int(building.get("position") or 0),
                    "level": int(building.get("level") or 0),
                }
        return None

    @staticmethod
    def _selected_gods(inputs: dict[str, Any]) -> list[int]:
        selected: list[int] = []
        for key, meta in GOD_META.items():
            if bool(inputs.get(f"god_{key}")):
                selected.append(meta["id"])
        return selected

    @staticmethod
    def _god_name(god_id: int) -> str:
        for meta in GOD_META.values():
            if meta["id"] == int(god_id):
                return str(meta["name"])
        return str(god_id)


class _MiracleRunnerMixin(BaseRunner):
    def _get_snapshot(self, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception:
            return None

    def _find_city_with_temple(self, snapshot: dict[str, Any] | None, city_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        for city in (snapshot or {}).get("cities") or []:
            if str(city.get("id") or "") != str(city_id):
                continue
            for building in city.get("buildings") or []:
                if str(building.get("building") or "") == "temple":
                    return city, building
            return city, None
        return None, None

    @staticmethod
    def _miracle_payload(state: dict[str, Any], city_id: str, last_status: str) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "city_id": int(state.get("city_id") or city_id or 0),
            "city_name": str(state.get("city_name") or ""),
            "island_id": int(state.get("island_id") or 0),
            "island_name": str(state.get("island_name") or ""),
            "x": int(state.get("x") or 0),
            "y": int(state.get("y") or 0),
            "temple_position": int(state.get("temple_position") or 0),
            "temple_level": int(state.get("temple_level") or 0),
            "wonder_name": str(state.get("wonder_name") or ""),
            "wonder_description": str(state.get("wonder_description") or ""),
            "wonder_effect": str(state.get("wonder_effect") or ""),
            "duration_text": str(state.get("duration_text") or ""),
            "cooldown_text": str(state.get("cooldown_text") or ""),
            "belief_level": int(state.get("belief_level") or 0),
            "belief_percent": float(state.get("belief_percent") or 0),
            "belief_text": str(state.get("belief_text") or ""),
            "activate_state": str(state.get("activate_state") or ""),
            "view_state": str(state.get("view_state") or ""),
            "priests": int(state.get("priests") or 0),
            "citizens": int(state.get("citizens") or 0),
            "max_priests": int(state.get("max_priests") or 0),
            "own_conversion_percent": float(state.get("own_conversion_percent") or 0),
            "island_conversion_percent": float(state.get("island_conversion_percent") or 0),
            "citizens_per_priest": int(state.get("citizens_per_priest") or 0),
            "max_inhabitants": int(state.get("max_inhabitants") or 0),
            "last_status": last_status,
            "updated_at": now_iso,
        }

    def _persist_miracle_state(
        self,
        *,
        job_id: str,
        account_id: str,
        game_account_id: str,
        snapshot: dict[str, Any] | None,
        city_id: str,
        state: dict[str, Any],
        last_status: str,
    ) -> None:
        if not snapshot:
            return
        try:
            base_snapshot = dict(snapshot.get("base_snapshot") or {})
            cities = [dict(city) for city in (snapshot.get("cities") or [])]
            military = snapshot.get("military") or {}
            payload = self._miracle_payload(state, city_id, last_status)

            for city in cities:
                if str(city.get("id") or "") != str(city_id):
                    continue
                city["miracle_state"] = payload
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
            self.log(job_id, "warn", f"Nao foi possivel persistir estado do milagre no snapshot: {exc}")


@register_runner(35)
class AdjustPriestsRunner(_MiracleRunnerMixin, BaseRunner):
    """Action 35 (N-141): set the priests of the chosen temples and read every island miracle.

    Inputs:
        cities            list of city ids (empty = every city with a temple)
        target_mode       "keep" (only read) | "absolute" | "percent_max" | "own_conversion"
        target_value      priests, % of the maximum, or % of the city's inhabitants converted
        reserve_citizens  free citizens never taken
    Everything read goes to the snapshot in one write: each city's temple (miracle_state) and each island's miracle
    (base_snapshot.island_wonders).
    """

    MODES = ("keep", "absolute", "percent_max", "own_conversion")

    @staticmethod
    def desired_priests(state: dict[str, Any], mode: str, value: int, reserve: int) -> int:
        current = int(state.get("priests") or 0)
        maximum = int(state.get("max_priests") or 0)
        citizens = int(state.get("citizens") or 0)
        if mode == "absolute":
            wanted = value
        elif mode == "percent_max":
            wanted = round(maximum * value / 100.0)
        elif mode == "own_conversion":
            per_priest = int(state.get("citizens_per_priest") or 0)
            inhabitants = int(state.get("max_inhabitants") or 0)
            if per_priest <= 0 or inhabitants <= 0:
                return current
            wanted = -(-inhabitants * value // (100 * per_priest))        # ceil
        else:
            return current
        free = max(0, citizens - reserve)
        return max(0, min(maximum or wanted, wanted, current + free))

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})
        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        mode = str(inputs.get("target_mode") or "keep").strip().lower()
        if mode not in self.MODES:
            mode = "keep"
        try:
            value = max(0, int(inputs.get("target_value") or 0))
        except (TypeError, ValueError):
            value = 0
        try:
            reserve = max(0, int(inputs.get("reserve_citizens") or 0))
        except (TypeError, ValueError):
            reserve = 0

        snapshot = self._get_snapshot(ga_id)
        temples: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for city in (snapshot or {}).get("cities") or []:
            temple = next((b for b in city.get("buildings") or [] if str(b.get("building") or "") == "temple"), None)
            if temple and int(temple.get("position") or 0):
                temples.append((city, temple))
        wanted = {str(c).strip() for c in (inputs.get("cities") or []) if str(c).strip()}
        chosen = [(c, t) for c, t in temples if not wanted or str(c.get("id")) in wanted]
        if not chosen:
            self.log(jid, "error", "Nenhuma cidade com templo encontrada")
            return RunnerResult(success=False, data={"error": "temple_not_found"})

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})
        client = self.get_or_login_game_client(jid, aid, ga_id, creds)

        states: dict[str, dict[str, Any]] = {}
        changed, failed = [], []
        for index, (city, temple) in enumerate(chosen):
            city_id, position = str(city.get("id")), int(temple.get("position") or 0)
            name = str(city.get("name") or city_id)
            if index:
                time.sleep(random.uniform(1.5, 3.5))
            try:
                state = client.get_temple_miracle_state(int(city_id), position)
                target = self.desired_priests(state, mode, value, reserve)
                current = int(state.get("priests") or 0)
                status = "inspected"
                if mode != "keep" and target != current:
                    client.set_priests(int(city_id), position, target)
                    state = client.get_temple_miracle_state(int(city_id), position)
                    changed.append({"city_id": city_id, "city_name": name, "before": current, "after": int(state.get("priests") or 0)})
                    status = "priests_set"
                self.log(jid, "info", (
                    f"Templo: {name} | sacerdotes {current} -> {state.get('priests')} (max {state.get('max_priests')}) | "
                    f"conversao {state.get('own_conversion_percent')}% | ilha {state.get('island_conversion_percent')}% | "
                    f"milagre {state.get('wonder_name') or '?'} ({state.get('activate_state') or '?'})"))
                states[city_id] = self._miracle_payload(state, city_id, status)
            except Exception as exc:
                failed.append({"city_id": city_id, "city_name": name, "reason": str(exc)})
                self.log(jid, "warn", f"Falha no templo de {name}: {exc}")

        # each island's miracle, read once from one of our cities on it
        wonders: dict[str, dict[str, Any]] = {}
        for city, _temple in temples:
            island_id = str(city.get("island_id") or (states.get(str(city.get("id"))) or {}).get("island_id") or "")
            if not island_id or island_id in wonders:
                continue
            try:
                time.sleep(random.uniform(1.0, 2.5))
                wonder = client.get_wonder_state(int(island_id), int(city.get("id")))
                miracle = states.get(str(city.get("id"))) or (city.get("miracle_state") or {})
                wonder["wonder_name"] = str(miracle.get("wonder_name") or "")
                wonders[island_id] = wonder
                self.log(jid, "info", (
                    f"Milagre da ilha {island_id}: {wonder['wonder_name'] or '?'} nivel {wonder['level']}"
                    f"{' (maximo)' if wonder['max_level_reached'] else ''} | fe {wonder['faith_percent']}% | "
                    f"{len(wonder['participants'])} cidades na ilha"))
            except Exception as exc:
                self.log(jid, "warn", f"Nao foi possivel ler o milagre da ilha {island_id}: {exc}")

        self.save_game_client(ga_id, client)
        self._persist_temples(jid, aid, ga_id, snapshot, states, wonders)
        return RunnerResult(
            success=bool(states) and not (failed and not states),
            data={"mode": mode, "target_value": value, "changed": changed, "failed": failed,
                  "temples": len(states), "islands": len(wonders)},
        )

    def _persist_temples(self, jid, aid, ga_id, snapshot, states, wonders) -> None:
        if not snapshot:
            return
        try:
            base_snapshot = dict(snapshot.get("base_snapshot") or {})
            cities = [dict(city) for city in (snapshot.get("cities") or [])]
            for city in cities:
                state = states.get(str(city.get("id")))
                if state:
                    city["miracle_state"] = state
            island_wonders = dict(base_snapshot.get("island_wonders") or {})
            island_wonders.update(wonders)
            base_snapshot["island_wonders"] = island_wonders
            base_snapshot["temples_updated_at"] = datetime.now(timezone.utc).isoformat()
            self.hub.update_snapshot(
                aid,
                {"base_snapshot": base_snapshot, "cities": cities, "military": snapshot.get("military") or {}, "source_job_id": jid},
                game_account_id=ga_id,
            )
        except Exception as exc:
            self.log(jid, "warn", f"Nao foi possivel gravar templos e milagres no snapshot: {exc}")


@register_runner(5)
class ActivateShrineOnceRunner(_ShrineRunnerMixin, BaseRunner):
    """Action 5: activate selected gods once, waiting for favor if needed."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        prepared = self._prepare_context(job)
        if isinstance(prepared, RunnerResult):
            return prepared
        context, snapshot, shrine = prepared
        jid = context["jid"]

        try:
            result, _before_state, _after_state = self._run_activation_cycle(context)
            if result["status"] == "waiting_favor":
                self._persist_shrine_state(
                    job_id=jid,
                    account_id=context["aid"],
                    game_account_id=context["ga_id"],
                    snapshot=snapshot,
                    shrine=shrine,
                    state=_before_state,
                )
                self.log(
                    jid,
                    "info",
                    (
                        f"Favor insuficiente: atual={result['current_favor']} | necessario={result['favor_needed']} "
                        f"| recheck_em={context['favor_recheck_seconds']}s"
                    ),
                )
                return RunnerResult(
                    success=True,
                    reschedule_seconds=context["favor_recheck_seconds"],
                    reschedule_inputs=context["inputs"],
                    data={"city_id": shrine["city_id"], "city_name": shrine["city_name"], **result},
                )

            self._persist_shrine_state(
                job_id=jid,
                account_id=context["aid"],
                game_account_id=context["ga_id"],
                snapshot=snapshot,
                shrine=shrine,
                state=_after_state or _before_state,
            )
            self.log(
                jid,
                "info",
                f"Santuario ativado uma vez: favor_restante={result['after_favor']}",
            )
            return RunnerResult(
                success=True,
                data={"city_id": shrine["city_id"], "city_name": shrine["city_name"], **result},
            )
        except Exception as exc:
            logger.exception("ActivateShrineOnceRunner failed for job %s", jid)
            retry_seconds = context["favor_recheck_seconds"]
            self.log(jid, "error", f"Erro ao ativar santuario: {exc} | retry_em={retry_seconds}s")
            return RunnerResult(
                success=True,
                reschedule_seconds=retry_seconds,
                reschedule_inputs=context["inputs"],
                data={"status": "retry", "error": str(exc), "retry_seconds": retry_seconds},
            )


@register_runner(1007)
class ActivateShrineLoopRunner(_ShrineRunnerMixin, BaseRunner):
    """Action 1007: keep selected gods activated on a recurring cycle."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        prepared = self._prepare_context(job)
        if isinstance(prepared, RunnerResult):
            return prepared
        context, snapshot, shrine = prepared
        jid = context["jid"]

        try:
            result, _before_state, _after_state = self._run_activation_cycle(context)
            if result["status"] == "waiting_favor":
                delay = context["favor_recheck_seconds"]
                self._persist_shrine_state(
                    job_id=jid,
                    account_id=context["aid"],
                    game_account_id=context["ga_id"],
                    snapshot=snapshot,
                    shrine=shrine,
                    state=_before_state,
                )
                self.log(
                    jid,
                    "info",
                    (
                        f"Loop do santuario aguardando favor: atual={result['current_favor']} "
                        f"| necessario={result['favor_needed']} | proximo_em={delay}s"
                    ),
                )
                return RunnerResult(
                    success=True,
                    reschedule_seconds=delay,
                    reschedule_inputs=context["inputs"],
                    data={"city_id": shrine["city_id"], "city_name": shrine["city_name"], **result},
                )

            delay = context["cycle_seconds"]
            self._persist_shrine_state(
                job_id=jid,
                account_id=context["aid"],
                game_account_id=context["ga_id"],
                snapshot=snapshot,
                shrine=shrine,
                state=_after_state or _before_state,
            )
            self.log(
                jid,
                "info",
                f"Loop do santuario concluido: favor_restante={result['after_favor']} | proximo_em={delay}s",
            )
            return RunnerResult(
                success=True,
                reschedule_seconds=delay,
                reschedule_inputs=context["inputs"],
                data={"city_id": shrine["city_id"], "city_name": shrine["city_name"], **result},
            )
        except Exception as exc:
            logger.exception("ActivateShrineLoopRunner failed for job %s", jid)
            retry_seconds = context["favor_recheck_seconds"]
            self.log(jid, "error", f"Erro no loop do santuario: {exc} | retry_em={retry_seconds}s")
            return RunnerResult(
                success=True,
                reschedule_seconds=retry_seconds,
                reschedule_inputs=context["inputs"],
                data={"status": "retry", "error": str(exc), "retry_seconds": retry_seconds},
            )


@register_runner(11)
class ActivateMiracleRunner(_MiracleRunnerMixin, BaseRunner):
    """Action 11: activate the island miracle from a selected city's temple."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            self.log(jid, "error", "game_account_id ausente para ativar milagre")
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        city_id = str(inputs.get("city") or inputs.get("city_id") or "").strip()
        if not city_id:
            self.log(jid, "error", "Nenhuma cidade informada para ativar milagre")
            return RunnerResult(success=False, data={"error": "missing_city"})

        snapshot = self._get_snapshot(ga_id)
        city_snapshot, temple = self._find_city_with_temple(snapshot, city_id)
        if city_snapshot is None:
            self.log(jid, "error", f"Cidade {city_id} nao encontrada no snapshot")
            return RunnerResult(success=False, data={"error": "city_not_found"})
        if temple is None:
            self.log(jid, "error", f"Cidade {city_snapshot.get('name') or city_id} nao possui templo")
            return RunnerResult(success=False, data={"error": "temple_not_found"})

        try:
            creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
            if not creds:
                return RunnerResult(success=False, data={"error": "missing_credentials"})

            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            position = int(temple.get("position") or 0)
            state = client.get_temple_miracle_state(int(city_id), position)
            self._persist_miracle_state(
                job_id=jid,
                account_id=aid,
                game_account_id=ga_id,
                snapshot=snapshot,
                city_id=city_id,
                state=state,
                last_status="inspected",
            )
            self.log(
                jid,
                "info",
                (
                    f"Milagre: {state.get('city_name') or city_snapshot.get('name') or city_id} | "
                    f"ilha={state.get('island_name') or city_snapshot.get('island_id') or '?'} | "
                    f"templo={state.get('temple_level') or temple.get('level') or 0} | "
                    f"wonder={state.get('wonder_name') or '?'} | estado={state.get('activate_state') or '?'}"
                ),
            )

            if str(state.get("activate_state") or "").strip().lower() != "enabled":
                reason = str(state.get("cooldown_text") or state.get("wonder_effect") or "Milagre indisponivel").strip()
                self.log(jid, "warn", f"Milagre indisponivel: {reason}")
                return RunnerResult(
                    success=False,
                    data={
                        "error": "miracle_unavailable",
                        "city_id": int(city_id),
                        "city_name": state.get("city_name") or city_snapshot.get("name") or city_id,
                        "wonder_name": state.get("wonder_name") or "",
                        "reason": reason,
                    },
                )

            after_state = client.activate_miracle(int(city_id), position)
            self.save_game_client(ga_id, client)
            self._persist_miracle_state(
                job_id=jid,
                account_id=aid,
                game_account_id=ga_id,
                snapshot=snapshot,
                city_id=city_id,
                state=after_state,
                last_status="activated",
            )
            self.log(
                jid,
                "info",
                (
                    f"Milagre ativado: {after_state.get('wonder_name') or state.get('wonder_name') or '?'} | "
                    f"efeito={after_state.get('wonder_effect') or state.get('wonder_effect') or '?'}"
                ),
            )
            if after_state.get("cooldown_text"):
                self.log(jid, "info", f"Regeneracao: {after_state.get('cooldown_text')}")

            return RunnerResult(
                success=True,
                data={
                    "city_id": int(city_id),
                    "city_name": after_state.get("city_name") or city_snapshot.get("name") or city_id,
                    "wonder_name": after_state.get("wonder_name") or state.get("wonder_name") or "",
                    "cooldown_text": after_state.get("cooldown_text") or "",
                    "belief_percent": after_state.get("belief_percent") or 0,
                },
            )
        except Exception as exc:
            self.log(jid, "error", f"Falha ao ativar milagre: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
