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

    def get_snapshot(self, *, game_account_id: str | None = None, account_id: str | None = None) -> dict:
        """GET /api/agent/snapshots/current"""
        params: dict[str, Any] = {}
        if game_account_id:
            params["game_account_id"] = game_account_id
        if account_id:
            params["account_id"] = account_id
        return self._get("/api/agent/snapshots/current", params=params)

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

    def market_order_sell_complete(self, order_id: str) -> dict:
        """POST /api/agent/market/orders/<uuid>/sell-complete/

        Called by Runner 802 after placing the sell offer in-game.
        Hub creates the buy_job (801) on the buyer's node.
        """
        return self._post(f"/api/agent/market/orders/{order_id}/sell-complete", {})

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
    ) -> dict:
        """POST /api/agent/market/orders/create/

        Request the hub to create an InternalMarketOrder (matching + sell_job).
        """
        return self._post("/api/agent/market/orders/create", {
            "game_account_id": game_account_id,
            "resource_idx": resource_idx,
            "amount": amount,
            "unit_price": unit_price,
        })

    # ── Blackbox & Captcha (proxied via hub → ikabotapi) ──

    def get_blackbox_token(self, user_agent: str) -> str:
        """GET /api/agent/blackbox/token"""
        resp = self._get("/api/agent/blackbox/token", params={"user_agent": user_agent})
        return resp.get("token", "")

    def solve_captcha(self, captcha_type: str, images: dict) -> dict:
        """POST /api/agent/captcha/solve"""
        return self._post("/api/agent/captcha/solve", {"type": captcha_type, "images": images})

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
