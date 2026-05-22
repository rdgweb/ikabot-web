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
    ) -> dict:
        payload: dict[str, Any] = {
            "action_code": action_code,
            "inputs": inputs,
            "delay_seconds": max(0, int(delay_seconds)),
        }
        if timeout_sec is not None:
            payload["timeout_sec"] = timeout_sec
        return self._post(f"/api/agent/jobs/{parent_job_id}/spawn", payload)

    def get_construction_support(self, job_id: str) -> dict:
        """GET /api/agent/jobs/{job_id}/construction-support/."""
        return self._get(f"/api/agent/jobs/{job_id}/construction-support")

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

    def save_spy_reports(self, game_account_id: str, reports: list[dict]) -> dict:
        """POST /api/agent/espionage/reports/

        Upserts a batch of spy reports captured from the safehouse.
        Returns {"saved": N, "new_count": N}.
        """
        return self._post("/api/agent/espionage/reports", {
            "game_account_id": game_account_id,
            "reports": reports,
        })

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

    # ── Blackbox & Captcha (proxied via hub → ikabotapi) ──

    def get_blackbox_token(self, user_agent: str) -> str:
        """GET /api/agent/blackbox/token"""
        resp = self._get("/api/agent/blackbox/token", params={"user_agent": user_agent})
        return resp.get("token", "")

    def solve_captcha(self, captcha_type: str, images: dict) -> dict:
        """POST /api/agent/captcha/solve"""
        return self._post("/api/agent/captcha/solve", {"type": captcha_type, "images": images})

    def create_captcha_challenge(self, captcha_type: str, image_b64: str, game_account_id: str = "") -> dict:
        """Create a captcha challenge. Returns {"solution": str|None, "challenge_id": int|None}."""
        return self._post("/api/agent/captcha/challenge", {
            "type": captcha_type,
            "image_b64": image_b64,
            "game_account_id": game_account_id or "",
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
