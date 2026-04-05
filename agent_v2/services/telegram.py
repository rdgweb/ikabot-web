"""Telegram notification service — sends alerts via hub API."""

import logging

from core.hub_client import HubClient

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Sends notifications to Telegram via the hub's telegram endpoint.
    The hub handles actual Telegram Bot API communication.
    """

    def __init__(self, hub: HubClient):
        self.hub = hub

    def notify_job_completed(self, account_id: str, action_name: str, success: bool):
        """Send notification when a job finishes."""
        status = "✅ Concluído" if success else "❌ Erro"
        message = f"{status}: {action_name}"
        self._send(account_id, message)

    def notify_attack_detected(self, account_id: str, city_name: str, attacker: str):
        """Send alert when an attack is detected."""
        message = f"⚠️ Ataque detectado em {city_name} por {attacker}!"
        self._send(account_id, message)

    def notify_session_expired(self, account_id: str):
        """Notify when session cookies expire."""
        message = "🔒 Sessão expirou — re-login necessário"
        self._send(account_id, message)

    def _send(self, account_id: str, message: str):
        """Send message via hub API."""
        try:
            # TODO: Implement hub endpoint for telegram notifications
            # self.hub._post("/api/agent/telegram/send", {
            #     "account_id": account_id,
            #     "message": message,
            # })
            logger.info(f"Telegram [{account_id}]: {message}")
        except Exception as e:
            logger.warning(f"Telegram notification failed: {e}")
