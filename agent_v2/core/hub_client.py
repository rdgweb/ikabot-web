"""HTTP client for hub API communication."""

import logging
import json
import platform
from typing import Any

import requests

from .config import settings

logger = logging.getLogger(__name__)


def _agent_image_identity() -> str:
    image = str(settings.agent_image or "").strip()
    digest = str(settings.agent_image_digest or "").strip()
    if image and digest:
        return f"{image}@{digest}"
    if digest:
        return digest
    return image


class HubClient:
    """All agent-to-hub communication goes through here."""

    def __init__(self):
        self.base_url = settings.hub_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["X-Agent-Token"] = settings.agent_token
        self.session.headers["X-Agent-Node-ID"] = settings.agent_node_id
        self.session.headers["Content-Type"] = "application/json"

    # ── Registration & Heartbeat ──

    def register(self) -> dict:
        """POST /api/agent/register"""
        return self._post("/api/agent/register", {
            "node_id": settings.agent_node_id,
            "agent_name": settings.agent_name,
            "agent_host": platform.node(),
            "agent_version": settings.agent_version,
            "agent_image": _agent_image_identity(),
        })

    def heartbeat(self, external_ip: str = "") -> dict:
        """POST /api/agent/heartbeat"""
        payload: dict = {
            "node_id": settings.agent_node_id,
            "agent_name": settings.agent_name,
            "agent_host": platform.node(),
            "agent_version": settings.agent_version,
            "agent_image": _agent_image_identity(),
        }
        if external_ip:
            payload["external_ip"] = external_ip
        return self._post("/api/agent/heartbeat", payload)

    def get_config(self) -> dict:
        """GET /api/agent/config"""
        return self._get("/api/agent/config", params={"node_id": settings.agent_node_id})

    # ── Job Lifecycle ──

    def report_status(
        self,
        job_id: str,
        status: str,
        exit_code: int | None = None,
        agent: str = "",
        progress: dict[str, Any] | None = None,
    ) -> dict:
        """POST /api/agent/jobs/{job_id}/status"""
        data: dict[str, Any] = {"status": status}
        if exit_code is not None:
            data["exit_code"] = exit_code
        if agent:
            data["agent"] = agent
        if progress is not None:
            data["progress"] = progress
        return self._post(f"/api/agent/jobs/{job_id}/status", data)

    def report_log(self, job_id: str, level: str, message: str) -> dict:
        """POST /api/agent/jobs/{job_id}/logs"""
        return self._post(f"/api/agent/jobs/{job_id}/logs", {"level": level, "message": message})

    def report_logs(self, job_id: str, logs: list[dict]) -> dict:
        """POST /api/agent/jobs/{job_id}/logs (bulk)"""
        return self._post(f"/api/agent/jobs/{job_id}/logs", logs)

    def reschedule_job(
        self,
        job_id: str,
        delay_seconds: int,
        inputs: dict | None = None,
    ) -> dict:
        """POST /api/agent/jobs/{job_id}/reschedule"""
        payload: dict[str, Any] = {"delay_seconds": delay_seconds}
        if inputs is not None:
            payload["inputs"] = inputs
        return self._post(f"/api/agent/jobs/{job_id}/reschedule", payload)

    def spawn_job(
        self,
        parent_job_id: str,
        *,
        action_code: int,
        inputs: dict,
        delay_seconds: int = 0,
        timeout_sec: int | None = None,
        game_account_id: str | None = None,
        node_id: str | None = None,
    ) -> dict:
        """POST /api/agent/jobs/{parent_job_id}/spawn/

        Optional game_account_id overrides the parent's GA for the child job.
        Must share the same server_id. Used by WorldSpyRunner to spawn ac=15 jobs
        from different safehouse accounts.

        Optional node_id explicitly routes the child job to a specific node.
        Use when the child account belongs to a different node than the parent.
        """
        payload: dict[str, Any] = {
            "action_code": action_code,
            "inputs": inputs,
            "delay_seconds": max(0, int(delay_seconds)),
        }
        if timeout_sec is not None:
            payload["timeout_sec"] = timeout_sec
        if game_account_id:
            payload["game_account_id"] = str(game_account_id)
        if node_id:
            payload["node_id"] = str(node_id)
        return self._post(f"/api/agent/jobs/{parent_job_id}/spawn", payload)

    def get_job_info(self, job_id: str) -> dict:
        """GET /api/agent/jobs/{job_id}/info/ — returns {job_id, status, action_code, finished}."""
        return self._get(f"/api/agent/jobs/{job_id}/info")

    def notify_parent(self, root_job_id: str, child_done: dict) -> dict:
        """POST /api/agent/jobs/{root_job_id}/notify/

        Mailbox pattern: filho notifica o job raiz da campanha.
        Hub resolve o current_runner_id do root e acorda o fallback atual.
        """
        return self._post(f"/api/agent/jobs/{root_job_id}/notify", {"child_done": child_done})

    def get_construction_support(self, job_id: str) -> dict:
        """GET /api/agent/jobs/{job_id}/construction-support/."""
        return self._get(f"/api/agent/jobs/{job_id}/construction-support")

    def get_construction_reservations(
        self,
        *,
        game_account_id: str,
        city_ids: list[str] | None = None,
    ) -> dict:
        params: dict[str, Any] = {}
        if city_ids:
            params["city_ids"] = ",".join(str(city_id).strip() for city_id in city_ids if str(city_id).strip())
        return self._get(f"/api/agent/game-accounts/{game_account_id}/construction-reservations", params=params)

    def get_login_cooldown(self, *, game_account_id: str) -> dict:
        return self._get(f"/api/agent/game-accounts/{game_account_id}/login-cooldown")

    def record_login_400(self, *, game_account_id: str, reason: str = "") -> dict:
        return self._post(
            f"/api/agent/game-accounts/{game_account_id}/login-cooldown",
            {"mode": "record_400", "reason": reason},
        )

    def clear_login_cooldown(self, *, game_account_id: str) -> dict:
        return self._post(
            f"/api/agent/game-accounts/{game_account_id}/login-cooldown",
            {"mode": "clear"},
        )

    def record_login_proxy_failure(self, *, game_account_id: str, reason: str = "") -> dict:
        return self._post(
            f"/api/agent/game-accounts/{game_account_id}/login-cooldown",
            {"mode": "record_proxy", "reason": reason},
        )

    def reserve_lobby_proxies(self, *, account_id: str, limit: int = 3) -> dict:
        return self._post(
            f"/api/agent/accounts/{account_id}/lobby-proxies",
            {"limit": max(1, int(limit or 1))},
        )

    def sync_construction_reservations(
        self,
        *,
        job_id: str,
        reservations: dict[str, dict[str, dict[str, int]]],
    ) -> dict:
        return self._post(
            f"/api/agent/jobs/{job_id}/construction-reservations/sync",
            {"mode": "refresh_remaining", "reservations": reservations},
        )

    def apply_construction_reservation_arrival(
        self,
        *,
        job_id: str,
        city_id: str | int,
        resources: dict[str, int],
    ) -> dict:
        return self._post(
            f"/api/agent/jobs/{job_id}/construction-reservations/sync",
            {"mode": "apply_arrival", "city_id": str(city_id), "resources": resources},
        )

    def send_notification(
        self,
        *,
        event: str,
        game_account_id: str | None = None,
        account_id: str | None = None,
        title: str = "",
        body: str = "",
        agent_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "event": event,
            "title": title,
            "body": body,
            "agent_name": agent_name,
            "metadata": metadata or {},
        }
        if game_account_id:
            payload["game_account_id"] = game_account_id
        if account_id:
            payload["account_id"] = account_id
        return self._post("/api/agent/notify", payload)

    # ── Game Data ──

    def update_snapshot(self, account_id: str, data: dict, game_account_id: str | None = None) -> dict:
        """POST /api/agent/snapshots"""
        payload = {"account_id": account_id, **data}
        if game_account_id:
            payload["game_account_id"] = game_account_id
        return self._post("/api/agent/snapshots", payload)

    def patch_snapshot_building(
        self,
        game_account_id: str,
        city_id: int | str,
        position: int,
        patch: dict,
    ) -> dict:
        """PATCH /api/agent/snapshots/patch-building/ — update one building in the snapshot."""
        return self._patch("/api/agent/snapshots/patch-building", {
            "game_account_id": game_account_id,
            "city_id": str(city_id),
            "position": position,
            "patch": patch,
        })

    def patch_snapshot_resources(
        self,
        game_account_id: str,
        city_id: int | str,
        resources: dict[str, int] | None = None,
        incoming_delta: dict[str, int] | None = None,
    ) -> dict:
        """PATCH /api/agent/snapshots/patch-resources/ - update one city's resource stock."""
        return self._patch("/api/agent/snapshots/patch-resources", {
            "game_account_id": game_account_id,
            "city_id": str(city_id),
            "resources": resources or {},
            "incoming_delta": incoming_delta or {},
        })

    def patch_snapshot_base(
        self,
        game_account_id: str,
        patch: dict[str, Any],
    ) -> dict:
        """PATCH /api/agent/snapshots/patch-base/ - update base_snapshot fields."""
        return self._patch("/api/agent/snapshots/patch-base", {
            "game_account_id": game_account_id,
            "patch": patch or {},
        })

    def patch_snapshot_ships(
        self,
        game_account_id: str,
        *,
        delta_transporters: int = 0,
        delta_freighters: int = 0,
    ) -> dict:
        """PATCH /api/agent/snapshots/patch-ships/ - atomic delta on ship counts.
        Negative pra despachar, positive pra liberar.
        """
        return self._patch("/api/agent/snapshots/patch-ships", {
            "game_account_id": game_account_id,
            "delta_transporters": int(delta_transporters),
            "delta_freighters": int(delta_freighters),
        })

    def retime_root_followup_job(
        self,
        *,
        root_job_id: str,
        action_code: int,
        delay_seconds: int,
        exclude_job_id: str | None = None,
    ) -> dict:
        return self._post("/api/agent/jobs/retime-followup", {
            "root_job_id": root_job_id,
            "action_code": int(action_code),
            "delay_seconds": max(0, int(delay_seconds)),
            "exclude_job_id": exclude_job_id or "",
        })

    def get_snapshot(self, *, game_account_id: str | None = None, account_id: str | None = None) -> dict:
        """GET /api/agent/snapshots/current"""
        params: dict[str, Any] = {}
        if game_account_id:
            params["game_account_id"] = game_account_id
        if account_id:
            params["account_id"] = account_id
        return self._get("/api/agent/snapshots/current", params=params)

    def save_world_dump(
        self,
        *,
        account_id: str,
        islands: list[dict[str, Any]],
        game_account_id: str | None = None,
        source_job_id: str | None = None,
        scope_mode: str = "own_islands",
        title: str = "",
        filters: dict[str, Any] | None = None,
        dump_status: str = "complete",
    ) -> dict:
        payload: dict[str, Any] = {
            "account_id": account_id,
            "scope_mode": scope_mode,
            "title": title,
            "filters": filters or {},
            "islands": islands,
            "dump_status": dump_status,
        }
        if game_account_id:
            payload["game_account_id"] = game_account_id
        if source_job_id:
            payload["source_job_id"] = source_job_id
        return self._post("/api/agent/world-dumps", payload)

    def refresh_island(self, game_account_id: str, island: dict[str, Any]) -> dict:
        """POST /api/agent/worldintel/islands/refresh/ — replace island in latest dump."""
        try:
            return self._post(
                "/api/agent/worldintel/islands/refresh",
                {"game_account_id": game_account_id, "island": island},
            )
        except Exception as e:
            logger.warning("Failed to refresh island: %s", e)
            return {}

    def append_world_dump(
        self,
        dump_id: str,
        islands: list[dict[str, Any]],
        is_final: bool = False,
    ) -> dict:
        return self._post(
            f"/api/agent/world-dumps/{dump_id}/append",
            {"islands": islands, "is_final": is_final},
        )

    def replace_world_dump_islands(
        self,
        dump_id: str,
        islands: list[dict[str, Any]],
    ) -> dict:
        return self._post(
            f"/api/agent/world-dumps/{dump_id}/replace-islands",
            {"islands": islands},
        )

    # ── Session Persistence ──

    def report_session(self, game_account_id: str, cookies: dict, lobby_token: str = "") -> dict:
        """POST /api/agent/sessions/ — persist game session cookies to hub."""
        payload: dict[str, Any] = {
            "game_account_id": game_account_id,
            "cookies": cookies,
        }
        if lobby_token:
            payload["lobby_token"] = lobby_token
        try:
            return self._post("/api/agent/sessions", payload)
        except Exception as e:
            logger.warning("Failed to report session: %s", e)
            return {}

    # ── Diplomacy ──

    def get_spy_targets(
        self,
        *,
        only_inactive: bool = True,
        x_min: int | None = None,
        x_max: int | None = None,
        y_min: int | None = None,
        y_max: int | None = None,
        max_total_score: int = 0,
        max_army_score: int = 0,
        skip_if_valid: bool = False,
        missions: list[int] | None = None,
        limit: int = 50,
        game_account_id: str = "",
        intel_ttl_hours: int = 0,
        source_cities: list[str] | None = None,
    ) -> dict:
        """GET /api/agent/worldintel/spy-targets/

        Returns list of WorldDumpCity targets suitable for espionage.
        Automatically excludes own accounts' cities.

        intel_ttl_hours: override TTL for skip_if_valid check (0 = use AppSetting).
        Response: {"targets": [...], "dump_id": "...", "total": N, "busy_source_cities": [...]}
        """
        params: dict[str, Any] = {
            "only_inactive": "1" if only_inactive else "0",
            "limit": limit,
            "skip_if_valid": "1" if skip_if_valid else "0",
        }
        if x_min is not None:
            params["x_min"] = x_min
        if x_max is not None:
            params["x_max"] = x_max
        if y_min is not None:
            params["y_min"] = y_min
        if y_max is not None:
            params["y_max"] = y_max
        if max_total_score:
            params["max_total_score"] = max_total_score
        if max_army_score:
            params["max_army_score"] = max_army_score
        if missions:
            params["missions"] = ",".join(str(m) for m in missions)
        if game_account_id:
            params["game_account_id"] = game_account_id
        if intel_ttl_hours and intel_ttl_hours > 0:
            params["intel_ttl_hours"] = intel_ttl_hours
        if source_cities:
            params["source_cities"] = ",".join(source_cities)
        return self._get("/api/agent/worldintel/spy-targets", params=params)

    def recommend_combat_force(
        self,
        *,
        enemy_units: dict,
        available_units: dict,
        wall_level: int = 15,
        town_hall_level: int = 1,
        attacker_upgrades: dict | None = None,
        defender_upgrades: dict | None = None,
        max_loss_pct: float = 30.0,
        reserve_pct: float = 25.0,
    ) -> dict:
        """POST /api/agent/combat/recommend/ — fonte única de simulação.
        Mesma lógica do Telegram (battle_land.recommend_attack_force).
        Retorna {can_win, recommended, simulation, note}.
        """
        return self._post("/api/agent/combat/recommend", {
            "enemy_units":       {str(k): v for k, v in (enemy_units or {}).items()},
            "available_units":   {str(k): v for k, v in (available_units or {}).items()},
            "wall_level":        wall_level,
            "town_hall_level":   town_hall_level,
            "attacker_upgrades": attacker_upgrades or {},
            "defender_upgrades": defender_upgrades or {},
            "max_loss_pct":      max_loss_pct,
            "reserve_pct":       reserve_pct,
        }) or {}

    def save_combat_report(self, game_account_id: str, report: dict) -> dict:
        """POST /api/agent/combat/reports/ — save/update a combat report.

        report dict fields (see CombatReport model):
            combat_id, combat_type, result, combat_date, total_rounds,
            source_city_id, source_city_name, target_city_id, target_city_name,
            target_owner, target_owner_id, loot_json, attacker_losses,
            defender_losses, summary_html, detailed_html
        """
        return self._post("/api/agent/combat/reports", {
            "game_account_id": game_account_id,
            **report,
        })

    def update_city_state(
        self,
        game_city_id: str = "",
        state: str = "",
        game_account_id: str | None = None,
        reason: str = "",
        owner_id: str = "",
    ) -> dict:
        """POST /api/agent/worldintel/cities/update-state/

        Update the state of a city in the WorldDump when the spy detects
        the player changed state (vacation, gone, etc.) after the dump was captured.
        Use owner_id to update ALL cities of a player at once.
        state: "vacation" | "inactive_banned" | "gone" | "active" | "inactive"
        """
        payload: dict[str, Any] = {
            "state": state,
            "reason": reason,
        }
        if game_city_id:
            payload["game_city_id"] = game_city_id
        if owner_id:
            payload["owner_id"] = owner_id
        if game_account_id:
            payload["game_account_id"] = game_account_id
        return self._post("/api/agent/worldintel/cities/update-state", payload)

    def update_military_movements(
        self,
        game_account_id: str,
        movements: dict,
        *,
        probe_key: str = "",
    ) -> dict:
        """POST /api/agent/military-movements/ — persist military advisor state."""
        try:
            payload = {
                "game_account_id": game_account_id,
                "movements": movements,
            }
            if probe_key:
                payload["probe_key"] = probe_key
            return self._post("/api/agent/military-movements", payload)
        except Exception as e:
            logger.warning("Failed to update military movements: %s", e)
            return {}

    def get_military_movements(self, game_account_id: str) -> dict:
        """GET /api/agent/military-movements/?game_account_id=<uuid>."""
        try:
            return self._get("/api/agent/military-movements", {
                "game_account_id": game_account_id,
            })
        except Exception as e:
            logger.warning("Failed to get military movements: %s", e)
            return {}

    def scan_raid_alerts(
        self,
        game_account_id: str,
        threshold: int = 50000,
        raid_source_city: str = "",
        raid_transporters: int = 0,
        raid_max_trips: int = 5,
        intel_ttl_hours: int = 24,
    ) -> dict:
        """POST /api/agent/espionage/scan-raid-alerts/

        Hub varre todos reports válidos do server e dispara alertas Telegram
        para cidades acima do threshold que ainda não foram alertadas (ou que
        têm report novo desde último alerta). Idempotente — pode ser chamado
        a cada execução do world spy sem spam.
        """
        try:
            return self._post("/api/agent/espionage/scan-raid-alerts", {
                "game_account_id":   game_account_id,
                "threshold":         threshold,
                "raid_source_city":  raid_source_city,
                "raid_transporters": raid_transporters,
                "raid_max_trips":    raid_max_trips,
                "intel_ttl_hours":   intel_ttl_hours,
            })
        except Exception as e:
            logger.warning("Failed to scan raid alerts: %s", e)
            return {}

    def lookup_city(self, city_id: str, game_account_id: str) -> dict:
        """GET /api/agent/worldintel/city-lookup/ — resolve island_id pra cidade no dump."""
        try:
            return self._get("/api/agent/worldintel/city-lookup",
                             params={"city_id": city_id, "ga_id": game_account_id}) or {}
        except Exception:
            return {}

    def list_active_spy_targets(self, game_account_id: str) -> list[dict]:
        """GET /api/agent/jobs/active-spy-targets/?ga_id=X
        Lista alvos com jobs ac=15 ativos pro GA. Usado pra detectar grupos órfãos.
        Retorna: [{target_owner, target_owner_id, target_city_name, target_city_id}, ...]
        """
        try:
            resp = self._get("/api/agent/jobs/active-spy-targets", params={"ga_id": game_account_id})
            return list((resp or {}).get("targets") or [])
        except Exception:
            return []

    def get_missions_covered(
        self,
        target_city_id: str,
        target_owner_id: str = "",
        target_owner: str = "",
        game_account_id: str = "",
        intel_ttl_hours: int = 0,
    ) -> list[int]:
        """GET /api/agent/espionage/missions-covered/ — missions already covered by valid recent reports."""
        params: dict[str, Any] = {"target_city_id": target_city_id}
        if target_owner_id:
            params["target_owner_id"] = target_owner_id
        if target_owner:
            params["target_owner"] = target_owner
        if game_account_id:
            params["game_account_id"] = game_account_id
        if intel_ttl_hours:
            params["intel_ttl_hours"] = intel_ttl_hours
        try:
            resp = self._get("/api/agent/espionage/missions-covered", params=params)
            return [int(m) for m in (resp or {}).get("covered", [])]
        except Exception:
            return []

    def get_latest_spy_intel(
        self,
        target_city_id: str,
        game_account_id: str | None = None,
    ) -> dict:
        """GET /api/agent/espionage/intel/?target_city_id=X

        Returns consolidated intel for the target city from the latest spy reports:
        {
            "resources": {"wood": N, "wine": N, ...},
            "troops":    {"315": 4, "301": 1},
            "fleet":     {"210": 2},
            "wall_level": N,
            "last_updated": "ISO datetime",
        }
        Returns {} if no intel available.
        """
        params: dict[str, Any] = {"target_city_id": target_city_id}
        if game_account_id:
            params["game_account_id"] = game_account_id
        return self._get("/api/agent/espionage/intel", params=params)

    def save_spy_reports(
        self,
        game_account_id: str,
        reports: list[dict],
        job_id: str | None = None,
        action_job_id: str | None = None,
    ) -> dict:
        """POST /api/agent/espionage/reports/

        Upserts a batch of spy reports captured from the safehouse.
        job_id (opcional): vincula os reports ao Job que os capturou.
        Returns {"saved": N, "new_count": N}.
        """
        payload = {"game_account_id": game_account_id, "reports": reports}
        if job_id:
            payload["job_id"] = str(job_id)
        if action_job_id:
            payload["action_job_id"] = str(action_job_id)
        return self._post("/api/agent/espionage/reports", payload)

    def save_diplomacy_messages(self, game_account_id: str, messages: list[dict]) -> dict:
        """POST /api/agent/diplomacy/messages/

        Upserts a batch of diplomacy messages captured from the inbox.
        Each message dict must have at minimum a 'game_msg_id' key.
        Returns {"saved": N, "new_count": N}.
        """
        return self._post("/api/agent/diplomacy/messages", {
            "game_account_id": game_account_id,
            "messages": messages,
        })

    # ── Internal Market ──

    def market_order_sell_complete(
        self,
        order_id: str,
        *,
        unit_price: int | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
    ) -> dict:
        """POST /api/agent/market/orders/<uuid>/sell-complete/

        Called by Runner 802 after placing the sell offer in-game.
        Hub creates the buy_job (801) on the buyer's node.
        """
        payload: dict[str, Any] = {}
        if unit_price is not None:
            payload["unit_price"] = unit_price
        if price_min is not None:
            payload["price_min"] = price_min
        if price_max is not None:
            payload["price_max"] = price_max
        return self._post(f"/api/agent/market/orders/{order_id}/sell-complete", payload)

    def market_order_complete(self, order_id: str) -> dict:
        """POST /api/agent/market/orders/<uuid>/complete/

        Called by Runner 801 after the purchase is confirmed in-game.
        """
        return self._post(f"/api/agent/market/orders/{order_id}/complete", {})

    def create_market_order(
        self,
        game_account_id: str,
        resource_idx: int,
        amount: int,
        unit_price: int = 12,
        preferred_buyer_city_id: int | None = None,
        target_city_id: int | None = None,
        source_job_id: str | None = None,
        source_action_code: int | None = None,
        source_reason: str = "",
        reason_detail: str = "",
        production_eta_seconds: int | None = None,
        missing_resource_keys: str = "",
    ) -> dict:
        """POST /api/agent/market/orders/create/

        Request the hub to create an InternalMarketOrder (matching + sell_job).
        """
        payload = {
            "game_account_id": game_account_id,
            "resource_idx": resource_idx,
            "amount": amount,
            "unit_price": unit_price,
        }
        if preferred_buyer_city_id is not None:
            payload["preferred_buyer_city_id"] = preferred_buyer_city_id
        if target_city_id is not None:
            payload["target_city_id"] = target_city_id
        if source_job_id:
            payload["source_job_id"] = source_job_id
        if source_action_code is not None:
            payload["source_action_code"] = source_action_code
        if source_reason:
            payload["source_reason"] = source_reason
        if reason_detail:
            payload["reason_detail"] = reason_detail
        if production_eta_seconds is not None:
            payload["production_eta_seconds"] = production_eta_seconds
        if missing_resource_keys:
            payload["missing_resource_keys"] = missing_resource_keys
        return self._post("/api/agent/market/orders/create", payload)

    def request_market_intervention(
        self,
        *,
        game_account_id: str,
        source_job_id: str | None,
        payload: dict[str, Any],
    ) -> dict:
        body = dict(payload or {})
        body["game_account_id"] = game_account_id
        if source_job_id:
            body["source_job_id"] = source_job_id
        return self._post("/api/agent/market/interventions/request", body)

    def save_bm_offer(
        self,
        *,
        game_account_id: str,
        job_id: str,
        city_id: int,
        city_name: str,
        unit_id: int,
        unit_name: str,
        amount: int,
        unit_price: int,
        offer_resource: int = 5,
        game_offer_id: int | None = None,
    ) -> dict:
        """POST /api/agent/market/bm-offers/ — persist a newly listed BM offer."""
        payload = {
            "game_account_id": game_account_id,
            "job_id": job_id,
            "city_id": city_id,
            "city_name": city_name,
            "unit_id": unit_id,
            "unit_name": unit_name,
            "amount": amount,
            "unit_price": unit_price,
            "offer_resource": offer_resource,
        }
        if game_offer_id is not None:
            payload["game_offer_id"] = game_offer_id
        return self._post("/api/agent/market/bm-offers", payload)

    def close_bm_offer(self, offer_hub_id: str) -> dict:
        """POST /api/agent/market/bm-offers/<id>/close/ — mark offer cancelled after removeOffer."""
        return self._post(f"/api/agent/market/bm-offers/{offer_hub_id}/close", {})

    def sync_bm_offers(
        self,
        *,
        game_account_id: str,
        city_id: int,
        active_unit_ids: list[int],
        active_offers: list[dict[str, Any]] | None = None,
    ) -> dict:
        """POST /api/agent/market/bm-offers/sync/ — reconcile active offers."""
        return self._post("/api/agent/market/bm-offers/sync", {
            "game_account_id": game_account_id,
            "city_id": city_id,
            "active_unit_ids": active_unit_ids,
            "active_offers": active_offers or [],
        })

    def save_bm_quotes(
        self,
        *,
        game_account_id: str,
        job_id: str,
        city_id: int,
        city_name: str,
        quotes: list[dict[str, Any]],
    ) -> dict:
        """POST /api/agent/market/bm-quotes/ â€” persist BM unit ranges for one execution."""
        return self._post("/api/agent/market/bm-quotes", {
            "game_account_id": game_account_id,
            "job_id": job_id,
            "city_id": city_id,
            "city_name": city_name,
            "quotes": quotes,
        })

    def get_bm_prices(self) -> list[dict]:
        """GET /api/agent/market/bm-offers/prices/ — return avg price per unit."""
        resp = self._get("/api/agent/market/bm-offers/prices")
        return resp.get("prices", [])

    def save_bm_available_offers(
        self,
        *,
        game_account_id: str,
        job_id: str,
        buyer_city_id: int,
        offers: list[dict],
    ) -> dict:
        """POST /api/agent/market/bm-available-offers/ — replace scanned offers for buyer city."""
        return self._post("/api/agent/market/bm-available-offers", {
            "game_account_id": game_account_id,
            "job_id": job_id,
            "buyer_city_id": buyer_city_id,
            "offers": offers,
        })

    # ── Blackbox & Captcha (proxied via hub → ikabotapi) ──

    def get_blackbox_token(self, user_agent: str) -> str:
        """GET /api/agent/blackbox/token"""
        resp = self._get("/api/agent/blackbox/token", params={"user_agent": user_agent})
        return resp.get("token", "")

    def solve_captcha(self, captcha_type: str, images: dict) -> dict:
        """POST /api/agent/captcha/solve"""
        return self._post("/api/agent/captcha/solve", {"type": captcha_type, "images": images})

    def create_captcha_challenge(
        self,
        captcha_type: str,
        image_b64: str,
        game_account_id: str = "",
        *,
        display_type: str = "",
        extra_data: dict | None = None,
    ) -> dict:
        """Create a captcha challenge. Returns {"solution": str|None, "challenge_id": int|None}."""
        return self._post("/api/agent/captcha/challenge", {
            "type": captcha_type,
            "image_b64": image_b64,
            "game_account_id": game_account_id or "",
            "display_type": display_type or "",
            "extra_data": extra_data or {},
        })

    def poll_captcha_solution(self, challenge_id: int, timeout_sec: int = 120, interval: int = 10) -> str:
        """Poll until challenge solved or timeout. Returns solution string or empty."""
        import time
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            result = self._get(f"/api/agent/captcha/challenge/{challenge_id}")
            if result.get("status") == "solved":
                return str(result.get("solution") or "")
            if result.get("status") in ("failed", "expired"):
                return ""
            time.sleep(interval)
        return ""

    # ── Generals Bank ──

    def bank_get_config(self, bank_config_id: str) -> dict:
        """GET /api/agent/generals-bank/configs/<id>/"""
        return self._get(f"/api/agent/generals-bank/configs/{bank_config_id}")

    def bank_create_cycle(
        self,
        bank_config_id: str,
        mode: str,
        target_units: dict | None = None,
        manager_job_id: str | None = None,
    ) -> dict:
        """POST /api/agent/generals-bank/cycles/create/"""
        payload: dict[str, Any] = {
            "bank_config_id": bank_config_id,
            "mode": mode,
            "target_units": target_units or {},
        }
        if manager_job_id:
            payload["manager_job_id"] = manager_job_id
        return self._post("/api/agent/generals-bank/cycles/create", payload)

    def bank_get_cycle_status(self, cycle_id: str) -> dict:
        """GET /api/agent/generals-bank/cycles/<id>/status/"""
        return self._get(f"/api/agent/generals-bank/cycles/{cycle_id}/status")

    def bank_update_task(self, task_id: str, *, status: str = "", quantity_done: int | None = None,
                         unit_price: int | None = None, unit_name: str = "", sell_job_id: str = "") -> dict:
        """POST /api/agent/generals-bank/tasks/<id>/update/"""
        payload: dict[str, Any] = {}
        if status:
            payload["status"] = status
        if quantity_done is not None:
            payload["quantity_done"] = quantity_done
        if unit_price is not None:
            payload["unit_price"] = unit_price
        if unit_name:
            payload["unit_name"] = unit_name
        if sell_job_id:
            payload["sell_job_id"] = sell_job_id
        return self._post(f"/api/agent/generals-bank/tasks/{task_id}/update", payload)

    def bank_buy_complete(self, cycle_id: str, purchases: list[dict]) -> dict:
        """POST /api/agent/generals-bank/cycles/<id>/buy-complete/"""
        return self._post(f"/api/agent/generals-bank/cycles/{cycle_id}/buy-complete", {"purchases": purchases})

    def bank_cycle_complete(self, cycle_id: str) -> dict:
        """POST /api/agent/generals-bank/cycles/<id>/complete/"""
        return self._post(f"/api/agent/generals-bank/cycles/{cycle_id}/complete", {})

    def bank_cycle_fail(self, cycle_id: str, note: str) -> dict:
        """POST /api/agent/generals-bank/cycles/<id>/fail/"""
        return self._post(f"/api/agent/generals-bank/cycles/{cycle_id}/fail", {"note": note})

    # ── Internal ──

    def _url(self, path: str) -> str:
        """Ensure trailing slash for Django's APPEND_SLASH."""
        if not path.endswith("/"):
            path += "/"
        return f"{self.base_url}{path}"

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self.session.get(self._url(path), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, data: Any) -> dict:
        resp = self.session.patch(self._url(path), json=data, timeout=15)
        resp.raise_for_status()
        if not resp.content:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {}

    def _post(self, path: str, data: Any) -> dict:
        resp = self.session.post(self._url(path), json=data, timeout=15)
        resp.raise_for_status()
        if not resp.content:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {}
