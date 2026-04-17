"""
Telegram update processor — runs as a long-lived service alongside the hub.

Polls Telegram's getUpdates API to receive messages and process
/start commands for account linking. Automatically waits for the
bot to be configured before starting.

Usage (docker-compose service — runs automatically):
    python manage.py telegram_poll

The service:
  - Waits until a bot_token is configured in the admin panel
  - Uses long-polling (getUpdates) to receive messages
  - Processes /start codes for global and per-subconta linking
  - Handles errors gracefully with exponential backoff
  - Restarts cleanly on SIGTERM (docker stop)
"""

import logging
import re
import signal
import sys
import time

import requests
from django.core.management.base import BaseCommand

from apps.telegram.models import TelegramBotConfig, TelegramAccountConfig
from apps.telegram.services.bot_api import send_message
from apps.telegram.services.linking import validate_link_code
from apps.telegram.api.webhook import _create_diplomacy_send_job_from_uuid

logger = logging.getLogger("telegram.poll")

CODE_RE = re.compile(r"^/start\s+(\d{6})$")
REPLYTO_RE = re.compile(r"^/replyto\s+([\w-]{36})\s+([\s\S]+)$", re.IGNORECASE)
API_BASE = "https://api.telegram.org/bot{token}"

# Backoff settings
MIN_BACKOFF = 2
MAX_BACKOFF = 60


class Command(BaseCommand):
    help = "Telegram polling service — processes bot messages for linking"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._running = True

    def handle(self, *args, **options):
        # Graceful shutdown on SIGTERM (docker stop)
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

        self.stdout.write("Telegram polling service starting...")

        while self._running:
            config = self._wait_for_config()
            if not config:
                break  # Shutdown requested

            self.stdout.write(self.style.SUCCESS(
                f"Bot configured. Starting poll loop..."
            ))

            self._poll_loop(config.bot_token)

        self.stdout.write("Telegram polling service stopped.")

    def _shutdown(self, signum, frame):
        self.stdout.write("\nShutdown signal received...")
        self._running = False

    def _wait_for_config(self):
        """Block until bot_token is configured and enabled. Returns config or None."""
        self.stdout.write("Waiting for bot to be configured...")
        while self._running:
            try:
                config = TelegramBotConfig.objects.get(pk=1)
                if config.bot_token and config.enabled:
                    return config
            except TelegramBotConfig.DoesNotExist:
                pass

            # Check every 10s
            for _ in range(10):
                if not self._running:
                    return None
                time.sleep(1)

        return None

    def _poll_loop(self, token):
        """Main polling loop with error recovery."""
        offset = self._get_latest_offset(token)
        backoff = MIN_BACKOFF
        poll_timeout = 5  # Short-poll timeout (long-poll causes SSL issues on some networks)

        while self._running:
            try:
                url = f"{API_BASE.format(token=token)}/getUpdates"
                resp = requests.get(
                    url,
                    params={"offset": offset, "timeout": poll_timeout},
                    timeout=poll_timeout + 10,
                )
                data = resp.json()

                if not data.get("ok"):
                    # Token might have changed — re-check config
                    desc = data.get("description", "")
                    if "Unauthorized" in desc or "Not Found" in desc:
                        self.stderr.write(
                            f"Bot token rejected: {desc}. Waiting for new config..."
                        )
                        return  # Back to _wait_for_config
                    raise Exception(f"API error: {data}")

                updates = data.get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1
                    self._process_update(update)

                backoff = MIN_BACKOFF  # Reset on success

            except requests.RequestException as e:
                self.stderr.write(f"Network error: {e}")
                self._backoff_sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

            except Exception as e:
                self.stderr.write(f"Error: {e}")
                self._backoff_sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    def _get_latest_offset(self, token):
        """Skip old messages by getting the latest update_id."""
        try:
            url = f"{API_BASE.format(token=token)}/getUpdates"
            resp = requests.get(url, params={"offset": -1, "limit": 1}, timeout=5)
            data = resp.json()
            if data.get("ok") and data.get("result"):
                return data["result"][-1]["update_id"] + 1
        except Exception:
            pass
        return 0

    def _backoff_sleep(self, seconds):
        """Sleep with shutdown awareness."""
        for _ in range(int(seconds)):
            if not self._running:
                return
            time.sleep(1)

    def _process_update(self, update):
        """Process a single Telegram update."""
        message = update.get("message", {})
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        from_user = message.get("from", {})
        username = from_user.get("username", "")

        if not text or not chat_id:
            return

        text_stripped = text.strip()

        # Handle /start <code>
        match = CODE_RE.match(text_stripped)
        if match:
            code = match.group(1)
            config = validate_link_code(code, chat_id, username)
            if config:
                if isinstance(config, TelegramBotConfig):
                    send_message(
                        chat_id,
                        "Vinculado com sucesso!\n\n"
                        "Este chat recebera <b>todas</b> as notificacoes "
                        "do ikabot hub.",
                    )
                    logger.info("GLOBAL linked -> chat %s (@%s)", chat_id, username)
                    self.stdout.write(self.style.SUCCESS(
                        f"Global linked -> chat {chat_id} (@{username})"
                    ))
                elif isinstance(config, TelegramAccountConfig):
                    ga_name = (
                        config.game_account.name
                        or config.game_account.server_id
                    )
                    send_message(
                        chat_id,
                        f"Vinculado com sucesso!\n\n"
                        f"Subconta: <b>{ga_name}</b>\n"
                        f"Este chat recebera notificacoes desta subconta.",
                    )
                    logger.info(
                        "GA %s linked -> chat %s (@%s)",
                        config.game_account_id, chat_id, username,
                    )
                    self.stdout.write(self.style.SUCCESS(
                        f"GA linked: {config.game_account_id} -> chat {chat_id}"
                    ))
            else:
                send_message(
                    chat_id,
                    "Codigo invalido ou expirado.\n\n"
                    "Gere um novo codigo no painel e tente novamente.",
                )
                logger.info("Invalid/expired code %s from chat %s", code, chat_id)
            return

        # Handle /replyto <db_uuid> [yes|no] [text]
        match = REPLYTO_RE.match(text_stripped)
        if match:
            db_uuid = match.group(1)
            rest = match.group(2).strip()
            yes_no = ""
            extra_text = rest
            lower = rest.lower()
            if lower.startswith("yes") and (len(lower) == 3 or lower[3] in (" ", "\n")):
                yes_no = "yes"
                extra_text = rest[3:].strip()
            elif lower.startswith("no") and (len(lower) == 2 or lower[2] in (" ", "\n")):
                yes_no = "no"
                extra_text = rest[2:].strip()

            ok, error = _create_diplomacy_send_job_from_uuid(db_uuid, yes_no, extra_text)
            if ok:
                if yes_no == "yes":
                    send_message(chat_id, "✅ Aceitar — job criado na fila.")
                elif yes_no == "no":
                    send_message(chat_id, "❌ Recusar — job criado na fila.")
                else:
                    send_message(chat_id, "↩️ Resposta enviada para o agente.")
                self.stdout.write(self.style.SUCCESS(
                    f"replyto {db_uuid} ({yes_no or 'text'}) from chat {chat_id}"
                ))
            else:
                send_message(chat_id, f"❌ {error}")
                logger.warning("replyto failed: uuid=%s error=%s", db_uuid, error)
