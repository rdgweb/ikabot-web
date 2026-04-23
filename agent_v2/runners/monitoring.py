"""
Monitoring runners.

Action codes:
    702  alert_wine
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from math import ceil
from typing import Any

from core.runner_registry import register_runner
from game_client.parsers.html_parser import GamePageParser
from runners.base import BaseRunner, RunnerResult
from services.resource_transport import estimate_incoming_transport_wait_seconds
from services.wine_tavern import (
    find_tavern_position,
    find_townhall_position,
    open_tavern_page,
    open_townhall_page,
    set_tavern_service,
)

logger = logging.getLogger(__name__)

ATTACK_SEEN_TTL_SECONDS = 6 * 3600
ATTACK_SEEN_MAX = 200
MIN_RECHECK_SECONDS = 30 * 60
MAX_RECHECK_SECONDS = 12 * 3600
ARRIVAL_BUFFER_SECONDS = 10 * 60
PROTECT_LEAD_SECONDS = 60 * 60


def _to_int(raw: Any, default: int, minimum: int = 0) -> int:
    try:
        value = int(float(raw))
    except Exception:
        value = default
    return max(minimum, value)


def _as_city_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        return [c for c in raw.values() if isinstance(c, dict)]
    return []


def _city_wine_production_per_hour(city: dict[str, Any]) -> int:
    tradegood = _to_int(city.get("produced_tradegood", city.get("tradegood", 0)), 0)
    return _to_int(city.get("tradegood_production_per_hour", 0), 0) if tradegood == 1 else 0


def _city_wine_net_per_hour(city: dict[str, Any]) -> int:
    production = _city_wine_production_per_hour(city)
    consumption = _to_int(city.get("wine_consumption", 0), 0)
    return production - consumption


def _city_wine_hours(city: dict[str, Any]) -> float | None:
    cached = city.get("wine_hours")
    try:
        if cached is not None:
            return float(cached)
    except Exception:
        pass

    wine = _to_int(city.get("wine", 0), 0)
    net = _city_wine_net_per_hour(city)
    if net < 0 and wine > 0:
        return wine / abs(net)
    return None


def _city_name(city: dict[str, Any]) -> str:
    return str(city.get("name") or city.get("city_name") or city.get("id") or "?")


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


def _extract_current_city_id(html: str) -> str | None:
    match = re.search(r'currentCityId\s*[:=]\s*["\']?(\d+)', html)
    return match.group(1) if match else None


def _safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(raw))
    except Exception:
        return default


def _city_is_full(city: dict[str, Any]) -> bool:
    citizens = _to_int(city.get("citizens", 0), 0)
    max_pop = _to_int(city.get("max_pop", city.get("population", 0)), 0)
    return bool(max_pop > 0 and citizens >= max_pop)


def _wine_happiness_from_breakdown(breakdown: dict[str, int]) -> int:
    return max(
        0,
        int(breakdown.get("js_TownHallSatisfactionOverviewWineBoniServeBonusValue", 0))
        + int(breakdown.get("js_TownHallSatisfactionOverviewWineBoniTavernBonusValue", 0)),
    )


@register_runner(701)
class AlertAttacksRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}
        recovery = inputs.get("__recovery") if isinstance(inputs.get("__recovery"), dict) else {}

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        interval_minutes = _to_int(inputs.get("interval_minutes", 20), 20, 3)
        min_troops = _to_int(inputs.get("min_troops", 0), 0, 0)
        min_fleet = _to_int(inputs.get("min_fleet", 0), 0, 0)
        notify_telegram = bool(inputs.get("notify_telegram", True))

        snapshot = self._get_snapshot(ga_id)
        state = dict((snapshot.get("base_snapshot") or {}).get("attack_alert_state") or {})
        seen_map = self._normalize_seen_map(state.get("seen_events"))
        notified_map = self._normalize_seen_map(state.get("notified_events") or state.get("seen_events"))

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            scan = self._fetch_military_advisor(client)
            hostile_movements = [item for item in scan["movements"] if bool(item.get("isHostile"))]
            total_movements = len(scan["movements"])
            hostile_count = len(hostile_movements)

            matching_events = []
            new_events = []
            now_ts = int(datetime.now(timezone.utc).timestamp())
            for movement in hostile_movements:
                event = self._movement_to_event(movement, scan["time_now"])
                if event["troops"] < min_troops and event["fleet"] < min_fleet:
                    continue
                matching_events.append(event)
                if event["event_id"] and event["event_id"] not in seen_map:
                    new_events.append(event)
                    seen_map[event["event_id"]] = now_ts

            seen_map = self._prune_seen_map(seen_map, now_ts)
            notified_map = self._prune_seen_map(notified_map, now_ts)
            self._persist_attack_state(
                account_id=aid,
                game_account_id=ga_id,
                source_job_id=jid,
                snapshot=snapshot,
                interval_minutes=interval_minutes,
                total_movements=total_movements,
                hostile_count=hostile_count,
                matching_events=matching_events,
                seen_map=seen_map,
                notified_map=notified_map,
            )

            self.log(
                jid,
                "info",
                f"Advisor militar: movimentos={total_movements} | hostis={hostile_count} | alertaveis={len(matching_events)} | novos={len(new_events)}",
            )
            for event in new_events:
                self.log(
                    jid,
                    "warn",
                    (
                        f"Ataque detectado: {event['mission']} | "
                        f"{event['origin_player']} ({event['origin_city']}) -> "
                        f"{event['target_city']} | tropas={event['troops']} | frotas={event['fleet']} | "
                        f"eta={event['time_left_human']}"
                    ),
                )
                event_id = str(event.get("event_id") or "").strip()
                if notify_telegram and event_id and event_id not in notified_map:
                    self._notify_attack(
                        aid,
                        ga_id,
                        event,
                        agent_name=str(job.get("agent") or ""),
                    )
                    notified_map[event_id] = now_ts

            if new_events:
                notified_map = self._prune_seen_map(notified_map, now_ts)
                self._persist_attack_state(
                    account_id=aid,
                    game_account_id=ga_id,
                    source_job_id=jid,
                    snapshot=snapshot,
                    interval_minutes=interval_minutes,
                    total_movements=total_movements,
                    hostile_count=hostile_count,
                    matching_events=matching_events,
                    seen_map=seen_map,
                    notified_map=notified_map,
                )

            if ga_id:
                self.save_game_client(ga_id, client)

            return RunnerResult(
                success=True,
                reschedule_seconds=max(180, interval_minutes * 60),
                data={
                    "status": "ok",
                    "total_movements": total_movements,
                    "hostile_count": hostile_count,
                    "matching_count": len(matching_events),
                    "new_count": len(new_events),
                },
            )
        except Exception as exc:
            self.log(jid, "warning", f"Falha ao varrer advisor militar: {exc}")
            return RunnerResult(
                success=True,
                reschedule_seconds=60,
                data={"status": "retry_after_error", "error": str(exc)},
            )

    def _get_snapshot(self, game_account_id: str) -> dict[str, Any]:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception:
            return {}

    def _fetch_military_advisor(self, client) -> dict[str, Any]:
        home = client.session.get(client._server_url, timeout=30).text
        current_city_id = _extract_current_city_id(home)
        if not current_city_id:
            raise RuntimeError("current_city_id_not_found")
        action_request = GamePageParser().extract_action_request(home) or client._action_request
        response = client.session.post(
            client._server_url,
            params={
                "view": "militaryAdvisor",
                "oldView": "city",
                "oldBackgroundView": "city",
                "backgroundView": "city",
                "currentCityId": current_city_id,
                "actionRequest": action_request,
                "ajax": "1",
            },
            timeout=30,
        )
        data = json.loads(response.text)

        time_now = 0
        movements: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, list) or len(item) < 2:
                continue
            if item[0] == "updateGlobalData" and isinstance(item[1], dict):
                time_now = _safe_int(item[1].get("time"), 0)
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    client._action_request = token
            elif item[0] == "changeView" and isinstance(item[1], list):
                try:
                    movements = item[1][2]["viewScriptParams"]["militaryAndFleetMovements"] or []
                except Exception:
                    movements = []

        return {
            "current_city_id": current_city_id,
            "time_now": time_now,
            "movements": movements,
        }

    def _movement_to_event(self, movement: dict[str, Any], time_now: int) -> dict[str, Any]:
        event = movement.get("event") or {}
        origin = movement.get("origin") or {}
        target = movement.get("target") or {}
        army = movement.get("army") or {}
        fleet = movement.get("fleet") or {}
        event_time = _safe_int(movement.get("eventTime"), 0)
        time_left = max(0, event_time - time_now) if event_time and time_now else 0
        return {
            "event_id": str(event.get("id") or ""),
            "mission": str(event.get("missionText") or "Ataque"),
            "origin_city": str(origin.get("name") or "?"),
            "origin_player": str(origin.get("avatarName") or "?"),
            "origin_city_id": str(origin.get("cityId") or ""),
            "target_city": str(target.get("name") or "?"),
            "target_player": str(target.get("avatarName") or "?"),
            "target_city_id": str(target.get("cityId") or ""),
            "troops": _safe_int(army.get("amount"), 0),
            "fleet": _safe_int(fleet.get("amount"), 0),
            "event_time": event_time,
            "time_left_sec": time_left,
            "time_left_human": _duration_human(time_left) if time_left else "",
        }

    def _normalize_seen_map(self, raw: Any) -> dict[str, int]:
        if isinstance(raw, dict):
            out = {}
            for key, value in raw.items():
                event_id = str(key or "").strip()
                if not event_id:
                    continue
                out[event_id] = _safe_int(value, 0)
            return out
        if isinstance(raw, list):
            now_ts = int(datetime.now(timezone.utc).timestamp())
            return {str(item): now_ts for item in raw if str(item or "").strip()}
        return {}

    def _prune_seen_map(self, seen_map: dict[str, int], now_ts: int) -> dict[str, int]:
        fresh = {
            event_id: ts
            for event_id, ts in seen_map.items()
            if now_ts - _safe_int(ts, 0) <= ATTACK_SEEN_TTL_SECONDS
        }
        if len(fresh) <= ATTACK_SEEN_MAX:
            return fresh
        ordered = sorted(fresh.items(), key=lambda item: item[1], reverse=True)[:ATTACK_SEEN_MAX]
        return dict(ordered)

    def _persist_attack_state(
        self,
        *,
        account_id: str,
        game_account_id: str,
        source_job_id: str,
        snapshot: dict[str, Any],
        interval_minutes: int,
        total_movements: int,
        hostile_count: int,
        matching_events: list[dict[str, Any]],
        seen_map: dict[str, int],
        notified_map: dict[str, int],
    ) -> None:
        base_snapshot = dict((snapshot or {}).get("base_snapshot") or {})
        base_snapshot["attack_alert_state"] = {
            "interval_minutes": int(interval_minutes),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "total_movements": int(total_movements),
            "hostile_count": int(hostile_count),
            "matching_count": len(matching_events),
            "events": matching_events[:10],
            "seen_events": seen_map,
            "notified_events": notified_map,
        }
        self.hub.update_snapshot(
            account_id,
            {
                "base_snapshot": base_snapshot,
                "cities": (snapshot or {}).get("cities") or [],
                "military": (snapshot or {}).get("military") or {},
                "source_job_id": source_job_id,
            },
            game_account_id=game_account_id,
        )

    def _notify_attack(self, account_id: str, game_account_id: str, event: dict[str, Any], *, agent_name: str) -> None:
        try:
            self.hub.send_notification(
                event="attack_alert",
                account_id=account_id,
                game_account_id=game_account_id,
                title=f"{event['mission']} em {event['target_city']}",
                body=(
                    f"{event['origin_player']} ({event['origin_city']}) -> {event['target_city']} | "
                    f"tropas={event['troops']} | frotas={event['fleet']} | chegada em {event['time_left_human'] or '-'}"
                ),
                agent_name=agent_name,
                metadata=event,
            )
        except Exception:
            logger.exception("Failed to send attack alert notification")


@register_runner(702)
class AlertWineRunner(BaseRunner):
    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}
        recovery = inputs.get("__recovery") if isinstance(inputs.get("__recovery"), dict) else {}

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        protect_hours = _to_int(inputs.get("protect_hours", inputs.get("trigger_hours", 24)), 24, 1)
        cover_hours = max(protect_hours, _to_int(inputs.get("cover_hours", inputs.get("target_hours", 168)), 168, 1))
        donor_reserve_hours = _to_int(inputs.get("donor_reserve_hours", 168), 168, 1)
        useful_transfer_min = _to_int(inputs.get("useful_transfer_min", inputs.get("min_transfer", 5000)), 5000, 1)
        shipment_cap = _to_int(inputs.get("shipment_cap", inputs.get("max_transfer", 25000)), 25000, 1)
        growth_happiness_floor = _to_int(inputs.get("growth_happiness_floor", 96), 96, 0)
        transport_load_percent = str(inputs.get("transport_load_percent", "100") or "100")

        snapshot = self._get_snapshot(jid, ga_id)
        if snapshot is None:
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "waiting_snapshot"})

        cities = _as_city_list(snapshot.get("cities"))
        if not cities:
            self.log(jid, "warn", "Snapshot sem cidades; aguardando novo check_status")
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "empty_snapshot"})

        snapshot_updated_at = self._parse_snapshot_time(snapshot.get("updated_at"))
        if snapshot_updated_at is None or (datetime.now(timezone.utc) - snapshot_updated_at).total_seconds() > self.get_snapshot_stale_seconds():
            self.log(jid, "warn", "Snapshot antigo demais para decidir transporte com seguranca; atualizando status")
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=MIN_RECHECK_SECONDS, data={"status": "stale_snapshot"})

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})
        client = self.get_or_login_game_client(jid, aid, ga_id, creds)
        if recovery:
            self.log(
                jid,
                "warn",
                f"Recovery do alerta de vinho: tentativa {int(recovery.get('attempt') or 1)}",
            )
        self.checkpoint(jid, phase="scan_start", metadata={"city_count": len(cities)})

        city_infos: list[dict[str, Any]] = []
        for index, city in enumerate(cities, start=1):
            self.log(
                jid,
                "info",
                f"Escaneando cidade {index}/{len(cities)}: {_city_name(city)}",
            )
            self.checkpoint(
                jid,
                phase="scan_city",
                metadata={
                    "index": index,
                    "city_id": str(city.get("id") or ""),
                    "city_name": _city_name(city),
                },
            )
            managed = self._read_and_manage_city(
                jid,
                client,
                city,
                growth_happiness_floor=growth_happiness_floor,
            )
            city_infos.append(managed)

        self.checkpoint(jid, phase="plan_transfers", metadata={"city_count": len(city_infos)})
        needy = [
            info for info in city_infos
            if info.get("floor_unmet")
            or (
                info["desired_hours"] is not None
                and info["desired_hours"] < cover_hours
                and info["desired_net"] < 0
            )
        ]
        needy.sort(key=lambda item: item["hours"] if item["hours"] is not None else 10**9)

        if needy:
            self.log(
                jid,
                "info",
                "Cidades em risco de vinho: " + ", ".join(
                    f"{info['name']} ({self._format_hours(info.get('desired_hours', info.get('hours')))}, felicidade={info['total_happiness']})"
                    for info in needy[:8]
                ),
            )
        else:
            self.log(jid, "info", "Nenhuma cidade abaixo da janela de cobertura de vinho")

        actions_spawned = 0
        incoming_waits: list[int] = []
        for index, target in enumerate(needy, start=1):
            self.checkpoint(
                jid,
                phase="dispatch_city",
                metadata={
                    "index": index,
                    "city_id": target["id"],
                    "city_name": target["name"],
                    "desired_hours": target.get("desired_hours"),
                    "total_happiness": target.get("total_happiness"),
                },
            )
            incoming_wait = estimate_incoming_transport_wait_seconds(client, target["id"])
            if incoming_wait:
                incoming_waits.append(incoming_wait)
                incoming_hours = incoming_wait / 3600.0
                if target["hours"] is None or target["hours"] > incoming_hours:
                    self.log(
                        jid,
                        "info",
                        f"{target['name']}: ja existe transporte chegando; nova avaliacao apos a chegada",
                    )
                    continue

            target_need = self._compute_target_need(
                target,
                cover_hours=cover_hours,
                useful_transfer_min=useful_transfer_min,
                shipment_cap=shipment_cap,
                protect_hours=protect_hours,
            )
            if target_need < 1:
                continue

            donor = self._pick_donor(
                city_infos,
                target_id=target["id"],
                donor_reserve_hours=donor_reserve_hours,
                useful_transfer_min=useful_transfer_min,
                requested=target_need,
            )
            if donor is None:
                self.log(jid, "warn", f"{target['name']}: nenhum doador com vinho suficiente")
                continue

            amount = min(target_need, donor["available"])
            if amount < useful_transfer_min:
                if (target["hours"] or 0) > max(1.0, protect_hours / 2):
                    self.log(
                        jid,
                        "info",
                        (
                            f"{target['name']}: necessidade ({int(amount):,}) abaixo do envio util "
                            f"({int(useful_transfer_min):,}); aguardando consolidacao"
                        ),
                    )
                    continue
                amount = min(shipment_cap, donor["available"], useful_transfer_min)

            if amount < 1:
                continue

            self.hub.spawn_job(
                jid,
                action_code=2,
                inputs={
                    "from_city": donor["id"],
                    "to_city": target["id"],
                    "wine": int(amount),
                    "transport_load_percent": transport_load_percent,
                },
            )
            actions_spawned += 1
            donor["available"] = max(0, donor["available"] - int(amount))
            donor["wine"] = max(0, donor["wine"] - int(amount))
            self.log(
                jid,
                "info",
                (
                    f"Envio de vinho criado: {donor['name']} -> {target['name']} | "
                    f"quantidade={int(amount):,} | cobertura_alvo={cover_hours}h"
                ),
            )

        if ga_id:
            self.save_game_client(ga_id, client)

        next_seconds = self._choose_next_check(
            city_infos,
            protect_hours=protect_hours,
            incoming_waits=incoming_waits,
        )
        self.checkpoint(
            jid,
            phase="rescheduled",
            metadata={
                "actions_spawned": actions_spawned,
                "next_seconds": next_seconds,
                "needy_count": len(needy),
            },
        )
        self.log(jid, "info", f"Proxima avaliacao de vinho em {next_seconds}s")
        return RunnerResult(
            success=True,
            reschedule_seconds=next_seconds,
            data={"status": "ok", "actions_spawned": actions_spawned},
        )

    def _read_and_manage_city(
        self,
        job_id: str,
        client,
        city: dict[str, Any],
        *,
        growth_happiness_floor: int,
    ) -> dict[str, Any]:
        city_id = str(city.get("id") or "").strip()
        is_full = _city_is_full(city)
        self.log(job_id, "debug", f"{_city_name(city)}: lendo estado da cidade")
        townhall = None if is_full else open_townhall_page(client, city_id=city_id, position=find_townhall_position(city))
        tavern_data = self._manage_tavern(
            job_id,
            client,
            city,
            is_full=is_full,
            townhall=townhall,
            growth_happiness_floor=growth_happiness_floor,
        )
        if tavern_data and tavern_data.get("townhall") is not None:
            townhall = tavern_data["townhall"]
        if townhall is None:
            townhall = open_townhall_page(client, city_id=city_id, position=find_townhall_position(city))

        city_effective = dict(city)
        if tavern_data and tavern_data.get("wine_spendings_per_hour") is not None:
            city_effective["wine_consumption"] = int(tavern_data["wine_spendings_per_hour"])
        desired_wine_consumption = int(
            (tavern_data or {}).get("target_wine_spendings_per_hour")
            or city_effective.get("wine_consumption")
            or 0
        )

        wine = _to_int(city_effective.get("wine", 0), 0)
        incoming_resources = city_effective.get("incoming_resources") if isinstance(city_effective.get("incoming_resources"), dict) else {}
        incoming_wine = _to_int(incoming_resources.get("wine", 0), 0)
        effective_wine = wine + incoming_wine
        net = _city_wine_net_per_hour(city_effective)
        hours = _city_wine_hours(city_effective)
        desired_net = _city_wine_production_per_hour(city_effective) - desired_wine_consumption
        desired_hours = (effective_wine / abs(desired_net)) if desired_net < 0 and effective_wine > 0 else (0.0 if desired_net < 0 else None)
        wine_happiness = _wine_happiness_from_breakdown(townhall.breakdown)
        non_wine_happiness = int(townhall.total_happiness) - int(wine_happiness)

        incoming_text = f" (+{incoming_wine:,} em rota)" if incoming_wine > 0 else ""
        self.log(
            job_id,
            "info",
            (
                f"Cidade avaliada: {_city_name(city_effective)} | vinho={wine:,}{incoming_text} | "
                f"cobertura_atual={self._format_hours(hours)} | cobertura_alvo={self._format_hours(desired_hours)} | "
                f"felicidade={int(townhall.total_happiness)} | taverna={int((tavern_data or {}).get('current_amount') or 0)}"
            ),
        )

        return {
            "raw": city_effective,
            "id": city_id,
            "name": _city_name(city_effective),
            "wine": wine,
            "incoming_wine": incoming_wine,
            "effective_wine": effective_wine,
            "net": net,
            "hours": hours,
            "desired_wine_consumption": desired_wine_consumption,
            "desired_net": desired_net,
            "desired_hours": desired_hours,
            "is_full": is_full,
            "total_happiness": int(townhall.total_happiness),
            "happiness_text": townhall.happiness_text,
            "growth_per_hour": float(townhall.growth_per_hour),
            "wine_happiness": int(wine_happiness),
            "non_wine_happiness": int(non_wine_happiness),
            "floor_unmet": bool((tavern_data or {}).get("floor_unmet")),
            "tavern": tavern_data or {},
        }

    def _manage_tavern(
        self,
        job_id: str,
        client,
        city: dict[str, Any],
        *,
        is_full: bool,
        townhall,
        growth_happiness_floor: int,
    ) -> dict[str, Any] | None:
        tavern_position = find_tavern_position(city)
        if tavern_position < 0:
            return None

        city_id = str(city.get("id") or "").strip()
        self.log(job_id, "debug", f"{_city_name(city)}: abrindo taverna")
        preview = open_tavern_page(client, city_id=city_id, position=tavern_position)
        if preview.max_amount <= 0 or not preview.action_request:
            self.log(job_id, "warn", f"{_city_name(city)}: taverna sem estado utilizavel")
            return None

        if is_full:
            desired_amount = 0
        else:
            desired_amount = self._desired_tavern_amount(
                preview=preview,
                townhall=townhall,
                is_full=is_full,
                growth_happiness_floor=growth_happiness_floor,
            )
        target_amount_hint = desired_amount
        happiness_floor = self._happiness_floor(is_full=is_full, growth_happiness_floor=growth_happiness_floor)

        if desired_amount == preview.current_amount:
            state = preview
            changed = False
        else:
            self.log(
                job_id,
                "info",
                f"{_city_name(city)}: ajustando taverna de {preview.current_amount}/{preview.max_amount} para {desired_amount}/{preview.max_amount}",
            )
            result = set_tavern_service(
                client,
                city_id=city_id,
                position=tavern_position,
                desired_amount=desired_amount,
            )
            if not result.get("ok"):
                self.log(job_id, "warn", f"{_city_name(city)}: nao foi possivel ajustar taverna")
                return None
            state = result.get("state") or preview
            townhall = open_townhall_page(client, city_id=city_id, position=find_townhall_position(city))
            changed = bool(result.get("changed"))

        if townhall is not None and townhall.total_happiness < happiness_floor:
            state, townhall, changed, target_amount_hint = self._enforce_happiness_floor(
                client,
                city=city,
                preview=state,
                townhall=townhall,
                floor=happiness_floor,
                changed=changed,
            )
            if townhall.total_happiness < happiness_floor:
                self.log(
                    job_id,
                    "warn",
                    (
                        f"{_city_name(city)}: felicidade total permaneceu abaixo do piso "
                        f"({townhall.total_happiness} < {happiness_floor}) mesmo apos subir a taverna"
                    ),
                )
        if changed:
            if townhall is None:
                townhall = open_townhall_page(client, city_id=city_id, position=find_townhall_position(city))
            self.log(
                job_id,
                "info",
                (
                    f"Taverna ajustada: {_city_name(city)} | servico={state.current_amount}/{state.max_amount} | "
                    f"felicidade_total={townhall.total_happiness} ({townhall.happiness_text}) | crescimento={townhall.growth_per_hour:.2f}/h"
                ),
            )

        return {
            "current_amount": state.current_amount,
            "max_amount": state.max_amount,
            "wine_spendings_per_hour": state.wine_spendings_per_hour,
            "target_amount": int(max(target_amount_hint, state.current_amount)),
            "target_wine_spendings_per_hour": int(
                state.option_costs.get(
                    int(max(target_amount_hint, state.current_amount)),
                    state.wine_spendings_per_hour or 0,
                )
            ),
            "happiness_bonus": state.happiness_bonus,
            "floor_unmet": townhall.total_happiness < happiness_floor,
            "townhall": townhall,
        }

    def _desired_tavern_amount(self, *, preview, townhall, is_full: bool, growth_happiness_floor: int) -> int:
        wine_happiness = _wine_happiness_from_breakdown(townhall.breakdown)
        non_wine_happiness = int(townhall.total_happiness) - int(wine_happiness)
        happiness_floor = self._happiness_floor(is_full=is_full, growth_happiness_floor=growth_happiness_floor)

        if preview.max_amount <= 0:
            return 0

        if happiness_floor <= non_wine_happiness:
            return 0

        if preview.current_amount > 0 and wine_happiness > 0:
            happiness_per_step = wine_happiness / max(1, preview.current_amount)
            required_wine_happiness = max(0, happiness_floor - non_wine_happiness)
            target_amount = int(ceil(required_wine_happiness / max(0.1, happiness_per_step)))
        else:
            target_amount = 0 if is_full else 1

        target_amount = max(0, min(preview.max_amount, target_amount))
        return target_amount

    def _happiness_floor(self, *, is_full: bool, growth_happiness_floor: int) -> int:
        return 0 if is_full else max(0, int(growth_happiness_floor))

    def _enforce_happiness_floor(
        self,
        client,
        *,
        city: dict[str, Any],
        preview,
        townhall,
        floor: int,
        changed: bool,
    ):
        city_id = str(city.get("id") or "").strip()
        tavern_position = find_tavern_position(city)
        state = preview
        current_townhall = townhall
        effective_changed = changed
        required_amount = int(state.current_amount)

        for amount in range(max(0, int(state.current_amount)) + 1, int(state.max_amount) + 1):
            required_amount = int(amount)
            result = set_tavern_service(
                client,
                city_id=city_id,
                position=tavern_position,
                desired_amount=amount,
            )
            if not result.get("ok"):
                break
            state = result.get("state") or state
            current_townhall = open_townhall_page(client, city_id=city_id, position=find_townhall_position(city))
            effective_changed = True
            if current_townhall.total_happiness >= floor:
                break

        return state, current_townhall, effective_changed, required_amount

    def _get_snapshot(self, job_id: str, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Falha ao buscar snapshot atual: {exc}")
            return None

    def _ensure_status_refresh(self, job_id: str) -> None:
        try:
            self.hub.spawn_job(job_id, action_code=100, inputs={})
            self.log(job_id, "info", "Check status solicitado para atualizar snapshot")
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel solicitar check_status: {exc}")

    def _parse_snapshot_time(self, raw: Any) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None

    def _compute_target_need(
        self,
        city_info: dict[str, Any],
        *,
        cover_hours: int,
        useful_transfer_min: int,
        shipment_cap: int,
        protect_hours: int,
    ) -> int:
        wine = _to_int(city_info.get("wine", 0), 0)
        effective_wine = _to_int(city_info.get("effective_wine", wine), wine)
        net = int(city_info.get("desired_net", city_info.get("net", 0)))
        hours = city_info.get("desired_hours", city_info.get("hours"))
        if net >= 0:
            return 0

        target_stock = int(ceil(abs(net) * cover_hours))
        deficit = max(0, target_stock - effective_wine)
        if deficit <= 0:
            return 0

        if deficit < useful_transfer_min and (hours is None or hours > max(1.0, float(protect_hours) / 2.0)):
            return 0

        return min(shipment_cap, deficit)

    def _pick_donor(
        self,
        city_infos: list[dict[str, Any]],
        *,
        target_id: str,
        donor_reserve_hours: int,
        useful_transfer_min: int,
        requested: int,
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for info in city_infos:
            if info["id"] == target_id:
                continue
            reserve = self._donor_reserve_stock(info["raw"], donor_reserve_hours, useful_transfer_min)
            available = max(0, int(info["wine"]) - reserve)
            if available < useful_transfer_min:
                continue
            candidates.append({**info, "reserve": reserve, "available": available})

        if not candidates:
            return None
        candidates.sort(key=lambda item: (item["available"], item["net"], item["wine"]), reverse=True)
        for candidate in candidates:
            if candidate["available"] >= requested:
                return candidate
        return candidates[0]

    def _format_hours(self, hours: Any) -> str:
        try:
            if hours is None:
                return "sem ETA"
            return f"{float(hours):.1f}h"
        except Exception:
            return "sem ETA"

    def _donor_reserve_stock(self, city: dict[str, Any], donor_reserve_hours: int, useful_transfer_min: int) -> int:
        net = _city_wine_net_per_hour(city)
        if net < 0:
            return max(useful_transfer_min, int(ceil(abs(net) * donor_reserve_hours)))
        return useful_transfer_min

    def _choose_next_check(
        self,
        city_infos: list[dict[str, Any]],
        *,
        protect_hours: int,
        incoming_waits: list[int],
    ) -> int:
        candidates = [MAX_RECHECK_SECONDS]

        for info in city_infos:
            hours = info.get("hours")
            if hours is None:
                continue
            seconds_to_protect = int(max(0.0, (float(hours) - float(protect_hours)) * 3600.0))
            if seconds_to_protect <= 0:
                candidates.append(MIN_RECHECK_SECONDS)
            else:
                candidates.append(max(MIN_RECHECK_SECONDS, seconds_to_protect - PROTECT_LEAD_SECONDS))

        if incoming_waits:
            candidates.append(max(MIN_RECHECK_SECONDS, min(incoming_waits) + ARRIVAL_BUFFER_SECONDS))

        chosen = min(candidates)
        return max(MIN_RECHECK_SECONDS, min(MAX_RECHECK_SECONDS, chosen))
